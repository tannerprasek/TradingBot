"""Paper book P&L sign, click rules, and write_combined survival. No Bloomberg."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
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
        self.assertEqual(pt.fmt_px(13.63), "13.63")
        self.assertEqual(pt.fmt_px(None), "—")

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


class PaperTabTests(unittest.TestCase):
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
        html = pt.panes_html(pt.empty_book(), {})
        self.assertIn("no open paper", html)
        self.assertIn("no closed yet this week", html)
        self.assertIn("no closed paper", html)
        self.assertIn('id="view-paper"', html)
        self.assertIn('data-view="paper"', html)
        self.assertIn("America/Edmonton", html)
        self.assertIn("data-fd-paper-open", html)
        self.assertIn("Closed trades", html)
        self.assertNotIn("fd-paper-chip", html)
        self.assertEqual(pt.home_host_html(pt.empty_book(), {}), "")

    def test_open_rows_list_every_position(self) -> None:
        book = pt.empty_book()
        for i in range(15):
            pt.apply_click(book, f"T{i:02d}", "buy", 10.0 + i, when=f"2026-09-17T00:{i:02d}:00Z")
        opens = pt.open_rows(book, {f"T{i:02d}": 11.0 for i in range(15)})
        self.assertEqual(len(opens), 15)
        html = pt.panes_html(book, {f"T{i:02d}": 11.0 for i in range(15)})
        self.assertNotIn("more 3", html)
        self.assertIn("fd-paper-table", html)
        self.assertIn(">Ticker</th>", html)
        self.assertIn(">Side</th>", html)
        self.assertIn(">Entry</th>", html)
        self.assertIn(">P&amp;L %</th>", html)
        self.assertNotIn("fd-paper-chip", html)
        self.assertEqual(html.count("data-fd-paper-ticker="), 45)  # row + ticker + Close per open
        self.assertEqual(html.count("data-fd-paper-close="), 15)
        self.assertIn(">Close</button>", html)

    def test_open_table_columns_and_pnl_color(self) -> None:
        book = pt.empty_book()
        pt.apply_click(book, "CNH", "buy", 13.63, when="2026-09-17T00:00:00Z")
        pt.apply_click(book, "PWR", "sell", 617.64, when="2026-09-17T01:00:00Z")
        html = pt.panes_html(book, {"CNH": 13.545, "PWR": 598.74})
        self.assertIn('class="fd-paper-table"', html)
        self.assertIn('aria-label="Open paper"', html)
        self.assertIn(">CNH</button>", html)
        self.assertIn("LONG", html)
        self.assertIn("SHORT", html)
        self.assertIn("13.63", html)
        self.assertIn("617.64", html)
        self.assertIn("fd-paper-down", html)
        self.assertIn("fd-paper-up", html)
        self.assertNotIn("CNH LONG @", html)
        self.assertNotIn("fd-paper-chip", html)

    def test_closed_table_is_html_table(self) -> None:
        book = pt.empty_book()
        pt.apply_click(book, "CNH", "buy", 13.63, when="2026-09-17T00:00:00Z")
        pt.apply_click(book, "CNH", "sell", 14.99, when="2026-09-18T00:00:00Z")
        html = pt.panes_html(book, {})
        self.assertIn('aria-label="Closed paper"', html)
        self.assertIn(">Date</th>", html)
        self.assertIn(">Ticker</th>", html)
        self.assertIn(">Side</th>", html)
        self.assertIn(">Entry</th>", html)
        self.assertIn(">Exit</th>", html)
        self.assertIn(">P&amp;L %</th>", html)
        self.assertIn("fd-paper-table", html)
        self.assertIn("CNH", html)
        self.assertIn("14.99", html)
        self.assertNotIn("<ul id=\"fd-paper-closed\">", html)
        self.assertNotIn("-100.00%", html)

    def test_ensure_embedded_injects_paper_tab_not_chrome_strip(self) -> None:
        html = """<!DOCTYPE html><html><head></head><body>
<nav>
  <button data-view="options">Options</button>
  <button data-view="experimental">Experimental</button>
