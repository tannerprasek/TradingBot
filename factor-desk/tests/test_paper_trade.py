"""Paper book P&L sign, click rules, and write_combined survival. No Bloomberg."""

from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from datetime import datetime, timezone
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


class WeekScorecardTests(unittest.TestCase):
    """Monday 00:00 America/Edmonton window + signed hit/win/loss."""

    NOW = datetime(2026, 9, 18, 15, 0, tzinfo=timezone.utc)  # Friday

    def _closed(self, ticker: str, side: str, entry: float, exit_px: float, closed_at: str) -> dict:
        book = pt.empty_book()
        pos = {"ticker": ticker, "side": side, "entry": entry, "opened_at": "2026-09-01T00:00:00Z"}
        row = pt.close_position(pos, exit_px, closed_at)
        book["closed"][pt._short(ticker)] = [row]
        return book

    def _merge(self, *books: dict) -> dict:
        out = pt.empty_book()
        for book in books:
            for key, rows in book["closed"].items():
                out["closed"].setdefault(key, []).extend(rows)
        return out

    def test_week_start_is_monday_edmonton(self) -> None:
        start = pt.week_start(self.NOW)
        self.assertEqual(start.tzinfo.key if hasattr(start.tzinfo, "key") else pt.WEEK_TZ_NAME, "America/Edmonton")
        self.assertEqual(start.weekday(), 0)
        self.assertEqual(start.hour, 0)
        self.assertEqual(start.minute, 0)
        # 2026-09-14 is MDT (UTC-6) → 06:00 UTC
        self.assertEqual(start.astimezone(timezone.utc).isoformat(), "2026-09-14T06:00:00+00:00")

    def test_monday_edmonton_midnight_is_in_week(self) -> None:
        book = self._closed("AAPL", "long", 100.0, 110.0, "2026-09-14T06:00:00Z")
        rows = pt.closed_this_week(book, now=self.NOW)
        self.assertEqual(len(rows), 1)

    def test_sunday_night_edmonton_is_previous_week(self) -> None:
        # Monday 05:59 UTC = Sunday 23:59 America/Edmonton
        book = self._closed("AAPL", "long", 100.0, 110.0, "2026-09-14T05:59:59Z")
        self.assertEqual(pt.closed_this_week(book, now=self.NOW), [])
        self.assertTrue(pt.week_scorecard(book, now=self.NOW)["empty"])

    def test_hit_rate_and_avg_win_loss_signed_for_shorts(self) -> None:
        book = self._merge(
            self._closed("AAA", "long", 100.0, 110.0, "2026-09-15T12:00:00Z"),   # +10 win
            self._closed("BBB", "short", 100.0, 90.0, "2026-09-16T12:00:00Z"),   # +10 win (price fell)
            self._closed("CCC", "short", 100.0, 110.0, "2026-09-17T12:00:00Z"),  # -10 loss (price rose)
            self._closed("DDD", "long", 100.0, 95.0, "2026-09-18T12:00:00Z"),    # -5 loss
        )
        sc = pt.week_scorecard(book, now=self.NOW)
        self.assertEqual(sc["count"], 4)
        self.assertEqual(sc["hits"], 2)
        self.assertAlmostEqual(sc["hit_rate"], 50.0)
        self.assertAlmostEqual(sc["avg_win"], 10.0)
        self.assertAlmostEqual(sc["avg_loss"], -7.5)
        self.assertIn("4 closed", pt.scorecard_line(sc))
        self.assertIn("50% hit", pt.scorecard_line(sc))
        self.assertIn("avg win +10.00%", pt.scorecard_line(sc))
        self.assertIn("avg loss -7.50%", pt.scorecard_line(sc))

    def test_zero_return_is_not_a_hit(self) -> None:
        book = self._merge(
            self._closed("AAA", "long", 100.0, 110.0, "2026-09-15T12:00:00Z"),
            self._closed("BBB", "long", 100.0, 100.0, "2026-09-16T12:00:00Z"),
        )
        sc = pt.week_scorecard(book, now=self.NOW)
        self.assertEqual(sc["count"], 2)
        self.assertEqual(sc["hits"], 1)
        self.assertAlmostEqual(sc["hit_rate"], 50.0)
        self.assertAlmostEqual(sc["avg_win"], 10.0)
        self.assertIsNone(sc["avg_loss"])
        self.assertIn("avg loss —", pt.scorecard_line(sc))

    def test_empty_week_copy(self) -> None:
        sc = pt.week_scorecard(pt.empty_book(), now=self.NOW)
        self.assertEqual(sc["count"], 0)
        self.assertTrue(sc["empty"])
        self.assertEqual(pt.scorecard_line(sc), "no closed yet this week")
        self.assertEqual(sc["tz"], "America/Edmonton")


