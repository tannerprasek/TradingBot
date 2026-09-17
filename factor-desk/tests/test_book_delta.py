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


if __name__ == "__main__":
    unittest.main()
