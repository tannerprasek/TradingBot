"""Experimental residual S-score (Avellaneda–Lee) on existing SparsePCA residuals.

Layer only — **not** a new PCA engine, **not** a FLAGS/WATCH/MOM rebrand.
Pointers only: no auto-enter, no paper auto-open, no A–L paper Sharpes as KPIs.

Desktop: recopy this file next to live ``desk_dash.py``. Do **not** wholesale
replace live FLAGS/WATCH/MOM chrome. See ``docs/S-SCORE.md`` and DEPLOY-HOOKS §11.
"""

from __future__ import annotations

import csv
import html
import json
import logging
import math
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import dapi_enrich  # noqa: E402
import mom_streak  # noqa: E402

LOG = logging.getLogger("s_score")

# ---------------------------------------------------------------------------
# Experimental defaults (Avellaneda–Lee 2008 literature). Not a proven edge.
# ---------------------------------------------------------------------------

S_OPEN = 1.25
S_CLOSE = 0.75
S_CLOSE_TIGHT = 0.50
WINDOW = 60
MIN_OBS = 20
HALF_LIFE_MAX_DAYS = 30.0
KAPPA_MIN_DAILY = math.log(2.0) / HALF_LIFE_MAX_DAYS  # ≈ 0.0231 → HL ≤ 30d
B_MIN = 0.01
B_MAX = 0.99
RANK_CAP = 40
DT_DAYS = 1.0  # daily OU (κ in trading-day units)

# S-score / κ UI is Experimental-tab only. Never attach pills to MOM / FLAGS / home.

PANEL_FILENAME = "residual_panel.json"
PANEL_KIND = "factor-desk-residual-panel"
PANEL_VERSION = 1
MAX_SERIES = 260

DEFAULTS_LABEL = (
    "experimental defaults (Avellaneda–Lee literature |s|~1.25 open, "
    "close ~0.75, reject slow κ) — not a proven edge"
)

# residual_20d (beta vs SPY) is a different engine — never treat as SparsePCA.
SKIP_RESIDUAL_FIELDS = frozenset({"residual_20d", "resid_20d", "beta_residual"})

RESIDUAL_PANEL_CANDIDATES: tuple[str, ...] = (
    PANEL_FILENAME,
    "residual_panel.csv",
    "v0_residuals.csv",
    "residuals.csv",
    "sparse_pca_residuals.csv",
    "sparsepca_residuals.csv",
    "resid_panel.csv",
    "clean/v0_residuals.csv",
    "clean/residuals.csv",
    "clean/residual_panel.csv",
)
RESIDUAL_LAST_CANDIDATES: tuple[str, ...] = (
    "residual_last.csv",
    "residual_last.json",
    "v0_residual_last.csv",
    "last_residual.csv",
    "v0_residual_last.json",
)
LOADINGS_CANDIDATES: tuple[str, ...] = (
    "sparse_pca_loadings.csv",
    "sparsepca_loadings.csv",
    "pca_loadings.csv",
    "factor_loadings.csv",
    "v0_loadings.csv",
    "loadings.csv",
    "sparse_pca_loadings.json",
    "pca_loadings.json",
    "clean/loadings.csv",
    "clean/v0_loadings.csv",
)
MEMBERSHIP_CANDIDATES: tuple[str, ...] = (
    "sparse_pca_membership.csv",
    "pca_membership.csv",
    "membership.csv",
    "v0_universe.csv",
    "factor_membership.csv",
    "clean/membership.csv",
)
RETURNS_CANDIDATES: tuple[str, ...] = (
    "returns_long.csv",
    "returns.csv",
    "rets.csv",
    "clean/returns_long.csv",
    "clean/returns.csv",
)
LAST_FIELD_KEYS: tuple[str, ...] = (
    "residual_last",
    "resid_last",
    "v0_residual",
    "v0_resid",
    "sparse_resid",
    "sparsepca_resid",
    "resid",
    "residual",
)
LEVEL_COLS = ("x", "cum_resid", "cum_residual", "residual_level", "x_level")
EPS_COLS = ("residual", "resid", "eps", "epsilon", "v0", "residual_v0", "idio", "idiosyncratic")
DATE_COLS = ("date", "asof", "as_of", "day", "dt")
TICKER_COLS = ("ticker", "name", "symbol", "yellow", "bbg", "t")
RET_COLS = ("ret", "return", "r", "simple_ret", "log_ret")

DB_SCRIPT_ID = "fd-sscore-db"
JS_SCRIPT_ID = "fd-sscore-js"
CSS_STYLE_ID = "fd-sscore-css"
VIEW_ID = "view-experimental"
GRID_ID = "sscore-grid"
NAV_ID = "fd-nav-experimental"
HID_CLASS = "fd-ss-hid"
PILL_KEY = "s_score"

BTN_EXPERIMENTAL = (
    f'<button type="button" class="btn nav-btn" id="{NAV_ID}" '
    'data-view="experimental" data-fd-sscore="1">Experimental</button>'
)
SETVIEW_MARKER = "/*fd-ss-setview*/"
HIDEALL_MARKER = "/*fd-ss-hideall*/"
PAINTVIEW_MARKER = "/*fd-ss-paintview*/"

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
    VIEW_ID,
)

_ALLOWLIST_RE = re.compile(
    r"home\|mom-up\|mom-down\|outliers\|options"
    r"(?:\|sectors)?(?:\|breakout\|breakdown)?(?!\|experimental)",
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
    r"""(?:\s*,\s*["']view-breakout["'])?(?:\s*,\s*["']view-breakdown["'])?))"""
    r"""(?![^\]]*(?:view-experimental))(\s*\])""",
    re.I,
)
_VIEW_SEL_RE = re.compile(
    r"(#home\s*,\s*#view-mom-up\s*,\s*#view-mom-down\s*,\s*#view-outliers\s*,\s*"
    r"#view-options(?:\s*,\s*#view-sectors)?(?:\s*,\s*#search-pane)?"
    r"(?:\s*,\s*#view-breakout)?(?:\s*,\s*#view-breakdown)?)"
    r"(?![^\"';)]*(?:#view-experimental))",
    re.I,
)
_DIV_TOKEN_RE = re.compile(r"<\s*(/)?\s*div\b([^>]*)>", re.I)
_OPTIONS_VIEW_BTN_RE = re.compile(
    r'(<button\b(?=[^>]*data-view=["\']options["\'])[^>]*>\s*Options\s*</button>)',
    re.I | re.S,
)
_BREAKDOWN_BTN_RE = re.compile(
    r'(<button\b(?=[^>]*(?:data-view=["\']breakdown["\']|>\s*Breakdown))[^>]*>\s*Breakdown\s*</button>)',
    re.I | re.S,
)


def _short(ticker: str) -> str:
    parts = (ticker or "").split()
    return parts[0].upper() if parts else ""


def _ticker_key(ticker: str) -> str:
    return dapi_enrich.name_key(ticker) if ticker else ""


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


def experimental_defaults() -> dict[str, Any]:
    return {
        "s_open": S_OPEN,
        "s_close": S_CLOSE,
        "s_close_tight": S_CLOSE_TIGHT,
        "kappa_min_daily": round(KAPPA_MIN_DAILY, 6),
        "half_life_max_days": HALF_LIFE_MAX_DAYS,
        "window": WINDOW,
        "min_obs": MIN_OBS,
        "label": DEFAULTS_LABEL,
    }


# ---------------------------------------------------------------------------
# Math — OU / AR(1) on the windowed cumulative residual
# ---------------------------------------------------------------------------


def ols_intercept_slope(x: Sequence[float], y: Sequence[float]) -> tuple[float, float] | None:
    """OLS ``y = a + b x``. ``None`` if variance of ``x`` is ~0 or lengths differ."""
    n = min(len(x), len(y))
    if n < 3:
        return None
    xs = [float(x[i]) for i in range(n)]
    ys = [float(y[i]) for i in range(n)]
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    var_x = sum((v - mean_x) ** 2 for v in xs)
    if var_x <= 1e-18:
        return None
    cov = sum((xs[i] - mean_x) * (ys[i] - mean_y) for i in range(n))
    b = cov / var_x
    a = mean_y - b * mean_x
    if not math.isfinite(a) or not math.isfinite(b):
        return None
    return a, b


def _sample_std(values: Sequence[float]) -> float | None:
    n = len(values)
    if n < 2:
        return None
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / (n - 1)
    if var < 0:
        return None
    return math.sqrt(var)


def cumsum(values: Sequence[float]) -> list[float]:
    out: list[float] = []
    acc = 0.0
    for v in values:
        acc += float(v)
        out.append(acc)
    return out