</nav>
<div id="fd-book-delta" class="fd-book-delta"><span class="fd-book-delta-kicker">book</span></div>
<div id="fd-paper-home" class="fd-paper-home"><span class="fd-paper-chip">CNH LONG @ 13.63  100.00%</span></div>
<article class="card" data-t="AAPL">AAPL</article>
<script>
function hideAllPanes() {
  ["home","view-mom-up","view-mom-down","view-outliers","view-options","view-sectors","search-pane"].forEach(function(id){
    var el = document.getElementById(id);
    if (el) el.classList.add("hide");
  });
}
function paintView(v) {
  if (!/^(home|mom-up|mom-down|outliers|options|sectors)$/.test(v)) v = "home";
}
function setView(v) {
  if (!/^(home|mom-up|mom-down|outliers|options|sectors)$/.test(v)) v = "home";
  hideAllPanes();
  paintView(v);
  paint();
}
function cardHTML(c){return '<article class="card" data-t="'+c.t+'">'+c.t+'</article>';}
</script>
</body></html>"""
        out = pt.ensure_embedded(html, {"AAPL": 12.0})
        self.assertNotIn('id="fd-paper-home"', out)
        self.assertIn('id="view-paper"', out)
        self.assertIn('id="fd-nav-paper"', out)
        self.assertRegex(out, r'<button\b[^>]*data-view=["\']paper["\']')
        self.assertIn("no open paper", out)
        self.assertIn("no closed yet this week", out)
        self.assertIn("America/Edmonton", out)
        self.assertIn("data-fd-paper-open", out)
        self.assertIn("data-fd-paper-close", out)
        self.assertIn("|paper", out)
        self.assertIn("fd-paper-table", out)
        self.assertIn(pt.SETVIEW_MARKER, out)
        self.assertIn(pt.HIDEALL_MARKER, out)
        js = pt.strip_js()
        self.assertIn("paintTab", js)
        self.assertIn("__FD_PAPER_SHOW__", js)
        self.assertIn("selectTicker", js)
        self.assertIn("data-fd-paper-close", js)
        self.assertIn("data-fd-paper-open", js)
        self.assertIn("fd-paper-table", js)
        self.assertIn("openTableHead", js)
        self.assertNotIn("function chipEl", js)
        self.assertIn("Already long — sell to close", js)
        self.assertIn("Previous trades", out)
        self.assertIn(">Buy</button>", pt.chrome_html("AAPL", mark=12.0))
        again = pt.ensure_embedded(out, None)
        self.assertEqual(again.count('id="view-paper"'), 1)
        self.assertEqual(again.count('id="fd-paper-js"'), 1)
        self.assertEqual(again.count('id="fd-nav-paper"'), 1)
        self.assertNotIn('id="fd-paper-home"', again)
        nav_at = again.find('data-view="options"')
        paper_at = again.find('id="fd-nav-paper"')
        exp_at = again.find('data-view="experimental"')
        self.assertGreater(paper_at, nav_at)
        self.assertGreater(paper_at, exp_at)

    def test_inline_close_uses_opposite_click(self) -> None:
        book = pt.empty_book()
        pt.apply_click(book, "AMGN", "buy", 78.11)
        res = pt.apply_click(book, "AMGN", "sell", 80.0)
        self.assertEqual(res["action"], "close_long")
        self.assertNotIn("AMGN", book["positions"])
        book2 = pt.empty_book()
        pt.apply_click(book2, "PWR", "sell", 617.64)
        res2 = pt.apply_click(book2, "PWR", "buy", 600.0)
        self.assertEqual(res2["action"], "close_short")


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

    def test_ensure_embedded_strips_stale_chip_script(self) -> None:
        html = """<!DOCTYPE html><html><head></head><body>
