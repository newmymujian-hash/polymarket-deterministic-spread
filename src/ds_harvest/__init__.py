"""Deterministic Spread & Tail-End Harvesting Strategy — Polymarket.

Implements STRATEGY.md: structural arbitrage (binary + multi-outcome
basket) plus tail-end high-probability harvesting, under the strategy's
risk rules (40/50/10 capital buckets, Half-Kelly with a 2%-5% per-trade
cap, rules-review gate, UMA-dispute avoidance). Paper-first: live
execution is gated and disabled by default (see live.py).
"""

__version__ = "0.1.0"
