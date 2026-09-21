"""Portable MOM card renderer for Factor Desk tabs.

Live Momentum Up/Down chrome (``cardHTML``) is the gold standard. Tabs must
not invent a second card skin. This module:

1. Wraps live ``window.cardHTML`` (or installs a MOM-shaped fallback).
2. Looks up the live MOM card by ticker and passes that object through the
   same ``cardHTML`` so Breakout/Breakdown match Momentum chrome.
3. Only polishes empty stats / ATR% (``atr_pct`` is already percent points —
   do not ×100 again). Does not strip the digest pill grid.

Does not wholesale replace live ~2.7–4.8MB ``factorbook.html``. Recopy this
module next to live ``desk_dash.py`` and call ``ensure_embedded``.
"""

from __future__ import annotations

import html
import json
import math
import re
from typing import Any, Mapping, MutableMapping

CSS_STYLE_ID = "fd-card-css"
JS_SCRIPT_ID = "fd-card-js"
JS_VER = "pr24-bake-sym"
DB_SCRIPT_ID = "fd-chg-1d-db"
CO_DB_SCRIPT_ID = "fd-co-name-db"

_BAND_WHY_RE = re.compile(r"^band\s", re.I)
_DUMP_PILL_KEYS = frozenset({"fd-bb"})

# Live digest catalog (screenshot: 4-col ghost matrix under the chips).
TAG_CATALOG: tuple[str, ...] = (
    "MA FAN",
    "CLOSE HI",
    "52W HI",
    "HM/HL",
    "V.EMA",
    "ABOVE 50",
    "ABOVE 200",
    "MOM+",
    "TREND↑",
    "SQUEEZE",
    "RS+",
    "BREAKOUT",
)
TAG_ALIASES: dict[str, str] = {
    "HM HL": "HM/HL",
    "HMHL": "HM/HL",
    "V EMA": "V.EMA",
    "VEMA": "V.EMA",
    "TREND^": "TREND↑",
    "TREND UP": "TREND↑",
    "ABOVE50": "ABOVE 50",
    "ABOVE200": "ABOVE 200",
    "MOM +": "MOM+",
    "RS +": "RS+",
    "GAP": "GAP",
}
R20_KEYS: tuple[str, ...] = (
    "r20",
    "R20",
    "ret20",
    "ret_20",
    "ret_20d",
    "RET_20D",
    "r_20",
    "R_20",
)
RS63_KEYS: tuple[str, ...] = (
    "rs63",
    "RS63",
    "rs_63",
    "rel_63",
    "rel_63d",
    "RS_63",
)
ATR_KEYS: tuple[str, ...] = (
    "atr_pct",
    "atrpct",
    "ATR_PCT",
    "atr%",
    "atrs",
    "ATRS",
    "atr",
    "ATR",
    "atrp",
    "atr_pctile",
)
DAY_KEYS: tuple[str, ...] = (
    "day",
    "Day",
    "d1",
    "ret_1d",
    "r1",
    "R1",
    "ret1",
)
# Bloomberg ``CHG_PCT_1D`` is percent points (1.2 = +1.2%). ``day`` / ``ret_1d`` /
# ``metrics.day_pct`` stay decimals (0.012 = +1.2%), same as ``r20_pct`` / ``ret_n``.
PCT_POINT_KEYS: tuple[str, ...] = (
    "CHG_PCT_1D",
    "chg_pct_1d",
)
_METRIC_DAY_KEYS: tuple[str, ...] = ("day_pct", "r1_pct", "ret_1d", "day")
_SKIP_PORTABLE = frozenset(
    {
        "px_series",
        "prices",
        "closes",
        "history",
        "px_hist",
        "mom_score_series",
        "series",
        "spark",
        "html",
        "svg",
        "_card",
        "card",
    }
)
_EMPTY_STAT = frozenset({"", "-", "—", "–", "−", "n/a", "na", "none", "null"})
# Bloomberg short name first (``NAME``), then the fields the DETAIL name card already uses.
CO_NAME_KEYS: tuple[str, ...] = (
    "short_name",
    "SHORT_NAME",
    "name_short",
    "co_name",
    "company",
    "company_name",
    "COMPANY_NAME",
    "sec_name",
    "security_name",
    "SECURITY_NAME",
    "NAME",
    "long_name",
    "LONG_COMP_NAME",
    "nm",
)
# ``MSTR US Equity`` / ``SPX Index``. A trailing ``CORP`` alone is a company
# suffix (``NVIDIA CORP``), not a bond yellow key (``AAPL 3.5 08/15/29 Corp``).
_YELLOW_KEY_RE = re.compile(
    r"^[A-Za-z0-9.\-]{1,12}(?:\s+[A-Za-z]{1,4})?\s+(?:Equity|Index|Comdty|Curncy|Govt|Pfd|Mtge)$"
    r"|^(?=.*\d)[A-Za-z0-9./%\-]+(?:\s+[A-Za-z0-9./%\-]+){1,8}\s+Corp$",
    re.I,
)


def _short(ticker: Any) -> str:
    parts = str(ticker or "").split()
    return parts[0].upper() if parts else ""


def _fmt_g(value: Any) -> str:
    if value is None or isinstance(value, bool):
        return ""
    try:
        return f"{float(value):g}"
    except (TypeError, ValueError):
        return str(value)


def _delta_up(delta: Any) -> bool:
    try:
        return float(delta) >= 0
    except (TypeError, ValueError):
        return True


def catalog_key(text: Any) -> str:
    """Canonical digest-tag label, or ``\"\"`` if this is not a catalog cell."""
    raw = " ".join(str(text or "").split()).strip().upper()
    if not raw:
        return ""
    if raw in TAG_CATALOG:
        return raw if raw != "GAP" else "GAP"
    aliased = TAG_ALIASES.get(raw)
    if aliased:
        return aliased
    collapsed = raw.replace(" ", "").replace(".", "").replace("/", "").replace("+", "")
    for label in TAG_CATALOG:
        if label.replace(" ", "").replace(".", "").replace("/", "").replace("+", "").replace("↑", "") == collapsed.replace("↑", "").replace("^", ""):
            return label
    return ""


def _truthy_on(value: Any) -> bool | None:
    if value in (True, 1, "1", "true", "True", "yes", "YES", "on", "ON"):
        return True
    if value in (False, 0, "0", "false", "False", "no", "NO", "off", "OFF"):
        return False
    return None


def _pick_num(card: Mapping[str, Any] | None, keys: tuple[str, ...]) -> float | str | None:
    if not isinstance(card, Mapping):
        return None
    for key in keys:
        if key not in card:
            continue
        val = card.get(key)
        if val is None or isinstance(val, bool):
            continue
        if isinstance(val, str):
            text = val.strip()
            if text.lower() in _EMPTY_STAT:
                continue
            try:
                return float(text.replace("%", "").replace(",", ""))
            except ValueError:
                return text
        try:
            num = float(val)
        except (TypeError, ValueError):
            continue
        return num
    return None


def active_tags(card: Mapping[str, Any] | None) -> list[str]:
    """On/true digest tags only. Empty when the card has no tag state."""
    if not isinstance(card, Mapping):
        return []
    out: list[str] = []
    seen: set[str] = set()

    def add(label: Any) -> None:
        key = catalog_key(label) or " ".join(str(label or "").split()).strip()
        if not key or key in seen or len(key) > 28:
            return
        seen.add(key)
        out.append(key)

    for field in ("tags", "digest_tags", "flags_tags", "on_tags", "active_tags"):
        raw = card.get(field)
        if isinstance(raw, str) and raw.strip():
            up = raw.upper()
            harvested = False
            for label in TAG_CATALOG:
                if label in up:
                    add(label)
                    harvested = True
            if not harvested:
                bits = re.split(r"[,|;]+", raw) if ("," in raw or "|" in raw or ";" in raw) else [raw]
                for bit in bits:
                    token = bit.strip()
                    if catalog_key(token) or (token and len(token) <= 28):
                        add(token)
        elif isinstance(raw, (list, tuple)):
            for item in raw:
                if isinstance(item, Mapping):
                    on = _truthy_on(item.get("on") if "on" in item else item.get("active"))
                    off = _truthy_on(item.get("off"))
                    if off is True or on is False:
                        continue
                    add(item.get("label") or item.get("tag") or item.get("name") or item.get("t"))
                else:
                    add(item)
    for field in ("tg", "tgs", "tag", "digest"):
        raw = card.get(field)
        if isinstance(raw, Mapping):
            for key, val in raw.items():
                flag = _truthy_on(val)
                if flag is True:
                    add(key)
                elif flag is None and catalog_key(key) and val not in (None, ""):
                    add(key)
        elif isinstance(raw, str):
            add(raw)
    for label in TAG_CATALOG:
        for key in (label, label.replace(" ", "_"), label.replace(" ", "").replace(".", "").replace("/", "").replace("+", "plus")):
            if key in card:
                flag = _truthy_on(card.get(key))
                if flag is True:
                    add(label)
    return out


