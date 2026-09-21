"""Breakout / Breakdown ranking for Factor Desk home tabs.

Breakout = early long inflection (score just into 7–10, strong 10d lift,
fresh above-5 streak, positive idio). Not an already-worked trend.
Breakdown = the mirror. Not an already-dead name.

Tune every threshold in the constants block below. Formula is documented in
``docs/BREAKOUT-BREAKDOWN.md``. No extra DAPI stages. ``mom_score_d10`` comes
from ``mom_streak`` (already on the card when hist is long enough).
"""

from __future__ import annotations

import json
import logging
import re
import sys
from typing import Any, Iterable, Mapping, Sequence

HERE = __import__("pathlib").Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import card_render  # noqa: E402
import dapi_enrich  # noqa: E402
import mom_streak  # noqa: E402

LOG = logging.getLogger("breakout")

# ---------------------------------------------------------------------------
# Thresholds — tune here, then re-Refresh. See docs/BREAKOUT-BREAKDOWN.md.
# ---------------------------------------------------------------------------

# Hard score bands on the 0–13 MOM rank (card ``score`` / ``mom_score``).
# Outside the band is excluded — not a soft falloff.
BREAKOUT_SCORE_MIN = 7.0
BREAKOUT_SCORE_MAX = 10.0  # 11–13 already extended
BREAKDOWN_SCORE_MIN = 2.0  # 0–1 already dead
BREAKDOWN_SCORE_MAX = 6.0

# 10-trading-day composite change. Strict: breakout needs > +3, breakdown < −3.
# Missing ``mom_score_d10`` (series shorter than 11 prints) excludes the name.
D10_BREAKOUT_MIN = 3.0
D10_BREAKDOWN_MAX = -3.0
D10_CLIP = 8.0  # rank weight only

# Streak vs 5. 1 = just crossed today. 2–15 = fresh. N>15 is baked — out.
STREAK_JUST_CROSSED = 1
STREAK_FRESH_MIN = 2
STREAK_FRESH_MAX = 15
STREAK_PEAK_LO = 5
STREAK_PEAK_HI = 12

# Dispersion. Prefer enrich ``residual_20d`` (r_20d − β × r_mkt_20d). If that
# field is absent, interim gate is RS63 > 0 / < 0 (card ``rs_63`` / ``rs63``,
# or 63d return minus SPY when both series exist). Do not invent a residual.
# A present residual of 0 does **not** fall through to RS63.
DISPERSION_KEYS: tuple[str, ...] = (
    "residual_20d",
    "factor_residual",
    "factor_resid",
    "vs_group",
    "vs_sleeve",
)
DISP_CLIP = 0.15  # rank weight only; gate is strictly > 0 or < 0

# Inflection rank weights. Gates already dropped non-inflections.
W_BAND = 3.0
W_D10 = 1.6
W_STREAK = 2.0
W_DISP = 1.4
W_STRUCTURE = 0.35  # FLAGS / hop / leader only — not an eligibility gate

# Display backstop. Selectivity comes from the gates, not this trim.
RANK_CAP = 24
MIN_COMPOSITE = 0.0  # unused as a junk filter; gates are the cut

# Price stats on ranked rows (same closes Mom cards use: px_series / prices_long).
R20_N = 20
RS63_N = 63
ATR_N = 14
BENCH_NAMES: tuple[str, ...] = (
    "SPY US Equity",
    "SPY",
    "SPX Index",
    "SPX",
    "ES1 Index",
    "QQQ US Equity",
    "QQQ",
)
PX_SERIES_KEYS: tuple[str, ...] = (
    "px_series",
    "prices",
    "closes",
    "px_hist",
    "history",
    "px",
    "hist",
)

DB_SCRIPT_ID = "fd-breakout-db"
JS_SCRIPT_ID = "fd-breakout-js"
JS_VER = "pr21-ignore-paper"
CSS_STYLE_ID = "fd-breakout-css"
PANE_BREAKOUT_ID = "fd-bb-breakout"
PANE_BREAKDOWN_ID = "fd-bb-breakdown"
VIEW_BREAKOUT_ID = "view-breakout"
VIEW_BREAKDOWN_ID = "view-breakdown"
GRID_BREAKOUT_ID = "breakout-grid"
GRID_BREAKDOWN_ID = "breakdown-grid"
HID_CLASS = "fd-bb-hid"

NAV_BREAKOUT_ID = "fd-nav-breakout"
NAV_BREAKDOWN_ID = "fd-nav-breakdown"
BTN_BREAKOUT = (
    f'<button type="button" class="btn nav-btn" id="{NAV_BREAKOUT_ID}" '
    'data-view="breakout" data-fd-breakout="1">Breakout</button>'
)
BTN_BREAKDOWN = (
    f'<button type="button" class="btn nav-btn" id="{NAV_BREAKDOWN_ID}" '
    'data-view="breakdown" data-fd-breakdown="1">Breakdown</button>'
)
SETVIEW_MARKER = "/*fd-bb-setview*/"
HIDEALL_MARKER = "/*fd-bb-hideall*/"
PAINTVIEW_MARKER = "/*fd-bb-paintview*/"
NATIVE_VIEW_IDS: tuple[str, ...] = (
    "home",
    "view-mom-up",
    "view-mom-down",
    "view-outliers",
    "view-options",
    "view-sectors",
    "search-pane",
    VIEW_BREAKOUT_ID,
    VIEW_BREAKDOWN_ID,
    "view-experimental",
    # Paper owns #view-paper — BB hideNativeViews must not sweep it.
    # Marker: __FD_BB_IGNORE_PAPER__
)
_ALLOWLIST_RE = re.compile(
    r"home\|mom-up\|mom-down\|outliers\|options(?:\|sectors)?(?!\|breakout)",
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
    r"""(?:\s*,\s*["']view-sectors["'])?(?:\s*,\s*["']search-pane["'])?))"""
    r"""(?![^\]]*(?:view-breakout|view-breakdown))(\s*\])""",
    re.I,
)
_VIEW_SEL_RE = re.compile(
    r"(#home\s*,\s*#view-mom-up\s*,\s*#view-mom-down\s*,\s*#view-outliers\s*,\s*"
    r"#view-options(?:\s*,\s*#view-sectors)?(?:\s*,\s*#search-pane)?)"
    r"(?![^\"';)]*(?:#view-breakout|#view-breakdown))",
    re.I,
)
# Experimental pane is a sibling tab (s_score.py). Hiding it from Breakout
# is a no-op until that pane exists.
_DIV_TOKEN_RE = re.compile(r"<\s*(/)?\s*div\b([^>]*)>", re.I)

_HOP_LEADER_RE = re.compile(r"\b(hop|leader)\b", re.I)
_OPT_SPIKE_RE = re.compile(r"opt[\s_-]*spike", re.I)
_MOM_DOWN_BTN_RE = re.compile(
    r'(<button\b(?=[^>]*(?:data-view=["\']mom-down["\']|>\s*Momentum Down))[^>]*>\s*Momentum Down\s*</button>)',
    re.I | re.S,
)


def _short(ticker: str) -> str:
    parts = (ticker or "").split()
    return parts[0].upper() if parts else ""


def _round_stat(value: float | None) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def _as_close(value: Any) -> float | None:
    if isinstance(value, bool) or value is None or value == "":
        return None
    return dapi_enrich.as_float(value)


def closes_from_raw(raw: Any) -> list[float]:
    """Extract a close series from px.by / hist / card px_series shapes."""
    if raw is None or raw == "":
        return []
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        try:
            raw = json.loads(text)
        except (TypeError, ValueError):
            return []
    out: list[float] = []

    def push(value: Any) -> None:
        px = _as_close(value)
        if px is not None and px > 0:
            out.append(float(px))

    if isinstance(raw, Mapping):
        for key in PX_SERIES_KEYS + ("c", "series"):
            nested = closes_from_raw(raw.get(key))
            if len(nested) >= 2:
                return nested
        dated: list[tuple[str, float]] = []
        for key, val in raw.items():
            if key in PX_SERIES_KEYS or key in {"by", "BY", "names"}:
                continue
            px = _as_close(val)
            if px is None and isinstance(val, Mapping):
                px = _as_close(
                    val.get("c")
                    or val.get("close")
                    or val.get("px")
                    or val.get("p")
                    or val.get("last")
                    or val.get("v")
                )
            if px is not None and px > 0:
                dated.append((str(key), float(px)))
        if dated:
            dated.sort(key=lambda kv: kv[0])
            return [px for _k, px in dated]
        return out
    if isinstance(raw, (list, tuple)):
        for row in raw:
            if isinstance(row, bool) or row is None:
                continue
            if isinstance(row, (int, float)):
                push(row)
                continue
            if isinstance(row, (list, tuple)) and len(row) >= 2:
                push(row[-1])
                continue
            if isinstance(row, Mapping):
                push(
                    row.get("c")
                    or row.get("close")
                    or row.get("px")
                    or row.get("p")
                    or row.get("last")
                    or row.get("v")
                    or row.get("price")
                )
        return out
    push(raw)
    return out


def closes_from_card(card: Mapping[str, Any] | None) -> list[float]:
    if not isinstance(card, Mapping):
        return []
    for key in PX_SERIES_KEYS:
        got = closes_from_raw(card.get(key))
        if len(got) >= 2:
            return got
    return []


def closes_from_panel(panel: Mapping[str, Any] | None, ticker: str) -> list[float]:
    if not isinstance(panel, Mapping) or not ticker:
        return []
    want = _short(ticker)
    rec = panel.get(ticker)
    if rec is None:
        rec = panel.get(mom_streak._ticker_key(ticker))
    if rec is None:
        for key, val in panel.items():
            if _short(str(key)) == want:
                rec = val
                break
    if rec is None:
        return []
    if isinstance(rec, list) and rec and isinstance(rec[0], tuple) and len(rec[0]) >= 2:
        out: list[float] = []
        for _d, px in rec:
            val = _as_close(px)
            if val is not None and val > 0:
                out.append(float(val))
        return out
    return closes_from_raw(rec)


