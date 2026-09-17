"""Home-card chart marks: tag-trigger polish + momentum streak bounds.

Live Factor Desk already plots **tag-trigger** marks on the name chart.
This module does two things on every ``write_combined``:

1. Re-layout those existing trigger labels (spacing, alignment, overlap,
   clutter) so they stay readable without burying the price series.
2. Add **streak begin / end** marks for the Feature A run vs score 5.
   Same overlay language as tag triggers, different glyph/color.

Skinny generator cards get a compact SVG sparkline when a price (or score)
series is available. Live ~2.7MB HTML is patched: overlay JS + JSON db.
"""

from __future__ import annotations

import html
import json
import logging
import re
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

HERE = Path(__file__).resolve().parent

import dapi_enrich  # noqa: E402
import mom_streak  # noqa: E402

LOG = logging.getLogger("chart_marks")

DB_SCRIPT_ID = "fd-chart-db"
JS_SCRIPT_ID = "fd-chart-marks-js"
CSS_STYLE_ID = "fd-chart-marks-css"
CHART_WIDTH = 240
CHART_HEIGHT = 72
PAD_X = 8
PLOT_TOP = 16
PLOT_BOT = 56
TAG_CLUSTER_PX = 12
LABEL_H = 11

TAG_KEYS = ("tag_triggers", "tags", "digest_tags", "chart_tags", "triggered_tags")


def _as_date(value: Any) -> date | None:
    return mom_streak._as_date(value)


def _ticker_key(ticker: str) -> str:
    return mom_streak._ticker_key(ticker)


def _short(ticker: str) -> str:
    return mom_streak._short(ticker)


def tags_of(card: Mapping[str, Any] | None) -> list[dict[str, str]]:
    """Normalize card tag-trigger dates. Does not invent triggers."""
    if not card:
        return []
    raw: Any = None
    for key in TAG_KEYS:
        if card.get(key):
            raw = card.get(key)
            break
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = [raw]
    out: list[dict[str, str]] = []
    if not isinstance(raw, (list, tuple)):
        return out
    for item in raw:
        if isinstance(item, Mapping):
            d = _as_date(item.get("date") or item.get("asof") or item.get("day"))
            label = str(item.get("label") or item.get("tag") or item.get("name") or "tag").strip()
        else:
            d = _as_date(item)
            label = "tag"
        if d is None:
            continue
        out.append({"date": d.isoformat(), "label": label or "tag"})
    return out


