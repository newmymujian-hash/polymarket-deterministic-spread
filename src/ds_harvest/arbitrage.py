"""Module 1 — structural mathematical arbitrage (STRATEGY.md section 2).

Binary complementary arbitrage: buy equal shares of YES and NO when the
combined ask cost, after taker fees, is below $1. One side must pay $1,
so the spread is locked — provided BOTH legs fill (see slippage note in
risk.py / live execution: always use limit orders at walked prices).

Multi-outcome basket arbitrage: for an event whose outcomes are mutually
exclusive and exhaustive, buy equal shares of every outcome's YES when
the combined ask cost after fees is below $1.

Sizing walks the real order-book depth: candidate sizes are the union of
both/all books' cumulative breakpoints; the chosen size maximises locked
profit subject to the bucket budget.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .features import annualized_return
from .fees import taker_fee
from .models import Book, MarketRef, Opportunity


def _evaluate_legs(books: list[Book], fee_rates: list[float], shares: float) -> tuple[float, float] | None:
    """Return (total_cost_incl_fees, unit_cost) for buying `shares` of every leg."""
    total = 0.0
    for book, rate in zip(books, fee_rates):
        got = book.buy_cost(shares)
        if got is None:
            return None
        cost, avg = got
        total += cost + taker_fee(shares, avg, rate)
    return total, total / shares


def _best_size(books: list[Book], fee_rates: list[float], budget: float, min_edge: float) -> tuple[float, float, float] | None:
    """Return (shares, total_cost, locked_profit) for the most profitable feasible size."""
    candidates = sorted({bp for b in books for bp in b.ask_breakpoints()})
    best = None
    for shares in candidates:
        if shares <= 0:
            continue
        ev = _evaluate_legs(books, fee_rates, shares)
        if ev is None:
            continue
        total, unit = ev
        if total > budget:
            continue
        if unit > 1.0 - min_edge:
            continue
        profit = shares * (1.0 - unit)
        if best is None or profit > best[2]:
            best = (shares, total, profit)
    return best


def detect_binary_arb(market: MarketRef, book_yes: Book, book_no: Book, *, budget: float, min_edge: float, min_annualized: float = 0.0, now: datetime | None = None) -> Opportunity | None:
    if book_yes.best_ask is None or book_no.best_ask is None:
        return None
    if book_yes.best_ask + book_no.best_ask >= 1.0:
        return None  # cheap pre-filter before walking depth
    sized = _best_size([book_yes, book_no], [market.fee_rate, market.fee_rate], budget, min_edge)
    if sized is None:
        return None
    shares, total, profit = sized
    unit = total / shares
    days = max(market.hours_to_end(now or datetime.now(timezone.utc)) / 24.0, 0.01)
    ann = annualized_return((1.0 - unit) / unit, days)
    if ann < min_annualized:
        return None  # edge too small for how long the capital is locked
    return Opportunity(
        kind="binary_arb",
        question=market.question,
        slug=market.slug,
        detail={
            "shares_each_leg": round(shares, 4),
            "yes_ask": book_yes.best_ask,
            "no_ask": book_no.best_ask,
            "unit_cost_after_fees": round(unit, 6),
            "days_locked": round(days, 2),
            "annualized_return": round(ann, 3),
            "end_date": market.end_date.isoformat(),
            "token_ids": market.token_ids,
            "fee_rate": market.fee_rate,
        },
        expected_profit=round(profit, 4),
        capital_required=round(total, 4),
        tags=["RULES_REVIEW_REQUIRED"],
    )


def detect_basket_arb(event_title: str, legs: list[tuple[MarketRef, Book]], *, budget: float, min_edge: float, min_annualized: float = 0.0, now: datetime | None = None) -> Opportunity | None:
    """legs = (market, YES book) for every outcome market of one event."""
    if len(legs) < 3:
        return None
    if any(b.best_ask is None for _, b in legs):
        return None
    if sum(b.best_ask for _, b in legs) >= 1.0:
        return None
    books = [b for _, b in legs]
    rates = [m.fee_rate for m, _ in legs]
    sized = _best_size(books, rates, budget, min_edge)
    if sized is None:
        return None
    shares, total, profit = sized
    unit = total / shares
    end = max(m.end_date for m, _ in legs)
    days = max((end - (now or datetime.now(timezone.utc))).total_seconds() / 86400.0, 0.01)
    ann = annualized_return((1.0 - unit) / unit, days)
    if ann < min_annualized:
        return None
    return Opportunity(
        kind="basket_arb",
        question=event_title,
        slug=legs[0][0].slug,
        detail={
            "legs": len(legs),
            "shares_each_leg": round(shares, 4),
            "sum_best_asks": round(sum(b.best_ask for _, b in legs), 6),
            "unit_cost_after_fees": round(unit, 6),
            "days_locked": round(days, 2),
            "annualized_return": round(ann, 3),
        },
        expected_profit=round(profit, 4),
        capital_required=round(total, 4),
        tags=["RULES_REVIEW_REQUIRED", "VERIFY_EXHAUSTIVE_OUTCOMES"],
    )
