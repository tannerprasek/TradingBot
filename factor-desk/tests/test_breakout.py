"""Breakout / Breakdown ranking + write_combined survival. No Bloomberg."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
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
import card_render as cr  # noqa: E402
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


def _pass_up() -> list[float]:
    """Score 9, d10 +5, above-5 streak of 6."""
    return [4, 4, 4, 4, 5, 5, 6, 7, 8, 8, 9, 9]


def _pass_dn() -> list[float]:
    """Score 3, d10 −5, below-5 streak of 5."""
    return [8, 8, 8, 8, 7, 6, 5, 4, 4, 3, 3, 3]


class RankingTests(unittest.TestCase):
    def test_climber_in_band_selected_maxed_and_weak_dropped(self) -> None:
        h1, climb = _hist_and_card("CLIMB US Equity", _pass_up(), extra={"rs63": 0.08})
        h2, maxed = _hist_and_card("MAXED US Equity", [13] * 40, extra={"rs63": 0.2})
        h3, weak = _hist_and_card("WEAK US Equity", [3] * 12, extra={"rs63": 0.08})
        h4, dead = _hist_and_card("DEAD US Equity", [1] * 20, extra={"rs63": -0.2})
        hist = _merge_hist([h1, h2, h3, h4])
        ranked = bo.rank_book([climb, maxed, weak, dead], hist)
        bo_names = {r["t"] for r in ranked["breakout"]}
        bd_names = {r["t"] for r in ranked["breakdown"]}
        self.assertIn("CLIMB", bo_names)
        self.assertNotIn("MAXED", bo_names)
        self.assertNotIn("WEAK", bo_names)
        self.assertNotIn("DEAD", bd_names)
        self.assertLessEqual(len(ranked["breakout"]), bo.RANK_CAP)

    def test_weak_accel_and_opt_spike_are_not_breakout(self) -> None:
        _h, card = _hist_and_card(
            "ACCEL US Equity",
            [2, 2, 3, 3, 4, 4, 5, 5],
            extra={"rs63": 0.2, "opt_spike": True},
        )
        self.assertIsNone(bo.score_one(card, _h, kind="breakout"))

    def test_breakdown_cracker_not_already_dead(self) -> None:
        h1, crack = _hist_and_card("CRACK US Equity", _pass_dn(), extra={"rs63": -0.07})
        h2, still_up = _hist_and_card("UP US Equity", [9] * 12, extra={"rs63": -0.2})
        h3, dead = _hist_and_card("DEAD US Equity", [1] * 16, extra={"rs63": -0.3})
        ranked = bo.rank_book([crack, still_up, dead], _merge_hist([h1, h2, h3]))
        names = {r["t"] for r in ranked["breakdown"]}
        self.assertIn("CRACK", names)
        self.assertNotIn("UP", names)
        self.assertNotIn("DEAD", names)
        self.assertEqual(ranked["breakdown"][0]["side"], "below")
        self.assertLess(ranked["breakdown"][0]["inflection_score"], 0)

    def test_fresh_streak_beats_baked_run(self) -> None:
        _hf, fresh = _hist_and_card("FRESH US Equity", _pass_up(), extra={"rs63": 0.05})
        baked_scores = [6] * 6 + [7, 8, 9, 10, 10, 10, 10, 10, 10, 10]
        _hb, baked = _hist_and_card("BAKED US Equity", baked_scores, extra={"rs63": 0.2})
        ranked = bo.rank_book([fresh, baked], _merge_hist([_hf, _hb]))
        names = [r["t"] for r in ranked["breakout"]]
        self.assertIn("FRESH", names)
        self.assertNotIn("BAKED", names)
        fresh_row = next(r for r in ranked["breakout"] if r["t"] == "FRESH")
        self.assertIn("breakout_score", fresh_row)
        self.assertGreaterEqual(fresh["mom_streak"], 2)
        self.assertLessEqual(fresh["mom_streak"], 15)
        self.assertGreater(baked["mom_streak"], 15)

    def test_cap_is_a_backstop_not_the_filter(self) -> None:
        cards = []
        hists = []
        for i in range(30):
            lift = 4 + (i % 4)
            early = 8 - lift
            scores = [early, early, 4, 4, 5, 5, 6, 7, 8, 8, 8, 8]
            h, c = _hist_and_card(
                f"N{i:02d} US Equity",
                scores,
                extra={"rs63": 0.02 + i * 0.001},
            )
            hists.append(h)
            cards.append(c)
        ranked = bo.rank_book(cards, _merge_hist(hists))
        self.assertEqual(len(ranked["breakout"]), bo.RANK_CAP)
        self.assertLessEqual(bo.RANK_CAP, 24)
        scores = [r["inflection_score"] for r in ranked["breakout"]]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_flags_hop_boosts_composite_opt_spike_does_not(self) -> None:
        scores = _pass_up()
        _h1, plain = _hist_and_card("PLAIN US Equity", scores, extra={"rs63": 0.05})
        _h2, flagged = _hist_and_card(
            "FLAG US Equity",
            scores,
            list_name="FLAGS",
            tags=[{"label": "hop"}, {"label": "leader"}],
            extra={"rs63": 0.05},
        )
        _h3, spiked = _hist_and_card(
            "SPIKE US Equity",
            scores,
            extra={"rs63": 0.05, "opt_spike": True},
        )
        a = bo.score_one(plain, _h1, kind="breakout")
        b = bo.score_one(flagged, _h2, kind="breakout")
        c = bo.score_one(spiked, _h3, kind="breakout")
        self.assertIsNotNone(a)
        self.assertIsNotNone(b)
        self.assertIsNotNone(c)
        self.assertGreater(b["breakout_score"], a["breakout_score"])
        self.assertEqual(c["breakout_score"], a["breakout_score"])


    def test_slim_payload_embeds_portable_stats_and_tags(self) -> None:
        ranked = {
            "breakout": [
                {
                    "t": "SPCX",
                    "ticker": "SPCX US Equity",
                    "score": 9,
                    "delta": 2,
                    "lookback": 7,
                    "why": "band 9 · +2 / 7d",
                    "_card": {
                        "t": "SPCX",
                        "r20": 1.2,
                        "rs63": 0.4,
                        "atr_pct": 2.1,
                        "tags": ["MA FAN", "BREAKOUT"],
                        "px_series": list(range(200)),
                    },
                }
            ],
            "breakdown": [],
        }
        payload = bo.slim_payload(ranked)
        card = payload["breakout"][0]["card"]
        self.assertEqual(card["r20"], 1.2)
        self.assertEqual(card["rs63"], 0.4)
        self.assertEqual(card["atr_pct"], 2.1)
        self.assertEqual(card["tags"], ["MA FAN", "BREAKOUT"])
        self.assertNotIn("px_series", card)
        self.assertEqual(payload["breakout"][0]["r20"], 1.2)
        self.assertEqual(payload["breakout"][0]["rs63"], 0.4)
        self.assertEqual(payload["breakout"][0]["atr_pct"], 2.1)
        self.assertIsNotNone(payload["breakout"][0]["card"])
        blob = json.dumps(payload)
        self.assertNotIn("px_series", blob)
        self.assertIn("fd-bb-band", json.dumps(payload["breakout"][0]["pills"]))

    def test_px_stats_from_close_history(self) -> None:
        spy = [100.0]
        spcx = [50.0]
        for _ in range(80):
            spy.append(round(spy[-1] * 1.001, 6))
            spcx.append(round(spcx[-1] * 1.004, 6))
        stats = bo.px_stats(
            {"ticker": "SPCX US Equity", "px_series": spcx},
            {"SPY US Equity": [(None, px) for px in spy]},
            ticker="SPCX US Equity",
        )
        self.assertIsInstance(stats["r20"], float)
        self.assertIsInstance(stats["rs63"], float)
        self.assertIsInstance(stats["atr_pct"], float)
        self.assertIsInstance(stats["day"], float)
        self.assertGreater(stats["r20"], 0)
        self.assertLess(abs(stats["r20"]), 1.0)
        self.assertGreater(stats["atr_pct"], 0)
        self.assertNotEqual(stats["r20"], stats["rs63"])

    def test_slim_payload_reads_live_metrics_blob(self) -> None:
        ranked = {
            "breakout": [
                {
                    "t": "SPCX",
                    "ticker": "SPCX US Equity",
                    "score": 9,
                    "why": "band 9",
                    "_card": {
                        "t": "SPCX",
                        "ticker": "SPCX US Equity",
                        "metrics": {"r20_pct": 0.1277, "rs_63": 0.041, "atr_pct": 2.3},
                    },
                }
            ],
            "breakdown": [],
        }
        row = bo.slim_payload(ranked)["breakout"][0]
        self.assertEqual(row["metrics"]["r20_pct"], 0.1277)
        self.assertEqual(row["metrics"]["rs_63"], 0.041)
        self.assertEqual(row["metrics"]["atr_pct"], 2.3)
        self.assertEqual(row["r20"], 0.1277)
        self.assertEqual(row["card"]["metrics"]["r20_pct"], 0.1277)
        self.assertEqual(row["card"]["metrics"]["atr_pct"], 2.3)

    def test_slim_payload_merges_html_live_map_when_card_has_no_metrics(self) -> None:
        ranked = {
            "breakout": [
                {
                    "t": "SPCX",
                    "ticker": "SPCX US Equity",
                    "score": 9,
                    "r20": None,
                    "rs63": None,
                    "atr_pct": None,
                    "card": None,
                    "why": "band 9",
                }
            ],
            "breakdown": [
                {
                    "t": "CRACK",
                    "ticker": "CRACK US Equity",
                    "score": 4,
                    "r20": None,
                    "card": None,
                }
            ],
        }
        live_map = {
            "SPCX": {"r20_pct": 0.1277, "rs_63": 0.041, "atr_pct": 2.3},
            "CRACK": {"r20_pct": -0.08, "rs_63": -0.11, "atr_pct": 4.2},
        }
        payload = bo.slim_payload(ranked, live_map=live_map)
        up = payload["breakout"][0]
        down = payload["breakdown"][0]
        self.assertEqual(up["metrics"]["r20_pct"], 0.1277)
        self.assertEqual(up["r20"], 0.1277)
        self.assertEqual(up["atr_pct"], 2.3)
        self.assertEqual(up["card"]["metrics"]["rs_63"], 0.041)
        self.assertEqual(down["metrics"]["r20_pct"], -0.08)
        self.assertEqual(down["metrics"]["atr_pct"], 4.2)
        self.assertIsInstance(up["card"], dict)

    def test_slim_payload_computes_stats_when_card_fields_null(self) -> None:
        closes = [100.0]
        for i in range(70):
            closes.append(round(closes[-1] * (1.01 if i % 3 else 0.997), 6))
        ranked = {
            "breakout": [
                {
                    "t": "SPCX",
                    "ticker": "SPCX US Equity",
                    "score": 9,
                    "r20": None,
                    "rs63": None,
                    "atr_pct": None,
                    "card": None,
                    "why": "band 9",
                    "_card": {
                        "t": "SPCX",
                        "ticker": "SPCX US Equity",
                        "score": 9,
                        "px_series": closes,
                    },
                }
            ],
            "breakdown": [],
        }
        payload = bo.slim_payload(ranked)
        row = payload["breakout"][0]
        self.assertIsInstance(row["r20"], float)
        self.assertIsInstance(row["rs63"], float)
        self.assertIsInstance(row["atr_pct"], float)
        self.assertIsInstance(row["day"], float)
        self.assertIsInstance(row["card"], dict)
        self.assertEqual(row["card"]["r20"], row["r20"])
        self.assertLess(abs(row["r20"]), 1.0)
        self.assertIn("r20_pct", row["metrics"])
        self.assertEqual(row["metrics"]["r20_pct"], row["r20"])
        self.assertNotIn("px_series", json.dumps(payload))

    def test_rank_book_fills_stats_from_prices_long(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            start = date(2026, 5, 4)
            days = _weekdays(start, 80)
            lines = ["date,ticker,close"]
            spy = 400.0
            px = 20.0
            for i, d in enumerate(days):
                spy *= 1.001
                px *= 1.003
                lines.append(f"{d.isoformat()},SPY US Equity,{spy:.4f}")
                lines.append(f"{d.isoformat()},SPCX US Equity,{px:.4f}")
            (root / "prices_long.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
            hist, card = _hist_and_card("SPCX US Equity", _pass_up())
            ranked = bo.rank_book([card], hist, root=root)
            self.assertTrue(ranked["breakout"], ranked)
            row = ranked["breakout"][0]
            self.assertIsInstance(row.get("r20"), float)
            self.assertIsInstance(row.get("rs63"), float)
            self.assertIsInstance(row.get("atr_pct"), float)
            slim = bo.slim_payload(ranked)["breakout"][0]
            self.assertIsInstance(slim["r20"], float)
            self.assertIsInstance(slim["atr_pct"], float)
            self.assertGreater(slim["atr_pct"], 0)
            self.assertEqual(ranked["breakout"][0]["dispersion_field"], "rs63")
            self.assertGreater(ranked["breakout"][0]["dispersion"], 0)


class InflectionGateTests(unittest.TestCase):
    """Hard gates: band, d10, streak length, dispersion. Each case is true or false."""

    def _row(self, scores: list[float], *, kind: str = "breakout", extra: dict | None = None):
        hist, card = _hist_and_card("NAME US Equity", scores, extra=extra)
        return bo.score_one(card, hist, kind=kind), card

    def test_breakout_band_edges(self) -> None:
        # 7 inclusive passes; 6 and 11 do not. d10 and streak stay fresh.
        low = [3, 3, 3, 3, 4, 4, 5, 5, 6, 6, 7, 7]
        hi = [6, 6, 6, 6, 6, 6, 7, 8, 9, 9, 10, 10]
        mush = [2, 2, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6]
        ext = [6, 6, 6, 6, 7, 7, 8, 9, 10, 10, 11, 11]
        self.assertIsNotNone(self._row(low, extra={"rs63": 0.04})[0])
        self.assertIsNotNone(self._row(hi, extra={"rs63": 0.04})[0])
        self.assertIsNone(self._row(mush, extra={"rs63": 0.04})[0])
        self.assertIsNone(self._row(ext, extra={"rs63": 0.2})[0])

    def test_breakout_d10_must_exceed_3_and_exist(self) -> None:
        exact = [5, 5, 5, 5, 5, 6, 6, 7, 7, 8, 8, 8]  # d10 = 8-5 = 3
        strong = [4, 4, 4, 4, 5, 5, 6, 7, 8, 8, 9, 9]  # d10 = 5
        short = [6, 7, 8, 9, 9]
        self.assertIsNone(self._row(exact, extra={"rs63": 0.05})[0])
        row, card = self._row(strong, extra={"rs63": 0.05})
        self.assertIsNotNone(row)
        self.assertGreater(card["mom_score_d10"], 3)
        self.assertEqual(row["dispersion_field"], "rs63")
        self.assertIsNone(self._row(short, extra={"rs63": 0.2})[0])

    def test_breakout_streak_fresh_or_just_crossed_not_baked(self) -> None:
        just = [3, 3, 3, 3, 3, 4, 4, 4, 4, 5, 5, 8]  # streak 1, score 8, d10 +5
        fresh15 = [4] + [6] * 5 + [7] * 5 + [8] * 4 + [10]
        baked = [6] * 6 + [7] * 5 + [8] * 4 + [10]  # streak 16, d10 +4, score 10
        flat_long = [10] * 20
        mild = [8] * 10 + [10] * 10  # score 10, d10 +2, streak 20
        self.assertEqual(self._row(just, extra={"rs63": 0.05})[1]["mom_streak"], 1)
        self.assertIsNotNone(self._row(just, extra={"rs63": 0.05})[0])
        row15, card15 = self._row(fresh15, extra={"rs63": 0.05})
        self.assertEqual(card15["mom_streak"], 15)
        self.assertIsNotNone(row15)
        self.assertGreater(self._row(baked, extra={"rs63": 0.2})[1]["mom_streak"], 15)
        self.assertIsNone(self._row(baked, extra={"rs63": 0.2})[0])
        self.assertIsNone(self._row(flat_long, extra={"rs63": 0.3})[0])
        self.assertIsNone(self._row(mild, extra={"rs63": 0.3})[0])

    def test_breakout_dispersion_residual_then_rs63(self) -> None:
        scores = _pass_up()
        self.assertIsNotNone(self._row(scores, extra={"residual_20d": 0.04})[0])
        row, _card = self._row(scores, extra={"residual_20d": 0.04, "rs63": -0.2})
        self.assertIsNotNone(row)
        self.assertEqual(row["dispersion_field"], "residual_20d")
        self.assertGreater(row["dispersion"], 0)
        # Present residual of 0 or negative does not fall through to a hot RS63.
        self.assertIsNone(self._row(scores, extra={"residual_20d": 0, "rs63": 0.4})[0])
        self.assertIsNone(self._row(scores, extra={"residual_20d": -0.02, "rs63": 0.4})[0])
        self.assertIsNone(self._row(scores, extra={"rs63": 0})[0])
        self.assertIsNone(self._row(scores, extra={"rs63": -0.01})[0])
        self.assertIsNone(self._row(scores)[0])
        met_hist, met = _hist_and_card(
            "MET US Equity",
            scores,
            extra={"metrics": {"rs_63": 0.03}},
        )
        met_row = bo.score_one(met, met_hist, kind="breakout")
        self.assertIsNotNone(met_row)
        self.assertEqual(met_row["dispersion_field"], "rs63")
        grp, _gcard = self._row(scores, extra={"vs_group": 0.02, "rs63": -0.4})
        self.assertEqual(grp["dispersion_field"], "vs_group")
        # Rising prices without a benchmark are not RS63.
        hist, card = _hist_and_card(
            "RAW US Equity",
            scores,
            extra={"px_series": [100.0 * (1.01 ** i) for i in range(80)]},
        )
        self.assertIsNone(bo.score_one(card, hist, kind="breakout"))

    def test_opt_spike_does_not_open_the_gate(self) -> None:
        scores = [8] * 12  # in band, d10 0, long streak
        row, _card = self._row(scores, extra={"rs63": 0.2, "opt_spike": True})
        self.assertIsNone(row)

    def test_breakdown_mirrors_gates(self) -> None:
        ok = _pass_dn()
        score2 = [8, 8, 8, 8, 7, 6, 5, 4, 3, 3, 2, 2]
        score6 = [10, 10, 10, 10, 9, 8, 7, 7, 6, 6, 6, 6]  # 6 is still above 5
        exact = [7, 7, 7, 7, 7, 6, 6, 5, 4, 4, 4, 4]  # d10 = -3
        dead = [4] * 4 + [1] * 12
        just = [9, 9, 9, 9, 8, 8, 7, 7, 6, 6, 5, 4]
        baked = [8] * 6 + [4] * 16
        self.assertIsNotNone(self._row(ok, kind="breakdown", extra={"rs63": -0.05})[0])
        self.assertIsNotNone(self._row(score2, kind="breakdown", extra={"rs63": -0.05})[0])
        # Score 6 is inside the band but has not broken below 5.
        self.assertIsNone(self._row(score6, kind="breakdown", extra={"residual_20d": -0.03})[0])
        self.assertIsNone(self._row(exact, kind="breakdown", extra={"rs63": -0.05})[0])
        self.assertIsNone(self._row(dead, kind="breakdown", extra={"rs63": -0.4})[0])
        self.assertIsNone(self._row(ok, kind="breakdown", extra={"rs63": 0.05})[0])
        self.assertIsNone(self._row(ok, kind="breakdown")[0])
        just_row, just_card = self._row(just, kind="breakdown", extra={"rs63": -0.04})
        self.assertEqual(just_card["mom_streak"], 1)
        self.assertEqual(just_card["mom_streak_side"], "below")
        self.assertIsNotNone(just_row)
        self.assertGreater(self._row(baked, kind="breakdown", extra={"rs63": -0.2})[1]["mom_streak"], 15)
        self.assertIsNone(self._row(baked, kind="breakdown", extra={"rs63": -0.2})[0])

    def test_gate_truth_table(self) -> None:
        p = bo._passes_breakout
        self.assertTrue(p(7, 3.01, 2, "above", 0.01))
        self.assertTrue(p(10, 4, 15, "above", 0.01))
        self.assertTrue(p(8, 4, 1, "above", 0.01))  # just crossed
        self.assertFalse(p(6, 5, 4, "above", 0.05))
        self.assertFalse(p(6.99, 5, 4, "above", 0.05))
        self.assertFalse(p(11, 5, 4, "above", 0.05))
        self.assertFalse(p(10.01, 5, 4, "above", 0.05))
        self.assertFalse(p(8, 3, 4, "above", 0.05))
        self.assertFalse(p(8, None, 4, "above", 0.05))
        self.assertFalse(p(8, 5, 16, "above", 0.05))
        self.assertFalse(p(8, 5, 0, "above", 0.05))
        self.assertFalse(p(8, 5, 4, "below", 0.05))
        self.assertFalse(p(8, 5, 4, "above", 0))
        self.assertFalse(p(8, 5, 4, "above", None))
        q = bo._passes_breakdown
        self.assertTrue(q(2, -3.01, 2, "below", -0.01))
        self.assertTrue(q(6, -4, 4, "below", -0.01))  # band allows 6; side must still be below
        self.assertTrue(q(4, -4, 1, "below", -0.01))
        self.assertTrue(q(4, -4, 15, "below", -0.01))
        self.assertFalse(q(1, -6, 4, "below", -0.1))
        self.assertFalse(q(1.99, -6, 4, "below", -0.1))
        self.assertFalse(q(6.01, -6, 4, "below", -0.1))
        self.assertFalse(q(4, -3, 4, "below", -0.1))
        self.assertFalse(q(4, None, 4, "below", -0.1))
        self.assertFalse(q(4, -5, 16, "below", -0.1))
        self.assertFalse(q(4, -5, 4, "above", -0.1))
        self.assertFalse(q(4, -5, 4, "below", 0))
        self.assertFalse(q(4, -5, 4, "below", None))

    def test_breakdown_sorts_most_negative_inflection_first(self) -> None:
        soft = [8, 8, 7, 7, 6, 6, 5, 4, 4, 4, 4, 4]  # d10 = -4
        hard = [12, 12, 9, 8, 7, 6, 5, 4, 3, 3, 3, 3]  # d10 = -9, score 3
        h1, a = _hist_and_card("SOFT US Equity", soft, extra={"rs63": -0.02})
        h2, b = _hist_and_card("HARD US Equity", hard, extra={"rs63": -0.12})
        ranked = bo.rank_book([a, b], _merge_hist([h1, h2]))
        names = [r["t"] for r in ranked["breakdown"]]
        self.assertEqual(names, ["HARD", "SOFT"])
        inflections = [r["inflection_score"] for r in ranked["breakdown"]]
        self.assertEqual(inflections, sorted(inflections))
        self.assertLess(inflections[0], inflections[1])
        self.assertLess(inflections[0], 0)

    def test_early_band_and_mid_streak_rank_higher(self) -> None:
        early = bo._inflection_magnitude(
            score=7, d10=5, streak=6, dispersion=0.05, card={}, kind="breakout"
        )
        late = bo._inflection_magnitude(
            score=10, d10=5, streak=6, dispersion=0.05, card={}, kind="breakout"
        )
        self.assertGreater(early, late)
        just = [3, 3, 3, 3, 3, 4, 4, 4, 4, 5, 5, 8]
        mid = [3, 3, 4, 4, 5, 5, 6, 7, 8, 8, 8, 8]
        h1, a = _hist_and_card("JUST US Equity", just, extra={"rs63": 0.05})
        h2, b = _hist_and_card("MID US Equity", mid, extra={"rs63": 0.05})
        ranked = bo.rank_book([a, b], _merge_hist([h1, h2]))
        self.assertEqual([r["t"] for r in ranked["breakout"]], ["MID", "JUST"])


def _live_mom_script(cards: list[dict]) -> str:
    """Synthetic live factorbook: MOM card objects with nested metrics, no fd-mom-db."""
    bits = []
    for card in cards:
        t = card["t"]
        m = card["metrics"]
        bits.append(
            '{t:"%s", d:"%s", ticker:"%s US Equity", score:%s, '
            '"metrics":{"r20_pct":%s, "rs_63":%s, "atr_pct":%s}}'
            % (t, t, t, card.get("score", 9), m["r20_pct"], m["rs_63"], m["atr_pct"])
        )
    return (
        "<script>\nwindow.MOM = { cards: [], up: ["
        + ",".join(bits)
        + "], down: [] };\n</script>"
    )


class LiveMetricsMapTests(unittest.TestCase):
    def test_extract_live_metrics_map_from_embedded_card_json(self) -> None:
        html = (
            "<!DOCTYPE html><html><body>"
            + _live_mom_script(
                [
                    {"t": "SPCX", "metrics": {"r20_pct": 0.1277, "rs_63": 0.041, "atr_pct": 2.3}},
                    {"t": "CRACK", "metrics": {"r20_pct": -0.08, "rs_63": -0.11, "atr_pct": 4.2}},
                ]
            )
            + "</body></html>"
        )
        self.assertNotIn("fd-mom-db", html)
        got = bo.extract_live_metrics_map(html)
        self.assertEqual(got["SPCX"]["r20_pct"], 0.1277)
        self.assertEqual(got["SPCX"]["rs_63"], 0.041)
        self.assertEqual(got["SPCX"]["atr_pct"], 2.3)
        self.assertEqual(got["CRACK"]["r20_pct"], -0.08)
        self.assertIn("SPCX US Equity", got)

    def test_extract_live_metrics_map_reads_ticker_after_metrics(self) -> None:
        html = (
            '<script>var cards=[{metrics:{r20_pct:0.05,rs_63:0.01,atr_pct:3.1},t:"LATE"}];'
            "</script>"
        )
        got = bo.extract_live_metrics_map(html)
        self.assertEqual(got["LATE"]["r20_pct"], 0.05)
        self.assertEqual(got["LATE"]["atr_pct"], 3.1)

    def test_enrich_ranked_from_map_fills_null_rows(self) -> None:
        ranked = {
            "breakout": [{"t": "SPCX", "ticker": "SPCX US Equity", "score": 9, "r20": None, "card": None}],
            "breakdown": [{"t": "CRACK", "score": 4, "r20": None, "card": None}],
        }
        live_map = {
            "SPCX": {"r20_pct": 0.1277, "rs_63": 0.041, "atr_pct": 2.3},
            "CRACK": {"r20_pct": -0.08, "rs_63": -0.11, "atr_pct": 4.2},
        }
        out = bo.enrich_ranked_from_map(ranked, live_map)
        self.assertEqual(out["breakout"][0]["metrics"]["r20_pct"], 0.1277)
        self.assertEqual(out["breakout"][0]["r20"], 0.1277)
        self.assertEqual(out["breakdown"][0]["metrics"]["atr_pct"], 4.2)


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
        _h, climb = _hist_and_card("CLIMB US Equity", _pass_up(), extra={"rs63": 0.08})
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
        self.assertIn("__FD_FIND_CARD__", js)
        self.assertIn("__FD_RENDER_CARD__", js)
        self.assertIn("__FD_BB_RENDER_ROW__", js)
        self.assertIn("callCardHTML", js)
        self.assertIn("__FD_BB_CARDHTML__", js)
        self.assertIn(bo.JS_VER, js)
        self.assertIn("window.cardHTML", js)
        self.assertIn("function callCardHTML", js)
        self.assertIn("selectTicker", js)
        self.assertIn("isNavControl", js)
        self.assertIn('closest("article.card, .card")', js)
        self.assertNotIn('closest("button, [data-view], [data-fd-breakout], [data-fd-breakdown]")', js)
        self.assertIn("fmtPct", js)
        self.assertIn("fmtAtr", js)
        self.assertIn("r20_pct", js)
        self.assertIn("Math.abs(n) < 1", js)
        self.assertNotIn("if (!display) return null", js)
        self.assertNotIn("fd-bb-card", js)
        self.assertNotIn('key: "fd-bb"', js)
        self.assertNotIn("cls: \"fd-bb-why\"", js)
        self.assertNotIn("denseHTML", js)
        self.assertNotIn("__FD_BB_NO_CARDHTML_FALLBACK__", js)
        self.assertNotIn("__FD_BB_PORTABLE_FIX__", js)
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
        pill_labels = [p.get("label") for p in data["breakout"][0].get("pills") or []]
        self.assertTrue(any(str(lab).startswith("band ") for lab in pill_labels))
        self.assertFalse(any(" · " in str(lab) for lab in pill_labels))
        card = data["breakout"][0].get("card") or {}
        self.assertEqual(card.get("t") or card.get("d"), "CLIMB")
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

    def test_ensure_embedded_none_refreshes_js_to_cardhtml(self) -> None:
        old_js = """
