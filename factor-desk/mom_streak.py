"""Momentum-score streak tag for home UP/DOWN cards.

Score
-----
Home cards rank Momentum Up / Down by a numeric momentum/trend score, typically
a small 0–13-style rank (not options ``score_v2``, not the scrapped Sectors
0–100 ``trend_score``).

Resolver, first hit:

1. Card/row fields already used for UP/DOWN ranking:
   ``mom_score``, ``momentum_score``, ``mom_rank``, ``trend_rank``,
   then ``score`` / ``trend_score`` only when the value is in ``[0, 20]``.
2. Durable history on disk (``mom_score_hist.json``, then v0/score CSVs).
3. If still missing **or hist is empty/thin**: **13-horizon trend-window count**
   from ``prices_long.csv`` / ``prices.csv`` (closes). That fallback is 0–13 =
   how many of ``TREND_WINDOWS`` have a positive total return. Missing or thin
   ``mom_score_hist.json`` is auto-backfilled (~120 trading days) on
   ``rebuild_hist_for_cards`` / Refresh so the first deploy is not every name
   ``1d>5``. Live card ``mom_score`` still wins for *today's* point.

Threshold
---------
Literal **5**. Current regime is the side of today's score:

- ``score > 5`` → above, tag ``↑{n}d>5``
- ``score < 5`` → below, tag ``↓{n}d<5``
- ``score == 5`` → neither, streak **0**, tag ``=5`` (visible at-cut, not missing)
- null score → no tag

``n`` is consecutive **trading days** of history on the same side, including
today. A day at 5, or a missing print, breaks the streak.

10-day change
-------------
``D10_LOOKBACK`` (10) is a count of **points** in the score series, not
calendar days. After today's upsert, ``prior = series[-(10+1)]`` and
``delta = current_score - prior.score``. Fewer than 11 points → no chip.
"""

from __future__ import annotations

import csv
import json
import logging
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, MutableMapping, Sequence

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import dapi_enrich  # noqa: E402

LOG = logging.getLogger("mom_streak")

HIST_FILENAME = "mom_score_hist.json"
THRESHOLD = 5.0
# Points in the hist series (trading prints), not calendar days.
D10_LOOKBACK = 10
MAX_SERIES = 252
# Fri→Mon is 3 calendar days. A longer hole in the series breaks the streak.
MAX_GAP_DAYS = 4
# First Refresh after deploy has no hist. Backfill this many daily scores
# from prices_long (need max(TREND_WINDOWS) extra closes of lookback).
BACKFILL_SCORE_DAYS = 120
THIN_SERIES_MEDIAN = 5
BACKFILL_SOURCE_PRICES_LONG = "prices_long.trend_windows_backfill"

# 13 lookbacks in trading days → score in 0..13. Used only when the live
# card does not already carry the UP/DOWN rank.
TREND_WINDOWS: tuple[int, ...] = (5, 10, 15, 20, 25, 30, 40, 50, 60, 80, 100, 150, 200)
PRICE_LOOKBACK = max(TREND_WINDOWS)

CARD_SCORE_KEYS: tuple[str, ...] = (
    "mom_score",
    "momentum_score",
    "mom_rank",
    "trend_rank",
)
OPTIONAL_SMALL_SCORE_KEYS: tuple[str, ...] = ("score", "trend_score")
# Options pulse / Sectors leftovers — never treat as the home MOM rank.
SKIP_SCORE_KEYS: frozenset[str] = frozenset({"score_v2", "opt_score", "options_score"})

HIST_CANDIDATES: tuple[str, ...] = (
    HIST_FILENAME,
    "mom_scores.csv",
    "score_hist.csv",
    "scores.csv",
    "v0_residuals.csv",
    "residuals.csv",
    "v0_scores.csv",
    "clean/mom_scores.csv",
    "clean/v0_residuals.csv",
    "clean/residuals.csv",
    "clean/scores.csv",
)
PRICE_CANDIDATES: tuple[str, ...] = (
    "prices_long.csv",
    "prices.csv",
    "px.csv",
    "closes.csv",
    "clean/prices_long.csv",
    "clean/prices.csv",
    "clean/px.csv",
)
DATE_COLS = ("date", "asof", "as_of", "day", "dt")
TICKER_COLS = ("ticker", "name", "symbol", "yellow", "bbg")
SCORE_COLS = ("mom_score", "momentum_score", "mom_rank", "trend_rank", "score", "rank")
PX_COLS = ("px_last", "px", "close", "adj_close", "px_close", "last", "PX_LAST")
RESIDUAL_COLS = ("residual", "resid", "v0", "residual_v0", "residual_20d")

DB_SCRIPT_ID = "mom-streak-db"
JS_SCRIPT_ID = "mom-streak-js"

PILL_KEY = "mom-streak"
D10_PILL_KEY = "mom-score-d10"
# Unicode minus, same glyph in labels and tooltips.
MINUS = "\u2212"
ARROW_RIGHT = "\u2192"


def _today() -> date:
    return datetime.now().date()


def _as_date(value: Any) -> date | None:
    parsed = dapi_enrich.as_date(value)
    if parsed is not None:
        return parsed
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(text[:10] if fmt.startswith("%Y-%") else text, fmt).date()
        except ValueError:
            continue
    return None


ARROW_UP = "\u2191"  # ↑
ARROW_DOWN = "\u2193"  # ↓


def _ticker_key(ticker: str) -> str:
    return dapi_enrich.name_key(ticker) if ticker else ""


def _short(ticker: str) -> str:
    parts = (ticker or "").split()
    return parts[0].upper() if parts else ""


def card_ticker(card: Mapping[str, Any] | None) -> str:
    """Live FLAGS/WATCH/MOM cards use ``t`` (and ``score``), not always ``ticker``."""
    if not card:
        return ""
    return str(card.get("ticker") or card.get("name") or card.get("t") or card.get("symbol") or "").strip()