def fit_ou(levels: Sequence[float], *, dt: float = DT_DAYS) -> dict[str, Any] | None:
    """AR(1) / OU fit on residual **levels** ``X``.

    ``X_{n+1} = a + b X_n + ζ`` with ``b = exp(-κ Δt)``, ``m = a/(1-b)``,
    ``σ_eq = std(ζ) / sqrt(1-b²)``, ``s = (X_last - m) / σ_eq``.
    """
    x = [float(v) for v in levels if v is not None and math.isfinite(float(v))]
    if len(x) < MIN_OBS:
        return None
    lagged, nxt = x[:-1], x[1:]
    fitted = ols_intercept_slope(lagged, nxt)
    if fitted is None:
        return None
    a, b = fitted
    zeta = [nxt[i] - (a + b * lagged[i]) for i in range(len(lagged))]
    sigma_zeta = _sample_std(zeta)
    if sigma_zeta is None or sigma_zeta <= 1e-18:
        return None
    if not (B_MIN < b < B_MAX):
        return {
            "ok": False,
            "reason": "not_mean_reverting",
            "a": a,
            "b": b,
            "n_obs": len(x),
        }
    denom = 1.0 - b
    if abs(denom) <= 1e-12:
        return None
    kappa = -math.log(b) / dt
    if not math.isfinite(kappa) or kappa <= 0:
        return {
            "ok": False,
            "reason": "kappa_nonpositive",
            "a": a,
            "b": b,
            "n_obs": len(x),
        }
    one_b2 = 1.0 - b * b
    if one_b2 <= 1e-12:
        return None
    m = a / denom
    sigma_eq = sigma_zeta / math.sqrt(one_b2)
    if not math.isfinite(sigma_eq) or sigma_eq <= 1e-18:
        return None
    x_last = x[-1]
    s = (x_last - m) / sigma_eq
    half_life = math.log(2.0) / kappa
    return {
        "ok": True,
        "a": a,
        "b": b,
        "kappa": kappa,
        "half_life": half_life,
        "m": m,
        "sigma_eq": sigma_eq,
        "sigma_zeta": sigma_zeta,
        "x": x_last,
        "s_score": s,
        "n_obs": len(x),
        "reason": None,
    }


def score_residuals(
    residuals: Sequence[float],
    *,
    already_levels: bool = False,
    window: int = WINDOW,
) -> dict[str, Any] | None:
    """S-score + κ from a residual **return** series (cumsum inside the window)."""
    vals = [float(v) for v in residuals if v is not None and math.isfinite(float(v))]
    if len(vals) < MIN_OBS:
        return None
    tail = vals[-int(window) :]
    levels = list(tail) if already_levels else cumsum(tail)
    return fit_ou(levels)


def _band_of(s: float, kappa: float, half_life: float) -> str:
    if kappa < KAPPA_MIN_DAILY or half_life > HALF_LIFE_MAX_DAYS:
        return "reject_slow_kappa"
    a = abs(s)
    if a >= S_OPEN:
        return "open"
    if a <= S_CLOSE:
        return "close"
    return "inside"


def _side_hint(s: float, band: str) -> str:
    if band == "reject_slow_kappa":
        return "reject slow κ"
    if s >= S_OPEN:
        return "fade residual high"
    if s <= -S_OPEN:
        return "buy residual low"
    if abs(s) <= S_CLOSE:
        return "OU close toward mean"
    if s > 0:
        return "inside · residual high"
    return "inside · residual low"


def _why(row: Mapping[str, Any]) -> str:
    s = row.get("s_score")
    hl = row.get("half_life")
    kap = row.get("kappa")
    band = str(row.get("band") or "")
    s_txt = f"{s:+.2f}" if isinstance(s, (int, float)) else "—"
    hl_txt = f"{hl:.1f}d" if isinstance(hl, (int, float)) else "—"
    k_txt = f"{kap:.3f}" if isinstance(kap, (int, float)) else "—"
    if band == "open":
        return f"|s| {s_txt} ≥ {S_OPEN:g} open · κ {k_txt} (HL {hl_txt})"
    if band == "close":
        return f"|s| {s_txt} ≤ {S_CLOSE:g} close · κ {k_txt} (HL {hl_txt})"
    if band == "reject_slow_kappa":
        return f"reject slow κ · HL {hl_txt} > {HALF_LIFE_MAX_DAYS:g}d"
    return f"|s| {s_txt} inside bands · κ {k_txt} (HL {hl_txt})"


def row_from_fit(
    ticker: str,
    fit: Mapping[str, Any] | None,
    *,
    source: str = "",
) -> dict[str, Any] | None:
    if not fit or not fit.get("ok"):
        return None
    s = dapi_enrich.as_float(fit.get("s_score"))
    kappa = dapi_enrich.as_float(fit.get("kappa"))
    half_life = dapi_enrich.as_float(fit.get("half_life"))
    if s is None or kappa is None or half_life is None:
        return None
    band = _band_of(s, kappa, half_life)
    row: dict[str, Any] = {
        "t": _short(ticker),
        "ticker": ticker,
        "s_score": s,
        "kappa": kappa,
        "half_life": half_life,
        "m": fit.get("m"),
        "sigma_eq": fit.get("sigma_eq"),
        "x": fit.get("x"),
        "b": fit.get("b"),
        "n_obs": fit.get("n_obs"),
        "band": band,
        "side_hint": _side_hint(s, band),
        "entry": S_OPEN,
        "exit": S_CLOSE,
        "source": source,
        "experimental": True,
    }
    row["why"] = _why(row)
    return row


# ---------------------------------------------------------------------------
# Disk: residual panel / last / loadings / returns
# ---------------------------------------------------------------------------


def default_panel_path(root: Path | None = None) -> Path:
    base = Path(root) if root is not None else HERE
    return base / PANEL_FILENAME


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


def _header_lower(rows: Sequence[Mapping[str, str]]) -> set[str]:
    if not rows:
        return set()
    return {k.lower() for k in rows[0]}


def empty_panel(
    *,
    reason: str = "no residual panel",
    source: str = "",
    pit_note: str = "",
    look_ahead_risk: str = "unknown",
) -> dict[str, Any]:
    return {
        "kind": PANEL_KIND,
        "version": PANEL_VERSION,
        "asof": None,
        "source": source or "none",
        "pit": False,
        "pit_note": pit_note
        or "PIT is not claimed — confirm whether Desktop SparsePCA is rolling or full-sample.",
        "look_ahead_risk": look_ahead_risk,
        "panel_depth": 0,
        "panel_names": 0,
        "already_levels": False,
        "names": {},
        "empty_reason": reason,
        "defaults": experimental_defaults(),
        "experimental": True,
    }


def _series_map(names: Mapping[str, Any] | None) -> dict[str, list[tuple[date, float]]]:
    out: dict[str, list[tuple[date, float]]] = {}
    if not isinstance(names, Mapping):
        return out
    for raw_key, rec in names.items():
        ticker = str(raw_key or "").strip()
        series: list[tuple[date, float]] = []
        rows: Any = rec
        if isinstance(rec, Mapping):
            rows = rec.get("series") or rec.get("residuals") or rec.get("values") or rec.get("history")
        if isinstance(rows, list):
            for item in rows:
                if isinstance(item, Mapping):
                    d = _as_date(item.get("date") or item.get("asof"))
                    v = dapi_enrich.as_float(
                        item.get("residual")
                        if item.get("residual") is not None
                        else item.get("resid")
                        if item.get("resid") is not None
                        else item.get("eps")
                        if item.get("eps") is not None
                        else item.get("value")
                        if item.get("value") is not None
                        else item.get("x")
                    )
                    if d is not None and v is not None:
                        series.append((d, v))
                elif isinstance(item, (list, tuple)) and len(item) >= 2:
                    d = _as_date(item[0])
                    v = dapi_enrich.as_float(item[1])
                    if d is not None and v is not None:
                        series.append((d, v))
        if ticker and series:
            series.sort(key=lambda x: x[0])
            out[_ticker_key(ticker) or ticker] = series[-MAX_SERIES:]
    return out