<script id="fd-breakout-js">
(function () {
  if (window.__FD_BB_BOUND__) return;
  window.__FD_BB_BOUND__ = true;
  function renderRow(row) {
    var fn = window.cardHTML;
    return fn(row);
  }
})();
</script>
"""
        html = f"""<!DOCTYPE html><html><body>
<nav><button>Momentum Down</button></nav>
<script type="application/json" id="fd-breakout-db">{{"breakout":[{{"t":"SPCX","ticker":"SPCX US Equity","score":9,"r20":null,"rs63":null,"atr_pct":null,"card":null,"why":"band 9"}}],"breakdown":[]}}</script>
{old_js}
</body></html>"""
        self.assertEqual(html.count("id=\"fd-breakout-js\""), 1)
        self.assertIn("window.cardHTML", html)
        out = bo.ensure_embedded(html, None)
        scripts = re.findall(
            r'<script\b[^>]*id=["\']fd-breakout-js["\'][^>]*>(.*?)</script>',
            out,
            re.I | re.S,
        )
        self.assertEqual(len(scripts), 1, "duplicate fd-breakout-js left behind")
        js = scripts[0]
        self.assertIn(bo.JS_VER, js)
        self.assertIn("__FD_BB_CARDHTML__", js)
        self.assertIn("callCardHTML", js)
        self.assertIn("window.cardHTML", js)
        self.assertNotIn("denseHTML", js)
        self.assertNotIn("__FD_BB_NO_CARDHTML_FALLBACK__", js)
        db_blob = re.search(
            r'<script\b[^>]*id=["\']fd-breakout-db["\'][^>]*>(.*?)</script>',
            out,
            re.I | re.S,
        )
        self.assertIsNotNone(db_blob)
        data = json.loads(db_blob.group(1))
        self.assertEqual(data["breakout"][0]["t"], "SPCX")

    def test_ensure_embedded_stamps_html_metrics_onto_ranked_rows(self) -> None:
        """CoS hot-fill: scrape ~396 live card metrics JSON onto BB db at embed time."""
        cards = []
        ranked_bo = []
        ranked_bd = []
        for i in range(12):
            t = f"UP{i:02d}"
            cards.append(
                {
                    "t": t,
                    "metrics": {
                        "r20_pct": round(0.10 + i * 0.002, 4),
                        "rs_63": round(0.03 + i * 0.001, 4),
                        "atr_pct": round(2.0 + i * 0.1, 2),
                    },
                }
            )
            ranked_bo.append(
                {
                    "t": t,
                    "ticker": f"{t} US Equity",
                    "score": 9,
                    "r20": None,
                    "rs63": None,
                    "atr_pct": None,
                    "card": None,
                    "why": "band 9",
                }
            )
            dt = f"DN{i:02d}"
            cards.append(
                {
                    "t": dt,
                    "metrics": {
                        "r20_pct": round(-0.09 - i * 0.001, 4),
                        "rs_63": round(-0.05 - i * 0.001, 4),
                        "atr_pct": round(3.5 + i * 0.1, 2),
                    },
                }
            )
            ranked_bd.append(
                {
                    "t": dt,
                    "ticker": f"{dt} US Equity",
                    "score": 4,
                    "r20": None,
                    "rs63": None,
                    "atr_pct": None,
                    "card": None,
                    "why": "band 4",
                }
            )
        html = (
            "<!DOCTYPE html><html><body><nav><button>Momentum Down</button></nav>"
            + _live_mom_script(cards)
            + "</body></html>"
        )
        self.assertNotIn("fd-mom-db", html)
        self.assertGreaterEqual(html.count('"metrics":{'), 24)
        out = bo.ensure_embedded(
            html, {"breakout": ranked_bo, "breakdown": ranked_bd}
        )
        blob = re.search(
            r'<script\b[^>]*id=["\']fd-breakout-db["\'][^>]*>(.*?)</script>',
            out,
            re.I | re.S,
        )
        self.assertIsNotNone(blob)
        data = json.loads(blob.group(1))
        self.assertEqual(len(data["breakout"]), 12)
        self.assertEqual(len(data["breakdown"]), 12)
        for i, row in enumerate(data["breakout"]):
            self.assertIsInstance(row["metrics"]["r20_pct"], float, row)
            self.assertEqual(row["metrics"]["r20_pct"], round(0.10 + i * 0.002, 4))
            self.assertEqual(row["r20"], row["metrics"]["r20_pct"])
            self.assertEqual(row["metrics"]["rs_63"], round(0.03 + i * 0.001, 4))
            self.assertEqual(row["metrics"]["atr_pct"], round(2.0 + i * 0.1, 2))
            self.assertGreaterEqual(abs(row["atr_pct"]), 1.0)
            self.assertEqual(row["card"]["metrics"]["r20_pct"], row["metrics"]["r20_pct"])
        for i, row in enumerate(data["breakdown"]):
            self.assertEqual(row["metrics"]["r20_pct"], round(-0.09 - i * 0.001, 4))
            self.assertEqual(row["metrics"]["atr_pct"], round(3.5 + i * 0.1, 2))
        bb_js = re.search(
            r'<script\b[^>]*id=["\']fd-breakout-js["\'][^>]*>(.*?)</script>',
            out,
            re.I | re.S,
        )
        self.assertIsNotNone(bb_js)
        self.assertIn("cardHTML", bb_js.group(1))
        self.assertIn("function renderRow", bb_js.group(1))
        render = bb_js.group(1).split("function renderRow", 1)[1][:700]
        self.assertIn("callCardHTML", render)
        self.assertNotIn("denseHTML", render)

    def test_ensure_embedded_none_enriches_existing_db_from_html_map(self) -> None:
        html = (
            "<!DOCTYPE html><html><body>\n"
            "<nav><button>Momentum Down</button></nav>\n"
            '<script type="application/json" id="fd-breakout-db">'
            '{"breakout":[{"t":"SPCX","ticker":"SPCX US Equity","score":9,"r20":null,'
            '"rs63":null,"atr_pct":null,"card":null,"why":"band 9"}],'
            '"breakdown":[{"t":"CRACK","ticker":"CRACK US Equity","score":4,"r20":null,'
            '"rs63":null,"atr_pct":null,"card":null}]}'
            "</script>\n"
            + _live_mom_script(
                [
                    {"t": "SPCX", "metrics": {"r20_pct": 0.1277, "rs_63": 0.041, "atr_pct": 2.3}},
                    {"t": "CRACK", "metrics": {"r20_pct": -0.08, "rs_63": -0.11, "atr_pct": 4.2}},
                ]
            )
            + "\n</body></html>"
        )
        self.assertNotIn("fd-mom-db", html)
        out = bo.ensure_embedded(html, None)
        blob = re.search(
            r'<script\b[^>]*id=["\']fd-breakout-db["\'][^>]*>(.*?)</script>',
            out,
            re.I | re.S,
        )
        self.assertIsNotNone(blob)
        data = json.loads(blob.group(1))
        up = data["breakout"][0]
        down = data["breakdown"][0]
        self.assertEqual(up["t"], "SPCX")
        self.assertEqual(up["metrics"]["r20_pct"], 0.1277)
        self.assertEqual(up["r20"], 0.1277)
        self.assertEqual(up["metrics"]["atr_pct"], 2.3)
        self.assertEqual(up["card"]["metrics"]["rs_63"], 0.041)
        self.assertEqual(down["metrics"]["r20_pct"], -0.08)
        self.assertEqual(down["metrics"]["atr_pct"], 4.2)
        js = re.search(
            r'<script\b[^>]*id=["\']fd-breakout-js["\'][^>]*>(.*?)</script>',
            out,
            re.I | re.S,
        )
        self.assertIsNotNone(js)
        self.assertIn("cardHTML", js.group(1))

    def test_strip_js_calls_live_cardhtml(self) -> None:
        js = bo.strip_js()
        self.assertIn("cardHTML", js)
        self.assertIn("callCardHTML", js)
        self.assertIn("momCardFor", js)
        self.assertIn("rewriteAtr", js)
        self.assertNotIn("denseHTML", js)
        self.assertNotIn("hasGhostMatrix", js)
        self.assertIn("fmtPct", js)
        self.assertIn("fmtAtr", js)
        self.assertIn("selectTicker", js)
        self.assertIn("function renderRow", js)
        render = js.split("function renderRow", 1)[1][:800]
        self.assertIn("callCardHTML", render)
        self.assertNotIn("denseHTML", render)
        self.assertNotIn("tryPortable", render)
        src = Path(bo.__file__).read_text(encoding="utf-8")
        js_block = src.split("def strip_js()", 1)[1].split("def panes_html", 1)[0]
        self.assertIn("window.cardHTML", js_block)
        self.assertIn("callCardHTML", js_block)
        self.assertNotIn('closest("button, [data-view], [data-fd-breakout], [data-fd-breakdown]")', js)

    def test_jsdom_cardhtml_paints_mom_chrome(self) -> None:
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
        live = """<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body>
