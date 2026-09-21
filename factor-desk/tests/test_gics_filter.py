"""GICS sector filter chips — pure logic. No live Bloomberg."""

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

import dapi_enrich as de  # noqa: E402
import desk_dash  # noqa: E402
import gics_filter as gf  # noqa: E402
import momentum_screen  # noqa: E402


def _cards() -> list[dict]:
    return [
        {"ticker": "AAPL US Equity", "gics_sector_name": "Information Technology", "list": "FLAGS"},
        {"ticker": "JPM US Equity", "gics_sector_name": "Financials", "list": "WATCH"},
        {"ticker": "XOM US Equity", "gics_sector_name": "Energy", "list": "MOM"},
        {"ticker": "NVDA US Equity", "gics_sector_name": "Information Technology", "list": "FLAGS"},
        {"ticker": "UNKN US Equity", "gics_sector_name": None, "list": "OUTLIERS"},
    ]


class FilterLogicTests(unittest.TestCase):
    def test_all_or_empty_keeps_every_card(self) -> None:
        cards = _cards()
        self.assertEqual(gf.filter_items(cards, None), cards)
        self.assertEqual(gf.filter_items(cards, ""), cards)
        self.assertEqual(gf.filter_items(cards, "  "), cards)
        self.assertEqual(gf.filter_items(cards, gf.ALL), cards)

    def test_sector_keeps_only_that_sector(self) -> None:
        it = gf.filter_items(_cards(), "Information Technology")
        self.assertEqual([c["ticker"] for c in it], ["AAPL US Equity", "NVDA US Equity"])
        fin = gf.filter_items(_cards(), "Financials")
        self.assertEqual([c["ticker"] for c in fin], ["JPM US Equity"])

    def test_unclassified_hidden_when_sector_selected(self) -> None:
        tickers = {c["ticker"] for c in gf.filter_items(_cards(), "Energy")}
        self.assertEqual(tickers, {"XOM US Equity"})
        self.assertNotIn("UNKN US Equity", tickers)

    def test_flags_watch_mom_same_cards_just_filtered(self) -> None:
        flags = [c for c in _cards() if c["list"] == "FLAGS"]
        out = gf.filter_items(flags, "Information Technology")
        self.assertEqual(len(out), 2)
        self.assertTrue(all(c["list"] == "FLAGS" for c in out))

    def test_sectors_present_official_order_only_in_book(self) -> None:
        names = gf.sectors_present(_cards())
        self.assertEqual(names, ["Energy", "Financials", "Information Technology"])
        self.assertNotIn("Health Care", names)

    def test_sectors_from_mapping_keeps_energy_and_skips_missing_re(self) -> None:
        mapping = {
            "AAPL US Equity": "Information Technology",
            "XOM US Equity": "Energy",
            "JPM US Equity": "Financials",
            "JNJ US Equity": "Health Care",
        }
        names = gf.sectors_from_mapping(mapping)
        self.assertEqual(names[0], "Energy")
        self.assertIn("Health Care", names)
        self.assertNotIn("Real Estate", names)
        self.assertEqual(
            names,
            ["Energy", "Health Care", "Financials", "Information Technology"],
        )

    def test_code_only_rejected_no_invented_map(self) -> None:
        name, reason = de.parse_gics_sector_name("45")
        self.assertIsNone(name)
        self.assertEqual(reason, "code_only_no_name")
        name, reason = de.parse_gics_sector_name(45)
        self.assertIsNone(name)
        self.assertEqual(reason, "code_only_no_name")
        self.assertIsNone(gf.sector_of({"ticker": "X", "gics_sector_name": "40"}))

    def test_chip_labels_are_short_tags(self) -> None:
        self.assertEqual(gf.chip_label("Information Technology"), "IT")
        self.assertEqual(gf.chip_label("Financials"), "FIN")
        self.assertEqual(gf.chip_label(""), "All")
        self.assertEqual(gf.chip_label("Custom Sector"), "Custom Sector")

    def test_unknown_sector_still_filters_by_exact_name(self) -> None:
        cards = [{"ticker": "Z", "gics_sector_name": "Custom Sector"}]
        self.assertEqual(gf.filter_items(cards, "Custom Sector"), cards)
        self.assertEqual(gf.filter_items(cards, "Financials"), [])