def panel_from_series(
    series_by_ticker: Mapping[str, list[tuple[date, float]]],
    *,
    source: str,
    already_levels: bool = False,
    pit: bool = False,
    pit_note: str = "",
    look_ahead_risk: str = "unknown",
    empty_reason: str | None = None,
) -> dict[str, Any]:
    names: dict[str, Any] = {}
    all_dates: set[date] = set()
    for ticker, series in series_by_ticker.items():
        ordered = sorted(series, key=lambda x: x[0])[-MAX_SERIES:]
        if not ordered:
            continue
        names[ticker] = {
            "series": [{"date": d.isoformat(), "residual": v} for d, v in ordered],
        }
        all_dates.update(d for d, _ in ordered)
    depth = 0
    if names:
        lengths = [len((rec.get("series") or [])) for rec in names.values()]
        depth = max(lengths) if lengths else 0
    asof = max(all_dates).isoformat() if all_dates else None
    reason = empty_reason
    if not names:
        reason = reason or "empty residual series"
    elif depth < MIN_OBS:
        reason = reason or (
            f"panel depth {depth} < {MIN_OBS} (need history for κ / S-score; "
            "Refresh will accumulate residual_last)"
        )
    else:
        reason = None
    return {
        "kind": PANEL_KIND,
        "version": PANEL_VERSION,
        "asof": asof,
        "source": source,
        "pit": bool(pit),
        "pit_note": pit_note
        or (
            "PIT is not claimed — confirm whether Desktop SparsePCA membership/loadings "
            "are rolling-window or full-sample."
        ),
        "look_ahead_risk": look_ahead_risk,
        "panel_depth": depth,
        "panel_names": len(names),
        "already_levels": bool(already_levels),
        "names": names,
        "empty_reason": reason,
        "defaults": experimental_defaults(),
        "experimental": True,
    }


def load_residual_csv(path: Path) -> tuple[dict[str, list[tuple[date, float]]], bool]:
    """Long or wide residual table. Returns ``(series, already_levels)``."""
    out: dict[str, list[tuple[date, float]]] = {}
    try:
        rows = _open_table(path)
    except OSError as exc:
        LOG.warning("residual csv %s: %s", path, exc)
        return out, False
    if not rows:
        return out, False
    header = _header_lower(rows)
    already = any(c in header for c in LEVEL_COLS)
    date_key_present = any(c in header for c in DATE_COLS)
    ticker_key_present = any(c in header for c in TICKER_COLS)
    value_cols = [c for c in list(EPS_COLS) + list(LEVEL_COLS) if c in header]

    if date_key_present and ticker_key_present and value_cols:
        for row in rows:
            d = _as_date(_col(row, DATE_COLS))
            ticker = (_col(row, TICKER_COLS) or "").strip()
            v = dapi_enrich.as_float(_col(row, tuple(value_cols)))
            if d is None or not ticker or v is None:
                continue
            key = _ticker_key(ticker)
            out.setdefault(key, []).append((d, v))
    elif date_key_present and not ticker_key_present:
        skip = {c.lower() for c in DATE_COLS}
        for row in rows:
            d = _as_date(_col(row, DATE_COLS))
            if d is None:
                continue
            for col, raw in row.items():
                if col.lower() in skip:
                    continue
                v = dapi_enrich.as_float(raw)
                if v is None:
                    continue
                key = _ticker_key(col)
                out.setdefault(key, []).append((d, v))
    for key, series in list(out.items()):
        series.sort(key=lambda x: x[0])
        out[key] = series[-MAX_SERIES:]
    return out, already


def load_residual_last_file(path: Path) -> tuple[dict[str, float], date | None, str]:
    """Cross-section snapshot ``ticker → residual``. Date may be missing."""
    asof: date | None = None
    out: dict[str, float] = {}
    suffix = path.suffix.lower()
    if suffix == ".json":
        try:
            blob = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            LOG.warning("residual_last json %s: %s", path, exc)
            return out, None, path.name
        asof = _as_date(blob.get("asof") or blob.get("date")) if isinstance(blob, Mapping) else None
        names: Any = blob
        if isinstance(blob, Mapping):
            names = blob.get("names") or blob.get("residuals") or blob.get("last") or blob
        if isinstance(names, Mapping):
            for ticker, rec in names.items():
                if str(ticker).lower() in {"asof", "date", "meta", "kind", "version", "names"}:
                    continue
                if isinstance(rec, Mapping):
                    v = None
                    for key in LAST_FIELD_KEYS:
                        if key in SKIP_RESIDUAL_FIELDS:
                            continue
                        v = dapi_enrich.as_float(rec.get(key))
                        if v is not None:
                            break
                    if v is None:
                        v = dapi_enrich.as_float(rec.get("value"))
                    d = _as_date(rec.get("date") or rec.get("asof"))
                    if d is not None:
                        asof = d if asof is None else max(asof, d)
                else:
                    v = dapi_enrich.as_float(rec)
                if v is None:
                    continue
                key = _ticker_key(str(ticker))
                if key:
                    out[key] = v
        return out, asof, path.name
    try:
        rows = _open_table(path)
    except OSError as exc:
        LOG.warning("residual_last csv %s: %s", path, exc)
        return out, None, path.name
    for row in rows:
        ticker = (_col(row, TICKER_COLS) or "").strip()
        v = dapi_enrich.as_float(
            _col(row, tuple(list(EPS_COLS) + list(LEVEL_COLS) + ["value", "last"]))
        )
        d = _as_date(_col(row, DATE_COLS))
        if d is not None:
            asof = d if asof is None else max(asof, d)
        if not ticker or v is None:
            continue
        out[_ticker_key(ticker)] = v
    return out, asof, path.name


def load_membership(path: Path) -> set[str]:
    members: set[str] = set()
    try:
        rows = _open_table(path)
    except OSError:
        return members
    for row in rows:
        ticker = (_col(row, TICKER_COLS) or "").strip()
        flag = _col(row, ("member", "in", "keep", "flag", "included"))
        if flag is not None:
            text = str(flag).strip().lower()
            if text in {"0", "false", "n", "no", ""}:
                continue
        if ticker:
            members.add(_ticker_key(ticker))
    return members


def load_loadings(path: Path) -> tuple[dict[str, list[float]], dict[str, Any]]:
    """``ticker → loading vector``. Meta flags dated/rolling windows when present."""
    meta: dict[str, Any] = {"path": path.name, "dated": False, "rolling": False}
    suffix = path.suffix.lower()
    loadings: dict[str, list[float]] = {}
    if suffix == ".json":
        try:
            blob = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            LOG.warning("loadings json %s: %s", path, exc)
            return loadings, meta
        if not isinstance(blob, Mapping):
            return loadings, meta
        meta["dated"] = any(k in blob for k in ("asof", "window_end", "fit_end", "window_start"))
        meta["rolling"] = bool(blob.get("rolling") or blob.get("rolling_window"))
        raw = blob.get("loadings") or blob.get("names") or blob
        if isinstance(raw, Mapping):
            for ticker, vec in raw.items():
                if str(ticker).lower() in {"asof", "meta", "factors", "kind", "version", "loadings", "names"}:
                    continue
                if isinstance(vec, Mapping):
                    nums = [dapi_enrich.as_float(v) for k, v in vec.items() if k.lower() not in TICKER_COLS]
                elif isinstance(vec, (list, tuple)):
                    nums = [dapi_enrich.as_float(v) for v in vec]
                else:
                    continue
                vals = [v for v in nums if v is not None]
                if vals:
                    loadings[_ticker_key(str(ticker))] = vals
        return loadings, meta
    try:
        rows = _open_table(path)
    except OSError as exc:
        LOG.warning("loadings csv %s: %s", path, exc)
        return loadings, meta
    if not rows:
        return loadings, meta
    header = _header_lower(rows)
    meta["dated"] = any(c in header for c in ("asof", "window_end", "fit_end", "window_start"))
    meta["rolling"] = "rolling" in header or "window" in header
    long_fmt = "loading" in header or "load" in header
    if long_fmt:
        buckets: dict[str, dict[str, float]] = {}
        for row in rows:
            ticker = (_col(row, TICKER_COLS) or "").strip()
            fac = (_col(row, ("factor", "pc", "component", "name")) or "").strip()
            val = dapi_enrich.as_float(_col(row, ("loading", "load", "value", "weight")))
            if not ticker or val is None:
                continue
            buckets.setdefault(_ticker_key(ticker), {})[fac or str(len(buckets.get(_ticker_key(ticker), {})))] = val
        factors = sorted({f for vec in buckets.values() for f in vec})
        for ticker, vec in buckets.items():
            loadings[ticker] = [float(vec.get(f, 0.0)) for f in factors]
        return loadings, meta
    skip = {c.lower() for c in list(TICKER_COLS) + list(DATE_COLS) + ["rolling", "window"]}
    for row in rows:
        ticker = (_col(row, TICKER_COLS) or "").strip()
        if not ticker:
            continue
        nums: list[float] = []
        for col, raw in row.items():
            if col.lower() in skip:
                continue
            v = dapi_enrich.as_float(raw)
            if v is None:
                nums = []
                break
            nums.append(v)
        if nums:
            loadings[_ticker_key(ticker)] = nums
    return loadings, meta


