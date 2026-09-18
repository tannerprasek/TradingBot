"""Portable MOM card renderer — why chips, normalize, embed wrap."""

from __future__ import annotations

import re
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import card_render as cr  # noqa: E402
import desk_dash  # noqa: E402


class WhyPillsTests(unittest.TestCase):
    def test_splits_band_dump_into_short_chips(self) -> None:
        row = {
            "score": 10,
            "delta": 3,
            "lookback": 7,
            "streak": 3,
            "side": "above",
            "why": "band 10 · +3 / 7d · above 3d",
        }
        pills = cr.why_pills(row)
        labels = [p["label"] for p in pills]
        self.assertEqual(labels, ["band 10", "+3/7d"])
        self.assertFalse(any(" · " in lab for lab in labels))
        self.assertEqual(pills[0]["key"], "fd-bb-band")
        self.assertEqual(pills[1]["cls"], "fd-bb-delta-up")

    def test_negative_delta_chip(self) -> None:
        pills = cr.why_pills({"score": 4, "delta": -2.5, "lookback": 7, "why": "band 4 · -2.5 / 7d · below 4d"})
        labels = [p["label"] for p in pills]
        self.assertEqual(labels, ["band 4", "-2.5/7d"])
        self.assertEqual(pills[1]["cls"], "fd-bb-delta-down")


class NormalizeTests(unittest.TestCase):
    def test_keeps_mom_fields_and_drops_band_why(self) -> None:
        mom = {
            "t": "SPCX",
            "d": "SPCX",
            "score": 10,
            "tags": ["MA FAN", "CLOSE HI", "52W HI"],
            "r20": 1.2,
            "rs63": 0.4,
            "atrs": 2.1,
            "enrich_pills": [{"key": "si", "label": "SI", "cls": "si-crowded"}],
            "why": "band 10 · +3 / 7d · above 3d",
        }
        row = {
            "t": "SPCX",
            "ticker": "SPCX US Equity",
            "score": 10,
            "delta": 3,
            "lookback": 7,
            "label": "↑3d>5",
            "side": "above",
            "why": mom["why"],
        }
        out = cr.normalize_card(mom, row)
        self.assertNotIn("why", out)
        self.assertEqual(out["tags"], ["MA FAN", "CLOSE HI", "52W HI"])
        self.assertEqual(out["r20"], 1.2)
        keys = [p["key"] for p in out["enrich_pills"]]
        labels = [p["label"] for p in out["enrich_pills"]]
        self.assertIn("si", keys)
        self.assertIn("fd-bb-band", keys)
        self.assertIn("fd-bb-delta", keys)
        self.assertIn("mom-streak", keys)
        self.assertNotIn("fd-bb", keys)
        self.assertIn("band 10", labels)
        self.assertIn("+3/7d", labels)
        self.assertIn("↑3d>5", labels)
        self.assertFalse(any(" · " in lab for lab in labels))
        self.assertIsNot(out, mom)
        self.assertEqual(mom["why"], row["why"])

    def test_merge_drops_dump_pill(self) -> None:
        card = {
            "enrich_pills": [
                {"key": "fd-bb", "label": "band 10 · +3 / 7d · above 3d", "cls": "fd-bb-why"},
                {"key": "si", "label": "SI", "cls": "si-crowded"},
            ]
        }
        merged = cr.merge_pills(card, cr.why_pills({"score": 10, "delta": 3, "lookback": 7}))
        keys = [p["key"] for p in merged]
        self.assertNotIn("fd-bb", keys)
        self.assertIn("si", keys)
        self.assertIn("fd-bb-band", keys)


class EmbedTests(unittest.TestCase):
    def test_ensure_embedded_wraps_cardhtml_and_exposes_render(self) -> None:
        html = """<!DOCTYPE html><html><head></head><body>
<script>function cardHTML(c){return '<article class="card"><div class="why">'+(c.why||'')+'</div><div class="tags"><span class="tg on">MA FAN</span><span class="tg off">GAP</span></div></article>';}</script>
</body></html>"""
        out = cr.ensure_embedded(html)
        self.assertIn('id="fd-card-css"', out)
        self.assertIn('id="fd-card-js"', out)
        self.assertIn("window.__FD_RENDER_CARD__", out)
        self.assertIn("window.__FD_RENDER_ROW__", out)
        self.assertIn("window.__FD_NORMALIZE_CARD__", out)
        self.assertIn("polishNode", out)
        self.assertIn("fd-tag-chip", out)
        self.assertIn("fd-bb-band", out)
        self.assertIn("labels[t]", out)
        self.assertIn("article.card .badge", out)
        again = cr.ensure_embedded(out)
        self.assertEqual(len(re.findall(r'id="fd-card-js"', again)), 1)
        self.assertEqual(len(re.findall(r'id="fd-card-css"', again)), 1)

    def test_write_combined_injects_renderer_before_breakout(self) -> None:
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
<script>function cardHTML(c){return '<article class="card" data-t="'+(c.t||'')+'">'+ (c.t||'') +'</article>';}</script>
</body></html>"""
        pad = 2_700_000 - len(body.encode("utf-8"))
        live = body + ("<!--" + ("P" * max(pad, 1)) + "-->")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dest = root / "factorbook.html"
            dest.write_text(live, encoding="utf-8")
            out = desk_dash.write_combined(dest, root=root, book={"asof": "2026-09-17", "names": {}, "meta": {}})
            text = out.read_text(encoding="utf-8")
            self.assertIn('id="fd-card-js"', text)
            self.assertIn("__FD_RENDER_ROW__", text)
            card_at = text.find('id="fd-card-js"')
            bb_at = text.find('id="fd-breakout-js"')
            paper_at = text.find('id="fd-paper-js"')
            self.assertGreater(card_at, 0)
            self.assertGreater(bb_at, card_at)
            if paper_at > 0:
                self.assertGreater(paper_at, card_at)
            ast_ok = compile(Path(desk_dash.__file__).read_text(encoding="utf-8"), desk_dash.__file__, "exec")
            self.assertIsNotNone(ast_ok)


if __name__ == "__main__":
    unittest.main()
