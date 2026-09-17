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
3. If still missing: **13-horizon trend-window count** from a price series
   already on disk (closes). That fallback is 0–13 = how many of
   ``TREND_WINDOWS`` have a positive total return. It is written into
   ``mom_score_hist.json`` so Refresh/write_dash keeps a trading-day series.

Threshold
---------
Literal **5**. Current regime is the side of today's score:

- ``score > 5`` → above, tag ``↑{n}d>5``
- ``score < 5`` → below, tag ``↓{n}d<5``
- ``score == 5`` → neither, streak **0**, tag ``=5`` (visible at-cut, not missing)
- null score → no tag

``n`` is consecutive **trading days** of history on the same side, including
today. A day at 5, or a missing print, breaks the streak.
"""

from __future__ import annotations

import csv
import html
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
MAX_SERIES = 252
# Fri→Mon is 3 calendar days. A longer hole in the series breaks the streak.
MAX_GAP_DAYS = 4

# 13 lookbacks in trading days → score in 0..13. Used only when the live
# card does not already carry the UP/DOWN rank.
TREND_WINDOWS: tuple[int, ...] = (5, 10, 15, 20, 25, 30, 40, 50, 60, 80, 100, 150, 200)

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
    "prices.csv",
    "px.csv",
    "closes.csv",
    "clean/prices.csv",
    "clean/px.csv",
)
DATE_COLS = ("date", "asof", "as_of", "day", "dt")
TICKER_COLS = ("ticker", "name", "symbol", "yellow", "bbg")
SCORE_COLS = ("mom_score", "momentum_score", "mom_rank", "trend_rank", "score", "rank")
PX_COLS = ("px_last", "px", "close", "adj_close", "px_close", "last", "PX_LAST")
RESIDUAL_COLS = ("residual", "resid", "v0", "residual_v0", "residual_20d")

DB_SCRIPT_ID = "mom-streak-db"

PILL_KEY = "mom-streak"


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


def _ticker_key(ticker: str) -> str:
    return dapi_enrich.name_key(ticker) if ticker else ""


def _short(ticker: str) -> str:
    return (ticker or "").split()[0].upper()


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
        return f"↑{int(streak)}d>{cut}"
    if side == "below":
        return f"↓{int(streak)}d<{cut}"
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


def scores_from_prices(panel: Mapping[str, list[tuple[date, float]]]) -> dict[str, list[tuple[date, float]]]:
    out: dict[str, list[tuple[date, float]]] = {}
    for ticker, rows in panel.items():
        dates = [d for d, _ in rows]
        closes = [p for _, p in rows]
        series: list[tuple[date, float]] = []
        for i in range(len(closes)):
            score = trend_window_score(closes[: i + 1])
            if score is not None:
                series.append((dates[i], float(score)))
        if series:
            out[ticker] = series[-MAX_SERIES:]
    return out


def discover_score_series(root: Path | None = None) -> tuple[dict[str, list[tuple[date, float]]], str]:
    """Load the best on-disk score history. Does not invent tickers."""
    base = Path(root) if root is not None else HERE
    hist = load_hist(root=base)
    merged: dict[str, list[tuple[date, float]]] = {}
    source = "none"

    hist_names = hist.get("names") if isinstance(hist.get("names"), dict) else {}
    if hist_names:
        for ticker in hist_names:
            series = series_of(hist, ticker)
            if series:
                merged[ticker] = series
        if merged:
            source = HIST_FILENAME

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

    if merged:
        return merged, source

    for rel in PRICE_CANDIDATES:
        path = base / rel
        if not path.is_file():
            continue
        panel = load_price_csv(path)
        computed = scores_from_prices(panel)
        if computed:
            return computed, f"prices.trend_windows:{rel}"
    return merged, source


def merge_into_hist(
    hist: MutableMapping[str, Any],
    series_by_ticker: Mapping[str, list[tuple[date, float]]],
    *,
    source: str | None = None,
    threshold: float = THRESHOLD,
) -> MutableMapping[str, Any]:
    for ticker, series in series_by_ticker.items():
        if not series:
            continue
        day, score = series[-1]
        names = hist.setdefault("names", {})
        existing = series_of(hist, ticker)
        combined = {d: s for d, s in existing}
        for d, s in series:
            combined[d] = s
        ordered = sorted(combined.items())[-MAX_SERIES:]
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
    return {
        "ticker": key,
        "mom_score": score,
        "mom_score_source": src,
        "mom_streak": streak,
        "mom_streak_side": side,
        "mom_streak_label": label,
        "mom_streak_threshold": threshold,
        "asof": day.isoformat() if isinstance(day, date) else str(day),
        "pill": pill_for(streak, side, score, threshold),
    }


def attach_card(
    card: MutableMapping[str, Any],
    hist: Mapping[str, Any] | None = None,
    series_by_ticker: Mapping[str, list[tuple[date, float]]] | None = None,
    *,
    asof: date | None = None,
    threshold: float = THRESHOLD,
) -> MutableMapping[str, Any]:
    ticker = str(card.get("ticker") or card.get("name") or "")
    rec = compute_for_ticker(ticker, card, hist, series_by_ticker, asof=asof, threshold=threshold)
    card["mom_score"] = rec["mom_score"]
    card["mom_score_source"] = rec["mom_score_source"]
    card["mom_streak"] = rec["mom_streak"]
    card["mom_streak_side"] = rec["mom_streak_side"]
    card["mom_streak_label"] = rec["mom_streak_label"]
    card["mom_streak_threshold"] = rec["mom_streak_threshold"]
    pill = rec.get("pill")
    pills = card.get("enrich_pills")
    if not isinstance(pills, list):
        pills = []
        card["enrich_pills"] = pills
    pills = [p for p in pills if not (isinstance(p, Mapping) and p.get("key") == PILL_KEY)]
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
) -> list[MutableMapping[str, Any]]:
    return [attach_card(c, hist, series_by_ticker, asof=asof) for c in cards]


def streak_db(cards: Iterable[Mapping[str, Any]] | None) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for card in cards or []:
        if not isinstance(card, Mapping):
            continue
        ticker = str(card.get("ticker") or card.get("name") or "").strip()
        label = card.get("mom_streak_label")
        if not ticker or not label:
            continue
        out[ticker] = {
            "label": label,
            "cls": tag_cls(str(card.get("mom_streak_side") or "")),
            "streak": card.get("mom_streak") or 0,
            "side": card.get("mom_streak_side"),
            "score": card.get("mom_score"),
            "title": tag_title(
                int(card.get("mom_streak") or 0),
                card.get("mom_streak_side"),
                dapi_enrich.as_float(card.get("mom_score")),
            ),
        }
        short = _short(ticker)
        out.setdefault(short, out[ticker])
    return out


def embed_db(mapping: Mapping[str, Any] | None) -> str:
    blob = json.dumps(dict(mapping or {}), separators=(",", ":"), ensure_ascii=True)
    return f'<script type="application/json" id="{DB_SCRIPT_ID}">{html.escape(blob, quote=False)}</script>'


def streak_css() -> str:
    return """
