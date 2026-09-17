"""Breakout / Breakdown ranking + write_combined survival. No Bloomberg."""

from __future__ import annotations

import json
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
        self.assertIn('data-view="breakout"', out)
        self.assertIn('data-view="breakdown"', out)
        self.assertIn('id="fd-breakout-db"', out)
        self.assertIn("CLIMB", out)
        self.assertIn("breakout_score", out)
        self.assertNotIn('data-tab="sectors"', out)
        self.assertNotIn("&gt;", bo.embed_db(ranked))

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
            self.assertIn("Breakout", text)
            self.assertIn("Breakdown", text)
            self.assertIn('id="fd-breakout-db"', text)
            self.assertIn('id="fd-book-delta"', text)
            self.assertIn("baseline set", text)
            self.assertIn('id="fd-hitch-db"', text)
            self.assertNotIn('data-tab="sectors"', text)
            self.assertTrue((root / book_delta.SNAPSHOT_FILENAME).is_file())
            snap = json.loads((root / book_delta.SNAPSHOT_FILENAME).read_text(encoding="utf-8"))
            self.assertIn("names", snap)


if __name__ == "__main__":
    unittest.main()