<nav><button data-view="options">Options</button></nav>
<script>
(function(){
  window.__FD_PAPER_APPLY__ = function(){};
  function chipEl(row){ var b=document.createElement("button"); b.className="fd-paper-chip"; return b; }
  function paintTab(){ var wrap=document.createElement("div"); wrap.className="fd-paper-open-row"; wrap.appendChild(chipEl({})); }
})();
</script>
</body></html>"""
        out = pt.ensure_embedded(html, {"AAPL": 12.0})
        self.assertIn('id="fd-paper-js"', out)
        self.assertIn("fd-paper-table", out)
        self.assertEqual(out.count("function chipEl"), 0)
        self.assertIn("openTableHead", out)

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
            self.assertIn('id="view-paper"', text)
            self.assertIn('id="fd-nav-paper"', text)
            self.assertNotIn('id="fd-paper-home"', text)
            self.assertIn("no open paper", text)
            self.assertIn("no closed yet this week", text)
            self.assertIn("fd-paper-book", text)
            self.assertIn("data-fd-paper-act", text)
            self.assertIn("data-fd-paper-open", text)
            self.assertIn("data-fd-paper-close", text)
            self.assertIn("fd-paper-table", text)
            self.assertIn("Momentum Up", text)
            self.assertIn("Momentum Down", text)
            self.assertIn("AAPL", json.loads(
                re.search(
                    r'<script\b[^>]*id=["\']fd-paper-marks["\'][^>]*>(.*?)</script>',
                    text,
                    re.I | re.S,
                ).group(1)
            ))


class PaperNavCaptureTests(unittest.TestCase):
    def test_paper_click_stops_immediate_before_show(self) -> None:
        js = pt.strip_js()
        self.assertIn("__FD_PAPER_SHOW__", js)
        click = js.split('if (kind === "paper")')[1].split('if (kind === "other")')[0]
        sip = click.find("stopImmediatePropagation")
        show = click.find("showPaper(true)")
        self.assertGreater(sip, -1)
        self.assertGreater(show, sip)
        src = Path(pt.__file__).read_text(encoding="utf-8")
        self.assertIn('if(window.__FD_PAPER_SHOW__)window.__FD_PAPER_SHOW__();', src)
        self.assertIn("__FD_PAPER_SHOW__", js)
        self.assertNotIn("__FD_BB_IGNORE_PAPER__ = false", js)
        self.assertNotIn("__FD_SS_IGNORE_FOREIGN_NAV__ = false", js)
        bridge = js.split("function installSetViewBridge")[1]
        paper_sv = bridge.split('kind === "paper"')[1].split("showPaper(false)")[0]
        self.assertIn("showPaper(true)", paper_sv)

    def test_jsdom_paper_click_does_not_unhide_home(self) -> None:
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        jsdom_root = Path("/tmp/fd-jsdom")
        jsdom_mod = jsdom_root / "node_modules" / "jsdom"
        if not jsdom_mod.is_dir():
            jsdom_root.mkdir(parents=True, exist_ok=True)
            npm = shutil.which("npm")
            if not npm:
                self.skipTest("npm not installed")
            subprocess.run(
                [npm, "install", "--prefix", str(jsdom_root), "jsdom@24"],
                check=True,
                capture_output=True,
                text=True,
                timeout=60,
            )
        import breakout as bo
        import s_score as ss

        live = """<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body>
<nav id="topnav">
  <button type="button" class="btn nav-btn" data-view="home">Home</button>
  <button type="button" class="btn nav-btn" id="fd-nav-breakout" data-view="breakout" data-fd-breakout="1">Breakout</button>
  <button type="button" class="btn nav-btn" id="fd-nav-experimental" data-view="experimental" data-fd-sscore="1">Experimental</button>
</nav>
<div id="home">FLAGS</div>
<script>
function hideAllPanes() {
  ["home","view-mom-up","view-mom-down","view-outliers","view-options","view-sectors","search-pane"].forEach(function(id){
    var el = document.getElementById(id);
    if (el) el.classList.add("hide");
  });
}
function paintView(v) {}
function setView(v) {
  if (!/^(home|mom-up|mom-down|outliers|options|sectors)$/.test(v)) v = "home";
  hideAllPanes();
  paintView(v);
}
function cardHTML(c) {
  c = c || {};
  return '<article class="card" data-t="'+(c.t||"")+'">'+(c.t||"")+'</article>';
}
</script>
</body></html>"""
        html = bo.ensure_embedded(live, {"breakout": [], "breakdown": []})
        html = pt.ensure_embedded(html, {})
        html = ss.ensure_embedded(html, ss.rank_panel(ss.empty_panel()))
        with tempfile.TemporaryDirectory() as tmp:
            page = Path(tmp) / "page.html"
            runner = Path(tmp) / "run.js"
            page.write_text(html, encoding="utf-8")
            runner.write_text(
                f"""
