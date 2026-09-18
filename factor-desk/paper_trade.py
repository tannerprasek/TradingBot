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
``#view-paper``: open positions with live P&L % and inline **Close**,
ticker + Buy/Sell to open at the current mark, week scorecard since
Monday 00:00 ``America/Edmonton``, and collapsible closed trades.
Same ``fd-paper-book`` store as card Buy/Sell. Never inject
``#fd-paper-home`` into top chrome / BOOK delta.

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

# First finite > 0 wins. Live Refresh often writes px / PX_LAST / last.
MARK_KEYS: tuple[str, ...] = (
    "paper_mark",
    "px_last",
    "PX_LAST",
    "LAST_PRICE",
    "last_px",
    "last_price",
    "mark",
    "mark_px",
    "price",
    "px",
    "last",
    "close",
    "px_close",
    "adj_close",
    "PX_CLOSE",
)
SERIES_KEYS: tuple[str, ...] = (
    "px_series",
    "prices",
    "closes",
    "px",
    "last_refresh",
    "refresh_px",
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
        px = dapi_enrich.as_float(card.get(key))
        if px is not None and px > 0:
            return px
    for key in SERIES_KEYS:
        if key in MARK_KEYS and not isinstance(card.get(key), (list, tuple)):
            continue
        raw = card.get(key)
        if isinstance(raw, (list, tuple)) and raw:
            px = _series_last(raw)
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


def marks_db(
    cards: Iterable[Mapping[str, Any]] | None = None,
    book: Mapping[str, Any] | None = None,
) -> dict[str, float]:
    """ticker / short → mark, for ``#fd-paper-marks`` after Refresh."""
    out: dict[str, float] = {}

    def put(ticker: str, px: float | None) -> None:
        if not ticker or px is None or px <= 0:
            return
        out[ticker] = px
        short = _short(ticker)
        if short:
            out.setdefault(short, px)

    for card in cards or []:
        if not isinstance(card, Mapping):
            continue
        put(card_ticker(card), mark_of(card))
        put(str(card.get("t") or ""), mark_of(card))
    names = (book or {}).get("names") if isinstance(book, Mapping) else None
    if isinstance(names, dict):
        for ticker, rec in names.items():
            put(str(ticker), mark_of(rec if isinstance(rec, Mapping) else None))
    return out


def pnl_pct(side: str | None, entry: float | None, mark: float | None) -> float | None:
    """Signed percent. Long up = +, short down = +."""
    if side not in {"long", "short"}:
        return None
    entry_px = dapi_enrich.as_float(entry)
    mark_px = dapi_enrich.as_float(mark)
    if entry_px is None or mark_px is None or entry_px == 0:
        return None
    if side == "long":
        return (mark_px - entry_px) / entry_px * 100.0
    return (entry_px - mark_px) / entry_px * 100.0


def fmt_pct(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:+.2f}%"


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


def _open_row_html(row: Mapping[str, Any]) -> str:
    ret = row.get("pnl_pct")
    cls = "fd-paper-chip"
    if ret is not None:
        cls += " fd-paper-up" if ret >= 0 else " fd-paper-down"
    ticker = html.escape(str(row.get("ticker") or ""), quote=True)
    label = html.escape(str(row.get("label") or ticker))
    return (
        f'<div class="fd-paper-open-row" data-fd-paper-ticker="{ticker}">'
        f'<button type="button" class="{cls}" data-fd-paper-ticker="{ticker}">{label}</button>'
        f'<button type="button" class="fd-paper-btn fd-paper-close" data-fd-paper-close="1" '
        f'data-fd-paper-ticker="{ticker}">Close</button>'
        f"</div>"
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
    if opens:
        open_inner = "".join(_open_row_html(row) for row in opens)
    else:
        open_inner = f'<p class="fd-paper-home-empty">{html.escape(EMPTY_OPENS)}</p>'
    closed_bits: list[str] = []
    for row in closed:
        line = (
            f"{_date_label(row.get('closed_at'))} · {str(row.get('ticker') or '')} · "
            f"{str(row.get('side') or '').upper()} · {fmt_pct(_row_ret(row))}"
        )
        closed_bits.append(f"<li>{html.escape(line)}</li>")
    closed_items = "".join(closed_bits)
    n_closed = len(closed)
    summary = f"Closed trades ({n_closed})" if n_closed else "Closed trades"
    empty_cls = " fd-paper-home-empty" if score.get("empty") else ""
    tz = html.escape(WEEK_TZ_NAME, quote=True)
    return "\n".join(
        [
            f'<div id="{VIEW_ID}" class="view-pane hide" data-view="paper" hidden>',
            '  <div class="ph">Paper</div>',
            f'  <div class="fd-paper-tab" id="{TAB_ID}" role="region" '
            f'aria-label="Paper book" data-week-tz="{tz}">',
            '    <form class="fd-paper-open-form" id="fd-paper-open-form" action="javascript:void(0)">',
            '      <label class="fd-paper-open-label">Open',
            f'        <input id="{TICKER_INPUT_ID}" type="text" placeholder="ticker" '
            'autocomplete="off" spellcheck="false" />',
            "      </label>",
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
            f'    <details class="fd-paper-hist fd-paper-closed">',
            f"      <summary>{html.escape(summary)}</summary>",
            f'      <ul id="{CLOSED_ID}">{closed_items}</ul>',
            "    </details>",
            "  </div>",
            "</div>",
        ]
    )


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
  max-width: 720px;
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
.fd-paper-opens-sec, .fd-paper-week-sec {{
  display: flex;
  flex-direction: column;
  gap: 6px;
}}
.fd-paper-opens {{
  display: flex;
  flex-direction: column;
  gap: 6px;
}}
.fd-paper-open-row {{
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
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
  display: inline-block;
  font: 650 10px/1.15 "Segoe UI", "DejaVu Sans", "Noto Sans", ui-sans-serif, system-ui, sans-serif;
  letter-spacing: 0.03em;
  padding: 2px 7px;
  border-radius: 3px;
  border: 1px solid #4b5563;
  color: #d1d5db;
  background: #111827;
  cursor: pointer;
  font-variant-numeric: tabular-nums;
}}
.fd-paper-chip:hover {{ color: #fff; }}
.fd-paper-chip.fd-paper-up {{ color: #6ee7b7; border-color: #34d399; }}
.fd-paper-chip.fd-paper-down {{ color: #fda4af; border-color: #fb7185; }}
.fd-paper-home-empty, .fd-paper-home-score.fd-paper-home-empty {{
  color: #6b7280;
  letter-spacing: 0.03em;
}}
.fd-paper-home-score {{
  color: #9ca3af;
  font-variant-numeric: tabular-nums;
  letter-spacing: 0.03em;
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
    open_limit = str(int(OPEN_SHOW_MAX))
    return r"""
(function () {
  if (window.__FD_PAPER_BOUND__) return;
  window.__FD_PAPER_BOUND__ = true;
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
  var HOME_ID = "fd-paper-home";
  var VIEW = "view-paper";
  var NAV_ID = "fd-nav-paper";
  var OPENS_ID = "fd-paper-opens";
  var WEEK_ID = "fd-paper-week";
  var CLOSED_ID = "fd-paper-closed";
  var TICKER_ID = "fd-paper-ticker";
  var NATIVE_VIEWS = """ + json.dumps(list(NATIVE_VIEW_IDS)) + r""";
  var toastTimer = null;

  function $(id) { return document.getElementById(id); }
  function shortOf(t) { return String(t || "").trim().split(/\s+/)[0].toUpperCase(); }
  function num(v) {
    if (v == null || v === "") return null;
    if (typeof v === "number") return isFinite(v) && v > 0 ? v : null;
    var n = parseFloat(String(v).replace(/[, ]/g, ""));
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
  function marksDb() {
    var el = $(DB_ID);
    if (!el) return {};
    try { return JSON.parse(el.textContent || "{}") || {}; }
    catch (e) { return {}; }
  }
  function momCards() {
    var mom = window.MOM || {};
    if (Array.isArray(mom.cards)) return mom.cards;
    var out = [];
    ["up", "down", "flags", "watch", "outliers", "search"].forEach(function (k) {
      if (Array.isArray(mom[k])) out = out.concat(mom[k]);
    });
    if (Array.isArray(window.MOM_CARDS)) out = out.concat(window.MOM_CARDS);
    return out;
  }
  function findMomCard(ticker) {
    var want = shortOf(ticker);
    var cards = momCards();
    for (var i = 0; i < cards.length; i++) {
      var c = cards[i] || {};
      if (shortOf(c.t || c.ticker || c.name || c.symbol || "") === want) return c;
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
  function markFromCard(card) {
    if (!card) return null;
    var keys = ["paper_mark","px_last","PX_LAST","LAST_PRICE","last_px","last_price","mark","mark_px","price","px","last","close","px_close","adj_close","PX_CLOSE"];
    for (var i = 0; i < keys.length; i++) {
      var v = num(card[keys[i]]);
      if (v) return v;
    }
    var seriesKeys = ["px_series","prices","closes","last_refresh","refresh_px"];
    for (var j = 0; j < seriesKeys.length; j++) {
      var s = seriesLast(card[seriesKeys[j]]);
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
  function markOf(ticker, node, card) {
    var m = markFromCard(card);
    if (m) return m;
    m = markFromNode(node);
    if (m) return m;
    var db = marksDb();
    var key = shortOf(ticker);
    if (db[ticker]) return num(db[ticker]);
    if (db[key]) return num(db[key]);
    return markFromCard(findMomCard(ticker || key));
  }
  function pnlPct(side, entry, mark) {
    entry = Number(entry); mark = Number(mark);
    if (!isFinite(entry) || !isFinite(mark) || entry === 0) return null;
    if (side === "long") return (mark - entry) / entry * 100;
    if (side === "short") return (entry - mark) / entry * 100;
    return null;
  }
  function fmtPct(v) {
    if (v == null || !isFinite(v)) return "—";
    return (v >= 0 ? "+" : "") + v.toFixed(2) + "%";
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
  function chipEl(row, bookMark) {
    var btn = document.createElement("button");
    btn.type = "button";
    var ticker = shortOf(row.ticker || "");
    var mark = bookMark;
    if (mark == null) mark = markOf(ticker, null, findMomCard(ticker));
    var ret = pnlPct(row.side, row.entry, mark);
    var entry = Number(row.entry);
    btn.className = "fd-paper-chip";
    if (ret != null) btn.classList.add(ret >= 0 ? "fd-paper-up" : "fd-paper-down");
    btn.setAttribute("data-fd-paper-ticker", ticker);
    btn.textContent = ticker + " " + String(row.side || "").toUpperCase() + " @ " +
      (isFinite(entry) ? entry.toFixed(2) : "—") + "  " + fmtPct(ret);
    return btn;
  }
  function closeBtn(ticker) {
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "fd-paper-btn fd-paper-close";
    btn.setAttribute("data-fd-paper-close", "1");
    btn.setAttribute("data-fd-paper-ticker", ticker);
    btn.textContent = "Close";
    return btn;
  }
  function stripLegacyHome() {
    var host = $(HOME_ID);
    if (host && host.parentNode) host.parentNode.removeChild(host);
  }
  function paintTab() {
    stripLegacyHome();
    var opensEl = $(OPENS_ID);
    var weekEl = $(WEEK_ID);
    var closedEl = $(CLOSED_ID);
    if (!opensEl && !document.getElementById("view-paper")) return;
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
        opens.forEach(function (row) {
          var ticker = shortOf(row.ticker || "");
          var wrap = document.createElement("div");
          wrap.className = "fd-paper-open-row";
          wrap.setAttribute("data-fd-paper-ticker", ticker);
          wrap.appendChild(chipEl(row));
          wrap.appendChild(closeBtn(ticker));
          opensEl.appendChild(wrap);
        });
      }
    }
    if (weekEl) {
      weekEl.className = "fd-paper-home-score" + (sc.empty ? " fd-paper-home-empty" : "");
      weekEl.textContent = scoreLine(sc);
    }
    if (closedEl) {
      var details = closedEl.closest ? closedEl.closest("details") : null;
      var wasOpen = details ? details.open : false;
      closedEl.innerHTML = "";
      var closedMap = (book && book.closed) || {};
      var rows = [];
      Object.keys(closedMap).forEach(function (key) {
        var items = closedMap[key] || [];
        for (var i = 0; i < items.length; i++) {
          var row = items[i] || {};
          rows.push(row);
        }
      });
      rows.sort(function (a, b) {
        var ac = String(a.closed_at || ""), bc = String(b.closed_at || "");
        if (ac < bc) return 1; if (ac > bc) return -1;
        var at = String(a.ticker || ""), bt = String(b.ticker || "");
        return at < bt ? -1 : (at > bt ? 1 : 0);
      });
      for (var j = 0; j < rows.length; j++) {
        var c = rows[j] || {};
        var li = document.createElement("li");
        var r = (c.ret_pct != null) ? Number(c.ret_pct) : pnlPct(c.side, c.entry, c.exit);
        li.textContent = dateLabel(c.closed_at) + " · " + shortOf(c.ticker || "") + " · " +
          String(c.side || "").toUpperCase() + " · " + fmtPct(r);
        closedEl.appendChild(li);
      }
      if (details) {
        var sum = details.querySelector("summary");
        if (sum) sum.textContent = rows.length ? ("Closed trades (" + rows.length + ")") : "Closed trades";
        details.open = wasOpen;
      }
    }
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
    var pane = document.getElementById("view-paper");
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
    var view = (btn.getAttribute("data-view") || "").toLowerCase();
    if (view === "paper" || btn.getAttribute("data-fd-paper-nav") === "1" || btn.id === NAV_ID) return "paper";
    var label = (btn.textContent || "").replace(/\s+/g, " ").trim();
    if (label === "Paper") return "paper";
    if (btn.id === "refresh" || btn.id === "options-refresh") return "";
    if (btn.closest && btn.closest("#topnav, nav, .topnav") && (btn.classList.contains("btn") || btn.classList.contains("nav-btn") || view)) return "other";
    if (btn.classList && (btn.classList.contains("nav-btn") || view)) return "other";
    return "";
  }
  document.addEventListener("click", function (ev) {
    var t = ev.target && ev.target.closest ? ev.target.closest("button, [data-view], [data-fd-paper-nav]") : ev.target;
    var kind = kindOf(t);
    if (!kind) return;
    if (kind === "paper") {
      ev.preventDefault();
      ev.stopPropagation();
      showPaper(true);
      if (ev.stopImmediatePropagation) ev.stopImmediatePropagation();
      return;
    }
    if (kind === "other") showPaper(false);
  }, true);
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
  function applyTabTrade(ticker, act) {
    var key = shortOf(ticker);
    if (!key) { showToast("No ticker"); return; }
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
        var entry = Number(pos.entry);
        posEl.textContent = String(pos.side || "").toUpperCase() + " @ " + (isFinite(entry) ? entry.toFixed(2) : "—") + "  " + fmtPct(ret);
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
    var closeBtn = ev.target && ev.target.closest ? ev.target.closest("[data-fd-paper-close]") : null;
    if (closeBtn) {
      ev.preventDefault();
      ev.stopPropagation();
      if (ev.stopImmediatePropagation) ev.stopImmediatePropagation();
      var ct = closeBtn.getAttribute("data-fd-paper-ticker") || "";
      var bookC = loadBook();
      var posC = (bookC.positions || {})[shortOf(ct)];
      var actC = posC && posC.side === "short" ? "buy" : "sell";
      applyTabTrade(ct, actC);
      return;
    }
    var chip = ev.target && ev.target.closest ? ev.target.closest("#" + VIEW + " [data-fd-paper-ticker]") : null;
    if (chip && !chip.getAttribute("data-fd-paper-close") && !chip.getAttribute("data-fd-paper-act")) {
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
    if (window.__FD_PAPER_OBS__) return;
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
    paintAll();
    observe();
    if (!window.__FD_PAPER_CLICK__) {
      window.__FD_PAPER_CLICK__ = true;
      document.addEventListener("click", onPaperClick, true);
    }
    var form = $("fd-paper-open-form");
    if (form && !form.__fdPaperBound) {
      form.__fdPaperBound = true;
      form.addEventListener("submit", function (ev) {
        ev.preventDefault();
        applyTabTrade(tabTicker(), "buy");
      });
    }
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


def _ensure_js(html_text: str) -> str:
    script = f'<script id="{JS_SCRIPT_ID}">\n{strip_js()}\n</script>\n'
    text, n = re.subn(
        rf'<script\b[^>]*\bid=["\']{JS_SCRIPT_ID}["\'][^>]*>.*?</script>\s*',
        lambda _m: script,
        html_text,
        count=1,
        flags=re.I | re.S,
    )
    if n:
        return text
    if "</body>" in html_text:
        return html_text.replace("</body>", script + "</body>", 1)
    return html_text + script


def ensure_embedded(html_text: str, marks: Mapping[str, Any] | None = None) -> str:
    """CSS + marks db + Paper tab + cardHTML wrap JS. Safe on live ~2.7MB HTML.

    ``marks=None`` still injects JS/CSS so Buy/Sell hydrate from ``MOM.cards`` /
    localStorage after Refresh. Pass ``marks_db(cards, book=book)`` on a write.
    ``#view-paper`` is a top-nav tab (not a home chrome strip) and reads
    ``fd-paper-book``. Leftover ``#fd-paper-home`` is stripped.
    """
    text = html_text or ""
    text = _strip_home_host(text)
    text = _ensure_nav(text)
    text = _ensure_css(text)
    text = _patch_setview(text)
    text = _ensure_panes(text, replace=True)
    if marks is not None or not re.search(rf'id=["\']{DB_SCRIPT_ID}["\']', text, re.I):
        text = _ensure_db(text, marks if marks is not None else {})
    text = _ensure_js(text)
    return text