def alias_stats(card: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    """Copy Day / R20 / RS63 / ATR% onto the names live chrome actually reads."""
    day = _pick_num(card, DAY_KEYS)
    r20 = _pick_num(card, R20_KEYS)
    rs63 = _pick_num(card, RS63_KEYS)
    atr = _pick_num(card, ATR_KEYS)
    if day is not None:
        card["day"] = day
        card.setdefault("Day", day)
        card.setdefault("ret_1d", day)
    if r20 is not None:
        card["r20"] = r20
        card.setdefault("R20", r20)
        card.setdefault("ret_20d", r20)
    if rs63 is not None:
        card["rs63"] = rs63
        card.setdefault("RS63", rs63)
    if atr is not None:
        card["atr_pct"] = atr
        card.setdefault("atrs", atr)
        card.setdefault("atr", atr)
        card.setdefault("ATR", atr)
        card.setdefault("ATRS", atr)
    stamp_day(card)
    return card


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None or value == "":
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(num) or math.isinf(num):
        return None
    return num


def day_decimal(card: Mapping[str, Any] | None) -> float | None:
    """1-day return as a decimal (``0.012`` → ``+1.2%``).

    Decimal fields (``metrics.day_pct`` / ``ret_1d`` / ``day``) win. Bloomberg
    ``CHG_PCT_1D`` (percent points) is used only when those are empty.
    """
    if not isinstance(card, Mapping):
        return None
    metrics = card.get("metrics") if isinstance(card.get("metrics"), Mapping) else None
    for src, keys in (
        (metrics, _METRIC_DAY_KEYS),
        (card, DAY_KEYS),
    ):
        picked = _pick_num(src, keys)
        num = _as_float(picked)
        if num is not None:
            return num
    for src in (metrics, card):
        picked = _pick_num(src, PCT_POINT_KEYS)
        num = _as_float(picked)
        if num is not None:
            return num / 100.0
    return None


def _js_round1(value: float) -> float:
    """Match ``Math.round(x * 10) / 10`` (ties towards +∞)."""
    return math.floor(value * 10.0 + 0.5) / 10.0


def format_chg_1d(decimal: float) -> tuple[str, str]:
    """Signed 1-day percent and side: ``('+1.2%', 'up')`` / ``('−0.8%', 'down')`` / ``('0.0%', 'flat')``."""
    rounded = _js_round1(float(decimal) * 100.0)
    if rounded == 0:
        return "0.0%", "flat"
    if rounded > 0:
        return f"+{rounded:.1f}%", "up"
    return f"\u2212{abs(rounded):.1f}%", "down"


def _text(value: Any) -> str:
    if value is None or isinstance(value, (bool, int, float)):
        return ""
    text = " ".join(str(value).split()).strip()
    if not text or text.lower() in _EMPTY_STAT:
        return ""
    return text


def _is_yellow_key(text: str) -> bool:
    return bool(_YELLOW_KEY_RE.match((text or "").strip()))


def symbol_of(card: Mapping[str, Any] | None) -> str:
    """Short ticker (``MSTR``), not the yellow key and not the company name."""
    if not isinstance(card, Mapping):
        return ""
    for key in ("d", "t", "ticker", "symbol"):
        short = _short(card.get(key))
        if short:
            return short
    name = _text(card.get("name"))
    if name and " " not in name and not _is_yellow_key(name):
        return _short(name)
    return ""


def _clean_company(symbol: str, company: str) -> str:
    """Company piece only. Drops a repeated symbol and yellow keys."""
    co = " ".join(str(company or "").split()).strip()
    if not co or _is_yellow_key(co):
        return ""
    sym = (symbol or "").strip().upper()
    if not sym:
        return co
    if co.upper() == sym:
        return ""
    if " | " in co:
        left, right = co.split(" | ", 1)
        if left.strip().upper() == sym:
            return _clean_company(sym, right)
    if co.upper().startswith(sym + " "):
        rest = co[len(sym) :].strip(" \t|-–—:·")
        if not rest or rest.upper() == sym or _is_yellow_key(rest):
            return ""
        return rest
    return co


def company_of(card: Mapping[str, Any] | None) -> str:
    """Short company name. Empty when missing or equal to the symbol."""
    if not isinstance(card, Mapping):
        return ""
    sym = symbol_of(card)
    for key in CO_NAME_KEYS:
        co = _clean_company(sym, _text(card.get(key)))
        if co:
            return co
    return _clean_company(sym, _text(card.get("name")))


def header_title(card: Mapping[str, Any] | None) -> str:
    """``MSTR | STRATEGY INC``, or the symbol alone when the company is missing."""
    sym = symbol_of(card)
    co = company_of(card)
    if sym and co:
        return f"{sym} | {co}"
    return sym or co


def chg_1d_html(card: Mapping[str, Any] | None) -> str:
    """Compact header chip. Empty string when the 1-day change is missing."""
    dec = day_decimal(card)
    if dec is None:
        return ""
    label, side = format_chg_1d(dec)
    shown = html.escape(label)
    attr = html.escape(f"{dec:.8g}", quote=True)
    return (
        f'<span class="px-1d chg-1d {side}" data-key="chg-1d" data-chg-1d="{attr}" '
        f'title="1d CHG_PCT_1D">{shown}</span>'
    )


def stamp_day(card: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    """Copy a resolved 1-day decimal onto ``day`` / ``ret_1d`` / ``metrics.day_pct`` when empty."""
    dec = day_decimal(card)
    if dec is None:
        return card
    if _pick_num(card, DAY_KEYS) is None:
        card["day"] = dec
        card.setdefault("Day", dec)
        card.setdefault("ret_1d", dec)
    metrics = card.get("metrics")
    chg = _pick_num(card, PCT_POINT_KEYS)
    if isinstance(metrics, Mapping) and not isinstance(metrics, dict):
        metrics = dict(metrics)
        card["metrics"] = metrics
    if isinstance(card.get("metrics"), dict):
        blob = card["metrics"]
        if blob.get("day_pct") is None and blob.get("r1_pct") is None:
            blob["day_pct"] = dec
    elif chg is not None:
        card["metrics"] = {"day_pct": dec}
    return card


def chg_1d_db(
    cards: Any = None,
    book: Mapping[str, Any] | None = None,
) -> dict[str, float]:
    """Ticker → decimal 1-day return for baked cards (Bloomberg fills gaps)."""
    out: dict[str, float] = {}

    def put(ticker: Any, src: Mapping[str, Any] | None) -> None:
        if not isinstance(src, Mapping):
            return
        dec = day_decimal(src)
        if dec is None:
            return
        key = str(ticker or "").strip()
        if not key:
            return
        val = round(float(dec), 8)
        if key not in out:
            out[key] = val
        short = _short(key)
        if short and short not in out:
            out[short] = val

    if cards:
        for card in cards:
            if not isinstance(card, Mapping):
                continue
            put(card.get("ticker") or card.get("t") or card.get("name") or card.get("d"), card)
    names = book.get("names") if isinstance(book, Mapping) else None
    if isinstance(names, dict):
        for ticker, rec in names.items():
            put(ticker, rec if isinstance(rec, Mapping) else None)
    return out


def co_name_db(
    cards: Any = None,
    book: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    """Ticker → short company name (``STRATEGY INC``), not ``SYMBOL | name``."""
    out: dict[str, str] = {}

    def put(ticker: Any, src: Mapping[str, Any] | None) -> None:
        if not isinstance(src, Mapping):
            return
        worked: Mapping[str, Any] = src
        if ticker and not symbol_of(src):
            copied = dict(src)
            copied["ticker"] = ticker
            worked = copied
        co = company_of(worked)
        if not co:
            return
        key = str(ticker or symbol_of(worked) or "").strip()
        if not key:
            return
        if key not in out:
            out[key] = co
        short = _short(key)
        if short and short not in out:
            out[short] = co

    if cards:
        for card in cards:
            if not isinstance(card, Mapping):
                continue
            put(card.get("ticker") or card.get("t") or card.get("d") or card.get("symbol"), card)
    names = book.get("names") if isinstance(book, Mapping) else None
    if isinstance(names, dict):
        for ticker, rec in names.items():
            put(ticker, rec if isinstance(rec, Mapping) else None)
    return out


def portable_card(
    card: Mapping[str, Any] | None,
    row: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Self-contained MOM-shaped object for ``#fd-breakout-db`` (no price series)."""
    src = dict(card) if isinstance(card, Mapping) else {}
    for key in list(src):
        if key in _SKIP_PORTABLE:
            src.pop(key, None)
    out = normalize_card(src, row)
    alias_stats(out)
    tags = active_tags(out) or active_tags(src)
    if tags:
        out["tags"] = tags
    return out


def why_pills(row: Mapping[str, Any] | None) -> list[dict[str, str]]:
    """Short Breakout/Breakdown chips. Not the ``band 10 · +3 / 7d · above 3d`` dump."""
    row = row or {}
    pills: list[dict[str, str]] = []
    why = str(row.get("why") or "")
    score = row.get("score")
    if score is not None and score != "":
        label = f"band {_fmt_g(score)}"
        pills.append(
            {
                "key": "fd-bb-band",
                "label": label,
                "cls": "fd-bb-band",
                "title": why or label,
            }
        )
    delta = row.get("delta")
    if delta is not None and delta != "":
        try:
            dval = float(delta)
            bit = f"{'+' if dval >= 0 else ''}{dval:g}"
        except (TypeError, ValueError):
            bit = str(delta)
        used = row.get("lookback")
        if used not in (None, ""):
            try:
                bit += f"/{int(used)}d"
            except (TypeError, ValueError):
                bit += f"/{used}d"
        pills.append(
            {
                "key": "fd-bb-delta",
                "label": bit,
                "cls": "fd-bb-delta-up" if _delta_up(delta) else "fd-bb-delta-down",
                "title": why or bit,
            }
        )
    return pills


def is_band_dump(text: Any) -> bool:
    return bool(_BAND_WHY_RE.match(str(text or "").strip()))


def merge_pills(
    card: Mapping[str, Any] | None,
    extra: list[Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Dedupe by ``key``. Drop the long ``fd-bb`` dump pill."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for src in (card.get("enrich_pills") if isinstance(card, Mapping) else None, extra):
        if not isinstance(src, list):
            continue
        for pill in src:
            if not isinstance(pill, Mapping):
                continue
            key = str(pill.get("key") or "")
            label = str(pill.get("label") or "")
            if not label:
                continue
            if key in _DUMP_PILL_KEYS or (not key and is_band_dump(label)):
                continue
            if " · " in label and is_band_dump(label):
                continue
            dedupe = key or label
            if dedupe in seen:
                continue
            seen.add(dedupe)
            out.append(dict(pill))
    return out


def normalize_card(
    card: Mapping[str, Any] | None,
    row: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Full MOM-shaped card for ``cardHTML`` / ``renderCard``. Does not mutate ``card``."""
    row = row or {}
    out: dict[str, Any] = dict(card) if isinstance(card, Mapping) else {}
    display = _short(
        out.get("d")
        or out.get("t")
        or out.get("ticker")
        or out.get("name")
        or out.get("symbol")
        or row.get("t")
        or row.get("ticker")
        or row.get("d")
        or ""
    )
    if display:
        out.setdefault("t", display)
        out.setdefault("d", display)
        out.setdefault("name", display)
        if not out.get("ticker"):
            out["ticker"] = row.get("ticker") or display
    embedded = row.get("card") if isinstance(row.get("card"), Mapping) else None
    if not embedded and isinstance(row.get("_card"), Mapping):
        embedded = row.get("_card")
    if isinstance(embedded, Mapping):
        for key, val in embedded.items():
            if key in _SKIP_PORTABLE:
                continue
            if val is None or val == "":
                continue
            if out.get(key) is None or out.get(key) == "":
                out[key] = val
    for key in DAY_KEYS + PCT_POINT_KEYS + R20_KEYS + RS63_KEYS + ATR_KEYS + ("score", "mom_score"):
        if out.get(key) is None and row.get(key) is not None and row.get(key) != "":
            out[key] = row.get(key)
    if out.get("score") is None and row.get("score") is not None:
        out["score"] = row.get("score")
    if out.get("mom_score") is None and row.get("score") is not None:
        out["mom_score"] = row.get("score")
    why = out.get("why")
    if is_band_dump(why) or (row.get("why") and why == row.get("why")):
        out.pop("why", None)
    extra = why_pills(row)
    if row.get("label") and not any(
        isinstance(p, Mapping) and p.get("key") == "mom-streak" for p in extra
    ):
        side = str(row.get("side") or "")
        cls = "mom-streak-down" if side == "below" else ("mom-streak-up" if side == "above" else "mom-streak")
        extra.append(
            {
                "key": "mom-streak",
                "label": str(row.get("label")),
                "cls": cls,
                "title": str(row.get("label")),
            }
        )
    out["enrich_pills"] = merge_pills(out, extra)
    alias_stats(out)
    tags = active_tags(out)
    if tags:
        out["tags"] = tags
    return out


def attach_row(
    card: MutableMapping[str, Any],
    row: Mapping[str, Any] | None = None,
) -> MutableMapping[str, Any]:
    """In-place: merge BB chips onto an existing MOM card (tests / Python paint)."""
    merged = normalize_card(card, row)
    card.clear()
    card.update(merged)
    return card


def strip_css() -> str:
    """Self-contained chip chrome on ``article.card`` — not scoped to a tab."""
    return """
/* Portable MOM card chips. Applies wherever the node is mounted. */
article.card .pills,
article.card .chips,
article.card .badges,
article.card .tags,
article.card .tgs,
article.card .tag-row,
article.card .tg-row,
article.card .fd-tag-wrap {
  display: flex !important;
  flex-wrap: wrap !important;
  gap: 4px !important;
  align-items: center;
  grid-template-columns: none !important;
  font-size: inherit !important;
  color: inherit !important;
  opacity: 1 !important;
  line-height: 1.2 !important;
}
article.card .badge,
article.card .spike-chip,
article.card .tg,
article.card .tag,
article.card .fd-tag-chip,
article.card .tags > span,
article.card .tgs > span,
article.card .tags > b,
article.card .tgs > b {
  display: inline-block !important;
  font: 650 10px/1.15 "Segoe UI", "Segoe UI Symbol", "DejaVu Sans", "Noto Sans", ui-sans-serif, system-ui, sans-serif !important;
  letter-spacing: 0.04em;
  padding: 2px 6px !important;
  margin: 0 3px 3px 0 !important;
  border-radius: 3px !important;
  border: 1px solid #6b7280 !important;
  color: #e5e7eb !important;
  background: #111827 !important;
  text-transform: none !important;
  vertical-align: middle;
  white-space: nowrap;
  opacity: 1 !important;
}
article.card .tg.on,
article.card .tag.on,
article.card .fd-tag-on,
article.card .tg[data-on="1"],
article.card .tag[data-on="1"] {
  color: #fde68a !important;
  border-color: #ca8a04 !important;
  background: #1c1917 !important;
}
article.card .badge.fd-bb-band, article.card .spike-chip.fd-bb-band {
  color: #fde68a !important; border-color: #ca8a04 !important;
}
article.card .badge.fd-bb-delta-up, article.card .spike-chip.fd-bb-delta-up {
  color: #6ee7b7 !important; border-color: #34d399 !important;
}
article.card .badge.fd-bb-delta-down, article.card .spike-chip.fd-bb-delta-down {
  color: #fda4af !important; border-color: #fb7185 !important;
}
article.card .why.fd-bb-why,
article.card .fd-bb-why,
article.card p.fd-bb-why,
article.card .why[data-fd-bb="1"] {
  display: none !important;
}
article.card .stats {
  display: flex !important;
  flex-wrap: wrap;
  gap: 6px 12px;
  font-size: 11px;
  color: #d1d5db;
  margin-top: 6px;
}
article.card .stats span {
  white-space: nowrap;
}
.card .mom-score-d10-near {
  display: block !important;
  margin-top: 1px;
  text-align: right;
  font: 600 10px/1.15 ui-monospace, "Cascadia Mono", "Segoe UI Mono", Menlo, Consolas, monospace !important;
  letter-spacing: 0.01em;
}
/* Live cards are <div class="card">, not <article>. .score.hi must not win. */
.card .mom-score-d10-near.up,
.card .score-d10.up,
.mom-score-d10-near.up { color: #6ee7b7 !important; }
.card .mom-score-d10-near.down,
.card .score-d10.down,
.score-d10.down,
.mom-score-d10-near.down { color: #fda4af !important; }
.card .mom-score-d10-near.flat,
.card .score-d10.flat,
.mom-score-d10-near.flat { color: #9ca3af !important; }
/* 1-day price change beside the ticker / company name. Not the 10d score delta. */
.px-1d, .chg-1d {
  display: inline !important;
  margin-left: 8px;
  padding: 0 !important;
  border: 0 !important;
  background: transparent !important;
  font: 600 12px/1.15 ui-monospace, "Cascadia Mono", "Segoe UI Mono", Menlo, Consolas, monospace !important;
  letter-spacing: 0.01em;
  white-space: nowrap;
  vertical-align: baseline;
  flex: 0 0 auto;
}
.px-1d.up, .chg-1d.up { color: #6ee7b7 !important; }
.px-1d.down, .chg-1d.down { color: #fda4af !important; }
.px-1d.flat, .chg-1d.flat { color: #9ca3af !important; }
""".strip()


def strip_js() -> str:
    """Wrap live ``cardHTML``. Tabs call ``__FD_RENDER_CARD__(card)`` only."""
    return r"""
(function () {
  var CARD_VER = "pr24-bake-sym";
  if (window.__FD_CARD_BOUND__ === CARD_VER) return;
  window.__FD_CARD_BOUND__ = CARD_VER;

  function shortOf(t) { return String(t || "").trim().split(/\s+/)[0].toUpperCase(); }
  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
  function fmt(v) {
    if (v == null || v === "") return "—";
    if (typeof v === "number" && isFinite(v)) return String(v);
    return String(v);
  }
  function fmtAtr(x) {
    if (x == null || x === "") return "";
    var n = Number(x);
    if (!isFinite(n)) return "";
    if (Math.abs(n) < 1) return (n * 100).toFixed(1) + "%";
    return n.toFixed(1) + "%";
  }
  function isBandDump(text) { return /^\s*band\s/i.test(String(text || "")); }
  function deltaUp(d) { var n = parseFloat(d); return !(isFinite(n) && n < 0); }
  var TAG_CATALOG = ["MA FAN","CLOSE HI","52W HI","HM/HL","V.EMA","ABOVE 50","ABOVE 200","MOM+","TREND↑","SQUEEZE","RS+","BREAKOUT"];
  var TAG_ALIAS = {"HM HL":"HM/HL","HMHL":"HM/HL","V EMA":"V.EMA","VEMA":"V.EMA","TREND^":"TREND↑","ABOVE50":"ABOVE 50","ABOVE200":"ABOVE 200","MOM +":"MOM+","RS +":"RS+","GAP":"GAP"};
  var TAG_SET = {};
  for (var ti = 0; ti < TAG_CATALOG.length; ti++) TAG_SET[TAG_CATALOG[ti]] = true;
  var R20_KEYS = ["r20","R20","ret20","ret_20","ret_20d","RET_20D","r_20","R_20"];
  var RS63_KEYS = ["rs63","RS63","rs_63","rel_63","rel_63d","RS_63"];
  var ATR_KEYS = ["atr_pct","atrpct","ATR_PCT","atr%","atrs","ATRS","atr","ATR","atrp"];
  var DAY_KEYS = ["day","Day","d1","ret_1d","r1","R1","ret1"];
  var PCT_POINT_KEYS = ["CHG_PCT_1D","chg_pct_1d"];
  var METRIC_DAY_KEYS = ["day_pct","r1_pct","ret_1d","day"];
  var SKIP_COPY = {px_series:1,prices:1,closes:1,history:1,px_hist:1,mom_score_series:1,series:1,spark:1,html:1,svg:1,_card:1,card:1,why:1,pills:1};

  function catalogKey(text) {
    var raw = String(text || "").replace(/\s+/g, " ").trim().toUpperCase();
    if (!raw) return "";
    if (TAG_SET[raw]) return raw;
    if (TAG_ALIAS[raw]) return TAG_ALIAS[raw];
    var collapsed = raw.replace(/[\s./+]/g, "").replace("↑","").replace("^","");
    for (var i = 0; i < TAG_CATALOG.length; i++) {
      var lab = TAG_CATALOG[i].replace(/[\s./+↑]/g, "");
      if (lab && lab === collapsed) return TAG_CATALOG[i];
    }
    return "";
  }
  function pickNum(card, keys) {
    if (!card) return null;
    for (var i = 0; i < keys.length; i++) {
      var v = card[keys[i]];
      if (v == null || v === "" || v === true || v === false) continue;
      if (typeof v === "number" && isFinite(v)) return v;
      var s = String(v).trim();
      if (!s || s === "-" || s === "—" || s === "–" || s === "−") continue;
      var n = parseFloat(s.replace("%", "").replace(",", ""));
      if (isFinite(n)) return n;
      return s;
    }
    return null;
  }
  function aliasStats(card) {
    if (!card) return card;
    var day = pickNum(card, DAY_KEYS);
    var r20 = pickNum(card, R20_KEYS);
    var rs63 = pickNum(card, RS63_KEYS);
    var atr = pickNum(card, ATR_KEYS);
    if (day != null) { card.day = day; if (card.Day == null) card.Day = day; if (card.ret_1d == null) card.ret_1d = day; }
    if (r20 != null) { card.r20 = r20; if (card.R20 == null) card.R20 = r20; if (card.ret_20d == null) card.ret_20d = r20; }
    if (rs63 != null) { card.rs63 = rs63; if (card.RS63 == null) card.RS63 = rs63; }
    if (atr != null) {
      card.atr_pct = atr;
      if (card.atrs == null) card.atrs = atr;
      if (card.atr == null) card.atr = atr;
      if (card.ATR == null) card.ATR = atr;
    }
    stampDay(card);
    return card;
  }
  function dayDecimal(card) {
    if (!card) return null;
    var m = card.metrics || {};
    var dec = pickNum(m, METRIC_DAY_KEYS);
    if (dec == null) dec = pickNum(card, DAY_KEYS);
    if (dec != null) {
      var n = Number(dec);
      return isFinite(n) ? n : null;
    }
    var pts = pickNum(m, PCT_POINT_KEYS);
    if (pts == null) pts = pickNum(card, PCT_POINT_KEYS);
    if (pts == null) return null;
    var p = Number(pts);
    return isFinite(p) ? p / 100 : null;
  }
  function stampDay(card) {
    if (!card) return card;
    var dec = dayDecimal(card);
    if (dec == null) return card;
    if (pickNum(card, DAY_KEYS) == null) {
      card.day = dec;
      if (card.Day == null) card.Day = dec;
      if (card.ret_1d == null) card.ret_1d = dec;
    }
    var chg = pickNum(card, PCT_POINT_KEYS);
    if (chg == null && card.metrics) chg = pickNum(card.metrics, PCT_POINT_KEYS);
    if (!card.metrics || typeof card.metrics !== "object") {
      if (chg != null) card.metrics = { day_pct: dec };
      return card;
    }
    if (card.metrics.day_pct == null && card.metrics.r1_pct == null) card.metrics.day_pct = dec;
    return card;
  }
  function formatChg1d(dec) {
    var pct = Number(dec) * 100;
    if (!isFinite(pct)) return null;
    var rounded = Math.round(pct * 10) / 10;
    if (rounded === 0) return { label: "0.0%", side: "flat" };
    if (rounded > 0) return { label: "+" + rounded.toFixed(1) + "%", side: "up" };
    return { label: "\u2212" + Math.abs(rounded).toFixed(1) + "%", side: "down" };
  }
  function chg1dHTML(card) {
    var dec = dayDecimal(card);
    if (dec == null) return "";
    var view = formatChg1d(dec);
    if (!view) return "";
    return '<span class="px-1d chg-1d ' + view.side + '" data-key="chg-1d" data-chg-1d="' + esc(dec) + '" title="1d CHG_PCT_1D">' + esc(view.label) + "</span>";
  }
  var CO_KEYS = ["short_name","SHORT_NAME","name_short","co_name","company","company_name","COMPANY_NAME","sec_name","security_name","NAME","long_name","LONG_COMP_NAME","nm"];
  function isYellowKey(text) {
    return /^[A-Za-z0-9.\-]{1,12}(?:\s+[A-Za-z]{1,4})?\s+(?:Equity|Index|Comdty|Curncy|Govt|Corp|Pfd|Mtge)$/i.test(String(text || "").trim());
  }
  function cleanCompany(sym, company) {
    var co = String(company || "").replace(/\s+/g, " ").trim();
    if (!co || co === "-" || co === "—" || isYellowKey(co)) return "";
    var S = String(sym || "").trim().toUpperCase();
    if (!S) return co;
    if (co.toUpperCase() === S) return "";
    var pipe = co.indexOf(" | ");
    if (pipe >= 0 && co.slice(0, pipe).trim().toUpperCase() === S) return cleanCompany(S, co.slice(pipe + 3));
    if (co.toUpperCase().indexOf(S + " ") === 0) {
      var rest = co.slice(S.length).replace(/^[\s|\-–—:·]+/, "").trim();
      if (!rest || rest.toUpperCase() === S || isYellowKey(rest)) return "";
      return rest;
    }
    return co;
  }
  function symbolOfCard(card) {
    if (!card) return "";
    var sym = shortOf(card.d || card.t || card.ticker || card.symbol || "");
    if (sym) return sym;
    var name = String(card.name || "").replace(/\s+/g, " ").trim();
    if (name && name.indexOf(" ") < 0 && !isYellowKey(name)) return shortOf(name);
    return "";
  }
  function companyOf(card) {
    if (!card) return "";
    var sym = symbolOfCard(card);
    var i, co;
    for (i = 0; i < CO_KEYS.length; i++) {
      co = cleanCompany(sym, card[CO_KEYS[i]]);
      if (co) return co;
    }
    return cleanCompany(sym, card.name);
  }
  function headerTitle(card) {
    var sym = symbolOfCard(card);
    var co = companyOf(card);
    if (sym && co) return sym + " | " + co;
    return sym || co;
  }
  function activeTagsFromCard(card) {
    var out = [];
    var seen = {};
    function add(label) {
      var key = catalogKey(label) || String(label || "").replace(/\s+/g, " ").trim();
      if (!key || seen[key] || key.length > 28) return;
      seen[key] = true;
      out.push(key);
    }
    if (!card) return out;
    ["tags","digest_tags","flags_tags","on_tags","active_tags"].forEach(function (field) {
      var raw = card[field];
      if (typeof raw === "string" && raw.trim()) {
        if (/[,|;]/.test(raw)) raw.split(/[,|;]+/).forEach(function (bit) { add(bit); });
        else add(raw);
      } else if (Array.isArray(raw)) {
        raw.forEach(function (item) {
          if (item && typeof item === "object") {
            if (item.off === true || item.on === false || item.on === 0 || item.on === "0") return;
            add(item.label || item.tag || item.name || item.t);
          } else add(item);
        });
      }
    });
    ["tg","tgs","tag","digest"].forEach(function (field) {
      var raw = card[field];
      if (raw && typeof raw === "object" && !Array.isArray(raw)) {
        Object.keys(raw).forEach(function (k) {
          var v = raw[k];
          if (v === true || v === 1 || v === "1" || v === "on" || v === "ON") add(k);
        });
      }
    });
    return out;
  }

  function whyPills(row) {
    row = row || {};
    var pills = [];
    var why = String(row.why || "");
    if (row.score != null && row.score !== "") {
      var band = "band " + String(row.score).replace(/\.0$/, "");
      pills.push({ key: "fd-bb-band", label: band, cls: "fd-bb-band", title: why || band });
    }
    if (row.delta != null && row.delta !== "") {
      var n = parseFloat(row.delta);
      var bit = (isFinite(n) ? ((n >= 0 ? "+" : "") + String(n).replace(/\.0$/, "")) : String(row.delta));
      if (row.lookback != null && row.lookback !== "") bit += "/" + String(row.lookback) + "d";
      pills.push({
        key: "fd-bb-delta",
        label: bit,
        cls: deltaUp(row.delta) ? "fd-bb-delta-up" : "fd-bb-delta-down",
        title: why || bit
      });
    }
    return pills;
  }

  function addList(dst, src) {
    if (!src) return;
    if (Array.isArray(src)) { for (var i = 0; i < src.length; i++) dst.push(src[i]); return; }
    if (typeof src !== "object") return;
    if (src.t != null || src.ticker != null || src.d != null || src.score != null || src.mom_score != null) {
      dst.push(src);
      return;
    }
    var keys = Object.keys(src);
    for (var k = 0; k < keys.length; k++) addList(dst, src[keys[k]]);
  }
  function collectCards() {
    var raw = [];
    var mom = window.MOM || {};
    addList(raw, mom.cards);
    addList(raw, mom.up);
    addList(raw, mom.down);
    addList(raw, mom.all);
    addList(raw, mom.flags);
    addList(raw, mom.watch);
    addList(raw, mom.outliers);
    addList(raw, mom.search);
    addList(raw, window.MOM_CARDS);
    addList(raw, window.CARDS);
    addList(raw, window.FLAGS);
    addList(raw, window.WATCH);
    addList(raw, window.OUTLIERS);
    addList(raw, window.BOOK);
    addList(raw, window.DIGEST);
    addList(raw, window.NAMES);
    addList(raw, window.MOMUP);
    addList(raw, window.MOMDOWN);
    if (window.BOOK && window.BOOK.names) addList(raw, window.BOOK.names);
    if (window.DIGEST && window.DIGEST.names) addList(raw, window.DIGEST.names);
    var out = [];
    var seen = {};
    for (var i = 0; i < raw.length; i++) {
      var c = raw[i];
      if (!c || typeof c !== "object") continue;
      var key = shortOf(c.t || c.ticker || c.d || c.name || c.symbol || "");
      if (!key || seen[key]) continue;
      seen[key] = true;
      out.push(c);
    }
    return out;
  }
  function findCard(ticker) {
    var want = shortOf(ticker);
    if (!want) return null;
    var cards = collectCards();
    for (var i = 0; i < cards.length; i++) {
      var c = cards[i] || {};
      if (shortOf(c.t || c.ticker || c.d || c.name || c.symbol || "") === want) return c;
    }
    return null;
  }

  function copyCard(card) {
    var out = {};
    if (!card) return out;
    for (var k in card) {
      if (SKIP_COPY[k] && k !== "why") continue;
      if (k === "px_series" || k === "prices" || k === "closes" || k === "history" || k === "mom_score_series") continue;
      out[k] = card[k];
    }
    return out;
  }
  function mergeCards(a, b) {
    var out = copyCard(a);
    if (!b || typeof b !== "object") return out;
    for (var k in b) {
      if (k === "why" || k === "pills" || k === "_card" || k === "card") continue;
      if (k === "px_series" || k === "prices" || k === "closes" || k === "history" || k === "mom_score_series") continue;
      var v = b[k];
      if (v == null || v === "") continue;
      if (out[k] == null || out[k] === "") out[k] = v;
    }
    if (Array.isArray(b.enrich_pills) && b.enrich_pills.length) {
      out.enrich_pills = (Array.isArray(out.enrich_pills) ? out.enrich_pills : []).concat(b.enrich_pills);
    }
    if (Array.isArray(b.tags) && b.tags.length && (!out.tags || !out.tags.length)) out.tags = b.tags.slice();
    return out;
  }
  function mergePills(card, extra) {
    var src = [];
    if (card && Array.isArray(card.enrich_pills)) src = src.concat(card.enrich_pills);
    if (Array.isArray(extra)) src = src.concat(extra);
    var out = [];
    var seen = {};
    for (var i = 0; i < src.length; i++) {
      var p = src[i];
      if (!p || !p.label) continue;
      var key = String(p.key || "");
      var label = String(p.label);
      if (key === "fd-bb" || (isBandDump(label) && label.indexOf(" · ") >= 0)) continue;
      var id = key || label;
      if (seen[id]) continue;
      seen[id] = true;
      out.push(p);
    }
    return out;
  }
  function normalizeCard(card, row) {
    row = row || {};
    var embedded = row.card || row._card || null;
    var out = mergeCards(card, embedded);
    var display = shortOf(out.d || out.t || out.ticker || out.name || out.symbol || row.t || row.ticker || row.d || "");
    if (display) {
      if (!out.t) out.t = display;
      if (!out.d) out.d = display;
      if (!out.name) out.name = display;
      if (!out.ticker) out.ticker = row.ticker || out.ticker || display;
    }
    if (out.score == null && row.score != null) out.score = row.score;
    if (out.mom_score == null && row.score != null) out.mom_score = row.score;
    ["day","ret_1d","CHG_PCT_1D","chg_pct_1d","r20","rs63","atr_pct","R20","RS63"].forEach(function (k) {
      if ((out[k] == null || out[k] === "") && row[k] != null && row[k] !== "") out[k] = row[k];
    });
    if (isBandDump(out.why) || (row.why && out.why === row.why)) delete out.why;
    var extra = Array.isArray(row.pills) && row.pills.length ? row.pills.slice() : whyPills(row);
    if (row.label && !extra.some(function (p) { return p && p.key === "mom-streak"; })) {
      var side = row.side || "";
      extra.push({
        key: "mom-streak",
        label: String(row.label),
        cls: side === "below" ? "mom-streak-down" : (side === "above" ? "mom-streak-up" : "mom-streak"),
        title: String(row.label)
      });
    }
    out.enrich_pills = mergePills(out, extra);
    aliasStats(out);
    var tags = activeTagsFromCard(out);
    if (tags.length) out.tags = tags;
    return out;
  }

  function pillsHTML(pills) {
    if (!Array.isArray(pills)) return "";
    var bits = [];
    for (var i = 0; i < pills.length; i++) {
      var p = pills[i];
      if (!p || !p.label) continue;
      bits.push(
        '<span class="badge spike-chip ' + esc(p.cls || "") + '" data-key="' + esc(p.key || "") + '"' +
        (p.title ? ' title="' + esc(p.title) + '"' : "") + ">" + esc(p.label) + "</span>"
      );
    }
    return bits.join("");
  }
  function readStreakDb() {
    var el = document.getElementById("mom-streak-db");
    if (!el) return {};
    try { return JSON.parse(el.textContent || "{}") || {}; } catch (e) { return {}; }
  }
  function d10From(card) {
    var src = card || {};
    if (src.mom_score_d10_short || src.mom_score_d10_label || src.mom_score_d10 != null) return src;
    var db = readStreakDb();
    var keys = [];
    ["ticker", "name", "t", "d", "symbol"].forEach(function (k) {
      if (src[k]) keys.push(String(src[k]));
    });
    var i;
    for (i = 0; i < keys.length; i++) if (db[keys[i]]) return db[keys[i]];
    var short = shortOf(keys[0] || "");
    if (short && db[short]) return db[short];
    var all = Object.keys(db);
    for (i = 0; i < all.length; i++) if (short && shortOf(all[i]) === short) return db[all[i]];
    return null;
  }
  function d10ShortText(src) {
    if (!src) return "";
    if (src.mom_score_d10_short) return String(src.mom_score_d10_short);
    var n = Number(src.mom_score_d10);
    if (isFinite(n)) {
      if (n > 0) return "+" + n;
      if (n < 0) return "\u2212" + String(src.mom_score_d10).replace(/^-/, "");
      return "0";
    }
    return String(src.mom_score_d10_label || "").replace(/^10d\s+/, "");
  }
  function d10View(card) {
    var src = d10From(card);
    var label = d10ShortText(src);
    if (!src || !label) return null;
    var n = Number(src.mom_score_d10);
    var side = n > 0 ? "up" : (n < 0 ? "down" : "flat");
    var title = src.d10_title || "";
    if (!title && src.mom_score_d10_prior != null && src.mom_score_d10_date) {
      var now = (card && card.score != null) ? card.score : ((card && card.mom_score != null) ? card.mom_score : src.score);
      title = "composite score 10 trading days: was " + src.mom_score_d10_prior + " on " + src.mom_score_d10_date + " \u2192 now " + now + " (\u0394 " + label + ")";
    }
    return { label: label, side: side, title: title, delta: src.mom_score_d10 };
  }
  function fallbackHTML(c) {
    c = c || {};
    var t = esc(shortOf(c.d || c.t || c.ticker || c.symbol || ""));
    var title = esc(headerTitle(c) || shortOf(c.d || c.t || c.ticker || c.name || ""));
    var score = c.score != null ? c.score : (c.mom_score != null ? c.mom_score : "");
    var day = fmt(c.day != null ? c.day : (c.Day != null ? c.Day : (c.ret_1d != null ? c.ret_1d : null)));
    var r20 = fmt(c.r20 != null ? c.r20 : (c.R20 != null ? c.R20 : null));
    var rs63 = fmt(c.rs63 != null ? c.rs63 : (c.RS63 != null ? c.RS63 : null));
    var atr = fmt(c.atr_pct != null ? c.atr_pct : (c.atrs != null ? c.atrs : (c.atr != null ? c.atr : (c.ATRS != null ? c.ATRS : (c.ATR != null ? c.ATR : null)))));
    var d10 = d10View(c);
    var near = d10 ? '<span class="mom-score-d10-near ' + d10.side + '" data-key="mom-score-d10-near"' +
      (d10.delta != null ? ' data-mom-score-d10="' + esc(d10.delta) + '"' : "") +
      (d10.title ? ' title="' + esc(d10.title) + '"' : "") + ">" + esc(d10.label) + "</span>" : "";
    var pills = (Array.isArray(c.enrich_pills) ? c.enrich_pills : []).filter(function (p) {
      return p && p.key !== "mom-score-d10";
    });
    return '<article class="card fd-card" data-t="' + t + '" data-ticker="' + esc(c.ticker || t) + '">' +
      '<div class="top"><span class="sym">' + title + "</span>" + chg1dHTML(c) +
      '<span class="sc score">' + esc(String(score)) + near + "</span></div>" +
      '<div class="pills">' + pillsHTML(pills) + "</div>" +
      '<div class="stats"><span>Day ' + esc(day) + "</span><span>R20 " + esc(r20) + "</span><span>RS63 " + esc(rs63) + "</span><span>ATR% " + esc(atr) + "</span></div>" +
      "</article>";
  }

  function ensurePillsHost(node) {
    var host = node.querySelector(".pills, .chips, .badges");
    if (host) return host;
    host = document.createElement("div");
    host.className = "pills";
    var header = node.querySelector("header, .hd, .head, .row1");
    if (header) header.appendChild(host);
    else if (node.firstChild) node.insertBefore(host, node.firstChild);
    else node.appendChild(host);
    return host;
  }
  function injectPills(node, pills) {
    if (!node || !Array.isArray(pills) || !pills.length) return;
    var host = ensurePillsHost(node);
    for (var i = 0; i < pills.length; i++) {
      var p = pills[i];
      if (!p || !p.label) continue;
      var key = String(p.key || "");
      var label = String(p.label);
      if (key === "fd-bb" || (isBandDump(label) && label.indexOf(" · ") >= 0)) continue;
      if (key && node.querySelector('[data-key="' + key.replace(/"/g, "") + '"]')) continue;
      var already = host.querySelectorAll(".badge, .spike-chip, [data-key]");
      var dup = false;
      for (var j = 0; j < already.length; j++) {
        if ((already[j].textContent || "").trim() === label) { dup = true; break; }
      }
      if (dup) continue;
      var span = document.createElement("span");
      span.className = "badge spike-chip " + (p.cls || "");
      if (key) span.setAttribute("data-key", key);
      if (p.title) span.title = String(p.title);
      span.textContent = label;
      host.appendChild(span);
    }
  }
  function isChip(el) {
    if (!el || !el.classList) return false;
    return el.classList.contains("badge") || el.classList.contains("spike-chip") ||
      el.classList.contains("fd-tag-chip") || el.classList.contains("fd-bb-band") ||
      !!(el.getAttribute && el.getAttribute("data-key"));
  }
  function ownText(el) {
    if (!el) return "";
    var t = "";
    for (var n = el.firstChild; n; n = n.nextSibling) {
      if (n.nodeType === 3) t += n.textContent;
    }
    return String(t || "").replace(/\s+/g, " ").trim();
  }
  function isOnCell(el) {
    if (!el) return false;
    var cls = el.className || "";
    if (/\boff\b/.test(cls) || (el.getAttribute && el.getAttribute("data-on") === "0")) return false;
    if (/\bon\b/.test(cls) || (el.getAttribute && el.getAttribute("data-on") === "1")) return true;
    try {
      var op = (window.getComputedStyle ? getComputedStyle(el).opacity : "") || "";
      if (op && parseFloat(op) < 0.45) return false;
    } catch (e0) {}
    return false;
  }
  function catalogCells(node) {
    var cells = [];
    var els = node.querySelectorAll("*");
    for (var i = 0; i < els.length; i++) {
      var el = els[i];
      if (isChip(el)) continue;
      if (el.closest && el.closest(".fd-paper, .pills, .chips, .badges")) continue;
      var own = ownText(el);
      var full = String(el.textContent || "").replace(/\s+/g, " ").trim();
      var label = catalogKey(own) || (el.children.length === 0 ? catalogKey(full) : "");
      if (!label) continue;
      var childHits = 0;
      for (var c = 0; c < el.children.length; c++) {
        var ct = ownText(el.children[c]) || String(el.children[c].textContent || "").replace(/\s+/g, " ").trim();
        if (catalogKey(ct)) childHits++;
      }
      if (childHits >= 2) continue;
      cells.push({ el: el, label: label });
    }
    return cells;
  }
  function catalogHitsIn(text) {
    var up = String(text || "").toUpperCase();
    var hits = [];
    for (var i = 0; i < TAG_CATALOG.length; i++) {
      if (up.indexOf(TAG_CATALOG[i]) >= 0) hits.push(TAG_CATALOG[i]);
    }
    return hits;
  }
  function findTextDump(node) {
    var els = node.querySelectorAll("div, span, p, pre, td, section, b, i");
    for (var i = 0; i < els.length; i++) {
      var el = els[i];
      if (isChip(el)) continue;
      if (el.children.length > 2) continue;
      if (catalogHitsIn(el.textContent).length >= 6) return el;
    }
    return null;
  }
  function hideGhostMatrix(node, card) {
    var active = activeTagsFromCard(card);
    var cells = catalogCells(node);
    var dump = findTextDump(node);
    var i;
    if (cells.length >= 6) {
      if (!active.length) {
        for (i = 0; i < cells.length; i++) {
          if (isOnCell(cells[i].el) && active.indexOf(cells[i].label) < 0) active.push(cells[i].label);
        }
      }
      var parents = [];
      for (i = 0; i < cells.length; i++) {
        var par = cells[i].el.parentElement;
        if (!par || par === node) {
          cells[i].el.classList.add("fd-tag-off", "fd-tag-ghost");
          if (cells[i].el.parentNode) cells[i].el.parentNode.removeChild(cells[i].el);
          continue;
        }
        if (parents.indexOf(par) < 0) parents.push(par);
      }
      for (i = 0; i < parents.length; i++) {
        var kidCat = 0;
        for (var ch = 0; ch < parents[i].children.length; ch++) {
          var kid = parents[i].children[ch];
          var kt = ownText(kid) || String(kid.textContent || "").replace(/\s+/g, " ").trim();
          if (catalogKey(kt)) kidCat++;
        }
        if (kidCat >= 6 || (parents[i].children.length && kidCat * 2 >= parents[i].children.length)) {
          parents[i].classList.add("fd-tag-matrix");
          if (parents[i].parentNode) parents[i].parentNode.removeChild(parents[i]);
        } else {
          for (var k2 = 0; k2 < cells.length; k2++) {
            if (cells[k2].el.parentElement === parents[i]) {
              cells[k2].el.classList.add("fd-tag-off", "fd-tag-ghost");
              if (cells[k2].el.parentNode) cells[k2].el.parentNode.removeChild(cells[k2].el);
            }
          }
        }
      }
    } else if (dump) {
      dump.classList.add("fd-tag-matrix");
      if (dump.parentNode) dump.parentNode.removeChild(dump);
    } else {
      var offs = node.querySelectorAll(".tg.off, .tag.off, [data-on='0']");
      for (i = 0; i < offs.length; i++) {
        offs[i].classList.add("fd-tag-off", "fd-tag-ghost");
        if (offs[i].parentNode) offs[i].parentNode.removeChild(offs[i]);
      }
      var ons = node.querySelectorAll(".tg.on, .tag.on, [data-on='1']");
      for (i = 0; i < ons.length; i++) {
        if (isChip(ons[i])) continue;
        var lab = String(ons[i].textContent || "").replace(/\s+/g, " ").trim();
        if (lab && lab.length <= 28) {
          ons[i].classList.add("badge", "spike-chip", "fd-tag-chip", "fd-tag-on");
          if (active.indexOf(lab) < 0) active.push(lab);
        }
      }
    }
    if (cells.length >= 6 || dump) node.setAttribute("data-fd-ghost-stripped", "1");
    if (active.length) {
      injectPills(node, active.map(function (lab) {
        return { key: "fd-tag-" + lab, label: lab, cls: "fd-tag-on", title: lab };
      }));
    }
  }
  function isEmptyStat(text) {
    var t = String(text || "").replace(/\s+/g, " ").trim();
    return !t || t === "-" || t === "—" || t === "–" || t === "−";
  }
  function fillLabeled(node, names, val) {
    if (val == null || val === "") return;
    var shown = String(val);
    var els = node.querySelectorAll("span, b, i, em, dt, dd, div, td, th, strong, small, label");
    for (var i = 0; i < els.length; i++) {
      var el = els[i];
      if (el.closest && el.closest(".fd-paper, .pills, .fd-tag-matrix, .chips, .badges")) continue;
      var t = String(el.textContent || "").replace(/\s+/g, " ").trim();
      var up = t.toUpperCase();
      for (var n = 0; n < names.length; n++) {
        var lab = names[n].toUpperCase();
        if (up === lab) {
          var sib = el.nextElementSibling;
          if (sib && isEmptyStat(sib.textContent)) { sib.textContent = shown; return; }
          for (var c = 0; c < el.children.length; c++) {
            if (isEmptyStat(el.children[c].textContent)) { el.children[c].textContent = shown; return; }
          }
        }
        if (up === lab + " -" || up === lab + " —" || up === lab + " –" || up === lab + " −") {
          el.textContent = names[n] + " " + shown;
          return;
        }
        if (up.indexOf(lab + " ") === 0 && isEmptyStat(up.slice(lab.length))) {
          el.textContent = names[n] + " " + shown;
          return;
        }
      }
    }
  }
  function fillStats(node, card) {
    var m = (card && card.metrics) || {};
    fillLabeled(node, ["Day"], pickNum(m, ["day_pct", "r1_pct", "day"]) != null ? pickNum(m, ["day_pct", "r1_pct", "day"]) : pickNum(card, DAY_KEYS));
    fillLabeled(node, ["R20"], pickNum(m, ["r20_pct", "r20"]) != null ? pickNum(m, ["r20_pct", "r20"]) : pickNum(card, R20_KEYS));
    fillLabeled(node, ["RS63"], pickNum(m, ["rs_63", "rs63"]) != null ? pickNum(m, ["rs_63", "rs63"]) : pickNum(card, RS63_KEYS));
    fillLabeled(node, ["ATR%", "ATRS", "ATR"], pickNum(m, ["atr_pct", "atr"]) != null ? pickNum(m, ["atr_pct", "atr"]) : pickNum(card, ATR_KEYS));
    rewriteAtr(node, card);
  }
  function rewriteAtr(node, card) {
    if (!node) return;
    var m = (card && card.metrics) || {};
    var atr = pickNum(m, ["atr_pct", "atr"]);
    if (atr == null) atr = pickNum(card, ATR_KEYS);
    if (atr == null) return;
    var shown = fmtAtr(atr);
    if (!shown) return;
    var els = node.querySelectorAll("span, b, i, em, dt, dd, div, td, th, strong, small, label");
    for (var i = 0; i < els.length; i++) {
      var el = els[i];
      if (el.closest && el.closest(".fd-paper, .pills, .chips, .badges")) continue;
      var text = String(el.textContent || "").replace(/\s+/g, " ").trim();
      var up = text.toUpperCase();
      if (up === "ATR%" || up === "ATR" || up === "ATRS") {
        var sib = el.nextElementSibling;
        if (sib) { sib.textContent = shown; return; }
        for (var c = 0; c < el.children.length; c++) {
          el.children[c].textContent = shown;
          return;
        }
      }
      if (up.indexOf("ATR% ") === 0 || up.indexOf("ATRS ") === 0 || up.indexOf("ATR ") === 0) {
        var lab = up.indexOf("ATR%") === 0 ? "ATR%" : (up.indexOf("ATRS") === 0 ? "ATRS" : "ATR");
        el.textContent = lab + " " + shown;
        return;
      }
    }
  }
  function chipifyTags(node, card) {
    hideGhostMatrix(node, card);
  }
  function stripBandWhy(node, card) {
    var labels = {};
    var pills = (card && card.enrich_pills) || [];
    for (var i = 0; i < pills.length; i++) {
      if (pills[i] && pills[i].label) labels[String(pills[i].label).replace(/\s+/g, " ").trim()] = true;
    }
    var badges = node.querySelectorAll(".badge, .spike-chip, [data-key]");
    for (var b = 0; b < badges.length; b++) {
      var bt = (badges[b].textContent || "").replace(/\s+/g, " ").trim();
      if (bt) labels[bt] = true;
    }
    var nodes = node.querySelectorAll(".why, .note, .sub, .fd-bb-why, p.fd-bb-why");
    for (var j = 0; j < nodes.length; j++) {
      var el = nodes[j];
      var t = (el.textContent || "").replace(/\s+/g, " ").trim();
      if (!t) {
        if (el.parentNode) el.parentNode.removeChild(el);
        continue;
      }
      var drop = isBandDump(t) || (el.classList && el.classList.contains("fd-bb-why")) || !!labels[t];
      if (!drop && t.indexOf(" · ") >= 0 && isBandDump(t.split("·")[0])) drop = true;
      if (drop) {
        el.setAttribute("data-fd-bb", "1");
        if (el.parentNode) el.parentNode.removeChild(el);
      }
    }
  }
  function paintScoreD10(node, card) {
    if (!node) return;
    var stale = node.querySelectorAll('[data-key="mom-score-d10"]');
    for (var i = stale.length - 1; i >= 0; i--) {
      if (stale[i].getAttribute("data-key") !== "mom-score-d10") continue;
      if (stale[i].parentNode) stale[i].parentNode.removeChild(stale[i]);
    }
    var d10 = d10View(card);
    var caps = node.querySelectorAll(".score-d10, .mom-score-d10-near, [data-key='mom-score-d10-near']");
    var cap = null;
    for (var c = 0; c < caps.length; c++) {
      var cls = caps[c].className || "";
      if (!cap || cls.indexOf("mom-score-d10-near") >= 0) cap = caps[c];
    }
    for (var k = caps.length - 1; k >= 0; k--) {
      if (caps[k] !== cap && caps[k].parentNode) caps[k].parentNode.removeChild(caps[k]);
    }
    if (!d10) return;
    if (!cap) {
      var scoreEl = node.querySelector(".score, .sc");
      if (!scoreEl) return;
      cap = document.createElement("span");
      cap.className = "score-d10";
      scoreEl.appendChild(cap);
    }
    cap.setAttribute("data-key", "mom-score-d10-near");
    cap.className = "score-d10 mom-score-d10-near " + d10.side;
    if (d10.title) cap.title = d10.title;
    if (d10.delta != null && d10.delta !== "") cap.setAttribute("data-mom-score-d10", String(d10.delta));
    cap.textContent = d10.label;
  }
  function readChgDb() {
    var el = document.getElementById("fd-chg-1d-db");
    if (!el) return {};
    try { return JSON.parse(el.textContent || "{}") || {}; } catch (e) { return {}; }
  }
  function tickerOfNode(node) {
    var n = node;
    while (n && n.getAttribute) {
      var t = n.getAttribute("data-t") || n.getAttribute("data-ticker") || n.getAttribute("data-name") || "";
      if (String(t).trim()) return String(t).trim();
      n = n.parentElement;
    }
    return "";
  }
  function dayFromDb(node) {
    var db = readChgDb();
    var t = tickerOfNode(node);
    if (!t) return null;
    if (db[t] != null && isFinite(Number(db[t]))) return Number(db[t]);
    var short = shortOf(t);
    if (short && db[short] != null && isFinite(Number(db[short]))) return Number(db[short]);
    var keys = Object.keys(db);
    for (var i = 0; i < keys.length; i++) {
      if (short && shortOf(keys[i]) === short && isFinite(Number(db[keys[i]]))) return Number(db[keys[i]]);
    }
    return null;
  }
  function isNameNode(el) {
    if (!el || !el.tagName) return false;
    var tag = el.tagName;
    if (tag === "H1" || tag === "H2" || tag === "H3") return true;
    var cls = String(el.className || "");
    return /(^|\s)(name|company|sec-name|long-name|nm|ticker|sym)(\s|$)/.test(cls);
  }
  function looksLikeChromeTitle(text) {
    var t = String(text || "").replace(/\s+/g, " ").trim().toUpperCase();
    if (!t || t.length > 80) return true;
    return t === "DETAIL" || t === "HOME" || t === "FLAGS" || t === "WATCH" || t === "OUTLIERS" ||
      t === "OPTIONS" || t === "EXPERIMENTAL" || t === "PAPER" || t === "FACTOR DESK" ||
      t.indexOf("MOMENTUM") === 0 || t.indexOf("BREAKOUT") === 0 || t.indexOf("BREAKDOWN") === 0;
  }
  function nameEl(node) {
    if (!node || !node.querySelector) return null;
    if (isNameNode(node) && !looksLikeChromeTitle(ownText(node) || node.textContent)) return node;
    var header = node.querySelector(".top, header, .hd, .head, .row1, .name-row, .title-row") || node;
    var el = header.querySelector(".sym, h1, h2, h3, .name, .company, .sec-name, .long-name, .nm, .ticker");
    if (!el) return null;
    if (el.closest && el.closest(".score, .sc, .pills, .chips, .badges, .stats, svg, nav, .topnav")) return null;
    if (looksLikeChromeTitle(ownText(el) || el.textContent)) return null;
    var owner = el.closest && el.closest("article.card, article[data-t], .card, .name-card, .factor-card, .detail-card, .fd-name-card");
    if (owner && owner !== node && node.contains && node.contains(owner)) return null;
    return el;
  }
  function resolveDay(node, card) {
    var dec = dayDecimal(card);
    if (dec == null && node) {
      var found = null;
      try { found = findCard(tickerOfNode(node)); } catch (e0) { found = null; }
      if (found && found !== card) dec = dayDecimal(found);
    }
    if (dec == null && node) dec = dayFromDb(node);
    return dec;
  }
  function readCoDb() {
    var el = document.getElementById("fd-co-name-db");
    if (!el) return {};
    try { return JSON.parse(el.textContent || "{}") || {}; } catch (e) { return {}; }
  }
  function companyFromDb(node, sym) {
    var db = readCoDb();
    var t = tickerOfNode(node);
    var short = shortOf(sym || t);
    var keys = [];
    if (t) keys.push(t);
    if (sym) keys.push(sym);
    if (short) keys.push(short);
    var i, co;
    for (i = 0; i < keys.length; i++) {
      if (db[keys[i]] == null || db[keys[i]] === "") continue;
      co = cleanCompany(short, db[keys[i]]);
      if (co) return co;
    }
    var all = Object.keys(db);
    for (i = 0; i < all.length; i++) {
      if (short && shortOf(all[i]) === short) {
        co = cleanCompany(short, db[all[i]]);
        if (co) return co;
      }
    }
    return "";
  }
  function isTitleKeep(el) {
    if (!el || !el.classList) return false;
    return el.classList.contains("px-1d") || el.classList.contains("chg-1d") ||
      el.classList.contains("score") || el.classList.contains("sc") ||
      el.classList.contains("mom-score-d10-near") || el.classList.contains("badge") ||
      el.classList.contains("spike-chip");
  }
  function headingText(host) {
    if (!host) return "";
    var parts = [];
    for (var n = host.firstChild; n; n = n.nextSibling) {
      if (n.nodeType === 3) parts.push(n.textContent || "");
      else if (n.nodeType === 1 && !isTitleKeep(n)) parts.push(n.textContent || "");
    }
    return parts.join(" ").replace(/\s+/g, " ").trim();
  }
  function setHeadingText(host, title) {
    var chip = null;
    var n = host.firstChild;
    while (n) {
      var next = n.nextSibling;
      if (n.nodeType === 1 && n.classList && (n.classList.contains("px-1d") || n.classList.contains("chg-1d"))) chip = n;
      else if (!(n.nodeType === 1 && isTitleKeep(n))) host.removeChild(n);
      n = next;
    }
    var text = document.createTextNode(title);
    if (chip && chip.parentNode === host) host.insertBefore(text, chip);
    else host.insertBefore(text, host.firstChild);
  }
  function paintTitle(node, card) {
    if (!node || node.nodeType !== 1) return;
    if (node.closest && node.closest("nav, .topnav, #topnav, #gics-filter-strip, svg, .chart, .fd-chart")) return;
    var host = nameEl(node);
    if (!host) return;
    var sym = symbolOfCard(card);
    if (!sym) sym = shortOf(tickerOfNode(host) || tickerOfNode(node));
    var raw = headingText(host);
    if (!sym) {
      var piped = raw.match(/^([A-Za-z][A-Za-z0-9.\-]{0,9})\s+\|\s+(.+)$/);
      if (piped) sym = shortOf(piped[1]);
    }
    if (!sym && !isYellowKey(raw)) {
      var lead = raw.match(/^([A-Z][A-Z0-9]{0,4}(?:\.[A-Z])?)\s+(\S.*)$/);
      if (lead) sym = lead[1];
    }
    if (!sym) {
      var lone = String(raw || "").trim();
      if (/^[A-Za-z][A-Za-z0-9.\-]{0,9}$/.test(lone)) sym = shortOf(lone);
    }
    var co = companyOf(card);
    if (!co) {
      var found = null;
      try { found = findCard(sym || tickerOfNode(node)); } catch (e0) { found = null; }
      if (found) co = companyOf(found);
    }
    if (!co) co = companyFromDb(host, sym);
    if (!co) co = cleanCompany(sym, raw);
    var title = (sym && co) ? (sym + " | " + co) : (sym || co || "");
    if (!title || raw === title) return;
    setHeadingText(host, title);
  }
  function paintChg1d(node, card) {
    if (!node || node.nodeType !== 1) return;
    if (node.closest && node.closest("nav, .topnav, #topnav, #gics-filter-strip")) return;
    var host = nameEl(node);
    if (!host) return;
    var dec = resolveDay(node, card);
    var chip = null;
    for (var c = host.firstElementChild; c; c = c.nextElementSibling) {
      if (c.classList && (c.classList.contains("px-1d") || c.classList.contains("chg-1d"))) { chip = c; break; }
    }
    if (!chip && host.nextElementSibling && host.nextElementSibling.classList &&
        (host.nextElementSibling.classList.contains("px-1d") || host.nextElementSibling.classList.contains("chg-1d"))) {
      chip = host.nextElementSibling;
    }
    if (dec == null) {
      if (chip && chip.parentNode) chip.parentNode.removeChild(chip);
      return;
    }
    var view = formatChg1d(dec);
    if (!view) {
      if (chip && chip.parentNode) chip.parentNode.removeChild(chip);
      return;
    }
    var inTop = !!(host.classList && host.classList.contains("sym") && host.parentElement &&
      host.parentElement.classList && host.parentElement.classList.contains("top"));
    var homeOk = chip && chip.textContent === view.label && chip.classList.contains(view.side) &&
      (chip.parentNode === host || (inTop && chip.previousElementSibling === host));
    if (homeOk) return;
    if (!chip) chip = document.createElement("span");
    chip.className = "px-1d chg-1d " + view.side;
    chip.setAttribute("data-key", "chg-1d");
    chip.setAttribute("data-chg-1d", String(dec));
    chip.title = "1d CHG_PCT_1D";
    chip.textContent = view.label;
    if (inTop) {
      var top = host.parentElement;
      if (host.nextSibling) top.insertBefore(chip, host.nextSibling);
      else top.appendChild(chip);
      return;
    }
    var score = host.querySelector(".score, .sc, .mom-score-d10-near");
    if (score && host.contains(score)) host.insertBefore(chip, score);
    else host.appendChild(chip);
  }
  var CARD_SEL = "article.card, article[data-t], article[data-ticker], .card, .name-card, .factor-card, .detail-card, .fd-name-card";
  var DETAIL_SEL = "#detail, #view-detail, #fd-detail, .view-detail, .detail-pane, .detail-grid, [data-view='detail']";
  function paintAllChg1d() {
    var nodes = document.querySelectorAll(CARD_SEL);
    var i;
    for (i = 0; i < nodes.length; i++) {
      paintTitle(nodes[i], null);
      paintChg1d(nodes[i], null);
    }
    var details = document.querySelectorAll(DETAIL_SEL);
    for (i = 0; i < details.length; i++) {
      var kids = details[i].children || [];
      var k;
      for (k = 0; k < kids.length; k++) {
        var kid = kids[k];
        if (!kid || kid.nodeType !== 1) continue;
        if (kid.closest && kid.closest("svg, .chart, .fd-chart")) continue;
        if (isNameNode(kid) && looksLikeChromeTitle(ownText(kid) || kid.textContent)) continue;
        paintTitle(kid, null);
        paintChg1d(kid, null);
      }
      var heads = details[i].querySelectorAll("h1, h2, h3, .company, .sec-name, .long-name, .nm");
      for (k = 0; k < heads.length; k++) {
        var head = heads[k];
        if (head.closest && head.closest("svg, .chart, .fd-chart, .stats, .score, .sc, nav, .topnav, .pills, .chips, .badges")) continue;
        if (looksLikeChromeTitle(ownText(head) || head.textContent)) continue;
        paintTitle(head, null);
        paintChg1d(head, null);
      }
    }
  }
  function polishNode(node, card) {
    if (!node || node.nodeType !== 1) return node;
    node.classList.add("fd-card");
    fillStats(node, card);
    stripBandWhy(node, card);
    paintScoreD10(node, card);
    paintTitle(node, card);
    paintChg1d(node, card);
    node.setAttribute("data-fd-card-polished", "1");
    return node;
  }
  function polishHtml(html, card) {
    var wrap = document.createElement("div");
    wrap.innerHTML = String(html || "");
    var node = wrap.firstElementChild;
    if (!node) return html;
    polishNode(node, card);
    return wrap.innerHTML;
  }

  function wrapCardHTML() {
    var fn = null;
    try { fn = window.cardHTML; } catch (e0) { fn = null; }
    if (typeof fn === "function" && (fn.__fdCard || fn.__fdPaper)) {
      window.cardHTML = fn;
      return fn;
    }
    if (typeof fn !== "function") {
      try { if (typeof cardHTML === "function") fn = cardHTML; } catch (e1) { fn = null; }
    }
    if (typeof fn === "function" && (fn.__fdCard || fn.__fdPaper)) {
      window.cardHTML = fn;
      return fn;
    }
    var inner = (typeof fn === "function") ? fn : function (card) { return fallbackHTML(card); };
    var wrapped = function (card) {
      var html = "";
      try { html = inner.apply(this, arguments); } catch (e) { html = ""; }
      if (!html) html = fallbackHTML(card);
      return polishHtml(html, card);
    };
    wrapped.__fdCard = true;
    if (inner && inner.__fdPaper) wrapped.__fdPaper = true;
    wrapped.__fdInner = (typeof fn === "function") ? fn : null;
    window.cardHTML = wrapped;
    try { cardHTML = wrapped; } catch (e2) {}
    return wrapped;
  }

  function renderCard(card) {
    wrapCardHTML();
    var fn = null;
    try { if (typeof window.cardHTML === "function") fn = window.cardHTML; } catch (e) { fn = null; }
    var html = "";
    if (fn) {
      try { html = fn(card || {}); } catch (e2) { html = ""; }
    }
    if (!html) html = fallbackHTML(card);
    var wrap = document.createElement("div");
    wrap.innerHTML = html;
    var node = wrap.firstElementChild;
    if (!node) return null;
    return polishNode(node, card);
  }
  function renderRow(row) {
    row = row || {};
    var ticker = row.ticker || row.t || row.d;
    if (!shortOf(ticker)) return null;
    var found = findCard(ticker);
    var card = found || normalizeCard(row.card || null, row);
    if (!shortOf((card && (card.t || card.d || card.ticker)) || ticker)) return null;
    if (found) {
      if (!card.metrics || typeof card.metrics !== "object") card.metrics = {};
      var m = card.metrics;
      var rm = (row.metrics && typeof row.metrics === "object") ? row.metrics : {};
      if (m.r20_pct == null && (rm.r20_pct != null || row.r20 != null)) m.r20_pct = rm.r20_pct != null ? rm.r20_pct : row.r20;
      if (m.rs_63 == null && (rm.rs_63 != null || row.rs63 != null)) m.rs_63 = rm.rs_63 != null ? rm.rs_63 : row.rs63;
      if (m.atr_pct == null && (rm.atr_pct != null || row.atr_pct != null)) m.atr_pct = rm.atr_pct != null ? rm.atr_pct : row.atr_pct;
      if (m.day_pct == null && (rm.day_pct != null || row.day != null || row.ret_1d != null)) {
        m.day_pct = rm.day_pct != null ? rm.day_pct : (row.day != null ? row.day : row.ret_1d);
      }
      if (m.day_pct == null) {
        var pts = pickNum(rm, PCT_POINT_KEYS);
        if (pts == null) pts = pickNum(row, PCT_POINT_KEYS);
        if (pts == null) pts = pickNum(card, PCT_POINT_KEYS);
        if (pts != null && isFinite(Number(pts))) m.day_pct = Number(pts) / 100;
      }
      if (card.day == null && m.day_pct != null) card.day = m.day_pct;
      if (card.ret_1d == null && m.day_pct != null) card.ret_1d = m.day_pct;
    }
    var node = renderCard(card);
    if (!node) return null;
    ["hide", "fd-bb-hid", "gics-hid"].forEach(function (cls) {
      if (cls) node.classList.remove(cls);
    });
    node.addEventListener("click", function (ev) {
      if (ev.target && ev.target.closest && ev.target.closest(".fd-paper, [data-fd-paper-act]")) return;
      var sel = null;
      try { sel = window.selectTicker; } catch (e0) { sel = null; }
      if (typeof sel !== "function") {
        try { if (typeof selectTicker === "function") sel = selectTicker; } catch (e1) { sel = null; }
      }
      if (typeof sel === "function") sel(card.t || card.d || card.ticker || row.t);
    });
    return node;
  }

  window.__FD_RENDER_CARD__ = renderCard;
  window.__FD_RENDER_ROW__ = renderRow;
  window.__FD_NORMALIZE_CARD__ = normalizeCard;
  window.__FD_FIND_CARD__ = findCard;
  window.__FD_WHY_PILLS__ = whyPills;
  window.__FD_CARD_COLLECT__ = collectCards;
  window.__FD_POLISH_NODE__ = polishNode;
  window.__FD_HIDE_GHOST__ = hideGhostMatrix;
  window.__FD_PAINT_CHG_1D__ = paintAllChg1d;
  function wrapSelectChg() {
    var orig = null;
    try { orig = window.selectTicker; } catch (e0) { orig = null; }
    if (typeof orig !== "function") {
      try { if (typeof selectTicker === "function") orig = selectTicker; } catch (e1) { orig = null; }
    }
    if (typeof orig !== "function" || orig.__fdChg1d) return;
    var wrapped = function () {
      var r = orig.apply(this, arguments);
      setTimeout(paintAllChg1d, 0);
      setTimeout(paintAllChg1d, 50);
      return r;
    };
    wrapped.__fdChg1d = true;
    window.selectTicker = wrapped;
    try { selectTicker = wrapped; } catch (e2) {}
  }
  function bootChg1d() {
    wrapCardHTML();
    wrapSelectChg();
    paintAllChg1d();
  }
  wrapCardHTML();
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", bootChg1d);
  else bootChg1d();
  setTimeout(bootChg1d, 0);
  setTimeout(bootChg1d, 50);
  if (window.MutationObserver && document.documentElement) {
    var chgTimer = null;
    var chgObs = new MutationObserver(function () {
      if (chgTimer) return;
      chgTimer = setTimeout(function () { chgTimer = null; paintAllChg1d(); }, 80);
    });
    var chgRoot = document.body || document.documentElement;
    if (chgRoot) chgObs.observe(chgRoot, { childList: true, subtree: true });
  }
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


def _ensure_js(html_text: str) -> str:
    script = f'<script id="{JS_SCRIPT_ID}">\n{strip_js()}\n</script>\n'
    text = re.sub(
        rf'<script\b[^>]*\bid=["\']{JS_SCRIPT_ID}["\'][^>]*>.*?</script>\s*',
        "",
        html_text or "",
        flags=re.I | re.S,
    )
    if "</body>" in text:
        return text.replace("</body>", script + "</body>", 1)
    return text + script


_SKIP_BLOCK_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.I | re.S)
_SYM_TAG_RE = re.compile(
    r"""(<(?P<tag>span|div|b|strong)\b[^>]*\bclass\s*=\s*(?P<q>["'])(?P<cls>[^"']*\bsym\b[^"']*)(?P=q)[^>]*>)"""
    r"""(?P<body>[^<]*)"""
    r"""(</(?P=tag)>)""",
    re.I,
)
_SYM_CONCAT_RE = re.compile(
    r"""(?P<open><(?P<tag>span|div|b|strong)\b[^>]*\bclass\s*=\s*(?P<cq>["'])[^"']*\bsym\b[^"']*(?P=cq)[^>]*>)"""
    r"""(?P<oq>["'])\s*\+\s*(?P<expr>[^+\n]{1,180}?)\s*\+\s*(?P=oq)"""
    r"""</(?P=tag)>""",
    re.I,
)
_SYM_TMPL_RE = re.compile(
    r"(?P<open><(?P<tag>span|div|b|strong)\b[^>]*\bclass=[\"'][^\"']*\bsym\b[^\"']*[\"'][^>]*>)"
    r"\$\{(?P<expr>[^}]+)\}"
    r"</(?P=tag)>",
    re.I,
)
_SYM_ASSIGN_RE = re.compile(
    r"""(?P<lhs>[A-Za-z_$][\w$.\[\]'"]*\.querySelector\(\s*(?P<q>["'])\.sym\2\s*\))"""
    r"""\.(?:textContent|innerText|innerHTML)\s*=\s*(?P<expr>[^;]{1,200});""",
)
_HARVEST_CO_RE = re.compile(
    r'"(?:t|d|ticker|symbol)"\s*:\s*"(?P<tick>[A-Za-z][A-Za-z0-9.]{0,11})"'
    r"[^}]{0,500}?"
    r'"(?:short_name|SHORT_NAME|name|nm|company|company_name|NAME|LONG_COMP_NAME)"\s*:\s*"(?P<co>[^"]+)"',
    re.I,
)
_HARVEST_CO_REV_RE = re.compile(
    r'"(?:short_name|SHORT_NAME|name|nm|company|company_name|NAME|LONG_COMP_NAME)"\s*:\s*"(?P<co>[^"]+)"'
    r"[^}]{0,500}?"
    r'"(?:t|d|ticker|symbol)"\s*:\s*"(?P<tick>[A-Za-z][A-Za-z0-9.]{0,11})"',
    re.I,
)
_HARVEST_DAY_RE = re.compile(
    r'"(?:t|d|ticker|symbol)"\s*:\s*"(?P<tick>[A-Za-z][A-Za-z0-9.]{0,11})"'
    r"[^}]{0,700}?"
    r'"(?P<key>day|ret_1d|day_pct|r1_pct|CHG_PCT_1D|chg_pct_1d)"\s*:\s*(?P<num>-?\d+(?:\.\d+)?)',
    re.I,
)


def _lookup_map(mapping: Mapping[str, Any] | None, ticker: str) -> Any:
    if not mapping or not ticker:
        return None
    short = _short(ticker)
    if ticker in mapping and mapping[ticker] not in (None, ""):
        return mapping[ticker]
    if short and short in mapping and mapping[short] not in (None, ""):
        return mapping[short]
    for key, val in mapping.items():
        if short and _short(key) == short and val not in (None, ""):
            return val
    return None


def _harvest_live_cards(html_text: str) -> tuple[dict[str, str], dict[str, float]]:
    """Pull company / 1d already sitting on live card objects (``t`` + ``name``)."""
    companies: dict[str, str] = {}
    days: dict[str, float] = {}
    for rx in (_HARVEST_CO_RE, _HARVEST_CO_REV_RE):
        for match in rx.finditer(html_text or ""):
            tick = _short(match.group("tick"))
            co = _clean_company(tick, match.group("co"))
            if tick and co and tick not in companies:
                companies[tick] = co
    for match in _HARVEST_DAY_RE.finditer(html_text or ""):
        tick = _short(match.group("tick"))
        if not tick or tick in days:
            continue
        try:
            num = float(match.group("num"))
        except ValueError:
            continue
        key = match.group("key").lower()
        if key in {"chg_pct_1d"}:
            num = num / 100.0
        days[tick] = num
    return companies, days


def _sym_title(ticker: str, co_map: Mapping[str, Any]) -> str:
    sym = _short(ticker)
    if not sym:
        return ""
    co = _clean_company(sym, str(_lookup_map(co_map, sym) or ""))
    if sym and co:
        return f"{sym} | {co}"
    return sym


def _sym_chip(ticker: str, day_map: Mapping[str, Any]) -> str:
    raw = _lookup_map(day_map, ticker)
    dec = _as_float(raw)
    if dec is None:
        return ""
    label, side = format_chg_1d(dec)
    attr = html.escape(f"{dec:.8g}", quote=True)
    return (
        f'<span class="px-1d chg-1d {side}" data-key="chg-1d" data-chg-1d="{attr}" '
        f'title="1d CHG_PCT_1D">{html.escape(label)}</span>'
    )


def _bake_sym_region(region: str, day_map: Mapping[str, Any], co_map: Mapping[str, Any]) -> str:
    def repl(match: re.Match[str]) -> str:
        body = " ".join((match.group("body") or "").split())
        if not body or looks_like_chrome_title(body):
            return match.group(0)
        if " | " in body:
            sym = _short(body.split(" | ", 1)[0])
        else:
            sym = _short(body)
            if not sym or not re.fullmatch(r"[A-Z][A-Z0-9.]{0,9}", sym):
                return match.group(0)
        title = _sym_title(sym, co_map) or sym
        chip = _sym_chip(sym, day_map)
        open_tag = match.group(1)
        close_tag = match.group(6)
        shown = html.escape(title if " | " not in body else body)
        if " | " in body:
            shown = html.escape(body)
        elif title:
            shown = html.escape(title)
        else:
            shown = html.escape(sym)
        tail = region[match.end() : match.end() + 180]
        has_chip = bool(re.match(r"\s*<span\b[^>]*\b(?:px-1d|chg-1d)\b", tail, re.I))
        extra = "" if has_chip or not chip else chip
        return f"{open_tag}{shown}{close_tag}{extra}"

    return _SYM_TAG_RE.sub(repl, region)


def looks_like_chrome_title(text: str) -> bool:
    t = " ".join(str(text or "").split()).strip().upper()
    if not t or len(t) > 80:
        return True
    return t in {"DETAIL", "HOME", "FLAGS", "WATCH", "OUTLIERS", "OPTIONS", "EXPERIMENTAL", "PAPER", "FACTOR DESK"} or t.startswith(
        ("MOMENTUM", "BREAKOUT", "BREAKDOWN")
    )


_DRILL_TITLE_RE = re.compile(
    r"""(?P<lhs>\b(?:const|let|var)\s+title\s*=\s*)"""
    r"""coName\s*\?\s*`\$\{(?P<obj>[A-Za-z_$][\w$]*)\.d\}[ \t]+\$\{coName\}`"""
    r"""\s*:\s*(?P<else_expr>[^;\n]+)"""
)
_DRILL_SYM_RE = re.compile(
    r"""(?P<open><(?P<tag>div|span|b|strong)\b[^>]*\bclass\s*=\s*(?P<q>["'])[^"']*\bsym\b[^"']*(?P=q)[^>]*>)"""
    r"""\$\{title\}"""
    r"""(?P<close></(?P=tag)>)""",
    re.I,
)


def _splice_factor_drill(script: str) -> str:
    """Title line of ``factorDrillHTML``: ``SYMBOL | company`` plus the 1d chip.

    Desktop builds ``${b.d}  ${coName}`` (two spaces, no chip). The chip stays
    on that ``.sym`` line. The grey ``NAME · Ticker`` subtitle is left alone.
    """
    match = _DRILL_TITLE_RE.search(script)
    if not match and "coName" not in script:
        return script
    obj = match.group("obj") if match else "b"

    def title_repl(found: re.Match[str]) -> str:
        name = found.group("obj")
        return (
            f"{found.group('lhs')}coName ? `${{{name}.d}} | ${{coName}}` : {found.group('else_expr')}"
        )

    text = _DRILL_TITLE_RE.sub(title_repl, script) if match else script

    def sym_repl(found: re.Match[str]) -> str:
        if "__fdChgSpan" in found.group(0):
            return found.group(0)
        return (
            f"{found.group('open')}${{title}}${{window.__fdChgSpan({obj}.d || {obj}.t)}}"
            f"{found.group('close')}"
        )

    if "coName" in text and "${title}" in text:
        text = _DRILL_SYM_RE.sub(sym_repl, text)
    return text


def _splice_sym_js(script: str) -> str:
    """Point live ``.sym`` builders at the baked title + 1d chip."""
    script = _splice_factor_drill(script)

    def concat_repl(match: re.Match[str]) -> str:
        expr = match.group("expr").strip()
        if "__fdSymTitle" in expr or "__fdApplySym" in expr:
            return match.group(0)
        quote = match.group("oq")
        tag = match.group("tag")
        open_tag = match.group("open")
        return (
            f"{open_tag}{quote} + window.__fdSymTitle({expr}) + {quote}</{tag}>{quote}"
            f" + window.__fdChgSpan({expr}) + {quote}"
        )

    def tmpl_repl(match: re.Match[str]) -> str:
        expr = match.group("expr").strip()
        if "__fdSymTitle" in expr:
            return match.group(0)
        tag = match.group("tag")
        open_tag = match.group("open")
        return (
            f"{open_tag}${{window.__fdSymTitle({expr})}}</{tag}>"
            f"${{window.__fdChgSpan({expr})}}"
        )

    def assign_repl(match: re.Match[str]) -> str:
        expr = match.group("expr").strip()
        if "__fdApplySym" in expr or "__fdSymTitle" in expr:
            return match.group(0)
        return f"window.__fdApplySym({match.group('lhs')}, {expr});"

    text = _SYM_CONCAT_RE.sub(concat_repl, script)
    text = _SYM_TMPL_RE.sub(tmpl_repl, text)
    text = _SYM_ASSIGN_RE.sub(assign_repl, text)
    return text


def _splice_sym_scripts(html_text: str) -> str:
    def repl(match: re.Match[str]) -> str:
        block = match.group(0)
        head = block[:240].lower()
        if "fd-card-js" in head or "fd-sym-helpers" in head or "__fd_card_bound__" in block.lower():
            return block
        open_end = block.find(">")
        if open_end < 0:
            return block
        inner = block[open_end + 1 :]
        close_at = inner.lower().rfind("</script>")
        if close_at < 0:
            return block
        body = inner[:close_at]
        spliced = _splice_sym_js(body)
        if spliced == body:
            return block
        return block[: open_end + 1] + spliced + inner[close_at:]

    return re.sub(r"<script\b[^>]*>.*?</script>", repl, html_text, flags=re.I | re.S)


def sym_helper_js() -> str:
    """Globals the spliced live ``cardHTML`` calls. Reads the baked name/1d maps."""
    return r"""
(function () {
  function shortOf(t) { return String(t || "").trim().split(/\s+/)[0].toUpperCase(); }
  function readDb(id) {
    var el = document.getElementById(id);
    if (!el) return {};
    try { return JSON.parse(el.textContent || "{}") || {}; } catch (e) { return {}; }
  }
  function lookup(db, sym) {
    if (!db) return null;
    var raw = String(sym || "").trim();
    var short = shortOf(raw);
    if (raw && db[raw] != null && db[raw] !== "") return db[raw];
    if (short && db[short] != null && db[short] !== "") return db[short];
    var keys = Object.keys(db);
    for (var i = 0; i < keys.length; i++) {
      if (short && shortOf(keys[i]) === short && db[keys[i]] != null && db[keys[i]] !== "") return db[keys[i]];
    }
    return null;
  }
  window.__fdSymTitle = function (sym) {
    var s = shortOf(sym);
    if (!s) return String(sym == null ? "" : sym);
    var co = lookup(readDb("fd-co-name-db"), s);
    co = co == null ? "" : String(co).replace(/\s+/g, " ").trim();
    if (!co || co.toUpperCase() === s) return s;
    if (co.toUpperCase().indexOf(s + " | ") === 0) return s + " | " + co.slice(s.length + 3).trim();
    return s + " | " + co;
  };
  window.__fdChgSpan = function (sym) {
    var dec = lookup(readDb("fd-chg-1d-db"), sym);
    var n = Number(dec);
    if (dec == null || !isFinite(n)) return "";
    var rounded = Math.round(n * 1000) / 10;
    var label, side;
    if (rounded === 0) { label = "0.0%"; side = "flat"; }
    else if (rounded > 0) { label = "+" + rounded.toFixed(1) + "%"; side = "up"; }
    else { label = "\u2212" + Math.abs(rounded).toFixed(1) + "%"; side = "down"; }
    return '<span class="px-1d chg-1d ' + side + '" data-key="chg-1d" title="1d CHG_PCT_1D">' + label + "</span>";
  };
  window.__fdApplySym = function (el, sym) {
    if (!el) return el;
    el.textContent = window.__fdSymTitle(sym);
    var parent = el.parentNode;
    if (!parent) return el;
    var html = window.__fdChgSpan(sym);
    var chip = el.nextElementSibling;
    var isChip = chip && chip.classList && (chip.classList.contains("px-1d") || chip.classList.contains("chg-1d"));
    if (!html) {
      if (isChip) parent.removeChild(chip);
      return el;
    }
    var hold = document.createElement("div");
    hold.innerHTML = html;
    var node = hold.firstChild;
    if (!node) return el;
    if (isChip) parent.replaceChild(node, chip);
    else if (el.nextSibling) parent.insertBefore(node, el.nextSibling);
    else parent.appendChild(node);
    return el;
  };
})();
""".strip()


def _ensure_sym_helpers(html_text: str) -> str:
    tag = f'<script id="fd-sym-helpers">\n{sym_helper_js()}\n</script>\n'
    text = re.sub(
        r'<script\b[^>]*\bid=["\']fd-sym-helpers["\'][^>]*>.*?</script>\s*',
        "",
        html_text or "",
        flags=re.I | re.S,
    )
    return _insert_early(text, tag)


def _insert_early(html_text: str, tag: str) -> str:
    match = re.search(r"<head[^>]*>", html_text or "", re.I)
    if match:
        at = match.end()
        return html_text[:at] + "\n" + tag + html_text[at:]
    return tag + (html_text or "")


def bake_dense_headers(
    html_text: str,
    day_map: Mapping[str, Any] | None = None,
    co_map: Mapping[str, Any] | None = None,
) -> str:
    """Write ``SYMBOL | company`` and the 1d chip into dense ``.sym`` markup.

    ``paintTitle`` no-ops when that title equals the ticker already in ``.sym``
    and the page has no company / 1d to add. This rewrites the HTML string
    ``write_dash`` saves, and retargets a live ``cardHTML`` that concatenates
    the ticker into ``.sym``.
    """
    text = html_text or ""
    harvested_co, harvested_day = _harvest_live_cards(text)
    companies = dict(harvested_co)
    days = dict(harvested_day)
    if co_map:
        companies.update({str(k): str(v) for k, v in co_map.items() if v not in (None, "")})
    if day_map:
        for key, val in day_map.items():
            num = _as_float(val)
            if num is not None:
                days[str(key)] = num
    parts: list[str] = []
    last = 0
    for block in _SKIP_BLOCK_RE.finditer(text):
        parts.append(_bake_sym_region(text[last : block.start()], days, companies))
        parts.append(block.group(0))
        last = block.end()
    parts.append(_bake_sym_region(text[last:], days, companies))
    return _splice_sym_scripts("".join(parts))


def embed_chg_db(mapping: Mapping[str, Any] | None) -> str:
    blob = json.dumps(dict(mapping or {}), separators=(",", ":"), ensure_ascii=True)
    blob = blob.replace("</", "<\\/")
    return f'<script type="application/json" id="{DB_SCRIPT_ID}">{blob}</script>\n'


def _read_embedded_db(html_text: str, script_id: str) -> dict[str, Any]:
    match = re.search(
        rf'<script\b[^>]*\bid=["\']{re.escape(script_id)}["\'][^>]*>(.*?)</script>',
        html_text or "",
        re.I | re.S,
    )
    if not match:
        return {}
    raw = (match.group(1) or "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return dict(data) if isinstance(data, dict) else {}


def _merge_embedded_db(existing: Mapping[str, Any] | None, incoming: Mapping[str, Any] | None) -> dict[str, Any]:
    """Keep every prior ticker. An empty or one-name map must not wipe the book."""
    merged: dict[str, Any] = {}
    for key, val in dict(existing or {}).items():
        if val is None or val == "":
            continue
        merged[str(key)] = val
    if incoming is None:
        return merged
    for key, val in dict(incoming).items():
        if val is None or val == "":
            continue
        merged[str(key)] = val
    return merged


def _ensure_chg_db(html_text: str, day_map: Mapping[str, Any] | None) -> str:
    """Embed ticker → decimal 1d for the whole book. ``None`` keeps an existing db.

    A later call with ``{}`` or a single ticker merges into the db already in
    the page. It does not replace a full book with an empty object or one name.
    """
    existing = _read_embedded_db(html_text, DB_SCRIPT_ID)
    if day_map is None:
        if existing or re.search(rf"""\bid=["']{DB_SCRIPT_ID}["']""", html_text or "", re.I):
            return html_text or ""
        day_map = {}
    merged = _merge_embedded_db(existing, day_map)
    tag = embed_chg_db(merged)
    text = re.sub(
        rf'<script\b[^>]*\bid=["\']{DB_SCRIPT_ID}["\'][^>]*>.*?</script>\s*',
        "",
        html_text or "",
        count=1,
        flags=re.I | re.S,
    )
    return _insert_early(text, tag)


def embed_co_db(mapping: Mapping[str, Any] | None) -> str:
    blob = json.dumps(dict(mapping or {}), separators=(",", ":"), ensure_ascii=True)
    blob = blob.replace("</", "<\\/")
    return f'<script type="application/json" id="{CO_DB_SCRIPT_ID}">{blob}</script>\n'


def _ensure_co_db(html_text: str, co_map: Mapping[str, Any] | None) -> str:
    """Embed ticker → short company name for the whole book. ``None`` keeps an existing db.

    A later call with ``{}`` or a single name merges. It does not replace the book.
    """
    existing = _read_embedded_db(html_text, CO_DB_SCRIPT_ID)
    if co_map is None:
        if existing or re.search(rf"""\bid=["']{CO_DB_SCRIPT_ID}["']""", html_text or "", re.I):
            return html_text or ""
        co_map = {}
    merged = _merge_embedded_db(existing, co_map)
    tag = embed_co_db(merged)
    text = re.sub(
        rf'<script\b[^>]*\bid=["\']{CO_DB_SCRIPT_ID}["\'][^>]*>.*?</script>\s*',
        "",
        html_text or "",
        count=1,
        flags=re.I | re.S,
    )
    return _insert_early(text, tag)


def ensure_embedded(
    html_text: str,
    day_map: Mapping[str, Any] | None = None,
    co_map: Mapping[str, Any] | None = None,
) -> str:
    """Inject portable card CSS + wrap live ``cardHTML``. Safe on ~2.7–4.8MB HTML.

    ``day_map`` is ticker → decimal 1-day return (Bloomberg ``CHG_PCT_1D`` / 100).
    ``co_map`` is ticker → short company name (Bloomberg ``NAME``), not ``SYMBOL | name``.
    Baked cards and the DETAIL name card pick both up on embed. Paper Buy/Sell is
    not part of this renderer.
    """
    text = html_text or ""
    text = _ensure_css(text)
    text = _ensure_chg_db(text, day_map)
    text = _ensure_co_db(text, co_map)
    text = bake_dense_headers(text, day_map, co_map)
    text = _ensure_sym_helpers(text)
    text = _ensure_js(text)
    return text