def in_home_score_range(value: float | None) -> bool:
    if value is None:
        return False
    return 0.0 <= float(value) <= 20.0


def resolve_card_score(item: Mapping[str, Any] | None) -> tuple[float | None, str | None]:
    """Return ``(score, source_key)`` from a MOM/FLAGS/WATCH card or enrich row."""
    if not item:
        return None, None
    for key in SKIP_SCORE_KEYS:
        # ignore
        _ = item.get(key)
    for key in CARD_SCORE_KEYS:
        val = dapi_enrich.as_float(item.get(key))
        if val is not None:
            return val, key
    for key in OPTIONAL_SMALL_SCORE_KEYS:
        val = dapi_enrich.as_float(item.get(key))
        if val is not None and in_home_score_range(val):
            return val, key
    return None, None


def trend_window_score(closes: Sequence[float | None], windows: Sequence[int] = TREND_WINDOWS) -> int | None:
    """Count of the 13 horizons with positive total return. ``None`` if too short."""
    px = [c for c in closes if c is not None]
    if len(px) < windows[0] + 1:
        return None
    last = px[-1]
    if last is None or last == 0:
        return None
    hits = 0
    usable = 0
    for window in windows:
        if len(px) <= window:
            continue
        prev = px[-1 - window]
        if prev is None or prev == 0:
            continue
        usable += 1
        if last / prev - 1.0 > 0:
            hits += 1
    if usable == 0:
        return None
    return hits


def side_of(score: float | None, threshold: float = THRESHOLD) -> str | None:
    if score is None:
        return None
    if score > threshold:
        return "above"
    if score < threshold:
        return "below"
    return "at"


def streak_vs_threshold(
    scores: Sequence[float | None] | None,
    threshold: float = THRESHOLD,
    dates: Sequence[date] | None = None,
) -> tuple[int, str | None]:
    """``scores`` oldest → newest. Returns ``(streak_days, side)``.

    Exactly ``threshold`` is neither side: streak 0, side ``at``.
    When ``dates`` is provided, a calendar gap longer than ``MAX_GAP_DAYS``
    (weekend + holiday) breaks the run so this is a trading-day streak.
    """
    series = list(scores or [])
    if not series:
        return 0, None
    current = series[-1]
    side = side_of(current, threshold)
    if side is None:
        return 0, None
    if side == "at":
        return 0, "at"
    n = 0
    prev_day: date | None = None
    date_list = list(dates) if dates is not None else [None] * len(series)
    if len(date_list) != len(series):
        date_list = [None] * len(series)
    for value, day in zip(reversed(series), reversed(date_list)):
        s = side_of(value, threshold)
        if s != side:
            break
        if prev_day is not None and day is not None and (prev_day - day).days > MAX_GAP_DAYS:
            break
        n += 1
        prev_day = day
    return n, side


def streak_span(
    series: Sequence[tuple[date, float]] | None,
    threshold: float = THRESHOLD,
) -> dict[str, Any] | None:
    """Begin/end of the **current** Feature A streak (latest print inclusive).

    ``open`` is True while the latest print is still on that side. A score of
    exactly 5 is not a streak — returns None (the card still gets ``=5``).
    """
    rows = list(series or [])
    if not rows:
        return None
    n, side = streak_from_dated(rows, threshold)
    if side in (None, "at") or n <= 0:
        return None
    start_d = rows[-n][0]
    end_d = rows[-1][0]
    return {
        "start": start_d.isoformat(),
        "end": end_d.isoformat(),
        "open": True,
        "side": side,
        "n": n,
        "label": tag_label(n, side, threshold),
    }


def streak_from_dated(
    series: Sequence[tuple[date, float]] | None,
    threshold: float = THRESHOLD,
) -> tuple[int, str | None]:
    rows = list(series or [])
    if not rows:
        return 0, None
    return streak_vs_threshold([s for _, s in rows], threshold, dates=[d for d, _ in rows])


def tag_label(streak: int, side: str | None, threshold: float = THRESHOLD) -> str | None:
    if side is None:
        return None
    cut = int(threshold) if float(threshold).is_integer() else threshold
    if side == "above":
        return f"{ARROW_UP}{int(streak)}d>{cut}"
    if side == "below":
        return f"{ARROW_DOWN}{int(streak)}d<{cut}"
    return f"={cut}"


def tag_cls(side: str | None) -> str:
    if side == "above":
        return "mom-streak-up"
    if side == "below":
        return "mom-streak-down"
    if side == "at":
        return "mom-streak-at"
    return "mom-streak"


def tag_title(streak: int, side: str | None, score: float | None, threshold: float = THRESHOLD) -> str:
    sc = "—" if score is None else (str(int(score)) if float(score).is_integer() else f"{score:g}")
    cut = int(threshold) if float(threshold).is_integer() else threshold
    if side == "above":
        return f"{streak} trading days with momentum score > {cut} (score {sc})"
    if side == "below":
        return f"{streak} trading days with momentum score < {cut} (score {sc})"
    if side == "at":
        return f"momentum score at {cut} — streak 0"
    return "momentum score unavailable"


def pill_for(streak: int, side: str | None, score: float | None, threshold: float = THRESHOLD) -> dict[str, str] | None:
    label = tag_label(streak, side, threshold)
    if not label:
        return None
    return {
        "key": PILL_KEY,
        "label": label,
        "cls": tag_cls(side),
        "title": tag_title(streak, side, score, threshold),
    }


def _whole(value: float) -> int | float:
    f = float(value)
    if abs(f - round(f)) < 1e-9:
        return int(round(f))
    return f


