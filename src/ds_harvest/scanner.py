"""Scanner: one pass over Polymarket looking for the strategy's setups.

Flow per pass:
  1. Pull markets ending within the arbitrage horizon (default 30 days).
  2. Binary arbitrage on every 2-outcome market (depth-walked, fee-aware).
  3. Basket arbitrage on events with >=3 outcome markets (YES legs).
  4. Tail-end candidates among markets ending within 24h.
Denylist screening and bucket budgets are applied before anything is
reported as an opportunity. Paper fills are executed by the CLI layer.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .arbitrage import detect_basket_arb, detect_binary_arb
from .marketdata import ClobClient, GammaClient, NoOrderBook
from .models import Opportunity
from .risk import BucketLedger, RiskConfig, denylist_hit
from .tailend import detect_tail_candidate


@dataclass
class ScanConfig:
    arb_max_days: float = 30.0
    tail_min_hours: float = 1.0
    tail_max_hours: float = 24.0
    tail_price_floor: float = 0.97
    tail_price_cap: float = 0.995
    tail_assumed_prob: float = 0.995
    tail_min_depth_usd: float = 100.0
    markets_limit: int = 500
    events_limit: int = 100


class Scanner:
    def __init__(self, risk: RiskConfig, cfg: ScanConfig | None = None):
        self.risk = risk
        self.cfg = cfg or ScanConfig()
        self.gamma = GammaClient()
        self.clob = ClobClient()
        self.ledger = BucketLedger()
        self.stats = {"markets_seen": 0, "books_fetched": 0, "denied": 0, "errors": 0, "no_book": 0, "past_end": 0}
        # The skip log is a first-class record (article sections 5/11):
        # it documents the money you did NOT lose, and a spike in one
        # reason usually means upstream data changed, not the market.
        self.skips: list[dict] = []

    def _skip(self, market, reason: str) -> None:
        self.skips.append({"slug": market.slug, "question": market.question[:120], "reason": reason})

    def _book(self, token_id: str):
        self.stats["books_fetched"] += 1
        return self.clob.get_book(token_id)

    def scan_once(self, now: datetime | None = None) -> list[Opportunity]:
        now = now or datetime.now(timezone.utc)
        cfg = self.cfg
        horizon = now + timedelta(days=max(cfg.arb_max_days, cfg.tail_max_hours / 24.0))
        markets = self.gamma.markets_ending_before(horizon, limit=cfg.markets_limit)
        self.stats["markets_seen"] = len(markets)
        found: list[Opportunity] = []

        for m in markets:
            try:
                if m.end_date <= now:
                    self.stats["past_end"] += 1
                    self._skip(m, "past_end")
                    continue
                hit = denylist_hit(m.question, self.risk.denylist)
                if hit:
                    self.stats["denied"] += 1
                    self._skip(m, f"denylist:{hit}")
                    continue
                books = [self._book(t) for t in m.token_ids]
                # Module 1a: binary complementary arbitrage
                if len(m.outcomes) == 2 and m.hours_to_end(now) <= cfg.arb_max_days * 24:
                    opp = detect_binary_arb(
                        m, books[0], books[1],
                        budget=self.ledger.arb_available(self.risk),
                        min_edge=self.risk.min_edge_after_fees,
                        min_annualized=self.risk.min_arb_annualized,
                        now=now,
                    )
                    if opp:
                        found.append(opp)
                        self.ledger.commit(opp.kind, opp.capital_required)
                # Module 2: tail-end harvesting (bucket AND per-event caps)
                event_key = m.event_id or m.slug
                tail_room = min(
                    self.ledger.tail_available(self.risk),
                    self.ledger.event_available(self.risk, event_key),
                )
                opp = detect_tail_candidate(
                    m, books,
                    now=now,
                    total_capital=self.risk.total_capital,
                    bucket_available=tail_room,
                    min_hours=cfg.tail_min_hours,
                    max_hours=cfg.tail_max_hours,
                    price_floor=cfg.tail_price_floor,
                    price_cap=cfg.tail_price_cap,
                    assumed_prob=cfg.tail_assumed_prob,
                    per_trade_cap_pct=self.risk.per_trade_cap_pct,
                    min_depth_usd=cfg.tail_min_depth_usd,
                    kelly_multiplier=self.risk.kelly_multiplier,
                    max_depth_fraction=self.risk.max_depth_fraction,
                )
                if opp:
                    found.append(opp)
                    self.ledger.commit(opp.kind, opp.capital_required, event_key=event_key)
            except NoOrderBook:
                self.stats["no_book"] += 1
                self._skip(m, "no_order_book")
                continue
            except Exception:
                self.stats["errors"] += 1
                continue

        # Module 1b: multi-outcome basket arbitrage (event level)
        try:
            events = self.gamma.events_ending_before(horizon, limit=cfg.events_limit)
            for _eid, title, legs_markets in events:
                if denylist_hit(title, self.risk.denylist):
                    continue
                legs = []
                ok = True
                for m in legs_markets:
                    yes_idx = 0
                    try:
                        legs.append((m, self._book(m.token_ids[yes_idx])))
                    except Exception:
                        ok = False
                        break
                if not ok:
                    continue
                opp = detect_basket_arb(
                    title, legs,
                    budget=self.ledger.arb_available(self.risk),
                    min_edge=self.risk.min_edge_after_fees,
                    min_annualized=self.risk.min_arb_annualized,
                    now=now,
                )
                if opp:
                    found.append(opp)
                    self.ledger.commit(opp.kind, opp.capital_required)
        except Exception:
            self.stats["errors"] += 1

        return found


def log_opportunities(opps: list[Opportunity], path: str | Path, now: datetime) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        for o in opps:
            rec = {"ts": now.isoformat(), **asdict(o)}
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def log_skips(skips: list[dict], path: str | Path, now: datetime) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        for s in skips:
            f.write(json.dumps({"ts": now.isoformat(), **s}, ensure_ascii=False) + "\n")
