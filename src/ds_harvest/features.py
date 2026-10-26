"""Feature library (merged from the methodology article, section 4).

Prediction-market prices are bounded probabilities, so momentum and
volatility must be computed in logit space, not on raw prices:
    z = ln(p / (1 - p))
These functions are a *library for research and logging* — the shipped
strategy (STRATEGY.md) is non-directional, so no entry rule consumes
them yet. They exist so future hypotheses are tested with the right
math from day one, and so scans can log book imbalance alongside each
candidate.
"""
from __future__ import annotations

import math

EPS = 1e-6


def logit(p: float) -> float:
    p = min(max(p, EPS), 1.0 - EPS)
    return math.log(p / (1.0 - p))


def roc_logit(prices: list[float], n: int) -> float | None:
    """Logit-space rate of change over the last n periods."""
    if len(prices) <= n:
        return None
    return logit(prices[-1]) - logit(prices[-1 - n])


def atr_z(prices: list[float], n: int = 14) -> float | None:
    """Mean absolute logit change over the last n periods (ATR analogue)."""
    if len(prices) < 2:
        return None
    diffs = [abs(logit(b) - logit(a)) for a, b in zip(prices[:-1], prices[1:])]
    window = diffs[-n:]
    return sum(window) / len(window)


def aroon(prices: list[float], n: int = 25) -> tuple[float, float, float] | None:
    """(AroonUp, AroonDown, oscillator) over the last n periods."""
    if len(prices) < 2:
        return None
    window = prices[-(n + 1):]
    periods = len(window) - 1
    since_high = periods - window.index(max(window))
    since_low = periods - window.index(min(window))
    up = 100.0 * (periods - since_high) / periods
    down = 100.0 * (periods - since_low) / periods
    return up, down, up - down


def book_imbalance(bid_depth_usd: float, ask_depth_usd: float) -> float:
    """(bid - ask) / (bid + ask) in [-1, 1]; positive = bid-side pressure."""
    total = bid_depth_usd + ask_depth_usd
    if total <= 0:
        return 0.0
    return (bid_depth_usd - ask_depth_usd) / total


def annualized_return(net_return: float, days_locked: float) -> float:
    """Simple annualisation of a locked-capital return (article section 1:
    the same 2% edge is worth ~24% annualised over 30 days, ~730% over 1)."""
    if days_locked <= 0:
        return 0.0
    return net_return * 365.0 / days_locked
