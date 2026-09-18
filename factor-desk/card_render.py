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

Live ``cardHTML`` metrics row: ``metrics.atr_pct`` is already percent points
(~1.5–4.8). ``fmtPct`` does ``(x*100).toFixed``, so ``fmtPct(m.atr_pct,1)``
paints ~150–480%. ``r20_pct`` is a fraction and must keep ``fmtPct``. RS63
stays ``fmtNum``.
"""

from __future__ import annotations

import math
import re
from typing import Any, Mapping, MutableMapping

CSS_STYLE_ID = "fd-card-css"
JS_SCRIPT_ID = "fd-card-js"

_BAND_WHY_RE = re.compile(r"^band\s", re.I)
_DUMP_PILL_KEYS = frozenset({"fd-bb"})
# Live desk: fmtPct(m.atr_pct,1) — object prefix + optional digits.
_ATR_FMT_PCT_RE = re.compile(
    r"fmtPct\s*\(\s*((?:[A-Za-z_$][\w$]*\s*\.\s*)+)atr_pct(\s*,\s*[^)]+)?\s*\)",
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


def fmt_atr_pct(value: Any, digits: int = 1) -> str:
    """ATR% display. ``atr_pct`` is percent points; fractions still ×100."""
    if value is None or value == "":
        return "—"
    try:
        x = float(value)
    except (TypeError, ValueError):
        return "—"
    if not math.isfinite(x):
        return "—"
    n = 1 if digits is None else int(digits)
    if abs(x) < 1:
        return f"{x * 100:.{n}f}%"
    return f"{x:.{n}f}%"


def patch_live_atr_fmt(html_text: str) -> str:
    """Rewrite live ``fmtPct(m.atr_pct,1)``; leave ``r20_pct`` on ``fmtPct``.

    ``atr_pct`` is already percent points, so paint with ``fmtNum`` + ``"%"``.
    Idempotent: the replacement no longer contains ``fmtPct(...atr_pct``.
    """

    def repl(match: re.Match[str]) -> str:
        obj = match.group(1)
        rest = match.group(2) or ",1"
        return f'(fmtNum({obj}atr_pct{rest})+"%")'

    return _ATR_FMT_PCT_RE.sub(repl, html_text or "")


def _delta_up(delta: Any) -> bool:
    try:
        return float(delta) >= 0
    except (TypeError, ValueError):
        return True


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
article.card .fd-tag-off {
  display: none !important;
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
""".strip()


def strip_js() -> str:
    """Wrap live ``cardHTML``. Tabs call ``__FD_RENDER_CARD__(card)`` only."""
    return r"""
(function () {
  function fdFmtNum(x, n) {
    if (x == null || x === "") return "—";
    x = +x;
    if (!isFinite(x)) return "—";
    n = n == null ? 1 : n;
    return x.toFixed(n);
  }
  function fdFmtPct(x, n) {
    if (x == null || x === "") return "—";
    x = +x;
    if (!isFinite(x)) return "—";
    n = n == null ? 1 : n;
    return (x * 100).toFixed(n) + "%";
  }
  function fmtAtrPct(x, n) {
    if (x == null || x === "") return "—";
    x = +x;
    if (!isFinite(x)) return "—";
    n = n == null ? 1 : n;
    if (Math.abs(x) < 1) {
      var pctFn = (typeof window.fmtPct === "function") ? window.fmtPct : fdFmtPct;
      return pctFn(x, n);
    }
    var numFn = (typeof window.fmtNum === "function") ? window.fmtNum : fdFmtNum;
    return numFn(x, n) + "%";
  }
  window.fmtAtrPct = fmtAtrPct;
  if (typeof window.fmtNum !== "function") window.fmtNum = fdFmtNum;
  if (typeof window.fmtPct !== "function") window.fmtPct = fdFmtPct;

  if (window.__FD_CARD_BOUND__) return;
  window.__FD_CARD_BOUND__ = true;

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
  function fmtNum(x, n) { return fdFmtNum(x, n); }
  function fmtPct(x, n) { return fdFmtPct(x, n); }
  function rewriteAtrCalls(src) {
    return String(src).replace(
      /fmtPct\s*\(\s*((?:[A-Za-z_$][\w$]*\s*\.\s*)+)atr_pct(\s*,\s*[^)]+)?\s*\)/gi,
      function (_m, obj, rest) { return "(fmtNum(" + obj + "atr_pct" + (rest || ",1") + ")+\"%\")"; }
    );
  }
  function rewriteAtrHtml(html) {
    return String(html || "").replace(
      /(ATR%?\s*)([+-]?\d+(?:\.\d+)?)(%)/gi,
      function (all, label, num, pct) {
        var x = parseFloat(num);
        if (!isFinite(x)) return all;
        if (Math.abs(x) >= 50) x = x / 100;
        return label + x.toFixed(1) + pct;
      }
    );
  }
  function patchFnSource(fn) {
    if (!fn || fn.__fdAtr) return fn;
    try {
      var src = Function.prototype.toString.call(fn);
      var next = rewriteAtrCalls(src);
      if (next === src) { fn.__fdAtr = true; return fn; }
      var patched = (0, eval)("(" + next + ")");
      if (typeof patched === "function") {
        patched.__fdAtr = true;
        patched.__fdInner = fn.__fdInner || fn;
        return patched;
      }
    } catch (e) {}
    fn.__fdAtr = true;
    return fn;
  }
  function isBandDump(text) { return /^\s*band\s/i.test(String(text || "")); }
  function deltaUp(d) { var n = parseFloat(d); return !(isFinite(n) && n < 0); }

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
    if (card) { for (var k in card) out[k] = card[k]; }
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
    var out = copyCard(card);
    var display = shortOf(out.d || out.t || out.ticker || out.name || out.symbol || row.t || row.ticker || row.d || "");
    if (display) {
      if (!out.t) out.t = display;
      if (!out.d) out.d = display;
      if (!out.name) out.name = display;
      if (!out.ticker) out.ticker = row.ticker || out.ticker || display;
    }
    if (out.score == null && row.score != null) out.score = row.score;
    if (out.mom_score == null && row.score != null) out.mom_score = row.score;
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
    var m = (c.metrics && typeof c.metrics === "object") ? c.metrics : c;
    var t = esc(shortOf(c.d || c.t || c.ticker || c.name || ""));
    var score = c.score != null ? c.score : (c.mom_score != null ? c.mom_score : "");
    var r20 = (m.r20_pct != null && m.r20_pct !== "")
      ? fmtPct(m.r20_pct, 1)
      : fmt(c.r20 != null ? c.r20 : (c.R20 != null ? c.R20 : null));
    var rs63Val = (m.rs63 != null && m.rs63 !== "") ? m.rs63 : (c.rs63 != null ? c.rs63 : (c.RS63 != null ? c.RS63 : null));
    var rs63 = (rs63Val != null && rs63Val !== "" && isFinite(+rs63Val)) ? fmtNum(rs63Val, 1) : fmt(rs63Val);
    var atr = (m.atr_pct != null && m.atr_pct !== "")
      ? fmtAtrPct(m.atr_pct, 1)
      : fmt(c.atrs != null ? c.atrs : (c.atr != null ? c.atr : (c.ATRS != null ? c.ATRS : (c.ATR != null ? c.ATR : null))));
    return '<article class="card fd-card" data-t="' + t + '" data-ticker="' + esc(c.ticker || t) + '">' +
      "<header><h2>" + t + '</h2><span class="sc">' + esc(String(score)) + "</span>" +
      '<div class="pills">' + pillsHTML(c.enrich_pills) + "</div></header>" +
      '<div class="stats"><span>R20 ' + esc(r20) + "</span><span>RS63 " + esc(rs63) + "</span><span>ATR% " + esc(atr) + "</span></div>" +
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
  function chipifyTags(node) {
    var roots = node.querySelectorAll(".tags, .tgs, .tag-row, .tg-row, .triggers, .flags-row, .taggrid");
    function paint(el, forceOn) {
      if (!el || !el.classList) return;
      var text = (el.textContent || "").replace(/\s+/g, " ").trim();
      if (!text || text.length > 28) return;
      el.classList.add("badge", "spike-chip", "fd-tag-chip");
      var on = forceOn || /\bon\b/.test(el.className) || el.getAttribute("data-on") === "1";
      var off = /\boff\b/.test(el.className) || el.getAttribute("data-on") === "0";
      if (on) { el.classList.add("fd-tag-on"); el.classList.remove("fd-tag-off"); }
      else if (off) { el.classList.add("fd-tag-off"); el.classList.remove("fd-tag-on"); }
      else el.classList.add("fd-tag-on");
    }
    var i, j, kids, anyOn;
    if (roots.length) {
      for (i = 0; i < roots.length; i++) {
        roots[i].classList.add("fd-tag-wrap");
        kids = roots[i].children;
        anyOn = false;
        for (j = 0; j < kids.length; j++) {
          if (/\bon\b/.test(kids[j].className) || kids[j].getAttribute("data-on") === "1") anyOn = true;
        }
        for (j = 0; j < kids.length; j++) paint(kids[j], !anyOn);
      }
      return;
    }
    var spans = node.querySelectorAll("span.tg, span.tag, .tg, .tag");
    anyOn = false;
    for (i = 0; i < spans.length; i++) {
      if (/\bon\b/.test(spans[i].className) || spans[i].getAttribute("data-on") === "1") anyOn = true;
    }
    for (i = 0; i < spans.length; i++) paint(spans[i], !anyOn);
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
    chipifyTags(node);
    stripBandWhy(node, card);
    node.setAttribute("data-fd-card-polished", "1");
    return node;
  }
  function polishHtml(html, card) {
    html = rewriteAtrHtml(html);
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
    var raw = fn;
    while (raw && raw.__fdInner) raw = raw.__fdInner;
    if (typeof raw === "function") raw = patchFnSource(raw);
    var inner = (typeof raw === "function") ? raw : ((typeof fn === "function") ? fn : function (card) { return fallbackHTML(card); });
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
    var card = normalizeCard(findCard(row.ticker || row.t || row.d), row);
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


def ensure_embedded(html_text: str) -> str:
    """Inject portable card CSS + wrap live ``cardHTML``. Safe on ~2.7–4.8MB HTML.

    Call **before** ``paper_trade.ensure_embedded`` so Buy/Sell still wrap the
    polished renderer. Breakout/Breakdown then mount via ``__FD_RENDER_ROW__``.
    Patches live ``fmtPct(m.atr_pct,1)`` in ``C:\\Users\\MLP\\Desktop\\factorbook.html``
    (and any copy ``write_combined`` writes) without replacing ``desk_dash.py``.
    """
    text = patch_live_atr_fmt(html_text or "")
    text = _ensure_css(text)
    text = _ensure_js(text)
    return text
