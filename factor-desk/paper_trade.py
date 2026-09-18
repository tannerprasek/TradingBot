"""Factor Desk paper book — 1-unit Buy/Sell on dense MOM cards.

Paper only (no brokerage). Positions persist in ``localStorage`` across Refresh.
Optional JSON schema is here for a later sidecar file; Desktop does not need it.

Position rules (do not flip in one click)
----------------------------------------
- Flat + Buy  → open **long** at current mark
- Flat + Sell → open **short** at current mark
- Long + Sell → **close** long
- Short + Buy → **close** short
- Same-side click while open → **no-op + brief toast**
  (``Already long — sell to close`` / ``Already short — buy to close``).
  Does not add size.

P&L (1 unit notional)
---------------------
- Long:  ``(mark - entry) / entry``
- Short: ``(entry - mark) / entry``  (profits when price falls)

Mark price (first finite > 0)
-----------------------------
card ``price`` / ``px`` / ``px_last`` / ``PX_LAST`` / last Refresh print /
last point of ``px_series``, then ``#fd-paper-marks``, then ``MOM.cards``.
Missing mark → buttons disabled, ``title`` explains why.

Paper tab (not a home chrome strip)
-----------------------------------
Top-nav **Paper** (``data-view="paper"``, ``#fd-nav-paper``) opens
``#view-paper``: ticker + Buy/Sell to open at the current mark, an
open-positions **table** (Opened / Ticker / Side / Entry / Mark /
Return % / Close), week scorecard since Monday 00:00
``America/Edmonton``, and a closed-trades table. Same
``fd-paper-book`` store as card Buy/Sell. Never inject
``#fd-paper-home`` into top chrome / BOOK delta.
Missing mark → Mark / Return show ``—`` (never fake ±100%); Close
is disabled. Marks refresh from live card prices on Refresh.

Live FLAGS/WATCH/MOM chrome is left alone except the card Buy/Sell
strip. Recopy ``paper_trade.py`` to Desktop; do **not** wholesale
replace ``desk_dash.py``.
"""

from __future__ import annotations

import html
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, MutableMapping, Sequence

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore[misc, assignment]

HERE = __import__("pathlib").Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import dapi_enrich  # noqa: E402
import mom_streak  # noqa: E402

LOG = __import__("logging").getLogger("paper_trade")

BOOK_KIND = "factor-desk-paper"
BOOK_VERSION = 1
STORAGE_KEY = "fd-paper-book"
QTY = 1.0

DB_SCRIPT_ID = "fd-paper-marks"
JS_SCRIPT_ID = "fd-paper-js"
CSS_STYLE_ID = "fd-paper-css"
HOST_CLASS = "fd-paper"
HOME_HOST_ID = "fd-paper-home"  # legacy chrome strip — stripped, never re-injected
VIEW_ID = "view-paper"
NAV_ID = "fd-nav-paper"
TAB_ID = "fd-paper-tab"
OPENS_ID = "fd-paper-opens"
WEEK_ID = "fd-paper-week"
CLOSED_ID = "fd-paper-closed"
TICKER_INPUT_ID = "fd-paper-ticker"
SETVIEW_MARKER = "/*fd-paper-setview*/"
HIDEALL_MARKER = "/*fd-paper-hideall*/"
PAINTVIEW_MARKER = "/*fd-paper-paintview*/"

BTN_PAPER = (
    f'<button type="button" class="btn nav-btn" id="{NAV_ID}" '
    'data-view="paper" data-fd-paper-nav="1">Paper</button>'
)

NATIVE_VIEW_IDS: tuple[str, ...] = (
    "home",
    "view-mom-up",
    "view-mom-down",
    "view-outliers",
    "view-options",
    "view-sectors",
    "search-pane",
    "view-breakout",
    "view-breakdown",
    "view-experimental",
    VIEW_ID,
)

_ALLOWLIST_RE = re.compile(
    r"home\|mom-up\|mom-down\|outliers\|options"
    r"(?:\|sectors)?(?:\|breakout\|breakdown)?(?:\|experimental)?(?!\|paper)",
    re.I,
)
_SETVIEW_FN_RE = re.compile(
    r"(function\s+setView\s*\(\s*(\w+)\s*(?:,[^)]*)?\)\s*\{)",
    re.I,
)
_PAINTVIEW_FN_RE = re.compile(
    r"(function\s+paintView\s*\(\s*(\w+)\s*(?:,[^)]*)?\)\s*\{)",
    re.I,
)
_HIDEALL_FN_RE = re.compile(
    r"(function\s+hideAllPanes\s*\(\s*\)\s*\{)",
    re.I,
)
_VIEW_ID_ARRAY_RE = re.compile(
    r"""((?:\[\s*["']home["']\s*,\s*["']view-mom-up["']\s*,\s*["']view-mom-down["']"""
    r"""\s*,\s*["']view-outliers["']\s*,\s*["']view-options["']"""
    r"""(?:\s*,\s*["']view-sectors["'])?(?:\s*,\s*["']search-pane["'])?"""
    r"""(?:\s*,\s*["']view-breakout["'])?(?:\s*,\s*["']view-breakdown["'])?"""
    r"""(?:\s*,\s*["']view-experimental["'])?))"""
    r"""(?![^\]]*(?:view-paper))(\s*\])""",
    re.I,
)
_VIEW_SEL_RE = re.compile(
    r"(#home\s*,\s*#view-mom-up\s*,\s*#view-mom-down\s*,\s*#view-outliers\s*,\s*"
    r"#view-options(?:\s*,\s*#view-sectors)?(?:\s*,\s*#search-pane)?"
    r"(?:\s*,\s*#view-breakout)?(?:\s*,\s*#view-breakdown)?"
    r"(?:\s*,\s*#view-experimental)?)"
    r"(?![^\"';)]*(?:#view-paper))",
    re.I,
)
_DIV_TOKEN_RE = re.compile(r"<\s*(/)?\s*div\b([^>]*)>", re.I)
_OPTIONS_VIEW_BTN_RE = re.compile(
    r'(<button\b(?=[^>]*data-view=["\']options["\'])[^>]*>\s*Options\s*</button>)',
    re.I | re.S,
)
_EXPERIMENTAL_BTN_RE = re.compile(
    r'(<button\b(?=[^>]*(?:data-view=["\']experimental["\']|>\s*Experimental))[^>]*>\s*Experimental\s*</button>)',
    re.I | re.S,
)

NO_MARK_REASON = "No mark price — need card price / PX_LAST / last Refresh print"
TOAST_ALREADY_LONG = "Already long — sell to close"
TOAST_ALREADY_SHORT = "Already short — buy to close"

# Week scorecard window: Monday 00:00 America/Edmonton (documented in DEPLOY-HOOKS §10).
WEEK_TZ_NAME = "America/Edmonton"
OPEN_SHOW_MAX = 12  # legacy chip-strip cap; the Paper tab lists every open
EMPTY_OPENS = "no open paper"
EMPTY_WEEK = "no closed yet this week"
EMPTY_CLOSED = "no closed paper"

# First finite > 0 wins. Live Refresh often writes px / PX_LAST / last.
MARK_KEYS: tuple[str, ...] = (
    "paper_mark",
    "px_last",
    "PX_LAST",
    "LAST_PRICE",
    "last_px",
    "last_price",
    "lastPx",
    "pxLast",
    "PxLast",
    "LAST_PX",
    "Last",
    "LAST",
    "mark",
    "mark_px",
    "price",
    "px",
    "last",
    "close",
    "px_close",
    "adj_close",
    "PX_CLOSE",
    "last_print",
    "print",
    "trd_px",
)
SERIES_KEYS: tuple[str, ...] = (
    "px_series",
    "prices",
    "closes",
    "px",
    "last_refresh",
    "refresh_px",
    "series",
    "hist",
)
NEST_MARK_KEYS: tuple[str, ...] = (
    "quote",
    "ref",
    "raw",
    "fields",
    "dapi",
    "bloomberg",
    "ohlc",
    "last_refresh",
    "px",
    "payload",
    "print",
    "prints",
)

SIDECAR_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "Factor Desk paper book",
    "description": (
        "Optional sidecar shape (paper_book.json later). Browser source of truth "
        "is localStorage key fd-paper-book. qty is always 1; no size UI."
    ),
    "type": "object",
    "required": ["version", "kind", "positions", "closed"],
    "properties": {
        "version": {"type": "integer", "const": BOOK_VERSION},
        "kind": {"type": "string", "const": BOOK_KIND},
        "asof": {"type": ["string", "null"]},
        "positions": {
            "type": "object",
            "additionalProperties": {
                "type": "object",
                "required": ["ticker", "side", "entry", "qty", "opened_at"],
                "properties": {
                    "ticker": {"type": "string"},
                    "side": {"type": "string", "enum": ["long", "short"]},
                    "entry": {"type": "number", "exclusiveMinimum": 0},
                    "qty": {"type": "number", "const": QTY},
                    "opened_at": {"type": "string"},
                },
            },
        },
        "closed": {
            "type": "object",
            "additionalProperties": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["ticker", "side", "entry", "exit", "qty", "ret_pct", "closed_at"],
                    "properties": {
                        "ticker": {"type": "string"},
                        "side": {"type": "string", "enum": ["long", "short"]},
                        "entry": {"type": "number"},
                        "exit": {"type": "number"},
                        "qty": {"type": "number", "const": QTY},
                        "ret_pct": {"type": "number"},
                        "opened_at": {"type": "string"},
                        "closed_at": {"type": "string"},
                    },
                },
            },
        },
    },
}


def sidecar_schema() -> dict[str, Any]:
    """JSON Schema for a later sidecar file. Not written on Refresh."""
    return json.loads(json.dumps(SIDECAR_SCHEMA))


def _short(ticker: str) -> str:
    parts = (ticker or "").strip().split()
    return parts[0].upper() if parts else ""


def card_ticker(card: Mapping[str, Any] | None) -> str:
    return mom_streak.card_ticker(card)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def empty_book(*, asof: str | None = None) -> dict[str, Any]:
    return {
        "version": BOOK_VERSION,
        "kind": BOOK_KIND,
        "asof": asof,
        "positions": {},
        "closed": {},
    }


def _series_last(value: Any) -> float | None:
    if isinstance(value, Mapping):
        for key in MARK_KEYS:
            px = dapi_enrich.as_float(value.get(key))
            if px is not None and px > 0:
                return px
        px = dapi_enrich.as_float(value.get("value") or value.get("y"))
        if px is not None and px > 0:
            return px
        return None
    if isinstance(value, (list, tuple)):
        if not value:
            return None
        if len(value) >= 2 and not isinstance(value[0], (list, tuple, Mapping)):
            px = dapi_enrich.as_float(value[-1])
            return px if px is not None and px > 0 else None
        return _series_last(value[-1])
    px = dapi_enrich.as_float(value)
    return px if px is not None and px > 0 else None


def mark_of(card: Mapping[str, Any] | None) -> float | None:
    """Best available mark on a card / enrich rec / MOM row."""
    if not card:
        return None
    for key in MARK_KEYS:
        raw = card.get(key)
        if isinstance(raw, Mapping):
            continue
        px = dapi_enrich.as_float(raw)
        if px is not None and px > 0:
            return px
    # Case-insensitive Last / PX_LAST on live MOM cards.
    lowered = {str(k).lower(): v for k, v in card.items()}
    for key in MARK_KEYS:
        raw = lowered.get(key.lower())
        if isinstance(raw, Mapping):
            continue
        px = dapi_enrich.as_float(raw)
        if px is not None and px > 0:
            return px
    for key in SERIES_KEYS:
        raw = card.get(key)
        if key in MARK_KEYS and not isinstance(raw, (list, tuple)):
            continue
        if isinstance(raw, (list, tuple)) and raw:
            px = _series_last(raw)
            if px is not None:
                return px
    for nest in NEST_MARK_KEYS:
        inner = card.get(nest)
        if isinstance(inner, Mapping) and inner is not card:
            px = mark_of(inner)
            if px is not None:
                return px
    return None


def attach_mark(
    card: MutableMapping[str, Any],
    rec: Mapping[str, Any] | None = None,
) -> MutableMapping[str, Any]:
    px = mark_of(card) or mark_of(rec)
    if px is not None:
        card["paper_mark"] = px
        if card.get("px_last") is None:
            card["px_last"] = px
    return card


def _put_mark(out: dict[str, float], ticker: str, px: float | None) -> None:
    if not ticker or px is None or px <= 0:
        return
    out[ticker] = px
    short = _short(ticker)
    if short:
        out.setdefault(short, px)


def marks_db(
    cards: Iterable[Mapping[str, Any]] | None = None,
    book: Mapping[str, Any] | None = None,
    html: str | None = None,
) -> dict[str, float]:
    """ticker / short → mark, for ``#fd-paper-marks`` after Refresh.

    Walks card rows, enrichment ``book.names`` (including nested ``PX_LAST`` /
    ``raw``), and optionally live HTML ``window.MOM`` so a Desktop Refresh of
    the ~4.8MB desk still fills prints when enrich cards have no ``px_last``.
    """
    out: dict[str, float] = {}
    if html:
        out.update(harvest_html_marks(html))
    for card in cards or []:
        if not isinstance(card, Mapping):
            continue
        _collect_mark(out, card)
    names = (book or {}).get("names") if isinstance(book, Mapping) else None
    if isinstance(names, dict):
        for ticker, rec in names.items():
            if isinstance(rec, Mapping):
                _collect_mark(out, rec, ticker_hint=str(ticker))
            else:
                _put_mark(out, str(ticker), dapi_enrich.as_float(rec))
    elif isinstance(book, Mapping):
        _collect_mark(out, book)
    return out


