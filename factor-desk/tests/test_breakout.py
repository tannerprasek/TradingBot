"""Breakout / Breakdown ranking + write_combined survival. No Bloomberg."""

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

import book_delta  # noqa: E402
import breakout as bo  # noqa: E402
import dapi_enrich as de  # noqa: E402
import desk_dash  # noqa: E402
import mom_streak as ms  # noqa: E402


def _weekdays(start: date, n: int) -> list[date]:
    days: list[date] = []
    d = start
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def _hist_and_card(
    ticker: str,
    scores: list[float],
    *,
    start: date = date(2026, 8, 3),
    list_name: str | None = None,
    tags: list | None = None,
    extra: dict | None = None,
) -> tuple[dict, dict]:
    days = _weekdays(start, len(scores))
    series = [{"date": d.isoformat(), "score": s} for d, s in zip(days, scores)]
    hist = {"names": {ticker: {"series": series, "score": scores[-1]}}}
    card: dict = {
        "t": ticker.split()[0],
        "score": scores[-1],
        "ticker": ticker,
    }
    if list_name:
        card["list"] = list_name
    if tags:
        card["tag_triggers"] = tags
    if extra:
        card.update(extra)
    ms.attach_card(card, hist, asof=days[-1])
    return hist, card


def _merge_hist(parts: list[dict]) -> dict:
    names: dict = {}
    for hist in parts:
        names.update(hist.get("names") or {})
    return {"names": names, "asof": "2026-09-17"}


class RankingTests(unittest.TestCase):
    def test_climber_in_band_selected_maxed_and_weak_dropped(self) -> None:
        h1, climb = _hist_and_card("CLIMB US Equity", [5, 6, 7, 8, 8, 9, 9, 9, 10, 10])
        h2, maxed = _hist_and_card("MAXED US Equity", [13] * 40)
        h3, weak = _hist_and_card("WEAK US Equity", [3, 3, 3, 3, 3, 3, 3, 3])
        h4, dead = _hist_and_card("DEAD US Equity", [1, 1, 1, 0, 0, 0, 0, 0])
        hist = _merge_hist([h1, h2, h3, h4])
        ranked = bo.rank_book([climb, maxed, weak, dead], hist)
        bo_names = {r["t"] for r in ranked["breakout"]}
        bd_names = {r["t"] for r in ranked["breakdown"]}
        self.assertIn("CLIMB", bo_names)
        self.assertNotIn("MAXED", bo_names)
        self.assertNotIn("WEAK", bo_names)
        self.assertNotIn("DEAD", bd_names)
        self.assertLessEqual(len(ranked["breakout"]), bo.RANK_CAP)

    def test_hard_accel_weak_name_can_breakout(self) -> None:
        _h, card = _hist_and_card("ACCEL US Equity", [2, 2, 3, 3, 4, 4, 5, 5])
        row = bo.score_one(card, _h, kind="breakout")
        self.assertIsNotNone(row)
        self.assertGreaterEqual(row["breakout_score"], bo.MIN_COMPOSITE)

    def test_breakdown_cracker_not_already_dead(self) -> None:
        h1, crack = _hist_and_card("CRACK US Equity", [8, 7, 6, 5, 5, 4, 4, 3, 3])
        h2, still_up = _hist_and_card("UP US Equity", [9, 9, 10, 10, 11, 11, 11])
        ranked = bo.rank_book([crack, still_up], _merge_hist([h1, h2]))
        names = {r["t"] for r in ranked["breakdown"]}
        self.assertIn("CRACK", names)
        self.assertNotIn("UP", names)
        self.assertEqual(ranked["breakdown"][0]["side"], "below")

    def test_fresh_streak_outRanks_baked_run(self) -> None:
        _hf, fresh = _hist_and_card("FRESH US Equity", [5, 6, 7, 8, 8, 8, 9, 9])
        baked_scores = [8] * 96 + [8, 8, 8, 9]
        _hb, baked = _hist_and_card("BAKED US Equity", baked_scores)
        ranked = bo.rank_book([fresh, baked], _merge_hist([_hf, _hb]))
        names = [r["t"] for r in ranked["breakout"]]
        self.assertIn("FRESH", names)
        if "BAKED" in names:
            self.assertLess(
                names.index("FRESH"),
                names.index("BAKED"),
            )
        fresh_row = next(r for r in ranked["breakout"] if r["t"] == "FRESH")
        self.assertIn("breakout_score", fresh_row)
        self.assertGreater(fresh["mom_streak"], 1)
        self.assertLess(fresh["mom_streak"], 20)

    def test_cap_keeps_few_cards(self) -> None:
        cards = []
        hists = []
        for i in range(20):
            scores = [6, 6, 7, 7, 8, 8, 8 + (i % 3) * 0.1, 9]
            h, c = _hist_and_card(f"N{i:02d} US Equity", scores)
            hists.append(h)
            cards.append(c)
        ranked = bo.rank_book(cards, _merge_hist(hists))
        self.assertLessEqual(len(ranked["breakout"]), 12)
        self.assertGreaterEqual(len(ranked["breakout"]), 1)
        scores = [r["breakout_score"] for r in ranked["breakout"]]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_flags_hop_boosts_composite(self) -> None:
        scores = [6, 7, 7, 8, 8, 9, 9, 9]
        _h1, plain = _hist_and_card("PLAIN US Equity", scores)
        _h2, flagged = _hist_and_card(
            "FLAG US Equity",
            scores,
            list_name="FLAGS",
            tags=[{"label": "hop"}, {"label": "leader"}],
        )
        a = bo.score_one(plain, _h1, kind="breakout")
        b = bo.score_one(flagged, _h2, kind="breakout")
        self.assertIsNotNone(a)
        self.assertIsNotNone(b)
        self.assertGreater(b["breakout_score"], a["breakout_score"])


