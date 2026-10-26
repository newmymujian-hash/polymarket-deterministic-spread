"""Module 2 — tail-end high-probability harvesting (STRATEGY.md section 3).

Screen: market settles within [min_hours, max_hours]; one outcome's best
ask sits in the harvest band (default 0.97-0.995, i.e. pay ~98.5c for a
share that settles at $1 if the near-certain outcome holds). Sizing is
Half-Kelly with the strategy's hard caps (kelly.py). Win probability is
NOT taken from the ask alone: `assumed_prob` is an explicit operator
parameter — a candidate is only tradeable when the operator's assessed
probability (after reading the market Rules) exceeds the ask by enough
for Half-Kelly to produce a positive size.
"""
from __future__ import annotations

from datetime import datetime

from .features import annualized_return, book_imbalance
from .fees import taker_fee_per_share
from .kelly import ev_per_dollar, tail_position_usd
from .models import Book, MarketRef, Opportunity


def detect_tail_candidate(
    market: MarketRef,
    books: list[Book],
    *,
    now: datetime,
    total_capital: float,
    bucket_available: float,
    min_hours: float = 1.0,
    max_hours: float = 24.0,
    price_floor: float = 0.97,
    price_cap: float = 0.995,
    assumed_prob: float = 0.995,
    per_trade_cap_pct: float = 0.05,
    min_depth_usd: float = 100.0,
    kelly_multiplier: float = 0.5,
    max_depth_fraction: float = 0.20,
) -> Opportunity | None:
    hours = market.hours_to_end(now)
    if not (min_hours <= hours <= max_hours):
        return None
    best: tuple[int, Book] | None = None
    for i, book in enumerate(books):
        if book.best_ask is None:
            continue
        if price_floor <= book.best_ask <= price_cap:
            if best is None or book.best_ask > best[1].best_ask:
                best = (i, book)
    if best is None:
        return None
    idx, book = best
    ask = book.best_ask
    depth_usd = sum(l.price * l.size for l in book.asks if l.price <= price_cap + 0.005)
    if depth_usd < min_depth_usd:
        return None
    # Edge must survive the taker fee: evaluate Kelly at the fee-loaded price.
    eff_price = ask + taker_fee_per_share(ask, market.fee_rate)
    if assumed_prob <= eff_price:
        return None
    usd = tail_position_usd(
        p=assumed_prob,
        price=eff_price,
        total_capital=total_capital,
        bucket_available=bucket_available,
        per_trade_cap_pct=per_trade_cap_pct,
        kelly_multiplier=kelly_multiplier,
        depth_cap_usd=max_depth_fraction * depth_usd,
    )
    if usd <= 0:
        return None
    shares = usd / ask
    gross_edge = shares * (1.0 - ask)
    bid_depth = sum(l.price * l.size for l in book.bids)
    ask_depth_all = sum(l.price * l.size for l in book.asks)
    net_ret = (1.0 - eff_price) / eff_price
    return Opportunity(
        kind="tail_harvest",
        question=market.question,
        slug=market.slug,
        detail={
            "outcome": market.outcomes[idx],
            "token_index": idx,
            "best_ask": ask,
            "effective_price_after_fees": round(eff_price, 6),
            "hours_to_end": round(hours, 2),
            "assumed_prob": assumed_prob,
            "ev_per_dollar": round(ev_per_dollar(assumed_prob, eff_price), 4),
            "annualized_return": round(annualized_return(net_ret, max(hours, 0.01) / 24.0), 3),
            "book_imbalance": round(book_imbalance(bid_depth, ask_depth_all), 3),
            "position_usd": round(usd, 2),
            "shares": round(shares, 2),
            "depth_usd_in_band": round(depth_usd, 2),
            "end_date": market.end_date.isoformat(),
            "token_id": market.token_ids[idx],
            "fee_rate": market.fee_rate,
        },
        expected_profit=round(gross_edge, 4),
        capital_required=round(usd, 2),
        tags=["RULES_REVIEW_REQUIRED", "VERIFY_RESULT_IS_FINAL"],
    )


def exit_signal(best_bid: float | None, *, exit_target: float = 0.995) -> bool:
    """Sell-into-strength rule (STRATEGY.md section 4.4): if the harvested
    share can be sold at/above the exit target before settlement, take the
    money and free the capital instead of holding to the last second."""
    return best_bid is not None and best_bid >= exit_target
