"""Unit tests — run with:  python -m unittest discover -s tests -v
(Standard library only, matching the runtime's zero-dependency design.)
"""
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ds_harvest.arbitrage import detect_basket_arb, detect_binary_arb
from ds_harvest.fees import taker_fee_per_share
from ds_harvest.kelly import kelly_fraction, tail_position_usd
from ds_harvest.models import Book, Level, MarketRef
from ds_harvest.paper import PaperAccount
from ds_harvest.risk import RiskConfig
from ds_harvest.tailend import detect_tail_candidate, exit_signal

NOW = datetime(2026, 10, 26, tzinfo=timezone.utc)


def book(token, asks, bids=None):
    return Book(
        token_id=token,
        asks=[Level(p, s) for p, s in asks],
        bids=[Level(p, s) for p, s in (bids or [])],
    )


def market(end_hours=48, fee=0.0, outcomes=("Yes", "No")):
    return MarketRef(
        market_id="1",
        question="Test market?",
        slug="test-market",
        end_date=NOW + timedelta(hours=end_hours),
        outcomes=list(outcomes),
        token_ids=[f"tok{i}" for i in range(len(outcomes))],
        fee_rate=fee,
    )


class TestBookAndFees(unittest.TestCase):
    def test_buy_cost_walks_levels(self):
        b = book("t", [(0.50, 100), (0.60, 100)])
        cost, avg = b.buy_cost(150)
        self.assertAlmostEqual(cost, 100 * 0.50 + 50 * 0.60)
        self.assertAlmostEqual(avg, cost / 150)

    def test_buy_cost_insufficient_depth(self):
        self.assertIsNone(book("t", [(0.50, 10)]).buy_cost(50))

    def test_fee(self):
        self.assertEqual(taker_fee_per_share(0.5, 0.0), 0.0)
        self.assertAlmostEqual(taker_fee_per_share(0.5, 0.1), 0.025)


class TestKelly(unittest.TestCase):
    def test_half_kelly_value(self):
        # p=0.995, price=0.985 -> full Kelly ~0.667, half ~0.333
        self.assertAlmostEqual(kelly_fraction(0.995, 0.985), 2 / 3, places=2)

    def test_tail_position_hard_cap(self):
        usd = tail_position_usd(p=0.995, price=0.985, total_capital=10_000, bucket_available=5_000, per_trade_cap_pct=0.05)
        self.assertEqual(usd, 500.0)  # 5% cap binds before Half-Kelly size

    def test_tail_position_bucket_cap(self):
        usd = tail_position_usd(p=0.995, price=0.985, total_capital=10_000, bucket_available=120, per_trade_cap_pct=0.05)
        self.assertEqual(usd, 120.0)

    def test_cap_band_enforced(self):
        with self.assertRaises(ValueError):
            tail_position_usd(p=0.99, price=0.98, total_capital=10_000, bucket_available=5_000, per_trade_cap_pct=0.10)


class TestBinaryArb(unittest.TestCase):
    def test_detects_and_sizes(self):
        m = market()
        opp = detect_binary_arb(m, book("y", [(0.48, 100)]), book("n", [(0.49, 100)]), budget=4_000, min_edge=0.005)
        self.assertIsNotNone(opp)
        self.assertEqual(opp.detail["shares_each_leg"], 100)
        self.assertAlmostEqual(opp.expected_profit, 3.0)
        self.assertAlmostEqual(opp.capital_required, 97.0)

    def test_no_edge_no_trade(self):
        m = market()
        self.assertIsNone(detect_binary_arb(m, book("y", [(0.52, 100)]), book("n", [(0.50, 100)]), budget=4_000, min_edge=0.005))

    def test_fees_can_kill_edge(self):
        m = market(fee=0.5)
        self.assertIsNone(detect_binary_arb(m, book("y", [(0.48, 100)]), book("n", [(0.49, 100)]), budget=4_000, min_edge=0.005))

    def test_depth_limits_size(self):
        m = market()
        yes = book("y", [(0.48, 50), (0.60, 1_000)])
        no = book("n", [(0.49, 1_000)])
        opp = detect_binary_arb(m, yes, no, budget=4_000, min_edge=0.005)
        self.assertIsNotNone(opp)
        self.assertEqual(opp.detail["shares_each_leg"], 50)  # deeper size breaks the spread


