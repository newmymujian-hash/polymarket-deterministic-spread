"""Command line entry point.

Usage (from the repo root, after `pip install -e .` or via run.py):

  python run.py scan --once                 # read-only scan, log opportunities
  python run.py scan --once --paper         # also simulate paper fills
  python run.py scan --interval 60          # loop every 60s (paper by default off)
  python run.py report                      # summarise the opportunity log

Live trading is NOT reachable from the CLI in v0.1 — see live.py's gates.
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from .risk import CircuitBreaker, RiskConfig
from .scanner import ScanConfig, Scanner, log_opportunities, log_skips

ROOT = Path(__file__).resolve().parents[2]


def _load_json(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def build_configs(config_path: Path) -> tuple[RiskConfig, ScanConfig]:
    raw = _load_json(config_path)
    risk_raw = raw.get("risk", {})
    scan_raw = raw.get("scan", {})
    risk = RiskConfig(**{k: v for k, v in risk_raw.items() if k in RiskConfig.__dataclass_fields__})
    scan = ScanConfig(**{k: v for k, v in scan_raw.items() if k in ScanConfig.__dataclass_fields__})
    return risk, scan


def _paper_fill(opps, account) -> None:
    """Simulate fills for binary-arb and tail opportunities against fresh books."""
    from .marketdata import ClobClient

    state_path = ROOT / "data" / "paper_state.json"
    clob = ClobClient()
    filled = 0
    for o in opps:
        d = o.detail
        try:
            if o.kind == "binary_arb":
                shares = float(d["shares_each_leg"])
                ok = True
                for token_id, outcome in zip(d["token_ids"], ("Yes", "No")):
                    got = account.buy(
                        question=o.question, slug=o.slug, kind=o.kind, outcome=outcome,
                        book=clob.get_book(token_id), shares=shares,
                        fee_rate=float(d.get("fee_rate", 0.0)), bucket="arb",
                    )
                    ok = ok and got
                filled += 1 if ok else 0
            elif o.kind == "tail_harvest":
                got = account.buy(
                    question=o.question, slug=o.slug, kind=o.kind, outcome=str(d["outcome"]),
                    book=clob.get_book(str(d["token_id"])), shares=float(d["shares"]),
                    fee_rate=float(d.get("fee_rate", 0.0)), bucket="tail",
                )
                filled += 1 if got else 0
        except Exception:
            continue
    account.save(state_path)
    print(f"  paper: {filled} opportunities filled -> {state_path} (cash ${account.cash:,.2f}, open positions {len(account.positions)})")


def cmd_scan(args: argparse.Namespace) -> int:
    risk, scan_cfg = build_configs(Path(args.config))
    log_path = ROOT / "logs" / "opportunities.jsonl"
    skip_path = ROOT / "logs" / "skips.jsonl"
    heartbeat_path = ROOT / "logs" / "heartbeat.json"
    stop_file = ROOT / "STOP"
    account = None
    if args.paper:
        from .paper import PaperAccount

        account = PaperAccount.load(ROOT / "data" / "paper_state.json", starting_cash=risk.total_capital)
    while True:
        # Kill switch (article section 9): a STOP file in the repo root
        # halts the loop within one iteration. Remove it to resume.
        if stop_file.exists():
            print("STOP file present — halting. Remove the STOP file to resume.")
            return 0
        now = datetime.now(timezone.utc)
        eff_risk = risk
        breaker_halted = False
        if account is not None:
            account.roll_day(now.strftime("%Y-%m-%d"))
            # Size from the CURRENT balance, not the starting balance
            # (article section 8): winners compound, losses shrink size.
            equity = account.equity()
            eff_risk = RiskConfig(**{**risk.__dict__, "total_capital": equity})
            breaker_halted = CircuitBreaker(risk.daily_loss_limit_pct).halted(account.day_start_equity, equity)
        scanner = Scanner(eff_risk, scan_cfg)
        opps = scanner.scan_once(now)
        log_opportunities(opps, log_path, now)
        log_skips(scanner.skips, skip_path, now)
        heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
        heartbeat_path.write_text(
            json.dumps({"ts": now.isoformat(), "stats": scanner.stats, "opportunities": len(opps)}, indent=1),
            encoding="utf-8",
        )
        if args.paper and opps:
            if breaker_halted:
                print(f"  paper: CIRCUIT BREAKER halted (day equity ${account.equity():,.2f} vs start ${account.day_start_equity:,.2f}) — no new positions")
            else:
                _paper_fill(opps, account)
        by_kind: dict[str, int] = {}
        for o in opps:
            by_kind[o.kind] = by_kind.get(o.kind, 0) + 1
        print(
            f"[{now:%Y-%m-%d %H:%M:%S}Z] markets={scanner.stats['markets_seen']} "
            f"books={scanner.stats['books_fetched']} denied={scanner.stats['denied']} "
            f"no_book={scanner.stats.get('no_book', 0)} past_end={scanner.stats.get('past_end', 0)} "
            f"errors={scanner.stats['errors']} opportunities={len(opps)} {by_kind}"
        )
        for o in opps:
            print(f"  {o.kind:12s} profit~${o.expected_profit:>8.2f} capital=${o.capital_required:>9.2f}  {o.question[:90]}")
        if args.once:
            return 0
        time.sleep(max(10, args.interval))


def cmd_check(args: argparse.Namespace) -> int:
    """Pre-flight checklist (article appendix): machine-checkable items only."""
    import os
    import sys

    results: list[tuple[str, bool, str]] = []

    def add(name: str, ok: bool, note: str = "") -> None:
        results.append((name, ok, note))

    add("python >= 3.11", sys.version_info >= (3, 11), sys.version.split()[0])
    try:
        risk, _ = build_configs(Path(args.config))
        add("config loads, buckets sum to 100%", True, f"capital=${risk.total_capital:,.0f}")
    except Exception as e:
        add("config loads, buckets sum to 100%", False, str(e))
    for d in ("logs", "data"):
        try:
            p = ROOT / d / ".write_test"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("ok", encoding="utf-8")
            p.unlink()
            add(f"{d}/ writable", True)
        except Exception as e:
            add(f"{d}/ writable", False, str(e))
    add("STOP file absent (kill switch)", not (ROOT / "STOP").exists())
    live_on = os.environ.get("DS_ENABLE_LIVE") == "1" and os.environ.get("DS_LIVE_CONFIRM") == "I_UNDERSTAND"
    add("live switches OFF (paper-safe default)", not live_on)
    rules_file = ROOT / "rules_approved.json"
    if rules_file.exists():
        try:
            json.loads(rules_file.read_text(encoding="utf-8"))
            add("rules_approved.json parses", True)
        except Exception as e:
            add("rules_approved.json parses", False, str(e))
    else:
        add("rules_approved.json parses", True, "not created yet (live gate stays shut)")
    try:
        from .live import region_blocked

        verdict = region_blocked()
        add("geoblock endpoint reachable", True, f"blocked={verdict.get('blocked')} {verdict.get('country')}/{verdict.get('region')}")
    except Exception as e:
        add("geoblock endpoint reachable", False, f"{type(e).__name__}: {e}")
    failed = 0
    for name, ok, note in results:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {note}" if note else ""))
        failed += 0 if ok else 1
    print(f"\n{len(results) - failed}/{len(results)} checks passed")
    return 1 if failed else 0


def cmd_report(args: argparse.Namespace) -> int:
    log_path = ROOT / "logs" / "opportunities.jsonl"
    if not log_path.exists():
        print("No opportunities logged yet. Run a scan first.")
        return 0
    counts: dict[str, int] = {}
    profit = 0.0
    for line in log_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        counts[rec["kind"]] = counts.get(rec["kind"], 0) + 1
        profit += float(rec.get("expected_profit", 0))
    print("Opportunities by kind:", counts)
    print(f"Total expected profit across log: ${profit:.2f} (pre-slippage estimates from detection time)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ds-harvest", description="Deterministic Spread & Tail-End Harvesting — Polymarket scanner (paper-first)")
    parser.add_argument("--config", default=str(ROOT / "config.json"))
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_scan = sub.add_parser("scan")
    p_scan.add_argument("--once", action="store_true")
    p_scan.add_argument("--interval", type=int, default=60)
    p_scan.add_argument("--paper", action="store_true", help="simulate paper fills for found opportunities (state in data/paper_state.json)")
    p_scan.set_defaults(func=cmd_scan)
    p_rep = sub.add_parser("report")
    p_rep.set_defaults(func=cmd_report)
    p_chk = sub.add_parser("check", help="pre-flight checklist")
    p_chk.set_defaults(func=cmd_check)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
