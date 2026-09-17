"""Paper book P&L sign, click rules, and write_combined survival. No Bloomberg."""

from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import dapi_enrich as de  # noqa: E402
import desk_dash  # noqa: E402
import paper_trade as pt  # noqa: E402


class PnlSignTests(unittest.TestCase):
    def test_long_up_is_plus(self) -> None:
        self.assertAlmostEqual(pt.pnl_pct("long", 100.0, 110.0), 10.0)

    def test_short_down_is_plus(self) -> None:
        self.assertAlmostEqual(pt.pnl_pct("short", 100.0, 90.0), 10.0)

    def test_long_down_is_minus(self) -> None:
        self.assertAlmostEqual(pt.pnl_pct("long", 50.0, 45.0), -10.0)

    def test_short_up_is_minus(self) -> None:
        self.assertAlmostEqual(pt.pnl_pct("short", 50.0, 55.0), -10.0)

    def test_closed_short_keeps_sign(self) -> None:
        row = pt.close_position(
            {"ticker": "AAPL", "side": "short", "entry": 20.0, "opened_at": "2026-09-01"},
            18.0,
            "2026-09-17T00:00:00+00:00",
        )
        self.assertAlmostEqual(row["ret_pct"], 10.0)
        self.assertEqual(row["side"], "short")
        self.assertIn("+10.00%", pt.fmt_pct(row["ret_pct"]))


class ClickRuleTests(unittest.TestCase):
    def test_flat_buy_opens_long(self) -> None:
        book = pt.empty_book()
        res = pt.apply_click(book, "AAPL US Equity", "buy", 12.5, when="2026-09-17T00:00:00+00:00")
        self.assertEqual(res["action"], "open_long")
        self.assertEqual(book["positions"]["AAPL"]["side"], "long")
        self.assertEqual(book["positions"]["AAPL"]["entry"], 12.5)
        self.assertEqual(book["positions"]["AAPL"]["qty"], 1)

    def test_flat_sell_opens_short(self) -> None:
        book = pt.empty_book()
        res = pt.apply_click(book, "MSFT", "sell", 400.0)
        self.assertEqual(res["action"], "open_short")
        self.assertEqual(book["positions"]["MSFT"]["side"], "short")

    def test_long_sell_closes_does_not_flip(self) -> None:
        book = pt.empty_book()
        pt.apply_click(book, "AAPL", "buy", 100.0, when="2026-09-10T00:00:00+00:00")
        res = pt.apply_click(book, "AAPL", "sell", 110.0, when="2026-09-17T00:00:00+00:00")
        self.assertEqual(res["action"], "close_long")
        self.assertNotIn("AAPL", book["positions"])
        closed = book["closed"]["AAPL"][0]
        self.assertAlmostEqual(closed["ret_pct"], 10.0)
        self.assertEqual(closed["side"], "long")
        self.assertEqual(closed["closed_at"][:10], "2026-09-17")

    def test_short_buy_closes_does_not_flip(self) -> None:
        book = pt.empty_book()
        pt.apply_click(book, "AAPL", "sell", 100.0)
        res = pt.apply_click(book, "AAPL", "buy", 90.0)
        self.assertEqual(res["action"], "close_short")
        self.assertNotIn("AAPL", book["positions"])
        self.assertAlmostEqual(book["closed"]["AAPL"][0]["ret_pct"], 10.0)

    def test_same_side_is_noop_toast(self) -> None:
        book = pt.empty_book()
        pt.apply_click(book, "AAPL", "buy", 10.0)
        res = pt.apply_click(book, "AAPL", "buy", 11.0)
        self.assertEqual(res["action"], "noop")
        self.assertEqual(res["toast"], pt.TOAST_ALREADY_LONG)
        self.assertEqual(book["positions"]["AAPL"]["entry"], 10.0)
        book2 = pt.empty_book()
        pt.apply_click(book2, "AAPL", "sell", 10.0)
        res2 = pt.apply_click(book2, "AAPL", "sell", 9.0)
        self.assertEqual(res2["action"], "noop")
        self.assertEqual(res2["toast"], pt.TOAST_ALREADY_SHORT)

    def test_missing_mark_blocks(self) -> None:
        book = pt.empty_book()
        res = pt.apply_click(book, "AAPL", "buy", None)
        self.assertFalse(res["ok"])
        self.assertEqual(res["reason"], "no_mark")
        self.assertIn("PX_LAST", res["toast"])
        self.assertEqual(book["positions"], {})


class MarkTests(unittest.TestCase):
    def test_px_last_and_series(self) -> None:
        self.assertEqual(pt.mark_of({"PX_LAST": 12.0}), 12.0)
        self.assertEqual(pt.mark_of({"price": 8.5}), 8.5)
        self.assertEqual(pt.mark_of({"px_series": [1, 2, 3.5]}), 3.5)
        self.assertEqual(pt.mark_of({"prices": [{"px_last": 9.0}]}), 9.0)
        self.assertIsNone(pt.mark_of({"t": "AAPL"}))
        self.assertIsNone(pt.mark_of({"px_last": 0}))

    def test_marks_db_shorts(self) -> None:
        db = pt.marks_db(
            [{"t": "AAPL", "ticker": "AAPL US Equity", "px_last": 12.0}],
            book={"names": {"MSFT US Equity": {"px_last": 400.0}}},
        )
        self.assertEqual(db["AAPL"], 12.0)
        self.assertEqual(db["MSFT"], 400.0)


