"""Unit tests for Factor Desk sectors — no live Bloomberg."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.request import urlopen

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import add_server  # noqa: E402
import dapi_enrich as de  # noqa: E402
import desk_dash  # noqa: E402
import sectors  # noqa: E402


def _snap(
    ticker: str,
    sector: str | None,
    sub: str | None,
    r1d: float | None,
    r1w: float | None,
    r1m: float | None,
    rytd: float | None = None,
) -> dict:
    return {
        "ticker": de.name_key(ticker),
        "short": sectors.short_ticker(ticker),
        "gics_sector_name": sector,
        "gics_sub_industry_name": sub,
        "ret_1d": r1d,
        "ret_1w": r1w,
        "ret_1m": r1m,
        "ret_ytd": rytd,
        "trend_score": None,
    }


class RankAndScoreTests(unittest.TestCase):
    def test_percentile_order_and_ties(self) -> None:
        ranks = sectors.percentile_ranks([0.01, 0.02, 0.03, None])
        self.assertEqual(ranks[0], 0.0)
        self.assertEqual(ranks[2], 100.0)
        self.assertIsNone(ranks[3])
        tied = sectors.percentile_ranks([1.0, 1.0, 2.0])
        self.assertAlmostEqual(tied[0], tied[1])
        self.assertLess(tied[0], tied[2])
        self.assertEqual(sectors.percentile_ranks([0.5]), [50.0])

    def test_one_month_only_is_null(self) -> None:
        # Lagging chase: 1M rank without 1W/1D must not fire.
        self.assertIsNone(sectors.early_trend_score(None, None, 90.0))

    def test_one_week_only_equals_rank(self) -> None:
        self.assertAlmostEqual(sectors.early_trend_score(None, 80.0, None), 80.0)

    def test_acceleration_beats_lagging_run(self) -> None:
        # Same 1W rank; the name whose 1W is beating 1M scores higher.
        early = sectors.early_trend_score(50.0, 80.0, 20.0)
        late = sectors.early_trend_score(50.0, 80.0, 90.0)
        self.assertIsNotNone(early)
        self.assertIsNotNone(late)
        self.assertGreater(early, late)  # type: ignore[operator]

    def test_weights_sum_documented(self) -> None:
        self.assertAlmostEqual(sectors.W_1D + sectors.W_1W + sectors.W_1M + sectors.W_ACCEL, 1.0)


class ReturnsAndGicsParseTests(unittest.TestCase):
    def test_chg_pct_is_percent_units(self) -> None:
        val, field, reason = de.first_success({"CHG_PCT_5D": 2.5}, "ret_1w", as_type="pct")
        self.assertAlmostEqual(val, 0.025)
        self.assertEqual(field, "CHG_PCT_5D")
        self.assertIsNone(reason)

    def test_gics_name_not_code(self) -> None:
        parsed = sectors.parse_gics({"GICS_SECTOR_NAME": "Information Technology", "GICS_SUB_INDUSTRY_NAME": "Semiconductors"})
        self.assertEqual(parsed["gics_sector_name"], "Information Technology")
        code_only = sectors.parse_gics({"GICS_SECTOR": "45", "GICS_SUB_INDUSTRY": "45301020"})
        self.assertIsNone(code_only["gics_sector_name"])
        self.assertIsNone(code_only["gics_sub_industry_name"])
        self.assertEqual(code_only["null_reasons"]["gics_sector_name"], "code_only_no_name")

    def test_prices_ctx_wins_over_dapi_pct(self) -> None:
        rets = sectors.parse_returns(
            {"CHG_PCT_5D": 50.0},
            prices_ctx_row={"ret_1w": 0.02},
        )
        self.assertAlmostEqual(rets["ret_1w"], 0.02)

    def test_returns_from_closes(self) -> None:
        start = date(2026, 1, 2)
        closes = []
        px = 100.0
        for i in range(30):
            closes.append({"date": start + timedelta(days=i), "px": px})
            px *= 1.01
        out = sectors.returns_from_closes(closes)
        self.assertIsNotNone(out["ret_1d"])
        self.assertIsNotNone(out["ret_1w"])
        self.assertGreater(out["ret_1w"], out["ret_1d"])  # type: ignore[operator]
        self.assertIsNotNone(out["ret_ytd"])


class AggregationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.snaps = [
            _snap("AAPL", "Information Technology", "Technology Hardware, Storage & Peripherals", 0.01, 0.04, 0.02, 0.10),
            _snap("MSFT", "Information Technology", "Systems Software", 0.00, 0.03, 0.05, 0.12),
            _snap("NVDA", "Information Technology", "Semiconductors", 0.02, 0.08, 0.03, 0.40),
            _snap("XOM", "Energy", "Integrated Oil & Gas", -0.01, -0.03, -0.02, 0.01),
            _snap("CVX", "Energy", "Integrated Oil & Gas", 0.00, -0.02, -0.04, -0.05),
            _snap("JPM", "Financials", "Diversified Banks", 0.005, 0.01, 0.02, 0.08),
            _snap("ORPHAN", None, None, 0.10, 0.20, 0.30, 0.50),
        ]
        sectors.score_snaps(self.snaps)

    def test_unclassified_omitted_no_invented_map(self) -> None:
        sectors_list, subs = sectors.aggregate_groups(self.snaps)
        names = {r["name"] for r in sectors_list}
        self.assertEqual(names, {"Information Technology", "Energy", "Financials"})
        self.assertTrue(all(r["n"] >= 1 for r in sectors_list))
        orphan = [s for s in self.snaps if s["short"] == "ORPHAN"][0]
        self.assertIsNone(orphan["gics_sector_name"])

    def test_equal_weight_sector_return(self) -> None:
        sectors_list, _ = sectors.aggregate_groups(self.snaps)
        energy = next(r for r in sectors_list if r["name"] == "Energy")
        expected = (-0.03 + -0.02) / 2.0
        self.assertAlmostEqual(energy["ret_1w"], expected, places=6)
        self.assertEqual(energy["weighting"], "equal")
        self.assertEqual(energy["n"], 2)

    def test_sub_industries_present(self) -> None:
        _, subs = sectors.aggregate_groups(self.snaps)
        sub_names = {r["name"] for r in subs}
        self.assertIn("Semiconductors", sub_names)
        self.assertIn("Integrated Oil & Gas", sub_names)
        oil = next(r for r in subs if r["name"] == "Integrated Oil & Gas")
        self.assertEqual(oil["parent_sector"], "Energy")
        self.assertEqual(oil["n"], 2)

    def test_top_bottom_no_overlap_and_condensed(self) -> None:
        tech = [s for s in self.snaps if s["gics_sector_name"] == "Information Technology"]
        top, bottom = sectors.top_bottom(tech, k=1)
        self.assertEqual(len(top), 1)
        self.assertEqual(len(bottom), 1)
        self.assertNotEqual(top[0]["ticker"], bottom[0]["ticker"])
        tiny_top, tiny_bot = sectors.top_bottom(tech[:2], k=3)
        self.assertTrue(tiny_top)
        self.assertEqual(tiny_bot, [])
        five = tech + [
            {**tech[0], "ticker": "T4 US Equity", "short": "T4", "trend_score": 40.0},
            {**tech[0], "ticker": "T5 US Equity", "short": "T5", "trend_score": 10.0},
        ]
        top3, bot2 = sectors.top_bottom(five, k=3)
        self.assertEqual(len(top3), 3)
        self.assertEqual(len(bot2), 2)
        top_t = {m["ticker"] for m in top3}
        bot_t = {m["ticker"] for m in bot2}
        self.assertFalse(top_t & bot_t)

    def test_nvda_early_vs_msft_lagging(self) -> None:
        by = {s["short"]: s["trend_score"] for s in self.snaps}
        self.assertIsNotNone(by["NVDA"])
        self.assertIsNotNone(by["MSFT"])
        # NVDA: strong 1W vs milder 1M; MSFT: 1W < 1M (already ran).
        self.assertGreater(by["NVDA"], by["MSFT"])  # type: ignore[operator]


class BookAndRefreshTests(unittest.TestCase):
    def test_build_from_dict_session_writes_json(self) -> None:
        table = {
            "AAPL US Equity": {
                "GICS_SECTOR_NAME": "Information Technology",
                "GICS_SUB_INDUSTRY_NAME": "Technology Hardware, Storage & Peripherals",
                "CHG_PCT_5D": 3.0,
                "CHG_PCT_1M": 1.0,
                "CHG_PCT_YTD": 12.0,
            },
            "XOM US Equity": {
                "GICS_SECTOR_NAME": "Energy",
                "GICS_SUB_INDUSTRY_NAME": "Integrated Oil & Gas",
                "CHG_PCT_5D": -2.0,
                "CHG_PCT_1M": -1.0,
                "CHG_PCT_YTD": 4.0,
            },
            "ZZZ US Equity": {
                "CHG_PCT_5D": 9.0,
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            book = sectors.build_sectors_book(
                ["AAPL", "XOM", "ZZZ"],
                de.DictSession(table),
                root=root,
            )
            self.assertTrue((root / "sectors.json").is_file())
            self.assertEqual(len(book["sectors"]), 2)
            self.assertEqual(book["meta"]["n_unclassified"], 1)
            self.assertIn("ZZZ", book["meta"]["unclassified"])
            tech = next(r for r in book["sectors"] if r["name"] == "Information Technology")
            self.assertAlmostEqual(tech["ret_1w"], 0.03)
            self.assertTrue(tech["top"])
            html = desk_dash.render_html(cards=[], book=None, sectors_book=book)
            self.assertIn('data-tab="sectors"', html)
            self.assertIn('id="tab-sectors"', html)
            self.assertIn('id="sectors-db"', html)
            self.assertIn("Information Technology", html)
            self.assertIn("GICS sectors", html)

    def test_refresh_writes_sectors_and_html(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = add_server.run_refresh(tickers=["AAPL"], intraday=False, root=root)
            self.assertTrue(result["ok"])
            self.assertTrue((root / "sectors.json").is_file())
            self.assertTrue((root / "factorbook.html").is_file())
            html = (root / "factorbook.html").read_text(encoding="utf-8")
            self.assertIn("Sectors", html)
            self.assertIn("si_ratio", html)
            self.assertIn('data-tab="sectors"', html)

    def test_sidecar_serves_sectors_json(self) -> None:
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), add_server.DeskHandler)
        port = httpd.server_address[1]
        t = Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        try:
            with urlopen(f"http://127.0.0.1:{port}/sectors.json", timeout=5) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            self.assertIn("sectors", body)
            self.assertIn("sub_industries", body)
        finally:
            httpd.shutdown()


class HtmlCardTests(unittest.TestCase):
    def test_cards_include_top_bottom_and_drill_hook(self) -> None:
        snaps = [
            _snap("AAA", "Health Care", "Biotechnology", 0.02, 0.06, 0.01, 0.20),
            _snap("BBB", "Health Care", "Biotechnology", 0.00, 0.01, 0.04, 0.05),
            _snap("CCC", "Health Care", "Biotechnology", -0.01, -0.03, 0.00, -0.02),
            _snap("DDD", "Health Care", "Pharmaceuticals", 0.01, 0.02, 0.02, 0.08),
        ]
        sectors.score_snaps(snaps)
        sec_list, sub_list = sectors.aggregate_groups(snaps)
        book = {
            "asof": "test",
            "weighting": "equal",
            "score": sectors.score_meta(),
            "sectors": sec_list,
            "sub_industries": sub_list,
            "meta": {"n_names": 4, "n_classified": 4, "n_unclassified": 0},
        }
        html = desk_dash.render_html(book={"asof": "test", "names": {}, "meta": {}}, sectors_book=book)
        self.assertIn("Health Care", html)
        self.assertIn("Biotechnology", html)
        self.assertIn("sec-drill", html)
        self.assertIn("early_trend", html)
        # JS renderer consumes JSON; payload must include top/bottom members.
        self.assertIn('"top"', html)
        self.assertIn('"bottom"', html)
        self.assertIn("AAA", html)
        self.assertIn("data-tab=\"mom\"", html)
        self.assertIn("data-tab=\"opt\"", html)
        self.assertIn("score v2 is unchanged", html.lower())


if __name__ == "__main__":
    unittest.main()