_ARTICLE_OPEN_RE = re.compile(r"<article\b([^>]*)>", re.I)
_ATTR_RE = re.compile(r"""\b([:\w.-]+)\s*=\s*["']([^"']*)["']""", re.I)
_MARKS_SCRIPT_RE = re.compile(
    rf'<script\b[^>]*\bid=["\']{DB_SCRIPT_ID}["\'][^>]*>(.*?)</script>',
    re.I | re.S,
)
_JSON_SCRIPT_RE = re.compile(
    r'<script\b[^>]*type=["\']application/json["\'][^>]*>(.*?)</script>',
    re.I | re.S,
)
_MOM_ASSIGN_RE = re.compile(
    r"(?:window\.)?(?:MOM(?:_CARDS)?(?:\s*\.\s*(?:cards|up|down|flags|watch|all))?|BOOK|NAMES)\s*=\s*",
    re.I,
)
_TICKER_TOKEN_RE = re.compile(r"^[A-Z][A-Z0-9./-]{0,11}$")
_STRUCT_KEYS = frozenset(
    {
        "CARDS",
        "CARD",
        "UP",
        "DOWN",
        "FLAGS",
        "WATCH",
        "PX",
        "LAST",
        "MARK",
        "MARKS",
        "PRICE",
        "PRICES",
        "CLOSE",
        "CLOSES",
        "RAW",
        "QUOTE",
        "REF",
        "FIELDS",
        "OHLC",
        "SERIES",
        "HIST",
        "PAYLOAD",
        "PRINT",
        "PRINTS",
        "MOM",
        "HOME",
        "ALL",
        "NAMES",
        "BOOK",
        "META",
        "ASOF",
        "VERSION",
        "KIND",
        "POSITIONS",
        "CLOSED",
        "OUTLIERS",
        "SEARCH",
        "DATA",
        "ITEMS",
        "ROWS",
        "VALUES",
        "T",
        "TICKER",
        "SYMBOL",
        "NAME",
        "D",
        "SIDE",
        "ENTRY",
        "EXIT",
        "QTY",
        "OPEN",
        "OPENS",
        "WEEK",
        "BLOOMBERG",
        "DAPI",
        "PXLAST",
        "LASTPRICE",
        "PXCLOSE",
        "ADJCLOSE",
    }
)
_TICKER_FIELD_RE = re.compile(
    r"""["']?(?:t|ticker|symbol)["']?\s*:\s*["']([^"']+)["']""",
    re.I,
)
_PX_FIELD_RE = re.compile(
    r"""["']?(?:PX_LAST|px_last|LAST_PRICE|last_px|last_price|lastPx|pxLast|"""
    r"""paper_mark|mark_px|price|px|Last|LAST|last|close)["']?\s*:\s*"""
    r"""["']?([0-9]+(?:\.[0-9]+)?)["']?""",
    re.I,
)


def _looks_like_ticker(value: str) -> bool:
    short = _short(value)
    if not short or short in _STRUCT_KEYS:
        return False
    return bool(_TICKER_TOKEN_RE.fullmatch(short))


def _collect_mark(
    out: dict[str, float],
    obj: Any,
    ticker_hint: str | None = None,
    *,
    _depth: int = 0,
) -> None:
    if _depth > 6 or obj is None:
        return
    if isinstance(obj, Mapping):
        hint = ticker_hint
        for key in ("t", "ticker", "symbol", "d", "name"):
            raw = obj.get(key)
            if isinstance(raw, str) and _looks_like_ticker(raw):
                hint = raw
                break
        px = mark_of(obj)
        if hint:
            _put_mark(out, str(hint), px)
        for key, val in obj.items():
            key_s = str(key)
            if _looks_like_ticker(key_s):
                if isinstance(val, Mapping):
                    _collect_mark(out, val, ticker_hint=key_s, _depth=_depth + 1)
                elif isinstance(val, (list, tuple)):
                    _collect_mark(out, val, ticker_hint=None, _depth=_depth + 1)
                else:
                    _put_mark(out, key_s, dapi_enrich.as_float(val) or mark_of({"last": val}))
            elif isinstance(val, (Mapping, list, tuple)) and key_s.lower() not in {
                "null_reasons",
                "fields_used",
                "enrich_pills",
            }:
                _collect_mark(out, val, ticker_hint=hint, _depth=_depth + 1)
        return
    if isinstance(obj, (list, tuple)):
        if len(obj) >= 2 and isinstance(obj[0], str) and _looks_like_ticker(obj[0]):
            _put_mark(out, str(obj[0]), dapi_enrich.as_float(obj[1]))
        for item in obj:
            _collect_mark(out, item, ticker_hint=ticker_hint, _depth=_depth + 1)


def _extract_balanced(text: str, start: int, max_len: int = 6_000_000) -> str | None:
    if start >= len(text) or text[start] not in "{[":
        return None
    opener = text[start]
    closer = "}" if opener == "{" else "]"
    depth = 0
    in_str = False
    quote = ""
    esc = False
    j = start
    n = len(text)
    while j < n and (j - start) < max_len:
        ch = text[j]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == quote:
                in_str = False
        elif ch in "'\"":
            in_str = True
            quote = ch
        elif ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return text[start : j + 1]
        j += 1
    return None


def _js_like_load(blob: str) -> Any:
    s = (blob or "").strip()
    if not s:
        return None
    s = re.sub(r"/\*.*?\*/", "", s, flags=re.S)
    s = re.sub(r"(^|[^:\"'])//.*?$", r"\1", s, flags=re.M)
    s = re.sub(r"\bundefined\b|\bNaN\b", "null", s)

    def _single_strings(m: re.Match[str]) -> str:
        return json.dumps(bytes(m.group(1), "utf-8").decode("unicode_escape") if "\\" in m.group(1) else m.group(1))

    try:
        s2 = re.sub(r"'([^'\\]*(?:\\.[^'\\]*)*)'", lambda m: json.dumps(m.group(1)), s)
    except re.error:
        s2 = s
    s2 = re.sub(r"([{\[,]\s*)([A-Za-z_][A-Za-z0-9_]*)\s*:", r'\1"\2":', s2)
    s2 = re.sub(r",\s*([}\]])", r"\1", s2)
    try:
        return json.loads(s2)
    except json.JSONDecodeError:
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            return None


def _harvest_ticker_px_pairs(text: str, out: dict[str, float]) -> None:
    for m in _TICKER_FIELD_RE.finditer(text or ""):
        ticker = m.group(1)
        if not _looks_like_ticker(ticker):
            continue
        window = (text or "")[m.end() : m.end() + 420]
        px_m = _PX_FIELD_RE.search(window)
        if px_m:
            _put_mark(out, ticker, dapi_enrich.as_float(px_m.group(1)))


def harvest_html_marks(html_text: str | None) -> dict[str, float]:
    """Pull marks already on the live desk HTML so Refresh cannot wipe them.

    Sources: existing ``#fd-paper-marks`` JSON, ``data-px`` on cards,
    ``window.MOM`` / ``MOM.cards`` (last / PX_LAST / px), other JSON script
    tags, then nearby ticker/price pairs. Never treats 0 as a mark.
    """
    out: dict[str, float] = {}
    text = html_text or ""
    blob_m = _MARKS_SCRIPT_RE.search(text)
    if blob_m:
        raw = (blob_m.group(1) or "").replace("<\\/", "</").strip()
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            parsed = {}
        if isinstance(parsed, Mapping):
            for key, val in parsed.items():
                _put_mark(out, str(key), dapi_enrich.as_float(val))
    for tag in _ARTICLE_OPEN_RE.finditer(text):
        attrs = {name.lower(): val for name, val in _ATTR_RE.findall(tag.group(1) or "")}
        ticker = attrs.get("data-t") or attrs.get("data-ticker") or attrs.get("data-name") or ""
        px = attrs.get("data-px") or attrs.get("data-px-last") or attrs.get("data-mark")
        _put_mark(out, ticker, dapi_enrich.as_float(px))
    for assign in _MOM_ASSIGN_RE.finditer(text):
        idx = assign.end()
        while idx < len(text) and text[idx] in " \t\r\n":
            idx += 1
        blob = _extract_balanced(text, idx)
        if not blob:
            continue
        parsed = _js_like_load(blob)
        if parsed is not None:
            _collect_mark(out, parsed)
        else:
            _harvest_ticker_px_pairs(blob, out)
    for script in _JSON_SCRIPT_RE.finditer(text):
        raw = (script.group(1) or "").replace("<\\/", "</").strip()
        if not raw or raw in {"{}", "[]"}:
            continue
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            continue
        _collect_mark(out, parsed)
    if len(out) < 2:
        _harvest_ticker_px_pairs(text, out)
    return out


def merge_marks(*maps: Mapping[str, Any] | None) -> dict[str, float]:
    """Later maps win when their values are finite ``> 0``."""
    out: dict[str, float] = {}
    for mapping in maps:
        if not isinstance(mapping, Mapping):
            continue
        for key, val in mapping.items():
            _put_mark(out, str(key), dapi_enrich.as_float(val))
    return out


def pnl_pct(side: str | None, entry: float | None, mark: float | None) -> float | None:
    """Signed percent. Long up = +, short down = +.

    Missing / non-positive mark or entry → ``None`` (never treat as 0,
    which would paint every long −100% and every short +100%).
    """
    if side not in {"long", "short"}:
        return None
    entry_px = dapi_enrich.as_float(entry)
    mark_px = dapi_enrich.as_float(mark)
    if entry_px is None or mark_px is None or entry_px <= 0 or mark_px <= 0:
        return None
    if side == "long":
        return (mark_px - entry_px) / entry_px * 100.0
    return (entry_px - mark_px) / entry_px * 100.0


def fmt_pct(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:+.2f}%"


def fmt_px(value: float | None) -> str:
    px = dapi_enrich.as_float(value)
    if px is None or px <= 0:
        return "—"
    return f"{px:.2f}"


def position_of(book: Mapping[str, Any] | None, ticker: str) -> dict[str, Any] | None:
    if not book:
        return None
    positions = book.get("positions") if isinstance(book.get("positions"), dict) else {}
    key = _short(ticker)
    raw = positions.get(key) or positions.get(ticker)
    return dict(raw) if isinstance(raw, Mapping) else None


def closed_of(book: Mapping[str, Any] | None, ticker: str) -> list[dict[str, Any]]:
    if not book:
        return []
    closed = book.get("closed") if isinstance(book.get("closed"), dict) else {}
    key = _short(ticker)
    raw = closed.get(key) or closed.get(ticker) or []
    if not isinstance(raw, list):
        return []
    return [dict(row) for row in raw if isinstance(row, Mapping)]


def _normalize_position(raw: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not raw:
        return None
    side = str(raw.get("side") or "").strip().lower()
    if side not in {"long", "short"}:
        return None
    entry = dapi_enrich.as_float(raw.get("entry"))
    if entry is None or entry <= 0:
        return None
    ticker = _short(str(raw.get("ticker") or ""))
    if not ticker:
        return None
    return {
        "ticker": ticker,
        "side": side,
        "entry": float(entry),
        "qty": QTY,
        "opened_at": str(raw.get("opened_at") or ""),
    }


def load_book(raw: Any, *, asof: str | None = None) -> dict[str, Any]:
    """Coerce localStorage / sidecar JSON into a book. Bad payload → empty."""
    book = empty_book(asof=asof)
    if isinstance(raw, str) and raw.strip():
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return book
    if not isinstance(raw, Mapping):
        return book
    positions: dict[str, dict[str, Any]] = {}
    for key, val in (raw.get("positions") or {}).items() if isinstance(raw.get("positions"), dict) else []:
        pos = _normalize_position(val if isinstance(val, Mapping) else None)
        if pos:
            positions[_short(str(key) or pos["ticker"])] = pos
    closed: dict[str, list[dict[str, Any]]] = {}
    src_closed = raw.get("closed") if isinstance(raw.get("closed"), dict) else {}
    for key, rows in src_closed.items():
        kept: list[dict[str, Any]] = []
        for row in rows or []:
            if not isinstance(row, Mapping):
                continue
            side = str(row.get("side") or "").strip().lower()
            entry = dapi_enrich.as_float(row.get("entry"))
            exit_px = dapi_enrich.as_float(row.get("exit"))
            if side not in {"long", "short"} or entry is None or exit_px is None:
                continue
            ret = dapi_enrich.as_float(row.get("ret_pct"))
            if ret is None:
                ret = pnl_pct(side, entry, exit_px)
            kept.append(
                {
                    "ticker": _short(str(row.get("ticker") or key)),
                    "side": side,
                    "entry": float(entry),
                    "exit": float(exit_px),
                    "qty": QTY,
                    "ret_pct": float(ret) if ret is not None else 0.0,
                    "opened_at": str(row.get("opened_at") or ""),
                    "closed_at": str(row.get("closed_at") or ""),
                }
            )
        if kept:
            closed[_short(str(key))] = kept
    book["positions"] = positions
    book["closed"] = closed
    if raw.get("asof"):
        book["asof"] = raw.get("asof")
    return book


def dump_book(book: Mapping[str, Any] | None) -> str:
    payload = load_book(book)
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=True)


def week_tz():
    """America/Edmonton; UTC-6 fallback if zoneinfo/tzdata is missing (MDT)."""
    if ZoneInfo is not None:
        try:
            return ZoneInfo(WEEK_TZ_NAME)
        except Exception:
            LOG.warning("zoneinfo %s unavailable; using UTC-6", WEEK_TZ_NAME)
    return timezone(timedelta(hours=-6))


