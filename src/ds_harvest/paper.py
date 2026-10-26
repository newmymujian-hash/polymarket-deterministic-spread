"""Paper trading: fills simulated against the real order book.

Buys walk the ask side, sells walk the bid side, taker fees are charged —
so paper results include the slippage and depth limits the strategy
warns about (STRATEGY.md section 2, slippage control). Positions settle
at $1 / $0 per share when the winning token is known.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .fees import taker_fee
from .models import Book


@dataclass
class Position:
    question: str
    slug: str
    kind: str
    token_id: str
    outcome: str
    shares: float
    avg_price: float
    cost: float  # incl. fees
    bucket: str  # "arb" | "tail"


@dataclass
class PaperAccount:
    cash: float
    positions: list[Position] = field(default_factory=list)
    realized_pnl: float = 0.0
    trades: list[dict] = field(default_factory=list)
    marks: dict = field(default_factory=dict)  # token_id -> last seen mid price
    day_start_date: str = ""
    day_start_equity: float = 0.0

    def equity(self) -> float:
        """Cash + open positions marked to last seen price (cost if unmarked)."""
        total = self.cash
        for pos in self.positions:
            total += pos.shares * self.marks.get(pos.token_id, pos.avg_price)
        return total

    def roll_day(self, today: str) -> None:
        if self.day_start_date != today:
            self.day_start_date = today
            self.day_start_equity = self.equity()

    def buy(self, *, question: str, slug: str, kind: str, outcome: str, book: Book, shares: float, fee_rate: float, bucket: str) -> bool:
        # Idempotency (article section 9): never stack a duplicate position
        # on the same token from a repeated scan of the same opportunity.
        if any(p.token_id == book.token_id for p in self.positions):
            return False
        got = book.buy_cost(shares)
        if got is None:
            return False
        cost, avg = got
        total = cost + taker_fee(shares, avg, fee_rate)
        if total > self.cash:
            return False
        self.cash -= total
        self.positions.append(Position(question, slug, kind, book.token_id, outcome, shares, avg, total, bucket))
        self.trades.append({"side": "BUY", "slug": slug, "outcome": outcome, "shares": shares, "avg_price": avg, "cost": round(total, 4)})
        return True

    def sell_all(self, pos: Position, book: Book, fee_rate: float) -> bool:
        got = book.sell_proceeds(pos.shares)
        if got is None:
            return False
        proceeds, avg = got
        net = proceeds - taker_fee(pos.shares, avg, fee_rate)
        self.cash += net
        self.realized_pnl += net - pos.cost
        self.trades.append({"side": "SELL", "slug": pos.slug, "outcome": pos.outcome, "shares": pos.shares, "avg_price": avg, "proceeds": round(net, 4)})
        self.positions.remove(pos)
        return True

    def settle(self, winning_token_ids: set[str]) -> None:
        for pos in list(self.positions):
            payout = pos.shares * 1.0 if pos.token_id in winning_token_ids else 0.0
            self.cash += payout
            self.realized_pnl += payout - pos.cost
            self.trades.append({"side": "SETTLE", "slug": pos.slug, "outcome": pos.outcome, "shares": pos.shares, "payout": payout})
            self.positions.remove(pos)

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path, starting_cash: float) -> "PaperAccount":
        p = Path(path)
        if not p.exists():
            return cls(cash=starting_cash)
        data = json.loads(p.read_text(encoding="utf-8"))
        positions = [Position(**x) for x in data.pop("positions", [])]
        return cls(positions=positions, **data)
