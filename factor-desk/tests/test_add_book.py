"""Add-to-book lands extras and residual-only names on the desk. No Bloomberg."""

from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import add_server  # noqa: E402
import dapi_enrich as de  # noqa: E402
import desk_dash  # noqa: E402


def _fat_desk_html() -> str:
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
<script>window.BOOK = {"names":{"AAPL US Equity":{"t":"AAPL","ticker":"AAPL US Equity"}}};</script>
<article class="card" data-t="AAPL">AAPL live chrome</article>
</body></html>"""
    pad = 1_200_000 - len(body.encode("utf-8"))
    return body + ("<!--" + ("P" * max(pad, 1)) + "-->")


def _seed_gap(root: Path) -> None:
    """Enrich knows AAPL only. Extra + residual already have TSEM."""
    (root / "dapi_enrichment.json").write_text(
        json.dumps(
            {
                "asof": "2026-09-21",
                "names": {"AAPL US Equity": {"ticker": "AAPL US Equity", "name": "Apple"}},
                "meta": {},
            }
        ),
        encoding="utf-8",
    )
    (root / "universe_extra.txt").write_text("TSEM US Equity\n", encoding="utf-8")
    v0 = root / "v0"
    v0.mkdir()
    (v0 / "residual_last.csv").write_text(
        "date,ticker,residual\n2026-09-21,TSEM US Equity,0.02\n",
        encoding="utf-8",
    )


class BoomSession(de.RefDataSession):
    def refdata(self, tickers, fields):
        raise RuntimeError("dapi down")


class TickerMatchTests(unittest.TestCase):
    def test_yellow_key_case_and_exchange_collapse(self) -> None:
        self.assertEqual(de.canonical_ticker("AAPL US Equity"), "AAPL US Equity")
        self.assertEqual(de.canonical_ticker("aapl"), "AAPL US Equity")
        self.assertEqual(de.canonical_ticker("aapl us"), "AAPL US Equity")
        self.assertEqual(de.canonical_ticker("AAPL US EQUITY"), "AAPL US Equity")
        self.assertEqual(de.canonical_ticker("aapl us equity"), "AAPL US Equity")
        self.assertEqual(de.name_key("SPX Index"), "SPX Index")
        self.assertEqual(de.name_key("SPX INDEX"), "SPX INDEX")

    def test_yellow_suffix_is_not_a_symbol(self) -> None:
        self.assertEqual(de.canonical_ticker("APFD"), "APFD US Equity")
        self.assertEqual(de.canonical_ticker("ACORP"), "ACORP US Equity")

    def test_lookup_finds_canonical_and_bare_symbol(self) -> None:
        book = {"names": {"AAPL US Equity": {"ticker": "AAPL US Equity", "name": "Apple"}}}
        self.assertEqual(de.lookup_name(book, "AAPL US EQUITY")["name"], "Apple")
        self.assertEqual(de.lookup_name(book, "aapl")["ticker"], "AAPL US Equity")

    def test_search_ranks_symbol_prefix_then_name(self) -> None:
        universe = {
            "names": {
                "AAPL US Equity": {"ticker": "AAPL US Equity", "name": "Apple", "t": "AAPL"},
                "AMGN US Equity": {"ticker": "AMGN US Equity", "name": "Amgen", "t": "AMGN"},
                "TSEM US Equity": {
                    "ticker": "TSEM US Equity",
                    "name": "Tower Semiconductor",
                    "t": "TSEM",
                },
            }
        }
        self.assertEqual([h["t"] for h in de.search_symbols("", universe)], [])
        exact = de.search_symbols("aapl us equity", universe)
        self.assertEqual([h["t"] for h in exact], ["AAPL"])
        prefix = [h["t"] for h in de.search_symbols("aa", universe)]
        self.assertEqual(prefix, ["AAPL"])
        self.assertIn("AMGN", [h["t"] for h in de.search_symbols("am", universe)])
        named = de.search_symbols("tower", universe)
        self.assertEqual([h["t"] for h in named], ["TSEM"])
        self.assertEqual(de.search_symbols("semi", universe)[0]["t"], "TSEM")
        self.assertEqual(de.search_symbols("equity", universe), [])

    def test_tsem_matches_across_exchange_spellings(self) -> None:
        """The pushed name was TSEM. US vs UW must not hide the short symbol."""
        universe = {
            "names": {
                "TSEM UW Equity": {
                    "ticker": "TSEM UW Equity",
                    "name": "Tower Semiconductor",
                    "t": "TSEM",
                }
            }
        }
        for query in ("TSEM", "tsem", "TSEM US", "TSEM US Equity", "TSEM UW Equity"):
            hits = de.search_symbols(query, universe)
            self.assertTrue(hits, msg=query)
            self.assertEqual(hits[0]["t"], "TSEM", msg=query)
        self.assertEqual(de.search_symbols("tower", universe)[0]["t"], "TSEM")


class CardUniverseTests(unittest.TestCase):
    def test_extras_and_residual_only_names_become_cards(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_gap(root)
            book = json.loads((root / "dapi_enrichment.json").read_text(encoding="utf-8"))
            cards = desk_dash.cards_from_enrichment(book, root=root)
            by_short = {c.get("t"): c for c in cards}
            self.assertIn("AAPL", by_short)
            self.assertIn("TSEM", by_short)
            self.assertEqual(by_short["TSEM"]["ticker"], "TSEM US Equity")
            self.assertTrue(by_short["TSEM"]["limited_history"])

    def test_residual_only_without_extra_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            v0 = root / "v0"
            v0.mkdir()
            (v0 / "residual_last.csv").write_text(
                "date,ticker,residual\n2026-09-21,TSEM US Equity,0.1\n",
                encoding="utf-8",
            )
            cards = desk_dash.cards_from_enrichment({"names": {}}, root=root)
            self.assertEqual([c.get("t") for c in cards], ["TSEM"])

    def test_residual_panel_and_ticker_names_are_searchable(self) -> None:
        """TSEM lives in the factor files, not in the live MOM card list."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "v0_residuals.csv").write_text(
                "date,ticker,residual\n2026-09-21,TSEM UW Equity,0.02\n",
                encoding="utf-8",
            )
            (root / "ticker_names.json").write_text(
                json.dumps(
                    {
                        "names": {
                            "TSEM US Equity": {
                                "ticker": "TSEM US Equity",
                                "name": "Tower Semiconductor",
                                "short_name": "TSEM",
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            cards = desk_dash.cards_from_enrichment({"names": {}}, root=root)
            self.assertIn("TSEM", {c.get("t") for c in cards})
            hits = desk_dash.search_desk("tsem", root=root)["hits"]
            self.assertEqual(hits[0]["t"], "TSEM")
            self.assertEqual(desk_dash.search_desk("TSEM US Equity", root=root)["hits"][0]["t"], "TSEM")

    def test_write_combined_puts_short_symbol_in_search_book(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_gap(root)
            dest = root / "factorbook.html"
            dest.write_text(_fat_desk_html(), encoding="utf-8")
            before = dest.stat().st_size
            book = json.loads((root / "dapi_enrichment.json").read_text(encoding="utf-8"))
            out = desk_dash.write_combined(dest, root=root, book=book)
            text = out.read_text(encoding="utf-8")
            self.assertGreaterEqual(out.stat().st_size, desk_dash.LIVE_MIN_BYTES)
            self.assertGreaterEqual(out.stat().st_size, before - 1024)
            self.assertIn("AAPL live chrome", text)
            self.assertIn("Refresh", text)
            self.assertNotIn("__PAYLOAD__", text)
            match = re.search(
                r'<script type="application/json" id="fd-search-book">(.*?)</script>',
                text,
                re.S,
            )
            self.assertIsNotNone(match)
            payload = json.loads(match.group(1))
            shorts = {row.get("t") for row in payload["names"].values()}
            self.assertIn("TSEM", shorts)
            self.assertIn("AAPL", shorts)
            self.assertIn('"t":"TSEM"', match.group(1))
            self.assertIn('id="fd-symbol-q"', text)
            self.assertIn("window.__FD_SEARCH__", text)
            self.assertIn("selectTicker", text)
            self.assertIn("search-pane", text)
            self.assertIn("fd-search-pane-hits", text)
            hits = desk_dash.search_desk("apple", root=root)["hits"]
            self.assertEqual(hits[0]["t"], "AAPL")
            self.assertEqual(desk_dash.search_desk("tsem", root=root)["hits"][0]["ticker"], "TSEM US Equity")


class AddPathTests(unittest.TestCase):
    def _desktop(self, tmp: str) -> tuple[Path, Path]:
        desktop = Path(tmp)
        pkg = desktop / "factorbook"
        pkg.mkdir()
        live = desktop / "factorbook.html"
        live.write_text(_fat_desk_html(), encoding="utf-8")
        (pkg / "dapi_enrichment.json").write_text(
            json.dumps({"asof": "2026-09-21", "names": {"AAPL US Equity": {"ticker": "AAPL US Equity"}}, "meta": {}}),
            encoding="utf-8",
        )
        (pkg / "prices_long.csv").write_text(
            "date,ticker,adj_close\n2026-09-18,TSEM US Equity,10\n2026-09-21,TSEM US Equity,12.5\n",
            encoding="utf-8",
        )
        return pkg, live

    def test_do_add_stubs_when_dapi_fails_and_rebuilds_html(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pkg, live = self._desktop(tmp)
            result = add_server.do_add("TSEM", root=pkg, session=BoomSession())
            self.assertTrue(result["ok"], msg=result)
            self.assertTrue(result["in_book"])
            self.assertFalse(result["enrich_ok"])
            self.assertTrue(result["stubbed"])
            self.assertIn("in the book", result["message"].lower())
            self.assertNotIn("add failed", result["message"].lower())
            self.assertIn("last price", result["message"].lower())
            book = json.loads((pkg / "dapi_enrichment.json").read_text(encoding="utf-8"))
            self.assertIn("AAPL US Equity", book["names"])
            self.assertIn("TSEM US Equity", book["names"])
            rec = book["names"]["TSEM US Equity"]
            self.assertEqual(rec.get("short_name"), "TSEM")
            self.assertEqual(rec.get("px_last"), 12.5)
            self.assertIn("TSEM", (pkg / "universe_extra.txt").read_text(encoding="utf-8"))
            names = json.loads((pkg / "ticker_names.json").read_text(encoding="utf-8"))
            self.assertIn("TSEM US Equity", names["names"])
            text = live.read_text(encoding="utf-8")
            self.assertGreaterEqual(live.stat().st_size, desk_dash.LIVE_MIN_BYTES)
            self.assertIn("TSEM", text)
            self.assertNotIn("__PAYLOAD__", text)
            self.assertIn("AAPL live chrome", text)
            match = re.search(
                r'<script type="application/json" id="fd-search-book">(.*?)</script>',
                text,
                re.S,
            )
            self.assertIsNotNone(match)
            self.assertIn("TSEM", match.group(1))

    def test_do_add_keeps_resolved_fields_when_dapi_has_them(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session = de.DictSession(
                {
                    "TSEM US Equity": {
                        "EQY_BETA": 1.2,
                        "PX_LAST": 42.5,
                        "NAME": "Tower Semiconductor",
                        "SHORT_NAME": "TSEM",
                        "GICS_SECTOR_NAME": "Information Technology",
                    }
                }
            )
            result = add_server.do_add("tsem", root=root, session=session, rebuild=False)
            self.assertTrue(result["ok"], msg=result)
            self.assertTrue(result["enrich_ok"])
            self.assertFalse(result["stubbed"])
            self.assertTrue(result["message"].startswith("Added TSEM"))
            book = json.loads((root / "dapi_enrichment.json").read_text(encoding="utf-8"))
            rec = book["names"]["TSEM US Equity"]
            self.assertEqual(rec.get("name"), "Tower Semiconductor")
            self.assertEqual(rec.get("short_name"), "TSEM")
            self.assertEqual(rec.get("gics_sector_name"), "Information Technology")
            self.assertEqual(rec.get("px_last"), 42.5)
            self.assertEqual(rec.get("beta"), 1.2)
            self.assertTrue(rec.get("enrich_pills"))

    def test_do_add_canonicalizes_mixed_case_and_false_yellow_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            equity = add_server.do_add("aapl us equity", root=root, session=BoomSession(), rebuild=False)
            preferred = add_server.do_add("APFD", root=root, session=BoomSession(), rebuild=False)
            self.assertTrue(equity["ok"], msg=equity)
            self.assertEqual(equity["ticker"], "AAPL US Equity")
            self.assertEqual(equity["short"], "AAPL")
            self.assertTrue(preferred["ok"], msg=preferred)
            self.assertEqual(preferred["ticker"], "APFD US Equity")
            book = json.loads((root / "dapi_enrichment.json").read_text(encoding="utf-8"))
            self.assertIn("AAPL US Equity", book["names"])
            self.assertNotIn("AAPL us Equity", book["names"])
            self.assertNotIn("APFD", book["names"])
            self.assertIn("APFD US Equity", book["names"])
            found = de.search_symbols("apfd", book)
            self.assertEqual(found[0]["ticker"], "APFD US Equity")

    def test_do_add_force_stubs_when_upsert_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            calls = {"n": 0}
            real = de.upsert_add_names

            def boom(*args, **kwargs):
                calls["n"] += 1
                if calls["n"] == 1:
                    raise RuntimeError("disk full mid-enrich")
                return real(*args, **kwargs)

            with mock.patch.object(add_server.dapi_enrich, "upsert_add_names", side_effect=boom):
                result = add_server.do_add("TSEM", root=root, session=BoomSession(), rebuild=False)
            self.assertTrue(result["ok"], msg=result)
            self.assertTrue(result["in_book"])
            self.assertTrue(result["searchable"])
            book = json.loads((root / "dapi_enrichment.json").read_text(encoding="utf-8"))
            self.assertIn("TSEM US Equity", book["names"])
            self.assertIn("disk full", str(result.get("enrich_error") or ""))

    def test_search_book_detects_missing_short_symbol(self) -> None:
        html = '<script type="application/json" id="fd-search-book">{"names":{"AAPL":{"t":"AAPL"}}}</script>'
        self.assertTrue(desk_dash.search_book_has_symbol(html, "AAPL"))
        self.assertFalse(desk_dash.search_book_has_symbol(html, "TSEM"))
        self.assertFalse(desk_dash.search_book_has_symbol("<html></html>", "TSEM"))

    def test_do_add_fails_when_search_book_never_lands(self) -> None:
        """Prices/extra alone used to report success; search must still fail closed."""
        with tempfile.TemporaryDirectory() as tmp:
            pkg, _live = self._desktop(tmp)
            with mock.patch.object(add_server, "run_rebuild", return_value=None), mock.patch.object(
                desk_dash, "land_search_book", side_effect=RuntimeError("land blocked")
            ), mock.patch.object(desk_dash, "search_book_has_symbol", return_value=False):
                result = add_server.do_add("TSEM", root=pkg, session=BoomSession(), rebuild=True)
            self.assertFalse(result["ok"], msg=result)
            self.assertTrue(result["in_book"])
            self.assertIs(result["searchable"], False)
            self.assertIn("search payload", result["message"].lower())
            book = json.loads((pkg / "dapi_enrichment.json").read_text(encoding="utf-8"))
            self.assertIn("TSEM US Equity", book["names"])


if __name__ == "__main__":
    unittest.main()
