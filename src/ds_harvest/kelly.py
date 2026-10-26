"""Kelly sizing for the tail-end module (STRATEGY.md section 4.2).

Full Kelly for a binary bought at `price` with true win probability `p`:
    b = (1 - price) / price          (net odds)
    f* = (p*b - q) / b,  q = 1 - p   ==   (p - price) / (1 - price)
(the two forms are algebraically identical; the second is the one used
in the methodology article). STRATEGY.md mandates Half-Kelly (0.5 x f*);
the article argues for quarter-Kelly (0.25 x f*) because p is estimated.
The multiplier is therefore a parameter — default stays 0.5 per the
strategy document; set 0.25 in config for the more conservative stance.
A hard per-trade cap of 2%-5% of total capital applies regardless.
"""
from __future__ import annotations


def kelly_fraction(p: float, price: float) -> float:
    """Full-Kelly fraction of bankroll. Negative means no bet."""
    if price <= 0 or price >= 1 or p <= 0 or p >= 1:
        return 0.0
    b = (1.0 - price) / price
    q = 1.0 - p
    return (p * b - q) / b


def ev_per_dollar(p: float, price: float) -> float:
    """Expected value per $1 staked: p / price - 1 (article section 1)."""
    if price <= 0:
        return 0.0
    return p / price - 1.0


def half_kelly_fraction(p: float, price: float) -> float:
    return max(0.0, kelly_fraction(p, price) / 2.0)


def scaled_kelly_fraction(p: float, price: float, multiplier: float = 0.5) -> float:
    if not (0 < multiplier <= 1.0):
        raise ValueError("kelly multiplier must be in (0, 1]")
    return max(0.0, kelly_fraction(p, price) * multiplier)


def tail_position_usd(
    *,
    p: float,
    price: float,
    total_capital: float,
    bucket_available: float,
    per_trade_cap_pct: float = 0.05,
    kelly_multiplier: float = 0.5,
    depth_cap_usd: float | None = None,
) -> float:
    """USD size for one tail-end trade.

    min(scaled-Kelly x capital, per-trade cap, bucket balance, depth cap).
    The depth cap is the article's impact rule (section 5/8): one order
    must not exceed ~20% of the book depth it trades against.
    """
    if not (0.02 <= per_trade_cap_pct <= 0.05):
        raise ValueError("per_trade_cap_pct must stay inside the strategy's 2%-5% band")
    frac = scaled_kelly_fraction(p, price, kelly_multiplier)
    usd = frac * total_capital
    usd = min(usd, per_trade_cap_pct * total_capital, bucket_available)
    if depth_cap_usd is not None:
        usd = min(usd, depth_cap_usd)
    return max(0.0, usd)