def layout_marks(
    marks: Sequence[Mapping[str, Any]] | None,
    *,
    width: int = CHART_WIDTH,
    height: int = CHART_HEIGHT,
) -> list[dict[str, Any]]:
    """Collision-avoiding layout. ``x`` may be 0–1 fraction or already px.

    Tag-trigger labels sit **above** the series. Streak bounds sit **on** the
    series as diamonds, with a tiny caption along the bottom edge.
    Nearby tag labels collapse onto one caption (``G4 · EVT``) instead of
    stacking into the line.
    """
    width = max(int(width), 40)
    height = max(int(height), 40)
    laid: list[dict[str, Any]] = []
    tags: list[dict[str, Any]] = []
    streaks: list[dict[str, Any]] = []
    for raw in marks or []:
        if not isinstance(raw, Mapping):
            continue
        try:
            x = float(raw.get("x"))
        except (TypeError, ValueError):
            continue
        if 0 <= x <= 1:
            x = PAD_X + x * (width - 2 * PAD_X)
        x = max(PAD_X, min(width - PAD_X, x))
        kind = str(raw.get("kind") or "tag")
        rec = {
            "x": round(x, 1),
            "kind": kind,
            "label": str(raw.get("label") or ""),
            "side": raw.get("side"),
            "title": str(raw.get("title") or raw.get("label") or ""),
            "show_label": True,
        }
        if kind.startswith("streak"):
            streaks.append(rec)
        else:
            tags.append(rec)

    tags.sort(key=lambda m: m["x"])
    clusters: list[list[dict[str, Any]]] = []
    for mark in tags:
        if clusters and abs(mark["x"] - clusters[-1][-1]["x"]) < TAG_CLUSTER_PX:
            clusters[-1].append(mark)
        else:
            clusters.append([mark])
    for group in clusters:
        labels = [m["label"] for m in group if m.get("label")]
        caption = " · ".join(labels[:3])
        if len(labels) > 3:
            caption = caption + f" +{len(labels) - 3}"
        titles = [m["title"] for m in group if m.get("title")]
        cx = sum(m["x"] for m in group) / len(group)
        for i, mark in enumerate(group):
            mark["x"] = round(cx if i == 0 else mark["x"], 1)
            mark["y"] = PLOT_TOP
            mark["label_y"] = 10
            mark["show_label"] = i == 0 and bool(caption)
            if i == 0:
                mark["label"] = caption
                mark["title"] = " · ".join(titles) if titles else caption
            else:
                mark["show_label"] = False
            laid.append(mark)

    streaks.sort(key=lambda m: (m["x"], 0 if m["kind"] == "streak-start" else 1))
    streak_groups: list[list[dict[str, Any]]] = []
    for mark in streaks:
        mark["y"] = float(PLOT_TOP + (PLOT_BOT - PLOT_TOP) * 0.55)
        mark["label_y"] = height - 8
        if streak_groups and abs(mark["x"] - streak_groups[-1][-1]["x"]) < 6:
            streak_groups[-1].append(mark)
        else:
            streak_groups.append([mark])
    for group in streak_groups:
        kinds = {m["kind"] for m in group}
        has_start = "streak-start" in kinds
        has_end = any(k.startswith("streak-end") for k in kinds)
        # Same-x (or 1-day) start + end: one caption, both glyphs may still sit
        # on the same pixel so a 1-day run reads as a single diamond.
        if has_start and has_end and len(group) >= 2:
            for i, mark in enumerate(group):
                if i == 0:
                    mark["show_label"] = True
                    mark["label"] = group[0].get("label") or group[-1].get("label") or "1d"
                    mark["title"] = group[0].get("title") or "streak start = end (still open)"
                else:
                    mark["show_label"] = False
        for mark in group:
            laid.append(mark)

    # Keep captions inside the viewBox and un-crowd start/end captions.
    last_streak_label_x = -999.0
    for mark in laid:
        if mark.get("show_label") and mark.get("label"):
            est = 3.2 * len(str(mark["label"]))
            lx = min(max(mark["x"], PAD_X + est / 2), width - PAD_X - est / 2)
            if str(mark.get("kind") or "").startswith("streak") and abs(lx - last_streak_label_x) < 16:
                lx = min(width - PAD_X - est / 2, last_streak_label_x + 16)
            mark["label_x"] = round(lx, 1)
            if str(mark.get("kind") or "").startswith("streak"):
                last_streak_label_x = mark["label_x"]
        else:
            mark["label_x"] = mark["x"]
    return laid


def _dates_to_x(dates: Sequence[date], width: int = CHART_WIDTH) -> dict[str, float]:
    ordered = sorted({d for d in dates if d is not None})
    if not ordered:
        return {}
    if len(ordered) == 1:
        return {ordered[0].isoformat(): width / 2}
    lo = ordered[0].toordinal()
    hi = ordered[-1].toordinal()
    span = max(hi - lo, 1)
    inner = width - 2 * PAD_X
    return {d.isoformat(): PAD_X + (d.toordinal() - lo) / span * inner for d in ordered}


def _px_points(series: Sequence[tuple[date, float]], width: int, height: int) -> str:
    if not series:
        return ""
    xs = _dates_to_x([d for d, _ in series], width)
    vals = [p for _, p in series]
    lo, hi = min(vals), max(vals)
    if hi == lo:
        hi = lo + 1.0
    inner_h = PLOT_BOT - PLOT_TOP
    bits: list[str] = []
    for d, p in series:
        x = xs.get(d.isoformat())
        if x is None:
            continue
        y = PLOT_BOT - (p - lo) / (hi - lo) * inner_h
        bits.append(f"{x:.1f},{y:.1f}")
    return " ".join(bits)