def load_returns_csv(path: Path) -> dict[str, list[tuple[date, float]]]:
    out: dict[str, list[tuple[date, float]]] = {}
    try:
        rows = _open_table(path)
    except OSError as exc:
        LOG.warning("returns csv %s: %s", path, exc)
        return out
    for row in rows:
        d = _as_date(_col(row, DATE_COLS))
        ticker = (_col(row, TICKER_COLS) or "").strip()
        v = dapi_enrich.as_float(_col(row, RET_COLS))
        if d is None or not ticker or v is None:
            continue
        out.setdefault(_ticker_key(ticker), []).append((d, v))
    for key, series in out.items():
        series.sort(key=lambda x: x[0])
        out[key] = series[-MAX_SERIES:]
    return out


def returns_from_prices(root: Path | None = None) -> tuple[dict[str, list[tuple[date, float]]], str]:
    panel, rel = mom_streak.load_price_panel(root)
    if not panel:
        return {}, ""
    out: dict[str, list[tuple[date, float]]] = {}
    for ticker, rows in panel.items():
        ordered = sorted(rows, key=lambda x: x[0])
        series: list[tuple[date, float]] = []
        for i in range(1, len(ordered)):
            d1, p1 = ordered[i - 1]
            d0, p0 = ordered[i]
            if p1 is None or p0 is None or p1 <= 0:
                continue
            ret = float(p0) / float(p1) - 1.0
            if math.isfinite(ret):
                series.append((d0, ret))
        if series:
            out[_ticker_key(ticker) or ticker] = series[-MAX_SERIES:]
    return out, f"prices.returns:{rel}" if rel else ""


def _solve(matrix: list[list[float]], rhs: list[float]) -> list[float] | None:
    """Gaussian elimination with partial pivoting. ``None`` if singular."""
    n = len(rhs)
    if n == 0 or any(len(row) != n for row in matrix):
        return None
    a = [row[:] + [rhs[i]] for i, row in enumerate(matrix)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(a[r][col]))
        if abs(a[pivot][col]) <= 1e-14:
            return None
        a[col], a[pivot] = a[pivot], a[col]
        div = a[col][col]
        for j in range(col, n + 1):
            a[col][j] /= div
        for r in range(n):
            if r == col:
                continue
            factor = a[r][col]
            if factor == 0:
                continue
            for j in range(col, n + 1):
                a[r][j] -= factor * a[col][j]
    return [a[i][n] for i in range(n)]


def reconstruct_residuals(
    returns: Mapping[str, list[tuple[date, float]]],
    loadings: Mapping[str, list[float]],
    *,
    membership: set[str] | None = None,
) -> dict[str, list[tuple[date, float]]]:
    """ε_t = r_t − L F_t with F_t = (L′L)⁻¹ L′ r_t on each date (intersected names)."""
    if not returns or not loadings:
        return {}
    k_len = 0
    for vec in loadings.values():
        if vec:
            k_len = len(vec)
            break
    if k_len <= 0:
        return {}
    names = [t for t in loadings if t in returns and (not membership or t in membership)]
    names = [t for t in names if len(loadings.get(t) or []) == k_len]
    if len(names) < k_len + 1:
        return {}
    by_date: dict[date, dict[str, float]] = {}
    for ticker in names:
        for d, ret in returns[ticker]:
            by_date.setdefault(d, {})[ticker] = ret
    out: dict[str, list[tuple[date, float]]] = {t: [] for t in names}
    ridge = 1e-8
    for d, cross in sorted(by_date.items()):
        present = [t for t in names if t in cross]
        if len(present) < k_len + 1:
            continue
        l_rows = [loadings[t] for t in present]
        r_vec = [cross[t] for t in present]
        ltl = [[0.0] * k_len for _ in range(k_len)]
        ltr = [0.0] * k_len
        for i, row in enumerate(l_rows):
            for k in range(k_len):
                ltr[k] += row[k] * r_vec[i]
                for j in range(k_len):
                    ltl[k][j] += row[k] * row[j]
        for k in range(k_len):
            ltl[k][k] += ridge
        factors = _solve(ltl, ltr)
        if factors is None:
            continue
        for i, ticker in enumerate(present):
            fitted = sum(l_rows[i][k] * factors[k] for k in range(k_len))
            out[ticker].append((d, r_vec[i] - fitted))
    return {t: series[-MAX_SERIES:] for t, series in out.items() if series}


def _merge_series(
    base: Mapping[str, list[tuple[date, float]]],
    extra: Mapping[str, list[tuple[date, float]]],
) -> dict[str, list[tuple[date, float]]]:
    """Keep existing dates; fill gaps / new names from ``extra``."""
    out: dict[str, list[tuple[date, float]]] = {k: list(v) for k, v in base.items()}
    for ticker, series in extra.items():
        have = {d for d, _ in out.get(ticker, [])}
        merged = list(out.get(ticker, []))
        for d, v in series:
            if d not in have:
                merged.append((d, v))
                have.add(d)
        merged.sort(key=lambda x: x[0])
        out[ticker] = merged[-MAX_SERIES:]
    return out


def _append_last(
    series: Mapping[str, list[tuple[date, float]]],
    last: Mapping[str, float],
    asof: date,
) -> dict[str, list[tuple[date, float]]]:
    out: dict[str, list[tuple[date, float]]] = {k: list(v) for k, v in series.items()}
    for ticker, val in last.items():
        rows = list(out.get(ticker, []))
        rows = [(d, v) for d, v in rows if d != asof]
        rows.append((asof, float(val)))
        rows.sort(key=lambda x: x[0])
        out[ticker] = rows[-MAX_SERIES:]
    return out


def _first_file(root: Path, rels: Sequence[str]) -> Path | None:
    for rel in rels:
        path = root / rel
        if path.is_file():
            return path
    return None


def discover_residual_panel(root: Path | None = None) -> dict[str, Any]:
    """Best on-disk residual **history** (not residual_last alone)."""
    base = Path(root) if root is not None else HERE
    for rel in RESIDUAL_PANEL_CANDIDATES:
        path = base / rel
        if not path.is_file():
            continue
        if path.suffix.lower() == ".json":
            try:
                blob = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(blob, Mapping):
                continue
            series = _series_map(blob.get("names") if isinstance(blob.get("names"), Mapping) else blob)
            if not series:
                continue
            already = bool(blob.get("already_levels"))
            return panel_from_series(
                series,
                source=str(blob.get("source") or rel),
                already_levels=already,
                pit=bool(blob.get("pit")),
                pit_note=str(blob.get("pit_note") or ""),
                look_ahead_risk=str(blob.get("look_ahead_risk") or "unknown"),
            )
        series, already = load_residual_csv(path)
        if series:
            return panel_from_series(
                series,
                source=rel,
                already_levels=already,
                pit=False,
                look_ahead_risk="unknown_sparsepca_fit",
            )
    return empty_panel(reason="no residual panel on disk")


def _loadings_and_membership(root: Path) -> tuple[dict[str, list[float]], dict[str, Any], set[str], str]:
    load_path = _first_file(root, LOADINGS_CANDIDATES)
    mem_path = _first_file(root, MEMBERSHIP_CANDIDATES)
    loadings: dict[str, list[float]] = {}
    meta: dict[str, Any] = {}
    src = ""
    if load_path is not None:
        loadings, meta = load_loadings(load_path)
        src = load_path.name
    members: set[str] = set()
    if mem_path is not None:
        members = load_membership(mem_path)
        src = f"{src}+{mem_path.name}" if src else mem_path.name
    return loadings, meta, members, src


def _returns_panel(root: Path) -> tuple[dict[str, list[tuple[date, float]]], str]:
    ret_path = _first_file(root, RETURNS_CANDIDATES)
    if ret_path is not None:
        panel = load_returns_csv(ret_path)
        if panel:
            return panel, ret_path.name
    return returns_from_prices(root)


def _residual_last_snapshot(root: Path) -> tuple[dict[str, float], date | None, str]:
    last_path = _first_file(root, RESIDUAL_LAST_CANDIDATES)
    if last_path is not None:
        return load_residual_last_file(last_path)
    enrich = dapi_enrich.load_enrichment(root / dapi_enrich.ENRICH_FILENAME)
    names = (enrich or {}).get("names") if isinstance(enrich, Mapping) else None
    out: dict[str, float] = {}
    asof = _as_date((enrich or {}).get("asof")) if isinstance(enrich, Mapping) else None
    if isinstance(names, Mapping):
        for ticker, rec in names.items():
            if not isinstance(rec, Mapping):
                continue
            v = None
            for key in LAST_FIELD_KEYS:
                if key in SKIP_RESIDUAL_FIELDS:
                    continue
                v = dapi_enrich.as_float(rec.get(key))
                if v is not None:
                    break
            if v is None:
                continue
            out[_ticker_key(str(ticker))] = v
    return out, asof, "dapi_enrichment.residual_last" if out else ""


