"""Paper-trade P&L sign, click rules, and write_combined embed. No Bloomberg."""

from __future__ import annotations

import json
import re
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
import mom_streak as ms  # noqa: E402
import paper_trade as pt  # noqa: E402


def _weekdays(start: date, n: int) -> list[date]:
    days: list[date] = []
    d = start
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


class PnlSignTests(unittest.TestCase):
    def test_long_up_is_positive(self) -> None:
        self.assertAlmostEqual(pt.pnl_pct("long", 100.0, 110.0), 0.10)
        self.assertEqual(pt.format_pnl_pct(0.10), "+10.00%")

    def test_long_down_is_negative(self) -> None:
        self.assertAlmostEqual(pt.pnl_pct("long", 100.0, 90.0), -0.10)
        self.assertEqual(pt.format_pnl_pct(-0.10), "-10.00%")

    def test_short_down_is_positive(self) -> None:
        self.assertAlmostEqual(pt.pnl_pct("short", 100.0, 90.0), 0.10)
        self.assertEqual(pt.format_pnl_pct(pt.pnl_pct("short", 50.0, 45.0)), "+10.00%")

    def test_short_up_is_negative(self) -> None:
        self.assertAlmostEqual(pt.pnl_pct("short", 100.0, 110.0), -0.10)

    def test_js_formula_matches_python_sign(self) -> None:
        js = pt.strip_js()
        self.assertIn("long up = +", js)
        self.assertIn("short down = +", js)
        self.assertIn("if (side === \"long\") return (mark - entry) / entry;", js)
        self.assertIn("if (side === \"short\") return (entry - mark) / entry;", js)


class ClickRuleTests(unittest.TestCase):
    def test_flat_buy_opens_long(self) -> None:
        res = pt.apply_click(None, "AAPL US Equity", "buy", 150.0, now="2026-09-17T12:00:00Z")
        self.assertTrue(res["ok"])
        self.assertEqual(res["action"], "open_long")
        opened = res["name"]["open"]
        self.assertEqual(opened["side"], "long")
        self.assertEqual(opened["entry"], 150.0)
        self.assertEqual(opened["qty"], 1)

    def test_flat_sell_opens_short(self) -> None:
        res = pt.apply_click(None, "MSFT", "sell", 400.0)
        self.assertEqual(res["action"], "open_short")
        self.assertEqual(res["name"]["open"]["side"], "short")

    def test_long_sell_closes_does_not_flip(self) -> None:
        book = pt.empty_book()
        book = pt.apply_click(book, "DE", "buy", 100.0, now="2026-09-10T00:00:00Z")["book"]
        res = pt.apply_click(book, "DE", "sell", 110.0, now="2026-09-17T00:00:00Z")
        self.assertEqual(res["action"], "close_long")
        self.assertIsNone(res["name"]["open"])
        closed = res["name"]["closed"][0]
        self.assertEqual(closed["side"], "long")
        self.assertAlmostEqual(closed["pnl_pct"], 0.10)
        self.assertEqual(closed["opened_at"][:10], "2026-09-10")
        self.assertEqual(closed["closed_at"][:10], "2026-09-17")
        self.assertIsNone(res["name"]["open"], "must not open short in the same click")

    def test_short_buy_closes_profit_when_price_falls(self) -> None:
        book = pt.apply_click(None, "JPM", "sell", 200.0, now="2026-09-01T00:00:00Z")["book"]
        res = pt.apply_click(book, "JPM", "buy", 180.0, now="2026-09-17T00:00:00Z")
        self.assertEqual(res["action"], "close_short")
        self.assertIsNone(res["name"]["open"])
        self.assertAlmostEqual(res["closed"]["pnl_pct"], 0.10)
        self.assertIn("+10.00%", res["toast"])

    def test_same_side_buy_while_long_is_noop_toast(self) -> None:
        book = pt.apply_click(None, "NVDA", "buy", 100.0)["book"]
        res = pt.apply_click(book, "NVDA", "buy", 120.0)
        self.assertEqual(res["action"], "noop")
        self.assertEqual(res["reason"], "same_side")
        self.assertEqual(res["name"]["open"]["entry"], 100.0)
        self.assertIn("already LONG", res["toast"])

    def test_same_side_sell_while_short_is_noop_toast(self) -> None:
        book = pt.apply_click(None, "X", "sell", 10.0)["book"]
        res = pt.apply_click(book, "X", "sell", 8.0)
        self.assertEqual(res["action"], "noop")
        self.assertEqual(res["name"]["open"]["side"], "short")
        self.assertIn("already SHORT", res["toast"])

    def test_missing_mark_blocks(self) -> None:
        res = pt.apply_click(None, "AAPL", "buy", None)
        self.assertFalse(res["ok"])
        self.assertEqual(res["reason"], "no_mark")
        self.assertIn("px_last", res["toast"])