def _marks_for_card(
    card: Mapping[str, Any],
    *,
    width: int = CHART_WIDTH,
) -> list[dict[str, Any]]:
    dates: list[date] = []
    tags = tags_of(card)
    start = _as_date(card.get("mom_streak_start"))
    end = _as_date(card.get("mom_streak_end"))
    for t in tags:
        d = _as_date(t.get("date"))
        if d:
            dates.append(d)
    if start:
        dates.append(start)
    if end:
        dates.append(end)
    px_series = _px_series(card)
    dates.extend(d for d, _ in px_series)
    score_series = card.get("mom_score_series")
    if isinstance(score_series, list):
        for row in score_series:
            if isinstance(row, Mapping):
                d = _as_date(row.get("date"))
                if d:
                    dates.append(d)
    xs = _dates_to_x(dates, width)
    marks: list[dict[str, Any]] = []
    for t in tags:
        d = t["date"]
        if d not in xs:
            continue
        marks.append(
            {
                "x": xs[d],
                "kind": "tag",
                "label": t.get("label") or "tag",
                "title": f"tag trigger {t.get('label') or ''} {d}".strip(),
            }
        )
    side = card.get("mom_streak_side")
    label = card.get("mom_streak_label") or ""
    same_day = bool(start and end and start == end)
    if start and start.isoformat() in xs:
        marks.append(
            {
                "x": xs[start.isoformat()],
                "kind": "streak-start",
                "label": "1d" if same_day else "s",
                "side": side,
                "title": f"streak start {start.isoformat()} {label}".strip(),
            }
        )
    if end and end.isoformat() in xs and not same_day:
        end_kind = "streak-end-open" if card.get("mom_streak_open") else "streak-end"
        marks.append(
            {
                "x": xs[end.isoformat()],
                "kind": end_kind,
                "label": "now" if card.get("mom_streak_open") else "e",
                "side": side,
                "title": f"streak {'open end' if card.get('mom_streak_open') else 'end'} {end.isoformat()} {label}".strip(),
            }
        )
    return layout_marks(marks, width=width)


def _px_series(card: Mapping[str, Any] | None) -> list[tuple[date, float]]:
    if not card:
        return []
    raw = card.get("px_series") or card.get("prices") or card.get("closes")
    out: list[tuple[date, float]] = []
    if not isinstance(raw, (list, tuple)):
        return out
    for row in raw:
        if isinstance(row, Mapping):
            d = _as_date(row.get("date") or row.get("asof"))
            p = dapi_enrich.as_float(row.get("px") or row.get("close") or row.get("px_last") or row.get("value"))
        elif isinstance(row, (list, tuple)) and len(row) >= 2:
            d = _as_date(row[0])
            p = dapi_enrich.as_float(row[1])
        else:
            continue
        if d is not None and p is not None:
            out.append((d, p))
    out.sort(key=lambda x: x[0])
    return out


def _score_as_px(card: Mapping[str, Any] | None) -> list[tuple[date, float]]:
    """Fallback polyline from the momentum-score series (not invented prices)."""
    raw = (card or {}).get("mom_score_series")
    out: list[tuple[date, float]] = []
    if not isinstance(raw, list):
        return out
    for row in raw:
        if not isinstance(row, Mapping):
            continue
        d = _as_date(row.get("date"))
        s = dapi_enrich.as_float(row.get("score"))
        if d is not None and s is not None:
            out.append((d, s))
    return out