class EmbedTests(unittest.TestCase):
    def test_ensure_embedded_injects_nav_and_db_not_sectors(self) -> None:
        html = """<!DOCTYPE html><html><head></head><body>
<nav>
  <button id="refresh">Refresh</button>
  <button>Momentum Up</button>
  <button>Momentum Down</button>
  <button>Outliers</button>
  <button>Options</button>
</nav>
<article class="card" data-t="CLIMB">CLIMB</article>
</body></html>"""
        _h, climb = _hist_and_card("CLIMB US Equity", [6, 7, 8, 8, 9, 9, 10])
        ranked = bo.rank_book([climb], _h)
        out = bo.ensure_embedded(html, ranked)
        self.assertRegex(out, r'<button\b[^>]*data-view=["\']breakout["\']')
        self.assertRegex(out, r'<button\b[^>]*data-view=["\']breakdown["\']')
        self.assertIn('id="fd-breakout-db"', out)
        self.assertIn("CLIMB", out)
        self.assertIn("breakout_score", out)
        self.assertIn('id="view-breakout"', out)
        self.assertIn('id="view-breakdown"', out)
        self.assertIn('id="breakout-grid"', out)
        self.assertIn('id="breakdown-grid"', out)
        self.assertIn('class="grid dense"', out)
        self.assertIn('<div class="ph">Breakout</div>', out)
        self.assertNotRegex(out, r'<article\b[^>]*fd-bb-card')
        self.assertNotIn('data-tab="sectors"', out)
        self.assertNotIn("&gt;", bo.embed_db(ranked))

    def test_css_without_buttons_still_gains_nav(self) -> None:
        """Live HTML already has breakout CSS selectors; that must not skip the tabs."""
        html = f"""<!DOCTYPE html><html><head>
<style id="fd-breakout-css">
{bo.strip_css()}
</style></head><body>
<nav>
  <button id="refresh">Refresh</button>
  <button>Momentum Up</button>
  <button>Momentum Down</button>
  <button>Outliers</button>
  <button>Options</button>
</nav>
<article class="card" data-t="X">X</article>
</body></html>"""
        self.assertIn('data-view="breakout"', html)
        self.assertIn('data-view="breakdown"', html)
        self.assertIsNone(re.search(r'<button\b[^>]*data-view=["\']breakout["\']', html, re.I))
        self.assertIsNone(re.search(r'<button\b[^>]*data-view=["\']breakdown["\']', html, re.I))
        self.assertFalse(bo._has_nav_button(html, view="breakout", data_attr="data-fd-breakout", btn_id="fd-nav-breakout"))
        self.assertFalse(bo._has_nav_button(html, view="breakdown", data_attr="data-fd-breakdown", btn_id="fd-nav-breakdown"))
        out = bo.ensure_embedded(html, {"breakout": [], "breakdown": []})
        bo_btns = re.findall(r'<button\b[^>]*data-view=["\']breakout["\'][^>]*>', out, re.I)
        bd_btns = re.findall(r'<button\b[^>]*data-view=["\']breakdown["\'][^>]*>', out, re.I)
        self.assertEqual(len(bo_btns), 1, out[out.find("<nav") : out.find("</nav>") + 6] if "<nav" in out else out[:800])
        self.assertEqual(len(bd_btns), 1)
        self.assertIn(">Breakout</button>", out)
        self.assertIn(">Breakdown</button>", out)
        self.assertIn('id="fd-nav-breakout"', out)
        self.assertIn('id="fd-nav-breakdown"', out)
        again = bo.ensure_embedded(out, {"breakout": [], "breakdown": []})
        self.assertEqual(len(re.findall(r'<button\b[^>]*data-view=["\']breakout["\'][^>]*>', again, re.I)), 1)
        self.assertEqual(len(re.findall(r'<button\b[^>]*data-view=["\']breakdown["\'][^>]*>', again, re.I)), 1)
        self.assertIn('class="btn nav-btn"', out)

    def test_ensure_embedded_patches_setview_allowlist_and_js_stops_native(self) -> None:
        html = """<!DOCTYPE html><html><body>
<nav id="topnav">
  <button class="btn" data-view="home">Home</button>
  <button class="btn">Momentum Down</button>
</nav>
<div id="home">FLAGS</div>
<div id="view-mom-up" class="view-pane hide"><div class="ph">Momentum Up</div><div class="grid dense" id="mom-up-grid"></div></div>
<script>
function hideAllPanes() {
  ["home","view-mom-up","view-mom-down","view-outliers","view-options","view-sectors","search-pane"].forEach(function(id){
    var el = document.getElementById(id);
    if (el) el.classList.add("hide");
  });
  document.querySelectorAll("#home, #view-mom-up, #view-mom-down, #view-outliers, #view-options, #view-sectors, #search-pane").forEach(function(el){
    el.classList.add("hide");
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
function syncNav() {}
</script>
</body></html>"""
        ranked = {
            "breakout": [{"t": "CLIMB", "ticker": "CLIMB US Equity", "score": 9, "why": "band 9"}],
            "breakdown": [],
        }
        out = bo.ensure_embedded(html, ranked)
        self.assertRegex(
            out,
            r"home\|mom-up\|mom-down\|outliers\|options\|sectors\|breakout\|breakdown",
        )
        self.assertIn(bo.SETVIEW_MARKER, out)
        self.assertIn(bo.HIDEALL_MARKER, out)
        self.assertIn(bo.PAINTVIEW_MARKER, out)
        self.assertIn("window.__FD_BB_SHOW__", out)
        setview_head = out.split("function setView")[1][:700]
        self.assertIn("return;", setview_head)
        self.assertIn("syncNav", setview_head)
        self.assertIn("__FD_BB_SHOW__", setview_head)
        self.assertNotIn("paint();", setview_head.split("return;")[0])
        hideall = out.split("function hideAllPanes")[1][:900]
        self.assertIn("view-breakout", hideall)
        self.assertIn("view-breakdown", hideall)
        self.assertIn("#view-breakout", out)
        paint_head = out.split("function paintView")[1][:500]
        self.assertIn("breakout|breakdown", paint_head)
        js = bo.strip_js()
        self.assertIn("stopImmediatePropagation", js)
        self.assertIn("window.__FD_BB_SHOW__", js)
        self.assertIn('classList.add("hide")', js)
        self.assertIn("view-mom-up", js)
        self.assertIn("view-breakout", js)
        self.assertIn("view-breakdown", js)
        self.assertIn("search-pane", js)
        self.assertIn("cardHTML", js)
        self.assertIn("MOM.cards", js)
        self.assertIn("selectTicker", js)
        self.assertIn("enrich_pills", js)
        self.assertIn('"fd-bb"', js)
        self.assertIn('"mom-streak"', js)
        self.assertIn("if (!out.d) out.d = display", js)
        self.assertIn("card.d = card.d || display", js)
        self.assertIn("if (!display) return null", js)
        self.assertNotIn("fd-bb-card", js)
        self.assertIn('class="btn nav-btn"', out)

    def test_panes_html_emits_view_shells_not_stubs(self) -> None:
        host = bo.panes_html(
            {
                "breakout": [{"t": "CLIMB", "ticker": "CLIMB US Equity", "score": 9, "why": "band 9", "_card": {"t": "CLIMB"}}],
                "breakdown": [{"t": "CRACK"}],
            }
        )
        self.assertIn('id="view-breakout"', host)
        self.assertIn('id="view-breakdown"', host)
        self.assertIn('class="grid dense"', host)
        self.assertIn('id="breakout-grid"', host)
        self.assertIn('id="breakdown-grid"', host)
        self.assertIn('<div class="ph">Breakout</div>', host)
        self.assertIn('<div class="ph">Breakdown</div>', host)
        self.assertIn('id="fd-bb-breakout"', host)
        self.assertIn("hidden", host)
        self.assertNotIn("CLIMB", host)
        self.assertNotIn("CRACK", host)
        self.assertNotIn("fd-bb-card", host)
        self.assertNotIn("No breakout names", host)

    def test_ensure_embedded_none_does_not_wipe_ranked_panes(self) -> None:
        html = """<!DOCTYPE html><html><body>
<nav><button>Momentum Down</button></nav>
<article class="card" data-t="CLIMB">CLIMB</article>
</body></html>"""
        ranked = {
            "breakout": [{"t": "CLIMB", "ticker": "CLIMB US Equity", "score": 9, "why": "band 9"}],
            "breakdown": [],
        }
        filled = bo.ensure_embedded(html, ranked)
        db_blob = re.search(
            r'<script\b[^>]*id=["\']fd-breakout-db["\'][^>]*>(.*?)</script>',
            filled,
            re.I | re.S,
        )
        self.assertIsNotNone(db_blob)
        data = json.loads(db_blob.group(1))
        self.assertEqual(data["breakout"][0]["t"], "CLIMB")
        self.assertIn("band 9", data["breakout"][0]["why"])
        self.assertIn('id="view-breakout"', filled)
        self.assertNotRegex(filled, r'<article\b[^>]*fd-bb-card')
        self.assertNotIn("No breakout names this Refresh.", filled)
        kept = bo.ensure_embedded(filled, None)
        kept_blob = re.search(
            r'<script\b[^>]*id=["\']fd-breakout-db["\'][^>]*>(.*?)</script>',
            kept,
            re.I | re.S,
        )
        self.assertIsNotNone(kept_blob)
        kept_data = json.loads(kept_blob.group(1))
        self.assertEqual(kept_data["breakout"][0]["t"], "CLIMB")
        self.assertIn("band 9", kept_data["breakout"][0]["why"])
        self.assertIn("CLIMB", kept)
        self.assertIn("band 9", kept)
        self.assertNotIn("No breakout names this Refresh.", kept)
        self.assertIn('id="view-breakout"', kept)
        self.assertIn('id="breakout-grid"', kept)

    def test_write_combined_patches_live_html_with_tabs_and_delta(self) -> None:
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
</body></html>"""
        pad = 2_700_000 - len(body.encode("utf-8"))
        live = body + ("<!--" + ("P" * max(pad, 1)) + "-->")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dest = root / "factorbook.html"
            dest.write_text(live, encoding="utf-8")
            rec = de.build_name_record("CLIMB US Equity", {"GICS_SECTOR_NAME": "Financials"})
            rec["mom_score"] = 9
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
            out = desk_dash.write_combined(dest, root=root, book=book)
            text = out.read_text(encoding="utf-8")
            self.assertGreater(out.stat().st_size, 2_000_000)
            self.assertRegex(text, r'<button\b[^>]*data-view=["\']breakout["\']')
            self.assertRegex(text, r'<button\b[^>]*data-view=["\']breakdown["\']')
            self.assertIn(">Breakout</button>", text)
            self.assertIn(">Breakdown</button>", text)
            self.assertIn('id="fd-breakout-db"', text)
            self.assertIn('id="view-breakout"', text)
            self.assertIn('id="breakout-grid"', text)
            self.assertIn('class="grid dense"', text)
            self.assertIn("cardHTML", text)
            self.assertIn("MOM.cards", text)
            self.assertIn("selectTicker", text)
            self.assertNotRegex(text, r'<article\b[^>]*fd-bb-card')
            self.assertIn('id="fd-book-delta"', text)
            self.assertIn("baseline set", text)
            self.assertIn('id="fd-hitch-db"', text)
            self.assertNotIn('data-tab="sectors"', text)
            self.assertTrue((root / book_delta.SNAPSHOT_FILENAME).is_file())
            snap = json.loads((root / book_delta.SNAPSHOT_FILENAME).read_text(encoding="utf-8"))
            self.assertIn("names", snap)


if __name__ == "__main__":
    unittest.main()