const {{ JSDOM }} = require({json.dumps(str(jsdom_mod))});
const fs = require("fs");
const html = fs.readFileSync({json.dumps(str(page))}, "utf8");
const dom = new JSDOM(html, {{ runScripts: "dangerously", url: "http://127.0.0.1/factorbook.html" }});
const document = dom.window.document;
const paperBtn = document.getElementById("fd-nav-paper");
if (!paperBtn) {{ console.log(JSON.stringify({{error:"no paper btn"}})); process.exit(2); }}
const bbKind = typeof dom.window.__FD_BB_KIND_OF__ === "function" ? dom.window.__FD_BB_KIND_OF__(paperBtn) : "missing";
const ssKind = typeof dom.window.__FD_SS_KIND_OF__ === "function" ? dom.window.__FD_SS_KIND_OF__(paperBtn) : "missing";
paperBtn.click();
const home = document.getElementById("home");
const pane = document.getElementById("view-paper");
const report = {{
  bbKind: bbKind,
  ssKind: ssKind,
  ignorePaper: !!dom.window.__FD_BB_IGNORE_PAPER__,
  ignoreForeign: !!dom.window.__FD_SS_IGNORE_FOREIGN_NAV__,
  homeHide: !!(home && home.classList.contains("hide")),
  paperOn: !!(pane && pane.classList.contains("fd-paper-on")),
  paperHide: !!(pane && pane.classList.contains("hide"))
}};
console.log(JSON.stringify(report));
""",
                encoding="utf-8",
            )
            proc = subprocess.run(
                [node, str(runner)],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
        if proc.returncode != 0:
            self.fail(proc.stderr or proc.stdout)
        report = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertEqual(report.get("bbKind"), "")
        self.assertEqual(report.get("ssKind"), "")
        self.assertTrue(report.get("ignorePaper"))
        self.assertTrue(report.get("ignoreForeign"))
        self.assertTrue(report.get("homeHide"), report)
        self.assertTrue(report.get("paperOn"), report)
        self.assertFalse(report.get("paperHide"), report)

    def test_jsdom_open_renders_table_not_chips(self) -> None:
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        jsdom_root = Path("/tmp/fd-jsdom")
        jsdom_mod = jsdom_root / "node_modules" / "jsdom"
        if not jsdom_mod.is_dir():
            jsdom_root.mkdir(parents=True, exist_ok=True)
            npm = shutil.which("npm")
            if not npm:
                self.skipTest("npm not installed")
            subprocess.run(
                [npm, "install", "--prefix", str(jsdom_root), "jsdom@24"],
                check=True,
                capture_output=True,
                text=True,
                timeout=60,
            )
        import breakout as bo
        import s_score as ss

        live = """<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body>
<nav id="topnav">
  <button type="button" class="btn nav-btn" data-view="home">Home</button>
  <button type="button" class="btn nav-btn" id="fd-nav-breakout" data-view="breakout" data-fd-breakout="1">Breakout</button>
  <button type="button" class="btn nav-btn" id="fd-nav-experimental" data-view="experimental" data-fd-sscore="1">Experimental</button>
</nav>
<div id="home">FLAGS</div>
<script>
function hideAllPanes() {
  ["home","view-mom-up","view-mom-down","view-outliers","view-options","view-sectors","search-pane"].forEach(function(id){
    var el = document.getElementById(id);
    if (el) el.classList.add("hide");
  });
}
function paintView(v) {}
function setView(v) {
  if (!/^(home|mom-up|mom-down|outliers|options|sectors)$/.test(v)) v = "home";
  hideAllPanes();
  paintView(v);
}
function cardHTML(c) {
  c = c || {};
  return '<article class="card" data-t="'+(c.t||"")+'">'+(c.t||"")+'</article>';
}
</script>
</body></html>"""
        html = bo.ensure_embedded(live, {"breakout": [], "breakdown": []})
        html = pt.ensure_embedded(html, {"CNH": 13.545, "PWR": 598.74})
        html = ss.ensure_embedded(html, ss.rank_panel(ss.empty_panel()))
        with tempfile.TemporaryDirectory() as tmp:
            page = Path(tmp) / "page.html"
            runner = Path(tmp) / "run.js"
            page.write_text(html, encoding="utf-8")
            runner.write_text(
                f"""