def _pit_from_loadings(meta: Mapping[str, Any] | None) -> tuple[bool, str, str]:
    meta = meta or {}
    if meta.get("rolling") or meta.get("dated"):
        return (
            False,
            "Loadings expose a window/asof — still treat as unverified rolling fit, not silent PIT.",
            "rolling_window_unverified",
        )
    if meta.get("path"):
        return (
            False,
            "SparsePCA loadings look full-sample (no window/asof). Reconstructed residuals are not point-in-time.",
            "full_sample_loadings",
        )
    return (
        False,
        "PIT is not claimed — confirm whether Desktop SparsePCA is rolling or full-sample.",
        "unknown_sparsepca_fit",
    )


def materialize_panel(root: Path | None = None, *, write: bool = True) -> dict[str, Any]:
    """Discover residual history; if thin, rebuild from returns + loadings and/or residual_last.

    Always Experimental-only. Missing files → empty panel with an honest reason.
    """
    base = Path(root) if root is not None else HERE
    existing = discover_residual_panel(base)
    series = _series_map(existing.get("names"))
    sources = [str(existing.get("source") or "")] if series else []
    already = bool(existing.get("already_levels"))
    pit = bool(existing.get("pit"))
    pit_note = str(existing.get("pit_note") or "")
    risk = str(existing.get("look_ahead_risk") or "unknown")

    loadings, load_meta, members, load_src = _loadings_and_membership(base)
    returns, ret_src = _returns_panel(base)
    if loadings and returns:
        recon = reconstruct_residuals(returns, loadings, membership=members or None)
        if recon:
            series = _merge_series(series, recon)
            sources.append(f"reconstructed:{ret_src}+{load_src}")
            p2, n2, r2 = _pit_from_loadings(load_meta)
            pit = pit and p2
            pit_note = n2
            risk = r2

    last, last_asof, last_src = _residual_last_snapshot(base)
    if last:
        day = last_asof or _as_date((existing or {}).get("asof")) or _today()
        series = _append_last(series, last, day)
        sources.append(f"residual_last:{last_src}")
        if not series or max(len(v) for v in series.values()) <= 1:
            risk = "residual_last_only"
            pit_note = (
                "Only residual_last (no residual time series). S-score/κ need a panel; "
                "this Refresh appends one day so history can accumulate."
            )

    source = "+".join(s for s in sources if s and s != "none") or "none"
    if not series:
        extra = []
        if not loadings:
            extra.append("no SparsePCA loadings")
        if not returns:
            extra.append("no returns / prices_long.csv")
        if not last:
            extra.append("no residual_last")
        reason = "no residual panel on disk"
        if extra:
            reason = reason + " (" + "; ".join(extra) + ")"
        panel = empty_panel(
            reason=reason,
            source=source,
            pit_note=pit_note,
            look_ahead_risk=risk,
        )
    else:
        panel = panel_from_series(
            series,
            source=source,
            already_levels=already,
            pit=pit,
            pit_note=pit_note,
            look_ahead_risk=risk,
        )
    if write:
        try:
            write_panel(panel, root=base)
        except OSError as exc:
            LOG.warning("could not persist %s: %s", PANEL_FILENAME, exc)
    return panel


def write_panel(panel: Mapping[str, Any], root: Path | None = None) -> Path:
    p = default_panel_path(root)
    tmp = p.with_suffix(p.suffix + ".tmp")
    text = json.dumps(panel, indent=2, default=str)
    tmp.write_text(text + "\n", encoding="utf-8")
    os.replace(tmp, p)
    return p


def load_panel(root: Path | None = None) -> dict[str, Any]:
    p = default_panel_path(root)
    if not p.is_file():
        return discover_residual_panel(root)
    try:
        blob = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return discover_residual_panel(root)
    if not isinstance(blob, Mapping):
        return empty_panel()
    series = _series_map(blob.get("names") if isinstance(blob.get("names"), Mapping) else None)
    return panel_from_series(
        series,
        source=str(blob.get("source") or PANEL_FILENAME),
        already_levels=bool(blob.get("already_levels")),
        pit=bool(blob.get("pit")),
        pit_note=str(blob.get("pit_note") or ""),
        look_ahead_risk=str(blob.get("look_ahead_risk") or "unknown"),
    )


# ---------------------------------------------------------------------------
# Rank
# ---------------------------------------------------------------------------


def rank_panel(panel: Mapping[str, Any] | None) -> dict[str, Any]:
    """Rank names by |s| descending. Empty panel → empty rows + reason. No Sharpes."""
    panel = panel or empty_panel()
    already = bool(panel.get("already_levels"))
    source = str(panel.get("source") or "")
    names = panel.get("names") if isinstance(panel.get("names"), Mapping) else {}
    rows: list[dict[str, Any]] = []
    skipped_short = 0
    skipped_fit = 0
    for ticker, rec in names.items():
        series_raw = rec.get("series") if isinstance(rec, Mapping) else rec
        vals: list[float] = []
        if isinstance(series_raw, list):
            for item in series_raw:
                if isinstance(item, Mapping):
                    v = dapi_enrich.as_float(item.get("residual") if item.get("residual") is not None else item.get("value"))
                    if v is not None:
                        vals.append(v)
                elif isinstance(item, (list, tuple)) and len(item) >= 2:
                    v = dapi_enrich.as_float(item[1])
                    if v is not None:
                        vals.append(v)
        if len(vals) < MIN_OBS:
            skipped_short += 1
            continue
        fit = score_residuals(vals, already_levels=already)
        row = row_from_fit(ticker, fit, source=source)
        if row is None:
            skipped_fit += 1
            continue
        rows.append(row)
    rows.sort(key=lambda r: -abs(float(r.get("s_score") or 0.0)))
    rows = rows[:RANK_CAP]
    for i, row in enumerate(rows, start=1):
        row["rank"] = i
    empty_reason = panel.get("empty_reason")
    if not rows and not empty_reason:
        if skipped_short and not (names and any(True for _ in names)):
            empty_reason = "no residual names"
        elif skipped_short and not rows:
            empty_reason = (
                f"panel too thin for OU fit (need ≥{MIN_OBS} residual prints; "
                f"{skipped_short} names short, {skipped_fit} fit-rejected)"
            )
        else:
            empty_reason = "no names passed the experimental κ / S-score screen"
    depth = int(panel.get("panel_depth") or 0)
    return {
        "kind": "experimental-sscore",
        "experimental": True,
        "asof": panel.get("asof"),
        "source": source,
        "panel_depth": depth,
        "panel_names": int(panel.get("panel_names") or len(names or {})),
        "pit": bool(panel.get("pit")),
        "pit_note": panel.get("pit_note"),
        "look_ahead_risk": panel.get("look_ahead_risk"),
        "defaults": experimental_defaults(),
        "rows": rows,
        "n_open": sum(1 for r in rows if r.get("band") == "open"),
        "n_close": sum(1 for r in rows if r.get("band") == "close"),
        "skipped_short": skipped_short,
        "skipped_fit": skipped_fit,
        "empty_reason": empty_reason,
        "cap": RANK_CAP,
    }


def rank_book(
    cards: Iterable[Mapping[str, Any]] | None = None,
    *,
    root: Path | None = None,
    panel: Mapping[str, Any] | None = None,
    write_panel_file: bool = True,
) -> dict[str, Any]:
    """Refresh entry: materialize panel (Experimental-only) then rank.

    ``cards`` is unused for scoring (SparsePCA residual universe, not MOM rank).
    """
    _ = cards
    base = Path(root) if root is not None else HERE
    if panel is None:
        panel = materialize_panel(base, write=write_panel_file)
    return rank_panel(panel)


def pill_for(row: Mapping[str, Any] | None) -> dict[str, str] | None:
    if not row:
        return None
    s = dapi_enrich.as_float(row.get("s_score"))
    if s is None:
        return None
    band = str(row.get("band") or "")
    cls = "fd-ss-pill"
    if band == "open" and s > 0:
        cls += " fd-ss-high"
    elif band == "open" and s < 0:
        cls += " fd-ss-low"
    elif band == "reject_slow_kappa":
        cls += " fd-ss-slow"
    return {
        "key": PILL_KEY,
        "label": f"s {s:+.2f}",
        "cls": cls,
        "title": str(row.get("why") or DEFAULTS_LABEL),
    }


