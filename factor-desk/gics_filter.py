"""GICS sector filter chips for the Factor Desk top filter strip.

Reads ``gics_sector_name`` from enrichment (or the optional one-shot cache).
Does not invent a ticker→sector map. Does not add a Sectors tab or a Refresh
stage. Chip UI matches the dark-desk G1–G12 / tag language (``.gchip``).
"""

from __future__ import annotations

import html
import json
import re
import sys
from typing import Any, Iterable, Mapping, MutableMapping, Sequence

HERE = __import__("pathlib").Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import dapi_enrich  # noqa: E402

ALL = ""  # All / clear

# Official GICS sector order (display only — not a ticker map).
GICS_SECTOR_ORDER: tuple[str, ...] = (
    "Energy",
    "Materials",
    "Industrials",
    "Consumer Discretionary",
    "Consumer Staples",
    "Health Care",
    "Financials",
    "Information Technology",
    "Communication Services",
    "Utilities",
    "Real Estate",
)

# Short tags so chips sit next to G1–G12. Full name is the tooltip / data value.
GICS_CHIP_LABEL: dict[str, str] = {
    "Energy": "EN",
    "Materials": "MAT",
    "Industrials": "IND",
    "Consumer Discretionary": "DISC",
    "Consumer Staples": "STAP",
    "Health Care": "HC",
    "Financials": "FIN",
    "Information Technology": "IT",
    "Communication Services": "COMM",
    "Utilities": "UTIL",
    "Real Estate": "RE",
}


def sector_of(item: Mapping[str, Any] | None, cache: Mapping[str, Any] | None = None) -> str | None:
    """Resolved main GICS sector for a card / enrich rec / MOM row."""
    if not item:
        return None
    name, _reason = dapi_enrich.parse_gics_sector_name(item.get("gics_sector_name"))
    if name:
        return name
    ticker = str(item.get("ticker") or item.get("name") or "")
    if cache and ticker:
        return dapi_enrich.gics_from_cache(cache, ticker)
    return None


def resolve_sector(
    ticker: str,
    rec: Mapping[str, Any] | None = None,
    cache: Mapping[str, Any] | None = None,
    book: Mapping[str, Any] | None = None,
) -> str | None:
    if rec is None and book is not None:
        rec = dapi_enrich.lookup_name(book, ticker)
    name = sector_of(rec, cache)
    if name:
        return name
    if cache:
        return dapi_enrich.gics_from_cache(cache, ticker)
    return None


def overlay_sector(
    card: MutableMapping[str, Any],
    rec: Mapping[str, Any] | None = None,
    cache: Mapping[str, Any] | None = None,
    book: Mapping[str, Any] | None = None,
) -> MutableMapping[str, Any]:
    ticker = str(card.get("ticker") or card.get("name") or "")
    name = sector_of(card, cache) or resolve_sector(ticker, rec, cache, book)
    card["gics_sector_name"] = name
    return card


def sectors_present(
    items: Iterable[Mapping[str, Any]] | Mapping[str, Any] | None,
    cache: Mapping[str, Any] | None = None,
) -> list[str]:
    """Unique main GICS sectors in the book, official order then extras."""
    found: set[str] = set()
    if isinstance(items, Mapping) and "names" in items and isinstance(items.get("names"), dict):
        seq: Iterable[Mapping[str, Any]] = [
            {"ticker": t, **(dict(r) if isinstance(r, Mapping) else {})}
            for t, r in items["names"].items()
        ]
    else:
        seq = items or []  # type: ignore[assignment]
    for item in seq:
        if not isinstance(item, Mapping):
            continue
        name = sector_of(item, cache)
        if name:
            found.add(name)
    known = [s for s in GICS_SECTOR_ORDER if s in found]
    rest = sorted(s for s in found if s not in GICS_SECTOR_ORDER)
    return known + rest


def chip_label(sector: str) -> str:
    if not sector:
        return "All"
    return GICS_CHIP_LABEL.get(sector, sector)


def filter_items(
    items: Sequence[Mapping[str, Any]] | None,
    sector: str | None,
    cache: Mapping[str, Any] | None = None,
) -> list[Mapping[str, Any]]:
    """Same cards, filtered. Empty / All → unchanged order. No sector map."""
    src = list(items or [])
    want = (sector or "").strip()
    if not want:
        return src
    return [item for item in src if sector_of(item, cache) == want]


