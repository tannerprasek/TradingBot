"""Unit tests for Factor Desk DAPI enrich — no live Bloomberg."""

from __future__ import annotations

import json
import math
import sys
import tempfile
import unittest
from datetime import date, timedelta
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.request import Request, urlopen

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import add_server  # noqa: E402
import dapi_enrich as de  # noqa: E402
import desk_dash  # noqa: E402
import momentum_screen  # noqa: E402
import pull_options_pulse as pulse  # noqa: E402
import write_dash  # noqa: E402


class CapacityOnce(de.RefDataSession):
    def __init__(self) -> None:
        self.n = 0

    def refdata(self, tickers, fields):
        self.n += 1
        if self.n >= 2:
            raise de.CapacityError("BLOOMBERG_LIMIT")
        return {t: {"EQY_BETA": 1.1} for t in tickers}


class FirstSuccessTests(unittest.TestCase):
    def test_first_alias_wins(self) -> None:
        raw = {"VOLATILITY_20D": 18.0, "VOLATILITY_30D": 22.0}
        value, field, reason = de.first_success(raw, "vol_30d")
        self.assertEqual(value, 22.0)
        self.assertEqual(field, "VOLATILITY_30D")
        self.assertIsNone(reason)

    def test_fallback_when_preferred_na(self) -> None:
        raw = {"VOLATILITY_30D": "#N/A Field Not Applicable", "VOLATILITY_20D": 19.5}
        value, field, reason = de.first_success(raw, "vol_30d")
        self.assertEqual(value, 19.5)
        self.assertEqual(field, "VOLATILITY_20D")
        self.assertIsNone(reason)

    def test_all_missing_is_null_with_reason(self) -> None:
        value, field, reason = de.first_success({}, "borrow_fee")
        self.assertIsNone(value)
        self.assertIsNone(field)
        self.assertEqual(reason, "no_candidate_resolved")

    def test_present_but_invalid(self) -> None:
        raw = {"INDICATIVE_FEE": "#N/A", "EQY_LEND_RATE": "N.A."}
        value, field, reason = de.first_success(raw, "borrow_fee")
        self.assertIsNone(value)
        self.assertEqual(reason, "candidates_present_but_invalid")

    def test_never_invents_from_null_session(self) -> None:
        book = de.enrich_book(["AAPL"], de.NullSession(), write=False)
        rec = book["names"]["AAPL US Equity"]
        for key in ("si_ratio", "beta", "credit", "vol_30d", "cds_oas", "borrow_fee"):
            self.assertIsNone(rec[key], key)
        self.assertTrue(rec["null_reasons"])


class ChipTests(unittest.TestCase):
    def test_si_crowded_and_light(self) -> None:
        self.assertEqual(de.classify_si(9.0, None), "crowded")
        self.assertEqual(de.classify_si(0.4, None), "light")
        self.assertEqual(de.classify_si(None, 20.0), "crowded")
        self.assertEqual(de.classify_si(None, 1.0), "light")
        self.assertIsNone(de.classify_si(3.0, 8.0))

    def test_iv_rich_cheap_need_both(self) -> None:
        self.assertEqual(de.classify_vol_regime(20.0, 26.0), "iv_rich")
        self.assertEqual(de.classify_vol_regime(20.0, 14.0), "iv_cheap")
        self.assertEqual(de.classify_vol_regime(20.0, 20.0), "neutral")
        self.assertIsNone(de.classify_vol_regime(20.0, None))
        self.assertIsNone(de.classify_vol_regime(None, 20.0))

    def test_illiquid_thresholds(self) -> None:
        self.assertEqual(de.classify_liq(100_000, 1_000_000), "illiquid")
        self.assertEqual(de.classify_liq(5_000_000, 80_000_000), "ok")
        self.assertIsNone(de.classify_liq(None, None))

    def test_event_badges_and_past_date(self) -> None:
        asof = date(2026, 9, 14)
        days, reason = de.event_days_from(asof + timedelta(days=3), asof)
        self.assertEqual(days, 3)
        self.assertIsNone(reason)
        days, reason = de.event_days_from(asof - timedelta(days=1), asof)
        self.assertIsNone(days)
        self.assertEqual(reason, "date_in_past")

    def test_pills_labels(self) -> None:
        rec = de.build_name_record(
            "X US Equity",
            {
                "SHORT_INT_RATIO": 12.0,
                "VOLATILITY_30D": 20.0,
                "HIST_CALL_IMP_VOL": 30.0,
                "HIST_PUT_IMP_VOL": 31.0,
                "VOLUME_AVG_20D": 50_000,
                "PX_LAST": 10.0,
                "EQY_INST_PCT_SH_OUT": 90.0,
                "EARNINGS_ANNOUNCEMENT_DT": (date.today() + timedelta(days=4)).isoformat(),
                "EQY_BETA": 1.2,
                "CDS_SPREAD_5Y": 250.0,
            },
            prices_ctx={"X US Equity": {"ret_20d": 0.12}},
        )
        labels = [p["label"] for p in rec["enrich_pills"]]
        self.assertIn("SI", labels)
        self.assertIn("IV↑", labels)
        self.assertIn("ILLIQ", labels)
        self.assertIn("INST", labels)
        self.assertIn("EVT≤5d", labels)
        self.assertIn("β", labels)
        self.assertIn("CRD", labels)
        self.assertEqual(rec["watch_hint"], "equity_mom_vs_credit_stress")
        self.assertEqual(rec["liq"], "illiquid")
        self.assertAlmostEqual(rec["adv_usd"], 500_000.0)

    def test_no_credit_watch_without_oas(self) -> None:
        rec = de.build_name_record(
            "Y US Equity",
            {"EQY_BETA": 1.0},
            prices_ctx={"Y US Equity": {"ret_20d": 0.20}},
        )
        self.assertIsNone(rec["watch_hint"])
        self.assertIsNone(rec["credit"])