# ---------------------------------------------------------------------------
# HTML / CSS / JS
# ---------------------------------------------------------------------------


def slim_payload(ranked: Mapping[str, Any] | None) -> dict[str, Any]:
    ranked = ranked or rank_panel(empty_panel())
    rows = []
    for i, row in enumerate(ranked.get("rows") or [], start=1):
        if not isinstance(row, Mapping):
            continue
        rank = row.get("rank")
        rows.append(
            {
                "rank": rank if rank is not None else i,
                "t": row.get("t"),
                "ticker": row.get("ticker"),
                "s_score": row.get("s_score"),
                "kappa": row.get("kappa"),
                "half_life": row.get("half_life"),
                "band": row.get("band"),
                "side_hint": row.get("side_hint"),
                "entry": row.get("entry"),
                "exit": row.get("exit"),
                "n_obs": row.get("n_obs"),
                "why": row.get("why"),
                "experimental": True,
            }
        )
    return {
        "kind": "experimental-sscore",
        "experimental": True,
        "asof": ranked.get("asof"),
        "source": ranked.get("source"),
        "panel_depth": ranked.get("panel_depth"),
        "panel_names": ranked.get("panel_names"),
        "pit": bool(ranked.get("pit")),
        "pit_note": ranked.get("pit_note"),
        "look_ahead_risk": ranked.get("look_ahead_risk"),
        "defaults": ranked.get("defaults") or experimental_defaults(),
        "rows": rows,
        "n_open": ranked.get("n_open", 0),
        "empty_reason": ranked.get("empty_reason"),
        "cap": ranked.get("cap", RANK_CAP),
    }


def _script_json(blob: str) -> str:
    return (blob or "").replace("</", "<\\/")


def embed_db(ranked: Mapping[str, Any] | None) -> str:
    blob = json.dumps(slim_payload(ranked), separators=(",", ":"), ensure_ascii=True, default=str)
    return f'<script type="application/json" id="{DB_SCRIPT_ID}">{_script_json(blob)}</script>'


def _fmt_num(value: Any, digits: int = 2) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return html.escape(str(value))


def card_html(row: Mapping[str, Any]) -> str:
    ticker_raw = str(row.get("ticker") or row.get("t") or "")
    t = html.escape(_short(ticker_raw) or ticker_raw)
    ticker_attr = html.escape(ticker_raw, quote=True)
    s = dapi_enrich.as_float(row.get("s_score"))
    s_txt = f"{s:+.2f}" if s is not None else "—"
    kap = dapi_enrich.as_float(row.get("kappa"))
    hl = dapi_enrich.as_float(row.get("half_life"))
    hl_txt = f"{hl:.1f}d" if hl is not None else "—"
    k_txt = f"{kap:.3f}" if kap is not None else "—"
    hint = html.escape(str(row.get("side_hint") or ""))
    why = html.escape(str(row.get("why") or ""), quote=True)
    band = html.escape(str(row.get("band") or ""))
    pill = pill_for(row) or {"label": f"s {s_txt}", "cls": "fd-ss-pill", "key": PILL_KEY}
    pill_label = html.escape(str(pill.get("label") or f"s {s_txt}"))
    pill_cls = html.escape(str(pill.get("cls") or "fd-ss-pill"))
    entry = _fmt_num(row.get("entry"), 2)
    exit_ = _fmt_num(row.get("exit"), 2)
    n_obs = html.escape(str(row.get("n_obs") or "—"))
    rank = row.get("rank")
    rank_txt = f"{int(rank):02d}" if isinstance(rank, int) else (html.escape(str(rank)) if rank is not None else "")
    rank_html = f'<span class="fd-ss-rank">{rank_txt}</span>' if rank_txt else ""
    return f"""
<article class="card fd-ss-card" data-t="{t}" data-ticker="{ticker_attr}" data-fd-sscore="1" data-band="{band}" title="{why}">
  <header>
    {rank_html}<h2>{t}</h2>
    <div class="pills">
      <span class="badge spike-chip {pill_cls}" data-key="{PILL_KEY}">{pill_label}</span>
      <span class="badge spike-chip fd-ss-exp" data-key="fd-ss-exp">EXP</span>
    </div>
  </header>
  <p class="fd-ss-hint">{hint}</p>
  <dl>
    <div><dt>κ / HL</dt><dd>{html.escape(k_txt)} / {html.escape(hl_txt)}</dd></div>
    <div><dt>open / close</dt><dd>≥{html.escape(entry)} / ≤{html.escape(exit_)}</dd></div>
    <div><dt>n</dt><dd>{n_obs}</dd></div>
    <div><dt>band</dt><dd>{band or "—"}</dd></div>
  </dl>
  <p class="fd-ss-note">pointer only · experimental default · not a trade</p>
</article>
""".strip()


def banner_html(ranked: Mapping[str, Any] | None) -> str:
    ranked = ranked or {}
    depth = ranked.get("panel_depth") if ranked.get("panel_depth") is not None else "—"
    n = ranked.get("panel_names") if ranked.get("panel_names") is not None else "—"
    src = html.escape(str(ranked.get("source") or "none"))
    pit = "PIT claimed" if ranked.get("pit") else "PIT not claimed"
    risk = html.escape(str(ranked.get("look_ahead_risk") or "unknown"))
    note = html.escape(str(ranked.get("pit_note") or ""))
    defaults = (ranked.get("defaults") or experimental_defaults()).get("label") or DEFAULTS_LABEL
    return f"""
<div class="fd-ss-banner" id="fd-ss-banner">
  <p class="fd-ss-kicker">Experimental · residual S-score (Avellaneda–Lee on SparsePCA residuals) · pointers only · not FLAGS / WATCH / MOM</p>
  <p class="fd-ss-meta">panel {html.escape(str(depth))}d × {html.escape(str(n))} names · source {src} · {html.escape(pit)} · look-ahead {risk}</p>
  <p class="fd-ss-meta">{note}</p>
  <p class="fd-ss-meta">{html.escape(str(defaults))}</p>
</div>
""".strip()


def empty_copy(ranked: Mapping[str, Any] | None) -> str:
    reason = (ranked or {}).get("empty_reason") or (
        "No residual panel yet — need v0_residuals.csv / residual_panel.json, "
        "or returns + SparsePCA loadings. residual_last alone cannot fit κ until Refresh accumulates history."
    )
    return f'<p class="fd-ss-empty">{html.escape(str(reason))}</p>'


def panes_html(ranked: Mapping[str, Any] | None = None) -> str:
    rows = list((ranked or {}).get("rows") or [])
    cards: list[str] = []
    for i, row in enumerate(rows, start=1):
        if not isinstance(row, Mapping):
            continue
        if row.get("rank") is None:
            row = dict(row)
            row["rank"] = i
        cards.append(card_html(row))
    body = "\n".join(cards) if cards else empty_copy(ranked)
    return "\n".join(
        [
            f'<div id="{VIEW_ID}" class="view-pane hide" data-view="experimental" hidden>',
            '  <div class="ph">Experimental</div>',
            f"  {banner_html(ranked)}",
            f'  <div class="grid dense fd-ss-grid" id="{GRID_ID}">{body}</div>',
            "</div>",
        ]
    )


