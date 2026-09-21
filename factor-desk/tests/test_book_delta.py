"""Book-delta snapshot diff. No Bloomberg."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import book_delta as bd  # noqa: E402


def _card(ticker: str, *, score: float, side: str, streak: int, list_name: str | None = None) -> dict:
    card = {
        "t": ticker.split()[0],
        "ticker": ticker,
        "score": score,
        "mom_score": score,
        "mom_streak": streak,
        "mom_streak_side": side,
        "mom_streak_label": f"{'↑' if side == 'above' else '↓'}{streak}d>5" if side == "above" else f"↓{streak}d<5",
    }
    if list_name:
        card["list"] = list_name
    return card


class DiffTests(unittest.TestCase):
    def test_no_prior_is_quiet_baseline(self) -> None:
        snap = bd.snapshot_from_cards([_card("AAPL US Equity", score=8, side="above", streak=6, list_name="FLAGS")])
        diff = bd.diff_snapshots(None, snap)
        self.assertTrue(diff["baseline"])
        self.assertEqual(diff["chips"][0]["label"], "baseline set")
        self.assertEqual(diff["new_flags"], [])

    def test_flags_watch_outlier_streak_and_score_jump(self) -> None:
        prior_cards = [
            _card("AAPL US Equity", score=7, side="above", streak=20, list_name="FLAGS"),
            _card("MSFT US Equity", score=6, side="above", streak=8, list_name="WATCH"),
            _card("JPM US Equity", score=4, side="below", streak=4),
        ]
        now_cards = [
            _card("NVDA US Equity", score=9, side="above", streak=3, list_name="FLAGS"),
            _card("MSFT US Equity", score=6, side="above", streak=9, list_name="FLAGS"),
            _card("JPM US Equity", score=2, side="below", streak=6, list_name="OUTLIERS"),
            _card("AAPL US Equity", score=4, side="below", streak=1),
        ]
        prior = bd.snapshot_from_cards(prior_cards, asof="2026-09-16")
        now = bd.snapshot_from_cards(now_cards, asof="2026-09-17")
        diff = bd.diff_snapshots(prior, now)
        self.assertFalse(diff["baseline"])
        self.assertIn("NVDA US Equity", diff["new_flags"])
        self.assertIn("MSFT US Equity", diff["new_flags"])
        self.assertIn("AAPL US Equity", diff["dropped_flags"])
        self.assertIn("MSFT US Equity", diff["watch_off"])
        self.assertIn("JPM US Equity", diff["new_outliers"])
        breaks = {b["t"] for b in diff["streak_breaks"]}
        self.assertIn("AAPL", breaks)
        jumps = {j["t"] for j in diff["score_jumps"]}
        self.assertIn("AAPL", jumps)
        labels = " ".join(c["label"] for c in diff["chips"] + diff["extra"])
        self.assertIn("FLAGS", labels)
        self.assertIn("NVDA", labels)
        self.assertIn("WATCH", labels)
        self.assertIn("OUT", labels)
        stk = next(c["label"] for c in diff["chips"] if c["label"].startswith("stk AAPL"))
        self.assertEqual(stk, "stk AAPL ↑ 20d>5 → ↓ 1d<5")
        self.assertNotIn("→↓", stk)
        self.assertNotIn("↑20", stk)

    def test_roundtrip_snapshot_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snap = bd.snapshot_from_cards(
                [_card("DT US Equity", score=8, side="above", streak=5, list_name="FLAGS")]
            )
            path = bd.write_snapshot(snap, root=root)
            self.assertTrue(path.is_file())
            loaded = bd.load_snapshot(root=root)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded["flags"], ["DT US Equity"])
            self.assertEqual(loaded["names"]["DT US Equity"]["score"], 8)

    def test_ensure_embedded_host(self) -> None:
        diff = bd.diff_snapshots(None, {"names": {}, "flags": [], "watch": [], "outliers": []})
        html = bd.ensure_embedded("<html><body><nav></nav></body></html>", diff)
        self.assertIn('id="fd-book-delta"', html)
        self.assertIn("baseline set", html)
        self.assertIn("fd-book-delta-db", html)
        self.assertIn(f'id="{bd.CSS_STYLE_ID}"', html)
        self.assertIn("gap: 6px", html)
        self.assertIn("fd-book-delta-kicker", html)

    def test_kicker_own_row_readable_not_crowded(self) -> None:
        diff = {
            "baseline": False,
            "chips": [
                {"label": "OUT +DKS +GTLB", "cls": "delta-out", "title": "New outliers"},
                {"label": "CRWD +2", "cls": "delta-jump", "title": "CRWD"},
            ],
            "extra": [{"label": "extra", "cls": "delta-jump", "title": "x"}],
        }
        html = bd.ensure_embedded("<html><head></head><body><nav></nav></body></html>", diff)
        self.assertIn("since last Refresh", html)
        self.assertIn('class="fd-book-delta-kicker">since last Refresh</span>', html)
        kicker_at = html.index("fd-book-delta-kicker")
        pills_at = html.index("OUT +DKS")
        self.assertLess(kicker_at, pills_at)
        css = bd.strip_css()
        block = css.split(".fd-book-delta-kicker", 1)[1].split(".fd-dchip", 1)[0]
        self.assertIn("flex: 0 0 100%", block)
        self.assertIn("#9ca3af", block)
        self.assertNotIn("uppercase", block)
        self.assertNotIn("#6b7280", block)
        js = bd.strip_js()
        self.assertIn('kicker.textContent = data.baseline ? "book" : "since last Refresh"', js)
        again = bd.ensure_embedded(html.replace("since last Refresh", "gone", 1), diff)
        self.assertIn("since last Refresh", again)
        self.assertEqual(again.count('id="fd-book-delta"'), 1)

    def test_css_style_id_replaces_on_resync(self) -> None:
        diff = bd.diff_snapshots(None, {"names": {}, "flags": [], "watch": [], "outliers": []})
        html = bd.ensure_embedded("<html><head></head><body></body></html>", diff)
        stale = html.replace("gap: 6px", "gap: 99px", 1)
        self.assertIn("gap: 99px", stale)
        again = bd.ensure_embedded(stale, diff)
        self.assertEqual(again.count(f'id="{bd.CSS_STYLE_ID}"'), 1)
        self.assertIn("gap: 6px", again)
        self.assertNotIn("gap: 99px", again)

    def test_streak_fragment_spacing(self) -> None:
        self.assertEqual(bd.format_streak_fragment("↓1d<5"), "↓ 1d<5")
        self.assertEqual(bd.format_streak_fragment("↑57d>5"), "↑ 57d>5")
        self.assertEqual(bd.format_streak_fragment("↓ 1d<5"), "↓ 1d<5")
        self.assertEqual(bd.format_streak_fragment("=5"), "=5")
        self.assertEqual(
            bd.format_streak_chip_label("MANH", "↓1d<5", "↑57d>5"),
            "stk MANH ↓ 1d<5 → ↑ 57d>5",
        )


if __name__ == "__main__":
    unittest.main()