class ResidualAndBetaTests(unittest.TestCase):
    def test_beta_field_preference(self) -> None:
        raw = {"BETA_ADJ_OVERRIDABLE": 0.9, "EQY_BETA": 1.4}
        rec = de.build_name_record("Z US Equity", raw)
        self.assertEqual(rec["beta"], 0.9)
        self.assertEqual(rec["beta_field"], "BETA_ADJ_OVERRIDABLE")

    def test_residual_when_prices_present(self) -> None:
        rec = de.build_name_record(
            "A US Equity",
            {"EQY_BETA": 1.5},
            prices_ctx={
                "A US Equity": {"ret_20d": 0.10},
                "SPY US Equity": {"ret_20d": 0.04},
            },
        )
        self.assertAlmostEqual(rec["residual_20d"], 0.10 - 1.5 * 0.04)

    def test_beta_only_without_prices(self) -> None:
        rec = de.build_name_record("A US Equity", {"EQY_BETA": 1.5})
        self.assertEqual(rec["beta"], 1.5)
        self.assertIsNone(rec["residual_20d"])
        self.assertEqual(rec["null_reasons"]["residual_20d"], "beta_only")


class IntradayTests(unittest.TestCase):
    def test_flag_parser(self) -> None:
        self.assertTrue(de.parse_intraday_flag("1"))
        self.assertTrue(de.parse_intraday_flag("true"))
        self.assertTrue(add_server.parse_intraday({"intraday": ["1"]}, None))
        self.assertTrue(add_server.parse_intraday({}, {"intraday": 1}))
        self.assertFalse(add_server.parse_intraday({}, None))
        self.assertFalse(add_server.parse_intraday({}, {"intraday": 0}))

    def test_off_does_not_attach_session(self) -> None:
        rec = de.build_name_record(
            "A US Equity",
            {"VOLUME": 1e6, "VOLUME_AVG_20D": 2e6},
            intraday=False,
        )
        self.assertIsNone(rec["intraday"])

    def test_on_computes_session_vs_adv(self) -> None:
        rec = de.build_name_record(
            "A US Equity",
            {"VOLUME": 1e6, "VOLUME_AVG_20D": 2e6},
            intraday=True,
        )
        self.assertIsNotNone(rec["intraday"])
        self.assertAlmostEqual(rec["intraday"]["session_vs_adv"], 0.5)


