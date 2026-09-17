"""Chart tag-trigger polish + momentum streak begin/end marks."""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import chart_marks as cm  # noqa: E402
import dapi_enrich as de  # noqa: E402
import desk_dash  # noqa: E402
import mom_streak as ms  # noqa: E402


class StreakSpanTests(unittest.TestCase):
    def test_start_is_first_day_of_current_run(self) -> None:
        series = [
            (date(2026, 9, 1), 3.0),
            (date(2026, 9, 2), 8.0),
            (date(2026, 9, 3), 9.0),
            (date(2026, 9, 4), 7.0),
        ]
        span = ms.streak_span(series)
        self.assertEqual(span["start"], "2026-09-02")
        self.assertEqual(span["end"], "2026-09-04")
        self.assertTrue(span["open"])
        self.assertEqual(span["side"], "above")
        self.assertEqual(span["n"], 3)

    def test_at_cut_has_no_span(self) -> None:
        self.assertIsNone(ms.streak_span([(date(2026, 9, 4), 5.0)]))

    def test_attach_writes_bounds(self) -> None:
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
        self.assertEqual(card["mom_streak_start"], "2026-09-01")
        self.assertEqual(card["mom_streak_end"], "2026-09-04")
        self.assertTrue(card["mom_streak_open"])


class LayoutTests(unittest.TestCase):
    def test_nearby_tag_labels_collapse(self) -> None:
        laid = cm.layout_marks(
            [
                {"x": 0.50, "kind": "tag", "label": "G4"},
                {"x": 0.52, "kind": "tag", "label": "EVT"},
                {"x": 0.20, "kind": "streak-start", "label": "s", "side": "above"},
                {"x": 0.90, "kind": "streak-end-open", "label": "now", "side": "above"},
            ]
        )
        tags = [m for m in laid if m["kind"] == "tag"]
        shown = [m for m in tags if m["show_label"]]
        self.assertEqual(len(shown), 1)
        self.assertIn("G4", shown[0]["label"])
        self.assertIn("EVT", shown[0]["label"])
        streaks = [m for m in laid if m["kind"].startswith("streak")]
        self.assertEqual(len(streaks), 2)
        self.assertNotEqual(streaks[0]["x"], streaks[1]["x"])
        # Captions sit on different y bands: tags near the top, streak at the bottom.
        self.assertLess(shown[0]["label_y"], streaks[0]["label_y"])

    def test_same_day_streak_is_one_caption(self) -> None:
        laid = cm.layout_marks(
            [
                {"x": 0.4, "kind": "streak-start", "label": "s"},
                {"x": 0.4, "kind": "streak-end-open", "label": "now"},
            ]
        )
        shown = [m for m in laid if m.get("show_label")]
        self.assertEqual(len(shown), 1)