def render_svg(card: Mapping[str, Any] | None, *, width: int = CHART_WIDTH, height: int = CHART_HEIGHT) -> str:
    """Compact SVG for a home card. Empty string if there is nothing to plot."""
    if not card:
        return ""
    ticker = html.escape(str(card.get("ticker") or card.get("name") or ""), quote=True)
    px = _px_series(card) or _score_as_px(card)
    marks = _marks_for_card(card, width=width)
    if not px and not marks:
        return ""
    line = _px_points(px, width, height) if px else ""
    glyphs: list[str] = []
    if line:
        glyphs.append(
            f'<polyline class="fd-chart-line" fill="none" points="{html.escape(line, quote=True)}"/>'
        )
    for mark in marks:
        kind = mark["kind"]
        x = mark["x"]
        y = mark["y"]
        side = mark.get("side") or ""
        title = html.escape(str(mark.get("title") or ""), quote=True)
        cls = f"fd-chart-mark fd-chart-mark-{html.escape(kind, quote=True)}"
        if side:
            cls += f" fd-chart-side-{html.escape(str(side), quote=True)}"
        if kind.startswith("streak"):
            body = f'<path d="M{x:.1f},{y - 4:.1f} L{x + 4:.1f},{y:.1f} L{x:.1f},{y + 4:.1f} L{x - 4:.1f},{y:.1f} Z"/>'
            stem = f'<line x1="{x:.1f}" y1="{PLOT_TOP}" x2="{x:.1f}" y2="{PLOT_BOT}" />'
        else:
            body = f'<path d="M{x:.1f},{PLOT_TOP - 1:.1f} L{x + 3.2:.1f},{PLOT_TOP + 6:.1f} L{x - 3.2:.1f},{PLOT_TOP + 6:.1f} Z"/>'
            stem = ""
        label = ""
        if mark.get("show_label") and mark.get("label"):
            lx = mark.get("label_x", x)
            ly = mark.get("label_y", 10)
            label = (
                f'<text x="{lx:.1f}" y="{ly:.1f}" text-anchor="middle">'
                f"{html.escape(str(mark['label']))}</text>"
            )
        glyphs.append(
            f'<g class="{cls}" data-kind="{html.escape(kind, quote=True)}" title="{title}">'
            f"<title>{title}</title>{stem}{body}{label}</g>"
        )
    return (
        f'<svg class="fd-chart" viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
        f'data-t="{ticker}" role="img" aria-label="price chart with tag and streak marks">'
        f"{''.join(glyphs)}</svg>"
    )


