"""Polymarket taker-fee model.

Polymarket charges takers a fee in USDC approximated by
    fee = shares * fee_rate * price * (1 - price)
where fee_rate comes from the market's Gamma `feeSchedule.rate`
(0 for many markets; category-dependent for others, e.g. crypto).
Makers are not charged. All detection in this codebase requires the
edge to survive this fee; see STRATEGY.md section 2 (套利须扣费后仍有净利润).
"""
from __future__ import annotations


def taker_fee_per_share(price: float, fee_rate: float) -> float:
    if fee_rate <= 0:
        return 0.0
    return fee_rate * price * (1.0 - price)


def taker_fee(shares: float, avg_price: float, fee_rate: float) -> float:
    return shares * taker_fee_per_share(avg_price, fee_rate)
