"""Risk layer — the strategy's non-negotiable rules (STRATEGY.md section 4).

1. Capital buckets: 40% arbitrage / 50% tail-end / 10% untouchable reserve.
2. Rules review gate: nothing trades live until a human has read the
   market's Rules page word by word and added its slug to the approved
   list (rules_approved.json). Paper mode only *tags* opportunities.
3. Dispute avoidance: keyword denylist screens out subjective /
   politically contestable markets prone to UMA disputes; objective,
   data-driven markets (rates decisions, official statistics, sports
   scores, on-chain data) are the intended hunting ground.
4. Arb capital must not be locked for months: arb_max_days caps the
   settlement horizon of arbitrage candidates.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_DENYLIST = [
    # subjective / dispute-prone wording (extend in config)
    "tweet", "say", "announce", "endorse", "resign", "scandal",
    "most popular", "best dressed", "viral",
]


@dataclass
class RiskConfig:
    total_capital: float = 10_000.0
    arb_alloc_pct: float = 0.40
    tail_alloc_pct: float = 0.50
    reserve_pct: float = 0.10
    min_edge_after_fees: float = 0.005  # arb needs >=0.5% locked edge after fees
    arb_max_days: float = 30.0          # avoid locking arb capital for months
    per_trade_cap_pct: float = 0.05     # strategy band is 2%-5%
    # --- merged from the methodology article ---
    kelly_multiplier: float = 0.5       # STRATEGY.md: Half-Kelly. Article argues 0.25.
    max_depth_fraction: float = 0.20    # one order <= 20% of the book depth it hits
    min_arb_annualized: float = 0.0     # min annualised return for locked arb capital (0 = off)
    max_event_exposure_pct: float = 0.10  # directional (tail) exposure cap per single event
    daily_loss_limit_pct: float = 0.02  # circuit breaker: halt new positions at -2% day
    denylist: list[str] = field(default_factory=lambda: list(DEFAULT_DENYLIST))

    def __post_init__(self) -> None:
        total = self.arb_alloc_pct + self.tail_alloc_pct + self.reserve_pct
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"allocation buckets must sum to 1.0, got {total}")
        if not (0.02 <= self.per_trade_cap_pct <= 0.05):
            raise ValueError("per_trade_cap_pct must stay inside the strategy's 2%-5% band")

    @property
    def arb_budget(self) -> float:
        return self.total_capital * self.arb_alloc_pct

    @property
    def tail_budget(self) -> float:
        return self.total_capital * self.tail_alloc_pct


@dataclass
class BucketLedger:
    """Tracks committed capital per bucket (paper fills or live holds)."""

    arb_committed: float = 0.0
    tail_committed: float = 0.0
    event_committed: dict[str, float] = field(default_factory=dict)

    def arb_available(self, cfg: RiskConfig) -> float:
        return max(0.0, cfg.arb_budget - self.arb_committed)

    def tail_available(self, cfg: RiskConfig) -> float:
        return max(0.0, cfg.tail_budget - self.tail_committed)

    def event_available(self, cfg: RiskConfig, event_key: str) -> float:
        """Directional exposure room left for one event (article section 8:
        positions on the same event are one bet on one narrative)."""
        used = self.event_committed.get(event_key, 0.0)
        return max(0.0, cfg.max_event_exposure_pct * cfg.total_capital - used)

    def commit(self, kind: str, usd: float, event_key: str | None = None) -> None:
        if kind in ("binary_arb", "basket_arb"):
            self.arb_committed += usd
        elif kind == "tail_harvest":
            self.tail_committed += usd
            if event_key:
                self.event_committed[event_key] = self.event_committed.get(event_key, 0.0) + usd


class CircuitBreaker:
    """Daily-loss circuit breaker (article sections 5/8): written into code
    BEFORE trading starts. When the day's equity falls to
    day_start_equity * (1 - limit), new positions are halted; exits and
    settlements still proceed."""

    def __init__(self, daily_loss_limit_pct: float = 0.02):
        if not (0 < daily_loss_limit_pct < 1):
            raise ValueError("daily_loss_limit_pct must be in (0, 1)")
        self.limit = daily_loss_limit_pct

    def halted(self, day_start_equity: float, current_equity: float) -> bool:
        if day_start_equity <= 0:
            return False
        return current_equity <= day_start_equity * (1.0 - self.limit)


def denylist_hit(question: str, denylist: list[str]) -> str | None:
    q = question.lower()
    for word in denylist:
        if word in q:
            return word
    return None


class RulesGate:
    """Live-trading gate: market slug must be in the human-approved file."""

    def __init__(self, approved_file: str | Path | None):
        self.approved: set[str] = set()
        if approved_file and Path(approved_file).exists():
            data = json.loads(Path(approved_file).read_text(encoding="utf-8"))
            self.approved = set(data.get("approved_slugs", []))

    def is_approved(self, slug: str) -> bool:
        return slug in self.approved