class SkewAndScoreTests(unittest.TestCase):
    def test_score_v2_exact_mix(self) -> None:
        # log1p(e-1)=1 ; log(e)=1 → 0.5+0.3+0.2 = 1
        e = math.e
        contracts = [
            {"volume": e - 1, "avg20": 1.0, "oi": 1.0},
            {"volume": e - 1, "avg20": 1.0, "oi": 1.0},
            {"volume": e - 1, "avg20": 1.0, "oi": 1.0},
        ]
        s = pulse.score_v2(contracts, opt_vol=e, stock_adv=1.0, vol_oi_contract={"volume": e, "oi": 1.0})
        self.assertAlmostEqual(s, 1.0)

    def test_score_v2_null_when_empty(self) -> None:
        self.assertIsNone(pulse.score_v2([], None, None))

    def test_skew_25d_from_delta(self) -> None:
        chain = [
            {"cp": "C", "delta": 0.25, "iv": 20.0},
            {"cp": "P", "delta": -0.25, "iv": 25.0},
        ]
        skew = de.summarize_skew(chain)
        self.assertAlmostEqual(skew["skew_25d_proxy"], 5.0)
        self.assertIsNone(skew["null_reason"])

    def test_skew_null_without_iv(self) -> None:
        skew = de.summarize_skew([{"cp": "C", "delta": 0.25, "volume": 10}])
        self.assertIsNone(skew["skew_25d_proxy"])
        self.assertEqual(skew["null_reason"], "no_iv_on_chain")

    def test_attach_skew_does_not_change_score(self) -> None:
        contracts = [{"volume": 10, "avg20": 5, "oi": 2, "iv": 30, "cp": "C"}]
        rec = pulse.pulse_name("AAPL US Equity", contracts, opt_vol=100, stock_adv=50)
        score_only = pulse.score_v2(contracts, 100, 50)
        self.assertEqual(rec["score_v2"], score_only)
        self.assertIn("skew_25d_proxy", rec)


