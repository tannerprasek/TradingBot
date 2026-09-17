"""Momentum score streak tags + write_combined GICS survival. No Bloomberg."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import dapi_enrich as de  # noqa: E402
import desk_dash  # noqa: E402
import gics_filter as gf  # noqa: E402
import mom_streak as ms  # noqa: E402
import write_dash  # noqa: E402


class ScoreResolveTests(unittest.TestCase):
    def test_prefers_mom_score_over_options_v2(self) -> None:
        score, src = ms.resolve_card_score({"mom_score": 8, "score_v2": 1.7, "score": 99})
        self.assertEqual(score, 8)
        self.assertEqual(src, "mom_score")

    def test_skips_100_scale_trend_score(self) -> None:
        score, src = ms.resolve_card_score({"trend_score": 72.4, "score_v2": 0.2})
        self.assertIsNone(score)
        self.assertIsNone(src)

    def test_accepts_small_score_field(self) -> None:
        score, src = ms.resolve_card_score({"score": 11})
        self.assertEqual(score, 11)
        self.assertEqual(src, "score")


class StreakRuleTests(unittest.TestCase):
    def test_above_counts_consecutive_including_today(self) -> None:
        streak, side = ms.streak_vs_threshold([3, 6, 7, 8, 9])
        self.assertEqual(side, "above")
        self.assertEqual(streak, 4)
        self.assertEqual(ms.tag_label(streak, side), "↑4d>5")

    def test_below_breaks_on_at_threshold(self) -> None:
        streak, side = ms.streak_vs_threshold([4, 3, 2, 5, 3])
        self.assertEqual(side, "below")
        self.assertEqual(streak, 1)

    def test_exactly_five_is_neither_streak_zero(self) -> None:
        streak, side = ms.streak_vs_threshold([8, 7, 5])
        self.assertEqual(side, "at")
        self.assertEqual(streak, 0)
        self.assertEqual(ms.tag_label(streak, side), "=5")

    def test_date_gap_breaks_trading_day_streak(self) -> None:
        streak, side = ms.streak_from_dated(
            [
                (date(2026, 9, 1), 8),
                (date(2026, 9, 2), 9),
                (date(2026, 9, 14), 8),
            ]
        )
        self.assertEqual(side, "above")
        self.assertEqual(streak, 1)
        streak, side = ms.streak_vs_threshold([8, None])
        self.assertEqual(streak, 0)
        self.assertIsNone(side)
        self.assertIsNone(ms.tag_label(streak, side))

    def test_trend_window_score_is_0_to_13(self) -> None:
        up = [100.0 + i for i in range(220)]
        down = [320.0 - i for i in range(220)]
        self.assertEqual(ms.trend_window_score(up), 13)
        self.assertEqual(ms.trend_window_score(down), 0)
        self.assertIsNone(ms.trend_window_score([10.0, 11.0]))


class HistAndPriceTests(unittest.TestCase):
    def test_prices_csv_builds_score_series(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            start = date(2026, 1, 2)
            lines = ["date,ticker,px_last"]
            px = 100.0
            for i in range(80):
                px *= 1.01
                lines.append(f"{(start + timedelta(days=i)).isoformat()},AAPL US Equity,{px:.4f}")
            (root / "prices.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
            series, source = ms.discover_score_series(root)
            self.assertIn("prices.trend_windows", source)
            self.assertIn("AAPL US Equity", series)
            last = series["AAPL US Equity"][-1][1]
            self.assertGreater(last, 5)

    def test_residual_only_csv_is_not_treated_as_rank(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "v0_residuals.csv").write_text(
                "date,ticker,residual\n2026-09-01,AAPL US Equity,0.04\n",
                encoding="utf-8",
            )
            series, source = ms.discover_score_series(root)
            self.assertEqual(series, {})
            self.assertEqual(source, "none")


class CardTagTests(unittest.TestCase):
    def test_attach_writes_pill(self) -> None:
        hist = {
            "names": {
                "AAPL US Equity": {
                    "series": [
                        {"date": "2026-09-01", "score": 8},
                        {"date": "2026-09-02", "score": 9},
                        {"date": "2026-09-03", "score": 7},
                    ]
                }
            }
        }
        card = {"ticker": "AAPL US Equity", "mom_score": 8}
        ms.attach_card(card, hist, asof=date(2026, 9, 4))
        self.assertEqual(card["mom_streak_side"], "above")
        self.assertEqual(card["mom_streak"], 4)
        self.assertEqual(card["mom_streak_label"], "↑4d>5")
        self.assertTrue(any(p["key"] == "mom-streak" for p in card["enrich_pills"]))

    def test_render_html_has_tag_and_nav(self) -> None:
        rec = de.build_name_record(
            "AAPL US Equity",
            {"GICS_SECTOR_NAME": "Information Technology", "EQY_BETA": 1.1},
        )
        rec["mom_score"] = 9
        html = desk_dash.render_html(
            book={"asof": "test", "names": {"AAPL US Equity": rec}, "meta": {}},
            cache={},
            hist={
                "names": {
                    "AAPL US Equity": {
                        "series": [{"date": "2026-09-10", "score": 9}, {"date": "2026-09-11", "score": 9}]
                    }
                }
            },
        )
        self.assertIn("Refresh", html)
        self.assertIn("Momentum Up", html)
        self.assertIn("Momentum Down", html)
        self.assertIn("Outliers", html)
        self.assertIn("Options", html)
        self.assertIn('data-t="AAPL US Equity"', html)
        self.assertIn("↑", html)
        self.assertIn("d&gt;5", html)
        self.assertIn('id="mom-streak-db"', html)
        self.assertIn("var STRIP_ID", html)


class WriteCombinedSurviveTests(unittest.TestCase):
    def _live_html(self, payload: str = "__GICS_SECTOR_DB__") -> str:
        body = f"""<!DOCTYPE html>
