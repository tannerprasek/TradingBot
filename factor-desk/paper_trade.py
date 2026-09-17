"""Factor Desk paper trading — 1-unit Buy/Sell on dense MOM cards.

Paper only. No brokerage. Positions persist in ``localStorage`` (UI) and an
optional ``paper_trades.json`` sidecar schema (same JSON) for later POST.

Rules (generic Buy / Sell, no size UI, no flip in one click)::

    Flat + Buy  → open LONG at mark
    Flat + Sell → open SHORT at mark
    Long + Sell → close long
    Short + Buy → close short
    Long + Buy / Short + Sell → no-op + toast ("already LONG/SHORT")

P&L % (1 unit notional)::

    long  = (mark - entry) / entry     # price up → +
    short = (entry - mark) / entry     # price down → +

Mark: first finite positive among card ``px_last`` / ``PX_LAST`` / last /
Refresh price fields / last ``px_series`` print / ``window.MOM.cards``.
Missing mark → buttons disabled with a title reason.

Live deploy: copy this module next to ``desk_dash.py`` and paste
``paper_trade.ensure_embedded`` on the ``write_combined`` tail. Do **not**
wholesale replace live ``desk_dash.py``. See ``DEPLOY-HOOKS.md`` §10.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, MutableMapping

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import dapi_enrich  # noqa: E402
import mom_streak  # noqa: E402

LOG = logging.getLogger("paper_trade")

BOOK_FILENAME = "paper_trades.json"
BOOK_VERSION = 1
UNIT = 1.0
MAX_CLOSED = 50
LS_KEY = "fd-paper-trade-v1"

DB_SCRIPT_ID = "fd-paper-db"
JS_SCRIPT_ID = "fd-paper-js"
CSS_STYLE_ID = "fd-paper-css"

NO_MARK_TITLE = (
    "no mark price (need card px_last / PX_LAST / last Refresh price)"
)
SAME_SIDE_LONG = "already LONG — Sell to close"
SAME_SIDE_SHORT = "already SHORT — Buy to close"

# Best-available last / mark on a MOM card or enrich rec.
MARK_KEYS: tuple[str, ...] = (
    "px_last",
    "PX_LAST",
    "LAST_PRICE",
    "last_price",
    "last",
    "price",
    "px",
    "close",
    "px_close",
    "PX_CLOSE",
    "mark",
    "mark_px",
    "last_px",
    "refresh_px",
    "pxLast",
    "lastPrice",
)
SERIES_KEYS: tuple[str, ...] = ("px_series", "prices", "closes", "px")
NEST_KEYS: tuple[str, ...] = ("quote", "last", "px", "price")


def _short(ticker: str) -> str:
    parts = (ticker or "").split()
    return parts[0].upper() if parts else ""


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _day(iso: str | None) -> str:
    text = str(iso or "").strip()
    if not text:
        return "—"
    if "T" in text:
        return text.split("T", 1)[0]
    return text[:10] if len(text) >= 10 else text


def empty_book() -> dict[str, Any]:
    return {
        "version": BOOK_VERSION,
        "paper": True,
        "unit": UNIT,
        "names": {},
    }


def empty_name(ticker: str) -> dict[str, Any]:
    short = _short(ticker)
    return {
        "t": short,
        "ticker": ticker or short,
        "open": None,
        "closed": [],
    }


def default_book_path(root: Path | None = None) -> Path:
    base = Path(root) if root is not None else HERE
    return base / BOOK_FILENAME


def normalize_book(book: Mapping[str, Any] | None) -> dict[str, Any]:
    """Coerce any blob to the sidecar / localStorage schema."""
    out = empty_book()
    if not isinstance(book, Mapping):
        return out
    names_src = book.get("names")
    if not isinstance(names_src, Mapping):
        names_src = {k: v for k, v in book.items() if k not in {"version", "paper", "unit", "names"}}
    for raw_key, raw in (names_src or {}).items():
        key = _short(str(raw_key))
        if not key:
            continue
        rec = _normalize_name(raw, key)
        if rec:
            out["names"][key] = rec
    if book.get("unit") is not None:
        try:
            out["unit"] = float(book.get("unit") or UNIT)
        except (TypeError, ValueError):
            out["unit"] = UNIT
    out["paper"] = True
    out["version"] = BOOK_VERSION
    return out


def _normalize_name(raw: Any, key: str) -> dict[str, Any] | None:
    rec = empty_name(key)
    if not isinstance(raw, Mapping):
        return rec
    rec["t"] = _short(str(raw.get("t") or raw.get("ticker") or key))
    rec["ticker"] = str(raw.get("ticker") or raw.get("t") or key)
    rec["open"] = _normalize_leg(raw.get("open"))
    closed: list[dict[str, Any]] = []
    src = raw.get("closed") or raw.get("history") or raw.get("trades") or []
    if isinstance(src, list):
        for item in src:
            leg = _normalize_leg(item, closed=True)
            if leg:
                closed.append(leg)
    rec["closed"] = closed[-MAX_CLOSED:]
    return rec


def _normalize_leg(raw: Any, *, closed: bool = False) -> dict[str, Any] | None:
    if not isinstance(raw, Mapping):
        return None
    side = str(raw.get("side") or "").strip().lower()
    if side in {"buy", "long", "l"}:
        side = "long"
    elif side in {"sell", "short", "s"}:
        side = "short"
    else:
        return None
    entry = dapi_enrich.as_float(raw.get("entry") or raw.get("open_px") or raw.get("px_open"))
    if entry is None or entry <= 0:
        return None
    out: dict[str, Any] = {
        "side": side,
        "entry": float(entry),
        "qty": UNIT,
        "opened_at": str(raw.get("opened_at") or raw.get("opened") or raw.get("date_opened") or ""),
    }
    if closed:
        exit_px = dapi_enrich.as_float(raw.get("exit") or raw.get("close_px") or raw.get("px_close") or raw.get("mark"))
        if exit_px is None or exit_px <= 0:
            return None
        out["exit"] = float(exit_px)
        out["closed_at"] = str(raw.get("closed_at") or raw.get("closed") or raw.get("date_closed") or "")
        stored = dapi_enrich.as_float(raw.get("pnl_pct") or raw.get("return_pct"))
        out["pnl_pct"] = stored if stored is not None else pnl_pct(side, entry, exit_px)
    return out


def load_book(path: Path | str | None = None, root: Path | None = None) -> dict[str, Any]:
    p = Path(path) if path is not None else default_book_path(root)
    if not p.is_file():
        return empty_book()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        LOG.warning("could not read %s: %s", p, exc)
        return empty_book()
    if not isinstance(data, dict):
        return empty_book()
    return normalize_book(data)


def write_book(
    book: Mapping[str, Any],
    path: Path | str | None = None,
    root: Path | None = None,
) -> Path:
    p = Path(path) if path is not None else default_book_path(root)
    blob = normalize_book(book)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(blob, indent=2, default=str) + "\n", encoding="utf-8")
    os.replace(tmp, p)
    return p


def pnl_pct(side: str | None, entry: float | None, mark: float | None) -> float | None:
    """Signed return. Long profits when price rises; short profits when it falls."""
    if side not in {"long", "short"}:
        return None
    entry_f = dapi_enrich.as_float(entry)
    mark_f = dapi_enrich.as_float(mark)
    if entry_f is None or mark_f is None or entry_f <= 0:
        return None
    if side == "long":
        return (mark_f - entry_f) / entry_f
    return (entry_f - mark_f) / entry_f


def format_pnl_pct(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value * 100:+.2f}%"


def _series_last(raw: Any) -> tuple[float | None, str | None]:
    if not isinstance(raw, list) or not raw:
        return None, None
    last = raw[-1]
    if isinstance(last, (int, float)) and not isinstance(last, bool):
        px = dapi_enrich.as_float(last)
        return (px, "series[-1]") if px is not None and px > 0 else (None, None)
    if isinstance(last, (list, tuple)) and len(last) >= 2:
        px = dapi_enrich.as_float(last[1])
        return (px, "series[-1][1]") if px is not None and px > 0 else (None, None)
    if isinstance(last, Mapping):
        for key in ("px_last", "px", "close", "last", "value", "price", "PX_LAST"):
            px = dapi_enrich.as_float(last.get(key))
            if px is not None and px > 0:
                return px, f"series[-1].{key}"
    return None, None


def resolve_mark(
    card: Mapping[str, Any] | None = None,
    rec: Mapping[str, Any] | None = None,
) -> tuple[float | None, str | None]:
    """Best available last/mark. Missing → ``(None, None)`` (do not invent)."""
    for src, prefix in ((card, "card"), (rec, "rec")):
        if not isinstance(src, Mapping):
            continue
        for key in MARK_KEYS:
            px = dapi_enrich.as_float(src.get(key))
            if px is not None and px > 0:
                return px, f"{prefix}.{key}"
        nested_mark = src.get("mark")
        if isinstance(nested_mark, Mapping):
            px = dapi_enrich.as_float(nested_mark.get("px") or nested_mark.get("last"))
            if px is not None and px > 0:
                return px, f"{prefix}.mark"
        for nest in NEST_KEYS:
            nested = src.get(nest)
            if not isinstance(nested, Mapping):
                continue
            for key in MARK_KEYS:
                px = dapi_enrich.as_float(nested.get(key))
                if px is not None and px > 0:
                    return px, f"{prefix}.{nest}.{key}"
        for skey in SERIES_KEYS:
            px, why = _series_last(src.get(skey))
            if px is not None:
                return px, f"{prefix}.{skey}/{why}"
    return None, None


def attach_mark(
    card: MutableMapping[str, Any],
    rec: Mapping[str, Any] | None = None,
) -> MutableMapping[str, Any]:
    """Stamp ``px_last`` / ``fd_paper_mark`` from best available card or enrich rec."""
    mark, src = resolve_mark(card, rec)
    card["fd_paper_mark"] = mark
    card["fd_paper_mark_src"] = src
    if mark is not None and card.get("px_last") is None:
        card["px_last"] = mark
    return card


def apply_click(
    book: Mapping[str, Any] | None,
    ticker: str,
    action: str,
    mark: float | None,
    *,
    now: str | None = None,
) -> dict[str, Any]:
    """Apply Buy/Sell. Never flips in one click. Same-side is a documented no-op."""
    blob = normalize_book(book)
    key = _short(ticker)
    action_n = str(action or "").strip().lower()
    ts = now or _now_iso()
    if not key:
        return {
            "ok": False,
            "action": "blocked",
            "reason": "no_ticker",
            "toast": "no ticker",
            "book": blob,
            "name": None,
        }
    rec = blob["names"].setdefault(key, empty_name(ticker or key))
    rec["ticker"] = rec.get("ticker") or ticker or key
    if action_n not in {"buy", "sell"}:
        return {
            "ok": False,
            "action": "blocked",
            "reason": "bad_action",
            "toast": "Buy or Sell only",
            "book": blob,
            "name": rec,
        }
    px = dapi_enrich.as_float(mark)
    if px is None or px <= 0:
        return {
            "ok": False,
            "action": "blocked",
            "reason": "no_mark",
            "toast": NO_MARK_TITLE,
            "book": blob,
            "name": rec,
        }
    opened = rec.get("open") if isinstance(rec.get("open"), Mapping) else None
    side = str((opened or {}).get("side") or "")
    if opened is None:
        new_side = "long" if action_n == "buy" else "short"
        rec["open"] = {
            "side": new_side,
            "entry": float(px),
            "qty": UNIT,
            "opened_at": ts,
        }
        verb = "open_long" if new_side == "long" else "open_short"
        return {
            "ok": True,
            "action": verb,
            "reason": verb,
            "toast": f"opened {new_side.upper()} @ {px:g}",
            "book": blob,
            "name": rec,
        }
    if action_n == "buy" and side == "long":
        return {
            "ok": True,
            "action": "noop",
            "reason": "same_side",
            "toast": SAME_SIDE_LONG,
            "book": blob,
            "name": rec,
        }
    if action_n == "sell" and side == "short":
        return {
            "ok": True,
            "action": "noop",
            "reason": "same_side",
            "toast": SAME_SIDE_SHORT,
            "book": blob,
            "name": rec,
        }
    # Cross click closes. Do not open the other side in this click.
    entry = dapi_enrich.as_float(opened.get("entry"))
    closed_side = side if side in {"long", "short"} else ("long" if action_n == "sell" else "short")
    ret = pnl_pct(closed_side, entry, px)
    row = {
        "side": closed_side,
        "entry": float(entry) if entry is not None else float(px),
        "exit": float(px),
        "qty": UNIT,
        "pnl_pct": ret,
        "opened_at": str(opened.get("opened_at") or ""),
        "closed_at": ts,
    }
    closed = list(rec.get("closed") or [])
    closed.append(row)
    rec["closed"] = closed[-MAX_CLOSED:]
    rec["open"] = None
    verb = "close_long" if closed_side == "long" else "close_short"
    shown = format_pnl_pct(ret)
    return {
        "ok": True,
        "action": verb,
        "reason": verb,
        "toast": f"closed {closed_side.upper()} {shown}",
        "book": blob,
        "name": rec,
        "closed": row,
    }


def chrome_host_html(card: Mapping[str, Any] | None = None) -> str:
    """Empty host; JS fills Buy/Sell + position + history. Safe in cardHTML."""
    _ = card
    return '<div class="fd-paper" data-fd-paper="1"></div>'


def _script_json(blob: str) -> str:
    return (blob or "").replace("</", "<\\/")


def slim_payload(book: Mapping[str, Any] | None) -> dict[str, Any]:
    return normalize_book(book)


def embed_db(book: Mapping[str, Any] | None) -> str:
    blob = json.dumps(slim_payload(book), separators=(",", ":"), ensure_ascii=True, default=str)
    return f'<script type="application/json" id="{DB_SCRIPT_ID}">{_script_json(blob)}</script>'


def strip_css() -> str:
    return """
