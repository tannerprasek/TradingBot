"""Breakout / Breakdown ranking for Factor Desk home tabs.

Breakout = emerging strength (mid scores climbing), **not** already-maxed MOM.
Breakdown = mirror: mid-weak scores deteriorating, **not** already dead.

Tune every threshold in the constants block below. Formula is documented in
``docs/BREAKOUT-BREAKDOWN.md``. No extra DAPI stages.
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

# Score bands on the 0–13 MOM rank (card ``score`` / ``mom_score``).
BREAKOUT_SCORE_MIN = 6.0
BREAKOUT_SCORE_MAX = 11.0  # 12–13 already there
BREAKOUT_SWEET_LOW = 7.0
BREAKOUT_SWEET_HIGH = 10.0
WEAK_ACCEL_MIN_SCORE = 4.0  # ≤5 still weak unless accelerating hard
WEAK_ACCEL_MIN_DELTA = 2.0

BREAKDOWN_SCORE_MIN = 2.0  # 0–1 already dead
BREAKDOWN_SCORE_MAX = 7.0
BREAKDOWN_SWEET_LOW = 3.0
BREAKDOWN_SWEET_HIGH = 6.0

# Rising / falling lookback in **trading days**. Prefer 5–10d; 7 is the default.
DELTA_LOOKBACK_PREF = 7
DELTA_LOOKBACK_MIN = 5
DELTA_LOOKBACK_MAX = 10
DELTA_CLIP = 4.0

# Streak: short-to-medium just-flipped/climbing beats a 100d already-baked run.
STREAK_FRESH_MIN = 2
STREAK_FRESH_MAX = 20
STREAK_PEAK = 12
STREAK_BAKED = 100

# Accel: last 3d Δ vs prior 3d Δ.
ACCEL_SHORT = 3

# Composite weights.
W_BAND = 3.0
W_DELTA = 1.6
W_STREAK = 2.0
W_BOOST_FLAGS = 0.8
W_BOOST_HOP_LEADER = 0.5
W_BOOST_OPT_SPIKE = 0.6
W_BOOST_ACCEL = 0.5
W_BOOST_OUTLIER = 0.4
W_WEAK_ACCEL = 1.0  # names still ≤5 that only qualify via hard Δ

# Discerning cap: few cards, ranked by breakout_score / breakdown_score.
RANK_CAP = 12
MIN_COMPOSITE = 1.25  # do not fill the cap with junk

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
JS_VER = "pr17-dense-nocardhtml"
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
    "view-paper",
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
    """Percent return over ``n`` trading days (needs n+1 closes)."""
    if n <= 0 or len(closes) < n + 1:
        return None
    start = float(closes[-(n + 1)])
    last = float(closes[-1])
    if start == 0:
        return None
    return _round_stat(100.0 * (last / start - 1.0))


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
    """Day / R20 / RS63 / ATR% from existing card fields or price history."""
    src = card if isinstance(card, Mapping) else {}
    aliased = dict(src)
    card_render.alias_stats(aliased)
    existing = {
        "day": _num_stat(aliased, card_render.DAY_KEYS),
        "r20": _num_stat(aliased, card_render.R20_KEYS),
        "rs63": _num_stat(aliased, card_render.RS63_KEYS),
        "atr_pct": _num_stat(aliased, card_render.ATR_KEYS),
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
            rs63 = _round_stat(stock63 - bench63)
        else:
            rs63 = stock63
    return {"day": day, "r20": r20, "rs63": rs63, "atr_pct": atr}


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


def delta_over(
    series: Sequence[tuple[Any, float]] | None,
    lookback: int = DELTA_LOOKBACK_PREF,
) -> tuple[float | None, int]:
    """``(today - score N trading days ago, N used)``. Prefers 5–10d."""
    rows = list(series or [])
    if len(rows) < 2:
        return None, 0
    today = float(rows[-1][1])
    n = max(DELTA_LOOKBACK_MIN, min(int(lookback), DELTA_LOOKBACK_MAX))
    if len(rows) - 1 < DELTA_LOOKBACK_MIN:
        n = len(rows) - 1
        return today - float(rows[0][1]), n
    idx = max(0, len(rows) - 1 - n)
    used = (len(rows) - 1) - idx
    return today - float(rows[idx][1]), used


def accel_boost(series: Sequence[tuple[Any, float]] | None, *, up: bool) -> float:
    rows = list(series or [])
    need = ACCEL_SHORT * 2 + 1
    if len(rows) < need:
        return 0.0
    recent = float(rows[-1][1]) - float(rows[-1 - ACCEL_SHORT][1])
    prior = float(rows[-1 - ACCEL_SHORT][1]) - float(rows[-1 - 2 * ACCEL_SHORT][1])
    if up and recent > prior and recent > 0:
        return W_BOOST_ACCEL
    if (not up) and recent < prior and recent < 0:
        return W_BOOST_ACCEL
    return 0.0


def band_fit(score: float, lo: float, hi: float, sweet_lo: float, sweet_hi: float) -> float:
    """1.0 in the sweet range, linear falloff to 0 just outside ``[lo, hi]``."""
    if score < lo - 0.75 or score > hi + 0.75:
        return 0.0
    if sweet_lo <= score <= sweet_hi:
        return 1.0
    if lo <= score < sweet_lo:
        span = max(sweet_lo - lo, 0.01)
        return 0.45 + 0.55 * (score - lo) / span
    if sweet_hi < score <= hi:
        span = max(hi - sweet_hi, 0.01)
        return 0.45 + 0.55 * (hi - score) / span
    if lo - 0.75 <= score < lo:
        return 0.25 * (score - (lo - 0.75)) / 0.75
    return 0.25 * ((hi + 0.75) - score) / 0.75


def streak_freshness(streak: int, side: str | None, want: str) -> float:
    """1.0 for a short-to-medium run on ``want``; ~0 for a 100d baked run."""
    if side != want:
        return 0.0
    n = max(int(streak or 0), 0)
    if n <= 0:
        return 0.0
    if n < STREAK_FRESH_MIN:
        return 0.35 * n / STREAK_FRESH_MIN
    if n <= STREAK_PEAK:
        return 1.0
    if n <= STREAK_FRESH_MAX:
        return 0.75
    if n >= STREAK_BAKED:
        return 0.05
    span = float(STREAK_BAKED - STREAK_FRESH_MAX)
    return max(0.05, 0.75 * (1.0 - (n - STREAK_FRESH_MAX) / span))


def field_boosts(card: Mapping[str, Any] | None) -> float:
    boost = 0.0
    lists = card_lists(card)
    if "FLAGS" in lists:
        boost += W_BOOST_FLAGS
    if has_hop_leader(card):
        boost += W_BOOST_HOP_LEADER
    if has_opt_spike(card):
        boost += W_BOOST_OPT_SPIKE
    if is_outlier_newbie(card):
        boost += W_BOOST_OUTLIER
    return boost


def _clip(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def breakout_eligible(score: float | None, delta: float | None) -> bool:
    if score is None:
        return False
    if score > BREAKOUT_SCORE_MAX:
        return False
    if delta is None or delta <= 0:
        return False
    if BREAKOUT_SCORE_MIN <= score <= BREAKOUT_SCORE_MAX:
        return True
    if score >= WEAK_ACCEL_MIN_SCORE and delta >= WEAK_ACCEL_MIN_DELTA:
        return True
    return False


def breakdown_eligible(score: float | None, delta: float | None) -> bool:
    if score is None:
        return False
    if score < BREAKDOWN_SCORE_MIN:
        return False
    if delta is None or delta >= 0:
        return False
    if BREAKDOWN_SCORE_MIN <= score <= BREAKDOWN_SCORE_MAX:
        return True
    return False


def _why(*, score: float, delta: float, used: int, streak: int, side: str | None, kind: str) -> str:
    arrow = "+" if delta >= 0 else ""
    side_bit = f" · {side or '—'} {int(streak)}d"
    return f"{kind} {score:g} · {arrow}{delta:g} / {used}d{side_bit}"


def score_one(
    card: Mapping[str, Any],
    hist: Mapping[str, Any] | None = None,
    *,
    kind: str,
) -> dict[str, Any] | None:
    """Return a ranked row or ``None`` if not eligible for ``kind``."""
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
    delta, used = delta_over(series)
    if kind == "breakout":
        if not breakout_eligible(score, delta):
            return None
        assert score is not None and delta is not None
        band = band_fit(score, BREAKOUT_SCORE_MIN, BREAKOUT_SCORE_MAX, BREAKOUT_SWEET_LOW, BREAKOUT_SWEET_HIGH)
        streak = int(card.get("mom_streak") or 0)
        side = card.get("mom_streak_side")
        if not side:
            streak, side = mom_streak.streak_from_dated(series)
        fresh = streak_freshness(streak, side, "above")
        weak_only = score < BREAKOUT_SCORE_MIN
        composite = (
            W_BAND * band
            + W_DELTA * (_clip(delta, -DELTA_CLIP, DELTA_CLIP) / DELTA_CLIP)
            + W_STREAK * fresh
            + field_boosts(card)
            + accel_boost(series, up=True)
            + (W_WEAK_ACCEL if weak_only else 0.0)
        )
        if composite < MIN_COMPOSITE:
            return None
        return {
            "t": _short(ticker),
            "ticker": ticker,
            "score": score,
            "delta": round(delta, 4),
            "lookback": used,
            "streak": streak,
            "side": side,
            "label": card.get("mom_streak_label") or mom_streak.tag_label(streak, side),
            "breakout_score": round(composite, 4),
            "why": _why(score=score, delta=delta, used=used, streak=streak, side=side, kind="band"),
        }
    if kind == "breakdown":
        if not breakdown_eligible(score, delta):
            return None
        assert score is not None and delta is not None
        band = band_fit(score, BREAKDOWN_SCORE_MIN, BREAKDOWN_SCORE_MAX, BREAKDOWN_SWEET_LOW, BREAKDOWN_SWEET_HIGH)
        streak = int(card.get("mom_streak") or 0)
        side = card.get("mom_streak_side")
        if not side:
            streak, side = mom_streak.streak_from_dated(series)
        fresh = streak_freshness(streak, side, "below")
        composite = (
            W_BAND * band
            + W_DELTA * (_clip(-delta, -DELTA_CLIP, DELTA_CLIP) / DELTA_CLIP)
            + W_STREAK * fresh
            + field_boosts(card)
            + accel_boost(series, up=False)
        )
        if composite < MIN_COMPOSITE:
            return None
        return {
            "t": _short(ticker),
            "ticker": ticker,
            "score": score,
            "delta": round(delta, 4),
            "lookback": used,
            "streak": streak,
            "side": side,
            "label": card.get("mom_streak_label") or mom_streak.tag_label(streak, side),
            "breakdown_score": round(composite, 4),
            "why": _why(score=score, delta=delta, used=used, streak=streak, side=side, kind="band"),
        }
    return None


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
        row = score_one(card, hist, kind=kind)
        if row:
            row["_card"] = card
            attach_px_stats(row, card, panel, bench=bench)
            rows.append(row)
    rows = _dedupe_best(rows, score_key)
    rows.sort(key=lambda r: float(r.get(score_key) or 0), reverse=True)
    return rows[: max(0, int(cap))]


def rank_book(
    cards: Iterable[Mapping[str, Any]] | None,
    hist: Mapping[str, Any] | None = None,
    *,
    cap: int = RANK_CAP,
    root: Any = None,
    prices: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Select at most ``cap`` (8–12) names per side, ranked by documented score."""
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