class EnrichGicsTests(unittest.TestCase):
    def test_refresh_pack_does_not_request_gics(self) -> None:
        book = de.enrich_book(["AAPL"], de.NullSession(), write=False)
        attempted = book["meta"]["fields_attempted"]
        self.assertNotIn("GICS_SECTOR_NAME", attempted)
        self.assertNotIn("GICS_SECTOR", attempted)
        self.assertIsNone(book["names"]["AAPL US Equity"]["gics_sector_name"])

    def test_parse_when_raw_already_has_name(self) -> None:
        rec = de.build_name_record(
            "AAPL US Equity",
            {"GICS_SECTOR_NAME": "Information Technology", "EQY_BETA": 1.1},
        )
        self.assertEqual(rec["gics_sector_name"], "Information Technology")
        self.assertEqual(rec["fields_used"]["gics_sector_name"], "GICS_SECTOR_NAME")

    def test_one_shot_fill_and_cache(self) -> None:
        table = {
            "AAPL US Equity": {"GICS_SECTOR_NAME": "Information Technology"},
            "JPM US Equity": {"GICS_SECTOR_NAME": "Financials"},
            "BAD US Equity": {"GICS_SECTOR": 40},
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            seed = de.enrich_book(["AAPL", "JPM", "BAD"], de.NullSession(), root=root, write=True)
            self.assertIsNone(seed["names"]["AAPL US Equity"]["gics_sector_name"])
            filled = de.fill_gics_sectors(
                seed,
                de.DictSession(table),
                root=root,
                write=True,
            )
            self.assertEqual(filled["names"]["AAPL US Equity"]["gics_sector_name"], "Information Technology")
            self.assertEqual(filled["names"]["JPM US Equity"]["gics_sector_name"], "Financials")
            self.assertIsNone(filled["names"]["BAD US Equity"]["gics_sector_name"])
            cache = json.loads((root / de.GICS_CACHE_FILENAME).read_text(encoding="utf-8"))
            self.assertEqual(cache["names"]["AAPL US Equity"], "Information Technology")
            self.assertNotIn("BAD US Equity", cache["names"])

    def test_refresh_preserves_cached_sector(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = de.enrich_book(["MSFT"], de.NullSession(), root=root, write=True)
            first["names"]["MSFT US Equity"]["gics_sector_name"] = "Information Technology"
            de.write_enrichment(first, root / de.ENRICH_FILENAME)
            again = de.enrich_book(["MSFT"], de.NullSession(), root=root, write=True)
            self.assertEqual(again["names"]["MSFT US Equity"]["gics_sector_name"], "Information Technology")
            self.assertNotIn("GICS_SECTOR_NAME", again["meta"]["fields_attempted"])

    def test_attach_and_mom_row(self) -> None:
        rec = de.build_name_record(
            "A US Equity",
            {"EQY_BETA": 0.8, "GICS_SECTOR_NAME": "Health Care"},
        )
        card: dict = {"ticker": "A US Equity"}
        desk_dash.attach_enrichment(card, rec)
        self.assertEqual(card["gics_sector_name"], "Health Care")
        rows = momentum_screen.screen(
            [{"ticker": "A US Equity", "ret_20d": 0.02}],
            {"names": {"A US Equity": rec}},
        )
        self.assertEqual(rows[0]["gics_sector_name"], "Health Care")


class HtmlChipTests(unittest.TestCase):
    def test_strip_and_data_attr(self) -> None:
        rec_it = de.build_name_record("AAPL US Equity", {"GICS_SECTOR_NAME": "Information Technology"})
        rec_fin = de.build_name_record("JPM US Equity", {"GICS_SECTOR_NAME": "Financials"})
        html = desk_dash.render_html(
            book={
                "asof": "test",
                "names": {"AAPL US Equity": rec_it, "JPM US Equity": rec_fin},
                "meta": {},
            },
            cache={},
        )
        self.assertIn('id="gics-filter-strip"', html)
        self.assertIn('data-gics-chip="Information Technology"', html)
        self.assertIn(">IT</button>", html)
        self.assertIn(">FIN</button>", html)
        self.assertIn(">All</button>", html)
        self.assertIn('data-gics-sector="Information Technology"', html)
        self.assertIn('id="gics-sector-db"', html)
        self.assertIn("gics-hid", html)
        self.assertNotIn('data-tab="sectors"', html)
        self.assertNotIn("tab-sectors", html)
        self.assertIn(f'id="{gf.CSS_STYLE_ID}"', html)
        self.assertIn("gap: 6px", html)
        self.assertIn(">All</button>", html)

    def test_empty_host_bakes_all_and_energy_from_db(self) -> None:
        html = (
            "<html><body><nav></nav>"
            '<div id="gics-filter-strip" class="filter-strip gics-chips"></div>'
            '<article class="card" data-t="AAPL US Equity">AAPL</article>'
            "</body></html>"
        )
        mapping = {
            "XOM US Equity": "Energy",
            "AAPL US Equity": "Information Technology",
            "SHW US Equity": "Materials",
            "JPM US Equity": "Financials",
        }
        out = gf.ensure_embedded(html, mapping)
        match = re.search(
            r'<div[^>]*id="gics-filter-strip"[^>]*>(.*?)</div>',
            out,
            re.I | re.S,
        )
        self.assertIsNotNone(match)
        host = match.group(1)
        self.assertTrue(host.strip(), "empty #gics-filter-strip must be replaced with chips")
        self.assertIn(">All</button>", host)
        self.assertIn(">EN</button>", host)
        self.assertIn('data-gics-chip="Energy"', host)
        self.assertIn(">MAT</button>", host)
        self.assertIn(">IT</button>", host)
        self.assertIn(">FIN</button>", host)
        self.assertNotIn(">RE</button>", host)
        labels = re.findall(r'data-gics-chip="([^"]*)"', host)
        self.assertEqual(labels[0], "")
        self.assertEqual(labels[1], "Energy")
        self.assertEqual(out.count('id="gics-filter-strip"'), 1)
        again = gf.ensure_embedded(out, mapping)
        again_host = re.search(
            r'<div[^>]*id="gics-filter-strip"[^>]*>(.*?)</div>',
            again,
            re.I | re.S,
        )
        self.assertIsNotNone(again_host)
        self.assertIn(">EN</button>", again_host.group(1))
        self.assertIn(">All</button>", again_host.group(1))
        self.assertEqual(again.count(f'id="{gf.JS_SCRIPT_ID}"'), 1)

    def test_span_empty_host_replaced_with_filled_div(self) -> None:
        html = (
            "<html><body>"
            '<span id="gics-filter-strip" class="filter-strip gics-chips"></span>'
            "</body></html>"
        )
        out = gf.ensure_embedded(html, {"XOM US Equity": "Energy"})
        self.assertNotIn('<span id="gics-filter-strip"', out)
        self.assertIn('<div id="gics-filter-strip"', out)
        self.assertIn(">All</button>", out)
        self.assertIn(">EN</button>", out)

    def test_unique_sectors_js_map_first_then_cards(self) -> None:
        js = gf.strip_js()
        start = js.index("function uniqueSectors")
        end = js.index("function ensureStrip")
        body = js[start:end]
        self.assertIn("ORDER", body)
        map_at = body.index("Object.keys(map")
        cards_at = body.index("cardNodes")
        self.assertLess(map_at, cards_at, "have must come from map values before cards")
        self.assertIn("Never drop Energy", body)
        stale = (
            "<html><body>"
            '<div id="gics-filter-strip" class="filter-strip gics-chips"></div>'
            "<script>\n(function () {\n"
            '  var STRIP_ID = "gics-filter-strip";\n'
            "  function uniqueSectors(map) { var cards = cardNodes(); }\n"
            "  var hide = 'gics-hid';\n"
            "})();\n</script>"
            "</body></html>"
        )
        out = gf.ensure_embedded(stale, {"XOM US Equity": "Energy"})
        self.assertEqual(out.count("function uniqueSectors"), 1)
        start = out.index("function uniqueSectors")
        end = out.index("function ensureStrip")
        body = out[start:end]
        self.assertLess(body.index("Object.keys(map"), body.index("cardNodes"))
        self.assertIn(">EN</button>", out)
        self.assertIn(f'id="{gf.JS_SCRIPT_ID}"', out)

    def test_css_style_id_replaces_even_when_gchip_already_present(self) -> None:
        seed = (
            "<html><head><style>.gchip { gap: 99px; } .gics-hid { display: none; }</style>"
            "</head><body></body></html>"
        )
        first = gf.ensure_embedded(seed, {})
        self.assertIn(f'id="{gf.CSS_STYLE_ID}"', first)
        self.assertIn("gap: 6px", first)
        stale = first.replace("gap: 6px", "gap: 99px")
        again = gf.ensure_embedded(stale, {})
        self.assertEqual(again.count(f'id="{gf.CSS_STYLE_ID}"'), 1)
        self.assertIn("gap: 6px", again)
        # dedicated style id is rewritten; leftover 99px may remain in the old blob
        id_block = again.split(f'id="{gf.CSS_STYLE_ID}"', 1)[1]
        self.assertIn("gap: 6px", id_block.split("</style>", 1)[0])

    def test_refresh_html_has_no_sectors_stage_copy(self) -> None:
        html = desk_dash.render_html(cards=[], book=None)
        self.assertIn("gics-filter-strip", html)
        self.assertNotIn("early trend", html.lower())
        self.assertIn("Options Refresh", html)
        self.assertIn('id="options-refresh"', html)
        self.assertIn("sidecar-progress", html)


if __name__ == "__main__":
    unittest.main()