class EmbedTests(unittest.TestCase):
    def test_ensure_embedded_injects_buttons_and_storage(self) -> None:
        html = """<!DOCTYPE html><html><head></head><body>
<nav><button>Momentum Up</button><button>Momentum Down</button></nav>
<article class="card" data-t="AAPL">AAPL</article>
<script>
function cardHTML(c) { return '<article class="card" data-t="'+c.t+'">'+c.t+'</article>'; }
window.MOM = { cards: [{ t: "AAPL", px_last: 12 }] };
</script>
</body></html>"""
        out = pt.ensure_embedded(html, {"AAPL": 12.0})
        self.assertIn('id="fd-paper-js"', out)
        self.assertIn('id="fd-paper-css"', out)
        self.assertIn('id="fd-paper-marks"', out)
        self.assertIn("fd-paper-book", out)
        self.assertIn(">Buy</button>", out)
        self.assertIn(">Sell</button>", pt.chrome_html("AAPL", mark=12.0))
        self.assertIn("cardHTML", out)
        self.assertIn("localStorage", out)
        self.assertIn("Already long — sell to close", out)
        self.assertIn("Previous trades", out)
        js = pt.strip_js()
        self.assertIn("window.cardHTML", js)
        self.assertIn("mom.cards", js)
        self.assertIn("window.MOM", js)
        self.assertIn("No mark price", js)
        self.assertIn("var painting = false", js)
        self.assertIn("if (painting) return", js)
        self.assertIn("__FD_PAPER_CLICK__", js)
        self.assertIn("onPaperClick", js)
        chrome = pt.chrome_html("AAPL", mark=None)
        self.assertIn("disabled", chrome)
        self.assertIn("No mark price", chrome)
        again = pt.ensure_embedded(out, None)
        blob = re.search(
            r'<script\b[^>]*id=["\']fd-paper-marks["\'][^>]*>(.*?)</script>',
            again,
            re.I | re.S,
        )
        self.assertIsNotNone(blob)
        self.assertEqual(json.loads(blob.group(1))["AAPL"], 12.0)

    def test_sidecar_schema_qty_one(self) -> None:
        schema = pt.sidecar_schema()
        self.assertEqual(schema["properties"]["kind"]["const"], "factor-desk-paper")
        pos = schema["properties"]["positions"]["additionalProperties"]["properties"]["qty"]
        self.assertEqual(pos["const"], 1.0)

    def test_write_combined_patches_live_html(self) -> None:
        body = """<!DOCTYPE html>
<html><head><title>Factor Desk</title>
<style>.gchip{} .gics-hid{display:none!important}</style></head>
<body>
<nav>
  <button id="refresh">Refresh</button>
  <button>Momentum Up</button>
  <button>Momentum Down</button>
  <button>Outliers</button>
  <button>Options</button>
</nav>
<div id="gics-filter-strip" class="filter-strip gics-chips"></div>
<article class="card" data-t="AAPL" data-ticker="AAPL US Equity">AAPL</article>
<script>
function cardHTML(c) { return '<article class="card" data-t="'+(c.t||'')+'">'+(c.t||'')+'</article>'; }
window.MOM = { cards: [{ t: "AAPL", ticker: "AAPL US Equity", px_last: 12.5, score: 9 }] };
</script>
</body></html>"""
        pad = 2_700_000 - len(body.encode("utf-8"))
        live = body + ("<!--" + ("P" * max(pad, 1)) + "-->")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dest = root / "factorbook.html"
            dest.write_text(live, encoding="utf-8")
            rec = de.build_name_record("AAPL US Equity", {"GICS_SECTOR_NAME": "Information Technology", "PX_LAST": 12.5})
            rec["mom_score"] = 9
            rec["px_last"] = 12.5
            book = {"asof": "2026-09-17", "names": {"AAPL US Equity": rec}, "meta": {}}
            out = desk_dash.write_combined(dest, root=root, book=book)
            text = out.read_text(encoding="utf-8")
            self.assertGreater(out.stat().st_size, 2_000_000)
            self.assertIn('id="fd-paper-js"', text)
            self.assertIn('id="fd-paper-marks"', text)
            self.assertIn("fd-paper-book", text)
            self.assertIn("data-fd-paper-act", text)
            self.assertIn("Momentum Up", text)
            self.assertIn("Momentum Down", text)
            self.assertIn("AAPL", json.loads(
                re.search(
                    r'<script\b[^>]*id=["\']fd-paper-marks["\'][^>]*>(.*?)</script>',
                    text,
                    re.I | re.S,
                ).group(1)
            ))


if __name__ == "__main__":
    unittest.main()