def chart_db(cards: Iterable[Mapping[str, Any]] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for card in cards or []:
        if not isinstance(card, Mapping):
            continue
        ticker = str(card.get("ticker") or card.get("name") or "").strip()
        if not ticker:
            continue
        rec = {
            "start": card.get("mom_streak_start"),
            "end": card.get("mom_streak_end"),
            "open": card.get("mom_streak_open"),
            "side": card.get("mom_streak_side"),
            "label": card.get("mom_streak_label"),
            "n": card.get("mom_streak") or 0,
            "tags": tags_of(card),
        }
        if not rec["start"] and not rec["tags"]:
            continue
        out[ticker] = rec
        out.setdefault(_short(ticker), rec)
        out.setdefault(_ticker_key(ticker), rec)
    return out


def embed_db(mapping: Mapping[str, Any] | None) -> str:
    blob = json.dumps(dict(mapping or {}), separators=(",", ":"), default=str, ensure_ascii=True)
    return f'<script type="application/json" id="{DB_SCRIPT_ID}">{html.escape(blob, quote=False)}</script>'


def strip_css() -> str:
    return """
.fd-chart, .fd-chart-host { display: block; width: 100%; max-width: 260px; margin: 8px 0 2px; }
.fd-chart-host { position: relative; }
.fd-chart-overlay {
  position: absolute; inset: 0; width: 100%; height: 100%;
  pointer-events: none; overflow: visible;
}
.fd-chart-line {
  stroke: #93c5fd; stroke-width: 1.25; fill: none;
}
.fd-chart-mark { pointer-events: auto; }
.fd-chart-mark-tag path {
  fill: #fbbf24; stroke: #92400e; stroke-width: 0.6;
}
.fd-chart-mark-tag line, .fd-chart-mark-streak-start line,
.fd-chart-mark-streak-end line, .fd-chart-mark-streak-end-open line {
  stroke: currentColor; stroke-width: 1; stroke-dasharray: 2 2; opacity: 0.45;
}
.fd-chart-mark-streak-start path, .fd-chart-mark-streak-end path {
  fill: #34d399; stroke: #065f46; stroke-width: 0.7;
}
.fd-chart-side-below.fd-chart-mark-streak-start path,
.fd-chart-side-below.fd-chart-mark-streak-end path {
  fill: #fb7185; stroke: #9f1239;
}
.fd-chart-side-above.fd-chart-mark-streak-start path,
.fd-chart-side-above.fd-chart-mark-streak-end path {
  fill: #34d399; stroke: #065f46;
}
.fd-chart-mark-streak-end-open path {
  fill: none !important; stroke: #34d399; stroke-width: 1.4;
}
.fd-chart-side-below.fd-chart-mark-streak-end-open path {
  fill: none !important; stroke: #fb7185;
}
.fd-chart-mark text {
  font: 650 9px/1.1 "Segoe UI", "DejaVu Sans", "Noto Sans", ui-sans-serif, system-ui, sans-serif;
  fill: #e5e7eb; letter-spacing: 0.03em;
  paint-order: stroke fill;
  stroke: #0b0f14; stroke-width: 3px; stroke-linejoin: round;
}
.chart-anno, .tag-label, [data-tag-trigger], .tag-trigger, .tag-mark, .anno-label {
  font: 650 9px/1.1 "Segoe UI", "DejaVu Sans", "Noto Sans", ui-sans-serif, system-ui, sans-serif !important;
  letter-spacing: 0.02em;
  white-space: nowrap !important;
  text-shadow: 0 0 3px #0b0f14, 0 1px 0 #0b0f14;
}
""".strip()


def overlay_js() -> str:
    """Polish live tag-trigger labels + overlay streak bounds. Idempotent."""
    return r"""
(function () {
  if (window.__FD_CHART_MARKS__) return;
  window.__FD_CHART_MARKS__ = true;
  var TAG_SEL = "[data-tag-trigger], .tag-trigger, .tag-mark, .chart-anno, .anno-label, .tag-label, text.tag-label";
  var CHART_SEL = "svg.fd-chart, canvas, svg.chart, svg.spark, .px-chart, [data-chart], .price-chart";
  var CLUSTER = 12;

  function db() {
    var el = document.getElementById("fd-chart-db");
    if (!el) return {};
    try { return JSON.parse(el.textContent || "{}") || {}; }
    catch (e) { return {}; }
  }
  function tickerOf(node) {
    var n = node;
    while (n && n !== document.body) {
      var t = (n.getAttribute && (n.getAttribute("data-t") || n.getAttribute("data-ticker") || n.getAttribute("data-name"))) || "";
      if (t) return t.trim();
      n = n.parentElement;
    }
    return "";
  }
  function recOf(t, map) {
    if (!t) return null;
    if (map[t]) return map[t];
    var short = t.split(/\s+/)[0];
    var keys = Object.keys(map);
    for (var i = 0; i < keys.length; i++) {
      var k = keys[i];
      if (k === t || k.split(/\s+/)[0] === short) return map[k];
    }
    return null;
  }
  function polishTags(root) {
    var nodes = Array.prototype.slice.call(root.querySelectorAll(TAG_SEL));
    if (!nodes.length) return;
    var items = nodes.map(function (el) {
      if (el.closest && el.closest("nav, .topnav, #gics-filter-strip, #options-refresh, #refresh")) return null;
      var box = el.getBoundingClientRect();
      return { el: el, x: box.left + box.width / 2, y: box.top, w: box.width, h: box.height };
    }).filter(Boolean).sort(function (a, b) { return a.x - b.x; });
    var groups = [];
    items.forEach(function (it) {
      var last = groups.length ? groups[groups.length - 1] : null;
      if (last && Math.abs(it.x - last[last.length - 1].x) < CLUSTER) last.push(it);
      else groups.push([it]);
    });
    var kept = [];
    groups.forEach(function (g) {
      var labels = [];
      g.forEach(function (it, i) {
        var text = (it.el.textContent || "").trim();
        if (text) labels.push(text);
        it.el.setAttribute("data-fd-polished", "1");
        if (i === 0) {
          it.el.style.opacity = "1";
          it.el.style.letterSpacing = "0.02em";
          it.el.style.whiteSpace = "nowrap";
          it.el.removeAttribute("aria-hidden");
        } else {
          it.el.style.opacity = "0";
          it.el.style.pointerEvents = "none";
          it.el.setAttribute("aria-hidden", "true");
        }
      });
      var keep = g[0] && g[0].el;
      if (!keep) return;
      var joined = labels.slice(0, 3).join(" · ");
      if (labels.length > 3) joined += " +" + (labels.length - 3);
      if (labels.length > 1) {
        if (keep.tagName === "text" || keep.tagName === "TEXT") keep.textContent = joined;
        else if (keep.childElementCount === 0) keep.textContent = joined;
      }
      keep.setAttribute("title", labels.join(" · "));
      var svg = keep.ownerSVGElement;
      if (svg && (keep.tagName === "text" || keep.tagName === "TEXT")) {
        keep.setAttribute("text-anchor", "middle");
        var vb = svg.viewBox && svg.viewBox.baseVal;
        keep.setAttribute("y", String(vb && isFinite(vb.y) ? Math.max(10, vb.y + 10) : 10));
      }
      kept.push(keep);
    });
    var lastRight = -999;
    kept.forEach(function (el) {
      var box = el.getBoundingClientRect();
      if (box.width && box.left < lastRight + 6) {
        var shift = (lastRight + 6) - box.left;
        if (el.tagName === "text" || el.tagName === "TEXT") {
          var x = parseFloat(el.getAttribute("x") || "0");
          if (isFinite(x)) el.setAttribute("x", String(x + shift));
        } else {
          el.style.transform = "translateX(" + Math.round(shift) + "px)";
        }
        lastRight = box.right + shift;
      } else {
        lastRight = box.right;
      }
    });
  }
  function xFromAnchors(chart, iso) {
    var anchors = [];
    Array.prototype.forEach.call(chart.querySelectorAll("[data-date], [data-asof], [data-tag-trigger], .tag-trigger, .tag-mark"), function (el) {
      var d = el.getAttribute("data-date") || el.getAttribute("data-asof") || el.getAttribute("data-tag-trigger");
      if (!d) return;
      var box = el.getBoundingClientRect();
      var host = chart.getBoundingClientRect();
      anchors.push({ d: d.slice(0, 10), x: box.left - host.left + box.width / 2 });
    });
    if (!anchors.length) return null;
    anchors.sort(function (a, b) { return a.d < b.d ? -1 : a.d > b.d ? 1 : 0; });
    if (anchors.length === 1) return anchors[0].x;
    var t = Date.parse(iso);
    if (!isFinite(t)) return null;
    for (var i = 0; i < anchors.length - 1; i++) {
      var a = Date.parse(anchors[i].d), b = Date.parse(anchors[i + 1].d);
      if (t >= a && t <= b && b !== a) {
        var u = (t - a) / (b - a);
        return anchors[i].x + u * (anchors[i + 1].x - anchors[i].x);
      }
    }
    if (t <= Date.parse(anchors[0].d)) return anchors[0].x;
    return anchors[anchors.length - 1].x;
  }
  function ensureOverlay(chart) {
    if (chart.classList && chart.classList.contains("fd-chart")) return null;
    if (chart.querySelector && chart.querySelector(".fd-chart-mark-streak-start, [data-kind='streak-start']")) return null;
    var host = chart.parentElement;
    if (!host) return null;
    if (!host.classList.contains("fd-chart-host")) {
      var wrap = document.createElement("div");
      wrap.className = "fd-chart-host";
      host.insertBefore(wrap, chart);
      wrap.appendChild(chart);
      host = wrap;
    }
    var svg = host.querySelector("svg.fd-chart-overlay");
    if (!svg) {
      svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      svg.setAttribute("class", "fd-chart-overlay");
      svg.setAttribute("aria-hidden", "true");
      host.appendChild(svg);
    }
    return svg;
  }
  function drawStreak(chart, rec) {
    if (!rec || !rec.start) return;
    var overlay = ensureOverlay(chart);
    if (!overlay) return;
    var hostBox = (chart.classList && chart.classList.contains("fd-chart") ? chart : overlay.parentElement).getBoundingClientRect();
    overlay.setAttribute("viewBox", "0 0 " + Math.max(hostBox.width, 1) + " " + Math.max(hostBox.height, 1));
    Array.prototype.forEach.call(overlay.querySelectorAll("[data-fd-streak]"), function (n) { n.remove(); });
    var lastCapX = -999;
    function mark(iso, kind, caption) {
      var x = xFromAnchors(chart, iso);
      if (x == null) {
        var dates = [];
        if (rec.start) dates.push(rec.start);
        if (rec.end) dates.push(rec.end);
        var i = dates.indexOf(iso);
        if (i < 0) return;
        x = 8 + (dates.length === 1 ? hostBox.width / 2 : i / Math.max(dates.length - 1, 1) * (hostBox.width - 16));
      }
      var y = hostBox.height * 0.55;
      var g = document.createElementNS("http://www.w3.org/2000/svg", "g");
      g.setAttribute("class", "fd-chart-mark fd-chart-mark-" + kind + (rec.side ? " fd-chart-side-" + rec.side : ""));
      g.setAttribute("data-fd-streak", kind);
      g.setAttribute("data-date", iso);
      var path = document.createElementNS("http://www.w3.org/2000/svg", "path");
      path.setAttribute("d", "M" + x + "," + (y - 4) + " L" + (x + 4) + "," + y + " L" + x + "," + (y + 4) + " L" + (x - 4) + "," + y + " Z");
      var line = document.createElementNS("http://www.w3.org/2000/svg", "line");
      line.setAttribute("x1", x); line.setAttribute("x2", x);
      line.setAttribute("y1", 8); line.setAttribute("y2", hostBox.height - 10);
      var title = document.createElementNS("http://www.w3.org/2000/svg", "title");
      title.textContent = (kind.indexOf("start") >= 0 ? "streak start " : "streak end ") + iso + " " + (rec.label || "");
      g.appendChild(title); g.appendChild(line); g.appendChild(path);
      if (caption) {
        var tx = x;
        if (Math.abs(tx - lastCapX) < 16) tx = lastCapX + 16;
        var text = document.createElementNS("http://www.w3.org/2000/svg", "text");
        text.setAttribute("x", tx); text.setAttribute("y", hostBox.height - 4);
        text.setAttribute("text-anchor", "middle");
        text.textContent = caption;
        g.appendChild(text);
        lastCapX = tx;
      }
      overlay.appendChild(g);
    }
    var same = rec.start && rec.end && rec.start === rec.end;
    mark(rec.start, "streak-start", same ? "1d" : "s");
    if (!same && rec.end) mark(rec.end, rec.open ? "streak-end-open" : "streak-end", rec.open ? "now" : "e");
  }
  function apply() {
    var map = db();
    polishTags(document);
    var charts = document.querySelectorAll(CHART_SEL);
    for (var i = 0; i < charts.length; i++) {
      var chart = charts[i];
      if (chart.closest && chart.closest("nav, .topnav")) continue;
      polishTags(chart);
      var rec = recOf(tickerOf(chart), map);
      if (rec) drawStreak(chart, rec);
    }
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", apply);
  else apply();
})();
""".strip()


def ensure_embedded(html_text: str, mapping: Mapping[str, Any] | None) -> str:
    """Always leave chart CSS + db + overlay JS. Safe on live 2.7MB HTML."""
    text = html_text or ""
    css = f'<style id="{CSS_STYLE_ID}">\n{strip_css()}\n</style>\n'
    text, n_css = re.subn(
        rf'<style\b[^>]*\bid=["\']{CSS_STYLE_ID}["\'][^>]*>.*?</style>\s*',
        lambda _m: css,
        text,
        count=1,
        flags=re.I | re.S,
    )
    if n_css == 0:
        if "</head>" in text:
            text = text.replace("</head>", css + "</head>", 1)
        else:
            text = css + text
    db = embed_db(mapping)
    text, n_db = re.subn(
        rf'<script\b[^>]*\bid=["\']{DB_SCRIPT_ID}["\'][^>]*>.*?</script>\s*',
        lambda _m: db,
        text,
        count=1,
        flags=re.I | re.S,
    )
    if n_db == 0:
        if "</body>" in text:
            text = text.replace("</body>", db + "\n</body>", 1)
        else:
            text += db
    script = f'<script id="{JS_SCRIPT_ID}">\n{overlay_js()}\n</script>\n'
    text, n_js = re.subn(
        rf'<script\b[^>]*\bid=["\']{JS_SCRIPT_ID}["\'][^>]*>.*?</script>\s*',
        lambda _m: script,
        text,
        count=1,
        flags=re.I | re.S,
    )
    if n_js == 0:
        if "</body>" in text:
            text = text.replace("</body>", script + "</body>", 1)
        else:
            text += script
    return text