def sector_db(items: Iterable[Mapping[str, Any]] | Mapping[str, Any] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    if isinstance(items, Mapping) and isinstance(items.get("names"), dict):
        seq: Iterable[Mapping[str, Any]] = [
            {"ticker": t, **(dict(r) if isinstance(r, Mapping) else {})}
            for t, r in items["names"].items()
        ]
    else:
        seq = items or []  # type: ignore[assignment]
    for item in seq:
        if not isinstance(item, Mapping):
            continue
        ticker = str(item.get("ticker") or item.get("name") or "").strip()
        name = sector_of(item)
        if ticker and name:
            out[ticker] = name
    return out


def strip_css() -> str:
    """Dark-desk G-chip language. Safe to paste into live factorbook.html."""
    return """
.filter-strip, .gics-chips {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  margin: 0;
  min-height: 20px;
}
#gics-filter-strip {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  width: 100%;
  box-sizing: border-box;
  flex: 0 0 100%;
  margin: 0;
  padding: 0 0 8px;
}
.filter-chip, .gchip {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  box-sizing: border-box;
  height: 20px;
  padding: 2px 8px;
  margin: 0;
  border-radius: 3px;
  border: 1px solid #6b7280;
  color: #d1d5db;
  background: #111827;
  font: 650 10px/1 "Segoe UI", "Segoe UI Symbol", "DejaVu Sans", "Noto Sans", ui-sans-serif, system-ui, sans-serif;
  letter-spacing: 0.04em;
  white-space: nowrap;
  text-transform: none;
  cursor: pointer;
  appearance: none;
  -webkit-appearance: none;
  flex: 0 0 auto;
}
.filter-chip:hover, .gchip:hover { border-color: #9ca3af; color: #f3f4f6; }
.filter-chip.active, .gchip.active {
  color: #93c5fd;
  border-color: #60a5fa;
  background: #1e3a5f;
}
.filter-chip[data-gics-chip=""] { letter-spacing: 0.06em; }
.gics-hid { display: none !important; }
""".strip()


def strip_js() -> str:
    """Client filter. Adds .gics-hid only — does not touch MOM Up/Down / Refresh."""
    return r"""
(function () {
  var STRIP_ID = "gics-filter-strip";
  var selected = "";
  var builtKey = null;
  var LABEL = {
    "Energy": "EN", "Materials": "MAT", "Industrials": "IND",
    "Consumer Discretionary": "DISC", "Consumer Staples": "STAP",
    "Health Care": "HC", "Financials": "FIN",
    "Information Technology": "IT", "Communication Services": "COMM",
    "Utilities": "UTIL", "Real Estate": "RE"
  };
  var ORDER = ["Energy","Materials","Industrials","Consumer Discretionary","Consumer Staples","Health Care","Financials","Information Technology","Communication Services","Utilities","Real Estate"];

  function db() {
    var el = document.getElementById("gics-sector-db");
    if (!el) return {};
    try { return JSON.parse(el.textContent || "{}") || {}; }
    catch (e) { return {}; }
  }

  function isChrome(el) {
    if (!el || !el.closest) return true;
    if (el.closest("#gics-filter-strip, nav, .topnav, .toolbar, #refresh, #options-refresh, #sidecar-progress")) return true;
    if (el.matches && el.matches("button.filter-chip, button.gchip, .nav-btn, #refresh, .refresh, #options-refresh, .options-refresh")) return true;
    return false;
  }

  function tickerOf(el) {
    return (el.getAttribute("data-t") || el.getAttribute("data-ticker") || el.getAttribute("data-name") || "").trim();
  }

  function lookupTicker(t, map) {
    if (!t) return "";
    if (map[t]) return map[t];
    var keys = Object.keys(map);
    var short = t.split(/\s+/)[0];
    for (var i = 0; i < keys.length; i++) {
      var k = keys[i];
      if (k === t) return map[k];
      if (t && (k.indexOf(t) === 0 || t.indexOf(k) === 0)) return map[k];
      if (k.split(/\s+/)[0] === short) return map[k];
    }
    return "";
  }

  function sectorOf(el, map) {
    var s = (el.getAttribute("data-gics-sector") || "").trim();
    if (s) return s;
    return lookupTicker(tickerOf(el), map);
  }

  function cardNodes() {
    var out = [];
    var nodes = document.querySelectorAll("[data-gics-sector], [data-ticker], [data-t], article.card, .card");
    for (var i = 0; i < nodes.length; i++) {
      var el = nodes[i];
      if (isChrome(el)) continue;
      if (el.closest && el.closest("#gics-filter-strip")) continue;
      if (out.indexOf(el) >= 0) continue;
      out.push(el);
    }
    return out;
  }

  function uniqueSectors(map) {
    var have = {};
    var cards = cardNodes();
    for (var i = 0; i < cards.length; i++) {
      var s = sectorOf(cards[i], map);
      if (s) have[s] = true;
    }
    var keys = Object.keys(map);
    for (var k = 0; k < keys.length; k++) have[map[keys[k]]] = true;
    var known = [];
    for (var o = 0; o < ORDER.length; o++) if (have[ORDER[o]]) known.push(ORDER[o]);
    var rest = Object.keys(have).filter(function (n) { return known.indexOf(n) < 0; }).sort();
    return known.concat(rest);
  }

  function ensureStrip() {
    var el = document.getElementById(STRIP_ID);
    if (el) return el;
    el = document.createElement("div");
    el.id = STRIP_ID;
    el.className = "filter-strip gics-chips";
    el.setAttribute("role", "toolbar");
    el.setAttribute("aria-label", "GICS sector filter");
    var host = document.querySelector(".filter-strip, .filters, .chip-row, .g-row, .top-filters") ||
               document.querySelector("h1") || document.body.firstElementChild;
    if (host && host.parentNode) {
      if (host.classList && (host.classList.contains("filter-strip") || host.classList.contains("filters") || host.classList.contains("chip-row") || host.classList.contains("g-row") || host.classList.contains("top-filters"))) {
        host.appendChild(el);
      } else {
        host.parentNode.insertBefore(el, host.nextSibling);
      }
    } else {
      document.body.insertBefore(el, document.body.firstChild);
    }
    return el;
  }

  function renderChips(sectors) {
    var strip = ensureStrip();
    strip.innerHTML = "";
    function add(name, label, title) {
      var b = document.createElement("button");
      b.type = "button";
      b.className = "filter-chip gchip" + ((name === selected) ? " active" : "");
      b.setAttribute("data-gics-chip", name);
      b.title = title;
      b.textContent = label;
      b.addEventListener("click", function () {
        selected = (selected === name) ? "" : name;
        apply();
      });
      strip.appendChild(b);
    }
    add("", "All", "All sectors");
    for (var i = 0; i < sectors.length; i++) {
      add(sectors[i], LABEL[sectors[i]] || sectors[i], sectors[i]);
    }
  }

  function apply() {
    var map = db();
    var sectors = uniqueSectors(map);
    var key = sectors.join("|");
    if (builtKey !== key) {
      renderChips(sectors);
      builtKey = key;
    }
    var cards = cardNodes();
    for (var i = 0; i < cards.length; i++) {
      var el = cards[i];
      var s = sectorOf(el, map);
      var hide = selected && s !== selected;
      if (hide) el.classList.add("gics-hid");
      else el.classList.remove("gics-hid");
    }
    var chips = document.querySelectorAll("#gics-filter-strip [data-gics-chip]");
    for (var c = 0; c < chips.length; c++) {
      var on = chips[c].getAttribute("data-gics-chip") === selected;
      chips[c].classList.toggle("active", on);
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", apply);
  } else {
    apply();
  }
})();
""".strip()


def render_strip(sectors: Sequence[str] | None) -> str:
    bits = [
        '<div id="gics-filter-strip" class="filter-strip gics-chips" role="toolbar" aria-label="GICS sector filter">'
    ]
    bits.append(
        '<button type="button" class="filter-chip gchip active" data-gics-chip="" title="All sectors">All</button>'
    )
    for name in sectors or []:
        label = html.escape(chip_label(name))
        full = html.escape(name)
        bits.append(
            f'<button type="button" class="filter-chip gchip" data-gics-chip="{full}" title="{full}">{label}</button>'
        )
    bits.append("</div>")
    return "".join(bits)


GICS_SECTOR_DB_PLACEHOLDER = "__GICS_SECTOR_DB__"
DB_SCRIPT_ID = "gics-sector-db"
STRIP_HOST_ID = "gics-filter-strip"
CSS_STYLE_ID = "gics-filter-css"


def sector_db_json(mapping: Mapping[str, str] | None) -> str:
    return json.dumps(dict(mapping or {}), separators=(",", ":"), ensure_ascii=True)


def filled_sector_db(
    items: Iterable[Mapping[str, Any]] | Mapping[str, Any] | None = None,
    cache: Mapping[str, Any] | None = None,
    book: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    """Ticker → GICS name from enrich/cache only. Never invents a map."""
    out: dict[str, str] = {}
    if cache:
        blob: Mapping[str, Any]
        names = cache.get("names") if isinstance(cache, Mapping) else None
        blob = names if isinstance(names, dict) else cache
        for ticker, raw in blob.items():
            if isinstance(raw, Mapping):
                raw = raw.get("gics_sector_name") or raw.get("sector")
            name, _reason = dapi_enrich.parse_gics_sector_name(raw)
            if name:
                out[str(ticker)] = name
    if book is not None:
        out.update(sector_db(book))
    if items is not None:
        out.update(sector_db(items))
    return out


def _gics_sector_db_json(
    items: Iterable[Mapping[str, Any]] | Mapping[str, Any] | None = None,
    cache: Mapping[str, Any] | None = None,
    book: Mapping[str, Any] | None = None,
) -> str:
    """Filled JSON for ``#gics-sector-db`` / ``__GICS_SECTOR_DB__``."""
    return sector_db_json(filled_sector_db(items, cache, book))


def embed_db(mapping: Mapping[str, str] | None) -> str:
    blob = sector_db_json(mapping)
    return f'<script type="application/json" id="{DB_SCRIPT_ID}">{html.escape(blob, quote=False)}</script>'


def markup_attr(sector: str | None) -> str:
    if not sector:
        return ""
    return html.escape(sector, quote=True)


def _ensure_css(html_text: str) -> str:
    css = f'<style id="{CSS_STYLE_ID}">\n{strip_css()}\n</style>\n'
    text, n = re.subn(
        rf'<style\b[^>]*\bid=["\']{CSS_STYLE_ID}["\'][^>]*>.*?</style>\s*',
        lambda _m: css,
        html_text,
        count=1,
        flags=re.I | re.S,
    )
    if n:
        return text
    if "</head>" in html_text:
        return html_text.replace("</head>", css + "</head>", 1)
    return css + html_text


def _ensure_host(html_text: str) -> str:
    if re.search(r'id=["\']gics-filter-strip["\']', html_text, re.I):
        return html_text
    host = (
        f'<div id="{STRIP_HOST_ID}" class="filter-strip gics-chips" '
        'role="toolbar" aria-label="GICS sector filter"></div>\n'
    )
    for pat in (
        r"(<nav\b[^>]*>.*?</nav>)",
        r'(<div\b[^>]*class=["\'][^"\']*(?:filter-strip|filters|chip-row|g-row|top-filters)[^"\']*["\'][^>]*>)',
        r"(<h1\b[^>]*>.*?</h1>)",
        r"(<body\b[^>]*>)",
    ):
        match = re.search(pat, html_text, re.I | re.S)
        if match:
            return html_text[: match.end()] + "\n" + host + html_text[match.end() :]
    return host + html_text


def _ensure_db(html_text: str, mapping: Mapping[str, str] | None) -> str:
    tag = embed_db(mapping)
    blob = sector_db_json(mapping)
    if re.search(r'id=["\']gics-sector-db["\']', html_text, re.I):
        html_text = re.sub(
            r'<script\b[^>]*\bid=["\']gics-sector-db["\'][^>]*>.*?</script>',
            lambda _m: tag,
            html_text,
            count=1,
            flags=re.I | re.S,
        )
        return html_text
    if GICS_SECTOR_DB_PLACEHOLDER in html_text:
        html_text = html_text.replace(GICS_SECTOR_DB_PLACEHOLDER, blob)
        if not re.search(r'id=["\']gics-sector-db["\']', html_text, re.I):
            wrapped = tag
            if "</body>" in html_text:
                html_text = html_text.replace("</body>", wrapped + "\n</body>", 1)
            else:
                html_text += wrapped
        return html_text
    if "</body>" in html_text:
        return html_text.replace("</body>", tag + "\n</body>", 1)
    return html_text + tag


def _ensure_js(html_text: str) -> str:
    has_strip = "STRIP_ID" in html_text and "gics-filter-strip" in html_text
    has_fn = "var STRIP_ID" in html_text or "STRIP_ID =" in html_text
    if has_strip and has_fn and "gics-hid" in html_text and "gics-sector-db" in html_text:
        return html_text
    script = "<script>\n" + strip_js() + "\n</script>\n"
    if "</body>" in html_text:
        return html_text.replace("</body>", script + "</body>", 1)
    return html_text + script


def ensure_embedded(html_text: str, mapping: Mapping[str, str] | None) -> str:
    """Re-embed host + CSS + filled sector-db + strip JS after every HTML write.

    Live ``write_combined`` sometimes keeps the strip/CSS but wipes
    ``#gics-sector-db`` and the ``STRIP_ID`` script. Call this on the way out.
    """
    text = html_text or ""
    text = _ensure_css(text)
    text = _ensure_host(text)
    text = _ensure_db(text, mapping)
    text = _ensure_js(text)
    return text
