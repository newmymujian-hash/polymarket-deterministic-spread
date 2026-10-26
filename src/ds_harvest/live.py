"""Live execution adapter — DELIBERATELY DISABLED by default.

Three independent gates must all pass before any real order can be placed:

1. Environment: DS_ENABLE_LIVE=1 plus CLOB L2 credentials in the
   environment (never in this repo, never in config files).
2. Region: Polymarket's geoblock is checked at runtime. As of 2026-10,
   Alberta (CA-AB), BC, Ontario and Quebec are CLOSE-ONLY on both the
   website and the API: existing positions may be closed, new positions
   may not be opened. Read-only data (marketdata.py) is unrestricted.
3. Rules gate: the market's slug must be in rules_approved.json, i.e. a
   human has read that market's Rules page word by word (STRATEGY.md
   section 4.1) and approved it manually.

Order placement itself should be wired to Polymarket's current unified
SDK (`polymarket-client`, Python 3.11+) or `py-clob-client-v2`. The
pre-V2 `py-clob-client` stopped working at the CLOB V2 cutover
(2026-04-28), and collateral is now pUSD (USDC.e was retired).
"""
from __future__ import annotations

import json
import os
import urllib.request

from .risk import RulesGate

GEOBLOCK_URL = "https://polymarket.com/api/geoblock"


def region_blocked() -> dict:
    """Return the geoblock verdict for this machine's public IP."""
    req = urllib.request.Request(GEOBLOCK_URL, headers={"User-Agent": "ds-harvest/0.1"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.load(resp)


class LiveTradingDisabled(RuntimeError):
    pass


class LiveExecutor:
    def __init__(self, rules_gate: RulesGate):
        # Triple switch (article section 9): paper default in code, live
        # flag in code review, and TWO environment confirmations — all
        # must hold at once, so no single mistake turns this live.
        if os.environ.get("DS_ENABLE_LIVE") != "1" or os.environ.get("DS_LIVE_CONFIRM") != "I_UNDERSTAND":
            raise LiveTradingDisabled(
                "Live trading is off. Both DS_ENABLE_LIVE=1 and DS_LIVE_CONFIRM=I_UNDERSTAND "
                "are required, after reviewing the gates documented at the top of live.py."
            )
        missing = [k for k in ("POLY_API_KEY", "POLY_API_SECRET", "POLY_API_PASSPHRASE", "POLY_FUNDER") if not os.environ.get(k)]
        if missing:
            raise LiveTradingDisabled(f"Missing CLOB credentials in environment: {missing}")
        verdict = region_blocked()
        if verdict.get("blocked"):
            raise LiveTradingDisabled(
                f"Polymarket geoblock: order placement blocked from "
                f"{verdict.get('country')}/{verdict.get('region')} (close-only or fully blocked)."
            )
        self.rules_gate = rules_gate

    def place_opportunity(self, slug: str, legs: list[dict]) -> None:
        if not self.rules_gate.is_approved(slug):
            raise LiveTradingDisabled(
                f"Market '{slug}' is not in rules_approved.json — read its Rules page first (STRATEGY.md 4.1)."
            )
        raise NotImplementedError(
            "Wire order placement to the Polymarket unified SDK (polymarket-client) here: "
            "one LIMIT order per leg at the walked book prices, both legs or none, "
            "signature type matching the funder wallet (0=EOA, 1=POLY_PROXY, 2=POLY_GNOSIS_SAFE, 3=POLY_1271)."
        )