class EnrichBookTests(unittest.TestCase):
    def test_writes_json_and_one_field_fail_does_not_kill(self) -> None:
        table = {
            "AAPL US Equity": {
                "EQY_BETA": 1.2,
                "SHORT_INT_RATIO": "#N/A",
                "VOLATILITY_30D": 18.0,
            },
            "MSFT US Equity": {
                "EQY_BETA": 0.95,
                "VOLUME_AVG_20D": 20_000_000,
                "PX_LAST": 400.0,
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            book = de.enrich_book(
                ["AAPL", "MSFT"],
                de.DictSession(table),
                out_path=root / "dapi_enrichment.json",
                root=root,
            )
            self.assertTrue((root / "dapi_enrichment.json").is_file())
            self.assertIsNone(book["names"]["AAPL US Equity"]["si_ratio"])
            self.assertEqual(book["names"]["AAPL US Equity"]["beta"], 1.2)
            self.assertEqual(book["names"]["MSFT US Equity"]["liq"], "ok")
            self.assertIn("EQY_BETA", book["meta"]["fields_resolved"])

    def test_capacity_keeps_first_chunk(self) -> None:
        tickers = [f"T{i:02d}" for i in range(30)]
        book = de.enrich_book(tickers, CapacityOnce(), chunk_size=10, write=False)
        self.assertTrue(book["meta"]["capacity_skipped"])
        resolved = [t for t, rec in book["names"].items() if rec.get("beta") is not None]
        self.assertGreaterEqual(len(resolved), 1)
        self.assertLess(len(resolved), 30)

    def test_refresh_without_enrich_file_still_renders(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            html = desk_dash.render_html(cards=[], book=None)
            self.assertIn("No enrichment file", html)
            dest = desk_dash.assemble_and_write(root / "factorbook.html", root=root, book=None)
            self.assertTrue(dest.is_file())
            self.assertIsNone(desk_dash.load_enrichment(root))


class CardHookTests(unittest.TestCase):
    def test_chg_pct_1d_maps_into_day_when_empty(self) -> None:
        rec = de.build_name_record("MSTR US Equity", {"CHG_PCT_1D": -0.8, "PX_LAST": 300.0})
        self.assertEqual(rec["fields_used"]["chg_pct_1d"], "CHG_PCT_1D")
        self.assertAlmostEqual(rec["chg_pct_1d"], -0.8)
        self.assertAlmostEqual(rec["day"], -0.008)
        self.assertAlmostEqual(rec["ret_1d"], -0.008)
        card: dict = {"ticker": "MSTR US Equity", "metrics": {"r20_pct": 0.1}}
        de.attach_card_fields(card, rec)
        self.assertAlmostEqual(card["day"], -0.008)
        self.assertAlmostEqual(card["ret_1d"], -0.008)
        self.assertAlmostEqual(card["metrics"]["day_pct"], -0.008)
        self.assertEqual(card["metrics"]["r20_pct"], 0.1)
        kept = {"day": 0.02, "ret_1d": 0.02, "metrics": {"day_pct": 0.02, "r20_pct": 0.1}}
        de.attach_card_fields(kept, rec)
        self.assertEqual(kept["day"], 0.02)
        self.assertEqual(kept["ret_1d"], 0.02)
        self.assertEqual(kept["metrics"]["day_pct"], 0.02)
        self.assertEqual(kept["metrics"]["r20_pct"], 0.1)
        missing = de.build_name_record("ZZ US Equity", {"PX_LAST": 1.0})
        self.assertIsNone(missing["chg_pct_1d"])
        self.assertIsNone(missing["day"])
        self.assertEqual(missing["null_reasons"]["chg_pct_1d"], "no_candidate_resolved")

    def test_attach_fields(self) -> None:
        rec = de.build_name_record("A US Equity", {"EQY_BETA": 1.1, "EQY_INST_PCT_SH_OUT": 90})
        card: dict = {"ticker": "A US Equity"}
        desk_dash.attach_enrichment(card, rec)
        self.assertEqual(card["beta"], 1.1)
        self.assertEqual(card["inst_pct"], 90)
        self.assertTrue(any(p["label"] == "INST" for p in card["enrich_pills"]))
        empty: dict = {"ticker": "MISSING"}
        desk_dash.attach_enrichment(empty, None)
        self.assertEqual(empty["enrich_pills"], [])
        self.assertIsNone(empty["beta"])

    def test_momentum_attach(self) -> None:
        book = {
            "names": {
                "A US Equity": de.build_name_record("A US Equity", {"EQY_BETA": 0.8}),
            }
        }
        rows = momentum_screen.screen([{"ticker": "A US Equity", "ret_20d": 0.02}], book)
        self.assertEqual(rows[0]["beta"], 0.8)
        self.assertEqual(rows[0]["beta_field"], "EQY_BETA")


class RefreshHookTests(unittest.TestCase):
    def test_run_refresh_writes_and_rebuilds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = add_server.run_refresh(tickers=["AAPL"], intraday=False, root=root)
            self.assertTrue(result["ok"])
            self.assertFalse(result["intraday"])
            self.assertTrue((root / "dapi_enrichment.json").is_file())
            data = json.loads((root / "dapi_enrichment.json").read_text(encoding="utf-8"))
            self.assertIn("AAPL US Equity", data["names"])
            self.assertTrue((root / "factorbook.html").is_file())

    def test_intraday_refresh_flag_in_meta(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            add_server.run_dapi_enrich_stage(["MSFT"], intraday=True, root=root)
            data = json.loads((root / "dapi_enrichment.json").read_text(encoding="utf-8"))
            self.assertTrue(data["meta"]["intraday"])
            self.assertIn("intraday", data["names"]["MSFT US Equity"])

    def test_http_refresh_accepts_intraday(self) -> None:
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), add_server.DeskHandler)
        port = httpd.server_address[1]
        t = Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        try:
            with urlopen(f"http://127.0.0.1:{port}/health", timeout=5) as resp:
                health = json.loads(resp.read().decode("utf-8"))
            self.assertTrue(health["ok"])
            with urlopen(f"http://127.0.0.1:{port}/refresh?intraday=1&tickers=AAPL", timeout=5) as resp:
                self.assertEqual(resp.status, 202)
                body = json.loads(resp.read().decode("utf-8"))
            self.assertTrue(body["intraday"])
            self.assertFalse(body["options"])
            self.assertEqual(body["kind"], "refresh")
            deadline = time.time() + 8
            while time.time() < deadline:
                with add_server._STATE_LOCK:
                    if not add_server._STATE["busy"]:
                        break
                time.sleep(0.05)
        finally:
            httpd.shutdown()

    def test_run_refresh_skips_options_pulse(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = add_server.run_refresh(tickers=["AAPL"], intraday=False, root=root)
            self.assertTrue(result["ok"])
            self.assertFalse(result["options"])
            self.assertEqual(result["kind"], "refresh")
            self.assertIsNone(result["opt_err"])
            self.assertFalse((root / "options_abnormal.json").is_file())
            self.assertTrue((root / "dapi_enrichment.json").is_file())

    def test_run_options_refresh_writes_abnormal_and_rebuilds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = add_server.run_options_refresh(tickers=["AAPL"], root=root)
            self.assertTrue(result["ok"])
            self.assertTrue(result["options"])
            self.assertEqual(result["kind"], "options")
            self.assertTrue((root / "options_abnormal.json").is_file())
            self.assertTrue((root / "factorbook.html").is_file())
            html = (root / "factorbook.html").read_text(encoding="utf-8")
            self.assertIn("Options Refresh", html)
            self.assertIn('id="sidecar-progress"', html)
            data = json.loads((root / "options_abnormal.json").read_text(encoding="utf-8"))
            self.assertIn("AAPL US Equity", data["names"])

    def test_http_options_refresh_aliases(self) -> None:
        add_server._STATE.update(
            {
                "busy": False,
                "pct": 0,
                "stage": "idle",
                "error": None,
                "last": None,
                "intraday": False,
                "options": False,
                "kind": "idle",
            }
        )
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), add_server.DeskHandler)
        port = httpd.server_address[1]
        t = Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        try:
            with urlopen(f"http://127.0.0.1:{port}/options-refresh?tickers=AAPL", timeout=5) as resp:
                self.assertEqual(resp.status, 202)
                body = json.loads(resp.read().decode("utf-8"))
            self.assertTrue(body["options"])
            self.assertEqual(body["kind"], "options")

            deadline = time.time() + 8
            busy = True
            while time.time() < deadline:
                with add_server._STATE_LOCK:
                    busy = add_server._STATE["busy"]
                if not busy:
                    break
                time.sleep(0.05)
            self.assertFalse(busy)

            req = Request(
                f"http://127.0.0.1:{port}/api/refresh_live",
                data=json.dumps({"options": 1, "tickers": ["AAPL"]}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(req, timeout=5) as resp:
                self.assertEqual(resp.status, 202)
                body = json.loads(resp.read().decode("utf-8"))
            self.assertTrue(body["options"])
            self.assertEqual(body["kind"], "options")

            deadline = time.time() + 8
            while time.time() < deadline:
                with add_server._STATE_LOCK:
                    busy = add_server._STATE["busy"]
                if not busy:
                    break
                time.sleep(0.05)

            req = Request(
                f"http://127.0.0.1:{port}/refresh",
                data=json.dumps({"options": 0, "intraday": 0, "tickers": ["AAPL"]}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(req, timeout=5) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            self.assertFalse(body["options"])
            self.assertEqual(body["kind"], "refresh")
        finally:
            httpd.shutdown()


class ParseFlagTests(unittest.TestCase):
    def test_parse_options_default_off(self) -> None:
        self.assertFalse(add_server.parse_options({}, None))
        self.assertFalse(add_server.parse_options({}, {"options": 0}))
        self.assertTrue(add_server.parse_options({"options": ["1"]}, None))
        self.assertTrue(add_server.parse_options({}, {"options": 1}))
        self.assertTrue(add_server.parse_options({}, {"options": "yes"}))


class HtmlPillsTests(unittest.TestCase):
    def test_pills_markup(self) -> None:
        rec = de.build_name_record(
            "DEMO US Equity",
            {
                "SHORT_INT_RATIO": 0.5,
                "VOLATILITY_30D": 25.0,
                "IVOL_MID": 15.0,
                "VOLUME_AVG_20D": 80_000,
                "PX_LAST": 8.0,
                "EQY_INST_PCT_SH_OUT": 88.0,
                "EARNINGS_ANNOUNCEMENT_DT": (date.today() + timedelta(days=12)).isoformat(),
                "BETA_ADJ_OVERRIDABLE": 1.05,
            },
        )
        html = desk_dash.render_html(book={"asof": "test", "names": {"DEMO US Equity": rec}, "meta": {}})
        self.assertIn('class="badge spike-chip si-light"', html)
        self.assertIn("IV↓", html)
        self.assertIn("ILLIQ", html)
        self.assertIn("EVT≤20d", html)
        self.assertIn("si_ratio", html)
        path = write_dash.write  # import surface
        self.assertTrue(callable(path))


if __name__ == "__main__":
    unittest.main()
