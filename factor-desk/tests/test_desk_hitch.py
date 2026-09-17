"""Desk Analyst hitch ticker parse + pill attach. No Bloomberg."""

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

import desk_hitch as dh  # noqa: E402


ASOF = date(2026, 9, 17)


class ParseTests(unittest.TestCase):
    def test_frontmatter_ticker_and_analyst(self) -> None:
        text = """---
ticker: AAPL
analyst: A
---
# AAPL cycle

Services mix is the thesis line.

Action: ignore this, frontmatter wins
"""
        self.assertEqual(dh.parse_tickers(text), ["AAPL"])
        self.assertEqual(dh.parse_analyst(text), "A")
        self.assertIn("Services mix", dh.first_thesis_line(text))

    def test_action_line(self) -> None:
        text = """# Note

**Action:** MSFT US Equity

Cloud run-rate still compounding.
"""
        self.assertEqual(dh.parse_tickers(text), ["MSFT"])

    def test_dollar_ticker(self) -> None:
        text = "Watching $NVDA into earnings.\n"
        self.assertEqual(dh.parse_tickers(text), ["NVDA"])

    def test_single_bold_ticker(self) -> None:
        text = "Still like **DE** on the dip.\n"
        self.assertEqual(dh.parse_tickers(text), ["DE"])

    def test_no_false_hits_on_prose(self) -> None:
        text = """# Random notes

The **NOTE** and **BUY** and **THIS** are not names.
Actionable prose without a ticker token.
"""
        self.assertEqual(dh.parse_tickers(text), [])

    def test_filename_analyst_letter(self) -> None:
        path = Path("2026-09-15-B-msft.md")
        self.assertEqual(dh.parse_analyst("no frontmatter", path), "B")


class ScanTests(unittest.TestCase):
    def test_missing_ideas_dir_is_noop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(dh.load_index(root=root, asof=ASOF), {})
            card = {"t": "AAPL", "score": 8}
            dh.attach_card(card, {})
            self.assertIsNone(card.get("desk_hitch"))
            self.assertFalse(any(p.get("key") == "desk-hitch" for p in card.get("enrich_pills") or []))

    def test_scan_last_10d_attaches_da_pill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ideas = Path(tmp) / "ideas"
            ideas.mkdir()
            (ideas / "2026-09-15-A-aapl.md").write_text(
                "---\nticker: AAPL\nanalyst: A\n---\n\n# Title\n\n"
                "iPhone cycle still under-owned.\n",
                encoding="utf-8",
            )
            (ideas / "2026-09-16-B-msft.md").write_text(
                "**Action:** MSFT\n\nAzure vs AWS gap.\n",
                encoding="utf-8",
            )
            (ideas / "2026-08-01-old.md").write_text(
                "---\nticker: OLD\n---\n\nstale\n",
                encoding="utf-8",
            )
            (ideas / "readme.md").write_text("not dated\n", encoding="utf-8")
            index = dh.load_index(ideas=ideas, asof=ASOF, lookback=10)
            self.assertIn("AAPL", index)
            self.assertIn("MSFT", index)
            self.assertNotIn("OLD", index)
            self.assertEqual(index["AAPL"]["analyst"], "A")
            card = {"t": "AAPL US Equity", "score": 9, "enrich_pills": []}
            dh.attach_card(card, index)
            labels = [p["label"] for p in card["enrich_pills"] if p.get("key") == "desk-hitch"]
            self.assertEqual(labels, ["DA·A"])
            self.assertIn("2026-09-15", card["enrich_pills"][-1]["title"])
            self.assertIn("iPhone cycle", card["enrich_pills"][-1]["title"])
            msft = {"t": "MSFT", "score": 8}
            dh.attach_card(msft, index)
            self.assertEqual(msft["desk_hitch_label"], "DA·B")


if __name__ == "__main__":
    unittest.main()