class MarkTests(unittest.TestCase):
    def test_prefers_px_last(self) -> None:
        px, src = pt.resolve_mark({"px_last": 12.5, "score": 9})
        self.assertEqual(px, 12.5)
        self.assertIn("px_last", src or "")

    def test_px_last_bloomberg_name(self) -> None:
        px, src = pt.resolve_mark({"PX_LAST": "99.5"})
        self.assertAlmostEqual(px or 0, 99.5)
        self.assertIn("PX_LAST", src or "")

    def test_series_last_print(self) -> None:
        px, src = pt.resolve_mark({"px_series": [{"date": "2026-09-16", "px": 10}, {"date": "2026-09-17", "close": 11}]})
        self.assertEqual(px, 11)
        self.assertIn("series", src or "")

    def test_missing_mark_is_none(self) -> None:
        px, src = pt.resolve_mark({"t": "AAPL", "score": 8})
        self.assertIsNone(px)
        self.assertIsNone(src)

    def test_attach_mark_copies_from_rec(self) -> None:
        card: dict = {"t": "AAPL", "score": 7}
        pt.attach_mark(card, {"PX_LAST": 41.0})
        self.assertEqual(card["px_last"], 41.0)
        self.assertEqual(card["fd_paper_mark"], 41.0)


class BookIoTests(unittest.TestCase):
    def test_roundtrip_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            book = pt.apply_click(None, "AAPL", "buy", 10.0, now="2026-09-17T00:00:00Z")["book"]
            path = pt.write_book(book, root=root)
            self.assertEqual(path.name, "paper_trades.json")
            loaded = pt.load_book(root=root)
            self.assertTrue(loaded["paper"])
            self.assertEqual(loaded["names"]["AAPL"]["open"]["side"], "long")

    def test_missing_file_is_empty_book(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            loaded = pt.load_book(root=Path(tmp))
            self.assertEqual(loaded["names"], {})
            self.assertTrue(loaded["paper"])


class EmbedTests(unittest.TestCase):
    def test_ensure_embedded_injects_buttons_and_cardhtml_wrap(self) -> None:
        html = "<html><head></head><body><article class='card' data-t='AAPL'>AAPL</article></body></html>"
        out = pt.ensure_embedded(html, pt.empty_book())
        self.assertIn('id="fd-paper-db"', out)
        self.assertIn('id="fd-paper-js"', out)
        self.assertIn(">Buy</button>", out)
        self.assertIn(">Sell</button>", out)
        self.assertIn("previous closed trades", out)
        self.assertIn("cardHTML", out)
        self.assertIn("localStorage", out)
        self.assertIn("fd-paper-trade-v1", out)
        self.assertIn("already LONG", out)
        js = pt.strip_js()
        self.assertIn("window.cardHTML", js)
        self.assertIn("__fdPaper", js)

    def test_write_combined_patches_live_html_with_paper_chrome(self) -> None:
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
<article class="card" data-t="CLIMB" data-ticker="CLIMB US Equity">CLIMB</article>
<script>
function cardHTML(c){ return '<article class="card" data-t="'+(c.t||'')+'">'+ (c.t||'') +'</article>'; }
window.MOM = { cards: [{ t: "CLIMB", ticker: "CLIMB US Equity", px_last: 42.5, score: 9 }] };
</script>
</body></html>"""
        pad = 2_700_000 - len(body.encode("utf-8"))
        live = body + ("<!--" + ("P" * max(pad, 1)) + "-->")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dest = root / "factorbook.html"
            dest.write_text(live, encoding="utf-8")
            rec = de.build_name_record("CLIMB US Equity", {"GICS_SECTOR_NAME": "Financials", "PX_LAST": 42.5})
            rec["mom_score"] = 9
            rec["px_last"] = 42.5
            book = {"asof": "2026-09-17", "names": {"CLIMB US Equity": rec}, "meta": {}}
            days = _weekdays(date(2026, 8, 3), 12)
            hist = {
                "asof": days[-1].isoformat(),
                "names": {
                    "CLIMB US Equity": {
                        "series": [{"date": d.isoformat(), "score": 6 + min(i, 3)} for i, d in enumerate(days)]
                    }
                },
            }
            ms.write_hist(hist, root=root)
            seed = pt.apply_click(None, "CLIMB", "sell", 50.0, now="2026-09-01T00:00:00Z")["book"]
            pt.write_book(seed, root=root)
            out = desk_dash.write_combined(dest, root=root, book=book)
            text = out.read_text(encoding="utf-8")
            self.assertGreater(out.stat().st_size, 2_000_000)
            self.assertIn('id="fd-paper-db"', text)
            self.assertIn('id="fd-paper-js"', text)
            self.assertIn(">Buy</button>", text)
            self.assertIn(">Sell</button>", text)
            self.assertIn("cardHTML", text)
            self.assertIn("localStorage", text)
            db = re.search(
                r'<script[^>]*id=["\']fd-paper-db["\'][^>]*>(.*?)</script>',
                text,
                re.I | re.S,
            )
            self.assertIsNotNone(db)
            payload = json.loads(db.group(1))
            self.assertTrue(payload.get("paper"))
            self.assertEqual(payload["names"]["CLIMB"]["open"]["side"], "short")
            self.assertIn("Momentum Up", text)
            self.assertIn("Momentum Down", text)

    def test_article_html_has_host_and_mark(self) -> None:
        card = {"t": "AAPL", "ticker": "AAPL US Equity", "px_last": 12.25, "mom_score": 8}
        pt.attach_mark(card)
        html = desk_dash._article_html(card)
        self.assertIn('data-t="AAPL"', html)
        self.assertIn('data-ticker="AAPL US Equity"', html)
        self.assertIn('data-px-last="12.25"', html)
        self.assertIn('data-fd-paper="1"', html)


if __name__ == "__main__":
    unittest.main()
