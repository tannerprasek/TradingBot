"""Portable MOM card renderer for Factor Desk tabs.

Live Momentum Up/Down chrome (``cardHTML``) is the gold standard. Tabs must
not invent a second card skin. This module:

1. Wraps live ``window.cardHTML`` (or installs a MOM-shaped fallback).
2. Normalizes a **full MOM-shaped card object** (ticker, score, tags, stats,
   ``enrich_pills``) so Breakout/Breakdown/Outliers/search can pass the same
   shape into one renderer.
3. Polishes tag / why chrome on the **card node itself** (``.badge`` /
   ``.spike-chip``) so moving a card between ``#breakout-grid``, ``#home``,
   and mom grids does not need tab-specific CSS.

Breakout "why" / streak belong on ``enrich_pills`` as short chips
(``band 10``, ``+3/7d``, ``↑3d>5``) — never a brown ``.why`` dump box.

Does not wholesale replace live ~2.7–4.8MB ``factorbook.html``. Recopy this
module next to live ``desk_dash.py`` and call ``ensure_embedded``.
"""

from __future__ import annotations

import json
import re
from typing import Any, Mapping, MutableMapping

CSS_STYLE_ID = "fd-card-css"
JS_SCRIPT_ID = "fd-card-js"
JS_VER = "pr17-stats"

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
    return card


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
    for key in DAY_KEYS + R20_KEYS + RS63_KEYS + ATR_KEYS + ("score", "mom_score"):
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
article.card .tg.off,
article.card .tag.off,
article.card .fd-tag-off,
article.card .fd-tag-ghost,
article.card .tg[data-on="0"],
article.card .tag[data-on="0"],
article.card .fd-tag-matrix,
article.card .fd-tag-matrix * {
  display: none !important;
  visibility: hidden !important;
  font-size: 0 !important;
  line-height: 0 !important;
  height: 0 !important;
  overflow: hidden !important;
  opacity: 0 !important;
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
""".strip()


def strip_js() -> str:
    """Wrap live ``cardHTML``. Tabs call ``__FD_RENDER_CARD__(card)`` only."""
    return r"""
(function () {
  var CARD_VER = "pr17-stats";
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
    return card;
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
    ["day","r20","rs63","atr_pct","R20","RS63"].forEach(function (k) {
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
  function fallbackHTML(c) {
    c = c || {};
    var t = esc(shortOf(c.d || c.t || c.ticker || c.name || ""));
    var score = c.score != null ? c.score : (c.mom_score != null ? c.mom_score : "");
    var day = fmt(c.day != null ? c.day : (c.Day != null ? c.Day : (c.ret_1d != null ? c.ret_1d : null)));
    var r20 = fmt(c.r20 != null ? c.r20 : (c.R20 != null ? c.R20 : null));
    var rs63 = fmt(c.rs63 != null ? c.rs63 : (c.RS63 != null ? c.RS63 : null));
    var atr = fmt(c.atr_pct != null ? c.atr_pct : (c.atrs != null ? c.atrs : (c.atr != null ? c.atr : (c.ATRS != null ? c.ATRS : (c.ATR != null ? c.ATR : null)))));
    return '<article class="card fd-card" data-t="' + t + '" data-ticker="' + esc(c.ticker || t) + '">' +
      "<header><h2>" + t + '</h2><span class="sc">' + esc(String(score)) + "</span>" +
      '<div class="pills">' + pillsHTML(c.enrich_pills) + "</div></header>" +
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
    fillLabeled(node, ["Day"], pickNum(card, DAY_KEYS));
    fillLabeled(node, ["R20"], pickNum(card, R20_KEYS));
    fillLabeled(node, ["RS63"], pickNum(card, RS63_KEYS));
    fillLabeled(node, ["ATR%", "ATRS", "ATR"], pickNum(card, ATR_KEYS));
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
  function polishNode(node, card) {
    if (!node || node.nodeType !== 1) return node;
    node.classList.add("fd-card");
    ensurePillsHost(node);
    injectPills(node, (card && card.enrich_pills) || []);
    chipifyTags(node, card);
    fillStats(node, card);
    stripBandWhy(node, card);
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
    if (typeof fn === "function" && fn.__fdCard) return fn;
    if (typeof fn !== "function") {
      try { if (typeof cardHTML === "function") fn = cardHTML; } catch (e1) { fn = null; }
    }
    if (typeof fn === "function" && fn.__fdCard) {
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
    var found = findCard(row.ticker || row.t || row.d);
    var card = normalizeCard(found, row);
    var node = renderCard(card);
    if (!node) return null;
    node.classList.remove("hide", "fd-bb-hid", "gics-hid");
    node.addEventListener("click", function (ev) {
      if (ev.target && ev.target.closest && ev.target.closest(".fd-paper, [data-fd-paper-act]")) return;
      var sel = window.selectTicker || (typeof selectTicker === "function" ? selectTicker : null);
      if (sel) sel(card.t || card.d || card.ticker || row.t);
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
  wrapCardHTML();
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", wrapCardHTML);
  setTimeout(wrapCardHTML, 0);
  setTimeout(wrapCardHTML, 50);
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


def ensure_embedded(html_text: str) -> str:
    """Inject portable card CSS + wrap live ``cardHTML``. Safe on ~2.7–4.8MB HTML.

    Call **before** ``paper_trade.ensure_embedded`` so Buy/Sell still wrap the
    polished renderer. Breakout/Breakdown then mount via ``__FD_RENDER_ROW__``.
    """
    text = html_text or ""
    text = _ensure_css(text)
    text = _ensure_js(text)
    return text
