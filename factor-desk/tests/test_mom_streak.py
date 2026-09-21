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

    def test_tag_label_arrows_are_unicode_up_down(self) -> None:
        up = ms.tag_label(12, "above")
        down = ms.tag_label(89, "below")
        self.assertEqual(ord(up[0]), 0x2191)
        self.assertEqual(ord(down[0]), 0x2193)
        self.assertEqual(up, "\u219112d>5")
        self.assertEqual(down, "\u219389d<5")
        self.assertEqual(ms.ARROW_UP, "\u2191")
        self.assertEqual(ms.ARROW_DOWN, "\u2193")

    def test_short_empty_does_not_raise(self) -> None:
        self.assertEqual(ms._short(""), "")
        self.assertEqual(ms._short("   "), "")
        self.assertEqual(ms._short("DT US Equity"), "DT")


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


def _weekdays_ending(end: date, n: int) -> list[date]:
    days: list[date] = []
    d = end
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    days.reverse()
    return days


def _write_prices_long(
    root: Path,
    *,
    n: int = 90,
    end: date | None = None,
    names: dict[str, str] | None = None,
) -> list[date]:
    """names: ticker → 'up' | 'down'. Default two persistent-side names."""
    end = end or date.today()
    days = _weekdays_ending(end, n)
    specs = names or {"UP US Equity": "up", "DOWN US Equity": "down"}
    lines = ["date,ticker,adj_close"]
    for ticker, side in specs.items():
        px = 100.0
        for _i, day in enumerate(days):
            px *= 1.012 if side == "up" else 0.988
            lines.append(f"{day.isoformat()},{ticker},{px:.6f}")
    (root / "prices_long.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return days


class PricesLongBackfillTests(unittest.TestCase):
    def test_hist_is_thin_when_missing_or_one_day(self) -> None:
        self.assertTrue(ms.hist_is_thin(None))
        self.assertTrue(ms.hist_is_thin({"names": {}}))
        self.assertTrue(
            ms.hist_is_thin(
                {
                    "asof": "2026-09-17",
                    "names": {
                        "AAPL US Equity": {"series": [{"date": "2026-09-17", "score": 8}]},
                        "JPM US Equity": {"series": [{"date": "2026-09-17", "score": 3}]},
                    },
                }
            )
        )
        rich = {
            "names": {
                f"T{i} US Equity": {
                    "series": [{"date": f"2026-09-{d:02d}", "score": 8} for d in range(1, 10)]
                }
                for i in range(3)
            }
        }
        self.assertFalse(ms.hist_is_thin(rich))

    def test_no_hist_prices_long_rebuild_has_multiday_streaks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            days = _write_prices_long(root, n=90, end=date.today())
            asof = days[-1]
            cards = [
                {"ticker": "UP US Equity", "mom_score": 11},
                {"ticker": "DOWN US Equity", "mom_score": 2},
            ]
            hist = ms.rebuild_hist_for_cards(cards, root=root, asof=asof, write=True)
            self.assertTrue((root / ms.HIST_FILENAME).is_file())
            self.assertEqual(hist["meta"]["source"], ms.BACKFILL_SOURCE_PRICES_LONG)
            self.assertGreater(cards[0]["mom_streak"], 1)
            self.assertEqual(cards[0]["mom_streak_side"], "above")
            self.assertGreater(cards[1]["mom_streak"], 1)
            self.assertEqual(cards[1]["mom_streak_side"], "below")
            up_series = ms.series_of(hist, "UP US Equity")
            self.assertGreaterEqual(len(up_series), 5)
            self.assertEqual(up_series[-1][1], 11)  # live card today wins
            self.assertNotEqual(up_series[-2][1], 11)  # prior days from backfill

    def test_thin_one_day_hist_still_backfills(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            days = _write_prices_long(root, n=90, end=date.today())
            asof = days[-1]
            thin = {
                "asof": asof.isoformat(),
                "threshold": 5,
                "names": {
                    "UP US Equity": {
                        "series": [{"date": asof.isoformat(), "score": 9}],
                        "score": 9,
                    }
                },
                "meta": {"source": "empty"},
            }
            ms.write_hist(thin, root=root)
            cards = [{"ticker": "UP US Equity", "mom_score": 9}]
            hist = ms.rebuild_hist_for_cards(cards, root=root, asof=asof, write=True)
            self.assertGreater(len(ms.series_of(hist, "UP US Equity")), 5)
            self.assertGreater(cards[0]["mom_streak"], 1)
            self.assertIn("backfill", str(hist["meta"]["source"]))

    def test_write_combined_fills_streak_db_not_empty_object(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            days = _write_prices_long(root, n=90, end=date.today())
            rec = de.build_name_record(
                "UP US Equity",
                {"GICS_SECTOR_NAME": "Information Technology", "EQY_BETA": 1.0},
            )
            rec["mom_score"] = 10
            rec2 = de.build_name_record(
                "DOWN US Equity",
                {"GICS_SECTOR_NAME": "Energy", "EQY_BETA": 0.8},
            )
            rec2["mom_score"] = 1
            book = {
                "asof": days[-1].isoformat(),
                "names": {"UP US Equity": rec, "DOWN US Equity": rec2},
                "meta": {},
            }
            dest = desk_dash.write_combined(root / "factorbook.html", root=root, book=book)
            text = dest.read_text(encoding="utf-8")
            self.assertIn('id="mom-streak-db"', text)
            self.assertNotIn('id="mom-streak-db">{}</script>', text)
            self.assertIn("UP US Equity", text)
            hist = ms.load_hist(root=root)
            filled = ms.streak_db([], hist=hist)
            self.assertTrue(filled)
            self.assertGreater(filled["UP US Equity"]["streak"], 1)
            patched = ms.ensure_embedded("<html><body></body></html>", filled)
            self.assertIn('id="mom-streak-db"', patched)
            self.assertNotIn(">{}</script>", patched)
            self.assertRegex(text, r'id="mom-streak-db">\{.+\}</script>')


class LiveCardFieldTests(unittest.TestCase):
    """Desktop FLAGS/WATCH/MOM cards use ``t`` / ``score``, not ticker / mom_score."""

    def test_attach_card_reads_t_and_score_sets_ticker(self) -> None:
        days = [date(2026, 1, 2) + timedelta(days=i) for i in range(89)]
        hist = {
            "names": {
                "DT US Equity": {
                    "series": [{"date": d.isoformat(), "score": 8} for d in days],
                }
            }
        }
        card = {"t": "DT", "score": 9}
        ms.attach_card(card, hist, asof=days[-1])
        self.assertEqual(card["ticker"], "DT")
        self.assertEqual(card["mom_score"], 9)
        self.assertEqual(card["mom_score_source"], "score")
        self.assertGreater(card["mom_streak"], 1)
        self.assertEqual(card["mom_streak_side"], "above")
        self.assertEqual(card["mom_streak_label"][0], "\u2191")
        self.assertGreaterEqual(card["mom_streak"], 80)

    def test_attach_all_without_hist_uses_mom_score_hist_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            days = _weekdays_ending(date.today(), 40)
            hist = {
                "asof": days[-1].isoformat(),
                "threshold": 5,
                "names": {
                    "DT US Equity": {
                        "series": [{"date": d.isoformat(), "score": 8} for d in days],
                        "score": 8,
                        "streak": 40,
                        "side": "above",
                        "label": "\u219140d>5",
                    }
                },
                "meta": {"source": "prices_long.trend_windows_backfill"},
            }
            ms.write_hist(hist, root=root)
            cards = [{"t": "DT US Equity", "score": 8}]
            out = ms.attach_all(cards, root=root, asof=days[-1])
            self.assertGreater(out[0]["mom_streak"], 1)
            self.assertEqual(out[0]["ticker"], "DT US Equity")
            cards2 = [{"t": "DT US Equity", "score": 8}]
            out2 = desk_dash.attach_all(cards2, root=root)
            self.assertGreater(out2[0]["mom_streak"], 1)
            self.assertEqual(out2[0]["ticker"], "DT US Equity")
            self.assertEqual(out2[0]["mom_streak_label"][0], "\u2191")


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

    def test_embed_db_does_not_html_escape_gt(self) -> None:
        html = ms.embed_db({"X": {"label": "↑12d>5"}})
        self.assertIn("12d>5", html)
        self.assertNotIn("&gt;", html)
        self.assertIn("<\\/", ms._script_json('{"x":"</script>"}'))

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
        self.assertIn("d&gt;5", html)  # HTML pill markup still escapes
        self.assertIn("d>5", html)  # #mom-streak-db JSON must keep raw >
        self.assertIn('id="mom-streak-db"', html)
        self.assertNotIn("var STRIP_ID", html)
        self.assertNotIn('id="gics-filter-strip"', html)


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
            self.assertNotIn('id="gics-sector-db"', text)
            self.assertNotIn("var STRIP_ID", text)
            self.assertNotIn('id="gics-filter-strip"', text)
            self.assertIn(".gchip", text)
            self.assertIn("mom-streak-db", text)
            self.assertNotIn('data-tab="sectors"', text)
            self.assertIn('<button id="refresh">Refresh</button>', text)
            self.assertIn('id="options-refresh"', text)
            self.assertIn("Options Refresh", text)
            self.assertIn("sidecar-progress", text)
            self.assertIn("sidecar-refresh-js", text)
            self.assertIn("data-sidecar-options-refresh", text)

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
            dest.unlink(missing_ok=True)
            dest = desk_dash.write_combined(root / "factorbook.html", root=root, book=book)
            html = dest.read_text(encoding="utf-8")
            self.assertIn("Financials", html)
            self.assertNotIn("var STRIP_ID", html)
            self.assertNotIn('id="gics-filter-strip"', html)
            self.assertIn("Refresh", html)

    def test_ensure_embedded_replaces_empty_db(self) -> None:
        html = """<!DOCTYPE html><html><body>
<div id="gics-filter-strip"></div>
<style>.gchip{} .gics-hid{display:none}</style>
<script type="application/json" id="gics-sector-db">{}</script>
</body></html>"""
        out = gf.ensure_embedded(html, {"AAPL US Equity": "Information Technology"})
        self.assertNotIn('id="gics-filter-strip"', out)
        self.assertNotIn('id="gics-sector-db"', out)
        self.assertNotIn("var STRIP_ID", out)


if __name__ == "__main__":
    unittest.main()
