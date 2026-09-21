"""Portfolio upload — parse, gross weights, mom, missing names. No live Bloomberg."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import add_server  # noqa: E402
import desk_dash  # noqa: E402
import portfolio as pf  # noqa: E402


CSV = """position,ticker
100,AAPL
-40,MSFT US Equity
25,GOOG
"""


def _desk(tmp: Path, *, extra_universe: bool = True) -> dict:
    names = {
        "AAPL US Equity": {
            "ticker": "AAPL US Equity",
            "t": "AAPL",
            "in_universe": True,
            "px_last": 200.0,
            "px_source": "fixture",
            "mom_score": 10,
            "mom_source": "mom_score",
            "metrics": {"mom_score": 10, "r20": 0.08, "rs63": 1.2, "atr_pct": 2.5, "beta": 1.1},
            "pills": ["SI"],
            "gics": "Information Technology",
            "factor": "F1",
            "loadings": [0.5, 0.1],
        },
        "MSFT US Equity": {
            "ticker": "MSFT US Equity",
            "t": "MSFT",
            "in_universe": True,
            "px_last": 400.0,
            "px_source": "fixture",
            "mom_score": 4,
            "mom_source": "mom_score",
            "metrics": {"mom_score": 4, "r20": -0.02, "rs63": 0.8, "atr_pct": 1.8, "beta": 0.9},
            "pills": ["IV↑"],
            "gics": "Information Technology",
            "factor": "F1",
            "loadings": [0.4, -0.2],
        },
        "XOM US Equity": {
            "ticker": "XOM US Equity",
            "t": "XOM",
            "in_universe": True,
            "px_last": 100.0,
            "px_source": "fixture",
            "mom_score": 2,
            "mom_source": "mom_score",
            "metrics": {"mom_score": 2, "r20": -0.10, "rs63": 0.4, "atr_pct": 3.0, "beta": 0.7},
            "pills": [],
            "gics": "Energy",
            "factor": "F2",
            "loadings": [-0.1, 0.8],
        },
    }
    if extra_universe:
        names["NVDA US Equity"] = {
            "ticker": "NVDA US Equity",
            "t": "NVDA",
            "in_universe": True,
            "px_last": 900.0,
            "px_source": "fixture",
            "mom_score": 12,
            "mom_source": "mom_score",
            "metrics": {"mom_score": 12, "r20": 0.20, "beta": 1.6},
            "pills": ["EVT≤5d"],
            "gics": "Information Technology",
            "factor": "F1",
            "loadings": [0.8, 0.0],
        }
    book = {
        "asof": "test",
        "names": names,
        "universe": list(names),
        "universe_n": len(names),
        "factor_names": ["F1", "F2"],
        "loadings_source": "fixture",
        "weight_def": pf.WEIGHT_DEF,
    }
    (tmp / "dapi_enrichment.json").write_text(
        json.dumps(
            {
                "asof": "test",
                "names": {
                    k: {
                        "px_last": v["px_last"],
                        "beta": v["metrics"].get("beta"),
                        "gics_sector_name": v.get("gics"),
                    }
                    for k, v in names.items()
                },
            }
        ),
        encoding="utf-8",
    )
    return book


class ParseTests(unittest.TestCase):
    def test_csv_header_and_bare_ticker(self) -> None:
        rows = pf.parse_table(CSV)
        by = {r["ticker"]: r["position"] for r in rows}
        self.assertEqual(by["AAPL US Equity"], 100)
        self.assertEqual(by["MSFT US Equity"], -40)
        self.assertEqual(by["GOOG US Equity"], 25)

    def test_paste_without_header(self) -> None:
        rows = pf.parse_table("100\tAAPL\n-50\tMSFT")
        by = {r["ticker"]: r["position"] for r in rows}
        self.assertEqual(by["AAPL US Equity"], 100)
        self.assertEqual(by["MSFT US Equity"], -50)

    def test_duplicate_tickers_sum(self) -> None:
        rows = pf.parse_table("position,ticker\n10,AAPL\n15,AAPL US Equity\n-5,AAPL")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["position"], 20)

    def test_paren_short_and_aliases(self) -> None:
        rows = pf.parse_table("shares,symbol\n(40),MSFT")
        self.assertEqual(rows[0]["ticker"], "MSFT US Equity")
        self.assertEqual(rows[0]["position"], -40)

    def test_empty_raises(self) -> None:
        with self.assertRaises(pf.ParseError):
            pf.parse_table("")

    def test_missing_columns_raises(self) -> None:
        with self.assertRaises(pf.ParseError):
            pf.parse_table("foo,bar\n1,2")


class WeightTests(unittest.TestCase):
    def test_gross_weights_long_short(self) -> None:
        rows = [
            {"ticker": "AAPL US Equity", "position": 100},
            {"ticker": "MSFT US Equity", "position": -40},
        ]
        quotes = {
            "AAPL US Equity": {"px_last": 200.0, "source": "t"},
            "MSFT US Equity": {"px_last": 400.0, "source": "t"},
        }
        priced, exp, unpriced = pf.apply_weights(rows, quotes)
        # notional: +20000 and -16000, gross=36000, net=4000
        self.assertEqual(unpriced, [])
        self.assertEqual(exp["gross"], 36000)
        self.assertEqual(exp["net"], 4000)
        self.assertAlmostEqual(exp["net_gross"], 4000 / 36000)
        by = {r["ticker"]: r for r in priced}
        self.assertAlmostEqual(by["AAPL US Equity"]["weight"], 20000 / 36000)
        self.assertAlmostEqual(by["MSFT US Equity"]["weight"], -16000 / 36000)
        self.assertAlmostEqual(sum(abs(r["weight"]) for r in priced), 1.0)

    def test_unpriced_out_of_denominator(self) -> None:
        rows = [
            {"ticker": "AAPL US Equity", "position": 10},
            {"ticker": "ZZZ US Equity", "position": 99},
        ]
        quotes = {
            "AAPL US Equity": {"px_last": 100.0, "source": "t"},
            "ZZZ US Equity": {"px_last": None, "source": None},
        }
        priced, exp, unpriced = pf.apply_weights(rows, quotes)
        self.assertEqual(unpriced, ["ZZZ US Equity"])
        self.assertEqual(exp["gross"], 1000)
        self.assertEqual(priced[0]["weight"], 1.0)
        self.assertEqual(priced[1]["weight"], 0.0)


class MomAndMissingTests(unittest.TestCase):
    def test_weighted_mom_and_missing_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            desk = _desk(root)
            rows = pf.parse_table(CSV)
            quotes = {
                "AAPL US Equity": {"px_last": 200.0, "source": "t"},
                "MSFT US Equity": {"px_last": 400.0, "source": "t"},
                "GOOG US Equity": {"px_last": 150.0, "source": "dapi"},
            }
            card = pf.score_portfolio(
                rows,
                root=root,
                desk=desk,
                quotes=quotes,
                allow_dapi=False,
                persist=True,
            )
            self.assertTrue(card["ok"])
            missing = {m["ticker"] for m in card["missing"]}
            self.assertIn("GOOG US Equity", missing)
            goog = next(r for r in card["rows"] if r["ticker"] == "GOOG US Equity")
            self.assertFalse(goog["in_universe"])
            self.assertIsNone(goog["mom_score"])
            self.assertIsNotNone(goog["notional"])  # priced anyway

            aapl_w = next(r["weight"] for r in card["rows"] if r["t"] == "AAPL")
            msft_w = next(r["weight"] for r in card["rows"] if r["t"] == "MSFT")
            goog_w = next(r["weight"] for r in card["rows"] if r["t"] == "GOOG")
            # GOOG has no mom → coverage is |aapl|+|msft|
            covered = abs(aapl_w) + abs(msft_w)
            expected = (aapl_w * 10 + msft_w * 4) / covered
            self.assertAlmostEqual(card["mom"]["score"], expected)
            self.assertAlmostEqual(card["mom"]["coverage"], covered)
            self.assertGreater(card["mom"]["missing_gross"], 0)
            self.assertNotIn("score_v2", card["mom"]["definition"].lower()[:20] + "x")
            self.assertIn("score_v2", card["mom"]["definition"])
            # persist under gitignored runtime path (tmp has no runtime/ → root)
            self.assertTrue((root / pf.BOOK_FILENAME).is_file())
            self.assertGreater(goog_w, 0)

    def test_score_v2_is_not_mom(self) -> None:
        rec = {"score_v2": 99.0, "score": 88.0, "mom_score": 7.0}
        score, src = pf.resolve_card_score(rec)
        self.assertEqual(score, 7.0)
        self.assertEqual(src, "mom_score")
        score, src = pf.resolve_card_score({"score_v2": 99.0, "trend_score": 55})
        self.assertIsNone(score)

    def test_factor_exposure_uses_existing_loadings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            desk = _desk(root)
            rows = pf.parse_table("position,ticker\n100,AAPL\n-40,MSFT")
            quotes = {
                "AAPL US Equity": {"px_last": 200.0, "source": "t"},
                "MSFT US Equity": {"px_last": 400.0, "source": "t"},
            }
            card = pf.score_portfolio(
                rows, root=root, desk=desk, quotes=quotes, allow_dapi=False, persist=False
            )
            by = {f["name"]: f["exposure"] for f in card["factors"]["factors"]}
            aapl_w = 20000 / 36000
            msft_w = -16000 / 36000
            self.assertAlmostEqual(by["F1"], aapl_w * 0.5 + msft_w * 0.4)
            self.assertEqual(card["factors"]["source"], "fixture")
            self.assertIn("not a new fit", card["factors"]["model"])


class EmbedTests(unittest.TestCase):
    def test_ensure_embedded_injects_tab(self) -> None:
        html = """<!DOCTYPE html><html><head><title>Factor Desk</title></head>