<nav>
  <button class="btn" data-view="home">Home</button>
  <button class="btn">Momentum Down</button>
</nav>
<div id="home">FLAGS</div>
<div id="breakout-grid" class="grid dense"></div>
<script>
window.__cardHTMLCalls = [];
window.MOM = { cards: [], up: [
  {t:"SPCX", d:"SPCX", ticker:"SPCX US Equity", score:9, mom_streak_label:"↑3d>5",
   "metrics":{"r20_pct":0.1277, "rs_63":0.04, "atr_pct":2.3}}
], down: [] };
function cardHTML(c) {
  c = c || {};
  window.__cardHTMLCalls.push(c.t || c.d || "");
  var t = c.t || c.d || "";
  var m = c.metrics || {};
  function fmtPct(x) {
    if (x == null || x === "") return "—";
    var n = Number(x);
    if (!isFinite(n)) return "—";
    return (n * 100).toFixed(1) + "%";
  }
  function fmtN(x) {
    if (x == null || x === "") return "—";
    return String(x);
  }
  var score = c.score != null ? c.score : "";
  return '<article class="card" data-t="'+t+'">' +
    '<header><h2>'+t+'</h2><span class="sc">'+score+'</span>' +
    '<span class="status">Weak / fading</span>' +
    '<span class="streak">'+(c.mom_streak_label || "")+'</span></header>' +
    '<div class="trend">Trending down</div>' +
    '<div class="digest"><div>MA FAN</div><div>CLOSE HI</div><div>52W HI</div><div>HM/HL</div>' +
    '<div>V.EMA</div><div>ABOVE 50</div><div>ABOVE 200</div><div>MOM+</div>' +
    '<div>TREND↑</div><div>SQUEEZE</div><div>RS+</div><div>BREAKOUT</div>' +
    '<div>OPT SPIKE</div></div>' +
    '<div class="stats"><span>R20 '+fmtPct(m.r20_pct)+'</span><span>RS63 '+fmtN(m.rs_63)+'</span><span>ATR% '+fmtPct(m.atr_pct)+'</span></div>' +
    '<p class="blurb">Trending down. below key MAs. Score '+score+'/12.</p>' +
    '</article>';
}
</script>
</body></html>"""
        html = cr.ensure_embedded(live)
        html = bo.ensure_embedded(
            html,
            {
                "breakout": [
                    {
                        "t": "SPCX",
                        "ticker": "SPCX US Equity",
                        "score": 9,
                        "delta": 2,
                        "lookback": 7,
                        "label": "↑3d>5",
                        "side": "above",
                        "why": "band 9",
                        "r20": None,
                        "rs63": None,
                        "atr_pct": None,
                        "card": None,
                    }
                ],
                "breakdown": [],
            },
        )
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
const window = dom.window;
const called = [];
const views = [];
window.selectTicker = function (t) {{ called.push("sel:" + String(t)); }};
const origShow = window.__FD_BB_SHOW__;
window.__FD_BB_SHOW__ = function (kind) {{ views.push(String(kind)); if (origShow) return origShow.apply(this, arguments); }};
const pane = window.document.getElementById("view-breakout");
if (pane) {{ pane.classList.remove("hide"); pane.setAttribute("data-view", "breakout"); }}
const grid = window.document.getElementById("breakout-grid");
const row = JSON.parse(window.document.getElementById("fd-breakout-db").textContent).breakout[0];
if (!row.metrics || row.metrics.r20_pct == null) {{
  console.log(JSON.stringify({{error:"db missing metrics", row: row}}));
  process.exit(3);
}}
const node = window.__FD_BB_RENDER_ROW__(row);
if (!node) {{ console.log(JSON.stringify({{error:"no node"}})); process.exit(2); }}
if (grid) grid.appendChild(node);
else if (pane) pane.appendChild(node);
else window.document.body.appendChild(node);
node.click();
const report = {{
  called: called,
  cardHTMLCalls: window.__cardHTMLCalls || [],
  views: views,
  flags: {{ cardhtml: !!window.__FD_BB_CARDHTML__, portable: !!window.__FD_BB_PORTABLE_FIX__, noCard: !!window.__FD_BB_NO_CARDHTML_FALLBACK__ }},
  dense: node.getAttribute("data-fd-bb-dense"),
  dataT: node.getAttribute("data-t"),
  text: (node.innerText || node.textContent || "").replace(/\\s+/g, " ").trim(),
  html: node.outerHTML
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
                timeout=20,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
            report = json.loads(proc.stdout.strip().splitlines()[-1])
            self.assertEqual(report["called"], ["sel:SPCX"], report)
            self.assertIn("SPCX", report.get("cardHTMLCalls") or [], report)
            self.assertNotIn("breakdown", report.get("views") or [])
            self.assertTrue(report["flags"]["cardhtml"], report)
            self.assertFalse(report["flags"]["portable"])
            self.assertFalse(report["flags"]["noCard"])
            self.assertNotEqual(report.get("dense"), "1")
            self.assertEqual(report.get("dataT"), "SPCX")
            text = report["text"]
            self.assertIn("SPCX", text)
            self.assertIn("Weak / fading", text)
            self.assertIn("Trending down", text)
            self.assertIn("CLOSE HI", text)
            self.assertIn("SQUEEZE", text)
            self.assertIn("V.EMA", text)
            self.assertIn("OPT SPIKE", text)
            self.assertIn("below key MAs", text)
            self.assertIn("R20 ", text)
            self.assertIn("12.8%", text)
            self.assertIn("ATR%", text)
            self.assertIn("2.3%", text)
            self.assertNotIn("230%", text)
            self.assertNotIn("203%", text)

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
            self.assertIn("__FD_RENDER_CARD__", text)
            self.assertIn("__FD_RENDER_ROW__", text)
            self.assertIn('id="fd-card-css"', text)
            self.assertIn('id="fd-card-js"', text)
            self.assertIn("window.MOM", text)
            self.assertIn("mom.cards", text)
            self.assertIn("selectTicker", text)
            self.assertNotRegex(text, r'<article\b[^>]*fd-bb-card')
            self.assertNotIn('id="fd-book-delta"', text)
            self.assertNotIn("baseline set", text)
            self.assertNotIn('id="gics-filter-strip"', text)
            self.assertIn('id="fd-hitch-db"', text)
            self.assertNotIn('id="fd-paper-marks"', text)
            self.assertNotIn('id="fd-paper-js"', text)
            self.assertNotIn('id="fd-nav-paper"', text)
            self.assertNotIn('id="view-paper"', text)
            self.assertIn('id="view-experimental"', text)
            self.assertNotIn('data-tab="sectors"', text)
            bb_js = re.search(
                r'<script\b[^>]*id=["\']fd-breakout-js["\'][^>]*>(.*?)</script>',
                text,
                re.I | re.S,
            )
            self.assertIsNotNone(bb_js)
            self.assertIn("cardHTML", bb_js.group(1))
            self.assertIn("callCardHTML", bb_js.group(1))
            self.assertNotIn("denseHTML", bb_js.group(1))
            self.assertTrue((root / book_delta.SNAPSHOT_FILENAME).is_file())
            snap = json.loads((root / book_delta.SNAPSHOT_FILENAME).read_text(encoding="utf-8"))
            self.assertIn("names", snap)


class PaperNavIgnoreTests(unittest.TestCase):
    def test_native_views_omit_paper_and_kindof_returns_empty(self) -> None:
        self.assertNotIn("view-paper", bo.NATIVE_VIEW_IDS)
        js = bo.strip_js()
        self.assertIn("__FD_BB_IGNORE_PAPER__", js)
        self.assertIn('window.__FD_BB_IGNORE_PAPER__ = true', js)
        kind = js.split("function kindOf")[1].split("window.__FD_BB_KIND_OF__")[0]
        self.assertIn('view === "paper"', kind)
        self.assertIn('btn.id === "fd-nav-paper"', kind)
        self.assertIn('data-fd-paper-nav', kind)
        paper_return = kind.split('view === "paper"')[1].split("if (view ===")[0]
        self.assertIn('return ""', paper_return)
        self.assertNotIn('return "other"', paper_return)
        self.assertIn('kind === "paper"', js)
        self.assertIn("return orig.apply(this, arguments)", js)


if __name__ == "__main__":
    unittest.main()