def parse_when(value: Any) -> datetime | None:
    """Parse closed_at / opened_at. Naive stamps are treated as UTC."""
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt: datetime | None = None
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        dt = None
    if dt is None:
        try:
            dt = datetime.strptime(text[:10], "%Y-%m-%d")
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def week_start(now: datetime | None = None) -> datetime:
    """Monday 00:00 America/Edmonton containing ``now`` (UTC if naive)."""
    tz = week_tz()
    if now is None:
        local = datetime.now(tz)
    elif now.tzinfo is None:
        local = now.replace(tzinfo=timezone.utc).astimezone(tz)
    else:
        local = now.astimezone(tz)
    monday = (local - timedelta(days=local.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return monday


def iter_closed(book: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    state = load_book(book)
    rows: list[dict[str, Any]] = []
    closed = state.get("closed") if isinstance(state.get("closed"), dict) else {}
    for key, items in closed.items():
        for row in items or []:
            if not isinstance(row, Mapping):
                continue
            item = dict(row)
            item.setdefault("ticker", _short(str(key)))
            rows.append(item)
    return rows


def closed_this_week(
    book: Mapping[str, Any] | None,
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    start = week_start(now)
    kept: list[dict[str, Any]] = []
    for row in iter_closed(book):
        when = parse_when(row.get("closed_at"))
        if when is None:
            continue
        if when >= start:
            kept.append(row)
    kept.sort(key=lambda r: (str(r.get("closed_at") or ""), str(r.get("ticker") or "")))
    return kept


def _row_ret(row: Mapping[str, Any]) -> float | None:
    ret = dapi_enrich.as_float(row.get("ret_pct"))
    if ret is not None:
        return float(ret)
    return pnl_pct(str(row.get("side") or ""), row.get("entry"), row.get("exit"))


def week_scorecard(
    book: Mapping[str, Any] | None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Closed-trade stats since Monday 00:00 America/Edmonton.

    Hit = signed return ``> 0`` (shorts that profit when price falls count).
    Avg win / avg loss are means of those signed percents (losses stay negative).
    Zero P&L is neither a win nor a loss; it still counts in ``count`` / hit rate.
    """
    start = week_start(now)
    rows = closed_this_week(book, now=now)
    rets = [_row_ret(row) for row in rows]
    rets = [r for r in rets if r is not None]
    count = len(rows)
    hits = sum(1 for r in rets if r > 0)
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r < 0]
    return {
        "count": count,
        "hits": hits,
        "hit_rate": (100.0 * hits / count) if count else None,
        "avg_win": (sum(wins) / len(wins)) if wins else None,
        "avg_loss": (sum(losses) / len(losses)) if losses else None,
        "week_start": start.isoformat(),
        "tz": WEEK_TZ_NAME,
        "empty": count == 0,
    }


def open_positions(book: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    state = load_book(book)
    rows: list[dict[str, Any]] = []
    positions = state.get("positions") if isinstance(state.get("positions"), dict) else {}
    for val in positions.values():
        pos = _normalize_position(val if isinstance(val, Mapping) else None)
        if pos:
            rows.append(pos)
    rows.sort(key=lambda p: (str(p.get("opened_at") or ""), p["ticker"]))
    return rows


def mark_lookup(marks: Mapping[str, Any] | None, ticker: str) -> float | None:
    if not marks or not ticker:
        return None
    px = dapi_enrich.as_float(marks.get(ticker))
    if px is not None and px > 0:
        return px
    short = _short(ticker)
    px = dapi_enrich.as_float(marks.get(short)) if short else None
    if px is not None and px > 0:
        return px
    return None


def open_rows(
    book: Mapping[str, Any] | None,
    marks: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Open book rows with live signed P&L % from ``marks`` / ``#fd-paper-marks``."""
    out: list[dict[str, Any]] = []
    for pos in open_positions(book):
        mark = mark_lookup(marks, pos["ticker"])
        ret = pnl_pct(pos["side"], pos["entry"], mark)
        out.append(
            {
                **pos,
                "mark": mark,
                "pnl_pct": ret,
                "label": f"{pos['ticker']} {position_line(pos, mark)}".strip(),
            }
        )
    return out


def close_position(pos: Mapping[str, Any], exit_px: float, when: str) -> dict[str, Any]:
    side = str(pos.get("side") or "")
    entry = float(pos.get("entry") or 0)
    ret = pnl_pct(side, entry, exit_px)
    return {
        "ticker": _short(str(pos.get("ticker") or "")),
        "side": side,
        "entry": entry,
        "exit": float(exit_px),
        "qty": QTY,
        "ret_pct": float(ret) if ret is not None else 0.0,
        "opened_at": str(pos.get("opened_at") or ""),
        "closed_at": when,
    }


def apply_click(
    book: MutableMapping[str, Any] | Mapping[str, Any] | None,
    ticker: str,
    action: str,
    mark: float | None,
    *,
    when: str | None = None,
) -> dict[str, Any]:
    """Apply Buy/Sell. Mutates ``book``. Never flips in one click."""
    state = load_book(book)
    if isinstance(book, dict):
        book.clear()
        book.update(state)
        state = book
    key = _short(ticker)
    stamp = when or now_iso()
    act = str(action or "").strip().lower()
    px = dapi_enrich.as_float(mark)
    if not key:
        return {"ok": False, "action": "blocked", "toast": "No ticker", "reason": "no_ticker"}
    if px is None or px <= 0:
        return {
            "ok": False,
            "action": "blocked",
            "toast": NO_MARK_REASON,
            "reason": "no_mark",
        }
    positions = state.setdefault("positions", {})
    closed = state.setdefault("closed", {})
    pos = positions.get(key)
    if not pos:
        if act == "buy":
            positions[key] = {
                "ticker": key,
                "side": "long",
                "entry": float(px),
                "qty": QTY,
                "opened_at": stamp,
            }
            return {"ok": True, "action": "open_long", "toast": None, "position": positions[key]}
        if act == "sell":
            positions[key] = {
                "ticker": key,
                "side": "short",
                "entry": float(px),
                "qty": QTY,
                "opened_at": stamp,
            }
            return {"ok": True, "action": "open_short", "toast": None, "position": positions[key]}
        return {"ok": False, "action": "blocked", "toast": "Unknown action", "reason": "bad_action"}
    side = pos.get("side")
    if side == "long":
        if act == "sell":
            row = close_position(pos, float(px), stamp)
            positions.pop(key, None)
            closed.setdefault(key, []).insert(0, row)
            return {"ok": True, "action": "close_long", "toast": None, "closed": row}
        return {"ok": True, "action": "noop", "toast": TOAST_ALREADY_LONG, "position": pos}
    if side == "short":
        if act == "buy":
            row = close_position(pos, float(px), stamp)
            positions.pop(key, None)
            closed.setdefault(key, []).insert(0, row)
            return {"ok": True, "action": "close_short", "toast": None, "closed": row}
        return {"ok": True, "action": "noop", "toast": TOAST_ALREADY_SHORT, "position": pos}
    return {"ok": False, "action": "blocked", "toast": "Bad position", "reason": "bad_side"}


def _date_label(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "—"
    return text[:10]


def _side_cell(side: str | None) -> str:
    raw = str(side or "").strip().lower()
    label = html.escape(raw.upper() or "—")
    cls = "fd-paper-side"
    if raw == "long":
        cls += " fd-paper-up"
    elif raw == "short":
        cls += " fd-paper-down"
    return f'<span class="{cls}">{label}</span>'


def _ret_cell(ret: float | None) -> str:
    cls = "fd-paper-num"
    if ret is not None:
        cls += " fd-paper-up" if ret >= 0 else " fd-paper-down"
    return f'<span class="{cls}">{html.escape(fmt_pct(ret))}</span>'


def _open_row_html(row: Mapping[str, Any]) -> str:
    ticker_raw = str(row.get("ticker") or "")
    ticker = html.escape(ticker_raw, quote=True)
    ticker_txt = html.escape(ticker_raw)
    mark = dapi_enrich.as_float(row.get("mark"))
    has_mark = mark is not None and mark > 0
    ret = row.get("pnl_pct") if has_mark else None
    disabled = "" if has_mark else " disabled"
    title = "" if has_mark else html.escape(NO_MARK_REASON, quote=True)
    return (
        f'<tr class="fd-paper-open-row" data-fd-paper-ticker="{ticker}">'
        f'<td class="fd-paper-num">{html.escape(_date_label(row.get("opened_at")))}</td>'
        f'<td><button type="button" class="fd-paper-ticker" data-fd-paper-ticker="{ticker}">'
        f"{ticker_txt}</button></td>"
        f"<td>{_side_cell(str(row.get('side') or ''))}</td>"
        f'<td class="fd-paper-num">{html.escape(fmt_px(row.get("entry")))}</td>'
        f'<td class="fd-paper-num">{html.escape(fmt_px(mark if has_mark else None))}</td>'
        f"<td>{_ret_cell(ret if isinstance(ret, (int, float)) else None)}</td>"
        f'<td><button type="button" class="fd-paper-btn fd-paper-close" data-fd-paper-close="1" '
        f'data-fd-paper-ticker="{ticker}"{disabled} title="{title}">Close</button></td>'
        f"</tr>"
    )


def _closed_row_html(row: Mapping[str, Any]) -> str:
    ticker_raw = str(row.get("ticker") or "")
    ticker = html.escape(ticker_raw, quote=True)
    return (
        f'<tr data-fd-paper-ticker="{ticker}">'
        f'<td class="fd-paper-num">{html.escape(_date_label(row.get("opened_at")))}</td>'
        f'<td class="fd-paper-num">{html.escape(_date_label(row.get("closed_at")))}</td>'
        f"<td>{html.escape(ticker_raw)}</td>"
        f"<td>{_side_cell(str(row.get('side') or ''))}</td>"
        f'<td class="fd-paper-num">{html.escape(fmt_px(row.get("entry")))}</td>'
        f'<td class="fd-paper-num">{html.escape(fmt_px(row.get("exit")))}</td>'
        f"<td>{_ret_cell(_row_ret(row))}</td>"
        f"</tr>"
    )


def _open_table_html(opens: Sequence[Mapping[str, Any]]) -> str:
    if not opens:
        return f'<p class="fd-paper-home-empty">{html.escape(EMPTY_OPENS)}</p>'
    body = "".join(_open_row_html(row) for row in opens)
    return (
        '<table class="fd-paper-table" aria-label="Open paper">'
        "<thead><tr>"
        "<th>Opened</th><th>Ticker</th><th>Side</th>"
        "<th>Entry</th><th>Mark</th><th>Return %</th><th></th>"
        "</tr></thead>"
        f"<tbody>{body}</tbody></table>"
    )


def _closed_table_html(closed: Sequence[Mapping[str, Any]]) -> str:
    if not closed:
        return f'<p class="fd-paper-home-empty">{html.escape(EMPTY_CLOSED)}</p>'
    body = "".join(_closed_row_html(row) for row in closed)
    return (
        '<table class="fd-paper-table" aria-label="Closed paper">'
        "<thead><tr>"
        "<th>Opened</th><th>Closed</th><th>Ticker</th><th>Side</th>"
        "<th>Entry</th><th>Exit</th><th>Return %</th>"
        "</tr></thead>"
        f"<tbody>{body}</tbody></table>"
    )


def panes_html(
    book: Mapping[str, Any] | None = None,
    marks: Mapping[str, Any] | None = None,
    *,
    now: datetime | None = None,
) -> str:
    """Paper tab pane. JS re-paints opens / scorecard / closed from localStorage."""
    opens = open_rows(book, marks) if book else []
    score = week_scorecard(book, now=now) if book else {
        "count": 0,
        "empty": True,
        "tz": WEEK_TZ_NAME,
    }
    closed = closed_rows(book) if book else []
    open_inner = _open_table_html(opens)
    closed_inner = _closed_table_html(closed)
    empty_cls = " fd-paper-home-empty" if score.get("empty") else ""
    tz = html.escape(WEEK_TZ_NAME, quote=True)
    return "\n".join(
        [
            f'<div id="{VIEW_ID}" class="view-pane hide" data-view="paper" hidden>',
            '  <div class="ph">Paper</div>',
            f'  <div class="fd-paper-tab" id="{TAB_ID}" role="region" '
            f'aria-label="Paper book" data-week-tz="{tz}">',
            '    <form class="fd-paper-open-form" id="fd-paper-open-form" action="javascript:void(0)">',
            f'      <input id="{TICKER_INPUT_ID}" type="text" placeholder="TICKER" '
            'autocomplete="off" spellcheck="false" aria-label="Ticker" />',
            '      <button type="button" class="fd-paper-btn fd-paper-buy" data-fd-paper-open="buy">Buy</button>',
            '      <button type="button" class="fd-paper-btn fd-paper-sell" data-fd-paper-open="sell">Sell</button>',
            "    </form>",
            '    <section class="fd-paper-opens-sec">',
            '      <span class="fd-paper-home-kicker">open</span>',
            f'      <div id="{OPENS_ID}" class="fd-paper-opens">{open_inner}</div>',
            "    </section>",
            '    <section class="fd-paper-week-sec">',
            '      <span class="fd-paper-home-kicker">week</span>',
            f'      <span id="{WEEK_ID}" class="fd-paper-home-score{empty_cls}">'
            f"{html.escape(scorecard_line(score))}</span>",
            "    </section>",
            '    <section class="fd-paper-closed-sec">',
            '      <span class="fd-paper-home-kicker">closed</span>',
            f'      <div id="{CLOSED_ID}" class="fd-paper-closed-wrap">{closed_inner}</div>',
            "    </section>",
            "  </div>",
            "</div>",
        ]
    )


def position_line(pos: Mapping[str, Any] | None, mark: float | None) -> str:
    if not pos:
        return ""
    side = str(pos.get("side") or "").upper()
    entry = dapi_enrich.as_float(pos.get("entry"))
    entry_s = f"{entry:.2f}" if entry is not None else "—"
    ret = pnl_pct(str(pos.get("side") or ""), entry, mark)
    return f"{side} @ {entry_s}  {fmt_pct(ret)}"


def history_rows(rows: Sequence[Mapping[str, Any]] | None) -> list[str]:
    out: list[str] = []
    for row in rows or []:
        if not isinstance(row, Mapping):
            continue
        side = str(row.get("side") or "").upper()
        ret = dapi_enrich.as_float(row.get("ret_pct"))
        if ret is None:
            ret = pnl_pct(str(row.get("side") or ""), row.get("entry"), row.get("exit"))
        out.append(f"{_date_label(row.get('closed_at'))} · {side} · {fmt_pct(ret)}")
    return out


def chrome_html(
    ticker: str,
    mark: float | None = None,
    book: Mapping[str, Any] | None = None,
) -> str:
    """Server chrome (skinny cloud cards). Live desk is hydrated by ``strip_js``."""
    key = html.escape(_short(ticker) or ticker, quote=True)
    has_mark = mark is not None and mark > 0
    reason = html.escape(NO_MARK_REASON if not has_mark else "", quote=True)
    disabled = "" if has_mark else " disabled"
    title = reason if not has_mark else ""
    pos = position_of(book, ticker) if book else None
    buy_title = title
    sell_title = title
    if has_mark and pos is None:
        buy_title = f"Open long @ {mark:g}"
        sell_title = f"Open short @ {mark:g}"
    elif has_mark and pos and pos.get("side") == "long":
        buy_title = TOAST_ALREADY_LONG
        sell_title = f"Close long @ {mark:g}"
    elif has_mark and pos and pos.get("side") == "short":
        buy_title = f"Close short @ {mark:g}"
        sell_title = TOAST_ALREADY_SHORT
    pos_line = position_line(pos, mark)
    hist = history_rows(closed_of(book, ticker)) if book else []
    hist_items = "".join(f"<li>{html.escape(line)}</li>" for line in hist)
    details = ""
    if hist:
        details = (
            f'<details class="fd-paper-hist"><summary>Previous trades ({len(hist)})</summary>'
            f"<ul>{hist_items}</ul></details>"
        )
    else:
        details = (
            '<details class="fd-paper-hist fd-paper-hist-empty">'
            "<summary>Previous trades</summary><ul></ul></details>"
        )
    pos_cls = ""
    if pos:
        ret = pnl_pct(str(pos.get("side") or ""), pos.get("entry"), mark)
        if ret is not None:
            pos_cls = " fd-paper-up" if ret >= 0 else " fd-paper-down"
    return (
        f'<div class="{HOST_CLASS}" data-fd-paper="1" data-ticker="{key}">'
        f'<div class="fd-paper-actions">'
        f'<button type="button" class="fd-paper-btn fd-paper-buy" data-fd-paper-act="buy"'
        f'{disabled} title="{html.escape(buy_title, quote=True)}">Buy</button>'
        f'<button type="button" class="fd-paper-btn fd-paper-sell" data-fd-paper-act="sell"'
        f'{disabled} title="{html.escape(sell_title, quote=True)}">Sell</button>'
        f"</div>"
        f'<div class="fd-paper-pos{pos_cls}">{html.escape(pos_line)}</div>'
        f"{details}"
        f"</div>"
    )


def host_html() -> str:
    return f'<div class="{HOST_CLASS}" data-fd-paper="1" hidden></div>'


def scorecard_line(card: Mapping[str, Any] | None) -> str:
    if not card or card.get("empty") or not card.get("count"):
        return EMPTY_WEEK
    count = int(card.get("count") or 0)
    rate = card.get("hit_rate")
    hit = f"{int(round(float(rate)))}% hit" if rate is not None else "— hit"
    win = card.get("avg_win")
    loss = card.get("avg_loss")
    win_s = f"avg win {fmt_pct(win)}" if win is not None else "avg win —"
    loss_s = f"avg loss {fmt_pct(loss)}" if loss is not None else "avg loss —"
    return f"{count} closed · {hit} · {win_s} · {loss_s}"


def closed_rows(book: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """All closed trades, newest first — Paper tab history list."""
    rows = iter_closed(book)
    rows.sort(
        key=lambda r: (str(r.get("closed_at") or ""), str(r.get("ticker") or "")),
        reverse=True,
    )
    return rows


def home_host_html(*_args: Any, **_kwargs: Any) -> str:
    """Legacy chrome strip. Always empty — Paper lives in ``#view-paper``."""
    return ""


def _script_json(blob: str) -> str:
    return (blob or "").replace("</", "<\\/")


def embed_db(marks: Mapping[str, Any] | None) -> str:
    blob = json.dumps(dict(marks or {}), separators=(",", ":"), ensure_ascii=True, default=str)
    return f'<script type="application/json" id="{DB_SCRIPT_ID}">{_script_json(blob)}</script>'


def strip_css() -> str:
    return f"""
.{HOST_CLASS} {{
  margin: 8px 0 0;
  padding-top: 8px;
  border-top: 1px solid #1f2937;
  font: 650 10px/1.25 "Segoe UI", "DejaVu Sans", "Noto Sans", ui-sans-serif, system-ui, sans-serif;
}}
.{HOST_CLASS}[hidden] {{ display: none !important; }}
.fd-paper-actions {{
  display: flex;
  gap: 6px;
  align-items: center;
  margin: 0 0 4px;
}}
.fd-paper-btn {{
  display: inline-block;
  font: 650 10px/1.15 "Segoe UI", "DejaVu Sans", "Noto Sans", ui-sans-serif, system-ui, sans-serif;
  letter-spacing: 0.04em;
  padding: 2px 8px;
  border-radius: 3px;
  border: 1px solid #4b5563;
  color: #e5e7eb;
  background: #1f2937;
  cursor: pointer;
  text-transform: none;
}}
.fd-paper-buy {{ color: #6ee7b7; border-color: #34d399; }}
.fd-paper-sell {{ color: #fda4af; border-color: #fb7185; }}
.fd-paper-btn:hover:not(:disabled) {{ color: #fff; }}
.fd-paper-btn:disabled {{
  opacity: 0.45;
  cursor: not-allowed;
}}
.fd-paper-pos {{
  font-variant-numeric: tabular-nums;
  color: #9ca3af;
  min-height: 1.15em;
  letter-spacing: 0.03em;
}}
.fd-paper-pos.fd-paper-up {{ color: #6ee7b7; }}
.fd-paper-pos.fd-paper-down {{ color: #fda4af; }}
.fd-paper-hist {{
  margin: 4px 0 0;
  color: #9ca3af;
}}
.fd-paper-hist summary {{
  cursor: pointer;
  list-style: none;
  letter-spacing: 0.04em;
}}
.fd-paper-hist summary::-webkit-details-marker {{ display: none; }}
.fd-paper-hist ul {{
  margin: 4px 0 0;
  padding: 0 0 0 12px;
}}
.fd-paper-hist li {{
  font-variant-numeric: tabular-nums;
}}
.fd-paper-toast {{
  position: fixed;
  left: 50%;
  bottom: 18px;
  transform: translateX(-50%);
  z-index: 40;
  padding: 6px 10px;
  border-radius: 3px;
  border: 1px solid #4b5563;
  background: #111827;
  color: #fde68a;
  font: 650 11px/1.2 "Segoe UI", "DejaVu Sans", "Noto Sans", ui-sans-serif, system-ui, sans-serif;
  letter-spacing: 0.04em;
  pointer-events: none;
}}
#{HOME_HOST_ID}, .fd-paper-home {{
  display: none !important;
}}
#{VIEW_ID},
#{VIEW_ID}.hide,
#{VIEW_ID}[hidden] {{
  display: none !important;
}}
body[data-fd-paper="1"] #{VIEW_ID}.fd-paper-on:not(.hide):not([hidden]),
#{VIEW_ID}.fd-paper-on {{
  display: block !important;
}}
#{VIEW_ID} .ph {{
  font: 650 13px/1.2 "Segoe UI", "DejaVu Sans", "Noto Sans", ui-sans-serif, system-ui, sans-serif;
  letter-spacing: 0.04em;
  margin: 0 0 10px;
  color: #9ca3af;
}}
.fd-paper-tab {{
  display: flex;
  flex-direction: column;
  gap: 14px;
  max-width: 960px;
  font: 650 10px/1.15 "Segoe UI", "DejaVu Sans", "Noto Sans", ui-sans-serif, system-ui, sans-serif;
}}
.fd-paper-open-form {{
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}}
.fd-paper-open-label {{
  display: flex;
  align-items: center;
  gap: 6px;
  color: #9ca3af;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}}
#{TICKER_INPUT_ID} {{
  font: 650 12px/1.2 "Segoe UI", ui-sans-serif, system-ui, sans-serif;
  padding: 4px 8px;
  border-radius: 3px;
  border: 1px solid #4b5563;
  background: #111827;
  color: #e5e7eb;
  width: 8.5em;
  text-transform: uppercase;
}}
.fd-paper-opens-sec, .fd-paper-week-sec, .fd-paper-closed-sec {{
  display: flex;
  flex-direction: column;
  gap: 6px;
}}
.fd-paper-opens, .fd-paper-closed-wrap {{
  display: block;
  overflow-x: auto;
}}
.fd-paper-table {{
  width: 100%;
  border-collapse: collapse;
  font-variant-numeric: tabular-nums;
}}
.fd-paper-table th {{
  text-align: left;
  color: #6b7280;
  font-weight: 650;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  padding: 4px 10px 6px 0;
  border-bottom: 1px solid #1f2937;
  white-space: nowrap;
}}
.fd-paper-table td {{
  padding: 6px 10px 6px 0;
  border-bottom: 1px solid #1f2937;
  color: #d1d5db;
  vertical-align: middle;
  white-space: nowrap;
}}
.fd-paper-table th:last-child,
.fd-paper-table td:last-child {{
  padding-right: 0;
}}
.fd-paper-num {{
  font-variant-numeric: tabular-nums;
}}
.fd-paper-ticker {{
  background: none;
  border: 0;
  color: #e5e7eb;
  cursor: pointer;
  font: inherit;
  letter-spacing: 0.04em;
  padding: 0;
}}
.fd-paper-ticker:hover {{ color: #fff; text-decoration: underline; }}
.fd-paper-side {{ letter-spacing: 0.04em; }}
.fd-paper-open-row {{
  /* table row; keep class for JS observers */
}}
.fd-paper-close {{
  color: #fde68a;
  border-color: #a3a3a3;
}}
.nav-btn[data-view="paper"].is-on,
.nav-btn[data-view="paper"].on,
.btn.nav-btn[data-view="paper"].on,
#{NAV_ID}.is-on, #{NAV_ID}.on {{
  border-color: #93c5fd;
  color: #fff;
}}
.fd-paper-home-kicker {{
  font: 650 10px/1.15 "Segoe UI", "DejaVu Sans", "Noto Sans", ui-sans-serif, system-ui, sans-serif;
  letter-spacing: 0.06em;
  color: #6b7280;
  text-transform: uppercase;
  margin-right: 2px;
}}
.fd-paper-chip {{
  display: none !important;
}}
#{VIEW_ID} .fd-paper-chip,
#{VIEW_ID} div.fd-paper-open-row {{
  display: none !important;
}}
.fd-paper-home-empty, .fd-paper-home-score.fd-paper-home-empty {{
  color: #6b7280;
  letter-spacing: 0.03em;
}}
.fd-paper-home-score {{
  color: #9ca3af;
  font-variant-numeric: tabular-nums;
  letter-spacing: 0.03em;
}}
.fd-paper-closed-sec {{
  margin-top: 2px;
}}
.fd-paper-closed {{
  margin-top: 4px;
}}
""".strip()


def strip_js() -> str:
    """Wrap live ``cardHTML`` + hydrate existing articles. localStorage persists Refresh."""
    no_mark = json.dumps(NO_MARK_REASON, ensure_ascii=False)
    already_long = json.dumps(TOAST_ALREADY_LONG, ensure_ascii=False)
    already_short = json.dumps(TOAST_ALREADY_SHORT, ensure_ascii=False)
    storage = json.dumps(STORAGE_KEY, ensure_ascii=False)
    kind = json.dumps(BOOK_KIND, ensure_ascii=False)
    week_tz = json.dumps(WEEK_TZ_NAME, ensure_ascii=False)
    empty_opens = json.dumps(EMPTY_OPENS, ensure_ascii=False)
    empty_week = json.dumps(EMPTY_WEEK, ensure_ascii=False)
    empty_closed = json.dumps(EMPTY_CLOSED, ensure_ascii=False)
    open_limit = str(int(OPEN_SHOW_MAX))
    return r"""
(function () {
  var STORAGE = """ + storage + r""";
  var DB_ID = "fd-paper-marks";
  var KIND = """ + kind + r""";
  var NO_MARK = """ + no_mark + r""";
  var ALREADY_LONG = """ + already_long + r""";
  var ALREADY_SHORT = """ + already_short + r""";
  var WEEK_TZ = """ + week_tz + r""";
  var OPEN_LIMIT = """ + open_limit + r""";
  var EMPTY_OPENS = """ + empty_opens + r""";
  var EMPTY_WEEK = """ + empty_week + r""";
  var EMPTY_CLOSED = """ + empty_closed + r""";
  var HOME_ID = "fd-paper-home";
  var VIEW = "view-paper";
  var NAV_ID = "fd-nav-paper";
  var OPENS_ID = "fd-paper-opens";
  var WEEK_ID = "fd-paper-week";
  var CLOSED_ID = "fd-paper-closed";
  var TICKER_ID = "fd-paper-ticker";
  var NATIVE_VIEWS = """ + json.dumps(list(NATIVE_VIEW_IDS)) + r""";
  var toastTimer = null;
  var liveMarks = window.__FD_PAPER_LIVE_MARKS__ || {};
  window.__FD_PAPER_LIVE_MARKS__ = liveMarks;

  function $(id) { return document.getElementById(id); }
  function shortOf(t) { return String(t || "").trim().split(/\s+/)[0].toUpperCase(); }
  function num(v) {
    if (v == null || v === "") return null;
    if (typeof v === "number") return isFinite(v) && v > 0 ? v : null;
    var s = String(v).trim();
    if (/^\d{4}-\d{2}-\d{2}/.test(s)) return null;
    var n = parseFloat(s.replace(/[$, ]/g, ""));
    return isFinite(n) && n > 0 ? n : null;
  }
  function emptyBook() { return { version: 1, kind: KIND, positions: {}, closed: {} }; }
  function loadBook() {
    try {
      var raw = window.localStorage.getItem(STORAGE);
      if (!raw) return emptyBook();
      var parsed = JSON.parse(raw);
      if (!parsed || typeof parsed !== "object") return emptyBook();
      if (!parsed.positions || typeof parsed.positions !== "object") parsed.positions = {};
      if (!parsed.closed || typeof parsed.closed !== "object") parsed.closed = {};
      parsed.kind = KIND;
      parsed.version = 1;
      return parsed;
    } catch (e) { return emptyBook(); }
  }
  function saveBook(book) {
    try { window.localStorage.setItem(STORAGE, JSON.stringify(book || emptyBook())); }
    catch (e) {}
  }
  function rememberMark(ticker, px) {
    var n = num(px);
    var key = shortOf(ticker);
    if (!n || !key) return n;
    liveMarks[key] = n;
    if (ticker && ticker !== key) liveMarks[ticker] = n;
    return n;
  }
  function persistMarks() {
    var el = $(DB_ID);
    if (!el) {
      el = document.createElement("script");
      el.type = "application/json";
      el.id = DB_ID;
      (document.body || document.documentElement).appendChild(el);
    }
    var db = {};
    try { db = JSON.parse(el.textContent || "{}") || {}; } catch (e) { db = {}; }
    Object.keys(liveMarks).forEach(function (k) {
      if (liveMarks[k]) db[k] = liveMarks[k];
    });
    try { el.textContent = JSON.stringify(db); } catch (e2) {}
  }
  function marksDb() {
    var el = $(DB_ID);
    var db = {};
    if (el) {
      try { db = JSON.parse(el.textContent || "{}") || {}; }
      catch (e) { db = {}; }
    }
    Object.keys(liveMarks).forEach(function (k) {
      if (liveMarks[k]) db[k] = liveMarks[k];
    });
    return db;
  }
  var MARK_KEY_NORM = {papermark:1,pxlast:1,lastprice:1,lastpx:1,last:1,mark:1,markpx:1,price:1,px:1,close:1,pxclose:1,adjclose:1,lastprint:1,print:1,trdpx:1};
  var NEST_MARK_KEYS = ["quote","ref","raw","fields","dapi","bloomberg","ohlc","last_refresh","px","PX","payload","print","prints"];
  var MOM_BAG_KEYS = ["up","down","flags","watch","outliers","search","mom","home","all","px","PX","prints","marks","last","payload","prices"];
  if (window.__FD_PAPER_OBS__) {
    try { window.__FD_PAPER_OBS__.disconnect(); } catch (e0) {}
    window.__FD_PAPER_OBS__ = null;
  }
  function addCards(out, arr) {
    if (!arr) return;
    if (Array.isArray(arr)) {
      for (var i = 0; i < arr.length; i++) {
        var c = arr[i];
        if (Array.isArray(c) && c.length >= 2) out.push({ t: c[0], last: c[1], px: c[1] });
        else out.push(c);
      }
      return;
    }
    if (typeof arr === "object") {
      Object.keys(arr).forEach(function (k) {
        var rec = arr[k];
        if (Array.isArray(rec)) addCards(out, rec);
        else if (rec && typeof rec === "object") {
          out.push(Object.assign({ t: rec.t || rec.ticker || rec.symbol || k, ticker: k }, rec));
        } else if (num(rec)) out.push({ t: k, last: rec, px: rec });
      });
    }
  }
  function momCards() {
    var mom = window.MOM || {};
    var out = [];
    addCards(out, mom.cards);
    MOM_BAG_KEYS.forEach(function (k) { addCards(out, mom[k]); });
    addCards(out, window.MOM_CARDS);
    addCards(out, window.CARDS);
    addCards(out, window.PX);
    addCards(out, window.PRICES);
    addCards(out, window.LAST);
    if (mom && typeof mom === "object" && !Array.isArray(mom.cards)) addCards(out, mom);
    var book = window.BOOK || window.NAMES || {};
    var names = book.names || (book && !Array.isArray(book) && book.t == null ? book : null);
    if (names && typeof names === "object" && !Array.isArray(names)) addCards(out, names);
    return out;
  }
  function findMomCard(ticker) {
    var want = shortOf(ticker);
    var cards = momCards();
    for (var i = 0; i < cards.length; i++) {
      var c = cards[i] || {};
      if (shortOf(c.t || c.ticker || c.d || c.name || c.symbol || "") === want) return c;
    }
    return null;
  }
  function seriesLast(raw) {
    if (raw == null) return null;
    if (Array.isArray(raw)) {
      if (!raw.length) return null;
      var last = raw[raw.length - 1];
      if (Array.isArray(last)) return num(last[last.length - 1]);
      if (last && typeof last === "object") {
        return num(last.px_last || last.PX_LAST || last.px || last.price || last.close || last.last || last.value || last.y);
      }
      return num(last);
    }
    if (typeof raw === "object") {
      return num(raw.px_last || raw.PX_LAST || raw.px || raw.price || raw.close || raw.last || raw.value);
    }
    return num(raw);
  }
  function markFromPxPayload(px) {
    if (px == null) return null;
    var direct = num(px);
    if (direct) return direct;
    if (typeof px !== "object") return null;
    return num(px.LAST || px.Last || px.last || px.PX_LAST || px.px_last || px.CLOSE || px.close || px.value || px.price);
  }
  function markFromCard(card, depth) {
    if (!card || typeof card !== "object") return null;
    depth = depth || 0;
    if (depth > 6) return null;
    var payload = markFromPxPayload(card.px || card.PX || card.payload);
    if (payload) return payload;
    var keys = ["paper_mark","px_last","PX_LAST","LAST_PRICE","last_px","last_price","lastPx","pxLast","PxLast","LAST_PX","Last","LAST","mark","mark_px","price","px","last","close","px_close","adj_close","PX_CLOSE","last_print","print","trd_px"];
    for (var i = 0; i < keys.length; i++) {
      var v = num(card[keys[i]]);
      if (v) return v;
    }
    for (var k in card) {
      if (!Object.prototype.hasOwnProperty.call(card, k)) continue;
      var nk = String(k).toLowerCase().replace(/[^a-z0-9]/g, "");
      if (!MARK_KEY_NORM[nk]) continue;
      var raw = card[k];
      var nv = num(raw);
      if (nv) return nv;
      if (raw && typeof raw === "object" && !Array.isArray(raw)) {
        var nested = markFromCard(raw, depth + 1);
        if (nested) return nested;
      }
    }
    for (var n = 0; n < NEST_MARK_KEYS.length; n++) {
      var inner = card[NEST_MARK_KEYS[n]];
      if (inner && typeof inner === "object" && inner !== card) {
        var nestPx = markFromCard(inner, depth + 1);
        if (nestPx) return nestPx;
      }
    }
    if (card.ohlc && typeof card.ohlc === "object") {
      var oc = num(card.ohlc.c || card.ohlc.close || card.ohlc.last);
      if (oc) return oc;
    }
    var seriesKeys = ["px_series","prices","closes","px","last_refresh","refresh_px","series","hist","ys","spark"];
    for (var j = 0; j < seriesKeys.length; j++) {
      var sraw = card[seriesKeys[j]];
      if (typeof sraw !== "object") continue;
      var s = seriesLast(sraw);
      if (s) return s;
    }
    return null;
  }
  function markFromNode(node) {
    if (!node || !node.getAttribute) return null;
    var attr = num(node.getAttribute("data-px") || node.getAttribute("data-px-last") || node.getAttribute("data-mark"));
    if (attr) return attr;
    var labeled = node.querySelector("[data-field='px_last'], [data-field='PX_LAST'], [data-field='px'], .px-last, .px_last, .last-px, .mark-px");
    if (labeled) {
      var n = num(labeled.getAttribute("data-value") || labeled.textContent);
      if (n) return n;
    }
    var dts = node.querySelectorAll("dt");
    for (var i = 0; i < dts.length; i++) {
      var lab = (dts[i].textContent || "").replace(/\s+/g, " ").trim().toUpperCase();
      if (lab === "PX_LAST" || lab === "PX LAST" || lab === "LAST" || lab === "PX" || lab === "PRICE") {
        var dd = dts[i].parentElement && dts[i].parentElement.querySelector("dd");
        var p = num(dd && dd.textContent);
        if (p) return p;
      }
    }
    return null;
  }
  function harvestPxMap(obj) {
    if (!obj || typeof obj !== "object") return;
    Object.keys(obj).forEach(function (k) {
      var rec = obj[k];
      var t = shortOf((rec && (rec.t || rec.ticker || rec.symbol)) || k);
      if (!t) return;
      var m = markFromPxPayload(rec) || (rec && typeof rec === "object" ? markFromCard(rec) : num(rec));
      if (m) rememberMark(t, m);
    });
  }
  function harvestAllMarks() {
    try {
      var mom = window.MOM || {};
      var cards = momCards();
      for (var i = 0; i < cards.length; i++) {
        var c = cards[i] || {};
        var t = shortOf(c.t || c.ticker || c.d || c.name || c.symbol || "");
        var m = markFromCard(c);
        if (t && m) rememberMark(t, m);
      }
      harvestPxMap(mom.px);
      harvestPxMap(mom.PX);
      harvestPxMap(mom.prints);
      harvestPxMap(mom.marks);
      harvestPxMap(mom.payload);
      harvestPxMap(window.PX);
      harvestPxMap(window.PRICES);
      harvestPxMap(window.LAST);
      var nodes = document.querySelectorAll("article.card, article[data-t], article[data-ticker], .grid .card, [data-px], [data-px-last]");
      for (var j = 0; j < nodes.length; j++) {
        var node = nodes[j];
        var nt = shortOf(node.getAttribute && (node.getAttribute("data-t") || node.getAttribute("data-ticker") || node.getAttribute("data-name") || ""));
        var nm = markFromNode(node);
        if (!nm && nt) nm = markFromCard(findMomCard(nt));
        if (nt && nm) {
          rememberMark(nt, nm);
          if (!node.getAttribute("data-px")) node.setAttribute("data-px", String(nm));
        }
      }
      persistMarks();
    } catch (harvestErr) {}
    return liveMarks;
  }
  function markOf(ticker, node, card) {
    var key = shortOf(ticker);
    var m = markFromCard(card);
    if (m) return rememberMark(key || ticker, m);
    m = markFromNode(node);
    if (m) return rememberMark(key || ticker, m);
    if (liveMarks[ticker]) return num(liveMarks[ticker]);
    if (liveMarks[key]) return num(liveMarks[key]);
    var db = marksDb();
    if (db[ticker]) return rememberMark(key, db[ticker]);
    if (db[key]) return rememberMark(key, db[key]);
    m = markFromCard(findMomCard(ticker || key));
    if (m) return rememberMark(key || ticker, m);
    return null;
  }
  function pnlPct(side, entry, mark) {
    entry = num(entry); mark = num(mark);
    if (!entry || !mark) return null;
    if (side === "long") return (mark - entry) / entry * 100;
    if (side === "short") return (entry - mark) / entry * 100;
    return null;
  }
  function fmtPct(v) {
    if (v == null || !isFinite(v)) return "—";
    return (v >= 0 ? "+" : "") + v.toFixed(2) + "%";
  }
  function fmtPx(v) {
    var n = num(v);
    if (!n) return "—";
    return n.toFixed(2);
  }
  function isoNow() { return new Date().toISOString().replace(/\.\d{3}Z$/, "Z"); }
  function dateLabel(v) { return String(v || "").slice(0, 10) || "—"; }
  function applyClick(book, ticker, act, mark, when) {
    var key = shortOf(ticker);
    var px = num(mark);
    if (!key) return { ok: false, action: "blocked", toast: "No ticker" };
    if (!px) return { ok: false, action: "blocked", toast: NO_MARK };
    book.positions = book.positions || {};
    book.closed = book.closed || {};
    var pos = book.positions[key];
    when = when || isoNow();
    if (!pos) {
      if (act === "buy") {
        book.positions[key] = { ticker: key, side: "long", entry: px, qty: 1, opened_at: when };
        return { ok: true, action: "open_long", toast: null };
      }
      if (act === "sell") {
        book.positions[key] = { ticker: key, side: "short", entry: px, qty: 1, opened_at: when };
        return { ok: true, action: "open_short", toast: null };
      }
      return { ok: false, action: "blocked", toast: "Unknown action" };
    }
    if (pos.side === "long") {
      if (act === "sell") {
        var retL = pnlPct("long", pos.entry, px);
        var rowL = { ticker: key, side: "long", entry: pos.entry, exit: px, qty: 1, ret_pct: retL == null ? 0 : retL, opened_at: pos.opened_at || "", closed_at: when };
        delete book.positions[key];
        (book.closed[key] = book.closed[key] || []).unshift(rowL);
        return { ok: true, action: "close_long", toast: null, closed: rowL };
      }
      return { ok: true, action: "noop", toast: ALREADY_LONG };
    }
    if (pos.side === "short") {
      if (act === "buy") {
        var retS = pnlPct("short", pos.entry, px);
        var rowS = { ticker: key, side: "short", entry: pos.entry, exit: px, qty: 1, ret_pct: retS == null ? 0 : retS, opened_at: pos.opened_at || "", closed_at: when };
        delete book.positions[key];
        (book.closed[key] = book.closed[key] || []).unshift(rowS);
        return { ok: true, action: "close_short", toast: null, closed: rowS };
      }
      return { ok: true, action: "noop", toast: ALREADY_SHORT };
    }
    return { ok: false, action: "blocked", toast: "Bad position" };
  }
  function tzParts(d, tz) {
    var out = {};
    new Intl.DateTimeFormat("en-US", {
      timeZone: tz,
      weekday: "short",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hourCycle: "h23"
    }).formatToParts(d).forEach(function (p) {
      if (p.type !== "literal") out[p.type] = p.value;
    });
    return out;
  }
  function zonedOffsetMs(t, tz) {
    var parts = tzParts(new Date(t), tz);
    var asUTC = Date.UTC(
      Number(parts.year), Number(parts.month) - 1, Number(parts.day),
      Number(parts.hour), Number(parts.minute), Number(parts.second)
    );
    return asUTC - t;
  }
  function zonedMidnight(ymd, tz) {
    var utcGuess = Date.parse(ymd + "T00:00:00Z");
    if (!isFinite(utcGuess)) return null;
    var t = utcGuess - zonedOffsetMs(utcGuess, tz);
    t = utcGuess - zonedOffsetMs(t, tz);
    return t;
  }
  function weekStartMs(now) {
    now = now || new Date();
    var p = tzParts(now, WEEK_TZ);
    var wd = { Sun: 0, Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6 }[p.weekday];
    if (wd == null) wd = now.getDay();
    var daysFromMonday = (wd + 6) % 7;
    var utcNoon = Date.UTC(Number(p.year), Number(p.month) - 1, Number(p.day), 12, 0, 0);
    var mondayNoon = utcNoon - daysFromMonday * 86400000;
    var md = new Date(mondayNoon);
    var y = md.getUTCFullYear();
    var m = String(md.getUTCMonth() + 1); if (m.length < 2) m = "0" + m;
    var day = String(md.getUTCDate()); if (day.length < 2) day = "0" + day;
    return zonedMidnight(y + "-" + m + "-" + day, WEEK_TZ);
  }
  function parseWhenMs(value) {
    if (!value) return null;
    var t = Date.parse(value);
    if (!isFinite(t)) t = Date.parse(String(value).slice(0, 10) + "T00:00:00Z");
    return isFinite(t) ? t : null;
  }
  function inThisWeek(closedAt, now) {
    var t = parseWhenMs(closedAt);
    var start = weekStartMs(now);
    return t != null && start != null && t >= start;
  }
  function closedThisWeek(book, now) {
    var closed = (book && book.closed) || {};
    var rows = [];
    Object.keys(closed).forEach(function (key) {
      var items = closed[key] || [];
      for (var i = 0; i < items.length; i++) {
        var row = items[i] || {};
        if (inThisWeek(row.closed_at, now)) rows.push(row);
      }
    });
    return rows;
  }
  function weekScorecard(book, now) {
    var rows = closedThisWeek(book, now);
    var rets = [];
    for (var i = 0; i < rows.length; i++) {
      var row = rows[i] || {};
      var r = (row.ret_pct != null) ? Number(row.ret_pct) : pnlPct(row.side, row.entry, row.exit);
      if (r != null && isFinite(r)) rets.push(r);
    }
    var count = rows.length;
    var hits = 0, winSum = 0, winN = 0, lossSum = 0, lossN = 0;
    for (var j = 0; j < rets.length; j++) {
      if (rets[j] > 0) { hits += 1; winSum += rets[j]; winN += 1; }
      else if (rets[j] < 0) { lossSum += rets[j]; lossN += 1; }
    }
    return {
      count: count,
      hits: hits,
      hit_rate: count ? (100 * hits / count) : null,
      avg_win: winN ? (winSum / winN) : null,
      avg_loss: lossN ? (lossSum / lossN) : null,
      empty: count === 0,
      tz: WEEK_TZ
    };
  }
  function scoreLine(sc) {
    if (!sc || sc.empty || !sc.count) return EMPTY_WEEK;
    var hit = sc.hit_rate == null ? "— hit" : (Math.round(sc.hit_rate) + "% hit");
    var win = sc.avg_win == null ? "avg win —" : ("avg win " + fmtPct(sc.avg_win));
    var loss = sc.avg_loss == null ? "avg loss —" : ("avg loss " + fmtPct(sc.avg_loss));
    return sc.count + " closed · " + hit + " · " + win + " · " + loss;
  }
  function openList(book) {
    var positions = (book && book.positions) || {};
    var rows = [];
    Object.keys(positions).forEach(function (key) {
      var pos = positions[key];
      if (!pos || (pos.side !== "long" && pos.side !== "short")) return;
      rows.push(pos);
    });
    rows.sort(function (a, b) {
      var ao = String(a.opened_at || ""), bo = String(b.opened_at || "");
      if (ao < bo) return -1; if (ao > bo) return 1;
      var at = String(a.ticker || ""), bt = String(b.ticker || "");
      return at < bt ? -1 : (at > bt ? 1 : 0);
    });
    return rows;
  }
  function selectFn() {
    if (typeof window.selectTicker === "function") return window.selectTicker;
    try { if (typeof selectTicker === "function") return selectTicker; } catch (e) {}
    return null;
  }
  function td(text, cls) {
    var cell = document.createElement("td");
    if (cls) cell.className = cls;
    cell.textContent = text;
    return cell;
  }
  function sideEl(side) {
    var span = document.createElement("span");
    var s = String(side || "").toLowerCase();
    span.className = "fd-paper-side";
    if (s === "long") span.classList.add("fd-paper-up");
    if (s === "short") span.classList.add("fd-paper-down");
    span.textContent = s ? s.toUpperCase() : "—";
    return span;
  }
  function retEl(ret) {
    var span = document.createElement("span");
    span.className = "fd-paper-num";
    if (ret != null && isFinite(ret)) span.classList.add(ret >= 0 ? "fd-paper-up" : "fd-paper-down");
    span.textContent = fmtPct(ret);
    return span;
  }
  function tickerBtn(ticker) {
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "fd-paper-ticker";
    btn.setAttribute("data-fd-paper-ticker", ticker);
    btn.textContent = ticker;
    return btn;
  }
  function closeBtn(ticker, hasMark) {
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "fd-paper-btn fd-paper-close";
    btn.setAttribute("data-fd-paper-close", "1");
    btn.setAttribute("data-fd-paper-ticker", ticker);
    btn.textContent = "Close";
    btn.disabled = !hasMark;
    btn.title = hasMark ? "Close at mark" : NO_MARK;
    return btn;
  }
  function openTableHead() {
    var thead = document.createElement("thead");
    var tr = document.createElement("tr");
    ["Opened", "Ticker", "Side", "Entry", "Mark", "Return %", ""].forEach(function (h) {
      var th = document.createElement("th");
      th.textContent = h;
      tr.appendChild(th);
    });
    thead.appendChild(tr);
    return thead;
  }
  function closedTableHead() {
    var thead = document.createElement("thead");
    var tr = document.createElement("tr");
    ["Opened", "Closed", "Ticker", "Side", "Entry", "Exit", "Return %"].forEach(function (h) {
      var th = document.createElement("th");
      th.textContent = h;
      tr.appendChild(th);
    });
    thead.appendChild(tr);
    return thead;
  }
  function openRowEl(row) {
    var ticker = shortOf(row.ticker || "");
    var mark = markOf(ticker, null, findMomCard(ticker));
    var hasMark = !!num(mark);
    var ret = hasMark ? pnlPct(row.side, row.entry, mark) : null;
    var tr = document.createElement("tr");
    tr.className = "fd-paper-open-row";
    tr.setAttribute("data-fd-paper-ticker", ticker);
    tr.appendChild(td(dateLabel(row.opened_at), "fd-paper-num"));
    var tcell = document.createElement("td");
    tcell.appendChild(tickerBtn(ticker));
    tr.appendChild(tcell);
    var scell = document.createElement("td");
    scell.appendChild(sideEl(row.side));
    tr.appendChild(scell);
    tr.appendChild(td(fmtPx(row.entry), "fd-paper-num"));
    tr.appendChild(td(hasMark ? fmtPx(mark) : "—", "fd-paper-num"));
    var rcell = document.createElement("td");
    rcell.appendChild(retEl(ret));
    tr.appendChild(rcell);
    var ccell = document.createElement("td");
    ccell.appendChild(closeBtn(ticker, hasMark));
    tr.appendChild(ccell);
    return tr;
  }
  function closedRowEl(row) {
    var ticker = shortOf(row.ticker || "");
    var r = (row.ret_pct != null) ? Number(row.ret_pct) : pnlPct(row.side, row.entry, row.exit);
    if (r != null && !isFinite(r)) r = null;
    var tr = document.createElement("tr");
    tr.setAttribute("data-fd-paper-ticker", ticker);
    tr.appendChild(td(dateLabel(row.opened_at), "fd-paper-num"));
    tr.appendChild(td(dateLabel(row.closed_at), "fd-paper-num"));
    tr.appendChild(td(ticker));
    var scell = document.createElement("td");
    scell.appendChild(sideEl(row.side));
    tr.appendChild(scell);
    tr.appendChild(td(fmtPx(row.entry), "fd-paper-num"));
    tr.appendChild(td(fmtPx(row.exit), "fd-paper-num"));
    var rcell = document.createElement("td");
    rcell.appendChild(retEl(r));
    tr.appendChild(rcell);
    return tr;
  }
  function stripLegacyHome() {
    var host = $(HOME_ID);
    if (host && host.parentNode) host.parentNode.removeChild(host);
  }
  function ensureHostById(id, cls, parentSel) {
    var el = $(id);
    if (el && (el.tagName === "UL" || el.tagName === "OL")) {
      var swap = document.createElement("div");
      swap.id = id;
      swap.className = cls || el.className || "";
      if (el.parentNode) el.parentNode.replaceChild(swap, el);
      el = swap;
    }
    if (el) return el;
    var view = $(VIEW);
    if (!view) return null;
    var parent = (parentSel && view.querySelector(parentSel)) || view.querySelector(".fd-paper-tab") || view;
    el = document.createElement("div");
    el.id = id;
    el.className = cls || "";
    parent.appendChild(el);
    return el;
  }
  function stripChipRows(root) {
    var host = root || $(VIEW);
    if (!host || !host.querySelectorAll) return;
    var stale = host.querySelectorAll(".fd-paper-chip, div.fd-paper-open-row");
    for (var i = 0; i < stale.length; i++) {
      var n = stale[i];
      if (n.closest && n.closest(".fd-paper-table")) continue;
      if (n.parentNode) n.parentNode.removeChild(n);
    }
  }
  function paintTab() {
    stripLegacyHome();
    harvestAllMarks();
    var opensEl = ensureHostById(OPENS_ID, "fd-paper-opens", ".fd-paper-opens-sec");
    var weekEl = $(WEEK_ID);
    var closedEl = ensureHostById(CLOSED_ID, "fd-paper-closed-wrap", ".fd-paper-closed-sec");
    stripChipRows($(VIEW));
    if (!opensEl && !$(VIEW)) return;
    var book = loadBook();
    var opens = openList(book);
    var sc = weekScorecard(book);
    var tab = $("fd-paper-tab");
    if (tab) tab.setAttribute("data-week-tz", WEEK_TZ);
    if (opensEl) {
      opensEl.innerHTML = "";
      if (!opens.length) {
        var empty = document.createElement("p");
        empty.className = "fd-paper-home-empty";
        empty.textContent = EMPTY_OPENS;
        opensEl.appendChild(empty);
      } else {
        var table = document.createElement("table");
        table.className = "fd-paper-table";
        table.setAttribute("aria-label", "Open paper");
        table.appendChild(openTableHead());
        var tbody = document.createElement("tbody");
        opens.forEach(function (row) { tbody.appendChild(openRowEl(row)); });
        table.appendChild(tbody);
        opensEl.appendChild(table);
      }
    }
    if (weekEl) {
      weekEl.className = "fd-paper-home-score" + (sc.empty ? " fd-paper-home-empty" : "");
      weekEl.textContent = scoreLine(sc);
    }
    if (closedEl) {
      closedEl.innerHTML = "";
      var closedMap = (book && book.closed) || {};
      var rows = [];
      Object.keys(closedMap).forEach(function (key) {
        var items = closedMap[key] || [];
        for (var i = 0; i < items.length; i++) rows.push(items[i] || {});
      });
      rows.sort(function (a, b) {
        var ac = String(a.closed_at || ""), bc = String(b.closed_at || "");
        if (ac < bc) return 1; if (ac > bc) return -1;
        var at = String(a.ticker || ""), bt = String(b.ticker || "");
        return at < bt ? -1 : (at > bt ? 1 : 0);
      });
      if (!rows.length) {
        var emptyC = document.createElement("p");
        emptyC.className = "fd-paper-home-empty";
        emptyC.textContent = EMPTY_CLOSED;
        closedEl.appendChild(emptyC);
      } else {
        var ctable = document.createElement("table");
        ctable.className = "fd-paper-table";
        ctable.setAttribute("aria-label", "Closed paper");
        ctable.appendChild(closedTableHead());
        var cbody = document.createElement("tbody");
        for (var j = 0; j < rows.length; j++) cbody.appendChild(closedRowEl(rows[j] || {}));
        ctable.appendChild(cbody);
        closedEl.appendChild(ctable);
      }
    }
    syncOpenForm();
  }
  function paintHome() { paintTab(); }
  function hideNativeViews() {
    for (var i = 0; i < NATIVE_VIEWS.length; i++) {
      var el = $(NATIVE_VIEWS[i]);
      if (!el) continue;
      if (NATIVE_VIEWS[i] === VIEW) continue;
      el.classList.add("hide");
    }
  }
  function setOn(btn, on) {
    if (!btn) return;
    btn.classList.toggle("is-on", !!on);
    btn.classList.toggle("on", !!on);
    btn.setAttribute("aria-pressed", on ? "true" : "false");
  }
  function navButtons() {
    return document.querySelectorAll("#topnav .btn, #topnav .nav-btn, .topnav .nav-btn, nav .btn, nav .nav-btn, [data-view], [data-fd-paper-nav]");
  }
  function oursOf(b) {
    var view = (b.getAttribute("data-view") || "");
    return view === "paper" || b.getAttribute("data-fd-paper-nav") === "1" || b.id === NAV_ID;
  }
  function syncNav(on) {
    var buttons = navButtons();
    for (var i = 0; i < buttons.length; i++) {
      var b = buttons[i];
      if (on) setOn(b, oursOf(b));
      else if (oursOf(b)) setOn(b, false);
    }
  }
  function showPaper(on) {
    stripLegacyHome();
    var pane = $(VIEW);
    if (on) {
      hideNativeViews();
      if (pane) {
        pane.classList.remove("hide");
        pane.classList.add("fd-paper-on");
        pane.removeAttribute("hidden");
      }
      paintTab();
      document.body.setAttribute("data-view", "paper");
      document.body.setAttribute("data-fd-paper", "1");
      syncNav(true);
      return;
    }
    if (pane) {
      pane.classList.add("hide");
      pane.classList.remove("fd-paper-on");
      pane.setAttribute("hidden", "hidden");
    }
    var home = $("home");
    if (home) home.classList.remove("hide");
    document.body.removeAttribute("data-fd-paper");
    if (document.body.getAttribute("data-view") === "paper") {
      document.body.removeAttribute("data-view");
    }
    syncNav(false);
  }
  window.__FD_PAPER_SHOW__ = function () { showPaper(true); };
  window.__FD_PAPER_HIDE__ = function () { showPaper(false); };
  function kindOf(btn) {
    if (!btn || !btn.getAttribute) return "";
    if (btn.closest && btn.closest("[data-fd-paper-act], [data-fd-paper-open], [data-fd-paper-close], #" + TICKER_ID + ", #fd-paper-open-form, .fd-paper-ticker, .fd-paper")) return "";
    var view = (btn.getAttribute("data-view") || "").toLowerCase();
    if (btn.id === NAV_ID || btn.getAttribute("data-fd-paper-nav") === "1") return "paper";
    if (view === "paper" && btn.tagName === "BUTTON") return "paper";
    var inNav = !!(btn.closest && btn.closest("#topnav, nav, .topnav"));
    var label = (btn.textContent || "").replace(/\s+/g, " ").trim();
    if (inNav && label === "Paper") return "paper";
    if (btn.id === "refresh" || btn.id === "options-refresh") return "";
    if (inNav && (btn.classList.contains("btn") || btn.classList.contains("nav-btn") || (view && view !== "paper"))) return "other";
    return "";
  }
  function isTradeEl(el) {
    if (!el || !el.closest) return false;
    return !!(el.closest("[data-fd-paper-act], [data-fd-paper-open], [data-fd-paper-close], #fd-paper-open-form, .fd-paper-ticker, .fd-paper"));
  }
  function onNavClick(ev) {
    if (isTradeEl(ev.target)) return;
    var t = ev.target && ev.target.closest
      ? ev.target.closest("#" + NAV_ID + ", [data-fd-paper-nav], #topnav button, nav button, .topnav button, button[data-view]")
      : ev.target;
    if (!t || (t.closest && t.closest("#" + VIEW) && !oursOf(t))) return;
    var kind = kindOf(t);
    if (!kind) return;
    if (kind === "paper") {
      ev.preventDefault();
      ev.stopPropagation();
      showPaper(true);
      return;
    }
    if (kind === "other") showPaper(false);
  }
  function onDocumentClick(ev) {
    if (isTradeEl(ev.target)) {
      onPaperClick(ev);
      return;
    }
    onNavClick(ev);
  }
  window.__FD_PAPER_ON_DOC_CLICK__ = onDocumentClick;
  function installSetViewBridge() {
    var orig = window.setView;
    if (typeof orig !== "function" || orig.__fdPaper) return;
    window.setView = function (v) {
      var kind = String(v || "").toLowerCase();
      if (kind === "paper") {
        showPaper(true);
        return;
      }
      showPaper(false);
      return orig.apply(this, arguments);
    };
    window.setView.__fdPaper = true;
  }
  function tabTicker() {
    var input = $(TICKER_ID);
    return shortOf(input && input.value);
  }
  function syncOpenForm() {
    var buy = document.querySelector("[data-fd-paper-open='buy']");
    var sell = document.querySelector("[data-fd-paper-open='sell']");
    var key = tabTicker();
    var mark = key ? markOf(key, null, findMomCard(key)) : null;
    var hasMark = !!num(mark);
    [buy, sell].forEach(function (btn) {
      if (!btn) return;
      if (!key) {
        btn.disabled = false;
        btn.title = "Enter a ticker";
        return;
      }
      btn.disabled = !hasMark;
      btn.title = hasMark ? ((btn.getAttribute("data-fd-paper-open") === "buy" ? "Open long @ " : "Open short @ ") + mark) : NO_MARK;
    });
  }
  function applyTabTrade(ticker, act) {
    var key = shortOf(ticker);
    if (!key) { showToast("No ticker"); return; }
    harvestAllMarks();
    var card = findMomCard(key);
    var mark = markOf(key, null, card);
    var book = loadBook();
    var res = applyClick(book, key, act, mark);
    if (res.toast) showToast(res.toast);
    if (res.ok && res.action !== "noop") saveBook(book);
    paintAll();
  }
  function onHomeClick(ev) { return false; }
  function showToast(text) {
    if (!text) return;
    var el = $("fd-paper-toast");
    if (!el) {
      el = document.createElement("div");
      el.id = "fd-paper-toast";
      el.className = "fd-paper-toast";
      el.setAttribute("role", "status");
      document.body.appendChild(el);
    }
    el.textContent = text;
    el.hidden = false;
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { el.hidden = true; }, 2200);
  }
  function tickerOf(node, card) {
    if (card) {
      var fromCard = shortOf(card.t || card.ticker || card.name || card.symbol || "");
      if (fromCard) return fromCard;
    }
    if (!node) return "";
    return shortOf(node.getAttribute("data-t") || node.getAttribute("data-ticker") || node.getAttribute("data-name") || "");
  }
  function paintHost(host, ticker, mark, book) {
    var key = shortOf(ticker);
    var pos = (book.positions || {})[key];
    var hist = (book.closed || {})[key] || [];
    var hasMark = !!num(mark);
    var buy = host.querySelector(".fd-paper-buy");
    var sell = host.querySelector(".fd-paper-sell");
    var posEl = host.querySelector(".fd-paper-pos");
    var details = host.querySelector("details.fd-paper-hist");
    if (buy) {
      buy.disabled = !hasMark;
      if (!hasMark) buy.title = NO_MARK;
      else if (!pos) buy.title = "Open long @ " + mark;
      else if (pos.side === "long") buy.title = ALREADY_LONG;
      else buy.title = "Close short @ " + mark;
    }
    if (sell) {
      sell.disabled = !hasMark;
      if (!hasMark) sell.title = NO_MARK;
      else if (!pos) sell.title = "Open short @ " + mark;
      else if (pos.side === "short") sell.title = ALREADY_SHORT;
      else sell.title = "Close long @ " + mark;
    }
    if (posEl) {
      if (!pos) {
        posEl.textContent = "";
        posEl.classList.remove("fd-paper-up", "fd-paper-down");
      } else {
        var ret = pnlPct(pos.side, pos.entry, mark);
        posEl.textContent = String(pos.side || "").toUpperCase() + " @ " + fmtPx(pos.entry) + "  " + fmtPct(ret);
        posEl.classList.toggle("fd-paper-up", ret != null && ret >= 0);
        posEl.classList.toggle("fd-paper-down", ret != null && ret < 0);
      }
    }
    if (details) {
      var open = details.open;
      var ul = details.querySelector("ul") || document.createElement("ul");
      ul.innerHTML = "";
      for (var i = 0; i < hist.length; i++) {
        var row = hist[i] || {};
        var li = document.createElement("li");
        var r = (row.ret_pct != null) ? Number(row.ret_pct) : pnlPct(row.side, row.entry, row.exit);
        li.textContent = dateLabel(row.closed_at) + " · " + String(row.side || "").toUpperCase() + " · " + fmtPct(r);
        ul.appendChild(li);
      }
      if (!details.querySelector("ul")) details.appendChild(ul);
      var sum = details.querySelector("summary") || document.createElement("summary");
      sum.textContent = hist.length ? ("Previous trades (" + hist.length + ")") : "Previous trades";
      if (!details.querySelector("summary")) details.insertBefore(sum, details.firstChild);
      details.classList.toggle("fd-paper-hist-empty", !hist.length);
      details.open = open;
    }
    host.hidden = false;
    host.removeAttribute("hidden");
    host.setAttribute("data-ticker", key);
  }
  function ensureHost(node) {
    var host = node.querySelector && node.querySelector("." + "fd-paper");
    if (host) return host;
    host = document.createElement("div");
    host.className = "fd-paper";
    host.setAttribute("data-fd-paper", "1");
    host.innerHTML = '<div class="fd-paper-actions">' +
      '<button type="button" class="fd-paper-btn fd-paper-buy" data-fd-paper-act="buy">Buy</button>' +
      '<button type="button" class="fd-paper-btn fd-paper-sell" data-fd-paper-act="sell">Sell</button>' +
      '</div><div class="fd-paper-pos"></div>' +
      '<details class="fd-paper-hist fd-paper-hist-empty"><summary>Previous trades</summary><ul></ul></details>';
    node.appendChild(host);
    return host;
  }
  function onPaperClick(ev) {
    var openBtn = ev.target && ev.target.closest ? ev.target.closest("[data-fd-paper-open]") : null;
    if (openBtn) {
      ev.preventDefault();
      ev.stopPropagation();
      if (ev.stopImmediatePropagation) ev.stopImmediatePropagation();
      if (openBtn.disabled) return;
      applyTabTrade(tabTicker(), openBtn.getAttribute("data-fd-paper-open"));
      return;
    }
    var closeBtnEl = ev.target && ev.target.closest ? ev.target.closest("[data-fd-paper-close]") : null;
    if (closeBtnEl) {
      ev.preventDefault();
      ev.stopPropagation();
      if (ev.stopImmediatePropagation) ev.stopImmediatePropagation();
      if (closeBtnEl.disabled) {
        showToast(closeBtnEl.title || NO_MARK);
        return;
      }
      var ct = closeBtnEl.getAttribute("data-fd-paper-ticker") || "";
      var bookC = loadBook();
      var posC = (bookC.positions || {})[shortOf(ct)];
      var actC = posC && posC.side === "short" ? "buy" : "sell";
      applyTabTrade(ct, actC);
      return;
    }
    var chip = ev.target && ev.target.closest ? ev.target.closest("#" + VIEW + " .fd-paper-ticker, #" + VIEW + " [data-fd-paper-ticker]") : null;
    if (chip && !chip.getAttribute("data-fd-paper-close") && !chip.getAttribute("data-fd-paper-act") && !chip.getAttribute("data-fd-paper-open")) {
      if (chip.tagName === "TR" || chip.tagName === "TABLE") return;
      ev.preventDefault();
      ev.stopPropagation();
      if (ev.stopImmediatePropagation) ev.stopImmediatePropagation();
      var tChip = chip.getAttribute("data-fd-paper-ticker") || "";
      var sel = selectFn();
      if (sel && tChip) sel(tChip);
      return;
    }
    var home = ev.target && ev.target.closest ? ev.target.closest("#" + HOME_ID) : null;
    if (home) {
      ev.preventDefault();
      ev.stopPropagation();
      return;
    }
    var btn = ev.target && ev.target.closest ? ev.target.closest("[data-fd-paper-act]") : null;
    var host = ev.target && ev.target.closest ? ev.target.closest(".fd-paper") : null;
    if (!btn && !host) return;
    ev.stopPropagation();
    if (ev.stopImmediatePropagation) ev.stopImmediatePropagation();
    if (!btn || btn.disabled) return;
    ev.preventDefault();
    harvestAllMarks();
    var node = (host && host.closest("article, .card")) || (btn.closest && btn.closest("article, .card"));
    var t = tickerOf(node, null);
    var card = findMomCard(t);
    var mark = markOf(t, node, card);
    var book = loadBook();
    var res = applyClick(book, t, btn.getAttribute("data-fd-paper-act"), mark);
    if (res.toast) showToast(res.toast);
    if (res.ok && res.action !== "noop") saveBook(book);
    paintAll();
  }
  function bindHost(host, node, ticker) {
    if (host.__fdPaperBound) return;
    host.__fdPaperBound = true;
    host.addEventListener("click", onPaperClick, true);
  }
  function hydrateCard(node, card) {
    if (!node || !node.querySelector) return;
    if (node.closest && node.closest("nav, .topnav, #gics-filter-strip, #fd-book-delta, #fd-paper-home, #view-paper, #view-experimental, #sscore-grid")) return;
    if (node.getAttribute && node.getAttribute("data-fd-sscore") === "1") return;
    if (node.classList && node.classList.contains("fd-ss-card")) return;
    if (!(node.matches && (node.matches("article.card, article, .card") || node.hasAttribute("data-t") || node.hasAttribute("data-ticker")))) {
      if (!node.classList || !node.classList.contains("card")) return;
    }
    var ticker = tickerOf(node, card);
    if (!ticker) return;
    var host = ensureHost(node);
    var mark = markOf(ticker, node, card || findMomCard(ticker));
    if (mark && !node.getAttribute("data-px")) node.setAttribute("data-px", String(mark));
    paintHost(host, ticker, mark, loadBook());
    bindHost(host, node, ticker);
  }
  var painting = false;
  function paintAll() {
    if (painting) return;
    painting = true;
    try {
      harvestAllMarks();
      var nodes = document.querySelectorAll("article.card, article[data-t], article[data-ticker], .grid .card, .grid.dense .card");
      for (var i = 0; i < nodes.length; i++) hydrateCard(nodes[i], null);
      paintTab();
    } finally { painting = false; }
  }
  function injectIntoHtml(html, card) {
    try {
      var wrap = document.createElement("div");
      wrap.innerHTML = String(html || "");
      var node = wrap.firstElementChild;
      if (node) hydrateCard(node, card);
      return wrap.innerHTML || html;
    } catch (e) { return html; }
  }
  function wrapCardHTML() {
    var fn = window.cardHTML;
    if (typeof fn !== "function") {
      try { if (typeof cardHTML === "function") fn = cardHTML; } catch (e) { fn = null; }
    }
    if (typeof fn === "function" && !fn.__fdPaper) {
      var wrapped = function (card) {
        var html = fn.apply(this, arguments);
        return injectIntoHtml(html, card);
      };
      wrapped.__fdPaper = true;
      window.cardHTML = wrapped;
      try { cardHTML = wrapped; } catch (e2) {}
    }
  }
  function observe() {
    if (window.__FD_PAPER_OBS__) {
      try { window.__FD_PAPER_OBS__.disconnect(); } catch (eObs) {}
      window.__FD_PAPER_OBS__ = null;
    }
    if (!document.body) return;
    window.__FD_PAPER_OBS__ = new MutationObserver(function (muts) {
      if (painting) return;
      var need = false;
      for (var i = 0; i < muts.length && !need; i++) {
        var added = muts[i].addedNodes || [];
        for (var j = 0; j < added.length; j++) {
          var n = added[j];
          if (!n || n.nodeType !== 1) continue;
          if (n.id === "fd-paper-toast" || n.id === "fd-paper-home" || n.id === "view-paper" || n.id === "fd-paper-tab" || n.id === "fd-paper-opens") continue;
          if (n.classList && (n.classList.contains("fd-paper") || n.classList.contains("fd-paper-home") || n.classList.contains("fd-paper-chip") || n.classList.contains("fd-paper-tab") || n.classList.contains("fd-paper-open-row"))) continue;
          if (n.closest && (n.closest("#fd-paper-home") || n.closest("#view-paper"))) continue;
          if (n.matches && n.matches("article.card, article[data-t], article[data-ticker], .card")) {
            need = true;
            break;
          }
          if (n.querySelector && n.querySelector("article.card, article[data-t], .card")) {
            need = true;
            break;
          }
        }
      }
      if (need) paintAll();
    });
    window.__FD_PAPER_OBS__.observe(document.body, { childList: true, subtree: true });
  }
  function install() {
    wrapCardHTML();
    installSetViewBridge();
    stripLegacyHome();
    harvestAllMarks();
    paintAll();
    observe();
    bindOpenForm();
  }
  function bindOpenForm() {
    var form = $("fd-paper-open-form");
    if (form && !form.__fdPaperBound) {
      form.__fdPaperBound = true;
      form.addEventListener("submit", function (ev) {
        ev.preventDefault();
        applyTabTrade(tabTicker(), "buy");
      });
    }
    var input = $(TICKER_ID);
    if (input && !input.__fdPaperBound) {
      input.__fdPaperBound = true;
      input.addEventListener("input", syncOpenForm);
      input.addEventListener("change", syncOpenForm);
    }
    syncOpenForm();
  }
  window.__FD_PAPER_APPLY__ = applyClick;
  window.__FD_PAPER_PNL__ = pnlPct;
  window.__FD_PAPER_MARK__ = markOf;
  window.__FD_PAPER_PAINT__ = paintAll;
  window.__FD_PAPER_LOAD__ = loadBook;
  window.__FD_PAPER_PAINT_HOME__ = paintTab;
  window.__FD_PAPER_PAINT_TAB__ = paintTab;
  window.__FD_PAPER_SCORECARD__ = weekScorecard;
  window.__FD_PAPER_WEEK_START__ = weekStartMs;
  window.__FD_PAPER_HARVEST__ = harvestAllMarks;
  window.__FD_PAPER_ON_DOC_CLICK__ = onDocumentClick;
  if (!window.__FD_PAPER_DOC_CLICK__) {
    window.__FD_PAPER_DOC_CLICK__ = true;
    document.addEventListener("click", function (ev) {
      if (typeof window.__FD_PAPER_ON_DOC_CLICK__ === "function") window.__FD_PAPER_ON_DOC_CLICK__(ev);
    }, true);
  }
  window.__FD_PAPER_BOUND__ = true;
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", install);
  else install();
  setTimeout(install, 0);
})();
""".strip()


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


def _find_tag_span(html_text: str, elem_id: str) -> tuple[int, int] | None:
    text = html_text or ""
    opener = re.compile(
        rf'<div\b(?=[^>]*\bid=["\']{re.escape(elem_id)}["\'])[^>]*>',
        re.I,
    )
    match = opener.search(text)
    if not match:
        return None
    start = match.start()
    depth = 1
    for tok in _DIV_TOKEN_RE.finditer(text, match.end()):
        closing = bool(tok.group(1))
        rest = tok.group(2) or ""
        self_close = rest.rstrip().endswith("/")
        if closing:
            depth -= 1
            if depth == 0:
                return start, tok.end()
        elif self_close:
            continue
        else:
            depth += 1
    return None


def _strip_home_host(html_text: str) -> str:
    """Remove leftover ``#fd-paper-home`` from top chrome / BOOK host."""
    text = html_text or ""
    span = _find_tag_span(text, HOME_HOST_ID)
    while span:
        start, end = span
        while end < len(text) and text[end] in " \t\r\n":
            end += 1
        text = text[:start] + text[end:]
        span = _find_tag_span(text, HOME_HOST_ID)
    text = re.sub(
        rf'<div\b[^>]*\bid=["\']{HOME_HOST_ID}["\'][^>]*>.*?</div>\s*',
        "",
        text,
        flags=re.I | re.S,
    )
    return text


def _has_nav_button(html_text: str) -> bool:
    text = html_text or ""
    patterns = (
        rf'<button\b[^>]*\bdata-view=["\']paper["\']',
        rf'<button\b[^>]*\bdata-fd-paper-nav=["\']1["\']',
        rf'<button\b[^>]*\bid=["\']{re.escape(NAV_ID)}["\']',
    )
    return any(re.search(pat, text, re.I | re.S) for pat in patterns)


def _ensure_nav(html_text: str) -> str:
    if _has_nav_button(html_text):
        return html_text
    pair = "\n  " + BTN_PAPER
    m = _EXPERIMENTAL_BTN_RE.search(html_text)
    if m:
        return html_text[: m.end()] + pair + html_text[m.end() :]
    m = _OPTIONS_VIEW_BTN_RE.search(html_text)
    if m:
        return html_text[: m.end()] + pair + html_text[m.end() :]
    if "<nav" in html_text.lower():
        return re.sub(r"(</nav>)", pair + r"\n\1", html_text, count=1, flags=re.I)
    return pair + "\n" + html_text


def _patch_setview_allowlist(html_text: str) -> str:
    text = html_text or ""
    if re.search(
        r"home\|mom-up\|mom-down\|outliers\|options(?:\|sectors)?(?:\|breakout\|breakdown)?(?:\|experimental)?\|paper",
        text,
        re.I,
    ):
        return text
    return _ALLOWLIST_RE.sub(lambda m: m.group(0) + "|paper", text)


def _early_return_snippet(marker: str, param: str) -> str:
    return (
        f"{marker}"
        f"if({param}===\"paper\"){{"
        f"if(window.__FD_PAPER_SHOW__)window.__FD_PAPER_SHOW__();"
        f"if(typeof syncNav===\"function\")syncNav();"
        f"return;}}"
    )


def _replace_or_inject_early_return(
    html_text: str,
    *,
    marker: str,
    fn_re: re.Pattern[str],
) -> str:
    text = html_text or ""
    existing = re.search(
        re.escape(marker) + r'if\((\w+)==="paper".*?return;\}',
        text,
        re.S,
    )
    if existing:
        return text[: existing.start()] + _early_return_snippet(marker, existing.group(1)) + text[existing.end() :]

    def inject(match: re.Match[str]) -> str:
        head, param = match.group(1), match.group(2)
        return f"{head}{_early_return_snippet(marker, param)}"

    return fn_re.sub(inject, text, count=1)


def _hideall_snippet() -> str:
    return (
        f"{HIDEALL_MARKER}"
        '["view-paper"].forEach(function(id){'
        "var el=document.getElementById(id);"
        'if(el){el.classList.add("hide");el.classList.remove("fd-paper-on");'
        'el.setAttribute("hidden","hidden");}'
        'if(document.body)document.body.removeAttribute("data-fd-paper");'
        "});"
    )


def _patch_hideall_panes(html_text: str) -> str:
    text = html_text or ""
    snippet = _hideall_snippet()
    existing = re.search(
        re.escape(HIDEALL_MARKER) + r'\["view-paper"\]\.forEach\(function\(id\)\{.*?\}\);',
        text,
        re.S,
    )
    if existing:
        return text[: existing.start()] + snippet + text[existing.end() :]
    return _HIDEALL_FN_RE.sub(lambda m: m.group(1) + snippet, text, count=1)


def _patch_view_id_lists(html_text: str) -> str:
    text = html_text or ""
    text = _VIEW_ID_ARRAY_RE.sub(
        r'\1,"view-paper"\2',
        text,
    )
    text = _VIEW_SEL_RE.sub(
        r"\1, #view-paper",
        text,
    )
    return text


def _patch_setview(html_text: str) -> str:
    text = _patch_setview_allowlist(html_text)
    text = _replace_or_inject_early_return(html_text=text, marker=SETVIEW_MARKER, fn_re=_SETVIEW_FN_RE)
    text = _replace_or_inject_early_return(html_text=text, marker=PAINTVIEW_MARKER, fn_re=_PAINTVIEW_FN_RE)
    text = _patch_hideall_panes(text)
    return _patch_view_id_lists(text)


def _insert_host(html_text: str, host: str) -> str:
    for elem_id in ("view-experimental", "view-breakdown", "view-mom-down", "view-mom-up"):
        span = _find_tag_span(html_text, elem_id)
        if span:
            return html_text[: span[1]] + "\n" + host + html_text[span[1] :]
    for pat in (
        r"(<nav\b[^>]*>.*?</nav>)",
        r"(<h1\b[^>]*>.*?</h1>)",
        r"(<body\b[^>]*>)",
    ):
        match = re.search(pat, html_text, re.I | re.S)
        if match:
            return html_text[: match.end()] + "\n" + host + html_text[match.end() :]
    return host + "\n" + html_text


def _ensure_panes(html_text: str, *, replace: bool = True) -> str:
    has_view = _find_tag_span(html_text, VIEW_ID) is not None
    if has_view and not replace:
        return html_text
    host = panes_html()
    span = _find_tag_span(html_text, VIEW_ID)
    if span:
        return html_text[: span[0]] + host + html_text[span[1] :]
    return _insert_host(html_text, host)


def _ensure_home_host(html_text: str) -> str:
    """Back-compat alias: strip chrome strip, do not re-inject it."""
    return _strip_home_host(html_text)


def _ensure_db(html_text: str, marks: Mapping[str, Any] | None) -> str:
    tag = embed_db(marks)
    if re.search(rf'id=["\']{DB_SCRIPT_ID}["\']', html_text, re.I):
        return re.sub(
            rf'<script\b[^>]*\bid=["\']{DB_SCRIPT_ID}["\'][^>]*>.*?</script>',
            lambda _m: tag,
            html_text,
            count=1,
            flags=re.I | re.S,
        )
    if "</body>" in html_text:
        return html_text.replace("</body>", tag + "\n</body>", 1)
    return html_text + tag


_ANY_SCRIPT_RE = re.compile(r"<script\b([^>]*)>(.*?)</script>\s*", re.I | re.S)


def _strip_stale_paper_scripts(html_text: str) -> str:
    """Drop leftover paper IIFEs that are not ``#fd-paper-js`` (old chip painter)."""
    text = html_text or ""
    chunks: list[str] = []
    last = 0
    for match in _ANY_SCRIPT_RE.finditer(text):
        attrs = match.group(1) or ""
        body = match.group(2) or ""
        if re.search(rf'\bid=["\']{JS_SCRIPT_ID}["\']', attrs, re.I):
            continue
        stale = (
            "__FD_PAPER_APPLY__" in body
            or ("fd-paper-book" in body and "function paintTab" in body)
            or ("function chipEl" in body and "fd-paper-chip" in body)
        )
        if not stale:
            continue
        chunks.append(text[last : match.start()])
        last = match.end()
    if not last:
        return text
    chunks.append(text[last:])
    return "".join(chunks)


def _ensure_js(html_text: str) -> str:
    script = f'<script id="{JS_SCRIPT_ID}">\n{strip_js()}\n</script>\n'
    text = _strip_stale_paper_scripts(html_text or "")
    text, n = re.subn(
        rf'<script\b[^>]*\bid=["\']{JS_SCRIPT_ID}["\'][^>]*>.*?</script>\s*',
        lambda _m: script,
        text,
        count=0,
        flags=re.I | re.S,
    )
    if n:
        return text
    if "</body>" in text:
        return text.replace("</body>", script + "</body>", 1)
    return text + script


def ensure_embedded(html_text: str, marks: Mapping[str, Any] | None = None) -> str:
    """CSS + marks db + Paper tab + cardHTML wrap JS. Safe on live ~2.7MB HTML.

    ``marks=None`` or ``{}`` still injects JS/CSS and **fills** ``#fd-paper-marks``
    from live ``window.MOM`` / ``MOM.cards`` last/PX_LAST/px (the ~4.8MB desk
    keeps prints there, not in enrich cards). Client harvest from MOM.cards
    is the fallback when the JSON db is empty. Pass ``marks_db(cards, book=book)``
    on a write so Refresh prints win when present.
    """
    text = html_text or ""
    harvested = harvest_html_marks(text)
    merged = merge_marks(harvested, marks)
    text = _strip_home_host(text)
    text = _ensure_nav(text)
    text = _ensure_css(text)
    text = _patch_setview(text)
    text = _ensure_panes(text, replace=True)
    text = _ensure_db(text, merged)
    text = _ensure_js(text)
    return text
