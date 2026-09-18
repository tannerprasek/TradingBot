"""Home-card chart marks: tag-trigger polish + momentum streak bounds.

Live Factor Desk already plots **tag-trigger** marks on the name chart.
This module does three things on every ``write_combined``:

1. Re-layout those existing trigger labels (spacing, alignment, overlap,
   clutter) so they stay readable without burying the price series.
2. Add **streak begin / end** marks for the Feature A run vs score 5.
   Same overlay language as tag triggers, different glyph/color.
3. Color the close path **green / red** by a simple MA regime. Live Desktop
   name-drill charts are drawn by ``paintPxChart`` (SVG ``[data-px-svg]`` +
   JSON ``[data-px-json]``). Overlay JS wraps that function — it does not
   regenerate skinny HTML. Generator ``svg.fd-chart`` still paints in Python.

Trend v1 (daily close series): Positive (green) iff close > SMA50 AND
close > SMA200; otherwise Negative (red). If the series is shorter than
200, omit SMA200 and color by close vs SMA50. If shorter than 50, keep
the default stroke (no regime). Path is split into contiguous segments
so color flips over time. No arrows / callouts.

Live ``paintPxChart`` already draws SMA20/50/200 + 52w high; the wrap
segments the white ``#e6edf3`` price path and does not pile on duplicate
MAs. The original path is hidden **only after** green/red segments exist
(fail-open: a wrap miss keeps the white series). ``padR`` is enlarged so
the last print / marker is not clipped.

Skinny generator cards get a compact SVG sparkline when a price (or score)
series is available. Live ~4.8MB HTML is patched: overlay JS + JSON db.
Desktop: ``sync_live_paintpx.py`` calls ``ensure_embedded`` / ``inject_paintpx``
on live ``factorbook.html`` (refuses files under 1MB; never skinny rewrite).
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
CHART_HEIGHT = 80
PAD_X = 10
PLOT_TOP = 22
PLOT_BOT = 62
DETAIL_WIDTH = 560
DETAIL_HEIGHT = 200
DETAIL_PLOT_TOP = 46
DETAIL_PLOT_BOT = 186
TAG_CLUSTER_PX = 12
LABEL_H = 11
SMA_FAST = 50
SMA_SLOW = 200
# Trend v1: green iff close > SMA50 AND close > SMA200; else red.
# Fallback when n < 200: color by close vs SMA50 only (no SMA200 overlay).
COLOR_POS = "#22c55e"
COLOR_NEG = "#ef4444"
COLOR_MA50 = "#3b82f6"
COLOR_MA200 = "#9f1239"
COLOR_NA = "#93c5fd"
# Live Desktop paintPxChart (factorbook.html) — do not assume generator SVG.
LIVE_PX_W = 760
LIVE_PX_H = 220
LIVE_PX_H_NEW = 300
LIVE_PX_PAD_R = 8
LIVE_PX_PAD_R_NEW = 36
LIVE_PX_PRICE = "#e6edf3"

TAG_KEYS = ("tag_triggers", "tags", "digest_tags", "chart_tags", "triggered_tags")
PX_SERIES_KEYS = ("px_series", "prices", "closes", "px_hist", "history", "px")


def sma(values: Sequence[float], window: int) -> list[float | None]:
    """Trailing simple moving average. ``None`` until ``window`` prints exist."""
    if window <= 0:
        raise ValueError("sma window must be positive")
    n = len(values)
    out: list[float | None] = [None] * n
    if n < window:
        return out
    acc = 0.0
    for i, v in enumerate(values):
        acc += float(v)
        if i >= window:
            acc -= float(values[i - window])
        if i >= window - 1:
            out[i] = acc / window
    return out


def trend_flag(
    close: float,
    sma_fast: float | None,
    sma_slow: float | None = None,
) -> str | None:
    """``pos`` / ``neg`` / ``None`` (not enough MA history).

    Positive: close > SMA50 and close > SMA200. Negative: close at or below
    either MA. When SMA200 is missing, Positive is close > SMA50.
    """
    if sma_fast is None:
        return None
    if sma_slow is None:
        return "pos" if close > sma_fast else "neg"
    return "pos" if (close > sma_fast and close > sma_slow) else "neg"


def trend_flags(
    closes: Sequence[float],
    *,
    fast: int = SMA_FAST,
    slow: int = SMA_SLOW,
) -> tuple[list[str | None], list[float | None], list[float | None]]:
    """Per-bar regime plus SMA series.

    When ``len(closes) < slow``, SMA200 is omitted and color is close vs SMA50.
    When the series is long enough, bars before SMA200 exists stay uncolored
    (``None``) rather than flipping to the short-history fallback.
    """
    fast_ma = sma(closes, fast)
    have_slow = len(closes) >= slow
    slow_ma = sma(closes, slow) if have_slow else [None] * len(closes)
    flags: list[str | None] = []
    for i, close in enumerate(closes):
        if have_slow:
            flags.append(
                None
                if (fast_ma[i] is None or slow_ma[i] is None)
                else trend_flag(close, fast_ma[i], slow_ma[i])
            )
        else:
            flags.append(trend_flag(close, fast_ma[i], None))
    return flags, fast_ma, slow_ma


def padded_x(
    x: float,
    *,
    pad_l: float,
    width: float,
    old_pad_r: float,
    new_pad_r: float,
) -> float:
    """Re-map a plot x from a tight ``padR`` to a roomier right gutter."""
    old_inner = float(width) - float(pad_l) - float(old_pad_r)
    new_inner = float(width) - float(pad_l) - float(new_pad_r)
    if old_inner <= 0 or new_inner <= 0:
        return float(x)
    return float(pad_l) + (float(x) - float(pad_l)) * new_inner / old_inner


def trend_segments(flags: Sequence[str | None]) -> list[tuple[str, int, int]]:
    """Contiguous inclusive index ranges of the same regime (``pos``/``neg``/``na``)."""
    out: list[tuple[str, int, int]] = []
    if not flags:
        return out
    start = 0
    cur = flags[0] or "na"
    for i in range(1, len(flags)):
        kind = flags[i] or "na"
        if kind != cur:
            out.append((cur, start, i - 1))
            start = i
            cur = kind
    out.append((cur, start, len(flags) - 1))
    return out


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
    plot_top: int | None = None,
    plot_bot: int | None = None,
) -> list[dict[str, Any]]:
    """Collision-avoiding layout. ``x`` may be 0–1 fraction or already px.

    Tag-trigger labels sit **above** the series. Streak bounds sit **on** the
    series as diamonds, with a tiny caption along the bottom edge.
    Nearby tag labels collapse onto one caption (``G4 · EVT``) instead of
    stacking into the line.
    """
    width = max(int(width), 40)
    height = max(int(height), 40)
    plot_top = PLOT_TOP if plot_top is None else int(plot_top)
    plot_bot = PLOT_BOT if plot_bot is None else int(plot_bot)
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
        try:
            rec["y_hint"] = float(raw.get("y"))
        except (TypeError, ValueError):
            rec["y_hint"] = None
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
            mark["y"] = plot_top
            mark["label_y"] = max(11, plot_top - 11)
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
        if mark.get("y_hint") is not None:
            mark["y"] = float(mark["y_hint"])
        else:
            mark["y"] = float(plot_top + (plot_bot - plot_top) * 0.55)
        mark["label_y"] = height - 6
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


def _px_points(
    series: Sequence[tuple[date, float]],
    width: int,
    height: int,
    *,
    plot_top: int = PLOT_TOP,
    plot_bot: int = PLOT_BOT,
    extra_vals: Sequence[float] | None = None,
) -> str:
    xy = _xy_points(
        series, width, plot_top=plot_top, plot_bot=plot_bot, extra_vals=extra_vals
    )
    return " ".join(f"{x:.1f},{y:.1f}" for x, y in xy)


def _value_span(
    series: Sequence[tuple[date, float]], extra_vals: Sequence[float] | None = None
) -> tuple[float, float]:
    vals = [p for _, p in series]
    extras = [v for v in (extra_vals or []) if v is not None]
    if extras:
        vals = vals + extras
    lo, hi = min(vals), max(vals)
    if hi == lo:
        hi = lo + 1.0
    return lo, hi


def _xy_points(
    series: Sequence[tuple[date, float]],
    width: int,
    *,
    plot_top: int = PLOT_TOP,
    plot_bot: int = PLOT_BOT,
    extra_vals: Sequence[float] | None = None,
) -> list[tuple[float, float]]:
    if not series:
        return []
    xs = _dates_to_x([d for d, _ in series], width)
    lo, hi = _value_span(series, extra_vals)
    inner_h = plot_bot - plot_top
    out: list[tuple[float, float]] = []
    for d, p in series:
        x = xs.get(d.isoformat())
        if x is None:
            continue
        y = plot_bot - (p - lo) / (hi - lo) * inner_h
        out.append((x, y))
    return out


def _y_at(
    value: float,
    lo: float,
    hi: float,
    plot_top: int,
    plot_bot: int,
) -> float:
    return plot_bot - (value - lo) / (hi - lo) * (plot_bot - plot_top)


def _polyline_pts(xy: Sequence[tuple[float, float]], i0: int, i1: int) -> str:
    """Inclusive slice; include the previous vertex so color flips stay connected."""
    start = max(i0, 0)
    if i0 > 0:
        start = i0 - 1
    chunk = xy[start : i1 + 1]
    return " ".join(f"{x:.1f},{y:.1f}" for x, y in chunk)


def _series_y(
    series: Sequence[tuple[date, float]],
    day: date,
    height: int,
    *,
    plot_top: int = PLOT_TOP,
    plot_bot: int = PLOT_BOT,
    extra_vals: Sequence[float] | None = None,
) -> float:
    """Y on the price polyline for ``day`` (nearest print)."""
    fallback = float(plot_top + (plot_bot - plot_top) * 0.55)
    if not series:
        return fallback
    lo, hi = _value_span(series, extra_vals)
    px = None
    best = None
    vals = [p for _, p in series]
    for d, v in series:
        if d == day:
            px = v
            break
        gap = abs((d - day).days)
        if best is None or gap < best[0]:
            best = (gap, v)
    if px is None:
        px = best[1] if best else vals[-1]
    y = _y_at(px, lo, hi, plot_top, plot_bot)
    return max(plot_top + 4, min(plot_bot - 4, y))


def _marks_for_card(
    card: Mapping[str, Any],
    *,
    width: int = CHART_WIDTH,
    height: int = CHART_HEIGHT,
    plot_top: int = PLOT_TOP,
    plot_bot: int = PLOT_BOT,
    extra_vals: Sequence[float] | None = None,
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
    series = px_series or _score_as_px(card)
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
                "y": _series_y(
                    series, start, height, plot_top=plot_top, plot_bot=plot_bot, extra_vals=extra_vals
                ),
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
                "y": _series_y(
                    series, end, height, plot_top=plot_top, plot_bot=plot_bot, extra_vals=extra_vals
                ),
                "title": f"streak {'open end' if card.get('mom_streak_open') else 'end'} {end.isoformat()} {label}".strip(),
            }
        )
    return layout_marks(marks, width=width, height=height, plot_top=plot_top, plot_bot=plot_bot)


def _parse_px_rows(raw: Any) -> list[tuple[date, float]]:
    out: list[tuple[date, float]] = []
    if not isinstance(raw, (list, tuple)) or not raw:
        return out
    if all(isinstance(row, (int, float)) and not isinstance(row, bool) for row in raw):
        base = date(2020, 1, 1).toordinal()
        return [(date.fromordinal(base + i), float(row)) for i, row in enumerate(raw)]
    for row in raw:
        if isinstance(row, Mapping):
            d = _as_date(row.get("date") or row.get("asof") or row.get("day") or row.get("d"))
            p = dapi_enrich.as_float(
                row.get("px")
                or row.get("close")
                or row.get("px_last")
                or row.get("adj_close")
                or row.get("value")
                or row.get("p")
                or row.get("y")
            )
        elif isinstance(row, (list, tuple)) and len(row) >= 2:
            d = _as_date(row[0])
            p = dapi_enrich.as_float(row[1])
        else:
            continue
        if d is not None and p is not None:
            out.append((d, p))
    out.sort(key=lambda x: x[0])
    return out


def _px_series(card: Mapping[str, Any] | None) -> list[tuple[date, float]]:
    if not card:
        return []
    for key in PX_SERIES_KEYS:
        parsed = _parse_px_rows(card.get(key))
        if parsed:
            return parsed
    return []


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


def _legend_svg(width: int) -> str:
    """Positive ↑ / Negative ↓ Trend Signals plus 50/200-day MA keys."""
    x_neg = 92
    x_note = 186
    x_ma50 = min(width - 150, 318)
    x_ma200 = min(width - 72, 400)
    return (
        '<g class="fd-chart-legend" data-fd-trend-legend="1">'
        f'<text class="fd-chart-legend-pos" x="{PAD_X}" y="14">Positive ↑</text>'
        f'<text class="fd-chart-legend-slash" x="{x_neg - 10}" y="14">/</text>'
        f'<text class="fd-chart-legend-neg" x="{x_neg}" y="14">Negative ↓</text>'
        f'<text class="fd-chart-legend-note" x="{x_note}" y="14">Trend Signals</text>'
        f'<line class="fd-chart-ma50" x1="{x_ma50}" y1="11" x2="{x_ma50 + 14}" y2="11"/>'
        f'<text class="fd-chart-legend-ma" x="{x_ma50 + 18}" y="14">50-Day MA</text>'
        f'<line class="fd-chart-ma200" x1="{x_ma200}" y1="11" x2="{x_ma200 + 14}" y2="11"/>'
        f'<text class="fd-chart-legend-ma" x="{x_ma200 + 18}" y="14">200-Day MA</text>'
        "</g>"
    )


def _trend_polylines(
    series: Sequence[tuple[date, float]],
    width: int,
    *,
    plot_top: int,
    plot_bot: int,
) -> list[str]:
    if len(series) < 2:
        line = _px_points(series, width, 0, plot_top=plot_top, plot_bot=plot_bot)
        if not line:
            return []
        return [
            f'<polyline class="fd-chart-line" fill="none" points="{html.escape(line, quote=True)}"/>'
        ]
    closes = [p for _, p in series]
    flags, fast_ma, slow_ma = trend_flags(closes)
    extras = [v for v in fast_ma + slow_ma if v is not None]
    xy = _xy_points(series, width, plot_top=plot_top, plot_bot=plot_bot, extra_vals=extras)
    if len(xy) != len(series):
        xy = _xy_points(series, width, plot_top=plot_top, plot_bot=plot_bot, extra_vals=extras)
    lo, hi = _value_span(series, extras)
    glyphs: list[str] = []

    def ma_points(ma: Sequence[float | None]) -> str:
        bits: list[str] = []
        for i, val in enumerate(ma):
            if val is None or i >= len(xy):
                continue
            y = _y_at(val, lo, hi, plot_top, plot_bot)
            bits.append(f"{xy[i][0]:.1f},{y:.1f}")
        return " ".join(bits)

    ma50_pts = ma_points(fast_ma)
    ma200_pts = ma_points(slow_ma)
    if ma50_pts:
        glyphs.append(
            f'<polyline class="fd-chart-ma50" fill="none" data-fd-ma="50" '
            f'points="{html.escape(ma50_pts, quote=True)}"/>'
        )
    if ma200_pts:
        glyphs.append(
            f'<polyline class="fd-chart-ma200" fill="none" data-fd-ma="200" '
            f'points="{html.escape(ma200_pts, quote=True)}"/>'
        )

    if len(xy) < 2 or all(f is None for f in flags):
        line = " ".join(f"{x:.1f},{y:.1f}" for x, y in xy)
        glyphs.append(
            f'<polyline class="fd-chart-line" fill="none" points="{html.escape(line, quote=True)}"/>'
        )
        return glyphs

    for kind, i0, i1 in trend_segments(flags):
        pts = _polyline_pts(xy, i0, i1)
        if not pts:
            continue
        cls = "fd-chart-line"
        if kind == "pos":
            cls += " fd-chart-line-pos"
        elif kind == "neg":
            cls += " fd-chart-line-neg"
        glyphs.append(
            f'<polyline class="{cls}" fill="none" data-fd-trend-seg="{kind}" '
            f'points="{html.escape(pts, quote=True)}"/>'
        )
    return glyphs


def render_svg(
    card: Mapping[str, Any] | None,
    *,
    width: int = CHART_WIDTH,
    height: int = CHART_HEIGHT,
    detail: bool = False,
) -> str:
    """Compact SVG for a home card. Empty string if there is nothing to plot.

    ``detail=True`` is the name-drill chart: larger viewBox, legend
    (Positive ↑ / Negative ↓), same MA-regime coloring as the spark.
    """
    if not card:
        return ""
    if detail:
        if width == CHART_WIDTH:
            width = DETAIL_WIDTH
        if height == CHART_HEIGHT:
            height = DETAIL_HEIGHT
        plot_top, plot_bot = DETAIL_PLOT_TOP, DETAIL_PLOT_BOT
    else:
        plot_top, plot_bot = PLOT_TOP, PLOT_BOT
    ticker = html.escape(str(card.get("ticker") or card.get("name") or ""), quote=True)
    px = _px_series(card) or _score_as_px(card)
    closes = [p for _, p in px]
    _flags, fast_ma, slow_ma = trend_flags(closes) if closes else ([], [], [])
    extras = [v for v in list(fast_ma) + list(slow_ma) if v is not None]
    marks = _marks_for_card(
        card,
        width=width,
        height=height,
        plot_top=plot_top,
        plot_bot=plot_bot,
        extra_vals=extras,
    )
    if not px and not marks:
        return ""
    glyphs: list[str] = []
    if detail:
        glyphs.append(_legend_svg(width))
    glyphs.extend(_trend_polylines(px, width, plot_top=plot_top, plot_bot=plot_bot))
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
            stem = ""
        else:
            body = (
                f'<path d="M{x:.1f},{plot_top - 1:.1f} L{x + 3.2:.1f},{plot_top + 6:.1f} '
                f'L{x - 3.2:.1f},{plot_top + 6:.1f} Z"/>'
            )
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
    cls = "fd-chart fd-chart-detail" if detail else "fd-chart"
    aria = (
        "name-drill price chart with MA trend coloring"
        if detail
        else "price chart with tag and streak marks"
    )
    return (
        f'<svg class="{cls}" viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
        f'data-t="{ticker}" data-fd-trend="1" role="img" aria-label="{aria}">'
        f"{''.join(glyphs)}</svg>"
    )


def render_detail_svg(
    card: Mapping[str, Any] | None,
    *,
    width: int = DETAIL_WIDTH,
    height: int = DETAIL_HEIGHT,
) -> str:
    """Name-drill / detail pane chart (legend + segmented MA-regime path)."""
    return render_svg(card, width=width, height=height, detail=True)


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
.fd-chart-detail, #fd-name-drill .fd-chart, #fd-name-drill svg {
  max-width: 100%; width: 100%; height: auto;
}
#fd-name-drill {
  display: none;
  margin: 8px 0 16px;
  padding: 8px 10px 10px;
  border: 1px solid #1f2937;
  background: #111827;
  border-radius: 8px;
}
#fd-name-drill.is-on { display: block; }
#fd-name-drill .fd-chart, #fd-name-drill .fd-chart-host { max-width: none; }
.fd-chart-host { position: relative; }
.fd-chart-overlay {
  position: absolute; inset: 0; width: 100%; height: 100%;
  pointer-events: none; overflow: visible;
}
/* Live paintPxChart host: taller plot, less ribbon stretch, room on the right. */
[data-px-svg], svg[data-px-svg], #detail-chart [data-px-svg], #name-chart [data-px-svg],
#fd-name-drill svg, .name-chart [data-px-svg], .detail-chart [data-px-svg] {
  display: block;
  width: 100% !important;
  max-width: 1100px;
  height: 320px !important;
  min-height: 300px;
  overflow: visible !important;
}
#detail-chart, #name-chart, #fd-name-drill, .name-chart, .detail-chart, [data-px-chart] {
  max-width: 1040px;
  overflow: visible;
}
[data-px-svg]:has([data-fd-trend-seg]) [data-fd-trend-src],
svg:has([data-fd-trend-seg]) [data-fd-trend-src],
[data-fd-trend-src]:has(+ [data-fd-trend-seg]) {
  display: none !important;
  stroke: none !important;
  opacity: 0 !important;
}
.fd-chart-line {
  stroke: #93c5fd; stroke-width: 1.6; fill: none;
  stroke-linejoin: round; stroke-linecap: round;
}
.fd-chart-line-pos, [data-px-svg] .fd-chart-line-pos, [data-px-svg] [data-fd-trend-seg="pos"] {
  stroke: #22c55e !important; fill: none !important;
}
.fd-chart-line-neg, [data-px-svg] .fd-chart-line-neg, [data-px-svg] [data-fd-trend-seg="neg"] {
  stroke: #ef4444 !important; fill: none !important;
}
.fd-chart-ma50 {
  stroke: #3b82f6; stroke-width: 1; fill: none;
  stroke-linejoin: round; stroke-linecap: round;
}
.fd-chart-ma200 {
  stroke: #9f1239; stroke-width: 1; fill: none;
  stroke-linejoin: round; stroke-linecap: round;
}
.fd-chart-legend { pointer-events: none; }
.fd-chart-legend text {
  font: 650 10px/1.1 "Segoe UI", "DejaVu Sans", "Noto Sans", ui-sans-serif, system-ui, sans-serif;
  letter-spacing: 0.02em;
}
.fd-chart-legend-pos { fill: #22c55e; }
.fd-chart-legend-neg { fill: #ef4444; }
.fd-chart-legend-slash, .fd-chart-legend-note, .fd-chart-legend-ma { fill: #9ca3af; }
.fd-chart-legend-html, .fd-px-leg-trend {
  display: inline-flex; align-items: center; gap: 6px; flex-wrap: wrap;
  font: 650 11px/1.2 "Segoe UI", "DejaVu Sans", "Noto Sans", ui-sans-serif, system-ui, sans-serif;
  color: #9ca3af; letter-spacing: 0.02em; margin: 0 8px 0 0;
}
.fd-chart-legend-html .pos, .fd-px-leg-trend .pos { color: #22c55e; }
.fd-chart-legend-html .neg, .fd-px-leg-trend .neg { color: #ef4444; }
.fd-chart-legend-html .slash { color: #6b7280; }
.fd-chart-legend-html .note { color: #9ca3af; font-weight: 600; }
.fd-chart-legend-html .ma50 { color: #3b82f6; }
.fd-chart-legend-html .ma200 { color: #9f1239; }
[data-px-leg] {
  display: flex; flex-wrap: wrap; align-items: center; gap: 8px 12px;
  font: 600 11px/1.2 "Segoe UI", "DejaVu Sans", "Noto Sans", ui-sans-serif, system-ui, sans-serif;
  color: #9ca3af; margin: 6px 0 2px;
}
.fd-px-chip-row {
  display: flex; flex-wrap: wrap; align-items: stretch; gap: 8px;
  margin: 0 0 8px;
}
.fd-px-chip {
  display: inline-flex; flex-direction: column; justify-content: center; gap: 3px;
  min-width: 68px; padding: 5px 9px 6px;
  border-radius: 6px; border: 1px solid #243244; background: #0f1720;
}
.fd-px-chip .k {
  font: 650 9px/1 "Segoe UI", "DejaVu Sans", "Noto Sans", ui-sans-serif, system-ui, sans-serif;
  color: #9ca3af; letter-spacing: 0.06em; text-transform: uppercase;
}
.fd-px-chip .v {
  font: 700 13px/1.2 "Segoe UI", "DejaVu Sans", "Noto Sans", ui-sans-serif, system-ui, sans-serif;
  font-variant-numeric: tabular-nums; color: #e5e7eb;
}
.fd-px-chip .v.up, .fd-px-chip .v.pos { color: #22c55e; }
.fd-px-chip .v.dn, .fd-px-chip .v.neg { color: #ef4444; }
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
    """Polish live tag-trigger labels + wrap ``paintPxChart`` for MA-regime coloring.

    Live Desktop name-drill charts are ``paintPxChart(wrap)`` writing into
    ``[data-px-svg]`` from ``[data-px-json]`` (Price path ``#e6edf3`` + SMA20/50/200).
    Overlay JS wraps that function so the white path is replaced with segmented
    green/red, ``padR`` is enlarged, and 1W/1M/YTD/Trend chips are cleaned up.
    Generator ``svg.fd-chart`` is already painted in Python (``data-fd-trend=1``).
    """
    return r"""
(function () {
  if (window.__FD_CHART_MARKS__) return;
  window.__FD_CHART_MARKS__ = true;
  var TAG_SEL = "[data-tag-trigger], .tag-trigger, .tag-mark, .chart-anno, .anno-label, .tag-label, text.tag-label";
  var CHART_SEL = "svg.fd-chart, canvas, svg.chart, svg.spark, .px-chart, [data-chart], .price-chart, #detail-chart, #name-chart, #px-chart, .name-chart, .detail-chart, [data-px-svg], [data-px-chart]";
  var DETAIL_SEL = "#fd-name-drill, #detail-chart, #name-chart, #px-chart, #chart-main, .name-chart, .detail-chart, [data-px-svg], [data-px-chart]";
  var CLUSTER = 12;
  var SMA_FAST = 50;
  var SMA_SLOW = 200;
  var LIVE_PX_W = 760;
  var LIVE_PX_H = 220;
  var LIVE_PX_H_NEW = 300;
  var LIVE_PX_PAD_R = 8;
  var LIVE_PX_PAD_R_NEW = 36;
  var LIVE_PX_PRICE = "#e6edf3";
  var restylingPx = false;

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
      var title = document.createElementNS("http://www.w3.org/2000/svg", "title");
      title.textContent = (kind.indexOf("start") >= 0 ? "streak start " : "streak end ") + iso + " " + (rec.label || "");
      g.appendChild(title); g.appendChild(path);
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
  function smaArr(vals, n) {
    var out = new Array(vals.length);
    var acc = 0;
    for (var i = 0; i < vals.length; i++) {
      acc += vals[i];
      if (i >= n) acc -= vals[i - n];
      out[i] = i >= n - 1 ? acc / n : null;
    }
    return out;
  }
  function trendFlag(close, s50, s200) {
    if (s50 == null) return null;
    if (s200 == null) return close > s50 ? "pos" : "neg";
    return (close > s50 && close > s200) ? "pos" : "neg";
  }
  function parsePoints(el) {
    var pts = [];
    if (!el) return pts;
    var raw = (el.getAttribute("points") || "").trim();
    if (raw) {
      var parts = raw.split(/[\s,]+/).filter(Boolean);
      for (var i = 0; i + 1 < parts.length; i += 2) {
        var x = parseFloat(parts[i]), y = parseFloat(parts[i + 1]);
        if (isFinite(x) && isFinite(y)) pts.push({x: x, y: y});
      }
      return pts;
    }
    var d = el.getAttribute("d") || "";
    if (!d) return pts;
    var tokens = d.replace(/,/g, " ").match(/[MmLlHhVvZz]|-?\d*\.?\d+(?:e[-+]?\d+)?/g);
    if (!tokens || !tokens.length) return pts;
    var i = 0, x = 0, y = 0, mode = "M";
    while (i < tokens.length) {
      var t = tokens[i];
      if (/^[MmLlHhVvZz]$/.test(t)) {
        if (/[CcQqSsAaTt]/.test(t)) return [];
        mode = t;
        i++;
        if (t === "Z" || t === "z") continue;
      } else if (/^[CcQqSsAaTt]$/.test(mode)) {
        return [];
      }
      if (mode === "H" || mode === "h") {
        var hx = parseFloat(tokens[i++]);
        if (!isFinite(hx)) break;
        x = mode === "h" ? x + hx : hx;
        pts.push({x: x, y: y});
        continue;
      }
      if (mode === "V" || mode === "v") {
        var vy = parseFloat(tokens[i++]);
        if (!isFinite(vy)) break;
        y = mode === "v" ? y + vy : vy;
        pts.push({x: x, y: y});
        continue;
      }
      var nx = parseFloat(tokens[i]), ny = parseFloat(tokens[i + 1]);
      if (!isFinite(nx) || !isFinite(ny)) break;
      i += 2;
      if (mode === "m" || mode === "l") { nx += x; ny += y; }
      x = nx; y = ny;
      pts.push({x: x, y: y});
      if (mode === "M") mode = "L";
      if (mode === "m") mode = "l";
    }
    return pts;
  }
  function isPxHost(node) {
    if (!node) return false;
    if (node.hasAttribute && (node.hasAttribute("data-px-svg") || node.hasAttribute("data-px-json") || node.hasAttribute("data-px-chart"))) return true;
    if (node.querySelector && node.querySelector("[data-px-svg], [data-px-json]")) return true;
    if (node.closest && node.closest("[data-px-svg], [data-px-chart], [data-px-json]")) return true;
    return false;
  }
  function resolvePxWrap(node) {
    if (!node) return document;
    if (node.querySelector && node.querySelector("[data-px-svg], [data-px-json]")) return node;
    if (node.closest) {
      var host = node.closest("[data-px-chart], [data-px-wrap], #detail-chart, #name-chart, .name-chart, .detail-chart, #fd-name-drill");
      if (host) return host;
    }
    var svg = resolvePxSvg(node);
    return (svg && svg.parentElement) || node;
  }
  function resolvePxSvg(node) {
    if (!node) return null;
    if (node.getAttribute && node.getAttribute("data-px-svg") != null && (node.tagName === "svg" || node.tagName === "SVG")) return node;
    if (node.querySelector) {
      var svg = node.querySelector("svg[data-px-svg], [data-px-svg] svg, svg.px, [data-px-svg]");
      if (svg && (svg.tagName === "svg" || svg.tagName === "SVG")) return svg;
      if (svg && svg.querySelector) {
        var inner = svg.querySelector("svg");
        if (inner) return inner;
      }
    }
    return (node.tagName === "svg" || node.tagName === "SVG") ? node : null;
  }
  function parsePxJson(wrap) {
    var host = resolvePxWrap(wrap);
    var el = host && host.querySelector ? host.querySelector("[data-px-json]") : null;
    if (!el && wrap && wrap.hasAttribute && wrap.hasAttribute("data-px-json")) el = wrap;
    if (!el) return null;
    var raw = el.textContent || el.getAttribute("data-px-json") || "";
    try { return JSON.parse(raw) || null; } catch (e) { return null; }
  }
  function arrOf(S, keys) {
    if (!S) return [];
    for (var i = 0; i < keys.length; i++) {
      var a = S[keys[i]];
      if (Array.isArray(a)) return a.map(function (v) { return (v == null || v === "") ? null : +v; });
    }
    return [];
  }
  function strokeOf(el) {
    var a = ((el.getAttribute && el.getAttribute("stroke")) || "").toLowerCase().replace(/\s/g, "");
    if (a && a !== "currentcolor" && a !== "none") return a;
    try {
      var cs = window.getComputedStyle(el);
      return ((cs && cs.stroke) || "").toLowerCase().replace(/\s/g, "");
    } catch (e2) {
      return ((el.style && el.style.stroke) || "").toLowerCase().replace(/\s/g, "");
    }
  }
  function isPriceStroke(s) {
    return /#e6edf3|#e5e7eb|#eef2f6|#f8fafc|#fff|#ffffff|white|rgb\(\s*230\s*,\s*237\s*,\s*243\s*\)/.test(s || "");
  }
  function isMaStroke(s) {
    return /#3b82f6|#60a5fa|#2563eb|#9f1239|#94a3b8|#9ca3af|#cbd5e1|#64748b|#f59e0b|#eab308|#fbbf24|#d4a017|#facc15|#88a/.test(s || "");
  }
  function findPricePath(svg) {
    if (!svg || !svg.querySelectorAll) return null;
    var nodes = svg.querySelectorAll("path, polyline");
    var white = null, whiteN = 0, best = null, bestN = 0;
    for (var i = 0; i < nodes.length; i++) {
      var el = nodes[i];
      if (isTrendGlyph(el)) continue;
      if (el.getAttribute && el.getAttribute("data-fd-trend-src") === "1") {
        if (!white) white = el;
        continue;
      }
      var pts = parsePoints(el);
      if (pts.length < 2) continue;
      var st = strokeOf(el);
      if (isPriceStroke(st) && pts.length >= whiteN) { white = el; whiteN = pts.length; }
      if (!isMaStroke(st) && pts.length > bestN) { best = el; bestN = pts.length; }
    }
    return white || best;
  }
  function flagsFromSeries(px, s50, s200) {
    var flags = [];
    var computed50 = (!s50 || !s50.length) ? smaArr(px.map(function (v) { return v == null ? 0 : v; }), SMA_FAST) : s50;
    var computed200 = (s200 && s200.length) ? s200 : (px.length >= SMA_SLOW ? smaArr(px.map(function (v) { return v == null ? 0 : v; }), SMA_SLOW) : []);
    for (var i = 0; i < px.length; i++) {
      if (px[i] == null || !isFinite(px[i])) { flags.push(null); continue; }
      var a = computed50[i] != null && isFinite(computed50[i]) ? computed50[i] : null;
      var b = computed200[i] != null && isFinite(computed200[i]) ? computed200[i] : null;
      // Live path: if SMA200 is not ready for this bar, fall back to SMA50-only
      // (same as a <200 series) so the visible drill is green/red, not a third color.
      flags.push(trendFlag(px[i], a, b));
    }
    return flags;
  }
  function drawnFlags(px, s50, s200) {
    var full = flagsFromSeries(px, s50, s200);
    var out = [];
    for (var i = 0; i < full.length; i++) {
      if (px[i] == null || !isFinite(px[i])) continue;
      out.push(full[i]);
    }
    return out;
  }
  function pathFromPts(chunk) {
    var d = "";
    for (var i = 0; i < chunk.length; i++) {
      d += (i ? "L" : "M") + chunk[i].x.toFixed(2) + "," + chunk[i].y.toFixed(2);
    }
    return d;
  }
  function hidePricePath(el) {
    if (!el) return;
    if (!el.getAttribute("data-fd-src-stroke")) {
      el.setAttribute("data-fd-src-stroke", el.getAttribute("stroke") || LIVE_PX_PRICE);
    }
    el.setAttribute("data-fd-trend-src", "1");
    el.setAttribute("stroke", "none");
    el.setAttribute("opacity", "0");
    el.setAttribute("display", "none");
    el.style.stroke = "none";
    el.style.opacity = "0";
    el.style.display = "none";
    el.style.visibility = "hidden";
  }
  function showPricePath(el) {
    if (!el) return;
    el.removeAttribute("data-fd-trend-src");
    var st = el.getAttribute("data-fd-src-stroke") || LIVE_PX_PRICE;
    el.setAttribute("stroke", st);
    el.removeAttribute("opacity");
    el.removeAttribute("display");
    el.style.stroke = st;
    el.style.opacity = "";
    el.style.display = "";
    el.style.visibility = "";
  }
  function remapAttrX(el, names, padL, oldInner, newInner) {
    names.forEach(function (name) {
      var raw = el.getAttribute(name);
      if (raw == null || raw === "") return;
      var n = parseFloat(raw);
      if (!isFinite(n)) return;
      el.setAttribute(name, String(padL + (n - padL) * newInner / oldInner));
    });
  }
  function remapPathD(el, padL, oldInner, newInner) {
    var pts = parsePoints(el);
    if (pts.length < 2) return;
    var next = pts.map(function (p) {
      return {x: padL + (p.x - padL) * newInner / oldInner, y: p.y};
    });
    if ((el.tagName || "").toLowerCase() === "polyline" || (el.tagName || "").toLowerCase() === "polygon") {
      el.setAttribute("points", next.map(function (p) { return p.x.toFixed(2) + "," + p.y.toFixed(2); }).join(" "));
    } else {
      el.setAttribute("d", pathFromPts(next));
    }
  }
  function expandPadR(svg, pts) {
    if (!svg || !pts || pts.length < 2) return pts;
    var vb = svg.viewBox && svg.viewBox.baseVal;
    var W = (vb && vb.width) ? vb.width : (parseFloat(svg.getAttribute("width")) || LIVE_PX_W);
    var xs = pts.map(function (p) { return p.x; });
    var padL = Math.min.apply(null, xs);
    var maxX = Math.max.apply(null, xs);
    var oldPadR = Math.max(0, W - maxX);
    if (oldPadR >= LIVE_PX_PAD_R_NEW - 1) return pts;
    var oldInner = W - padL - oldPadR;
    var newInner = W - padL - LIVE_PX_PAD_R_NEW;
    if (oldInner <= 0 || newInner <= 0) return pts;
    var nodes = svg.querySelectorAll("path, polyline, polygon, line, circle, ellipse, text, rect");
    for (var i = 0; i < nodes.length; i++) {
      var el = nodes[i];
      if (el.getAttribute && el.getAttribute("data-fd-trend-legend")) continue;
      var tag = (el.tagName || "").toLowerCase();
      if (tag === "path" || tag === "polyline" || tag === "polygon") remapPathD(el, padL, oldInner, newInner);
      else if (tag === "line") remapAttrX(el, ["x1", "x2"], padL, oldInner, newInner);
      else if (tag === "circle" || tag === "ellipse") remapAttrX(el, ["cx"], padL, oldInner, newInner);
      else if (tag === "text") remapAttrX(el, ["x"], padL, oldInner, newInner);
      else if (tag === "rect") remapAttrX(el, ["x"], padL, oldInner, newInner);
    }
    return pts.map(function (p) {
      return {x: padL + (p.x - padL) * newInner / oldInner, y: p.y};
    });
  }
  function applyAspect(svg) {
    if (!svg) return;
    svg.setAttribute("overflow", "visible");
    svg.style.overflow = "visible";
    var vb = svg.viewBox && svg.viewBox.baseVal;
    var W = (vb && vb.width) ? vb.width : LIVE_PX_W;
    var H = (vb && vb.height) ? vb.height : LIVE_PX_H;
    if (H && H <= LIVE_PX_H + 1) {
      svg.setAttribute("height", String(LIVE_PX_H_NEW));
    }
    svg.style.width = "100%";
    svg.style.maxWidth = "1100px";
    svg.style.height = "320px";
    if (W) svg.setAttribute("viewBox", "0 0 " + W + " " + (vb && vb.height ? vb.height : LIVE_PX_H));
    svg.setAttribute("preserveAspectRatio", "none");
  }
  function ensureHtmlLegend(wrap, svg) {
    var host = resolvePxWrap(wrap);
    var leg = host.querySelector && host.querySelector("[data-px-leg]");
    if (svg) {
      Array.prototype.forEach.call(svg.querySelectorAll("[data-fd-trend-legend]"), function (n) {
        n.setAttribute("opacity", "0");
        n.style.display = "none";
      });
    }
    if (host.querySelector && host.querySelector("[data-fd-trend-legend='html']")) return;
    var row = document.createElement("div");
    row.className = "fd-chart-legend-html fd-px-leg-trend";
    row.setAttribute("data-fd-trend-legend", "html");
    row.innerHTML = '<span class="pos">Positive ↑</span><span class="slash">/</span><span class="neg">Negative ↓</span><span class="note">price vs SMA50/SMA200</span>';
    if (leg) {
      leg.insertBefore(row, leg.firstChild);
      return;
    }
    var svgEl = svg || resolvePxSvg(host);
    if (svgEl && svgEl.parentNode) svgEl.parentNode.insertBefore(row, svgEl);
    else if (host.insertBefore) host.insertBefore(row, host.firstChild);
  }
  function polishChips(wrap) {
    var host = resolvePxWrap(wrap);
    if (!host || !host.querySelectorAll) return;
    var labels = host.querySelectorAll("span, small, div, b, label, em, [data-range], [data-ret]");
    var found = 0;
    for (var i = 0; i < labels.length; i++) {
      var el = labels[i];
      if (el.closest && el.closest("nav, .topnav, #gics-filter-strip, [data-px-leg], [data-px-svg]")) continue;
      if (el.getAttribute("data-fd-chip") === "1") continue;
      if (el.childElementCount > 2) continue;
      var t = (el.childElementCount ? (el.firstChild && el.firstChild.nodeType === 3 ? el.firstChild.textContent : "") : (el.textContent || "")).trim();
      if (!/^(1W|1M|1Y|3M|6M|YTD|Trend)$/i.test(t)) continue;
      var box = el.parentElement;
      if (!box) continue;
      box.classList.add("fd-px-chip");
      if (box.parentElement) box.parentElement.classList.add("fd-px-chip-row");
      el.classList.add("k");
      el.setAttribute("data-fd-chip", "1");
      box.setAttribute("data-fd-chip", "1");
      var sib = el.nextElementSibling;
      if (sib) {
        sib.classList.add("v");
        var vt = (sib.textContent || "").trim();
        if (/^-/.test(vt)) sib.classList.add("dn");
        else if (/^\+/.test(vt)) sib.classList.add("up");
      }
      found++;
    }
    return found;
  }
  function restylePxChart(wrap) {
    if (restylingPx) return;
    var host = resolvePxWrap(wrap);
    var svg = resolvePxSvg(host) || resolvePxSvg(wrap);
    if (!svg) return;
    restylingPx = true;
    try {
      applyAspect(svg);
      polishChips(host);
      var src = findPricePath(svg);
      if (!src) { ensureHtmlLegend(host, svg); return; }
      var hadSeg = !!svg.querySelector("[data-fd-trend-seg]");
      if (src.getAttribute("data-fd-trend-src") === "1" && hadSeg) {
        ensureHtmlLegend(host, svg);
        return;
      }
      if (src.getAttribute("data-fd-trend-src") === "1" && !hadSeg) showPricePath(src);
      Array.prototype.forEach.call(svg.querySelectorAll("[data-fd-trend-seg]"), function (n) { n.remove(); });
      var pts = parsePoints(src);
      if (pts.length < 2) { ensureHtmlLegend(host, svg); return; }
      pts = expandPadR(svg, pts);
      src = findPricePath(svg) || src;
      pts = parsePoints(src);
      var S = parsePxJson(host);
      var px = arrOf(S, ["px", "PX", "close", "closes", "price", "prices"]);
      var s50 = arrOf(S, ["s50", "S50", "sma50", "SMA50", "ma50"]);
      var s200 = arrOf(S, ["s200", "S200", "sma200", "SMA200", "ma200"]);
      var flags;
      if (px.length) {
        flags = drawnFlags(px, s50, s200);
        if (flags.length !== pts.length) {
          if (flags.length > pts.length) flags = flags.slice(flags.length - pts.length);
          else {
            var ys = pts.map(function (p) { return -p.y; });
            flags = flagsFromSeries(ys, smaArr(ys, SMA_FAST), ys.length >= SMA_SLOW ? smaArr(ys, SMA_SLOW) : []);
          }
        }
      } else {
        var ys2 = pts.map(function (p) { return -p.y; });
        flags = flagsFromSeries(ys2, smaArr(ys2, SMA_FAST), ys2.length >= SMA_SLOW ? smaArr(ys2, SMA_SLOW) : []);
      }
      var parent = src.parentNode || svg;
      var start = 0;
      var cur = flags[0] || "na";
      function emit(kind, a, b) {
        if (kind === "na") return;
        var chunk = slicePts(pts, a, b);
        if (chunk.length < 2) return;
        var el = svgEl("path");
        var cls = "fd-chart-line" + (kind === "pos" ? " fd-chart-line-pos" : kind === "neg" ? " fd-chart-line-neg" : "");
        el.setAttribute("class", cls);
        el.setAttribute("fill", "none");
        el.setAttribute("stroke", kind === "pos" ? "#22c55e" : kind === "neg" ? "#ef4444" : "#93c5fd");
        el.setAttribute("stroke-width", "1.7");
        el.setAttribute("stroke-linejoin", "round");
        el.setAttribute("stroke-linecap", "round");
        el.setAttribute("data-fd-trend-seg", kind);
        el.setAttribute("d", pathFromPts(chunk));
        parent.appendChild(el);
      }
      if (flags.some(function (f) { return f === "pos" || f === "neg"; })) {
        for (var j = 1; j < flags.length; j++) {
          var kind = flags[j] || "na";
          if (kind !== cur) { emit(cur, start, j - 1); start = j; cur = kind; }
        }
        emit(cur, start, flags.length - 1);
      }
      var drew = svg.querySelector("[data-fd-trend-seg]");
      if (drew) hidePricePath(src);
      else showPricePath(src);
      ensureHtmlLegend(host, svg);
    } catch (err) {
      try { showPricePath(src); } catch (e2) {}
    } finally {
      restylingPx = false;
    }
  }
  function patchPaintSource(fn) {
    try {
      var src = Function.prototype.toString.call(fn);
      if (!src || src.indexOf("[native code]") >= 0) return null;
      if (src.indexOf("__fdPxPatched") >= 0) return fn;
      var next = src
        .replace(/\bpadR\s*=\s*8\b/g, "padR=36")
        .replace(/\bH\s*=\s*220\b/g, "H=300");
      if (next === src) return null;
      var expr = /^\s*function(\s+[A-Za-z0-9_$]+)?\s*\(/.test(next) ? "(" + next + ")" : next;
      var patched = (0, eval)(expr);
      if (typeof patched !== "function") return null;
      patched.__fdPxPatched = true;
      return patched;
    } catch (e) { return null; }
  }
  function wrapPaintFn(orig) {
    if (typeof orig !== "function" || orig.__fdPxTrend) return orig;
    var body = patchPaintSource(orig) || orig;
    var wrapped = function (wrap) {
      var ret = body.apply(this, arguments);
      try { restylePxChart(wrap); } catch (e) {}
      return ret;
    };
    wrapped.__fdPxTrend = true;
    wrapped.__fdPxOrig = orig;
    wrapped.__fdPxBody = body;
    return wrapped;
  }
  function wrapPaintPxChart() {
    function install() {
      var orig = window.paintPxChart;
      try { if (typeof orig !== "function" && typeof paintPxChart === "function") orig = paintPxChart; } catch (e) {}
      if (typeof orig !== "function") return false;
      if (orig.__fdPxTrend) return true;
      var wrapped = wrapPaintFn(orig);
      window.paintPxChart = wrapped;
      try { paintPxChart = wrapped; } catch (e2) {}
      return true;
    }
    install();
    if (window.__FD_PX_WRAP__) return;
    window.__FD_PX_WRAP__ = true;
    try {
      var current = window.paintPxChart;
      Object.defineProperty(window, "paintPxChart", {
        configurable: true,
        enumerable: true,
        get: function () { return current; },
        set: function (v) { current = wrapPaintFn(v); }
      });
    } catch (e3) {}
    if (!install()) {
      var n = 0;
      var t = setInterval(function () {
        n++;
        if (install() || n > 40) clearInterval(t);
      }, 50);
    }
  }
  function observePxCharts() {
    if (window.__FD_PX_OBS__ || !document.documentElement) return;
    window.__FD_PX_OBS__ = new MutationObserver(function (muts) {
      if (restylingPx) return;
      var seen = [];
      function add(svg) {
        if (!svg || seen.indexOf(svg) >= 0) return;
        seen.push(svg);
      }
      for (var i = 0; i < muts.length; i++) {
        var t = muts[i].target;
        if (t && t.getAttribute && t.getAttribute("data-px-svg") != null) add(t.tagName === "svg" || t.tagName === "SVG" ? t : t.querySelector && t.querySelector("svg"));
        if (t && t.closest) {
          var svg = t.closest("[data-px-svg]");
          if (svg) add(svg.tagName === "svg" || svg.tagName === "SVG" ? svg : svg.querySelector && svg.querySelector("svg"));
        }
        var added = muts[i].addedNodes || [];
        for (var j = 0; j < added.length; j++) {
          var n = added[j];
          if (!n || n.nodeType !== 1) continue;
          if (n.getAttribute && n.getAttribute("data-fd-trend-seg")) continue;
          if (n.getAttribute && n.getAttribute("data-px-svg") != null) add(n.tagName === "svg" || n.tagName === "SVG" ? n : n.querySelector && n.querySelector("svg"));
          if (n.querySelector) {
            var s = n.querySelector("[data-px-svg]");
            if (s) add(s.tagName === "svg" || s.tagName === "SVG" ? s : s.querySelector && s.querySelector("svg"));
          }
        }
      }
      seen.forEach(function (svg) {
        if (svg) restylePxChart(svg.parentElement || svg);
      });
    });
    window.__FD_PX_OBS__.observe(document.documentElement, {childList: true, subtree: true});
  }
  function isTrendGlyph(el) {
    if (!el || !el.getAttribute) return false;
    if (el.getAttribute("data-fd-trend-seg") || el.getAttribute("data-fd-ma") || el.getAttribute("data-fd-trend-legend")) return true;
    var cls = el.getAttribute("class") || "";
    return cls.indexOf("fd-chart-ma50") >= 0 || cls.indexOf("fd-chart-ma200") >= 0 || cls.indexOf("fd-chart-line-pos") >= 0 || cls.indexOf("fd-chart-line-neg") >= 0;
  }
  function sourcePoly(chart) {
    if (!chart || !chart.querySelectorAll) return null;
    var best = null, bestN = 0;
    var nodes = chart.querySelectorAll("polyline, path");
    for (var i = 0; i < nodes.length; i++) {
      var el = nodes[i];
      if (isTrendGlyph(el)) continue;
      if (el.closest && el.closest(".fd-chart-mark")) continue;
      var pts = parsePoints(el);
      if (pts.length > bestN) { best = el; bestN = pts.length; }
    }
    return bestN >= 2 ? best : null;
  }
  function svgEl(name) {
    return document.createElementNS("http://www.w3.org/2000/svg", name);
  }
  function polyEl(cls, pts, attrs) {
    var el = svgEl("polyline");
    el.setAttribute("class", cls);
    el.setAttribute("fill", "none");
    el.setAttribute("points", pts.map(function (p) { return p.x.toFixed(1) + "," + p.y.toFixed(1); }).join(" "));
    if (attrs) Object.keys(attrs).forEach(function (k) { el.setAttribute(k, attrs[k]); });
    return el;
  }
  function slicePts(pts, i0, i1) {
    var start = i0 > 0 ? i0 - 1 : i0;
    return pts.slice(start, i1 + 1);
  }
  function addLegend(svg, width) {
    if (!svg || svg.querySelector("[data-fd-trend-legend]")) return;
    var g = svgEl("g");
    g.setAttribute("class", "fd-chart-legend");
    g.setAttribute("data-fd-trend-legend", "1");
    function tx(cls, x, text) {
      var t = svgEl("text");
      t.setAttribute("class", cls);
      t.setAttribute("x", String(x));
      t.setAttribute("y", "14");
      t.textContent = text;
      g.appendChild(t);
    }
    function tick(cls, x) {
      var ln = svgEl("line");
      ln.setAttribute("class", cls);
      ln.setAttribute("x1", String(x));
      ln.setAttribute("y1", "11");
      ln.setAttribute("x2", String(x + 14));
      ln.setAttribute("y2", "11");
      g.appendChild(ln);
    }
    tx("fd-chart-legend-pos", 10, "Positive ↑");
    tx("fd-chart-legend-slash", 82, "/");
    tx("fd-chart-legend-neg", 92, "Negative ↓");
    tx("fd-chart-legend-note", 186, "Trend Signals");
    var x50 = Math.min((width || 400) - 150, 318);
    var x200 = Math.min((width || 400) - 72, 400);
    tick("fd-chart-ma50", x50);
    tx("fd-chart-legend-ma", x50 + 18, "50-Day MA");
    tick("fd-chart-ma200", x200);
    tx("fd-chart-legend-ma", x200 + 18, "200-Day MA");
    svg.insertBefore(g, svg.firstChild);
  }
  function isDetailChart(chart) {
    if (!chart) return false;
    if (chart.classList && chart.classList.contains("fd-chart-detail")) return true;
    if (chart.id && /detail|name-chart|px-chart|chart-main/i.test(chart.id)) return true;
    if (chart.closest && chart.closest(DETAIL_SEL)) return true;
    var box = chart.getBoundingClientRect ? chart.getBoundingClientRect() : {width: 0};
    return box.width >= 300;
  }
  function drawTrend(chart) {
    if (!chart || chart.tagName === "CANVAS") return;
    if (isPxHost(chart)) { restylePxChart(chart); return; }
    if (chart.getAttribute && chart.getAttribute("data-fd-trend") === "1") return;
    if (chart.classList && chart.classList.contains("fd-chart") && chart.querySelector("[data-fd-trend-seg], [data-fd-ma]")) return;
    var src = sourcePoly(chart);
    if (!src) return;
    var pts = parsePoints(src);
    if (pts.length < 2) return;
    var parent = src.parentNode;
    if (!parent) return;
    Array.prototype.forEach.call(parent.querySelectorAll("[data-fd-trend-seg], [data-fd-ma]"), function (n) { n.remove(); });
    var ys = pts.map(function (p) { return -p.y; });
    var s50 = smaArr(ys, SMA_FAST);
    var s200 = ys.length >= SMA_SLOW ? smaArr(ys, SMA_SLOW) : ys.map(function () { return null; });
    var ma50 = [], ma200 = [];
    for (var i = 0; i < pts.length; i++) {
      if (s50[i] != null) ma50.push({x: pts[i].x, y: -s50[i]});
      if (s200[i] != null) ma200.push({x: pts[i].x, y: -s200[i]});
    }
    if (ma50.length) parent.insertBefore(polyEl("fd-chart-ma50", ma50, {"data-fd-ma": "50"}), src);
    if (ma200.length) parent.insertBefore(polyEl("fd-chart-ma200", ma200, {"data-fd-ma": "200"}), src);
    var flags = ys.map(function (c, idx) { return trendFlag(c, s50[idx], s200[idx]); });
    if (flags.every(function (f) { return f == null; })) return;
    var start = 0;
    var cur = flags[0] || "na";
    function emit(kind, a, b) {
      var chunk = slicePts(pts, a, b);
      if (chunk.length < 2) return;
      var cls = "fd-chart-line" + (kind === "pos" ? " fd-chart-line-pos" : kind === "neg" ? " fd-chart-line-neg" : "");
      parent.insertBefore(polyEl(cls, chunk, {"data-fd-trend-seg": kind}), src);
    }
    for (var j = 1; j < flags.length; j++) {
      var kind = flags[j] || "na";
      if (kind !== cur) { emit(cur, start, j - 1); start = j; cur = kind; }
    }
    emit(cur, start, flags.length - 1);
    if (parent.querySelector("[data-fd-trend-seg]")) {
      src.setAttribute("data-fd-trend-src", "1");
      src.style.opacity = "0";
    }
    var svg = chart.tagName === "svg" || chart.tagName === "SVG" ? chart : (chart.querySelector && chart.querySelector("svg"));
    if (svg && isDetailChart(chart)) addLegend(svg, (svg.viewBox && svg.viewBox.baseVal && svg.viewBox.baseVal.width) || chart.getBoundingClientRect().width);
  }
  function wrapSelect() {
    var orig = window.selectTicker;
    try { if (typeof orig !== "function" && typeof selectTicker === "function") orig = selectTicker; } catch (e) {}
    if (typeof orig !== "function" || orig.__fdTrend) return;
    var wrapped = function () {
      var r = orig.apply(this, arguments);
      setTimeout(apply, 40);
      return r;
    };
    wrapped.__fdTrend = true;
    window.selectTicker = wrapped;
    try { selectTicker = wrapped; } catch (e2) {}
  }
  function canSkinnyDrill() {
    if (typeof window.selectTicker === "function") return false;
    try { if (typeof selectTicker === "function") return false; } catch (e) {}
    return !!document.querySelector("svg.fd-chart[data-fd-trend]");
  }
  function ensureDrillHost() {
    var live = document.querySelector("#detail-chart, #name-chart, #px-chart, #chart-main, .name-chart, .detail-chart");
    if (live) return null;
    var host = document.getElementById("fd-name-drill");
    if (host) return host;
    host = document.createElement("section");
    host.id = "fd-name-drill";
    host.setAttribute("data-fd-drill-chart", "1");
    var home = document.getElementById("home");
    if (home && home.parentNode) home.parentNode.insertBefore(host, home);
    else (document.body || document.documentElement).insertBefore(host, (document.body || document.documentElement).firstChild);
    return host;
  }
  function showSkinnyDrill(card) {
    if (!canSkinnyDrill() || !card) return;
    var existing = document.querySelector("#fd-name-drill svg.fd-chart-detail[data-fd-trend]");
    if (existing) return;
    var src = card.querySelector && card.querySelector("svg.fd-chart");
    if (!src) return;
    var host = ensureDrillHost();
    if (!host) return;
    host.classList.add("is-on");
    host.innerHTML = "";
    var legend = document.createElement("div");
    legend.className = "fd-chart-legend-html";
    legend.innerHTML = '<span class="pos">Positive ↑</span> / <span class="neg">Negative ↓</span> Trend Signals · <span class="ma50">50-Day MA</span> · <span class="ma200">200-Day MA</span>';
    var clone = src.cloneNode(true);
    clone.classList.add("fd-chart-detail");
    clone.setAttribute("width", "560");
    clone.setAttribute("height", "200");
    clone.setAttribute("aria-label", "name-drill price chart with MA trend coloring");
    host.appendChild(legend);
    host.appendChild(clone);
  }
  function apply() {
    var map = db();
    polishTags(document);
    wrapSelect();
    wrapPaintPxChart();
    observePxCharts();
    var pxNodes = document.querySelectorAll("[data-px-svg], [data-px-chart]");
    for (var p = 0; p < pxNodes.length; p++) restylePxChart(pxNodes[p]);
    var charts = document.querySelectorAll(CHART_SEL);
    for (var i = 0; i < charts.length; i++) {
      var chart = charts[i];
      if (chart.closest && chart.closest("nav, .topnav")) continue;
      polishTags(chart);
      if (isPxHost(chart)) continue;
      drawTrend(chart);
      var rec = recOf(tickerOf(chart), map);
      if (rec) drawStreak(chart, rec);
    }
  }
  document.addEventListener("click", function (ev) {
    var t = ev.target && ev.target.closest ? ev.target.closest("article.card, article[data-t], [data-range], .chart-range") : null;
    if (!t) return;
    if (ev.target.closest && ev.target.closest(".fd-paper, [data-fd-paper-act], button.nav-btn, #refresh, #options-refresh")) return;
    var card = ev.target.closest && ev.target.closest("article.card, article[data-t]");
    setTimeout(function () {
      apply();
      if (card) showSkinnyDrill(card);
    }, 40);
  }, true);
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", apply);
  else apply();
})();
""".strip()


def _upsert_block(text: str, pattern: str, block: str) -> str:
    next_text, n = re.subn(pattern, lambda _m: block, text, count=1, flags=re.I | re.S)
    if n:
        return next_text
    if "</head>" in block or block.lstrip().startswith("<style"):
        if "</head>" in text:
            return text.replace("</head>", block + "</head>", 1)
    if "</body>" in text:
        return text.replace("</body>", block + "</body>", 1)
    return text + block


def ensure_embedded(html_text: str, mapping: Mapping[str, Any] | None) -> str:
    """Always leave chart CSS + overlay JS. Safe on live ~4.8MB HTML.

    ``mapping=None`` re-injects CSS + the ``paintPxChart`` wrap without wiping
    ``#fd-chart-db``. Pass a dict (including ``{}``) to replace the db.
    """
    text = html_text or ""
    css = f'<style id="{CSS_STYLE_ID}">\n{strip_css()}\n</style>\n'
    text = _upsert_block(
        text,
        rf'<style\b[^>]*\bid=["\']{CSS_STYLE_ID}["\'][^>]*>.*?</style>\s*',
        css,
    )
    have_db = bool(re.search(rf'id=["\']{DB_SCRIPT_ID}["\']', text, re.I))
    if mapping is not None or not have_db:
        db = embed_db(mapping)
        text = _upsert_block(
            text,
            rf'<script\b[^>]*\bid=["\']{DB_SCRIPT_ID}["\'][^>]*>.*?</script>\s*',
            db + ("\n" if not have_db else ""),
        )
    script = f'<script id="{JS_SCRIPT_ID}">\n{overlay_js()}\n</script>\n'
    text = _upsert_block(
        text,
        rf'<script\b[^>]*\bid=["\']{JS_SCRIPT_ID}["\'][^>]*>.*?</script>\s*',
        script,
    )
    return text


def inject_paintpx(html_text: str) -> str:
    """Patch live ``factorbook.html`` with the ``paintPxChart`` wrap.

    CSS + overlay JS only. Does not rebuild skinny ``desk_dash`` HTML and does
    not wipe ``#fd-chart-db`` / Paper / Experimental / Breakout.
    """
    return ensure_embedded(html_text, None)