class TestBasketArb(unittest.TestCase):
    def test_three_leg_basket(self):
        legs = []
        for i in range(3):
            legs.append((market(outcomes=("Yes", "No")), book(f"t{i}", [(0.30, 100)])))
        opp = detect_basket_arb("Election", legs, budget=4_000, min_edge=0.005)
        self.assertIsNotNone(opp)
        self.assertAlmostEqual(opp.expected_profit, 10.0)

    def test_two_legs_rejected(self):
        legs = [(market(), book("a", [(0.3, 100)])), (market(), book("b", [(0.3, 100)]))]
        self.assertIsNone(detect_basket_arb("X", legs, budget=4_000, min_edge=0.005))


class TestTailHarvest(unittest.TestCase):
    def test_candidate_and_sizing(self):
        m = market(end_hours=12)
        books = [book("t0", [(0.985, 10_000)]), book("t1", [(0.02, 10_000)])]
        opp = detect_tail_candidate(
            m, books, now=NOW, total_capital=10_000, bucket_available=5_000,
            assumed_prob=0.995, per_trade_cap_pct=0.05,
        )
        self.assertIsNotNone(opp)
        self.assertEqual(opp.kind, "tail_harvest")
        self.assertEqual(opp.detail["position_usd"], 500.0)  # 5% hard cap

    def test_outside_time_window(self):
        m = market(end_hours=48)
        books = [book("t0", [(0.985, 10_000)]), book("t1", [(0.02, 10_000)])]
        self.assertIsNone(detect_tail_candidate(m, books, now=NOW, total_capital=10_000, bucket_available=5_000))

    def test_below_price_floor(self):
        m = market(end_hours=12)
        books = [book("t0", [(0.96, 10_000)]), book("t1", [(0.05, 10_000)])]
        self.assertIsNone(detect_tail_candidate(m, books, now=NOW, total_capital=10_000, bucket_available=5_000))

    def test_exit_signal(self):
        self.assertTrue(exit_signal(0.996, exit_target=0.995))
        self.assertFalse(exit_signal(0.99, exit_target=0.995))
        self.assertFalse(exit_signal(None))


class TestMarketDataErrors(unittest.TestCase):
    def test_book_404_maps_to_no_order_book(self):
        import io
        import urllib.error
        from unittest.mock import patch

        from ds_harvest.marketdata import ClobClient, NoOrderBook

        err = urllib.error.HTTPError("https://clob.polymarket.com/book", 404, "Not Found", {}, io.BytesIO(b""))
        with patch("ds_harvest.marketdata._get", side_effect=err):
            with self.assertRaises(NoOrderBook):
                ClobClient().get_book("123")


class TestRiskAndPaper(unittest.TestCase):
    def test_allocation_must_sum_to_one(self):
        with self.assertRaises(ValueError):
            RiskConfig(arb_alloc_pct=0.5, tail_alloc_pct=0.5, reserve_pct=0.1)

    def test_paper_round_trip(self):
        acct = PaperAccount(cash=10_000)
        b = book("tok0", [(0.50, 200)])
        self.assertTrue(acct.buy(question="Q", slug="s", kind="tail_harvest", outcome="Yes", book=b, shares=100, fee_rate=0.0, bucket="tail"))
        self.assertEqual(acct.cash, 9_950.0)
        acct.settle({"tok0"})
        self.assertEqual(acct.cash, 10_050.0)
        self.assertEqual(acct.realized_pnl, 50.0)