def ret_n(closes: Sequence[float], n: int) -> float | None:
    """Decimal return over ``n`` trading days (needs n+1 closes). Live ``fmtPct`` does ``x*100``."""
    if n <= 0 or len(closes) < n + 1:
        return None
    start = float(closes[-(n + 1)])
    last = float(closes[-1])
    if start == 0:
        return None
    return round(last / start - 1.0, 4)


def atr_pct(closes: Sequence[float], period: int = ATR_N) -> float | None:
    """Wilder ATR as percent of last close. Close-to-close TR when H/L absent."""
    if period <= 0 or len(closes) < period + 1:
        return None
    trs = [abs(float(closes[i]) - float(closes[i - 1])) for i in range(1, len(closes))]
    atr = sum(trs[:period]) / float(period)
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / float(period)
    last = float(closes[-1])
    if last == 0:
        return None
    return _round_stat(100.0 * atr / last)


def _bench_closes(
    panel: Mapping[str, Any] | None,
    cards: Iterable[Mapping[str, Any]] | None = None,
) -> list[float]:
    for name in BENCH_NAMES:
        got = closes_from_panel(panel, name)
        if len(got) >= RS63_N + 1:
            return got
    for card in cards or []:
        if not isinstance(card, Mapping):
            continue
        ticker = str(card.get("ticker") or card.get("t") or card.get("name") or "")
        if _short(ticker) not in {"SPY", "SPX", "QQQ", "ES1"}:
            continue
        got = closes_from_card(card)
        if len(got) >= RS63_N + 1:
            return got
    return []