class HomeStripTests(unittest.TestCase):
    def test_open_rows_use_marks_and_short_sign(self) -> None:
        book = pt.empty_book()
        pt.apply_click(book, "AAPL", "buy", 100.0, when="2026-09-17T00:00:00Z")
        pt.apply_click(book, "MSFT", "sell", 200.0, when="2026-09-17T01:00:00Z")
        rows = {r["ticker"]: r for r in pt.open_rows(book, {"AAPL": 110.0, "MSFT": 180.0})}
        self.assertAlmostEqual(rows["AAPL"]["pnl_pct"], 10.0)
        self.assertEqual(rows["AAPL"]["side"], "long")
        self.assertAlmostEqual(rows["MSFT"]["pnl_pct"], 10.0)
        self.assertEqual(rows["MSFT"]["side"], "short")
        self.assertIn("MSFT SHORT", rows["MSFT"]["label"])

    def test_empty_opens_copy(self) -> None:
        html = pt.home_host_html(pt.empty_book(), {})
        self.assertIn("no open paper", html)
        self.assertIn("no closed yet this week", html)
        self.assertIn('id="fd-paper-home"', html)
        self.assertIn("America/Edmonton", html)

    def test_compact_after_twelve_opens(self) -> None:
        book = pt.empty_book()
        for i in range(15):
            pt.apply_click(book, f"T{i:02d}", "buy", 10.0 + i, when=f"2026-09-17T00:{i:02d}:00Z")
        opens = pt.open_rows(book, {f"T{i:02d}": 11.0 for i in range(15)})
        self.assertEqual(len(opens), 15)
        html = pt.home_host_html(book, {f"T{i:02d}": 11.0 for i in range(15)})
        self.assertIn("more 3", html)
        self.assertEqual(html.count("data-fd-paper-ticker="), 15)
        self.assertLessEqual(pt.OPEN_SHOW_MAX, 12)
        self.assertEqual(pt.OPEN_SHOW_MAX, 12)

    def test_ensure_embedded_injects_home_strip_near_book(self) -> None:
        html = """<!DOCTYPE html><html><head></head><body>
<nav><button>Momentum Up</button></nav>
<div id="fd-book-delta" class="fd-book-delta"><span class="fd-book-delta-kicker">book</span></div>
<article class="card" data-t="AAPL">AAPL</article>
<script>function cardHTML(c){return '<article class="card" data-t="'+c.t+'">'+c.t+'</article>';}</script>
</body></html>"""
        out = pt.ensure_embedded(html, {"AAPL": 12.0})
        delta_at = out.find('id="fd-book-delta"')
        home_at = out.find('id="fd-paper-home"')
        self.assertGreater(delta_at, 0)
        self.assertGreater(home_at, delta_at)
        self.assertIn("no open paper", out)
        self.assertIn("no closed yet this week", out)
        self.assertIn("America/Edmonton", out)
        js = pt.strip_js()
        self.assertIn("paintHome", js)
        self.assertIn("selectTicker", js)
        self.assertIn("OPEN_LIMIT = 12", js)
        self.assertIn("weekStartMs", js)
        self.assertIn("Already long — sell to close", js)
        self.assertIn("Previous trades", out)
        self.assertIn(">Buy</button>", pt.chrome_html("AAPL", mark=12.0))
        again = pt.ensure_embedded(out, None)
        self.assertEqual(again.count('id="fd-paper-home"'), 1)
        self.assertEqual(again.count('id="fd-paper-js"'), 1)


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
            self.assertIn('id="fd-paper-home"', text)
            self.assertIn("no open paper", text)
            self.assertIn("no closed yet this week", text)
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