<html><head><title>Factor Desk</title>
<style>
.filter-chip, .gchip {{ border: 1px solid #333; }}
.gics-hid {{ display: none !important; }}
</style></head>
<body>
<nav>
  <button id="refresh">Refresh</button>
  <button>Momentum Up</button>
  <button>Momentum Down</button>
  <button>Outliers</button>
  <button>Options</button>
</nav>
<div id="gics-filter-strip" class="filter-strip gics-chips"></div>
<article class="card" data-t="AAPL US Equity" data-ticker="AAPL US Equity">AAPL</article>
<script type="application/json" id="gics-sector-db">{payload}</script>
</body></html>"""
        # Live desk is ~2.7MB. Pad so write_combined must not shrink to skinny.
        pad = 2_700_000 - len(body.encode("utf-8"))
        return body + ("<!--" + ("P" * max(pad, 1)) + "-->")

    def test_write_combined_fills_wiped_db_and_js_without_shrinking(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dest = root / "factorbook.html"
            dest.write_text(self._live_html(), encoding="utf-8")
            before = dest.stat().st_size
            self.assertGreater(before, 2_000_000)
            rec = de.build_name_record(
                "AAPL US Equity",
                {"GICS_SECTOR_NAME": "Information Technology", "EQY_BETA": 1.0},
            )
            rec["mom_score"] = 3
            book = {"asof": "test", "names": {"AAPL US Equity": rec}, "meta": {}}
            out = desk_dash.write_combined(dest, root=root, book=book)
            text = out.read_text(encoding="utf-8")
            self.assertGreater(out.stat().st_size, 2_000_000)
            self.assertGreaterEqual(out.stat().st_size, before - 1024)
            self.assertIn("Refresh", text)
            self.assertIn("Momentum Up", text)
            self.assertIn("Momentum Down", text)
            self.assertIn("Outliers", text)
            self.assertIn("Options", text)
            self.assertNotIn("__GICS_SECTOR_DB__", text)
            self.assertIn('id="gics-sector-db"', text)
            self.assertIn("Information Technology", text)
            self.assertIn("var STRIP_ID", text)
            self.assertIn("data-t", gf.strip_js())
            self.assertIn('id="gics-filter-strip"', text)
            self.assertIn(".gics-hid", text)
            self.assertIn(".gchip", text)
            self.assertIn("mom-streak-db", text)
            self.assertNotIn('data-tab="sectors"', text)

    def test_write_dash_entry_embeds_gics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rec = de.build_name_record(
                "JPM US Equity",
                {"GICS_SECTOR_NAME": "Financials"},
            )
            rec["mom_score"] = 11
            book = {"asof": "test", "names": {"JPM US Equity": rec}, "meta": {}}
            dest = write_dash.write(root / "factorbook.html", root=root)
            # write_dash → write_combined; book was not passed so it loads enrich file.
            de.write_enrichment(book, root / de.ENRICH_FILENAME)
            dest = desk_dash.write_combined(root / "factorbook.html", root=root, book=book)
            html = dest.read_text(encoding="utf-8")
            self.assertIn("Financials", html)
            self.assertIn("var STRIP_ID", html)
            self.assertIn("Refresh", html)

    def test_ensure_embedded_replaces_empty_db(self) -> None:
        html = """<!DOCTYPE html><html><body>
<div id="gics-filter-strip"></div>
<style>.gchip{} .gics-hid{display:none}</style>
<script type="application/json" id="gics-sector-db">{}</script>
</body></html>"""
        out = gf.ensure_embedded(html, {"AAPL US Equity": "Information Technology"})
        self.assertIn("Information Technology", out)
        self.assertIn("var STRIP_ID", out)
        self.assertIn("data-t", out)
        self.assertNotIn(">{}</script>", out)


if __name__ == "__main__":
    unittest.main()