def _nested_metrics(card: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(card, Mapping):
        return {}
    raw = card.get("metrics")
    return dict(raw) if isinstance(raw, Mapping) else {}


def _float_any(value: Any) -> float | None:
    if isinstance(value, bool) or value is None or value == "":
        return None
    return dapi_enrich.as_float(value)


def metrics_of(card: Mapping[str, Any] | None) -> dict[str, float | None]:
    """Live MOM ``cardHTML`` reads ``c.metrics.r20_pct`` / ``rs_63`` / ``atr_pct``."""
    blob = _nested_metrics(card)
    return {
        "day": _float_any(blob.get("day_pct") if blob.get("day_pct") is not None else blob.get("r1_pct") or blob.get("ret_1d")),
        "r20": _float_any(blob.get("r20_pct") if blob.get("r20_pct") is not None else blob.get("r20")),
        "rs63": _float_any(blob.get("rs_63") if blob.get("rs_63") is not None else blob.get("rs63") or blob.get("rs_63d")),
        "atr_pct": _float_any(blob.get("atr_pct") if blob.get("atr_pct") is not None else blob.get("atr")),
    }


def _num_stat(src: Mapping[str, Any] | None, keys: Sequence[str]) -> float | None:
    if not isinstance(src, Mapping):
        return None
    picked = card_render._pick_num(src, tuple(keys))
    if isinstance(picked, bool) or picked is None or picked == "":
        return None
    try:
        return float(picked)
    except (TypeError, ValueError):
        return None


def px_stats(
    card: Mapping[str, Any] | None = None,
    panel: Mapping[str, Any] | None = None,
    *,
    ticker: str = "",
    bench: Sequence[float] | None = None,
) -> dict[str, float | None]:
    """Day / R20 / RS63 / ATR% from ``metrics.*``, then aliases, then price history.

    Returns are **decimals** (0.1277 → live ``fmtPct`` → 12.8%). ``atr_pct`` is
    already percent points (~2–5); do not multiply by 100 again.
    """
    src = card if isinstance(card, Mapping) else {}
    from_metrics = metrics_of(src)
    aliased = dict(src)
    card_render.alias_stats(aliased)
    existing = {
        "day": from_metrics["day"] if from_metrics["day"] is not None else _num_stat(aliased, card_render.DAY_KEYS),
        "r20": from_metrics["r20"] if from_metrics["r20"] is not None else _num_stat(aliased, card_render.R20_KEYS),
        "rs63": from_metrics["rs63"] if from_metrics["rs63"] is not None else _num_stat(aliased, card_render.RS63_KEYS),
        "atr_pct": from_metrics["atr_pct"] if from_metrics["atr_pct"] is not None else _num_stat(aliased, card_render.ATR_KEYS),
    }
    name = ticker or str(src.get("ticker") or src.get("t") or src.get("name") or "")
    closes = closes_from_card(src)
    if len(closes) < 2:
        closes = closes_from_panel(panel, name)
    day = existing["day"] if existing["day"] is not None else ret_n(closes, 1)
    r20 = existing["r20"] if existing["r20"] is not None else ret_n(closes, R20_N)
    atr = existing["atr_pct"] if existing["atr_pct"] is not None else atr_pct(closes)
    rs63 = existing["rs63"]
    if rs63 is None:
        stock63 = ret_n(closes, RS63_N)
        bench_closes = list(bench) if bench is not None else _bench_closes(panel, [src] if src else None)
        bench63 = ret_n(bench_closes, RS63_N)
        if stock63 is not None and bench63 is not None:
            rs63 = round(stock63 - bench63, 4)
        else:
            rs63 = stock63
    return {"day": day, "r20": r20, "rs63": rs63, "atr_pct": atr}


def metrics_payload(stats: Mapping[str, Any] | None, card: Mapping[str, Any] | None = None) -> dict[str, float]:
    """Shape live ``cardHTML`` actually reads."""
    stats = stats or {}
    out = _nested_metrics(card)
    mapping = (
        ("r20_pct", stats.get("r20")),
        ("rs_63", stats.get("rs63")),
        ("atr_pct", stats.get("atr_pct")),
        ("day_pct", stats.get("day")),
    )
    for key, value in mapping:
        if out.get(key) is None and value is not None and value != "":
            try:
                out[key] = float(value)
            except (TypeError, ValueError):
                continue
    return {k: v for k, v in out.items() if v is not None and v != ""}


_TICKER_CTX_RE = re.compile(
    r"""(?:["'](?:t|d|ticker|symbol)["']|\b(?:t|d|ticker|symbol))\s*:\s*["']([^"']+)["']""",
    re.I,
)
_METRICS_KEY_RE = re.compile(r'(?:["\']metrics["\']|\bmetrics)\s*:\s*\{', re.I)
_METRIC_NUM_RE = re.compile(
    r"""["']?(r20_pct|rs_63|atr_pct|day_pct|r1_pct|r20|rs63|atr)["']?\s*:\s*(-?\d+(?:\.\d+)?)""",
    re.I,
)


def _read_js_object(text: str, open_idx: int, *, limit: int = 4000) -> str:
    if open_idx < 0 or open_idx >= len(text) or text[open_idx] != "{":
        return ""
    depth = 0
    in_str = None
    escape = False
    end = min(len(text), open_idx + limit)
    i = open_idx
    while i < end:
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == in_str:
                in_str = None
        else:
            if ch in "\"'":
                in_str = ch
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[open_idx : i + 1]
        i += 1
    return ""


def _parse_metrics_obj(blob: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for match in _METRIC_NUM_RE.finditer(blob or ""):
        key = match.group(1)
        try:
            out[key] = float(match.group(2))
        except (TypeError, ValueError):
            continue
    canon: dict[str, float] = {}
    if "r20_pct" in out:
        canon["r20_pct"] = out["r20_pct"]
    elif "r20" in out:
        canon["r20_pct"] = out["r20"]
    if "rs_63" in out:
        canon["rs_63"] = out["rs_63"]
    elif "rs63" in out:
        canon["rs_63"] = out["rs63"]
    if "atr_pct" in out:
        canon["atr_pct"] = out["atr_pct"]
    elif "atr" in out:
        canon["atr_pct"] = out["atr"]
    if "day_pct" in out:
        canon["day_pct"] = out["day_pct"]
    elif "r1_pct" in out:
        canon["day_pct"] = out["r1_pct"]
    return canon


def extract_live_metrics_map(html_text: str) -> dict[str, dict[str, float]]:
    """Ticker → metrics from live HTML MOM cards (no ``fd-mom-db``).

    Live Desktop embeds ~396 ``{t, metrics:{r20_pct, rs_63, atr_pct}}`` objects
    in factorbook HTML. CoS hot-filled BB rows from that map; this is the
    durable equivalent at ``ensure_embedded`` time.
    """
    out: dict[str, dict[str, float]] = {}
    text = html_text or ""
    for match in _METRICS_KEY_RE.finditer(text):
        body = _read_js_object(text, match.end() - 1)
        metrics = _parse_metrics_obj(body)
        if not metrics:
            continue
        ctx_before = text[max(0, match.start() - 800) : match.start()]
        after_start = match.end() - 1 + len(body)
        ctx_after = text[after_start : after_start + 300]
        before_tickers = _TICKER_CTX_RE.findall(ctx_before)
        after_tickers = _TICKER_CTX_RE.findall(ctx_after)
        raw = ""
        if before_tickers:
            raw = str(before_tickers[-1] or "").strip()
        elif after_tickers:
            raw = str(after_tickers[0] or "").strip()
        if not raw:
            continue
        short = _short(raw)
        if not short:
            continue
        for key in (short, raw):
            cur = out.get(key)
            if not isinstance(cur, dict):
                out[key] = dict(metrics)
                continue
            for mk, mv in metrics.items():
                if cur.get(mk) is None:
                    cur[mk] = mv
    return out


def lookup_live_metrics(live_map: Mapping[str, Any] | None, ticker: str) -> dict[str, float]:
    if not live_map or not ticker:
        return {}
    rec = live_map.get(ticker) or live_map.get(_short(ticker))
    if not isinstance(rec, Mapping):
        for key, val in live_map.items():
            if _short(str(key)) == _short(ticker) and isinstance(val, Mapping):
                rec = val
                break
    return {k: float(v) for k, v in (rec or {}).items() if _float_any(v) is not None}


def merge_live_metrics_into_card(
    card: Mapping[str, Any] | None,
    live_map: Mapping[str, Any] | None,
    ticker: str = "",
) -> dict[str, Any]:
    src = dict(card) if isinstance(card, Mapping) else {}
    blob = lookup_live_metrics(live_map, ticker or str(src.get("ticker") or src.get("t") or ""))
    if not blob:
        return src
    metrics = dict(src.get("metrics") or {})
    for key, value in blob.items():
        if metrics.get(key) is None:
            metrics[key] = value
    if metrics:
        src["metrics"] = metrics
    return src


def enrich_ranked_from_map(
    ranked: Mapping[str, Any] | None,
    live_map: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Stamp live MOM ``metrics.*`` onto ranked BB rows (CoS map → durable)."""
    ranked = dict(ranked or {})
    if not live_map:
        return ranked
    for side in ("breakout", "breakdown"):
        rows: list[dict[str, Any]] = []
        for row in ranked.get(side) or []:
            if not isinstance(row, Mapping):
                continue
            row = dict(row)
            ticker = str(row.get("ticker") or row.get("t") or "")
            src = row.get("_card") if isinstance(row.get("_card"), Mapping) else None
            if src is None and isinstance(row.get("card"), Mapping):
                src = row.get("card")
            src = merge_live_metrics_into_card(src, live_map, ticker)
            if src:
                if isinstance(row.get("_card"), Mapping) or row.get("_card") is None:
                    row["_card"] = src
                if isinstance(row.get("card"), Mapping):
                    card = dict(row["card"])
                    if src.get("metrics"):
                        card["metrics"] = src["metrics"]
                    row["card"] = card
                attach_px_stats(row, src)
            rows.append(row)
        ranked[side] = rows
    return ranked


def attach_px_stats(
    row: dict[str, Any],
    card: Mapping[str, Any] | None = None,
    panel: Mapping[str, Any] | None = None,
    *,
    bench: Sequence[float] | None = None,
) -> dict[str, Any]:
    """Write numeric Day/R20/RS63/ATR% onto a ranked row when prices exist."""
    stats = px_stats(
        card,
        panel,
        ticker=str(row.get("ticker") or row.get("t") or ""),
        bench=bench,
    )
    for key, value in stats.items():
        if value is not None:
            row[key] = value
        elif key not in row:
            row[key] = None
    metrics = metrics_payload(stats, card)
    if metrics:
        row["metrics"] = metrics
    return row


def card_lists(card: Mapping[str, Any] | None) -> set[str]:
    """FLAGS / WATCH / OUTLIERS / MOM buckets from existing card fields only."""
    out: set[str] = set()
    if not card:
        return out
    raw = card.get("list") or card.get("lists") or card.get("bucket") or card.get("src")
    if isinstance(raw, str) and raw.strip():
        out.add(raw.strip().upper().replace("OUTLIER", "OUTLIERS") if raw.strip().upper() == "OUTLIER" else raw.strip().upper())
    elif isinstance(raw, (list, tuple, set)):
        for item in raw:
            token = str(item or "").strip().upper()
            if token == "OUTLIER":
                token = "OUTLIERS"
            if token:
                out.add(token)
    flags = (
        ("flags", "FLAGS"),
        ("is_flags", "FLAGS"),
        ("on_flags", "FLAGS"),
        ("watch", "WATCH"),
        ("is_watch", "WATCH"),
        ("on_watch", "WATCH"),
        ("outlier", "OUTLIERS"),
        ("outliers", "OUTLIERS"),
        ("is_outlier", "OUTLIERS"),
        ("on_outliers", "OUTLIERS"),
    )
    for key, label in flags:
        val = card.get(key)
        if val in (True, 1, "1", "true", "True", "yes", "YES"):
            out.add(label)
    kind = str(card.get("kind") or card.get("tab") or card.get("view") or "").strip().upper()
    if kind in {"FLAGS", "WATCH", "MOM", "OUTLIERS", "OUTLIER"}:
        out.add("OUTLIERS" if kind == "OUTLIER" else kind)
    return out


def _blob(card: Mapping[str, Any] | None) -> str:
    if not card:
        return ""
    bits: list[str] = []
    for key in ("tags", "tag_triggers", "digest_tags", "flags_tags", "notes", "label", "why"):
        val = card.get(key)
        if isinstance(val, str):
            bits.append(val)
        elif isinstance(val, (list, tuple)):
            for item in val:
                if isinstance(item, Mapping):
                    bits.append(str(item.get("label") or item.get("tag") or item.get("name") or ""))
                else:
                    bits.append(str(item or ""))
    pills = card.get("enrich_pills")
    if isinstance(pills, list):
        for pill in pills:
            if isinstance(pill, Mapping):
                bits.append(str(pill.get("label") or ""))
                bits.append(str(pill.get("key") or ""))
    return " ".join(bits)


def has_hop_leader(card: Mapping[str, Any] | None) -> bool:
    return bool(_HOP_LEADER_RE.search(_blob(card)))


def has_opt_spike(card: Mapping[str, Any] | None) -> bool:
    if not card:
        return False
    for key in ("opt_spike", "opt_spike_flag", "options_spike"):
        val = card.get(key)
        if val in (True, 1, "1", "true", "True"):
            return True
        if isinstance(val, (int, float)) and not isinstance(val, bool) and val:
            return True
    pills = card.get("enrich_pills")
    if isinstance(pills, list):
        for pill in pills:
            if not isinstance(pill, Mapping):
                continue
            key = str(pill.get("key") or "")
            label = str(pill.get("label") or "")
            if _OPT_SPIKE_RE.search(key) or _OPT_SPIKE_RE.search(label):
                return True
            if str(pill.get("cls") or "") == "opt-spike":
                return True
    return bool(_OPT_SPIKE_RE.search(_blob(card)))


def is_outlier_newbie(card: Mapping[str, Any] | None) -> bool:
    if not card:
        return False
    lists = card_lists(card)
    if "OUTLIERS" not in lists and not card.get("outlier"):
        return False
    try:
        streak = int(card.get("mom_streak") or 0)
    except (TypeError, ValueError):
        streak = 0
    return streak <= 5


def series_for(
    card: Mapping[str, Any] | None,
    hist: Mapping[str, Any] | None,
    ticker: str,
) -> list[tuple[Any, float]]:
    raw = None
    if card:
        raw = card.get("mom_score_series") or card.get("series")
    rows: list[tuple[Any, float]] = []
    if isinstance(raw, list) and raw:
        for item in raw:
            if isinstance(item, Mapping):
                day = mom_streak._as_date(item.get("date") or item.get("asof"))
                score = dapi_enrich.as_float(item.get("score") or item.get("mom_score") or item.get("value"))
                if day is not None and score is not None:
                    rows.append((day, score))
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                day = mom_streak._as_date(item[0])
                score = dapi_enrich.as_float(item[1])
                if day is not None and score is not None:
                    rows.append((day, score))
    if not rows:
        rows = list(mom_streak.series_of(hist, ticker))
    rows.sort(key=lambda x: x[0])
    return rows


def _clip(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _in_band(score: float | None, lo: float, hi: float) -> bool:
    return score is not None and lo <= float(score) <= hi


def _d10_of(
    card: Mapping[str, Any],
    series: Sequence[tuple[Any, float]],
    score: float | None,
) -> float | None:
    """Card ``mom_score_d10`` when attached; else the same 10-print helper."""
    if "mom_score_d10" in card and card.get("mom_score_d10") not in (None, ""):
        return _float_any(card.get("mom_score_d10"))
    rec = mom_streak.score_change_d10(series, score)
    if not rec:
        return None
    return _float_any(rec.get("mom_score_d10"))


def _streak_of(
    card: Mapping[str, Any],
    series: Sequence[tuple[Any, float]],
) -> tuple[int, str | None]:
    side = card.get("mom_streak_side")
    raw = card.get("mom_streak")
    if side in ("above", "below", "at") and raw is not None and raw != "":
        try:
            return int(raw), str(side)
        except (TypeError, ValueError):
            pass
    return mom_streak.streak_from_dated(series)


def streak_is_fresh(streak: int, side: str | None, want: str) -> bool:
    """Wanted side, and either just crossed (1d) or a fresh run of 2–15d."""
    if side != want:
        return False
    n = int(streak or 0)
    return STREAK_JUST_CROSSED <= n <= STREAK_FRESH_MAX


def _present_float(src: Mapping[str, Any] | None, key: str) -> float | None:
    if not isinstance(src, Mapping) or key not in src:
        return None
    raw = src.get(key)
    if raw is None or raw == "":
        return None
    return _float_any(raw)


def dispersion_of(
    card: Mapping[str, Any] | None,
    panel: Mapping[str, Any] | None = None,
    *,
    bench: Sequence[float] | None = None,
) -> tuple[float | None, str]:
    """``(value, field)`` for the idio gate.

    Order: existing ~20d factor residual on the card or ``metrics``
    (``residual_20d``, then ``factor_residual`` / ``vs_group`` / ``vs_sleeve``).
    A present number, including 0, wins — no fall-through. If none of those
    fields exist, interim RS63: card/metrics ``rs_63`` / ``rs63``, else 63d
    return minus the benchmark when **both** series exist. A raw return with
    no benchmark is not treated as RS63.
    """
    src = card if isinstance(card, Mapping) else {}
    blobs: list[Mapping[str, Any]] = [src]
    metrics = src.get("metrics")
    if isinstance(metrics, Mapping):
        blobs.append(metrics)
    for blob in blobs:
        for key in DISPERSION_KEYS:
            val = _present_float(blob, key)
            if val is not None:
                return val, key
    for blob in blobs:
        for key in ("rs_63", "rs63", "RS63", "rs_63d"):
            val = _present_float(blob, key)
            if val is not None:
                return val, "rs63"
    name = str(src.get("ticker") or src.get("t") or src.get("name") or "")
    closes = closes_from_card(src)
    if len(closes) < RS63_N + 1:
        closes = closes_from_panel(panel, name)
    stock63 = ret_n(closes, RS63_N)
    bench_closes = list(bench) if bench is not None else _bench_closes(panel, [src] if src else None)
    bench63 = ret_n(bench_closes, RS63_N)
    if stock63 is not None and bench63 is not None:
        return round(stock63 - bench63, 4), "rs63"
    return None, ""


def band_early(score: float, *, kind: str) -> float:
    """1.0 on the early edge of the gate band, lower toward the extended edge."""
    if kind == "breakout":
        if not _in_band(score, BREAKOUT_SCORE_MIN, BREAKOUT_SCORE_MAX):
            return 0.0
        span = max(BREAKOUT_SCORE_MAX - BREAKOUT_SCORE_MIN, 0.01)
        return 1.0 - 0.45 * (float(score) - BREAKOUT_SCORE_MIN) / span
    if not _in_band(score, BREAKDOWN_SCORE_MIN, BREAKDOWN_SCORE_MAX):
        return 0.0
    span = max(BREAKDOWN_SCORE_MAX - BREAKDOWN_SCORE_MIN, 0.01)
    return 1.0 - 0.45 * (BREAKDOWN_SCORE_MAX - float(score)) / span


def streak_freshness(streak: int) -> float:
    """1.0 on a mid run (~5–12d). Just-crossed is lower. Baked (>15) is 0."""
    n = int(streak or 0)
    if n < STREAK_JUST_CROSSED or n > STREAK_FRESH_MAX:
        return 0.0
    if STREAK_PEAK_LO <= n <= STREAK_PEAK_HI:
        return 1.0
    if n < STREAK_PEAK_LO:
        span = float(STREAK_PEAK_LO - STREAK_JUST_CROSSED)
        return 0.35 + 0.65 * (n - STREAK_JUST_CROSSED) / span
    span = float(STREAK_FRESH_MAX - STREAK_PEAK_HI)
    return 1.0 - 0.35 * (n - STREAK_PEAK_HI) / span


def structure_bonus(card: Mapping[str, Any] | None) -> float:
    """Soft confirm only. Opt spike does not add rank and is not a gate."""
    bonus = 0.0
    lists = card_lists(card)
    if "FLAGS" in lists:
        bonus += 0.6
    if has_hop_leader(card):
        bonus += 0.4
    return bonus


def _inflection_magnitude(
    *,
    score: float,
    d10: float,
    streak: int,
    dispersion: float,
    card: Mapping[str, Any],
    kind: str,
) -> float:
    return (
        W_BAND * band_early(score, kind=kind)
        + W_D10 * (_clip(abs(d10), 0.0, D10_CLIP) / D10_CLIP)
        + W_STREAK * streak_freshness(streak)
        + W_DISP * (_clip(abs(dispersion), 0.0, DISP_CLIP) / DISP_CLIP)
        + W_STRUCTURE * structure_bonus(card)
    )


def _passes_breakout(
    score: float | None,
    d10: float | None,
    streak: int,
    side: str | None,
    dispersion: float | None,
) -> bool:
    if not _in_band(score, BREAKOUT_SCORE_MIN, BREAKOUT_SCORE_MAX):
        return False
    if d10 is None or d10 <= D10_BREAKOUT_MIN:
        return False
    if not streak_is_fresh(streak, side, "above"):
        return False
    if dispersion is None or dispersion <= 0:
        return False
    return True


def _passes_breakdown(
    score: float | None,
    d10: float | None,
    streak: int,
    side: str | None,
    dispersion: float | None,
) -> bool:
    if not _in_band(score, BREAKDOWN_SCORE_MIN, BREAKDOWN_SCORE_MAX):
        return False
    if d10 is None or d10 >= D10_BREAKDOWN_MAX:
        return False
    if not streak_is_fresh(streak, side, "below"):
        return False
    if dispersion is None or dispersion >= 0:
        return False
    return True


def _why(
    *,
    score: float,
    d10: float,
    streak: int,
    side: str | None,
    dispersion: float,
    field: str,
    kind: str,
) -> str:
    arrow = "+" if d10 >= 0 else ""
    return (
        f"{kind} {score:g} · {arrow}{d10:g}/10d · {side or '—'} {int(streak)}d"
        f" · {field} {dispersion:g}"
    )


def score_one(
    card: Mapping[str, Any],
    hist: Mapping[str, Any] | None = None,
    *,
    kind: str,
    panel: Mapping[str, Any] | None = None,
    bench: Sequence[float] | None = None,
) -> dict[str, Any] | None:
    """Return a ranked row or ``None`` if the name fails any hard gate."""
    ticker = mom_streak.card_ticker(card)
    if not ticker:
        return None
    score = dapi_enrich.as_float(card.get("mom_score"))
    if score is None:
        score, _src = mom_streak.resolve_card_score(card)
    series = series_for(card, hist, ticker)
    if score is not None and series:
        last_day = series[-1][0]
        if series[-1][1] != score:
            series = list(series[:-1]) + [(last_day, float(score))]
    elif score is not None and not series:
        series = [(mom_streak._today(), float(score))]
    d10 = _d10_of(card, series, score)
    streak, side = _streak_of(card, series)
    dispersion, field = dispersion_of(card, panel, bench=bench)
    if kind == "breakout":
        if not _passes_breakout(score, d10, streak, side, dispersion):
            return None
    elif kind == "breakdown":
        if not _passes_breakdown(score, d10, streak, side, dispersion):
            return None
    else:
        return None
    assert score is not None and d10 is not None and dispersion is not None
    magnitude = _inflection_magnitude(
        score=score,
        d10=d10,
        streak=streak,
        dispersion=dispersion,
        card=card,
        kind=kind,
    )
    signed = magnitude if kind == "breakout" else -magnitude
    score_key = "breakout_score" if kind == "breakout" else "breakdown_score"
    return {
        "t": _short(ticker),
        "ticker": ticker,
        "score": score,
        "delta": round(d10, 4),
        "lookback": mom_streak.D10_LOOKBACK,
        "mom_score_d10": round(d10, 4),
        "streak": streak,
        "side": side,
        "label": card.get("mom_streak_label") or mom_streak.tag_label(streak, side),
        "dispersion": round(dispersion, 4),
        "dispersion_field": field,
        "inflection_score": round(signed, 4),
        score_key: round(magnitude, 4),
        "why": _why(
            score=score,
            d10=d10,
            streak=streak,
            side=side,
            dispersion=dispersion,
            field=field,
            kind="band",
        ),
    }


def _dedupe_best(rows: list[dict[str, Any]], score_key: str) -> list[dict[str, Any]]:
    best: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = _short(str(row.get("ticker") or row.get("t") or ""))
        if not key:
            continue
        prev = best.get(key)
        if prev is None or float(row.get(score_key) or 0) > float(prev.get(score_key) or 0):
            best[key] = row
    return list(best.values())


def rank_side(
    cards: Iterable[Mapping[str, Any]] | None,
    hist: Mapping[str, Any] | None,
    *,
    kind: str,
    cap: int = RANK_CAP,
    panel: Mapping[str, Any] | None = None,
    bench: Sequence[float] | None = None,
) -> list[dict[str, Any]]:
    score_key = "breakout_score" if kind == "breakout" else "breakdown_score"
    rows: list[dict[str, Any]] = []
    for card in cards or []:
        if not isinstance(card, Mapping):
            continue
        row = score_one(card, hist, kind=kind, panel=panel, bench=bench)
        if row:
            row["_card"] = card
            attach_px_stats(row, card, panel, bench=bench)
            rows.append(row)
    rows = _dedupe_best(rows, score_key)
    if kind == "breakdown":
        # Most negative inflection first (mirror of breakout desc).
        rows.sort(key=lambda r: float(r.get("inflection_score") or 0))
    else:
        rows.sort(key=lambda r: float(r.get("inflection_score") or 0), reverse=True)
    return rows[: max(0, int(cap))]


def rank_book(
    cards: Iterable[Mapping[str, Any]] | None,
    hist: Mapping[str, Any] | None = None,
    *,
    cap: int = RANK_CAP,
    root: Any = None,
    prices: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Select names that clear every inflection gate, ranked by inflection score.

    ``cap`` (default 24) is a display backstop. The gates are the real cut.
    """
    src = [c for c in (cards or []) if isinstance(c, Mapping)]
    panel = prices
    if panel is None:
        panel, _rel = mom_streak.load_price_panel(root)
    bench = _bench_closes(panel, src)
    breakout = rank_side(src, hist, kind="breakout", cap=cap, panel=panel, bench=bench)
    breakdown = rank_side(src, hist, kind="breakdown", cap=cap, panel=panel, bench=bench)
    return {
        "breakout": breakout,
        "breakdown": breakdown,
        "cap": cap,
        "asof": None if not hist else hist.get("asof"),
    }


def slim_payload(
    ranked: Mapping[str, Any] | None,
    live_map: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """JSON-safe db. Nested ``metrics.r20_pct`` / ``rs_63`` / ``atr_pct`` (Mom shape)."""
    ranked = ranked or {}
    live_map = live_map or {}

    def slim(rows: Any, score_key: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for row in rows or []:
            if not isinstance(row, Mapping):
                continue
            ticker = str(row.get("ticker") or row.get("t") or "")
            src = row.get("_card") if isinstance(row.get("_card"), Mapping) else None
            if src is None and isinstance(row.get("card"), Mapping):
                src = row.get("card")
            src = merge_live_metrics_into_card(src, live_map, ticker)
            stats = px_stats(src, ticker=ticker)
            for key in ("day", "r20", "rs63", "atr_pct"):
                if row.get(key) is not None and row.get(key) != "":
                    stats[key] = row.get(key)
            merged = dict(row)
            merged.update({k: v for k, v in stats.items() if v is not None})
            card = card_render.portable_card(src, merged) or {}
            for key, value in stats.items():
                if value is not None:
                    card.setdefault(key, value)
            metrics = metrics_payload(stats, src)
            if isinstance(row.get("metrics"), Mapping):
                for key, value in row["metrics"].items():
                    if value is not None and metrics.get(key) is None:
                        metrics[key] = value
            if metrics:
                card["metrics"] = metrics
            payload = {
                "t": row.get("t") or _short(ticker),
                "ticker": row.get("ticker"),
                "score": row.get("score"),
                "delta": row.get("delta"),
                "lookback": row.get("lookback"),
                "streak": row.get("streak"),
                "side": row.get("side"),
                "label": row.get("label"),
                score_key: row.get(score_key),
                "why": row.get("why"),
                "mom_score_d10": row.get("mom_score_d10", row.get("delta")),
                "dispersion": row.get("dispersion"),
                "dispersion_field": row.get("dispersion_field"),
                "inflection_score": row.get("inflection_score"),
                "day": stats.get("day"),
                "r20": stats.get("r20"),
                "rs63": stats.get("rs63"),
                "atr_pct": stats.get("atr_pct"),
                "metrics": metrics or None,
                "pills": card_render.why_pills(row),
                "card": card,
            }
            out.append(payload)
        return out

    return {
        "breakout": slim(ranked.get("breakout"), "breakout_score"),
        "breakdown": slim(ranked.get("breakdown"), "breakdown_score"),
        "cap": ranked.get("cap", RANK_CAP),
        "asof": ranked.get("asof"),
    }


def _script_json(blob: str) -> str:
    return (blob or "").replace("</", "<\\/")


def embed_db(
    ranked: Mapping[str, Any] | None,
    live_map: Mapping[str, Any] | None = None,
) -> str:
    blob = json.dumps(
        slim_payload(ranked, live_map=live_map),
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    )
    return f'<script type="application/json" id="{DB_SCRIPT_ID}">{_script_json(blob)}</script>'


def strip_css() -> str:
    return f"""
/* Legacy stub panes must never paint — dense cards live in #view-breakout / #view-breakdown. */
#fd-bb-breakout, #fd-bb-breakdown, .fd-bb-pane,
article.fd-bb-card, .fd-bb-card {{
  display: none !important;
}}
#view-breakout.hide, #view-breakdown.hide {{ display: none !important; }}
.{HID_CLASS} {{ display: none !important; }}
.fd-bb-empty {{
  grid-column: 1 / -1;
  color: #9ca3af;
  font-size: 12px;
  margin: 8px 0;
}}
#breakout-grid article.card .stats,
#breakdown-grid article.card .stats {{
  display: flex !important;
  flex-wrap: wrap;
  gap: 6px 12px;
  font-size: 11px;
  color: #d1d5db;
  margin-top: 6px;
}}
#breakout-grid article.card .stats span,
#breakdown-grid article.card .stats span {{
  white-space: nowrap;
}}
.nav-btn[data-view="breakout"].is-on,
.nav-btn[data-view="breakout"].on,
.btn.nav-btn[data-view="breakout"].on,
.nav-btn[data-fd-breakout="1"].is-on,
.nav-btn[data-fd-breakout="1"].on {{
  color: #6ee7b7; border-color: #34d399; background: #064e3b;
}}
.nav-btn[data-view="breakdown"].is-on,
.nav-btn[data-view="breakdown"].on,
.btn.nav-btn[data-view="breakdown"].on,
.nav-btn[data-fd-breakdown="1"].is-on,
.nav-btn[data-fd-breakdown="1"].on {{
  color: #fda4af; border-color: #fb7185; background: #3f1d1d;
}}
""".strip()


def strip_js() -> str:
    """Fill #breakout-grid / #breakdown-grid via live ``cardHTML(momCard)``.

    Look up the Momentum card by ticker and pass that object unchanged into
    the same ``cardHTML`` Momentum Up/Down uses. Do **not** paint the skinny
    dense substitute as the primary path. Capture-phase click on nav still
    stops the live topnav listener; card clicks call ``selectTicker``.
    ``show`` is ``window.__FD_BB_SHOW__``.

    Paper is not a BB view: ``kindOf`` returns ``""`` for ``data-view=paper`` /
    ``#fd-nav-paper`` / ``data-fd-paper-nav`` so capture never ``show("")``.
    Marker ``__FD_BB_IGNORE_PAPER__``.
    """
    view_ids = json.dumps(list(NATIVE_VIEW_IDS))
    return rf"""
(function () {{
  var BB_VER = "{JS_VER}";
  if (window.__FD_BB_BOUND__ === BB_VER) return;
  window.__FD_BB_BOUND__ = BB_VER;
  window.__FD_BB_CARDHTML__ = true;
  window.__FD_BB_IGNORE_PAPER__ = true;
  var DB_ID = "fd-breakout-db";
  var VIEW_BO = "view-breakout";
  var VIEW_BD = "view-breakdown";
  var GRID_BO = "breakout-grid";
  var GRID_BD = "breakdown-grid";
  var LEGACY_BO = "fd-bb-breakout";
  var LEGACY_BD = "fd-bb-breakdown";
  var NATIVE_VIEWS = {view_ids};
  var TAG_CATALOG = ["MA FAN","CLOSE HI","52W HI","HM/HL","V.EMA","ABOVE 50","ABOVE 200","MOM+","TREND↑","SQUEEZE","RS+","BREAKOUT"];
  var TAG_SET = {{}};
  for (var ti = 0; ti < TAG_CATALOG.length; ti++) TAG_SET[TAG_CATALOG[ti]] = true;

  function $(id) {{ return document.getElementById(id); }}
  function db() {{
    var el = $(DB_ID);
    if (!el) return {{ breakout: [], breakdown: [] }};
    try {{ return JSON.parse(el.textContent || "{{}}") || {{ breakout: [], breakdown: [] }}; }}
    catch (e) {{ return {{ breakout: [], breakdown: [] }}; }}
  }}
  function shortOf(t) {{ return String(t || "").trim().split(/\s+/)[0].toUpperCase(); }}
  function esc(s) {{
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }}
  function fmtPct(x, d) {{
    d = (d == null) ? 1 : d;
    if (x == null || x === "") return "—";
    var n = Number(x);
    if (!isFinite(n)) return "—";
    return (n * 100).toFixed(d) + "%";
  }}
  function fmtAtr(x) {{
    if (x == null || x === "") return "—";
    var n = Number(x);
    if (!isFinite(n)) return "—";
    if (Math.abs(n) < 1) return (n * 100).toFixed(1) + "%";
    return n.toFixed(1) + "%";
  }}
  function pickNum(obj, keys) {{
    if (!obj) return null;
    for (var i = 0; i < keys.length; i++) {{
      var v = obj[keys[i]];
      if (v == null || v === "" || v === true || v === false) continue;
      if (typeof v === "number" && isFinite(v)) return v;
      var s = String(v).trim();
      if (!s || s === "-" || s === "—" || s === "–") continue;
      var n = parseFloat(s.replace("%", "").replace(",", ""));
      if (isFinite(n)) return n;
    }}
    return null;
  }}
  function asCloses(raw) {{
    if (raw == null || raw === "") return [];
    if (typeof raw === "string") {{
      try {{ raw = JSON.parse(raw); }} catch (e0) {{ return []; }}
    }}
    var out = [];
    function push(v) {{
      var n = typeof v === "number" ? v : parseFloat(v);
      if (isFinite(n) && n > 0) out.push(n);
    }}
    if (Array.isArray(raw)) {{
      for (var i = 0; i < raw.length; i++) {{
        var row = raw[i];
        if (typeof row === "number" || typeof row === "string") push(row);
        else if (Array.isArray(row) && row.length >= 2) push(row[row.length - 1]);
        else if (row && typeof row === "object") push(row.c || row.close || row.px || row.p || row.last || row.v || row.price);
      }}
      return out;
    }}
    if (raw && typeof raw === "object") {{
      var nestedKeys = ["px_series","prices","closes","px_hist","history","px","hist","c","series"];
      for (var k = 0; k < nestedKeys.length; k++) {{
        var nested = asCloses(raw[nestedKeys[k]]);
        if (nested.length >= 2) return nested;
      }}
      var dates = Object.keys(raw).sort();
      for (var d = 0; d < dates.length; d++) {{
        var val = raw[dates[d]];
        if (typeof val === "number" || typeof val === "string") push(val);
        else if (val && typeof val === "object") push(val.c || val.close || val.px || val.p || val.last);
      }}
    }}
    return out;
  }}
  function lookupPx(ticker) {{
    var want = shortOf(ticker);
    if (!want) return [];
    var bags = [window.px, window.PX, window.prices, window.PRICES, window.hist, window.HIST];
    if (window.MOM) bags.push(window.MOM.px, window.MOM.hist, window.MOM.prices);
    for (var i = 0; i < bags.length; i++) {{
      var bag = bags[i];
      if (!bag) continue;
      var by = bag.by || bag.BY || bag.names || bag;
      if (!by || typeof by !== "object") continue;
      var rec = by[want] || by[ticker] || by[want + " US Equity"];
      if (!rec) {{
        var keys = Object.keys(by);
        for (var k = 0; k < keys.length; k++) {{
          if (shortOf(keys[k]) === want) {{ rec = by[keys[k]]; break; }}
        }}
      }}
      var closes = asCloses(rec);
      if (closes.length >= 2) return closes;
    }}
    return [];
  }}
  function retN(closes, n) {{
    if (!closes || n <= 0 || closes.length < n + 1) return null;
    var a = closes[closes.length - (n + 1)];
    var b = closes[closes.length - 1];
    if (!isFinite(a) || !isFinite(b) || a === 0) return null;
    return Math.round((b / a - 1) * 1e6) / 1e6;
  }}
  function atrPct(closes, period) {{
    period = period || 14;
    if (!closes || closes.length < period + 1) return null;
    var atr = 0;
    for (var i = 1; i <= period; i++) atr += Math.abs(closes[i] - closes[i - 1]);
    atr = atr / period;
    for (var j = period + 1; j < closes.length; j++) {{
      atr = (atr * (period - 1) + Math.abs(closes[j] - closes[j - 1])) / period;
    }}
    var last = closes[closes.length - 1];
    if (!isFinite(last) || last === 0) return null;
    return Math.round((100 * atr / last) * 100) / 100;
  }}
  function benchCloses() {{
    var names = ["SPY","SPY US Equity","SPX","SPX Index","QQQ","QQQ US Equity"];
    for (var i = 0; i < names.length; i++) {{
      var got = lookupPx(names[i]);
      if (got.length >= 64) return got;
    }}
    return [];
  }}
  function liveCard(ticker) {{
    var want = shortOf(ticker);
    if (!want) return null;
    if (typeof window.__FD_FIND_CARD__ === "function") {{
      try {{
        var found = window.__FD_FIND_CARD__(ticker);
        if (found) return found;
      }} catch (e0) {{}}
    }}
    var bags = [];
    var mom = window.MOM || {{}};
    [mom.cards, mom.up, mom.down, mom.all, window.CARDS, window.MOM_CARDS].forEach(function (src) {{
      if (!src) return;
      if (Array.isArray(src)) {{ for (var i = 0; i < src.length; i++) bags.push(src[i]); return; }}
      if (typeof src === "object") bags.push(src);
    }});
    for (var i = 0; i < bags.length; i++) {{
      var c = bags[i];
      if (!c || typeof c !== "object") continue;
      if (shortOf(c.t || c.ticker || c.d || "") === want) return c;
    }}
    return null;
  }}
  function liveMetrics(ticker) {{
    var found = liveCard(ticker);
    return (found && found.metrics) ? found.metrics : null;
  }}
  function mergeStats(row) {{
    row = row || {{}};
    var card = (row.card && typeof row.card === "object") ? row.card : {{}};
    var t = row.t || row.ticker || card.t || card.ticker || "";
    var m = (row.metrics && typeof row.metrics === "object") ? row.metrics : ((card.metrics && typeof card.metrics === "object") ? card.metrics : (liveMetrics(t) || {{}}));
    var closes = lookupPx(t);
    var bench = benchCloses();
    var computed = {{
      day: retN(closes, 1),
      r20: retN(closes, 20),
      rs63: (function () {{
        var s = retN(closes, 63);
        var b = retN(bench, 63);
        if (s != null && b != null) return Math.round((s - b) * 1e6) / 1e6;
        return s;
      }})(),
      atr_pct: atrPct(closes, 14)
    }};
    function takeMetric(obj, keys, fallback) {{
      var v = pickNum(obj, keys);
      if (v == null) v = fallback;
      return v;
    }}
    row.day = takeMetric(m, ["day_pct","r1_pct","ret_1d","day"], takeMetric(row, ["day","Day","d1","ret_1d"], takeMetric(card, ["day","Day","d1"], computed.day)));
    row.r20 = takeMetric(m, ["r20_pct","r20","R20"], takeMetric(row, ["r20","R20","ret_20d"], takeMetric(card, ["r20","R20","ret_20d"], computed.r20)));
    row.rs63 = takeMetric(m, ["rs_63","rs63","RS63"], takeMetric(row, ["rs63","RS63","rs_63"], takeMetric(card, ["rs63","RS63","rs_63"], computed.rs63)));
    row.atr_pct = takeMetric(m, ["atr_pct","atr"], takeMetric(row, ["atr_pct","atrs","ATR"], takeMetric(card, ["atr_pct","atrs","ATR"], computed.atr_pct)));
    row.metrics = {{
      r20_pct: row.r20,
      rs_63: row.rs63,
      atr_pct: row.atr_pct,
      day_pct: row.day
    }};
    card.metrics = row.metrics;
    if (row.day != null) card.day = row.day;
    if (row.r20 != null) {{ card.r20 = row.r20; if (card.R20 == null) card.R20 = row.r20; }}
    if (row.rs63 != null) {{ card.rs63 = row.rs63; if (card.RS63 == null) card.RS63 = row.rs63; }}
    if (row.atr_pct != null) {{ card.atr_pct = row.atr_pct; if (card.atrs == null) card.atrs = row.atr_pct; }}
    if (row.score != null && card.score == null) card.score = row.score;
    if (!card.t) card.t = shortOf(t);
    if (!card.d) card.d = card.t;
    if (!card.ticker) card.ticker = row.ticker || t;
    row.card = card;
    return row;
  }}
  function callCardHTML(card) {{
    var fn = null;
    try {{ fn = window.cardHTML; }} catch (e0) {{ fn = null; }}
    if (typeof fn !== "function") {{
      try {{ if (typeof cardHTML === "function") fn = cardHTML; }} catch (e1) {{ fn = null; }}
    }}
    if (typeof fn !== "function") return "";
    try {{ return fn(card) || ""; }} catch (e2) {{ return ""; }}
  }}
  function momCardFor(row) {{
    row = row || {{}};
    var t = shortOf(row.t || row.ticker || row.d || "");
    if (!t) return null;
    var card = liveCard(row.ticker || row.t || t);
    if (!card && row.card && typeof row.card === "object") card = row.card;
    if (!card) return null;
    if (!card.t) card.t = t;
    if (!card.d) card.d = card.t;
    if (!card.ticker) card.ticker = row.ticker || t;
    if (card.score == null && row.score != null) card.score = row.score;
    if (!card.metrics || typeof card.metrics !== "object") card.metrics = {{}};
    var m = card.metrics;
    if (m.r20_pct == null && row.r20 != null) m.r20_pct = row.r20;
    if (m.rs_63 == null && row.rs63 != null) m.rs_63 = row.rs63;
    if (m.atr_pct == null && row.atr_pct != null) m.atr_pct = row.atr_pct;
    if (m.day_pct == null && row.day != null) m.day_pct = row.day;
    if (row.metrics && typeof row.metrics === "object") {{
      if (m.r20_pct == null && row.metrics.r20_pct != null) m.r20_pct = row.metrics.r20_pct;
      if (m.rs_63 == null && row.metrics.rs_63 != null) m.rs_63 = row.metrics.rs_63;
      if (m.atr_pct == null && row.metrics.atr_pct != null) m.atr_pct = row.metrics.atr_pct;
      if (m.day_pct == null && row.metrics.day_pct != null) m.day_pct = row.metrics.day_pct;
    }}
    return card;
  }}
  function rewriteAtr(node, card) {{
    if (!node) return;
    var atr = pickNum((card && card.metrics) || {{}}, ["atr_pct", "atr"]);
    if (atr == null) atr = pickNum(card, ["atr_pct", "atrs", "atr", "ATR", "ATRS"]);
    if (atr == null) return;
    var shown = fmtAtr(atr);
    var els = node.querySelectorAll("span, b, i, em, dt, dd, div, td, th, strong, small, label");
    for (var i = 0; i < els.length; i++) {{
      var el = els[i];
      if (el.closest && el.closest(".fd-paper, .pills, .chips, .badges")) continue;
      var text = String(el.textContent || "").replace(/\s+/g, " ").trim();
      var up = text.toUpperCase();
      if (up === "ATR%" || up === "ATR" || up === "ATRS") {{
        var sib = el.nextElementSibling;
        if (sib) {{ sib.textContent = shown; return; }}
        for (var c = 0; c < el.children.length; c++) {{
          el.children[c].textContent = shown;
          return;
        }}
      }}
      if (up.indexOf("ATR% ") === 0 || up === "ATR%" || up.indexOf("ATRS ") === 0 || up.indexOf("ATR ") === 0) {{
        var lab = up.indexOf("ATR%") === 0 ? "ATR%" : (up.indexOf("ATRS") === 0 ? "ATRS" : "ATR");
        el.textContent = lab + " " + shown;
        return;
      }}
    }}
  }}
  function drillTicker(ticker) {{
    var t = ticker;
    if (!t) return false;
    var fn = window.selectTicker;
    if (typeof fn !== "function") {{
      try {{ if (typeof selectTicker === "function") fn = selectTicker; }} catch (e0) {{ fn = null; }}
    }}
    if (typeof fn === "function") {{
      try {{ fn(t); return true; }} catch (e1) {{}}
    }}
    return false;
  }}
  function bindSelect(node, row, card) {{
    if (!node) return node;
    var t = shortOf((card && (card.t || card.d)) || (row && (row.t || row.ticker)) || node.getAttribute("data-t") || "");
    var full = (card && (card.ticker || card.name)) || (row && row.ticker) || t;
    if (t) node.setAttribute("data-t", t);
    if (full) node.setAttribute("data-ticker", String(full));
    node.onclick = function (ev) {{
      if (ev.target && ev.target.closest && ev.target.closest(".fd-paper, [data-fd-paper-act]")) return;
      if (ev && ev.stopPropagation) ev.stopPropagation();
      if (ev && ev.preventDefault) ev.preventDefault();
      drillTicker(node.getAttribute("data-t") || t || full);
    }};
    return node;
  }}
  function fromHTML(html, row, card) {{
    var wrap = document.createElement("div");
    wrap.innerHTML = String(html || "");
    var node = wrap.firstElementChild;
    if (!node) return null;
    node.classList.remove("hide", "fd-bb-hid", "gics-hid");
    node.removeAttribute("data-fd-bb-dense");
    return bindSelect(node, row, card);
  }}
  function renderRow(row) {{
    row = mergeStats(row || {{}});
    var t = shortOf(row.t || row.ticker || row.d || "");
    if (!t) return null;
    var card = momCardFor(row);
    if (!card) return null;
    var html = callCardHTML(card);
    var node = html ? fromHTML(html, row, card) : null;
    if (!node && typeof window.__FD_RENDER_CARD__ === "function") {{
      try {{ node = window.__FD_RENDER_CARD__(card); }} catch (e0) {{ node = null; }}
      if (node) {{
        node.classList.remove("hide", "fd-bb-hid", "gics-hid");
        bindSelect(node, row, card);
      }}
    }}
    if (!node) return null;
    rewriteAtr(node, card);
    return node;
  }}
  function fillGrid(grid, rows) {{
    if (!grid) return;
    grid.innerHTML = "";
    if (!rows || !rows.length) {{
      var empty = document.createElement("p");
      empty.className = "fd-bb-empty";
      empty.textContent = "No early inflections this Refresh.";
      grid.appendChild(empty);
      return;
    }}
    for (var i = 0; i < rows.length; i++) {{
      var row = rows[i] || {{}};
      if (!shortOf(row.t || row.ticker || row.d || "")) continue;
      var node = renderRow(row);
      if (node) grid.appendChild(node);
    }}
  }}
  function hideLegacy() {{
    var a = $(LEGACY_BO), b = $(LEGACY_BD);
    if (a) {{ a.classList.add("hide"); a.hidden = true; a.innerHTML = ""; }}
    if (b) {{ b.classList.add("hide"); b.hidden = true; b.innerHTML = ""; }}
  }}
  function hideNativeViews() {{
    for (var i = 0; i < NATIVE_VIEWS.length; i++) {{
      var el = $(NATIVE_VIEWS[i]);
      if (el) el.classList.add("hide");
    }}
  }}
  function setOn(btn, on) {{
    if (!btn) return;
    btn.classList.toggle("is-on", !!on);
    btn.classList.toggle("on", !!on);
    btn.setAttribute("aria-pressed", on ? "true" : "false");
  }}
  function navButtons() {{
    return document.querySelectorAll("#topnav .btn, #topnav .nav-btn, .topnav .nav-btn, nav .btn, nav .nav-btn, #fd-nav-breakout, #fd-nav-breakdown, button[data-fd-breakout], button[data-fd-breakdown], button[data-view]");
  }}
  function oursOf(b, kind) {{
    if (!b || !b.getAttribute) return false;
    if (b.closest && b.closest("article.card, #breakout-grid, #breakdown-grid, #view-breakout, #view-breakdown") && !(b.closest("#topnav, nav, .topnav"))) return false;
    var view = (b.getAttribute("data-view") || "");
    return view === kind || (kind === "breakout" && (b.getAttribute("data-fd-breakout") === "1" || b.id === "fd-nav-breakout")) ||
      (kind === "breakdown" && (b.getAttribute("data-fd-breakdown") === "1" || b.id === "fd-nav-breakdown"));
  }}
  function syncNav(kind) {{
    var buttons = navButtons();
    for (var i = 0; i < buttons.length; i++) {{
      var b = buttons[i];
      if (kind === "breakout" || kind === "breakdown") setOn(b, oursOf(b, kind));
      else if (oursOf(b, "breakout") || oursOf(b, "breakdown")) setOn(b, false);
    }}
  }}
  function show(kind) {{
    hideNativeViews();
    hideLegacy();
    var data = db();
    if (kind === "breakout" || kind === "breakdown") {{
      var pane = $(kind === "breakout" ? VIEW_BO : VIEW_BD);
      var grid = $(kind === "breakout" ? GRID_BO : GRID_BD);
      if (pane) pane.classList.remove("hide");
      fillGrid(grid, data[kind] || []);
      document.body.setAttribute("data-view", kind);
      document.body.setAttribute("data-fd-bb", kind);
      syncNav(kind);
      return;
    }}
    document.body.removeAttribute("data-fd-bb");
    syncNav("");
  }}
  window.__FD_BB_SHOW__ = show;
  window.__FD_BB_SYNC_NAV__ = syncNav;
  window.__FD_BB_RENDER_ROW__ = renderRow;
  function isNavControl(el) {{
    if (!el || !el.getAttribute) return false;
    if (el.closest && el.closest("article.card, .card, #breakout-grid, #breakdown-grid, #fd-name-drill")) return false;
    if (el.id === "view-breakout" || el.id === "view-breakdown" || el.id === "breakout-grid" || el.id === "breakdown-grid") return false;
    var tag = (el.tagName || "").toLowerCase();
    var inNav = !!(el.closest && el.closest("#topnav, nav, .topnav"));
    if (tag === "button" || tag === "a" || (el.classList && (el.classList.contains("btn") || el.classList.contains("nav-btn")))) {{
      if (inNav) return true;
      if (el.id === "fd-nav-breakout" || el.id === "fd-nav-breakdown") return true;
      if (el.getAttribute("data-fd-breakout") === "1" || el.getAttribute("data-fd-breakdown") === "1") return true;
      var view = (el.getAttribute("data-view") || "").toLowerCase();
      if (inNav && (view === "breakout" || view === "breakdown" || view)) return true;
    }}
    return false;
  }}
  function kindOf(btn) {{
    if (!isNavControl(btn)) return "";
    var view = (btn.getAttribute("data-view") || "").toLowerCase();
    if (view === "paper" || btn.id === "fd-nav-paper" || btn.getAttribute("data-fd-paper-nav") === "1") return "";
    if (view === "breakout" || btn.getAttribute("data-fd-breakout") === "1" || btn.id === "fd-nav-breakout") return "breakout";
    if (view === "breakdown" || btn.getAttribute("data-fd-breakdown") === "1" || btn.id === "fd-nav-breakdown") return "breakdown";
    var label = (btn.textContent || "").replace(/\s+/g, " ").trim();
    if (label === "Breakout") return "breakout";
    if (label === "Breakdown") return "breakdown";
    if (label === "Paper") return "";
    if (btn.id === "refresh" || btn.id === "options-refresh") return "";
    if (btn.closest && btn.closest("#topnav, nav, .topnav") && (btn.classList.contains("btn") || btn.classList.contains("nav-btn") || view)) return "other";
    if (btn.classList && btn.classList.contains("nav-btn")) return "other";
    return "";
  }}
  window.__FD_BB_KIND_OF__ = kindOf;
  if (window.__FD_BB_CLICK__) {{
    document.removeEventListener("click", window.__FD_BB_CLICK__, true);
  }}
  window.__FD_BB_CLICK__ = function (ev) {{
    if (ev.target && ev.target.closest && ev.target.closest("article.card, .card")) return;
    var t = ev.target && ev.target.closest
      ? ev.target.closest("#topnav .nav-btn, #topnav .btn, nav .nav-btn, nav .btn, .topnav .nav-btn, button[data-view], button[data-fd-breakout], button[data-fd-breakdown], #fd-nav-breakout, #fd-nav-breakdown")
      : ev.target;
    var kind = kindOf(t);
    if (!kind) return;
    if (kind === "breakout" || kind === "breakdown") {{
      ev.preventDefault();
      ev.stopPropagation();
      if (ev.stopImmediatePropagation) ev.stopImmediatePropagation();
      show(kind);
      return;
    }}
    if (kind === "other") show("");
  }};
  document.addEventListener("click", window.__FD_BB_CLICK__, true);
  function installSetViewBridge() {{
    var orig = window.setView;
    if (typeof orig === "function" && orig.__fdBb) orig = orig.__fdBbOrig || orig;
    if (typeof orig !== "function") return;
    var wrapped = function (v) {{
      var kind = String(v || "").toLowerCase();
      if (kind === "breakout" || kind === "breakdown") {{
        show(kind);
        syncNav(kind);
        return;
      }}
      if (kind === "paper" || kind === "experimental") {{
        return orig.apply(this, arguments);
      }}
      show("");
      return orig.apply(this, arguments);
    }};
    wrapped.__fdBb = true;
    wrapped.__fdBbOrig = orig.__fdBbOrig || orig;
    window.setView = wrapped;
  }}
  installSetViewBridge();
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", installSetViewBridge);
}})();
""".strip()


def panes_html(ranked: Mapping[str, Any] | None = None, article_html=None) -> str:
    """Momentum-style view shells. Grids are filled by live ``cardHTML(momCard)``.

    ``ranked`` / ``article_html`` are unused (kept for call-site compatibility).
    Legacy ``#fd-bb-*`` stay empty so old CSS cannot paint stub articles.
    """
    _ = (ranked, article_html)
    return "\n".join(
        [
            f'<div id="{VIEW_BREAKOUT_ID}" class="view-pane hide" data-view="breakout">',
            '  <div class="ph">Breakout</div>',
            f'  <div class="grid dense" id="{GRID_BREAKOUT_ID}"></div>',
            "</div>",
            f'<div id="{VIEW_BREAKDOWN_ID}" class="view-pane hide" data-view="breakdown">',
            '  <div class="ph">Breakdown</div>',
            f'  <div class="grid dense" id="{GRID_BREAKDOWN_ID}"></div>',
            "</div>",
            f'<div id="{PANE_BREAKOUT_ID}" class="fd-bb-pane hide" hidden aria-hidden="true"></div>',
            f'<div id="{PANE_BREAKDOWN_ID}" class="fd-bb-pane hide" hidden aria-hidden="true"></div>',
        ]
    )


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


def _has_nav_button(html_text: str, *, view: str, data_attr: str, btn_id: str) -> bool:
    """True only when an actual ``<button>`` exists — not CSS like ``.nav-btn[data-view="breakout"]``."""
    text = html_text or ""
    patterns = (
        rf'<button\b[^>]*\bdata-view=["\']{re.escape(view)}["\']',
        rf'<button\b[^>]*\b{re.escape(data_attr)}=["\']1["\']',
        rf'<button\b[^>]*\bid=["\']{re.escape(btn_id)}["\']',
    )
    return any(re.search(pat, text, re.I | re.S) for pat in patterns)


def _ensure_nav(html_text: str) -> str:
    has_bo = _has_nav_button(
        html_text, view="breakout", data_attr="data-fd-breakout", btn_id=NAV_BREAKOUT_ID
    )
    has_bd = _has_nav_button(
        html_text, view="breakdown", data_attr="data-fd-breakdown", btn_id=NAV_BREAKDOWN_ID
    )
    if has_bo and has_bd:
        return html_text
    pair = ""
    if not has_bo:
        pair += "\n  " + BTN_BREAKOUT
    if not has_bd:
        pair += "\n  " + BTN_BREAKDOWN
    m = _MOM_DOWN_BTN_RE.search(html_text)
    if m:
        return html_text[: m.end()] + pair + html_text[m.end() :]
    if re.search(r'data-view=["\']mom-down["\']', html_text, re.I):
        return re.sub(
            r'(<button\b(?=[^>]*data-view=["\']mom-down["\'])[^>]*>.*?</button>)',
            lambda mm: mm.group(1) + pair,
            html_text,
            count=1,
            flags=re.I | re.S,
        )
    if "<nav" in html_text.lower():
        return re.sub(r"(<nav\b[^>]*>)", r"\1" + pair, html_text, count=1, flags=re.I)
    return pair + "\n" + html_text


def _patch_setview_allowlist(html_text: str) -> str:
    """Append ``|breakout|breakdown`` to live ``setView`` / ``paintView`` allowlists."""
    text = html_text or ""
    if re.search(
        r"home\|mom-up\|mom-down\|outliers\|options(?:\|sectors)?\|breakout\|breakdown",
        text,
        re.I,
    ):
        return text
    return _ALLOWLIST_RE.sub(lambda m: m.group(0) + "|breakout|breakdown", text)


def _early_return_snippet(marker: str, param: str) -> str:
    """``SHOW`` + live ``syncNav`` + our nav re-assert. No ``paint()``, no legacy panes."""
    return (
        f"{marker}"
        f"if({param}===\"breakout\"||{param}===\"breakdown\"){{"
        f"if(window.__FD_BB_SHOW__)window.__FD_BB_SHOW__({param});"
        f"if(typeof syncNav===\"function\")syncNav();"
        f"if(window.__FD_BB_SYNC_NAV__)window.__FD_BB_SYNC_NAV__({param});"
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
        re.escape(marker) + r'if\((\w+)==="breakout".*?return;\}',
        text,
        re.S,
    )
    if existing:
        return text[: existing.start()] + _early_return_snippet(marker, existing.group(1)) + text[existing.end() :]

    def inject(match: re.Match[str]) -> str:
        head, param = match.group(1), match.group(2)
        return f"{head}{_early_return_snippet(marker, param)}"

    return fn_re.sub(inject, text, count=1)


def _patch_setview_early_return(html_text: str) -> str:
    """Live ``setView(v)`` early-returns for our tabs — ``SHOW`` + ``syncNav``, no ``paint()``."""
    return _replace_or_inject_early_return(html_text, marker=SETVIEW_MARKER, fn_re=_SETVIEW_FN_RE)


def _patch_paintview_early_return(html_text: str) -> str:
    """Live ``paintView(v)`` allowlist is patched separately; also early-return to ``SHOW``."""
    return _replace_or_inject_early_return(html_text, marker=PAINTVIEW_MARKER, fn_re=_PAINTVIEW_FN_RE)


def _patch_hideall_panes(html_text: str) -> str:
    """``hideAllPanes`` must hide ``#view-breakout`` / ``#view-breakdown`` so cards cannot bleed."""
    text = html_text or ""
    if HIDEALL_MARKER in text:
        return text
    snippet = (
        f"{HIDEALL_MARKER}"
        '["view-breakout","view-breakdown","view-experimental","view-paper"].forEach(function(id){'
        'var el=document.getElementById(id);if(el)el.classList.add("hide");});'
    )
    return _HIDEALL_FN_RE.sub(lambda m: m.group(1) + snippet, text, count=1)


def _patch_view_id_lists(html_text: str) -> str:
    """Append our view ids to live hideAllPanes arrays / querySelectorAll lists."""
    text = html_text or ""
    text = _VIEW_ID_ARRAY_RE.sub(
        r'\1,"view-breakout","view-breakdown","view-experimental"\2',
        text,
    )
    text = _VIEW_SEL_RE.sub(
        r"\1, #view-breakout, #view-breakdown, #view-experimental",
        text,
    )
    return text


def _patch_setview(html_text: str) -> str:
    text = _patch_setview_allowlist(html_text)
    text = _patch_setview_early_return(text)
    text = _patch_paintview_early_return(text)
    text = _patch_hideall_panes(text)
    return _patch_view_id_lists(text)


def _find_tag_span(html_text: str, elem_id: str) -> tuple[int, int] | None:
    """``[start, end)`` of ``<div id=elem_id>…</div>`` with nested divs balanced."""
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


def _legacy_empty_html() -> tuple[str, str]:
    return (
        f'<div id="{PANE_BREAKOUT_ID}" class="fd-bb-pane hide" hidden aria-hidden="true"></div>',
        f'<div id="{PANE_BREAKDOWN_ID}" class="fd-bb-pane hide" hidden aria-hidden="true"></div>',
    )


def _ensure_legacy_empty(html_text: str) -> str:
    empty_bo, empty_bd = _legacy_empty_html()
    text = html_text
    for elem_id, empty in ((PANE_BREAKOUT_ID, empty_bo), (PANE_BREAKDOWN_ID, empty_bd)):
        span = _find_tag_span(text, elem_id)
        if span:
            text = text[: span[0]] + empty + text[span[1] :]
            continue
        anchor = _find_tag_span(text, VIEW_BREAKDOWN_ID) or _find_tag_span(text, VIEW_BREAKOUT_ID)
        if anchor:
            text = text[: anchor[1]] + "\n" + empty + text[anchor[1] :]
        else:
            text = text + "\n" + empty
    return text


def _insert_host(html_text: str, host: str) -> str:
    for elem_id in ("view-mom-down", "view-mom-up", "gics-filter-strip", "fd-book-delta"):
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
    """Keyed on ``#view-breakout``. ``replace=False`` keeps shells; always empties legacy stubs."""
    _ = ranked
    has_view = _find_tag_span(html_text, VIEW_BREAKOUT_ID) is not None
    if has_view and not replace:
        return _ensure_legacy_empty(html_text)
    host = panes_html(ranked)
    found: list[tuple[int, int]] = []
    for elem_id in (VIEW_BREAKOUT_ID, VIEW_BREAKDOWN_ID, PANE_BREAKOUT_ID, PANE_BREAKDOWN_ID):
        span = _find_tag_span(html_text, elem_id)
        if span:
            found.append(span)
    if found:
        found.sort()
        insert_at = found[0][0]
        for start, end in sorted(found, key=lambda s: s[0], reverse=True):
            while end < len(html_text) and html_text[end] in " \t\r\n":
                end += 1
            html_text = html_text[:start] + html_text[end:]
        return html_text[:insert_at] + host + "\n" + html_text[insert_at:]
    return _insert_host(html_text, host)


def _ensure_db(
    html_text: str,
    ranked: Mapping[str, Any] | None,
    live_map: Mapping[str, Any] | None = None,
) -> str:
    tag = embed_db(ranked, live_map=live_map)
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


def _enrich_existing_db(html_text: str, live_map: Mapping[str, Any] | None) -> str:
    """Fill null metrics on an existing ``#fd-breakout-db`` from the live MOM map."""
    if not live_map:
        return html_text
    match = re.search(
        rf'<script\b[^>]*id=["\']{DB_SCRIPT_ID}["\'][^>]*>(.*?)</script>',
        html_text or "",
        re.I | re.S,
    )
    if not match:
        return html_text
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return html_text
    if not isinstance(data, dict):
        return html_text
    ranked = enrich_ranked_from_map(
        {
            "breakout": data.get("breakout") or [],
            "breakdown": data.get("breakdown") or [],
            "cap": data.get("cap", RANK_CAP),
            "asof": data.get("asof"),
        },
        live_map,
    )
    tag = embed_db(ranked, live_map=live_map)
    return html_text[: match.start()] + tag + html_text[match.end() :]


def _replace_id_script(html_text: str, script_id: str, script: str) -> str:
    """Drop every copy of ``script_id`` then inject ``script`` before ``</body>``.

    Replaces even when an older embed used a different attribute order, and
    even when ``ranked is None``. Duplicate leftover tags cannot keep an old
    ``__FD_BB_BOUND__`` IIFE in front of the new one.
    """
    text = re.sub(
        rf'<script\b[^>]*\bid=["\']{script_id}["\'][^>]*>.*?</script>\s*',
        "",
        html_text or "",
        flags=re.I | re.S,
    )
    if "</body>" in text:
        return text.replace("</body>", script + "</body>", 1)
    return text + script


def _ensure_js(html_text: str) -> str:
    script = f'<script id="{JS_SCRIPT_ID}">\n{strip_js()}\n</script>\n'
    return _replace_id_script(html_text, JS_SCRIPT_ID, script)


def ensure_embedded(html_text: str, ranked: Mapping[str, Any] | None = None) -> str:
    """Nav buttons + view shells + filled db + JS. Safe on live ~2.7–4.8MB HTML.

    ``ranked=None`` must not wipe ``#fd-breakout-db``. View shells stay; legacy
    ``#fd-bb-*`` are emptied. JS is **always** replaced (never skipped when
    ``ranked is None``) so a recopy refreshes ``renderRow``. Live MOM card
    ``metrics.*`` are scraped from the HTML (no ``fd-mom-db``) and stamped
    onto BB rows at embed time.
    """
    text = html_text or ""
    live_map = extract_live_metrics_map(text)
    # Buttons before CSS: CSS selectors contain ``data-view="breakout"`` text.
    text = _ensure_nav(text)
    text = _ensure_css(text)
    text = _patch_setview(text)
    if ranked is not None:
        ranked = enrich_ranked_from_map(ranked, live_map)
        text = _ensure_panes(text, ranked, replace=True)
        text = _ensure_db(text, ranked, live_map=live_map)
    else:
        text = _ensure_panes(text, {"breakout": [], "breakdown": []}, replace=False)
        if not re.search(rf'id=["\']{DB_SCRIPT_ID}["\']', text, re.I):
            text = _ensure_db(text, {"breakout": [], "breakdown": []}, live_map=live_map)
        elif live_map:
            text = _enrich_existing_db(text, live_map)
    text = _ensure_js(text)
    return text
