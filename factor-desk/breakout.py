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

DB_SCRIPT_ID = "fd-breakout-db"
JS_SCRIPT_ID = "fd-breakout-js"
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
) -> list[dict[str, Any]]:
    score_key = "breakout_score" if kind == "breakout" else "breakdown_score"
    rows: list[dict[str, Any]] = []
    for card in cards or []:
        if not isinstance(card, Mapping):
            continue
        row = score_one(card, hist, kind=kind)
        if row:
            row["_card"] = card
            rows.append(row)
    rows = _dedupe_best(rows, score_key)
    rows.sort(key=lambda r: float(r.get(score_key) or 0), reverse=True)
    return rows[: max(0, int(cap))]


def rank_book(
    cards: Iterable[Mapping[str, Any]] | None,
    hist: Mapping[str, Any] | None = None,
    *,
    cap: int = RANK_CAP,
) -> dict[str, Any]:
    """Select at most ``cap`` (8–12) names per side, ranked by documented score."""
    src = [c for c in (cards or []) if isinstance(c, Mapping)]
    breakout = rank_side(src, hist, kind="breakout", cap=cap)
    breakdown = rank_side(src, hist, kind="breakdown", cap=cap)
    return {
        "breakout": breakout,
        "breakdown": breakdown,
        "cap": cap,
        "asof": None if not hist else hist.get("asof"),
    }


def slim_payload(ranked: Mapping[str, Any] | None) -> dict[str, Any]:
    """JSON-safe db (no card objects)."""
    ranked = ranked or {}

    def slim(rows: Any, score_key: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for row in rows or []:
            if not isinstance(row, Mapping):
                continue
            out.append(
                {
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
                }
            )
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
    """Fill #breakout-grid / #breakdown-grid with live ``cardHTML`` (MOM chrome).

    Capture-phase click stops the live topnav listener. ``show`` is
    ``window.__FD_BB_SHOW__`` so a patched live ``setView`` early-returns
    without ``paint()`` and without toggling legacy ``#fd-bb-*`` panes.
    """
    view_ids = json.dumps(list(NATIVE_VIEW_IDS))
    return rf"""
(function () {{
  if (window.__FD_BB_BOUND__) return;
  window.__FD_BB_BOUND__ = true;
  var DB_ID = "fd-breakout-db";
  var VIEW_BO = "view-breakout";
  var VIEW_BD = "view-breakdown";
  var GRID_BO = "breakout-grid";
  var GRID_BD = "breakdown-grid";
  var LEGACY_BO = "fd-bb-breakout";
  var LEGACY_BD = "fd-bb-breakdown";
  var NATIVE_VIEWS = {view_ids};

  function $(id) {{ return document.getElementById(id); }}
  function db() {{
    var el = $(DB_ID);
    if (!el) return {{ breakout: [], breakdown: [] }};
    try {{ return JSON.parse(el.textContent || "{{}}") || {{ breakout: [], breakdown: [] }}; }}
    catch (e) {{ return {{ breakout: [], breakdown: [] }}; }}
  }}
  function shortOf(t) {{ return String(t || "").trim().split(/\s+/)[0].toUpperCase(); }}
  function momCards() {{
    var mom = window.MOM || {{}};
    if (Array.isArray(mom.cards)) return mom.cards; // window.MOM.cards
    if (Array.isArray(mom.up) && Array.isArray(mom.down)) return mom.up.concat(mom.down);
    if (Array.isArray(window.MOM_CARDS)) return window.MOM_CARDS;
    return [];
  }}
  function findMomCard(ticker) {{
    var want = shortOf(ticker);
    var cards = momCards();
    for (var i = 0; i < cards.length; i++) {{
      var c = cards[i] || {{}};
      var t = shortOf(c.t || c.ticker || c.name || c.symbol || "");
      if (t && t === want) return c;
    }}
    return null;
  }}
  function withPills(card, row) {{
    row = row || {{}};
    var out = {{}};
    if (card) {{ for (var k in card) out[k] = card[k]; }}
    var display = shortOf(out.d || out.t || out.ticker || out.name || out.symbol || row.t || row.ticker || row.d || "");
    if (display) {{
      if (!out.t) out.t = display;
      if (!out.d) out.d = display;
      if (!out.name) out.name = display;
      if (!out.ticker) out.ticker = row.ticker || out.ticker || display;
    }}
    if (out.score == null && row.score != null) out.score = row.score;
    if (out.mom_score == null && row.score != null) out.mom_score = row.score;
    var pills = Array.isArray(out.enrich_pills) ? out.enrich_pills.slice() : [];
    pills = pills.filter(function (p) {{ return p && p.key !== "fd-bb"; }});
    if (row.why) {{
      pills.push({{ key: "fd-bb", label: String(row.why), cls: "fd-bb-why", title: String(row.why) }});
    }}
    if (row.label && !pills.some(function (p) {{ return p && p.key === "mom-streak"; }})) {{
      var side = row.side || "";
      pills.push({{
        key: "mom-streak",
        label: String(row.label),
        cls: side === "below" ? "mom-streak-down" : (side === "above" ? "mom-streak-up" : "mom-streak"),
        title: String(row.label)
      }});
    }}
    out.enrich_pills = pills;
    return out;
  }}
  function htmlFn() {{
    if (typeof window.cardHTML === "function") return window.cardHTML;
    if (typeof cardHTML === "function") return cardHTML;
    return null;
  }}
  function selectFn() {{
    if (typeof window.selectTicker === "function") return window.selectTicker;
    if (typeof selectTicker === "function") return selectTicker;
    return null;
  }}
  function renderRow(row) {{
    row = row || {{}};
    var fn = htmlFn();
    if (!fn) return null;
    var card = withPills(findMomCard(row.ticker || row.t), row);
    var display = shortOf(card.d || card.t || card.ticker || card.name || row.t || row.ticker || "");
    if (!display) return null;
    card.d = card.d || display;
    card.t = card.t || display;
    card.name = card.name || display;
    if (!card.ticker) card.ticker = row.ticker || display;
    var wrap = document.createElement("div");
    wrap.innerHTML = fn(card);
    var node = wrap.firstElementChild;
    if (!node) return null;
    node.classList.remove("hide", "fd-bb-hid", "gics-hid");
    node.addEventListener("click", function () {{
      var sel = selectFn();
      if (sel) sel(card.t || card.d || card.ticker || row.t);
    }});
    return node;
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
  document.addEventListener("click", function (ev) {{
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
  }}, true);
  function installSetViewBridge() {{
    var orig = window.setView;
    if (typeof orig !== "function" || orig.__fdBb) return;
    window.setView = function (v) {{
      var kind = String(v || "").toLowerCase();
      if (kind === "breakout" || kind === "breakdown") {{
        show(kind);
        syncNav(kind);
        return;
      }}
      show("");
      return orig.apply(this, arguments);
    }};
    window.setView.__fdBb = true;
  }}
  installSetViewBridge();
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", installSetViewBridge);
}})();
""".strip()


def panes_html(ranked: Mapping[str, Any] | None = None, article_html=None) -> str:
    """Momentum-style view shells. Grids are filled by ``strip_js`` via ``cardHTML``.

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
    """Nav buttons + view shells + filled db + JS. Safe on live ~2.7MB HTML. No Sectors tab.

    ``ranked=None`` must not wipe ``#fd-breakout-db``. View shells stay; legacy
    ``#fd-bb-*`` are emptied. Always pass ``rank_book(...)`` on a live write.
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
