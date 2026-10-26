"""Core data models: order book levels, books, market references, fills."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Level:
    price: float
    size: float  # shares available at this price


@dataclass
class Book:
    """One outcome token's CLOB order book. Asks ascending, bids descending."""

    token_id: str
    asks: list[Level] = field(default_factory=list)
    bids: list[Level] = field(default_factory=list)

    @property
    def best_ask(self) -> float | None:
        return self.asks[0].price if self.asks else None

    @property
    def best_bid(self) -> float | None:
        return self.bids[0].price if self.bids else None

    def buy_cost(self, shares: float) -> tuple[float, float] | None:
        """Walk the ask side. Return (total_cost, avg_price) or None if depth is insufficient."""
        remaining = shares
        cost = 0.0
        for lvl in self.asks:
            take = min(remaining, lvl.size)
            cost += take * lvl.price
            remaining -= take
            if remaining <= 1e-12:
                return cost, cost / shares
        return None

    def sell_proceeds(self, shares: float) -> tuple[float, float] | None:
        """Walk the bid side. Return (total_proceeds, avg_price) or None if depth is insufficient."""
        remaining = shares
        proceeds = 0.0
        for lvl in self.bids:
            take = min(remaining, lvl.size)
            proceeds += take * lvl.price
            remaining -= take
            if remaining <= 1e-12:
                return proceeds, proceeds / shares
        return None

    def ask_breakpoints(self) -> list[float]:
        """Cumulative share quantities at each ask level (candidate order sizes)."""
        out, acc = [], 0.0
        for lvl in self.asks:
            acc += lvl.size
            out.append(acc)
        return out


@dataclass
class MarketRef:
    market_id: str
    question: str
    slug: str
    end_date: datetime
    outcomes: list[str]
    token_ids: list[str]
    fee_rate: float = 0.0
    neg_risk: bool = False
    liquidity: float = 0.0
    volume24h: float = 0.0
    event_id: str | None = None
    event_title: str | None = None
    description: str = ""  # market rules text lives here on Gamma

    def hours_to_end(self, now: datetime) -> float:
        return (self.end_date - now).total_seconds() / 3600.0


@dataclass
class Opportunity:
    kind: str  # "binary_arb" | "basket_arb" | "tail_harvest"
    question: str
    slug: str
    detail: dict
    expected_profit: float = 0.0
    capital_required: float = 0.0
    tags: list[str] = field(default_factory=list)