class TestMergedFromArticle(unittest.TestCase):
    """Additions merged from the methodology article (see README 融合说明)."""

    def test_logit_space(self):
        from ds_harvest.features import annualized_return, aroon, atr_z, book_imbalance, logit, roc_logit

        self.assertAlmostEqual(logit(0.5), 0.0)
        self.assertGreater(logit(0.95) - logit(0.90), logit(0.55) - logit(0.50))  # bounded-scale effect
        prices = [0.50, 0.55, 0.60, 0.58, 0.62]
        self.assertGreater(roc_logit(prices, 2), 0)
        self.assertGreater(atr_z(prices), 0)
        self.assertIsNotNone(aroon(prices, 4))
        self.assertAlmostEqual(book_imbalance(300, 100), 0.5)
        self.assertAlmostEqual(annualized_return(0.02, 30), 0.02 * 365 / 30)

    def test_scaled_kelly_and_ev(self):
        from ds_harvest.kelly import ev_per_dollar, scaled_kelly_fraction

        self.assertAlmostEqual(ev_per_dollar(0.995, 0.985), 0.995 / 0.985 - 1)
        self.assertAlmostEqual(
            scaled_kelly_fraction(0.995, 0.985, 0.25),
            scaled_kelly_fraction(0.995, 0.985, 0.5) / 2,
        )

    def test_tail_depth_impact_cap(self):
        m = market(end_hours=12)
        books = [book("t0", [(0.985, 200)]), book("t1", [(0.02, 10_000)])]
        opp = detect_tail_candidate(m, books, now=NOW, total_capital=10_000, bucket_available=5_000)
        self.assertIsNotNone(opp)
        # depth in band = 200 x 0.985 = $197; 20% impact cap -> ~$39.40
        self.assertAlmostEqual(opp.detail["position_usd"], 39.40, places=1)

    def test_tail_fee_can_kill(self):
        m = market(end_hours=12, fee=0.9)
        books = [book("t0", [(0.985, 10_000)]), book("t1", [(0.02, 10_000)])]
        self.assertIsNone(detect_tail_candidate(m, books, now=NOW, total_capital=10_000, bucket_available=5_000))

    def test_arb_annualized_gate(self):
        m = market(end_hours=24 * 300)
        yes, no = book("y", [(0.48, 100)]), book("n", [(0.49, 100)])
        self.assertIsNotNone(detect_binary_arb(m, yes, no, budget=4_000, min_edge=0.005, now=NOW))
        self.assertIsNone(detect_binary_arb(m, yes, no, budget=4_000, min_edge=0.005, min_annualized=0.5, now=NOW))

    def test_circuit_breaker(self):
        from ds_harvest.risk import CircuitBreaker

        br = CircuitBreaker(0.02)
        self.assertTrue(br.halted(10_000, 9_750))
        self.assertFalse(br.halted(10_000, 9_850))
        self.assertFalse(br.halted(0, 0))

    def test_event_exposure_cap(self):
        from ds_harvest.risk import BucketLedger, RiskConfig

        cfg = RiskConfig()
        led = BucketLedger()
        led.commit("tail_harvest", 1_000, event_key="ev1")
        self.assertEqual(led.event_available(cfg, "ev1"), 0.0)  # 10% of 10k used up
        self.assertGreater(led.event_available(cfg, "ev2"), 0.0)

    def test_paper_no_duplicate_token(self):
        acct = PaperAccount(cash=10_000)
        b = book("tok0", [(0.50, 500)])
        self.assertTrue(acct.buy(question="Q", slug="s", kind="tail_harvest", outcome="Yes", book=b, shares=100, fee_rate=0.0, bucket="tail"))
        self.assertFalse(acct.buy(question="Q", slug="s", kind="tail_harvest", outcome="Yes", book=b, shares=100, fee_rate=0.0, bucket="tail"))

    def test_paper_equity_marks(self):
        acct = PaperAccount(cash=10_000)
        b = book("tok0", [(0.50, 500)])
        acct.buy(question="Q", slug="s", kind="tail_harvest", outcome="Yes", book=b, shares=100, fee_rate=0.0, bucket="tail")
        acct.marks["tok0"] = 0.90
        self.assertAlmostEqual(acct.equity(), 9_950 + 90)


if __name__ == "__main__":
    unittest.main()