def _signed(value: float) -> str:
    n = _whole(value)
    if n > 0:
        return f"+{n}"
    if n < 0:
        return f"{MINUS}{abs(n)}"
    return "0"


def d10_short(delta: float) -> str:
    """Signed delta under the composite score: ``+3``, ``−2``, ``0``."""
    return _signed(delta)


def d10_label(delta: float) -> str:
    """Display label under the score. No ``10d`` prefix."""
    return d10_short(delta)


def d10_cls(delta: float) -> str:
    if delta > 0:
        return "mom-score-d10-up"
    if delta < 0:
        return "mom-score-d10-down"
    return "mom-score-d10"


def d10_side(delta: float) -> str:
    if delta > 0:
        return "up"
    if delta < 0:
        return "down"
    return "flat"


def d10_title(current: float, prior: float, prior_date: date | str, delta: float) -> str:
    when = prior_date.isoformat() if isinstance(prior_date, date) else str(prior_date)[:10]
    return (
        f"composite score 10 trading days: was {_whole(prior)} on {when}"
        f" {ARROW_RIGHT} now {_whole(current)} (\u0394 {_signed(delta)})"
    )


def score_change_d10(
    series: Sequence[tuple[date, float]] | None,
    current_score: float | None,
    lookback: int = D10_LOOKBACK,
) -> dict[str, Any] | None:
    """Delta vs the score ``lookback`` prints earlier. ``None`` if the series is short."""
    if current_score is None or not series:
        return None
    if len(series) < lookback + 1:
        return None
    prior_date, prior_score = series[-(lookback + 1)]
    delta = float(current_score) - float(prior_score)
    return {
        "mom_score_d10": _whole(delta),
        "mom_score_d10_prior": _whole(prior_score),
        "mom_score_d10_date": prior_date.isoformat(),
        "mom_score_d10_label": d10_label(delta),
        "mom_score_d10_short": d10_short(delta),
    }


D10_CARD_KEYS: tuple[str, ...] = (
    "mom_score_d10",
    "mom_score_d10_prior",
    "mom_score_d10_date",
    "mom_score_d10_label",
    "mom_score_d10_short",
)


# ---------------------------------------------------------------------------
# History IO
# ---------------------------------------------------------------------------


def default_hist_path(root: Path | None = None) -> Path:
    base = Path(root) if root is not None else HERE
    return base / HIST_FILENAME


def load_hist(path: Path | str | None = None, root: Path | None = None) -> dict[str, Any]:
    p = Path(path) if path is not None else default_hist_path(root)
    if not p.is_file():
        return {"asof": None, "threshold": THRESHOLD, "names": {}, "meta": {"source": "empty"}}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        LOG.warning("could not read %s: %s", p, exc)
        return {"asof": None, "threshold": THRESHOLD, "names": {}, "meta": {"source": "unreadable"}}
    if not isinstance(data, dict):
        return {"asof": None, "threshold": THRESHOLD, "names": {}, "meta": {"source": "invalid"}}
    data.setdefault("names", {})
    data.setdefault("threshold", THRESHOLD)
    return data


def write_hist(hist: Mapping[str, Any], path: Path | str | None = None, root: Path | None = None) -> Path:
    p = Path(path) if path is not None else default_hist_path(root)
    tmp = p.with_suffix(p.suffix + ".tmp")
    text = json.dumps(hist, indent=2, default=str)
    tmp.write_text(text + "\n", encoding="utf-8")
    os.replace(tmp, p)
    return p


def series_of(hist: Mapping[str, Any] | None, ticker: str) -> list[tuple[date, float]]:
    out: list[tuple[date, float]] = []
    if not hist:
        return out
    names = hist.get("names") if isinstance(hist.get("names"), dict) else hist
    rec = None
    if isinstance(names, Mapping):
        rec = names.get(ticker) or names.get(_ticker_key(ticker)) or names.get(_short(ticker))
        if rec is None:
            short = _short(ticker)
            for key, value in names.items():
                if _short(str(key)) == short or _ticker_key(str(key)) == _ticker_key(ticker):
                    rec = value
                    break
    if isinstance(rec, Mapping):
        raw = rec.get("series") or rec.get("scores") or rec.get("history")
        if isinstance(raw, list):
            for row in raw:
                if isinstance(row, Mapping):
                    d = _as_date(row.get("date") or row.get("asof"))
                    s = dapi_enrich.as_float(row.get("score") or row.get("mom_score") or row.get("value"))
                    if d is not None and s is not None:
                        out.append((d, s))
                elif isinstance(row, (list, tuple)) and len(row) >= 2:
                    d = _as_date(row[0])
                    s = dapi_enrich.as_float(row[1])
                    if d is not None and s is not None:
                        out.append((d, s))
    out.sort(key=lambda x: x[0])
    return out


def _median(values: Sequence[int]) -> float:
    if not values:
        return 0.0
    xs = sorted(values)
    n = len(xs)
    mid = n // 2
    if n % 2:
        return float(xs[mid])
    return (xs[mid - 1] + xs[mid]) / 2.0


def hist_is_thin(hist: Mapping[str, Any] | None, asof: date | None = None) -> bool:
    """True when hist is missing, empty, median series < 5, or only one asof day."""
    if not hist:
        return True
    names = hist.get("names") if isinstance(hist.get("names"), dict) else {}
    if not names:
        return True
    lengths: list[int] = []
    all_dates: set[date] = set()
    for ticker in names:
        series = series_of(hist, ticker)
        lengths.append(len(series))
        for day, _score in series:
            all_dates.add(day)
    if not lengths or max(lengths) == 0:
        return True
    if _median(lengths) < THIN_SERIES_MEDIAN:
        return True
    if len(all_dates) <= 1:
        return True
    today = asof or _as_date(hist.get("asof"))
    if today is not None and all_dates <= {today}:
        return True
    return False