def strip_css() -> str:
    return f"""
/* Default OFF — Experimental never paints on FLAGS/WATCH/MOM/home. */
#{VIEW_ID},
#{VIEW_ID}.hide,
#{VIEW_ID}[hidden] {{
  display: none !important;
}}
body[data-fd-ss="1"] #{VIEW_ID}.fd-ss-on:not(.hide):not([hidden]) {{
  display: block !important;
}}
#home.hide {{ display: none !important; }}
.{HID_CLASS} {{ display: none !important; }}
#{VIEW_ID}.fd-ss-on {{
  margin: 0 0 16px;
}}
#{VIEW_ID} .ph {{
  font: 650 13px/1.2 "Segoe UI", "DejaVu Sans", sans-serif;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: #fbbf24;
  margin: 0 0 8px;
}}
.fd-ss-banner {{
  border: 1px solid #92400e;
  background: #1c1408;
  border-radius: 6px;
  padding: 8px 10px;
  margin: 0 0 10px;
}}
.fd-ss-kicker {{
  margin: 0 0 4px;
  color: #fcd34d;
  font: 650 11px/1.35 "Segoe UI", sans-serif;
}}
.fd-ss-meta {{
  margin: 0;
  color: #a8a29e;
  font-size: 11px;
}}
.fd-ss-grid {{
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 12px;
}}
.fd-ss-card {{
  border-color: #78350f;
}}
.fd-ss-card header {{
  display: flex;
  align-items: baseline;
  gap: 8px;
  flex-wrap: wrap;
}}
.fd-ss-card header h2 {{
  margin: 0;
  flex: 1 1 auto;
}}
.fd-ss-card .pills {{
  margin-left: auto;
}}
.fd-ss-rank {{
  display: inline-block;
  min-width: 1.6em;
  margin: 0;
  font: 650 11px/1.2 "Segoe UI", sans-serif;
  color: #a8a29e;
  letter-spacing: 0.04em;
  font-variant-numeric: tabular-nums;
}}
.fd-ss-hint {{
  margin: 6px 0 0;
  font: 650 12px/1.3 "Segoe UI", sans-serif;
  color: #fde68a;
}}
.fd-ss-note {{
  margin: 8px 0 0;
  font-size: 10px;
  color: #78716c;
}}
.fd-ss-empty {{
  grid-column: 1 / -1;
  color: #a8a29e;
  font-size: 12px;
}}
.badge.fd-ss-pill, .spike-chip.fd-ss-pill {{ color: #fcd34d; border-color: #f59e0b; }}
.badge.fd-ss-high, .spike-chip.fd-ss-high {{ color: #fda4af; border-color: #fb7185; }}
.badge.fd-ss-low, .spike-chip.fd-ss-low {{ color: #6ee7b7; border-color: #34d399; }}
.badge.fd-ss-slow, .spike-chip.fd-ss-slow {{ color: #a8a29e; border-color: #78716c; }}
.badge.fd-ss-exp, .spike-chip.fd-ss-exp {{ color: #fbbf24; border-color: #d97706; }}
.nav-btn[data-view="experimental"].is-on,
.nav-btn[data-view="experimental"].on,
.btn.nav-btn[data-view="experimental"].on,
.nav-btn[data-fd-sscore="1"].is-on,
.nav-btn[data-fd-sscore="1"].on {{
  color: #fcd34d; border-color: #f59e0b; background: #451a03;
}}
""".strip()


