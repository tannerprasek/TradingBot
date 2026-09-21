"""Add-to-book lands extras and residual-only names on the desk. No Bloomberg."""

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


if __name__ == "__main__":
    unittest.main()
