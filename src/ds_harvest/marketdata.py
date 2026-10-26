"""Read-only Polymarket market data (no keys, no auth).

- Gamma API  https://gamma-api.polymarket.com  market/event metadata
- CLOB API   https://clob.polymarket.com/book  order books

Read-only market data is not geographically restricted. Order placement is
a different matter — see live.py.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from .models import Book, Level, MarketRef


class NoOrderBook(Exception):
    """CLOB returned 404: this token has no live order book (common for
    markets past their end date whose books were already cleared)."""

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"


def _get(url: str) -> object:
    req = urllib.request.Request(url, headers={"User-Agent": "ds-harvest/0.1"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def _parse_dt(s: str) -> datetime:
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _market_ref(m: dict, event_id: str | None = None, event_title: str | None = None) -> MarketRef | None:
    try:
        outcomes = json.loads(m.get("outcomes") or "[]")
        token_ids = json.loads(m.get("clobTokenIds") or "[]")
        if len(outcomes) != len(token_ids) or not token_ids:
            return None
        fee = (m.get("feeSchedule") or {}).get("rate") or 0.0
        return MarketRef(
            market_id=str(m.get("id", "")),
            question=m.get("question", ""),
            slug=m.get("slug", ""),
            end_date=_parse_dt(m["endDate"]),
            outcomes=[str(o) for o in outcomes],
            token_ids=[str(t) for t in token_ids],
            fee_rate=float(fee),
            neg_risk=bool(m.get("negRisk")),
            liquidity=float(m.get("liquidityNum") or m.get("liquidity") or 0),
            volume24h=float(m.get("volume24hr") or 0),
            event_id=event_id,
            event_title=event_title,
            description=m.get("description", "") or "",
        )
    except Exception:
        return None


class GammaClient:
    def markets_ending_before(self, deadline: datetime, limit: int = 500) -> list[MarketRef]:
        """Active markets ending in (now, deadline].

        end_date_min matters: Gamma still lists long-expired markets as
        active, and ordering by endDate ascending would otherwise fill the
        page with dead markets whose books are already cleared.
        """
        now = datetime.now(timezone.utc)
        params = urllib.parse.urlencode(
            {
                "closed": "false",
                "active": "true",
                "limit": limit,
                "order": "endDate",
                "ascending": "true",
                "end_date_min": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "end_date_max": deadline.strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
        )
        raw = _get(f"{GAMMA}/markets?{params}")
        out = []
        for m in raw if isinstance(raw, list) else []:
            ev = (m.get("events") or [{}])[0]
            ref = _market_ref(m, str(ev.get("id")) if ev.get("id") else None, ev.get("title"))
            if ref and ref.end_date <= deadline:
                out.append(ref)
        return out

    def events_ending_before(self, deadline: datetime, limit: int = 100) -> list[tuple[str, str, list[MarketRef]]]:
        """Events (for multi-outcome basket arbitrage): (event_id, title, markets)."""
        now = datetime.now(timezone.utc)
        params = urllib.parse.urlencode(
            {
                "closed": "false",
                "active": "true",
                "limit": limit,
                "order": "endDate",
                "ascending": "true",
                "end_date_min": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "end_date_max": deadline.strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
        )
        raw = _get(f"{GAMMA}/events?{params}")
        out = []
        for ev in raw if isinstance(raw, list) else []:
            markets = []
            for m in ev.get("markets", []):
                if m.get("closed"):
                    continue
                ref = _market_ref(m, str(ev.get("id")), ev.get("title"))
                if ref:
                    markets.append(ref)
            if len(markets) >= 3:
                out.append((str(ev.get("id")), ev.get("title", ""), markets))
        return out


class ClobClient:
    def get_book(self, token_id: str) -> Book:
        try:
            raw = _get(f"{CLOB}/book?token_id={urllib.parse.quote(token_id)}")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise NoOrderBook(token_id) from e
            raise
        asks = sorted(
            (Level(float(a["price"]), float(a["size"])) for a in raw.get("asks", [])),
            key=lambda x: x.price,
        )
        bids = sorted(
            (Level(float(b["price"]), float(b["size"])) for b in raw.get("bids", [])),
            key=lambda x: -x.price,
        )
        return Book(token_id=token_id, asks=asks, bids=bids)