def strip_js() -> str:
    view_ids = json.dumps(list(NATIVE_VIEW_IDS))
    return rf"""
(function () {{
  if (window.__FD_SS_BOUND__) return;
  window.__FD_SS_BOUND__ = true;
  var DB_ID = "{DB_SCRIPT_ID}";
  var VIEW = "{VIEW_ID}";
  var GRID = "{GRID_ID}";
  var NATIVE_VIEWS = {view_ids};

  function $(id) {{ return document.getElementById(id); }}
  function db() {{
    var el = $(DB_ID);
    if (!el) return {{ rows: [], experimental: true }};
    try {{ return JSON.parse(el.textContent || "{{}}") || {{ rows: [] }}; }}
    catch (e) {{ return {{ rows: [] }}; }}
  }}
  function fmt(v, d) {{
    var n = Number(v);
    if (!isFinite(n)) return "—";
    return n.toFixed(d);
  }}
  function pillCls(row) {{
    var s = Number(row.s_score);
    var band = String(row.band || "");
    if (band === "reject_slow_kappa") return "badge spike-chip fd-ss-pill fd-ss-slow";
    if (band === "open" && s > 0) return "badge spike-chip fd-ss-pill fd-ss-high";
    if (band === "open" && s < 0) return "badge spike-chip fd-ss-pill fd-ss-low";
    return "badge spike-chip fd-ss-pill";
  }}
  function selectFn() {{
    if (typeof window.selectTicker === "function") return window.selectTicker;
    if (typeof selectTicker === "function") return selectTicker;
    return null;
  }}
  function cardNode(row) {{
    row = row || {{}};
    var t = String(row.t || (String(row.ticker || "").split(/\s+/)[0]) || "");
    var s = Number(row.s_score);
    var sTxt = isFinite(s) ? ((s >= 0 ? "+" : "") + s.toFixed(2)) : "—";
    var art = document.createElement("article");
    art.className = "card fd-ss-card";
    art.setAttribute("data-t", t);
    art.setAttribute("data-ticker", String(row.ticker || t));
    art.setAttribute("data-fd-sscore", "1");
    art.setAttribute("data-band", String(row.band || ""));
    if (row.why) art.setAttribute("title", String(row.why));
    var rankTxt = "";
    if (row.rank != null && isFinite(Number(row.rank))) {{
      var n = Number(row.rank);
      rankTxt = (n < 10 ? "0" : "") + String(n);
    }} else if (row.rank != null) {{
      rankTxt = String(row.rank);
    }}
    var rank = rankTxt ? '<span class="fd-ss-rank">' + rankTxt + '</span>' : '';
    art.innerHTML =
      '<header>' + rank + '<h2>' + t.replace(/</g, "") + '</h2><div class="pills">' +
      '<span class="' + pillCls(row) + '" data-key="s_score">s ' + sTxt + '</span>' +
      '<span class="badge spike-chip fd-ss-exp" data-key="fd-ss-exp">EXP</span>' +
      '</div></header>' +
      '<p class="fd-ss-hint">' + String(row.side_hint || "").replace(/</g, "") + '</p>' +
      '<dl><div><dt>κ / HL</dt><dd>' + fmt(row.kappa, 3) + ' / ' +
        (isFinite(Number(row.half_life)) ? Number(row.half_life).toFixed(1) + 'd' : '—') +
      '</dd></div><div><dt>open / close</dt><dd>≥' + fmt(row.entry, 2) +
      ' / ≤' + fmt(row.exit, 2) + '</dd></div>' +
      '<div><dt>n</dt><dd>' + String(row.n_obs == null ? "—" : row.n_obs) + '</dd></div>' +
      '<div><dt>band</dt><dd>' + String(row.band || "—") + '</dd></div></dl>' +
      '<p class="fd-ss-note">pointer only · experimental default · not a trade</p>';
    art.addEventListener("click", function () {{
      var sel = selectFn();
      if (sel) sel(t || row.ticker);
    }});
    return art;
  }}
  function fillGrid(grid, payload) {{
    if (!grid) return;
    grid.innerHTML = "";
    var rows = ((payload && payload.rows) || []).slice();
    rows.sort(function (a, b) {{
      return Math.abs(Number((b && b.s_score) || 0)) - Math.abs(Number((a && a.s_score) || 0));
    }});
    if (!rows.length) {{
      var empty = document.createElement("p");
      empty.className = "fd-ss-empty";
      empty.textContent = (payload && payload.empty_reason) ||
        "No residual panel yet — need v0_residuals.csv / residual_panel.json, or returns + SparsePCA loadings.";
      grid.appendChild(empty);
      return;
    }}
    for (var i = 0; i < rows.length; i++) {{
      var row = rows[i] || {{}};
      if (row.rank == null) row.rank = i + 1;
      grid.appendChild(cardNode(row));
    }}
  }}
  function paintBanner(payload) {{
    var el = $("fd-ss-banner");
    if (!el || !payload) return;
    var pit = payload.pit ? "PIT claimed" : "PIT not claimed";
    el.innerHTML =
      '<p class="fd-ss-kicker">Experimental · residual S-score (Avellaneda–Lee on SparsePCA residuals) · pointers only · not FLAGS / WATCH / MOM</p>' +
      '<p class="fd-ss-meta">panel ' + String(payload.panel_depth == null ? "—" : payload.panel_depth) +
      'd × ' + String(payload.panel_names == null ? "—" : payload.panel_names) +
      ' names · source ' + String(payload.source || "none").replace(/</g, "") +
      ' · ' + pit + ' · look-ahead ' + String(payload.look_ahead_risk || "unknown").replace(/</g, "") + '</p>' +
      '<p class="fd-ss-meta">' + String(payload.pit_note || "").replace(/</g, "") + '</p>' +
      '<p class="fd-ss-meta">' + String((payload.defaults && payload.defaults.label) || "").replace(/</g, "") + '</p>';
  }}
  function hideNativeViews() {{
    for (var i = 0; i < NATIVE_VIEWS.length; i++) {{
      var el = $(NATIVE_VIEWS[i]);
      if (!el) continue;
      if (NATIVE_VIEWS[i] === VIEW) continue;
      el.classList.add("hide");
    }}
  }}
  function setOn(btn, on) {{
    if (!btn) return;
    btn.classList.toggle("is-on", !!on);
    btn.classList.toggle("on", !!on);
    btn.setAttribute("aria-pressed", on ? "true" : "false");
  }}
  function navButtons() {{
    return document.querySelectorAll("#topnav .btn, #topnav .nav-btn, .topnav .nav-btn, nav .btn, nav .nav-btn, [data-view], [data-fd-sscore]");
  }}
  function oursOf(b) {{
    var view = (b.getAttribute("data-view") || "");
    return view === "experimental" || b.getAttribute("data-fd-sscore") === "1" || b.id === "{NAV_ID}";
  }}
  function syncNav(on) {{
    var buttons = navButtons();
    for (var i = 0; i < buttons.length; i++) {{
      var b = buttons[i];
      if (on) setOn(b, oursOf(b));
      else if (oursOf(b)) setOn(b, false);
    }}
  }}
  function show(on) {{
    var pane = $(VIEW);
    if (on) {{
      hideNativeViews();
      if (pane) {{
        pane.classList.remove("hide");
        pane.classList.add("fd-ss-on");
        pane.removeAttribute("hidden");
      }}
      var payload = db();
      paintBanner(payload);
      fillGrid($(GRID), payload);
      document.body.setAttribute("data-view", "experimental");
      document.body.setAttribute("data-fd-ss", "1");
      syncNav(true);
      return;
    }}
    if (pane) {{
      pane.classList.add("hide");
      pane.classList.remove("fd-ss-on");
      pane.setAttribute("hidden", "hidden");
    }}
    var home = $("home");
    if (home) home.classList.remove("hide");
    document.body.removeAttribute("data-fd-ss");
    if (document.body.getAttribute("data-view") === "experimental") {{
      document.body.removeAttribute("data-view");
    }}
    syncNav(false);
  }}
  window.__FD_SS_SHOW__ = function () {{ show(true); }};
  window.__FD_SS_HIDE__ = function () {{ show(false); }};
  function kindOf(btn) {{
    if (!btn || !btn.getAttribute) return "";
    var view = (btn.getAttribute("data-view") || "").toLowerCase();
    if (view === "experimental" || btn.getAttribute("data-fd-sscore") === "1" || btn.id === "{NAV_ID}") return "experimental";
    var label = (btn.textContent || "").replace(/\s+/g, " ").trim();
    if (label === "Experimental" || label === "Exp") return "experimental";
    if (btn.id === "refresh" || btn.id === "options-refresh") return "";
    if (btn.closest && btn.closest("#topnav, nav, .topnav") && (btn.classList.contains("btn") || btn.classList.contains("nav-btn") || view)) return "other";
    if (btn.classList && (btn.classList.contains("nav-btn") || view)) return "other";
    return "";
  }}
  document.addEventListener("click", function (ev) {{
    var t = ev.target && ev.target.closest ? ev.target.closest("button, [data-view], [data-fd-sscore]") : ev.target;
    var kind = kindOf(t);
    if (!kind) return;
    if (kind === "experimental") {{
      ev.preventDefault();
      ev.stopPropagation();
      if (ev.stopImmediatePropagation) ev.stopImmediatePropagation();
      show(true);
      return;
    }}
    if (kind === "other") show(false);
  }}, true);
  function installSetViewBridge() {{
    var orig = window.setView;
    if (typeof orig !== "function" || orig.__fdSs) return;
    window.setView = function (v) {{
      var kind = String(v || "").toLowerCase();
      if (kind === "experimental" || kind === "exp") {{
        show(true);
        return;
      }}
      show(false);
      return orig.apply(this, arguments);
    }};
    window.setView.__fdSs = true;
  }}
  installSetViewBridge();
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", installSetViewBridge);
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


def _has_nav_button(html_text: str) -> bool:
    text = html_text or ""
    patterns = (
        rf'<button\b[^>]*\bdata-view=["\']experimental["\']',
        rf'<button\b[^>]*\bdata-fd-sscore=["\']1["\']',
        rf'<button\b[^>]*\bid=["\']{re.escape(NAV_ID)}["\']',
    )
    return any(re.search(pat, text, re.I | re.S) for pat in patterns)


def _ensure_nav(html_text: str) -> str:
    if _has_nav_button(html_text):
        return html_text
    pair = "\n  " + BTN_EXPERIMENTAL
    m = _OPTIONS_VIEW_BTN_RE.search(html_text)
    if m:
        return html_text[: m.end()] + pair + html_text[m.end() :]
    m2 = _BREAKDOWN_BTN_RE.search(html_text)
    if m2:
        return html_text[: m2.end()] + pair + html_text[m2.end() :]
    if "<nav" in html_text.lower():
        return re.sub(r"(</nav>)", pair + r"\n\1", html_text, count=1, flags=re.I)
    return pair + "\n" + html_text


def _patch_setview_allowlist(html_text: str) -> str:
    text = html_text or ""
    if re.search(
        r"home\|mom-up\|mom-down\|outliers\|options(?:\|sectors)?(?:\|breakout\|breakdown)?\|experimental",
        text,
        re.I,
    ):
        return text
    return _ALLOWLIST_RE.sub(lambda m: m.group(0) + "|experimental", text)


def _early_return_snippet(marker: str, param: str) -> str:
    return (
        f"{marker}"
        f"if({param}===\"experimental\"||{param}===\"exp\"){{"
        f"if(window.__FD_SS_SHOW__)window.__FD_SS_SHOW__();"
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
        re.escape(marker) + r'if\((\w+)==="experimental".*?return;\}',
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
        '["view-experimental"].forEach(function(id){'
        "var el=document.getElementById(id);"
        'if(el){el.classList.add("hide");el.classList.remove("fd-ss-on");'
        'el.setAttribute("hidden","hidden");}'
        'if(document.body)document.body.removeAttribute("data-fd-ss");'
        "});"
    )


def _patch_hideall_panes(html_text: str) -> str:
    text = html_text or ""
    snippet = _hideall_snippet()
    existing = re.search(
        re.escape(HIDEALL_MARKER) + r'\["view-experimental"\]\.forEach\(function\(id\)\{.*?\}\);',
        text,
        re.S,
    )
    if existing:
        return text[: existing.start()] + snippet + text[existing.end() :]
    return _HIDEALL_FN_RE.sub(lambda m: m.group(1) + snippet, text, count=1)


def _patch_view_id_lists(html_text: str) -> str:
    text = html_text or ""
    text = _VIEW_ID_ARRAY_RE.sub(
        r'\1,"view-experimental"\2',
        text,
    )
    text = _VIEW_SEL_RE.sub(
        r"\1, #view-experimental",
        text,
    )
    return text


def _patch_setview(html_text: str) -> str:
    text = _patch_setview_allowlist(html_text)
    text = _replace_or_inject_early_return(html_text=text, marker=SETVIEW_MARKER, fn_re=_SETVIEW_FN_RE)
    text = _replace_or_inject_early_return(html_text=text, marker=PAINTVIEW_MARKER, fn_re=_PAINTVIEW_FN_RE)
    text = _patch_hideall_panes(text)
    return _patch_view_id_lists(text)


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


def _insert_host(html_text: str, host: str) -> str:
    for elem_id in ("view-breakdown", "view-breakout", "view-mom-down", "view-mom-up"):
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


def _ensure_panes(html_text: str, ranked: Mapping[str, Any] | None, *, replace: bool = True) -> str:
    has_view = _find_tag_span(html_text, VIEW_ID) is not None
    if has_view and not replace:
        return html_text
    host = panes_html(ranked)
    span = _find_tag_span(html_text, VIEW_ID)
    if span:
        return html_text[: span[0]] + host + html_text[span[1] :]
    return _insert_host(html_text, host)


def _ensure_db(html_text: str, ranked: Mapping[str, Any] | None) -> str:
    tag = embed_db(ranked)
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


def ensure_embedded(html_text: str, ranked: Mapping[str, Any] | None = None) -> str:
    """Experimental nav + pane + filled db + JS. Safe on live ~2.7MB HTML.

    ``ranked=None`` must not wipe ``#fd-sscore-db``. Does not change home
    FLAGS→WATCH→MOM. S-score / κ UI stays inside ``#view-experimental`` only
    (never pills on live MOM / FLAGS / home cards). Does not wrap ``cardHTML``.
    """
    text = html_text or ""
    text = _ensure_nav(text)
    text = _ensure_css(text)
    text = _patch_setview(text)
    if ranked is not None:
        text = _ensure_panes(text, ranked, replace=True)
        text = _ensure_db(text, ranked)
    else:
        text = _ensure_panes(text, rank_panel(empty_panel()), replace=False)
        if not re.search(rf'id=["\']{DB_SCRIPT_ID}["\']', text, re.I):
            text = _ensure_db(text, rank_panel(empty_panel()))
    text = _ensure_js(text)
    return text