.badge.mom-streak-up, .spike-chip.mom-streak-up { color: #6ee7b7; border-color: #34d399; }
.badge.mom-streak-down, .spike-chip.mom-streak-down { color: #fda4af; border-color: #fb7185; }
.badge.mom-streak-at, .spike-chip.mom-streak-at { color: #fde68a; border-color: #a3a3a3; }
""".strip()


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
  function apply() {
    var nodes = document.querySelectorAll("[data-t], [data-ticker], article.card, .card");
    for (var i = 0; i < nodes.length; i++) {
      var node = nodes[i];
      if (node.closest && node.closest("nav, .topnav, #gics-filter-strip, #refresh, #options-refresh, #sidecar-progress")) continue;
      var rec = recOf(tickerOf(node));
      if (!rec || !rec.label) continue;
      if (node.querySelector('[data-key="mom-streak"]')) continue;
      var host = node.querySelector(".pills, .chips, .badges") || node;
      var span = document.createElement("span");
      span.className = "badge spike-chip " + (rec.cls || "mom-streak");
      span.setAttribute("data-key", "mom-streak");
      span.title = rec.title || "momentum score streak vs 5";
      span.textContent = rec.label;
      host.appendChild(span);
    }
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", apply);
  else apply();
})();
""".strip()


def ensure_embedded(html_text: str, mapping: Mapping[str, Any] | None) -> str:
    """Always leave a filled ``#mom-streak-db`` + tag JS in the HTML."""
    text = html_text or ""
    tag = embed_db(mapping)
    if ".mom-streak-up" not in text:
        css = streak_css()
        if "</style>" in text:
            idx = text.rfind("</style>")
            text = text[:idx] + css + "\n" + text[idx:]
        elif "</head>" in text:
            text = text.replace("</head>", f"<style>\n{css}\n</style>\n</head>", 1)
        else:
            text = f"<style>{css}</style>\n" + text
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
    if 'getElementById("mom-streak-db")' not in text and "getElementById('mom-streak-db')" not in text:
        script = "<script>\n" + strip_js() + "\n</script>\n"
        if "</body>" in text:
            text = text.replace("</body>", script + "</body>", 1)
        else:
            text += script
    return text


def rebuild_hist_for_cards(
    cards: Iterable[MutableMapping[str, Any]],
    *,
    root: Path | None = None,
    asof: date | None = None,
    write: bool = True,
) -> dict[str, Any]:
    """Attach streaks, persist hist. Called from write_combined / Refresh rebuild."""
    base = Path(root) if root is not None else HERE
    hist = load_hist(root=base)
    series, source = discover_score_series(base)
    if series:
        merge_into_hist(hist, series, source=source)
    day = asof or _today()
    for card in cards:
        attach_card(card, hist, series, asof=day)
        score = dapi_enrich.as_float(card.get("mom_score"))
        ticker = str(card.get("ticker") or card.get("name") or "")
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
    if write:
        write_hist(hist, root=base)
    return hist