def _merge_series_maps(
    base: Mapping[str, list[tuple[date, float]]],
    overlay: Mapping[str, list[tuple[date, float]]],
) -> dict[str, list[tuple[date, float]]]:
    """Overlay wins on the same date (live / hist today over backfill)."""
    out: dict[str, list[tuple[date, float]]] = {k: list(v) for k, v in base.items()}
    for ticker, rows in overlay.items():
        combined = {d: s for d, s in out.get(ticker, [])}
        for d, s in rows:
            combined[d] = s
        out[ticker] = sorted(combined.items())[-MAX_SERIES:]
    return out


def _upsert_point(series: list[tuple[date, float]], day: date, score: float) -> list[tuple[date, float]]:
    if series and series[-1][0] == day:
        series = series[:-1] + [(day, score)]
    elif any(d == day for d, _ in series):
        series = [(d, score if d == day else s) for d, s in series]
    else:
        series = series + [(day, score)]
    series.sort(key=lambda x: x[0])
    return series[-MAX_SERIES:]


def record_to_hist(
    hist: MutableMapping[str, Any],
    ticker: str,
    score: float,
    *,
    asof: date | None = None,
    source: str | None = None,
    threshold: float = THRESHOLD,
) -> dict[str, Any]:
    day = asof or _today()
    names = hist.setdefault("names", {})
    if not isinstance(names, dict):
        names = {}
        hist["names"] = names
    key = _ticker_key(ticker) or ticker
    rec = names.get(key) if isinstance(names.get(key), dict) else {}
    series = series_of({"names": {key: rec}}, key)
    series = _upsert_point(series, day, score)
    streak, side = streak_from_dated(series, threshold)
    payload = {
        "series": [{"date": d.isoformat(), "score": s} for d, s in series],
        "score": score,
        "streak": streak,
        "side": side,
        "label": tag_label(streak, side, threshold),
        "source": source or rec.get("source"),
        "asof": day.isoformat(),
    }
    names[key] = payload
    hist["asof"] = day.isoformat()
    hist["threshold"] = threshold
    return payload


# ---------------------------------------------------------------------------
# Disk loaders (prices / v0 residuals / score CSVs)
# ---------------------------------------------------------------------------