const {{ JSDOM }} = require({json.dumps(str(jsdom_mod))});
const fs = require("fs");
(async () => {{
const html = fs.readFileSync({json.dumps(str(page))}, "utf8");
const dom = new JSDOM(html, {{ runScripts: "dangerously", url: "http://127.0.0.1/factorbook.html" }});
await new Promise(function (resolve) {{ setTimeout(resolve, 30); }});
const document = dom.window.document;
const book = {{
  version: 1,
  kind: "factor-desk-paper",
  positions: {{
    CNH: {{ ticker: "CNH", side: "long", entry: 13.63, qty: 1, opened_at: "2026-09-17T00:00:00Z" }},
    PWR: {{ ticker: "PWR", side: "short", entry: 617.64, qty: 1, opened_at: "2026-09-17T01:00:00Z" }}
  }},
  closed: {{
    AAPL: [{{ ticker: "AAPL", side: "long", entry: 100, exit: 97.74, qty: 1, ret_pct: -2.26, closed_at: "2026-09-17T12:00:00Z" }}]
  }}
}};
dom.window.localStorage.setItem("fd-paper-book", JSON.stringify(book));
const paperBtn = document.getElementById("fd-nav-paper");
if (!paperBtn) {{ console.log(JSON.stringify({{error:"no paper btn"}})); process.exit(2); }}
const src = document.getElementById("fd-paper-js") && document.getElementById("fd-paper-js").textContent || "";
paperBtn.click();
const pane = document.getElementById("view-paper");
const opens = document.getElementById("fd-paper-opens");
const closed = document.getElementById("fd-paper-closed");
const openTable = opens && opens.querySelector("table.fd-paper-table");
const closedTable = closed && closed.querySelector("table.fd-paper-table");
const openHeads = openTable ? Array.from(openTable.querySelectorAll("thead th")).map(function (th) {{ return th.textContent; }}) : [];
const closedHeads = closedTable ? Array.from(closedTable.querySelectorAll("thead th")).map(function (th) {{ return th.textContent; }}) : [];
const tableOpenRows = openTable ? openTable.querySelectorAll("tr.fd-paper-open-row").length : 0;
const closeBtn = pane && pane.querySelector("#fd-paper-opens [data-fd-paper-close]");
const cnhRow = pane && pane.querySelector("tr.fd-paper-open-row[data-fd-paper-ticker='CNH']");
const cnhCells = cnhRow ? Array.from(cnhRow.querySelectorAll("td")).map(function (td) {{ return td.textContent.trim(); }}) : [];
if (closeBtn) closeBtn.click();
const after = JSON.parse(dom.window.localStorage.getItem("fd-paper-book") || "{{}}");
const report = {{
  ignorePaper: !!dom.window.__FD_BB_IGNORE_PAPER__,
  ignoreForeign: !!dom.window.__FD_SS_IGNORE_FOREIGN_NAV__,
  sipBeforeShow: src.indexOf("stopImmediatePropagation") !== -1 &&
    src.split('if (kind === "paper")')[1].split('if (kind === "other")')[0].indexOf("stopImmediatePropagation") <
    src.split('if (kind === "paper")')[1].split('if (kind === "other")')[0].indexOf("showPaper(true)"),
  paperOn: !!(pane && pane.classList.contains("fd-paper-on")),
  homeHide: !!(document.getElementById("home") && document.getElementById("home").classList.contains("hide")),
  hasOpenTable: !!openTable,
  openHeads: openHeads,
  chipCount: opens ? opens.querySelectorAll(".fd-paper-chip").length : -1,
  divOpenRows: opens ? opens.querySelectorAll("div.fd-paper-open-row").length : -1,
  tableOpenRows: tableOpenRows,
  closeWired: !!closeBtn,
  clickBound: !!dom.window.__FD_PAPER_CLICK__,
  cnhCells: cnhCells,
  hasClosedTable: !!closedTable,
  closedHeads: closedHeads,
  closedAfterClose: Object.keys(after.positions || {{}}).sort()
}};
console.log(JSON.stringify(report));
}})().catch(function (err) {{ console.error(err); process.exit(1); }});
""",
                encoding="utf-8",
            )
            proc = subprocess.run(
                [node, str(runner)],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
        if proc.returncode != 0:
            self.fail(proc.stderr or proc.stdout)
        report = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertTrue(report.get("ignorePaper"), report)
        self.assertTrue(report.get("ignoreForeign"), report)
        self.assertTrue(report.get("sipBeforeShow"), report)
        self.assertTrue(report.get("paperOn"), report)
        self.assertTrue(report.get("homeHide"), report)
        self.assertTrue(report.get("hasOpenTable"), report)
        self.assertEqual(report.get("openHeads"), ["Ticker", "Side", "Entry", "P&L %", ""])
        self.assertEqual(report.get("chipCount"), 0)
        self.assertEqual(report.get("divOpenRows"), 0)
        self.assertEqual(report.get("tableOpenRows"), 2)
        self.assertTrue(report.get("closeWired"), report)
        self.assertTrue(report.get("clickBound"), report)
        cells = report.get("cnhCells") or []
        self.assertGreaterEqual(len(cells), 4, report)
        self.assertEqual(cells[0], "CNH")
        self.assertEqual(cells[1], "LONG")
        self.assertEqual(cells[2], "13.63")
        self.assertTrue(cells[3].endswith("%"), report)
        self.assertEqual(cells[4], "Close")
        self.assertTrue(report.get("hasClosedTable"), report)
        self.assertEqual(
            report.get("closedHeads"),
            ["Date", "Ticker", "Side", "Entry", "Exit", "P&L %"],
        )
        self.assertEqual(report.get("closedAfterClose"), ["PWR"], report)


if __name__ == "__main__":
    unittest.main()
