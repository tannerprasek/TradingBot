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

Live FLAGS/WATCH/MOM chrome is left alone except this card strip. Recopy
``paper_trade.py`` to Desktop; do **not** wholesale replace ``desk_dash.py``.
"""

from __future__ import annotations

import html
import json
import re
import sys
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, MutableMapping, Sequence

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

NO_MARK_REASON = "No mark price — need card price / PX_LAST / last Refresh print"
TOAST_ALREADY_LONG = "Already long — sell to close"
TOAST_ALREADY_SHORT = "Already short — buy to close"

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
""".strip()


def strip_js() -> str:
    """Wrap live ``cardHTML`` + hydrate existing articles. localStorage persists Refresh."""
    no_mark = json.dumps(NO_MARK_REASON, ensure_ascii=False)
    already_long = json.dumps(TOAST_ALREADY_LONG, ensure_ascii=False)
    already_short = json.dumps(TOAST_ALREADY_SHORT, ensure_ascii=False)
    storage = json.dumps(STORAGE_KEY, ensure_ascii=False)
    kind = json.dumps(BOOK_KIND, ensure_ascii=False)
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
    toastTimer = setTimeout(function () { el.hidden = true; }, 1600);
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
  function bindHost(host, node, ticker) {
    if (host.__fdPaperBound) return;
    host.__fdPaperBound = true;
    host.addEventListener("click", function (ev) {
      ev.stopPropagation();
    }, true);
    host.addEventListener("click", function (ev) {
      var btn = ev.target && ev.target.closest ? ev.target.closest("[data-fd-paper-act]") : null;
      if (!btn) return;
      ev.preventDefault();
      ev.stopPropagation();
      if (ev.stopImmediatePropagation) ev.stopImmediatePropagation();
      var act = btn.getAttribute("data-fd-paper-act");
      var t = tickerOf(node, null) || ticker;
      var card = findMomCard(t);
      var mark = markOf(t, node, card);
      var book = loadBook();
      var res = applyClick(book, t, act, mark);
      if (res.toast) showToast(res.toast);
      if (res.ok && res.action !== "noop") saveBook(book);
      else if (res.ok && res.action === "noop") { /* same-side: no persist change */ }
      paintAll();
    });
  }
  function hydrateCard(node, card) {
    if (!node || !node.querySelector) return;
    if (node.closest && node.closest("nav, .topnav, #gics-filter-strip, #fd-book-delta")) return;
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
  function paintAll() {
    var nodes = document.querySelectorAll("article.card, article[data-t], article[data-ticker], .grid .card, .grid.dense .card");
    for (var i = 0; i < nodes.length; i++) hydrateCard(nodes[i], null);
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
    window.__FD_PAPER_OBS__ = new MutationObserver(function () { paintAll(); });
    window.__FD_PAPER_OBS__.observe(document.body, { childList: true, subtree: true });
  }
  function install() {
    wrapCardHTML();
    paintAll();
    observe();
  }
  window.__FD_PAPER_APPLY__ = applyClick;
  window.__FD_PAPER_PNL__ = pnlPct;
  window.__FD_PAPER_MARK__ = markOf;
  window.__FD_PAPER_PAINT__ = paintAll;
  window.__FD_PAPER_LOAD__ = loadBook;
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
    """CSS + marks db + cardHTML wrap JS. Safe on live ~2.7MB HTML.

    ``marks=None`` still injects JS/CSS so Buy/Sell hydrate from ``MOM.cards`` /
    localStorage after Refresh. Pass ``marks_db(cards, book=book)`` on a write.
    """
    text = html_text or ""
    text = _ensure_css(text)
    if marks is not None or not re.search(rf'id=["\']{DB_SCRIPT_ID}["\']', text, re.I):
        text = _ensure_db(text, marks if marks is not None else {})
    text = _ensure_js(text)
    return text