def _open_table(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        sample = fh.read(4096)
        fh.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(fh, dialect=dialect)
        rows = []
        for row in reader:
            rows.append({str(k).strip(): (v if v is not None else "") for k, v in row.items() if k})
        return rows


def _col(row: Mapping[str, str], names: Sequence[str]) -> str | None:
    lower = {k.lower(): k for k in row}
    for name in names:
        key = lower.get(name.lower())
        if key is not None:
            return row.get(key)
    return None


def load_score_csv(path: Path) -> dict[str, list[tuple[date, float]]]:
    """Dated score series. Residual-only files are skipped (not a 0–13 rank)."""
    out: dict[str, list[tuple[date, float]]] = {}
    try:
        rows = _open_table(path)
    except OSError as exc:
        LOG.warning("score csv %s: %s", path, exc)
        return out
    if not rows:
        return out
    header = {k.lower() for k in rows[0]}
    has_score = any(c.lower() in header for c in SCORE_COLS)
    has_resid = any(c.lower() in header for c in RESIDUAL_COLS)
    if not has_score:
        LOG.info(
            "skip %s — no score/mom_score column%s",
            path.name,
            " (residual-only is not the home rank)" if has_resid else "",
        )
        return out
    for row in rows:
        d = _as_date(_col(row, DATE_COLS))
        ticker = (_col(row, TICKER_COLS) or "").strip()
        score = dapi_enrich.as_float(_col(row, SCORE_COLS))
        if d is None or not ticker or score is None:
            continue
        if not in_home_score_range(score):
            continue
        key = _ticker_key(ticker)
        out.setdefault(key, []).append((d, score))
    for key, series in out.items():
        series.sort(key=lambda x: x[0])
        out[key] = series[-MAX_SERIES:]
    return out


def load_price_csv(path: Path) -> dict[str, list[tuple[date, float]]]:
    out: dict[str, list[tuple[date, float]]] = {}
    try:
        rows = _open_table(path)
    except OSError as exc:
        LOG.warning("price csv %s: %s", path, exc)
        return out
    for row in rows:
        d = _as_date(_col(row, DATE_COLS))
        ticker = (_col(row, TICKER_COLS) or "").strip()
        px = dapi_enrich.as_float(_col(row, PX_COLS))
        if d is None or not ticker or px is None:
            continue
        key = _ticker_key(ticker)
        out.setdefault(key, []).append((d, px))
    for key, series in out.items():
        series.sort(key=lambda x: x[0])
        out[key] = series
    return out


def scores_from_prices(
    panel: Mapping[str, list[tuple[date, float]]],
    *,
    score_days: int | None = None,
    lookback: int | None = None,
) -> dict[str, list[tuple[date, float]]]:
    """Daily ``trend_window_score`` series. ``score_days`` keeps the tail (default max)."""
    keep_scores = score_days if score_days is not None else MAX_SERIES
    look = lookback if lookback is not None else PRICE_LOOKBACK
    keep_px = look + keep_scores
    out: dict[str, list[tuple[date, float]]] = {}
    for ticker, rows in panel.items():
        ordered = sorted(rows, key=lambda x: x[0])[-keep_px:]
        dates = [d for d, _ in ordered]
        closes = [p for _, p in ordered]
        series: list[tuple[date, float]] = []
        for i in range(len(closes)):
            score = trend_window_score(closes[: i + 1])
            if score is not None:
                series.append((dates[i], float(score)))
        if series:
            out[ticker] = series[-keep_scores:]
    return out


def load_price_panel(root: Path | None = None) -> tuple[dict[str, list[tuple[date, float]]], str]:
    """First readable prices file under the factorbook root (``prices_long.csv`` first)."""
    base = Path(root) if root is not None else HERE
    for rel in PRICE_CANDIDATES:
        path = base / rel
        if not path.is_file():
            continue
        panel = load_price_csv(path)
        if panel:
            return panel, rel
    return {}, ""


def backfill_source_for(rel: str) -> str:
    name = Path(str(rel)).name.lower()
    if name == "prices_long.csv":
        return BACKFILL_SOURCE_PRICES_LONG
    return f"prices.trend_windows_backfill:{rel}"


def backfill_from_prices(
    root: Path | None = None,
    *,
    score_days: int = BACKFILL_SCORE_DAYS,
) -> tuple[dict[str, list[tuple[date, float]]], str]:
    """Compute ~120 trading days of 0–13 scores from ``prices_long.csv`` (or prices.csv)."""
    panel, rel = load_price_panel(root)
    if not panel:
        return {}, ""
    computed = scores_from_prices(panel, score_days=score_days, lookback=PRICE_LOOKBACK)
    if not computed:
        return {}, ""
    return computed, backfill_source_for(rel)


def discover_score_series(
    root: Path | None = None,
    hist: Mapping[str, Any] | None = None,
) -> tuple[dict[str, list[tuple[date, float]]], str]:
    """Load the best on-disk score history. Thin hist does not block a prices backfill."""
    base = Path(root) if root is not None else HERE
    if hist is None:
        hist = load_hist(root=base)
    merged: dict[str, list[tuple[date, float]]] = {}
    source = "none"
    thin = True

    hist_names = hist.get("names") if isinstance(hist.get("names"), dict) else {}
    if hist_names:
        for ticker in hist_names:
            series = series_of(hist, ticker)
            if series:
                merged[ticker] = series
        if merged:
            source = HIST_FILENAME
            thin = hist_is_thin(hist)
            if not thin:
                return merged, source

    for rel in HIST_CANDIDATES:
        if rel == HIST_FILENAME:
            continue
        path = base / rel
        if not path.is_file():
            continue
        extra = load_score_csv(path)
        if not extra:
            continue
        for ticker, series in extra.items():
            merged.setdefault(ticker, series)
        source = f"{source}+{rel}" if source != "none" else rel
        thin = False

    if merged and not thin:
        return merged, source

    filled, bsrc = backfill_from_prices(base)
    if filled:
        if merged:
            filled = _merge_series_maps(filled, merged)
        return filled, bsrc
    return merged, source


def merge_into_hist(
    hist: MutableMapping[str, Any],
    series_by_ticker: Mapping[str, list[tuple[date, float]]],
    *,
    source: str | None = None,
    threshold: float = THRESHOLD,
    prefer_existing: bool = False,
) -> MutableMapping[str, Any]:
    for ticker, series in series_by_ticker.items():
        if not series:
            continue
        names = hist.setdefault("names", {})
        existing = series_of(hist, ticker)
        combined = {d: s for d, s in existing}
        for d, s in series:
            if prefer_existing and d in combined:
                continue
            combined[d] = s
        ordered = sorted(combined.items())[-MAX_SERIES:]
        if not ordered:
            continue
        day, score = ordered[-1]
        rec = names.get(_ticker_key(ticker)) if isinstance(names.get(_ticker_key(ticker)), dict) else {}
        rec = dict(rec or {})
        rec["series"] = [{"date": d.isoformat(), "score": s} for d, s in ordered]
        rec["score"] = score
        rec["source"] = source or rec.get("source")
        rec["asof"] = day.isoformat()
        names[_ticker_key(ticker)] = rec
        hist["asof"] = day.isoformat()
        hist["threshold"] = threshold
    return hist


# ---------------------------------------------------------------------------
# Card attach + HTML embed
# ---------------------------------------------------------------------------


def compute_for_ticker(
    ticker: str,
    item: Mapping[str, Any] | None = None,
    hist: Mapping[str, Any] | None = None,
    series_by_ticker: Mapping[str, list[tuple[date, float]]] | None = None,
    *,
    asof: date | None = None,
    threshold: float = THRESHOLD,
) -> dict[str, Any]:
    key = _ticker_key(ticker) or ticker
    score, src = resolve_card_score(item)
    series = []
    if series_by_ticker:
        series = list(
            series_by_ticker.get(key)
            or series_by_ticker.get(ticker)
            or series_by_ticker.get(_short(ticker))
            or []
        )
        if not series:
            short = _short(ticker)
            for tk, rows in series_by_ticker.items():
                if _short(tk) == short or _ticker_key(tk) == key:
                    series = list(rows)
                    break
    if not series:
        series = series_of(hist, ticker)
    if score is None and series:
        score = series[-1][1]
        src = src or "hist"
    day = asof or (series[-1][0] if series else _today())
    if score is not None:
        series = _upsert_point(series, day, float(score))
        if src is None:
            src = "prices.trend_windows"
    scores = [s for _, s in series]
    dates = [d for d, _ in series]
    streak, side = streak_vs_threshold(scores, threshold, dates=dates)
    label = tag_label(streak, side, threshold)
    span = streak_span(series, threshold)
    d10 = score_change_d10(series, score)
    out: dict[str, Any] = {
        "ticker": key,
        "mom_score": score,
        "mom_score_source": src,
        "mom_streak": streak,
        "mom_streak_side": side,
        "mom_streak_label": label,
        "mom_streak_threshold": threshold,
        "mom_streak_start": None if not span else span.get("start"),
        "mom_streak_end": None if not span else span.get("end"),
        "mom_streak_open": None if not span else span.get("open"),
        "asof": day.isoformat() if isinstance(day, date) else str(day),
        "pill": pill_for(streak, side, score, threshold),
        "series": [{"date": d.isoformat(), "score": s} for d, s in series],
    }
    if d10:
        out.update(d10)
    return out


def attach_card(
    card: MutableMapping[str, Any],
    hist: Mapping[str, Any] | None = None,
    series_by_ticker: Mapping[str, list[tuple[date, float]]] | None = None,
    *,
    asof: date | None = None,
    threshold: float = THRESHOLD,
    root: Path | None = None,
) -> MutableMapping[str, Any]:
    ticker = card_ticker(card)
    if ticker and not str(card.get("ticker") or "").strip():
        card["ticker"] = ticker
    if hist is None and series_by_ticker is None:
        hist = load_hist(root=root)
    rec = compute_for_ticker(ticker, card, hist, series_by_ticker, asof=asof, threshold=threshold)
    card["mom_score"] = rec["mom_score"]
    card["mom_score_source"] = rec["mom_score_source"]
    card["mom_streak"] = rec["mom_streak"]
    card["mom_streak_side"] = rec["mom_streak_side"]
    card["mom_streak_label"] = rec["mom_streak_label"]
    card["mom_streak_threshold"] = rec["mom_streak_threshold"]
    card["mom_streak_start"] = rec.get("mom_streak_start")
    card["mom_streak_end"] = rec.get("mom_streak_end")
    card["mom_streak_open"] = rec.get("mom_streak_open")
    for key in D10_CARD_KEYS:
        if rec.get(key) is not None:
            card[key] = rec[key]
        else:
            card.pop(key, None)
    if rec.get("series"):
        card["mom_score_series"] = rec["series"]
    pill = rec.get("pill")
    pills = card.get("enrich_pills")
    if not isinstance(pills, list):
        pills = []
        card["enrich_pills"] = pills
    pills = [
        p
        for p in pills
        if not (isinstance(p, Mapping) and p.get("key") in (PILL_KEY, D10_PILL_KEY))
    ]
    if pill:
        pills.append(pill)
    card["enrich_pills"] = pills
    return card


def attach_all(
    cards: Iterable[MutableMapping[str, Any]],
    hist: Mapping[str, Any] | None = None,
    series_by_ticker: Mapping[str, list[tuple[date, float]]] | None = None,
    *,
    asof: date | None = None,
    root: Path | None = None,
) -> list[MutableMapping[str, Any]]:
    cards_list = list(cards)
    if hist is None and series_by_ticker is None:
        hist = load_hist(root=root)
    return [attach_card(c, hist, series_by_ticker, asof=asof, root=root) for c in cards_list]


def streak_db(
    cards: Iterable[Mapping[str, Any]] | None,
    hist: Mapping[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """JSON map for ``#mom-streak-db``. Always prefer attached card fields."""
    out: dict[str, dict[str, Any]] = {}
    for card in cards or []:
        rec = _streak_db_rec(card)
        if rec is None:
            continue
        ticker = rec.pop("_ticker")
        out[ticker] = rec
        out.setdefault(_short(ticker), rec)
    if not out and hist:
        out.update(streak_db_from_hist(hist))
    return out


def _streak_db_rec(item: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(item, Mapping):
        return None
    ticker = card_ticker(item)
    side = item.get("mom_streak_side") or item.get("side")
    try:
        streak = int(item.get("mom_streak") if item.get("mom_streak") is not None else item.get("streak") or 0)
    except (TypeError, ValueError):
        streak = 0
    score = item.get("mom_score") if item.get("mom_score") is not None else item.get("score")
    label = item.get("mom_streak_label") or item.get("label") or tag_label(streak, side)
    if not ticker or not label:
        return None
    start = item.get("mom_streak_start") or item.get("start")
    end = item.get("mom_streak_end") or item.get("end")
    open_flag = item.get("mom_streak_open") if "mom_streak_open" in item else item.get("open")
    out: dict[str, Any] = {
        "_ticker": ticker,
        "label": label,
        "cls": tag_cls(str(side or "")),
        "streak": streak,
        "side": side,
        "score": score,
        "start": start,
        "end": end,
        "open": open_flag,
        "title": tag_title(streak, side, dapi_enrich.as_float(score)),
    }
    d10_label_txt = item.get("mom_score_d10_short") or item.get("mom_score_d10_label")
    if d10_label_txt or item.get("mom_score_d10") is not None:
        delta = dapi_enrich.as_float(item.get("mom_score_d10"))
        prior = dapi_enrich.as_float(item.get("mom_score_d10_prior"))
        when = item.get("mom_score_d10_date")
        cur = dapi_enrich.as_float(score)
        shown = d10_short(delta) if delta is not None else str(d10_label_txt or "").removeprefix("10d").strip()
        if shown:
            out["mom_score_d10_label"] = shown
            out["mom_score_d10_short"] = shown
        if delta is not None:
            out["mom_score_d10"] = _whole(delta)
            out["d10_cls"] = d10_cls(delta)
            out["d10_side"] = d10_side(delta)
        if prior is not None:
            out["mom_score_d10_prior"] = _whole(prior)
        if when:
            out["mom_score_d10_date"] = str(when)[:10]
        if delta is not None and prior is not None and when and cur is not None:
            out["d10_title"] = d10_title(cur, prior, str(when), delta)
    return out


def streak_db_from_hist(hist: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    names = hist.get("names") if isinstance(hist, Mapping) and isinstance(hist.get("names"), dict) else {}
    for ticker, rec in names.items():
        if not isinstance(rec, Mapping):
            continue
        series = series_of(hist, ticker)
        payload: dict[str, Any] = {
            "ticker": ticker,
            "name": ticker,
            "mom_score": rec.get("score"),
            "mom_streak": rec.get("streak"),
            "mom_streak_side": rec.get("side"),
            "mom_streak_label": rec.get("label"),
            "mom_streak_start": rec.get("start"),
            "mom_streak_end": rec.get("end"),
            "mom_streak_open": rec.get("open"),
        }
        if series and payload.get("mom_streak") is None:
            n, side = streak_from_dated(series)
            span = streak_span(series)
            payload["mom_streak"] = n
            payload["mom_streak_side"] = side
            payload["mom_streak_label"] = rec.get("label") or tag_label(n, side)
            payload["mom_score"] = series[-1][1]
            if span:
                payload["mom_streak_start"] = span.get("start")
                payload["mom_streak_end"] = span.get("end")
                payload["mom_streak_open"] = span.get("open")
        if series and payload.get("mom_score_d10_label") is None:
            current = dapi_enrich.as_float(payload.get("mom_score"))
            if current is None:
                current = series[-1][1]
            d10 = score_change_d10(series, current)
            if d10:
                payload.update(d10)
        db_rec = _streak_db_rec(payload)
        if db_rec is None:
            continue
        key = db_rec.pop("_ticker")
        out[key] = db_rec
        out.setdefault(_short(key), db_rec)
    return out


def _script_json(blob: str) -> str:
    """Put JSON in a ``<script>`` tag without HTML-escaping ``>`` / ``<``.

    ``type="application/json"`` does not decode entities, so ``html.escape``
    would leave literal ``&gt;`` in labels like ``↑12d>5``. Only neutralize
    ``</`` so a value cannot close the script element.
    """
    return (blob or "").replace("</", "<\\/")


def embed_db(mapping: Mapping[str, Any] | None) -> str:
    blob = json.dumps(dict(mapping or {}), separators=(",", ":"), ensure_ascii=True)
    return f'<script type="application/json" id="{DB_SCRIPT_ID}">{_script_json(blob)}</script>'


def d10_css() -> str:
    return """
.mom-score-d10-near { display: block; margin-top: 1px; text-align: right; font: 600 10px/1.15 ui-monospace, "Cascadia Mono", "Segoe UI Mono", Menlo, Consolas, monospace; letter-spacing: 0.01em; }
.score .mom-score-d10-near, .sc .mom-score-d10-near { display: block; font-size: 10px !important; font-weight: 600 !important; line-height: 1.15 !important; font-family: ui-monospace, "Cascadia Mono", "Segoe UI Mono", Menlo, Consolas, monospace !important; }
.mom-score-d10-near.up { color: #6ee7b7; }
.mom-score-d10-near.down { color: #fda4af; }
.mom-score-d10-near.flat { color: #9ca3af; }
""".strip()


def streak_css() -> str:
    return (
        """
.badge.mom-streak-up, .spike-chip.mom-streak-up { color: #6ee7b7; border-color: #34d399; }
.badge.mom-streak-down, .spike-chip.mom-streak-down { color: #fda4af; border-color: #fb7185; }
.badge.mom-streak-at, .spike-chip.mom-streak-at { color: #fde68a; border-color: #a3a3a3; }
""".strip()
        + "\n"
        + d10_css()
    )


def strip_js() -> str:
    """Append streak pills onto cards. Does not touch GICS / nav / Refresh."""
    return r"""
(function () {
  var el = document.getElementById("mom-streak-db");
  if (!el) return;
  var db = {};
  try { db = JSON.parse(el.textContent || "{}") || {}; }
  catch (e) { return; }

  function tickerOf(node) {
    return (node.getAttribute("data-t") || node.getAttribute("data-ticker") || node.getAttribute("data-name") || "").trim();
  }
  function recOf(t) {
    if (!t) return null;
    if (db[t]) return db[t];
    var keys = Object.keys(db);
    var short = t.split(/\s+/)[0];
    for (var i = 0; i < keys.length; i++) {
      var k = keys[i];
      if (k === t) return db[k];
      if (k.indexOf(t) === 0 || t.indexOf(k) === 0) return db[k];
      if (k.split(/\s+/)[0] === short) return db[k];
    }
    return null;
  }
  function d10Text(rec) {
    if (!rec) return "";
    if (rec.mom_score_d10_short) return String(rec.mom_score_d10_short);
    var n = Number(rec.mom_score_d10);
    if (isFinite(n)) {
      if (n > 0) return "+" + n;
      if (n < 0) return "\u2212" + String(n).replace(/^-/, "");
      return "0";
    }
    return String(rec.mom_score_d10_label || "").replace(/^10d\s+/, "");
  }
  function dropD10Pills(node) {
    var old = node.querySelectorAll('[data-key="mom-score-d10"]');
    for (var i = old.length - 1; i >= 0; i--) {
      if (old[i].getAttribute("data-key") !== "mom-score-d10") continue;
      if (old[i].parentNode) old[i].parentNode.removeChild(old[i]);
    }
  }
  function paintD10(node, rec) {
    dropD10Pills(node);
    var text = d10Text(rec);
    if (!text) return;
    var title = rec.d10_title || "";
    var side = rec.d10_side || (Number(rec.mom_score_d10) > 0 ? "up" : (Number(rec.mom_score_d10) < 0 ? "down" : "flat"));
    var cap = node.querySelector("[data-key='mom-score-d10-near']");
    if (!cap) {
      var scoreEl = node.querySelector(".score, .sc");
      if (!scoreEl) return;
      cap = document.createElement("span");
      cap.setAttribute("data-key", "mom-score-d10-near");
      scoreEl.appendChild(cap);
    }
    cap.className = "mom-score-d10-near " + side;
    if (title) cap.title = title;
    if (rec.mom_score_d10 != null && rec.mom_score_d10 !== "") cap.setAttribute("data-mom-score-d10", String(rec.mom_score_d10));
    cap.textContent = text;
  }
  function apply() {
    var nodes = document.querySelectorAll("[data-t], [data-ticker], article.card, .card");
    for (var i = 0; i < nodes.length; i++) {
      var node = nodes[i];
      if (node.closest && node.closest("nav, .topnav, #gics-filter-strip, #refresh, #options-refresh, #sidecar-progress")) continue;
      var rec = recOf(tickerOf(node));
      if (!rec || !rec.label) continue;
      if (!node.querySelector('[data-key="mom-streak"]')) {
        var host = node.querySelector(".pills, .chips, .badges") || node;
        var span = document.createElement("span");
        span.className = "badge spike-chip " + (rec.cls || "mom-streak");
        span.setAttribute("data-key", "mom-streak");
        span.title = rec.title || "momentum score streak vs 5";
        span.textContent = rec.label;
        host.appendChild(span);
      }
      paintD10(node, rec);
    }
  }
  apply();
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", apply);
})();
""".strip()


def _inject_css(text: str, css: str) -> str:
    if "</style>" in text:
        idx = text.rfind("</style>")
        return text[:idx] + css + "\n" + text[idx:]
    if "</head>" in text:
        return text.replace("</head>", f"<style>\n{css}\n</style>\n</head>", 1)
    return f"<style>{css}</style>\n" + text


_SCRIPT_RE = re.compile(r"<script\b[^>]*>.*?</script>", re.I | re.S)


def _streak_script_tag() -> str:
    return f'<script id="{JS_SCRIPT_ID}">\n{strip_js()}\n</script>\n'


def _replace_streak_script(text: str) -> str:
    """Refresh the streak painter so a prior embed picks up the 10d chip."""
    script = _streak_script_tag()
    matches = list(_SCRIPT_RE.finditer(text))
    for match in matches:
        body = match.group(0)
        if re.search(rf"""\bid=["']{JS_SCRIPT_ID}["']""", body, re.I):
            return text[: match.start()] + script + text[match.end() :]
    for match in matches:
        body = match.group(0)
        if re.search(r"""type=["']application/json["']""", body, re.I):
            continue
        if "momentum score streak vs 5" in body and "mom-streak-db" in body:
            return text[: match.start()] + script + text[match.end() :]
    if "</body>" in text:
        return text.replace("</body>", script + "</body>", 1)
    return text + script


def ensure_embedded(html_text: str, mapping: Mapping[str, Any] | None) -> str:
    """Always leave a filled ``#mom-streak-db`` + tag JS in the HTML."""
    text = html_text or ""
    tag = embed_db(mapping)
    if ".mom-streak-up" not in text:
        text = _inject_css(text, streak_css())
    elif ".mom-score-d10-near" not in text:
        text = _inject_css(text, d10_css())
    if re.search(r'id=["\']mom-streak-db["\']', text, re.I):
        text = re.sub(
            r'<script\b[^>]*\bid=["\']mom-streak-db["\'][^>]*>.*?</script>',
            lambda _m: tag,
            text,
            count=1,
            flags=re.I | re.S,
        )
    elif "</body>" in text:
        text = text.replace("</body>", tag + "\n</body>", 1)
    else:
        text += tag
    return _replace_streak_script(text)


def rebuild_hist_for_cards(
    cards: Iterable[MutableMapping[str, Any]],
    *,
    root: Path | None = None,
    asof: date | None = None,
    write: bool = True,
) -> dict[str, Any]:
    """Attach streaks, persist hist. Called from write_combined / Refresh rebuild.

    Missing or thin ``mom_score_hist.json`` is auto-backfilled from
    ``prices_long.csv`` (``trend_window_score`` over ~120 trading days) **before**
    cards are attached, so the first Refresh after deploy is not every name ``1d``.
    Live card ``mom_score`` still wins for *today's* point.
    """
    base = Path(root) if root is not None else HERE
    hist = load_hist(root=base)
    source = str((hist.get("meta") or {}).get("source") or "none") if isinstance(hist.get("meta"), dict) else "none"
    series: dict[str, list[tuple[date, float]]] = {}

    if hist_is_thin(hist):
        filled, bsrc = backfill_from_prices(base)
        if filled:
            merge_into_hist(hist, filled, source=bsrc, prefer_existing=True)
            series = filled
            source = bsrc
            LOG.info("mom_score_hist auto-backfill: %s names from %s", len(filled), bsrc)

    discovered, dsrc = discover_score_series(base, hist=hist)
    if discovered:
        prefer = bool(series)  # keep backfill + any existing today; fill gaps from discover
        merge_into_hist(hist, discovered, source=dsrc if source in ("none", "") else source, prefer_existing=prefer)
        if not series:
            series = discovered
            source = dsrc
        else:
            series = _merge_series_maps(series, discovered)

    day = asof or _today()
    for card in cards:
        attach_card(card, hist, series, asof=day, root=base)
        score = dapi_enrich.as_float(card.get("mom_score"))
        if score is None:
            score, _src = resolve_card_score(card)
        ticker = card_ticker(card)
        if ticker and not str(card.get("ticker") or "").strip():
            card["ticker"] = ticker
        if score is not None and ticker:
            record_to_hist(
                hist,
                ticker,
                score,
                asof=day,
                source=str(card.get("mom_score_source") or source),
            )
    hist.setdefault("meta", {})
    if isinstance(hist["meta"], dict):
        hist["meta"]["source"] = source
        hist["meta"]["windows"] = list(TREND_WINDOWS)
        hist["meta"]["threshold"] = THRESHOLD
        hist["meta"]["backfill"] = "backfill" in str(source)
    if write:
        write_hist(hist, root=base)
    return hist