class SvgTests(unittest.TestCase):
    def test_one_day_streak_is_single_diamond(self) -> None:
        card = {
            "ticker": "AAPL US Equity",
            "mom_streak_side": "above",
            "mom_streak_label": "↑1d>5",
            "mom_streak_start": "2026-09-05",
            "mom_streak_end": "2026-09-05",
            "mom_streak_open": True,
            "px_series": [
                (date(2026, 9, 4), 100.0),
                (date(2026, 9, 5), 101.0),
            ],
        }
        svg = cm.render_svg(card)
        self.assertEqual(svg.count('data-kind="streak-start"'), 1)
        self.assertNotIn('data-kind="streak-end', svg)
        self.assertIn(">1d</text>", svg)

    def test_svg_has_distinct_tag_and_streak_marks(self) -> None:
        card = {
            "ticker": "AAPL US Equity",
            "mom_streak_side": "above",
            "mom_streak_label": "↑4d>5",
            "mom_streak_start": "2026-09-02",
            "mom_streak_end": "2026-09-05",
            "mom_streak_open": True,
            "tag_triggers": [
                {"date": "2026-09-03", "label": "G4"},
                {"date": "2026-09-03", "label": "EVT"},
            ],
            "px_series": [
                (date(2026, 9, 1), 100.0),
                (date(2026, 9, 2), 101.0),
                (date(2026, 9, 3), 102.5),
                (date(2026, 9, 4), 101.8),
                (date(2026, 9, 5), 103.0),
            ],
        }
        svg = cm.render_svg(card)
        self.assertIn("fd-chart-line", svg)
        self.assertIn("fd-chart-mark-tag", svg)
        self.assertIn("fd-chart-mark-streak-start", svg)
        self.assertIn("fd-chart-mark-streak-end-open", svg)
        self.assertIn("G4 · EVT", svg)
        self.assertIn(">s</text>", svg)
        self.assertIn(">now</text>", svg)
        self.assertIn("tag trigger", svg)

    def test_render_html_includes_chart_and_options_refresh(self) -> None:
        rec = de.build_name_record(
            "AAPL US Equity",
            {"GICS_SECTOR_NAME": "Information Technology", "EQY_BETA": 1.1},
        )
        rec["mom_score"] = 9
        rec["tag_triggers"] = [{"date": "2026-09-11", "label": "G4"}]
        rec["px_series"] = [
            {"date": "2026-09-10", "px": 10},
            {"date": "2026-09-11", "px": 11},
            {"date": "2026-09-12", "px": 12},
        ]
        html = desk_dash.render_html(
            book={"asof": "test", "names": {"AAPL US Equity": rec}, "meta": {}},
            cache={},
            hist={
                "names": {
                    "AAPL US Equity": {
                        "series": [
                            {"date": "2026-09-10", "score": 9},
                            {"date": "2026-09-11", "score": 9},
                            {"date": "2026-09-12", "score": 8},
                        ]
                    }
                }
            },
        )
        self.assertIn("fd-chart", html)
        self.assertIn("fd-chart-mark-streak-start", html)
        self.assertIn("fd-chart-db", html)
        self.assertIn("fd-chart-marks-js", html)
        self.assertIn("Options Refresh", html)
        self.assertIn('id="options-refresh"', html)
        self.assertIn('classList.contains("fd-chart")', html)


class WriteCombinedChartTests(unittest.TestCase):
    def test_live_patch_keeps_chart_js_and_options_refresh(self) -> None:
        body = """<!DOCTYPE html>
<html><head><title>Factor Desk</title></head>
<body>
<nav>
  <button id="refresh">Refresh</button>
  <button>Momentum Up</button>
  <button>Momentum Down</button>
  <button>Outliers</button>
  <button>Options</button>
</nav>
<article class="card" data-t="AAPL US Equity">
  <svg class="chart" width="200" height="60">
    <text class="tag-label" data-tag-trigger="2026-09-03" x="40" y="10">G4</text>
    <text class="tag-label" data-tag-trigger="2026-09-03" x="42" y="10">EVT</text>
  </svg>
</article>
</body></html>"""
        pad = 2_700_000 - len(body.encode("utf-8"))
        live = body + ("<!--" + ("P" * max(pad, 1)) + "-->")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dest = root / "factorbook.html"
            dest.write_text(live, encoding="utf-8")
            rec = de.build_name_record("AAPL US Equity", {"GICS_SECTOR_NAME": "Information Technology"})
            rec["mom_score"] = 8
            book = {"asof": "test", "names": {"AAPL US Equity": rec}, "meta": {}}
            out = desk_dash.write_combined(dest, root=root, book=book)
            text = out.read_text(encoding="utf-8")
            self.assertGreater(out.stat().st_size, 2_000_000)
            self.assertIn("fd-chart-db", text)
            self.assertIn("fd-chart-marks-js", text)
            self.assertIn("__FD_CHART_MARKS__", text)
            self.assertIn("id=\"options-refresh\"", text)
            self.assertIn("class=\"chart\"", text)


if __name__ == "__main__":
    unittest.main()