.fd-paper {
  margin: 8px 0 0;
  padding-top: 8px;
  border-top: 1px solid #1f2937;
  font: 650 11px/1.25 "Segoe UI", "DejaVu Sans", "Noto Sans", ui-sans-serif, system-ui, sans-serif;
}
.fd-paper-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}
.fd-paper-btn {
  display: inline-block;
  font: inherit;
  letter-spacing: 0.04em;
  padding: 3px 10px;
  border-radius: 3px;
  cursor: pointer;
  text-transform: none;
}
.fd-paper-buy {
  color: #6ee7b7;
  border: 1px solid #34d399;
  background: #064e3b;
}
.fd-paper-sell {
  color: #fda4af;
  border: 1px solid #fb7185;
  background: #3f1d1d;
}
.fd-paper-btn:hover { color: #fff; }
.fd-paper-btn:disabled,
.fd-paper-btn[disabled] {
  opacity: 0.45;
  cursor: not-allowed;
}
.fd-paper-note {
  color: #6b7280;
  font-weight: 500;
  letter-spacing: 0.03em;
}
.fd-paper-pos {
  margin: 6px 0 0;
  font-variant-numeric: tabular-nums;
  letter-spacing: 0.03em;
}
.fd-paper-pos.is-long { color: #6ee7b7; }
.fd-paper-pos.is-short { color: #fda4af; }
.fd-paper-pos.is-up { color: #6ee7b7; }
.fd-paper-pos.is-down { color: #fda4af; }
.fd-paper-hist {
  margin: 6px 0 0;
  color: #9ca3af;
}
.fd-paper-hist > summary {
  cursor: pointer;
  list-style: none;
  font-weight: 650;
  letter-spacing: 0.03em;
}
.fd-paper-hist > summary::-webkit-details-marker { display: none; }
.fd-paper-hist-list {
  margin: 4px 0 0;
  padding: 0 0 0 14px;
  font-weight: 500;
  font-variant-numeric: tabular-nums;
}
.fd-paper-hist-list li { margin: 2px 0; }
.fd-paper-hist-list .is-up { color: #6ee7b7; }
.fd-paper-hist-list .is-down { color: #fda4af; }
#fd-paper-toast {
  position: fixed;
  right: 16px;
  bottom: 16px;
  z-index: 80;
  max-width: 280px;
  padding: 8px 12px;
  border-radius: 4px;
  border: 1px solid #4b5563;
  background: #111827;
  color: #e5e7eb;
  font: 650 12px/1.3 "Segoe UI", "DejaVu Sans", "Noto Sans", ui-sans-serif, system-ui, sans-serif;
  box-shadow: 0 8px 24px rgba(0,0,0,.4);
}
#fd-paper-toast[hidden], #fd-paper-toast:not(.is-on) { display: none !important; }
""".strip()


def strip_js() -> str:
    """Wrap live ``cardHTML`` + paint existing dense cards. localStorage is UI source of truth.

    Same-side click while open is a no-op + toast (does not flip).
    """
    no_mark = json.dumps(NO_MARK_TITLE)
    same_long = json.dumps(SAME_SIDE_LONG)
    same_short = json.dumps(SAME_SIDE_SHORT)
    ls_key = json.dumps(LS_KEY)
    max_closed = int(MAX_CLOSED)
    return rf"""
(function () {{
  if (window.__FD_PAPER_BOUND__) return;
  window.__FD_PAPER_BOUND__ = true;
  var LS_KEY = {ls_key};
  var DB_ID = "fd-paper-db";
  var NO_MARK = {no_mark};
  var SAME_LONG = {same_long};
  var SAME_SHORT = {same_short};
  var MAX_CLOSED = {max_closed};
  var MARK_KEYS = ["px_last","PX_LAST","LAST_PRICE","last_price","last","price","px","close","px_close","PX_CLOSE","mark","mark_px","last_px","refresh_px","pxLast","lastPrice"];

  function $(id) {{ return document.getElementById(id); }}
  function shortOf(t) {{ return String(t || "").trim().split(/\s+/)[0].toUpperCase(); }}
  function num(v) {{
    if (v == null || v === "") return null;
    if (typeof v === "number") return (isFinite(v) && v > 0) ? v : null;
    var n = Number(v);
    return (isFinite(n) && n > 0) ? n : null;
  }}
  /* P&L sign: long up = + ; short down = + */
  function pnlPct(side, entry, mark) {{
    entry = num(entry); mark = num(mark);
    if (!entry || !mark) return null;
    if (side === "long") return (mark - entry) / entry;
    if (side === "short") return (entry - mark) / entry;
    return null;
  }}
  function fmtPct(x) {{
    if (x == null || !isFinite(x)) return "—";
    var p = x * 100;
    return (p >= 0 ? "+" : "") + p.toFixed(2) + "%";
  }}
  function fmtPx(x) {{
    x = num(x);
    if (x == null) return "—";
    if (x >= 1000) return x.toFixed(2);
    if (x >= 1) return String(Math.round(x * 10000) / 10000);
    return String(x);
  }}
  function dayOf(iso) {{
    var s = String(iso || "").trim();
    if (!s) return "—";
    var i = s.indexOf("T");
    return i > 0 ? s.slice(0, i) : s.slice(0, 10);
  }}
  function seed() {{
    var el = $(DB_ID);
    if (!el) return {{ version: 1, paper: true, unit: 1, names: {{}} }};
    try {{ return JSON.parse(el.textContent || "{{}}") || {{ version: 1, paper: true, unit: 1, names: {{}} }}; }}
    catch (e) {{ return {{ version: 1, paper: true, unit: 1, names: {{}} }}; }}
  }}
  function loadBook() {{
    try {{
      var raw = localStorage.getItem(LS_KEY);
      if (raw) {{
        var parsed = JSON.parse(raw);
        if (parsed && typeof parsed === "object") {{
          if (!parsed.names) parsed.names = {{}};
          parsed.paper = true;
          return parsed;
        }}
      }}
    }} catch (e) {{}}
    var s = seed();
    if (!s.names) s.names = {{}};
    s.paper = true;
    return s;
  }}
  function saveBook(book) {{
    try {{ localStorage.setItem(LS_KEY, JSON.stringify(book)); }} catch (e) {{}}
  }}
  var BOOK = loadBook();

  function nameOf(t) {{
    var k = shortOf(t);
    if (!k) return null;
    BOOK.names = BOOK.names || {{}};
    if (!BOOK.names[k]) BOOK.names[k] = {{ t: k, ticker: t || k, open: null, closed: [] }};
    return BOOK.names[k];
  }}
  function seriesLast(raw) {{
    if (!Array.isArray(raw) || !raw.length) return null;
    var last = raw[raw.length - 1];
    if (typeof last === "number") return num(last);
    if (Array.isArray(last) && last.length >= 2) return num(last[1]);
    if (last && typeof last === "object") {{
      return num(last.px_last || last.px || last.close || last.last || last.value || last.price || last.PX_LAST);
    }}
    return null;
  }}
  function markFromObj(c) {{
    if (!c || typeof c !== "object") return null;
    for (var i = 0; i < MARK_KEYS.length; i++) {{
      var v = num(c[MARK_KEYS[i]]);
      if (v) return v;
    }}
    if (c.mark && typeof c.mark === "object") {{
      var m = num(c.mark.px || c.mark.last);
      if (m) return m;
    }}
    var nests = [c.quote, c.last, c.px, c.price];
    for (var n = 0; n < nests.length; n++) {{
      var nested = nests[n];
      if (!nested || typeof nested !== "object") continue;
      for (var j = 0; j < MARK_KEYS.length; j++) {{
        var nv = num(nested[MARK_KEYS[j]]);
        if (nv) return nv;
      }}
    }}
    return seriesLast(c.px_series) || seriesLast(c.prices) || seriesLast(c.closes);
  }}
  function momCards() {{
    var mom = window.MOM || {{}};
    if (Array.isArray(mom.cards)) return mom.cards;
    if (Array.isArray(mom.up) && Array.isArray(mom.down)) return mom.up.concat(mom.down);
    if (Array.isArray(window.MOM_CARDS)) return window.MOM_CARDS;
    return [];
  }}
  function findMomCard(ticker) {{
    var want = shortOf(ticker);
    var cards = momCards();
    for (var i = 0; i < cards.length; i++) {{
      var c = cards[i] || {{}};
      var t = shortOf(c.t || c.ticker || c.name || c.symbol || "");
      if (t && t === want) return c;
    }}
    return null;
  }}
  function tickerOf(node, card) {{
    if (card) {{
      var ct = shortOf(card.t || card.ticker || card.name || card.symbol || "");
      if (ct) return ct;
    }}
    if (!node || !node.getAttribute) return "";
    return shortOf(node.getAttribute("data-t") || node.getAttribute("data-ticker") || node.getAttribute("data-name") || "");
  }}
  function markOf(node, card) {{
    var fromCard = markFromObj(card);
    if (fromCard) return fromCard;
    if (node && node.getAttribute) {{
      var attr = num(node.getAttribute("data-px-last") || node.getAttribute("data-px") || node.getAttribute("data-last"));
      if (attr) return attr;
    }}
    return markFromObj(findMomCard(tickerOf(node, card)));
  }}
  function nowIso() {{ return new Date().toISOString().replace(/\.\d+Z$/, "Z"); }}

  function applyClick(ticker, action, mark) {{
    var rec = nameOf(ticker);
    if (!rec) return {{ ok: false, toast: "no ticker", action: "blocked" }};
    if (action !== "buy" && action !== "sell") return {{ ok: false, toast: "Buy or Sell only", action: "blocked" }};
    mark = num(mark);
    if (!mark) return {{ ok: false, toast: NO_MARK, action: "blocked" }};
    var opened = rec.open;
    if (!opened) {{
      rec.open = {{ side: action === "buy" ? "long" : "short", entry: mark, qty: 1, opened_at: nowIso() }};
      var side = rec.open.side;
      return {{ ok: true, action: side === "long" ? "open_long" : "open_short", toast: "opened " + side.toUpperCase() + " @ " + fmtPx(mark) }};
    }}
    if (action === "buy" && opened.side === "long") return {{ ok: true, action: "noop", toast: SAME_LONG }};
    if (action === "sell" && opened.side === "short") return {{ ok: true, action: "noop", toast: SAME_SHORT }};
    var closedSide = opened.side;
    var ret = pnlPct(closedSide, opened.entry, mark);
    rec.closed = rec.closed || [];
    rec.closed.push({{
      side: closedSide,
      entry: opened.entry,
      exit: mark,
      qty: 1,
      pnl_pct: ret,
      opened_at: opened.opened_at || "",
      closed_at: nowIso()
    }});
    if (rec.closed.length > MAX_CLOSED) rec.closed = rec.closed.slice(-MAX_CLOSED);
    rec.open = null;
    return {{ ok: true, action: closedSide === "long" ? "close_long" : "close_short", toast: "closed " + String(closedSide).toUpperCase() + " " + fmtPct(ret) }};
  }}

  function toast(msg) {{
    if (!msg) return;
    var el = $("fd-paper-toast");
    if (!el) {{
      el = document.createElement("div");
      el.id = "fd-paper-toast";
      (document.body || document.documentElement).appendChild(el);
    }}
    el.textContent = msg;
    el.hidden = false;
    el.classList.add("is-on");
    clearTimeout(toast._t);
    toast._t = setTimeout(function () {{ el.classList.remove("is-on"); el.hidden = true; }}, 2200);
  }}

  function shellHtml() {{
    return '<div class="fd-paper-actions">' +
      '<button type="button" class="fd-paper-btn fd-paper-buy" data-fd-paper-act="buy">Buy</button>' +
      '<button type="button" class="fd-paper-btn fd-paper-sell" data-fd-paper-act="sell">Sell</button>' +
      '<span class="fd-paper-note">paper · 1 unit</span></div>' +
      '<div class="fd-paper-pos" hidden></div>' +
      '<details class="fd-paper-hist"><summary>previous closed trades</summary>' +
      '<ul class="fd-paper-hist-list"></ul></details>';
  }}

  function ensureChrome(node) {{
    if (!node || !node.querySelector) return null;
    var host = node.querySelector(":scope > .fd-paper, .fd-paper");
    if (!host) {{
      host = document.createElement("div");
      host.className = "fd-paper";
      host.setAttribute("data-fd-paper", "1");
      node.appendChild(host);
    }}
    if (!host.querySelector("[data-fd-paper-act]")) host.innerHTML = shellHtml();
    return host;
  }}

  function paintHost(host, ticker, mark) {{
    if (!host) return;
    var rec = nameOf(ticker);
    var buy = host.querySelector('[data-fd-paper-act="buy"]');
    var sell = host.querySelector('[data-fd-paper-act="sell"]');
    var noMark = mark == null;
    if (buy) {{ buy.disabled = noMark; buy.title = noMark ? NO_MARK : "paper Buy (1 unit)"; }}
    if (sell) {{ sell.disabled = noMark; sell.title = noMark ? NO_MARK : "paper Sell (1 unit)"; }}
    var pos = host.querySelector(".fd-paper-pos");
    if (pos) {{
      if (!rec || !rec.open) {{
        pos.hidden = true;
        pos.textContent = "";
        pos.className = "fd-paper-pos";
      }} else {{
        var side = rec.open.side;
        var entry = rec.open.entry;
        var pnl = pnlPct(side, entry, mark);
        pos.hidden = false;
        var cls = "fd-paper-pos " + (side === "short" ? "is-short" : "is-long");
        if (pnl != null) cls += pnl >= 0 ? " is-up" : " is-down";
        pos.className = cls;
        pos.textContent = String(side || "").toUpperCase() + " · " + fmtPx(entry) + (pnl == null ? "" : " · " + fmtPct(pnl));
      }}
    }}
    var ul = host.querySelector(".fd-paper-hist-list");
    var sum = host.querySelector(".fd-paper-hist > summary");
    var closed = (rec && rec.closed) ? rec.closed.slice() : [];
    if (sum) sum.textContent = "previous closed trades" + (closed.length ? " (" + closed.length + ")" : "");
    if (ul) {{
      ul.innerHTML = "";
      if (!closed.length) {{
        var empty = document.createElement("li");
        empty.textContent = "none yet";
        ul.appendChild(empty);
      }} else {{
        for (var i = closed.length - 1; i >= 0; i--) {{
          var row = closed[i] || {{}};
          var li = document.createElement("li");
          var ret = (row.pnl_pct != null) ? row.pnl_pct : pnlPct(row.side, row.entry, row.exit);
          li.className = ret != null && ret < 0 ? "is-down" : "is-up";
          li.textContent = dayOf(row.opened_at) + " → " + dayOf(row.closed_at) + " · " +
            String(row.side || "").toUpperCase() + " · " + fmtPct(ret);
          ul.appendChild(li);
        }}
      }}
    }}
  }}

  function paintNode(node, card) {{
    if (!node || node.nodeType !== 1) return;
    if (node.closest && node.closest("nav, .topnav, #gics-filter-strip, #fd-book-delta, #refresh, #options-refresh")) return;
    var t = tickerOf(node, card);
    if (!t) return;
    var host = ensureChrome(node);
    var mark = markOf(node, card);
    if (mark && node.setAttribute) node.setAttribute("data-px-last", String(mark));
    paintHost(host, t, mark);
  }}

  function cardNodes() {{
    return document.querySelectorAll("article.card, article[data-t], article[data-ticker], .card[data-t], .card[data-ticker]");
  }}
  function applyAll() {{
    var nodes = cardNodes();
    for (var i = 0; i < nodes.length; i++) paintNode(nodes[i]);
  }}

  function spliceChrome(html, card) {{
    html = String(html || "");
    if (html.indexOf("data-fd-paper") >= 0) return html;
    var chrome = '<div class="fd-paper" data-fd-paper="1">' + shellHtml() + "</div>";
    if (/<\/article>\s*$/i.test(html)) return html.replace(/<\/article>\s*$/i, chrome + "</article>");
    return html + chrome;
  }}
  window.__FD_PAPER_CHROME__ = function (card) {{
    return '<div class="fd-paper" data-fd-paper="1">' + shellHtml() + "</div>";
  }};
  window.__FD_PAPER_PNL_PCT__ = pnlPct;
  window.__FD_PAPER_APPLY__ = applyClick;
  function wrapCardHTML() {{
    var orig = window.cardHTML;
    if (typeof orig !== "function" || orig.__fdPaper) return typeof orig === "function";
    window.cardHTML = function (card) {{
      var html = orig.apply(this, arguments);
      var out = spliceChrome(html, card);
      return out;
    }};
    window.cardHTML.__fdPaper = true;
    return true;
  }}
  var wrapTries = 0;
  function waitWrap() {{
    if (wrapCardHTML()) return;
    if (++wrapTries < 80) setTimeout(waitWrap, 50);
  }}
  waitWrap();

  document.addEventListener("click", function (ev) {{
    var t = ev.target && ev.target.closest ? ev.target.closest("[data-fd-paper-act], .fd-paper details, .fd-paper summary") : null;
    if (!t) return;
    ev.stopPropagation();
    if (ev.stopImmediatePropagation) ev.stopImmediatePropagation();
    var btn = ev.target.closest ? ev.target.closest("[data-fd-paper-act]") : null;
    if (!btn) return;
    ev.preventDefault();
    var card = btn.closest("article, .card");
    var ticker = tickerOf(card, null);
    var act = (btn.getAttribute("data-fd-paper-act") || "").toLowerCase();
    var mark = markOf(card, findMomCard(ticker));
    var res = applyClick(ticker, act, mark);
    saveBook(BOOK);
    toast(res && res.toast);
    applyAll();
  }}, true);

  var obs = null;
  function armObserver() {{
    if (obs || !window.MutationObserver || !document.body) return;
    obs = new MutationObserver(function (muts) {{
      for (var i = 0; i < muts.length; i++) {{
        var nodes = muts[i].addedNodes || [];
        for (var j = 0; j < nodes.length; j++) {{
          var n = nodes[j];
          if (!n || n.nodeType !== 1) continue;
          if (n.matches && n.matches("article.card, article[data-t], .card[data-t]")) paintNode(n);
          if (n.querySelectorAll) {{
            var inner = n.querySelectorAll("article.card, article[data-t], .card[data-t], .card[data-ticker]");
            for (var k = 0; k < inner.length; k++) paintNode(inner[k]);
          }}
        }}
      }}
    }});
    obs.observe(document.body, {{ childList: true, subtree: true }});
  }}
  function boot() {{
    wrapCardHTML();
    applyAll();
    armObserver();
  }}
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
}})();
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


def _ensure_db(html_text: str, book: Mapping[str, Any] | None) -> str:
    tag = embed_db(book)
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


def _ensure_toast(html_text: str) -> str:
    if re.search(r'id=["\']fd-paper-toast["\']', html_text, re.I):
        return html_text
    host = '<div id="fd-paper-toast" hidden></div>\n'
    if "</body>" in html_text:
        return html_text.replace("</body>", host + "</body>", 1)
    return html_text + host


def ensure_embedded(html_text: str, book: Mapping[str, Any] | None = None) -> str:
    """CSS + seed db + JS wrap of ``cardHTML``. Safe on live ~2.7MB HTML.

    ``book=None`` still embeds an empty paper book so JS can boot from
    localStorage. Pass ``load_book(root=...)`` on a live write to seed.
    """
    text = html_text or ""
    text = _ensure_css(text)
    text = _ensure_toast(text)
    text = _ensure_db(text, book if book is not None else empty_book())
    text = _ensure_js(text)
    return text