def slim_payload(ranked: Mapping[str, Any] | None) -> dict[str, Any]:
    """JSON-safe db. Top-level Day/R20/RS63/ATR% plus a slim MOM card (no series)."""
    ranked = ranked or {}

    def slim(rows: Any, score_key: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for row in rows or []:
            if not isinstance(row, Mapping):
                continue
            src = row.get("_card") if isinstance(row.get("_card"), Mapping) else None
            stats = px_stats(src, ticker=str(row.get("ticker") or row.get("t") or ""))
            for key in ("day", "r20", "rs63", "atr_pct"):
                if row.get(key) is not None and row.get(key) != "":
                    stats[key] = row.get(key)
            merged = dict(row)
            merged.update({k: v for k, v in stats.items() if v is not None})
            card = card_render.portable_card(src, merged) or {}
            for key, value in stats.items():
                if value is not None:
                    card.setdefault(key, value)
            payload = {
                "t": row.get("t") or _short(str(row.get("ticker") or "")),
                "ticker": row.get("ticker"),
                "score": row.get("score"),
                "delta": row.get("delta"),
                "lookback": row.get("lookback"),
                "streak": row.get("streak"),
                "side": row.get("side"),
                "label": row.get("label"),
                score_key: row.get(score_key),
                "why": row.get("why"),
                "day": stats.get("day"),
                "r20": stats.get("r20"),
                "rs63": stats.get("rs63"),
                "atr_pct": stats.get("atr_pct"),
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


def embed_db(ranked: Mapping[str, Any] | None) -> str:
    blob = json.dumps(slim_payload(ranked), separators=(",", ":"), ensure_ascii=True, default=str)
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
    """Fill #breakout-grid / #breakdown-grid with dense MOM-style cards.

    Tabs only choose which rows to show. Markup comes from
    ``window.__FD_RENDER_ROW__`` when that path does **not** paint a digest
    matrix. Breakout **never** calls live ``cardHTML`` — that fallback is
    what paints the 12-label gray ghost. Missing portable render → dense
    ticker / score / Day / R20 / RS63 / ATR% card. Capture-phase click
    stops the live topnav listener. ``show`` is ``window.__FD_BB_SHOW__``.
    """
    view_ids = json.dumps(list(NATIVE_VIEW_IDS))
    return rf"""
(function () {{
  var BB_VER = "{JS_VER}";
  if (window.__FD_BB_BOUND__ === BB_VER) return;
  window.__FD_BB_BOUND__ = BB_VER;
  window.__FD_BB_NO_CARDHTML_FALLBACK__ = true;
  window.__FD_BB_PORTABLE_FIX__ = true;
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
  function fmt(v) {{
    if (v == null || v === "") return "—";
    if (typeof v === "number" && isFinite(v)) return String(v);
    var s = String(v).trim();
    if (!s || s === "-" || s === "—" || s === "–") return "—";
    return s;
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
    return Math.round((100 * (b / a - 1)) * 100) / 100;
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
  function mergeStats(row) {{
    row = row || {{}};
    var card = (row.card && typeof row.card === "object") ? row.card : {{}};
    var t = row.t || row.ticker || card.t || card.ticker || "";
    var closes = lookupPx(t);
    var bench = benchCloses();
    var computed = {{
      day: retN(closes, 1),
      r20: retN(closes, 20),
      rs63: (function () {{
        var s = retN(closes, 63);
        var b = retN(bench, 63);
        if (s != null && b != null) return Math.round((s - b) * 100) / 100;
        return s;
      }})(),
      atr_pct: atrPct(closes, 14)
    }};
    var dayKeys = ["day","Day","d1","ret_1d","r1","R1"];
    var r20Keys = ["r20","R20","ret20","ret_20","ret_20d","RET_20D"];
    var rsKeys = ["rs63","RS63","rs_63","rel_63","RS_63"];
    var atrKeys = ["atr_pct","atrpct","ATR_PCT","atrs","ATRS","atr","ATR"];
    function take(keys, fallback) {{
      var v = pickNum(row, keys);
      if (v == null) v = pickNum(card, keys);
      if (v == null) v = fallback;
      return v;
    }}
    row.day = take(dayKeys, computed.day);
    row.r20 = take(r20Keys, computed.r20);
    row.rs63 = take(rsKeys, computed.rs63);
    row.atr_pct = take(atrKeys, computed.atr_pct);
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
  function pillsHTML(pills) {{
    if (!Array.isArray(pills)) return "";
    var bits = [];
    for (var i = 0; i < pills.length; i++) {{
      var p = pills[i];
      if (!p || !p.label) continue;
      bits.push(
        '<span class="badge spike-chip ' + esc(p.cls || "") + '" data-key="' + esc(p.key || "") + '"' +
        (p.title ? ' title="' + esc(p.title) + '"' : "") + ">" + esc(p.label) + "</span>"
      );
    }}
    return bits.join("");
  }}
  function denseHTML(row) {{
    row = mergeStats(row || {{}});
    var card = row.card || {{}};
    var t = esc(shortOf(card.d || card.t || row.t || row.ticker || ""));
    var score = row.score != null ? row.score : (card.score != null ? card.score : (card.mom_score != null ? card.mom_score : ""));
    var pills = Array.isArray(row.pills) && row.pills.length ? row.pills : (card.enrich_pills || []);
    return '<article class="card fd-card" data-t="' + t + '" data-ticker="' + esc(card.ticker || t) + '" data-fd-bb-dense="1">' +
      "<header><h2>" + t + '</h2><span class="sc">' + esc(String(score)) + "</span>" +
      '<div class="pills">' + pillsHTML(pills) + "</div></header>" +
      '<div class="stats">' +
        "<span>Day " + esc(fmt(row.day)) + "</span>" +
        "<span>R20 " + esc(fmt(row.r20)) + "</span>" +
        "<span>RS63 " + esc(fmt(row.rs63)) + "</span>" +
        "<span>ATR% " + esc(fmt(row.atr_pct)) + "</span>" +
      "</div></article>";
  }}
  function hasGhostMatrix(node) {{
    if (!node || !node.querySelectorAll) return false;
    var n = 0;
    var els = node.querySelectorAll("div, span, td, li, b, i, small");
    for (var i = 0; i < els.length; i++) {{
      var el = els[i];
      if (el.classList && (el.classList.contains("badge") || el.classList.contains("spike-chip"))) continue;
      var own = "";
      for (var c = el.firstChild; c; c = c.nextSibling) {{
        if (c.nodeType === 3) own += c.textContent;
      }}
      var lab = String(own || "").replace(/\s+/g, " ").trim().toUpperCase();
      if (TAG_SET[lab]) n++;
    }}
    return n >= 6;
  }}
  function bindSelect(node, row, card) {{
    if (!node || node.__fdBbClick) return node;
    node.__fdBbClick = true;
    node.addEventListener("click", function (ev) {{
      if (ev.target && ev.target.closest && ev.target.closest(".fd-paper, [data-fd-paper-act]")) return;
      var sel = window.selectTicker || (typeof selectTicker === "function" ? selectTicker : null);
      if (sel) sel((card && (card.t || card.d || card.ticker)) || (row && (row.t || row.ticker)));
    }});
    return node;
  }}
  function fromHTML(html, row, card) {{
    var wrap = document.createElement("div");
    wrap.innerHTML = String(html || "");
    var node = wrap.firstElementChild;
    if (!node) return null;
    node.classList.remove("hide", "fd-bb-hid", "gics-hid");
    return bindSelect(node, row, card);
  }}
  function tryPortable(row) {{
    if (typeof window.__FD_RENDER_ROW__ === "function") {{
      try {{
        var node = window.__FD_RENDER_ROW__(row);
        if (node && !hasGhostMatrix(node)) {{
          node.classList.remove("hide", "fd-bb-hid", "gics-hid");
          return node;
        }}
      }} catch (e0) {{}}
    }}
    var render = window.__FD_RENDER_CARD__;
    var norm = window.__FD_NORMALIZE_CARD__;
    if (typeof render === "function") {{
      try {{
        var card = typeof norm === "function" ? norm(row.card || null, row) : (row.card || row);
        var painted = render(card);
        if (painted && !hasGhostMatrix(painted)) {{
          painted.classList.remove("hide", "fd-bb-hid", "gics-hid");
          return painted;
        }}
      }} catch (e1) {{}}
    }}
    return null;
  }}
  function renderRow(row) {{
    row = mergeStats(row || {{}});
    var node = tryPortable(row);
    if (node) return node;
    return fromHTML(denseHTML(row), row, row.card || row);
  }}
  function fillGrid(grid, rows) {{
    if (!grid) return;
    grid.innerHTML = "";
    if (!rows || !rows.length) {{
      var empty = document.createElement("p");
      empty.className = "fd-bb-empty";
      empty.textContent = "No names this Refresh — mid-score climbers / crackers only.";
      grid.appendChild(empty);
      return;
    }}
    for (var i = 0; i < rows.length; i++) {{
      var node = renderRow(rows[i] || {{}});
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
    return document.querySelectorAll("#topnav .btn, #topnav .nav-btn, .topnav .nav-btn, nav .btn, nav .nav-btn, [data-view], [data-fd-breakout], [data-fd-breakdown]");
  }}
  function oursOf(b, kind) {{
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
  function kindOf(btn) {{
    if (!btn || !btn.getAttribute) return "";
    var view = (btn.getAttribute("data-view") || "").toLowerCase();
    if (view === "breakout" || btn.getAttribute("data-fd-breakout") === "1" || btn.id === "fd-nav-breakout") return "breakout";
    if (view === "breakdown" || btn.getAttribute("data-fd-breakdown") === "1" || btn.id === "fd-nav-breakdown") return "breakdown";
    var label = (btn.textContent || "").replace(/\s+/g, " ").trim();
    if (label === "Breakout") return "breakout";
    if (label === "Breakdown") return "breakdown";
    if (btn.id === "refresh" || btn.id === "options-refresh") return "";
    if (btn.closest && btn.closest("#topnav, nav, .topnav") && (btn.classList.contains("btn") || btn.classList.contains("nav-btn") || view)) return "other";
    if (btn.classList && (btn.classList.contains("nav-btn") || view)) return "other";
    return "";
  }}
  if (window.__FD_BB_CLICK__) {{
    document.removeEventListener("click", window.__FD_BB_CLICK__, true);
  }}
  window.__FD_BB_CLICK__ = function (ev) {{
    var t = ev.target && ev.target.closest ? ev.target.closest("button, [data-view], [data-fd-breakout], [data-fd-breakdown]") : ev.target;
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
    """Momentum-style view shells. Grids are filled by ``__FD_RENDER_ROW__``.

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
    ``ranked is None``) so a recopy refreshes ``renderRow``. Always pass
    ``rank_book(...)`` on a live write so stats stay filled.
    """
    text = html_text or ""
    # Buttons before CSS: CSS selectors contain ``data-view="breakout"`` text.
    text = _ensure_nav(text)
    text = _ensure_css(text)
    text = _patch_setview(text)
    if ranked is not None:
        text = _ensure_panes(text, ranked, replace=True)
        text = _ensure_db(text, ranked)
    else:
        text = _ensure_panes(text, {"breakout": [], "breakdown": []}, replace=False)
        if not re.search(rf'id=["\']{DB_SCRIPT_ID}["\']', text, re.I):
            text = _ensure_db(text, {"breakout": [], "breakdown": []})
    text = _ensure_js(text)
    return text