<body><nav><button class="btn nav-btn" data-view="options">Options</button></nav>
<script>
function setView(v) {
  if (!/^(home|mom-up|mom-down|outliers|options)$/.test(v)) v = "home";
}
function hideAllPanes() {}
</script></body></html>"""
        out = pf.ensure_embedded(html, scorecard=None, desk={"names": {}})
        self.assertIn('data-view="portfolio"', out)
        self.assertIn('id="view-portfolio"', out)
        self.assertIn("fd-portfolio-js", out)
        self.assertIn("|portfolio", out)
        self.assertIn("look-here scorecard", out)
        again = pf.ensure_embedded(out, None, None)
        self.assertEqual(again.count('id="fd-nav-portfolio"'), 1)
        self.assertEqual(again.count('id="view-portfolio"'), 1)

    def test_write_combined_patches_live_html(self) -> None:
        live = (
            "<!DOCTYPE html><html><head><title>live</title></head><body>"
            + ("Refresh Momentum Up Momentum Down Outliers Options " * 3)
            + '<nav id="topnav"><button class="nav-btn" data-view="options">Options</button></nav>'
            + "<div id='home'>FLAGS</div>"
            + "</body></html>"
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dest = root / "factorbook.html"
            dest.write_text(live, encoding="utf-8")
            wrote = desk_dash.write_combined(dest, root=root, book={"names": {}, "meta": {}})
            text = wrote.read_text(encoding="utf-8")
            self.assertIn("FLAGS", text)
            self.assertIn("Portfolio", text)
            self.assertIn("view-portfolio", text)
            self.assertNotIn("skinny", text.lower())


class HttpTests(unittest.TestCase):
    def test_quote_and_run_endpoints(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _desk(root)
            enrich = json.loads((root / "dapi_enrichment.json").read_text(encoding="utf-8"))
            enrich["names"]["AAPL US Equity"]["PX_LAST"] = 200
            # quote_last reads px_last via load_desk_book → enrichment
            httpd = ThreadingHTTPServer(("127.0.0.1", 0), add_server.DeskHandler)
            port = httpd.server_address[1]
            t = Thread(target=httpd.serve_forever, daemon=True)
            t.start()
            old = dict(**{k: None for k in ()})
            try:
                import os

                os.environ["FACTOR_DESK_ROOT"] = str(root)
                with urlopen(f"http://127.0.0.1:{port}/api/quote/AAPL", timeout=5) as resp:
                    quote = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(quote["ticker"], "AAPL US Equity")
                self.assertEqual(quote["px_last"], 200.0)

                body = json.dumps({"csv": CSV, "allow_dapi": False}).encode("utf-8")
                req = Request(
                    f"http://127.0.0.1:{port}/api/portfolio",
                    data=body,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(req, timeout=5) as resp:
                    card = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(card["ok"])
                self.assertTrue(any(m["t"] == "GOOG" for m in card["missing"]))
                self.assertTrue((root / pf.BOOK_FILENAME).is_file())
            finally:
                httpd.shutdown()
                import os

                os.environ.pop("FACTOR_DESK_ROOT", None)
                _ = old

    def test_bad_csv_is_400(self) -> None:
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), add_server.DeskHandler)
        port = httpd.server_address[1]
        t = Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        try:
            req = Request(
                f"http://127.0.0.1:{port}/api/portfolio",
                data=json.dumps({"csv": ""}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with self.assertRaises(HTTPError) as ctx:
                urlopen(req, timeout=5)
            self.assertEqual(ctx.exception.code, 400)
        finally:
            httpd.shutdown()


class RuntimePathTests(unittest.TestCase):
    def test_runtime_folder_preferred(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "runtime").mkdir()
            path = pf.default_book_path(root)
            self.assertEqual(path, root / "runtime" / pf.BOOK_FILENAME)


if __name__ == "__main__":
    unittest.main()
