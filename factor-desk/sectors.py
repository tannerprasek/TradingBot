"""Sectors tab — GICS aggregation + early trend score.

Refresh slot (after ``dapi_enrich``, before rebuild)::

    prices → options pulse → dapi_enrich → **sectors** → write_dash

Never invent a ticker→sector map. Names without a resolved GICS sector /
sub-industry name are omitted from that list (counted in ``meta.unclassified``).

Public API
----------
- ``early_trend_score`` / ``percentile_ranks`` / ``aggregate_groups``
- ``build_sectors_book`` / ``write_sectors`` / ``load_sectors``
- ``run_sectors_stage`` (Refresh hook)
- ``panel_markup`` / ``tab_css`` / ``tab_js`` (HTML embed)

Output: ``sectors.json`` next to ``dapi_enrichment.json``.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import dapi_enrich as de  # noqa: E402

LOG = logging.getLogger("sectors")

SECTORS_FILENAME = "sectors.json"
WEIGHTING = "equal"

# Early trend score — see docs/SECTORS.md. Shorter horizons + acceleration.
# YTD is display-only (lagging) and is not in the score.
W_1D = 0.10
W_1W = 0.50
W_1M = 0.10
W_ACCEL = 0.30
TOP_K = 3

CTX_RET_KEYS: dict[str, tuple[str, ...]] = {
    "ret_1d": ("ret_1d", "r_1d", "d1"),
    "ret_1w": ("ret_1w", "r_1w", "ret_5d", "d5"),
    "ret_1m": ("ret_1m", "r_1m", "ret_21d", "d21"),
    "ret_3m": ("ret_3m", "r_3m", "ret_63d", "d63"),
    "ret_ytd": ("ret_ytd", "r_ytd", "ytd"),
}


# ---------------------------------------------------------------------------
# Pure score / rank
# ---------------------------------------------------------------------------


def percentile_ranks(values: Sequence[float | None]) -> list[float | None]:
    """Average-rank percentile in ``[0, 100]``. Nulls stay null.

    ``n == 1`` → 50. Ties share the mid-rank so a cluster does not get an
    arbitrary order. This is cross-sectional (book-relative), not time-series.
    """
    n = len(values)
    out: list[float | None] = [None] * n
    idx = [
        i
        for i, v in enumerate(values)
        if v is not None and not (isinstance(v, float) and (math.isnan(v) or math.isinf(v)))
    ]
    m = len(idx)
    if m == 0:
        return out
    if m == 1:
        out[idx[0]] = 50.0
        return out
    ordered = sorted(idx, key=lambda i: float(values[i]))  # type: ignore[arg-type]
    ranks = [0.0] * n
    i = 0
    while i < m:
        j = i
        while j + 1 < m and float(values[ordered[j + 1]]) == float(values[ordered[i]]):  # type: ignore[arg-type]
            j += 1
        avg = (i + j) / 2.0
        for k in range(i, j + 1):
            ranks[ordered[k]] = avg
        i = j + 1
    scale = m - 1
    for i in idx:
        out[i] = 100.0 * ranks[i] / scale
    return out


def early_trend_score(
    rank_1d: float | None,
    rank_1w: float | None,
    rank_1m: float | None,
    *,
    w_1d: float = W_1D,
    w_1w: float = W_1W,
    w_1m: float = W_1M,
    w_accel: float = W_ACCEL,
) -> float | None:
    """Multi-horizon rank blend. Null if we only have lagging 1M (no chase).

    ``accel = 50 + 0.5 * (R_1w − R_1m)`` so a name whose 1W rank is beating its
    1M rank scores earlier than a name that already ran for a month.
    Missing legs are dropped and remaining weights are renormalized.
    """
    parts: list[tuple[float, float]] = []
    if rank_1d is not None:
        parts.append((rank_1d, w_1d))
    if rank_1w is not None:
        parts.append((rank_1w, w_1w))
    if rank_1m is not None:
        parts.append((rank_1m, w_1m))
    if rank_1w is not None and rank_1m is not None:
        accel = 50.0 + 0.5 * (rank_1w - rank_1m)
        parts.append((accel, w_accel))
    if not parts:
        return None
    if rank_1w is None and rank_1d is None:
        # 1M (and maybe accel-less) only — that is a lagging chase. Skip.
        return None
    wsum = sum(w for _, w in parts)
    if wsum <= 0:
        return None
    return round(sum(v * w for v, w in parts) / wsum, 4)


def ew_mean(values: Iterable[float | None]) -> float | None:
    xs = [
        v
        for v in values
        if v is not None and not (isinstance(v, float) and (math.isnan(v) or math.isinf(v)))
    ]
    if not xs:
        return None
    return sum(xs) / len(xs)


def short_ticker(ticker: str) -> str:
    text = (ticker or "").strip()
    if not text:
        return text
    upper = text.upper()
    for suf in (
        " US EQUITY",
        " UN EQUITY",
        " UW EQUITY",
        " UA EQUITY",
        " UF EQUITY",
        " EQUITY",
    ):
        if upper.endswith(suf):
            return text[: -len(suf)].strip()
    return text.split()[0]


def looks_like_gics_code(value: str | None) -> bool:
    if not value:
        return False
    t = value.strip()
    return t.isdigit() and 1 <= len(t) <= 8


# ---------------------------------------------------------------------------
# Returns from a close series (optional hist path)
# ---------------------------------------------------------------------------


def _as_points(closes: Sequence[Any]) -> list[tuple[date, float]]:
    pts: list[tuple[date, float]] = []
    for row in closes:
        if isinstance(row, Mapping):
            d = de.as_date(row.get("date") or row.get("d") or row.get("asof"))
            px = de.as_float(row.get("px") or row.get("close") or row.get("PX_LAST") or row.get("px_last"))
        elif isinstance(row, (tuple, list)) and len(row) >= 2:
            d = de.as_date(row[0])
            px = de.as_float(row[1])
        else:
            continue
        if d is not None and px is not None and px > 0:
            pts.append((d, px))
    pts.sort(key=lambda x: x[0])
    return pts


def returns_from_closes(
    closes: Sequence[Any] | None,
    *,
    asof: date | None = None,
) -> dict[str, float | None]:
    """Session lookbacks: 1 / 5 / 21 / 63. YTD vs last close of prior year."""
    empty = {k: None for k in ("ret_1d", "ret_1w", "ret_1m", "ret_3m", "ret_ytd")}
    pts = _as_points(closes or [])
    if len(pts) < 2:
        return empty
    last_d, last_px = pts[-1]
    _ = asof or last_d

    def ret_n(n: int) -> float | None:
        if n < 1 or len(pts) < n + 1:
            return None
        px0 = pts[-1 - n][1]
        if px0 <= 0:
            return None
        return last_px / px0 - 1.0

    ytd_start = date(last_d.year, 1, 1)
    prior_year = [p for p in pts if p[0] < ytd_start]
    if prior_year:
        base = prior_year[-1][1]
    else:
        year_pts = [p for p in pts if p[0] >= ytd_start]
        base = year_pts[0][1] if year_pts else None
    ret_ytd = (last_px / base - 1.0) if base and base > 0 else None
    return {
        "ret_1d": ret_n(1),
        "ret_1w": ret_n(5),
        "ret_1m": ret_n(21),
        "ret_3m": ret_n(63),
        "ret_ytd": ret_ytd,
    }


def _ctx_return(row: Mapping[str, Any], dest: str) -> float | None:
    for key in CTX_RET_KEYS.get(dest, (dest,)):
        v = de.as_float(row.get(key))
        if v is not None:
            return v
    return None


# ---------------------------------------------------------------------------
# Parse GICS + returns from a refdata row / enrich rec / prices_ctx
# ---------------------------------------------------------------------------


def parse_gics(raw: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return name fields. A numeric GICS code is *not* promoted to a name."""
    out: dict[str, Any] = {k: None for k in de.GICS_PACK_KEYS}
    used: dict[str, str] = {}
    reasons: dict[str, str] = {}
    for dest in de.GICS_PACK_KEYS:
        val, field, reason = de.first_success(raw, dest, as_type="str")
        if val and looks_like_gics_code(val):
            reasons[dest] = "code_only_no_name"
            if field:
                used[dest + "_code"] = field
            continue
        out[dest] = val
        if field:
            used[dest] = field
        if reason:
            reasons[dest] = reason
    out["fields_used"] = used
    out["null_reasons"] = reasons
    return out


def parse_returns(
    raw: Mapping[str, Any] | None,
    *,
    prices_ctx_row: Mapping[str, Any] | None = None,
    enrich_rec: Mapping[str, Any] | None = None,
) -> dict[str, float | None]:
    """Precedence: prices_ctx (decimal) > close series > CHG_PCT_* > enrich rec."""
    out: dict[str, float | None] = {k: None for k in de.RETURN_PACK_KEYS}
    row = prices_ctx_row if isinstance(prices_ctx_row, Mapping) else {}
    rec = enrich_rec if isinstance(enrich_rec, Mapping) else {}

    closes = row.get("closes") or row.get("px_series") or rec.get("closes")
    from_hist = returns_from_closes(closes if isinstance(closes, list) else None)

    for dest in de.RETURN_PACK_KEYS:
        ctx = _ctx_return(row, dest)
        if ctx is not None:
            out[dest] = ctx
            continue
        hist = from_hist.get(dest)
        if hist is not None:
            out[dest] = hist
            continue
        val, _field, _reason = de.first_success(raw, dest, as_type="pct")
        if val is not None:
            out[dest] = val
            continue
        inherited = de.as_float(rec.get(dest))
        if inherited is not None:
            out[dest] = inherited
    return out


# ---------------------------------------------------------------------------
# Name snapshots + group aggregation
# ---------------------------------------------------------------------------


def member_payload(snap: Mapping[str, Any], *, compact: bool = True) -> dict[str, Any]:
    keys = ("ticker", "short", "trend_score", "ret_1w", "ret_1m", "ret_ytd")
    if not compact:
        keys = keys + ("ret_1d", "ret_3m", "gics_sector_name", "gics_sub_industry_name", "adv_usd")
    return {k: snap.get(k) for k in keys}


def top_bottom(members: Sequence[Mapping[str, Any]], k: int = TOP_K) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Top / bottom ``k`` by trend score, no overlap. Tiny groups: top only."""
    ranked = [m for m in members if de.as_float(m.get("trend_score")) is not None]
    ranked.sort(key=lambda m: float(m["trend_score"]), reverse=True)
    if not ranked:
        ranked = [m for m in members if de.as_float(m.get("ret_1w")) is not None]
        ranked.sort(key=lambda m: float(m["ret_1w"]), reverse=True)
    n = len(ranked)
    if n == 0:
        return [], []
    if n <= k:
        return [member_payload(m) for m in ranked], []
    if n < 2 * k:
        top_n = (n + 1) // 2
        return (
            [member_payload(m) for m in ranked[:top_n]],
            [member_payload(m) for m in reversed(ranked[top_n:])],
        )
    return (
        [member_payload(m) for m in ranked[:k]],
        [member_payload(m) for m in reversed(ranked[-k:])],
    )


def _group_row(
    key: str,
    name: str,
    level: str,
    members: Sequence[Mapping[str, Any]],
    *,
    parent: str | None = None,
) -> dict[str, Any]:
    top, bottom = top_bottom(members)
    row: dict[str, Any] = {
        "key": key,
        "name": name,
        "level": level,
        "parent_sector": parent,
        "n": len(members),
        "n_scored": sum(1 for m in members if m.get("trend_score") is not None),
        "trend_score": ew_mean(m.get("trend_score") for m in members),
        "ret_1w": ew_mean(m.get("ret_1w") for m in members),
        "ret_1m": ew_mean(m.get("ret_1m") for m in members),
        "ret_ytd": ew_mean(m.get("ret_ytd") for m in members),
        "weighting": WEIGHTING,
        "top": top,
        "bottom": bottom,
    }
    if row["trend_score"] is not None:
        row["trend_score"] = round(float(row["trend_score"]), 4)
    for rk in ("ret_1w", "ret_1m", "ret_ytd"):
        if row[rk] is not None:
            row[rk] = round(float(row[rk]), 6)
    return row


def aggregate_groups(snaps: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Equal-weight GICS sector and sub-industry lists, scored desc."""
    by_sector: dict[str, list[Mapping[str, Any]]] = {}
    by_sub: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for snap in snaps:
        sector = snap.get("gics_sector_name")
        if isinstance(sector, str) and sector.strip():
            by_sector.setdefault(sector.strip(), []).append(snap)
        sub = snap.get("gics_sub_industry_name")
        if isinstance(sub, str) and sub.strip() and isinstance(sector, str) and sector.strip():
            by_sub.setdefault((sector.strip(), sub.strip()), []).append(snap)
        elif isinstance(sub, str) and sub.strip():
            by_sub.setdefault(("", sub.strip()), []).append(snap)

    sectors = [
        _group_row(name, name, "sector", members)
        for name, members in by_sector.items()
    ]
    sectors.sort(key=lambda r: (r["trend_score"] is None, -(r["trend_score"] or 0.0), r["name"]))

    subs: list[dict[str, Any]] = []
    for (sector, sub), members in by_sub.items():
        key = f"{sector}::{sub}" if sector else sub
        subs.append(_group_row(key, sub, "sub_industry", members, parent=sector or None))
    subs.sort(key=lambda r: (r["trend_score"] is None, -(r["trend_score"] or 0.0), r["name"]))
    return sectors, subs


def score_snaps(snaps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    r1d = percentile_ranks([s.get("ret_1d") for s in snaps])
    r1w = percentile_ranks([s.get("ret_1w") for s in snaps])
    r1m = percentile_ranks([s.get("ret_1m") for s in snaps])
    for i, snap in enumerate(snaps):
        snap["rank_1d"] = r1d[i]
        snap["rank_1w"] = r1w[i]
        snap["rank_1m"] = r1m[i]
        snap["trend_score"] = early_trend_score(r1d[i], r1w[i], r1m[i])
        if snap["trend_score"] is None and r1w[i] is None and r1d[i] is None:
            snap.setdefault("null_reasons", {})
            if isinstance(snap["null_reasons"], dict):
                snap["null_reasons"]["trend_score"] = "lagging_only_or_missing"
    return snaps


# ---------------------------------------------------------------------------
# Book assemble / IO
# ---------------------------------------------------------------------------


def default_out_path(root: Path | None = None) -> Path:
    base = Path(root) if root is not None else HERE
    return base / SECTORS_FILENAME


def load_sectors(path: Path | str | None = None) -> dict[str, Any] | None:
    p = Path(path) if path is not None else default_out_path()
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        LOG.warning("could not read %s: %s", p, exc)
        return None
    return data if isinstance(data, dict) else None


def write_sectors(book: Mapping[str, Any], path: Path | str | None = None) -> Path:
    p = Path(path) if path is not None else default_out_path()
    tmp = p.with_suffix(p.suffix + ".tmp")
    text = json.dumps(book, indent=2, default=str)
    tmp.write_text(text + "\n", encoding="utf-8")
    os.replace(tmp, p)
    return p


def empty_book(*, asof: str | None = None, reason: str | None = None) -> dict[str, Any]:
    return {
        "asof": asof or de._now_iso(),
        "weighting": WEIGHTING,
        "score": score_meta(),
        "sectors": [],
        "sub_industries": [],
        "meta": {
            "n_names": 0,
            "n_classified": 0,
            "n_unclassified": 0,
            "unclassified": [],
            "source": "sectors",
            "skips": [reason] if reason else [],
        },
    }


def score_meta() -> dict[str, Any]:
    return {
        "name": "early_trend",
        "weighting": WEIGHTING,
        "weights": {"ret_1d": W_1D, "ret_1w": W_1W, "ret_1m": W_1M, "accel_1w_vs_1m": W_ACCEL},
        "display": ["trend_score", "ret_1w", "ret_1m", "ret_ytd"],
        "note": "Cross-sectional percentile blend; YTD is display-only.",
    }


def _prices_row(ticker: str, prices_ctx: Mapping[str, Any] | None) -> dict[str, Any]:
    if not prices_ctx:
        return {}
    row = prices_ctx.get(ticker) or prices_ctx.get(de.name_key(ticker)) or {}
    return dict(row) if isinstance(row, Mapping) else {}


def _enrich_rec(ticker: str, enrich: Mapping[str, Any] | None) -> dict[str, Any]:
    rec = de.lookup_name(enrich, ticker) if enrich else None
    return dict(rec) if isinstance(rec, Mapping) else {}


def name_snap(
    ticker: str,
    *,
    gics_raw: Mapping[str, Any] | None = None,
    enrich_rec: Mapping[str, Any] | None = None,
    prices_ctx_row: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    rec = dict(enrich_rec or {})
    gics = parse_gics(gics_raw)
    # Enrich may already hold resolved names from a prior parse.
    for key in de.GICS_PACK_KEYS:
        if not gics.get(key):
            inherited = rec.get(key)
            if isinstance(inherited, str) and inherited.strip() and not looks_like_gics_code(inherited):
                gics[key] = inherited.strip()
    rets = parse_returns(gics_raw, prices_ctx_row=prices_ctx_row, enrich_rec=rec)
    adv = de.as_float((prices_ctx_row or {}).get("adv_usd")) or de.as_float(rec.get("adv_usd"))
    snap: dict[str, Any] = {
        "ticker": de.name_key(ticker),
        "short": short_ticker(ticker),
        "gics_sector_name": gics.get("gics_sector_name"),
        "gics_industry_group_name": gics.get("gics_industry_group_name"),
        "gics_industry_name": gics.get("gics_industry_name"),
        "gics_sub_industry_name": gics.get("gics_sub_industry_name"),
        "ret_1d": rets.get("ret_1d"),
        "ret_1w": rets.get("ret_1w"),
        "ret_1m": rets.get("ret_1m"),
        "ret_3m": rets.get("ret_3m"),
        "ret_ytd": rets.get("ret_ytd"),
        "adv_usd": adv,
        "trend_score": None,
        "fields_used": gics.get("fields_used") or {},
        "null_reasons": gics.get("null_reasons") or {},
    }
    return snap


def build_sectors_book(
    tickers: Sequence[str],
    session: Any | None = None,
    *,
    prices_ctx: Mapping[str, Any] | None = None,
    enrich_book: Mapping[str, Any] | None = None,
    gics_rows: Mapping[str, Mapping[str, Any]] | None = None,
    chunk_size: int = de.DEFAULT_CHUNK_SIZE,
    progress_cb: Callable[[float, str], None] | None = None,
    asof: datetime | date | str | None = None,
    root: Path | None = None,
    write: bool = True,
    out_path: Path | str | None = None,
) -> dict[str, Any]:
    """Score the book and aggregate GICS sector + sub-industry lists."""
    if progress_cb:
        progress_cb(0.60, "sectors start")

    if isinstance(asof, datetime):
        asof_iso = asof.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    elif isinstance(asof, date):
        asof_iso = asof.isoformat()
    elif isinstance(asof, str) and asof:
        asof_iso = asof
    else:
        asof_iso = (enrich_book or {}).get("asof") if isinstance(enrich_book, Mapping) else None
        asof_iso = asof_iso or de._now_iso()

    ordered: list[str] = []
    seen: set[str] = set()
    for t in tickers:
        k = de.name_key(t)
        if k and k not in seen:
            seen.add(k)
            ordered.append(k)
    if not ordered and isinstance(enrich_book, Mapping):
        names = enrich_book.get("names")
        if isinstance(names, dict):
            ordered = list(names.keys())

    meta: dict[str, Any] = {
        "n_names": len(ordered),
        "n_classified": 0,
        "n_unclassified": 0,
        "unclassified": [],
        "source": "sectors",
        "weighting": WEIGHTING,
        "session": "none",
        "skips": [],
        "fields_attempted": [],
        "fields_resolved": [],
        "capacity_skipped": False,
        "chunk_size": chunk_size,
    }

    fields = de.flatten_candidates(tuple(de.GICS_PACK_KEYS) + tuple(de.RETURN_PACK_KEYS))
    meta["fields_attempted"] = list(fields)

    rows: dict[str, dict[str, Any]] = {t: dict(gics_rows[t]) for t in ordered if gics_rows and t in gics_rows}
    wrapped = de.wrap_session(session)
    need = [t for t in ordered if t not in rows]
    # Skip DAPI when every name already has a GICS name on the enrich rec
    # *and* a 1W return from prices/enrich — still pull if anything is missing.
    if wrapped is not None and need:
        still: list[str] = []
        for t in need:
            rec = _enrich_rec(t, enrich_book)
            px = _prices_row(t, prices_ctx)
            has_gics = isinstance(rec.get("gics_sector_name"), str) and bool(str(rec.get("gics_sector_name")).strip())
            has_ret = _ctx_return(px, "ret_1w") is not None or de.as_float(rec.get("ret_1w")) is not None
            if has_gics and has_ret:
                rows[t] = {}
            else:
                still.append(t)
        if still:
            meta["session"] = type(wrapped).__name__
            try:
                got, skips, cap = de.batch_refdata(
                    wrapped,
                    still,
                    fields,
                    chunk_size=chunk_size,
                    progress_cb=progress_cb,
                    progress_lo=0.60,
                    progress_hi=0.64,
                )
                meta["skips"].extend(skips)
                meta["capacity_skipped"] = bool(cap)
                for t, row in got.items():
                    rows[t] = row
            except de.CapacityError as exc:
                meta["capacity_skipped"] = True
                meta["skips"].append(f"capacity:{exc.reason}")
                LOG.warning("sectors DAPI capacity — classify what we have (%s)", exc.reason)
    elif wrapped is None:
        meta["skips"].append("no_dapi_session")
        LOG.info("sectors: no DAPI session — classify only from enrich/prices_ctx")

    resolved: set[str] = set()
    for row in rows.values():
        for fname, val in row.items():
            if not de.is_na(val):
                resolved.add(fname)
    meta["fields_resolved"] = sorted(resolved)

    snaps: list[dict[str, Any]] = []
    for t in ordered:
        snap = name_snap(
            t,
            gics_raw=rows.get(t) or {},
            enrich_rec=_enrich_rec(t, enrich_book),
            prices_ctx_row=_prices_row(t, prices_ctx),
        )
        snaps.append(snap)

    score_snaps(snaps)

    unclassified = [
        s["short"] or s["ticker"]
        for s in snaps
        if not s.get("gics_sector_name")
    ]
    meta["unclassified"] = unclassified
    meta["n_unclassified"] = len(unclassified)
    meta["n_classified"] = len(snaps) - len(unclassified)

    sectors, subs = aggregate_groups(snaps)
    book = {
        "asof": asof_iso,
        "weighting": WEIGHTING,
        "score": score_meta(),
        "sectors": sectors,
        "sub_industries": subs,
        "meta": meta,
    }
    if write:
        dest = Path(out_path) if out_path is not None else default_out_path(root)
        write_sectors(book, dest)
        meta["path"] = str(dest)
        LOG.info("wrote %s (%s sectors, %s sub-industries)", dest, len(sectors), len(subs))
    if progress_cb:
        progress_cb(0.65, "sectors done")
    return book


def run_sectors_stage(
    tickers: Sequence[str] | None = None,
    *,
    session: Any | None = None,
    prices_ctx: Mapping[str, Any] | None = None,
    enrich_book: Mapping[str, Any] | None = None,
    progress_cb: Callable[[float, str], None] | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    """ENRICH HOOK companion — Refresh ~60–65%. Never raises out of Refresh."""
    cb = progress_cb or (lambda _f, _s: None)
    cb(0.60, "sectors")
    base = Path(root) if root is not None else HERE
    names = list(tickers or [])
    book_in = enrich_book
    if book_in is None:
        book_in = de.load_enrichment(base / de.ENRICH_FILENAME)
    if not names:
        names = de.discover_tickers(base)
    try:
        if session is None:
            try:
                session = de.open_dapi_session()
            except de.CapacityError as exc:
                LOG.warning("sectors skipped DAPI at open (%s)", exc.reason)
                session = de.NullSession()
        return build_sectors_book(
            names,
            session,
            prices_ctx=prices_ctx,
            enrich_book=book_in,
            progress_cb=cb,
            root=base,
        )
    except de.CapacityError as exc:
        LOG.warning("sectors capacity — writing empty-classify book (%s)", exc.reason)
        try:
            return build_sectors_book(
                names,
                de.NullSession(),
                prices_ctx=prices_ctx,
                enrich_book=book_in,
                progress_cb=cb,
                root=base,
            )
        except Exception as inner:  # noqa: BLE001
            LOG.warning("sectors fallback failed: %s", inner)
            book = empty_book(reason=f"capacity:{exc.reason}")
            write_sectors(book, default_out_path(base))
            return book
    except Exception as exc:  # noqa: BLE001
        LOG.warning("sectors stage non-fatal: %s", exc)
        book = empty_book(reason=f"sectors_error:{exc}")
        try:
            write_sectors(book, default_out_path(base))
        except OSError:
            pass
        cb(0.65, "sectors failed")
        return book


# ---------------------------------------------------------------------------
# HTML fragment (embedded by desk_dash)
# ---------------------------------------------------------------------------


def embed_json(dom_id: str, data: Mapping[str, Any] | None) -> str:
    payload = data if isinstance(data, Mapping) else empty_book(reason="missing")
    text = json.dumps(payload, default=str)
    text = text.replace("<", "\\u003c")
    return f'<script type="application/json" id="{dom_id}">{text}</script>'


def tab_css() -> str:
    return """
.topnav { display:flex; gap:6px; flex-wrap:wrap; margin: 14px 0 16px; }
.nav-btn {
  font: 650 11px/1.2 "Segoe UI", "DejaVu Sans", "Noto Sans", ui-sans-serif, system-ui, sans-serif;
  letter-spacing: 0.06em; text-transform: uppercase;
  padding: 6px 12px; border-radius: 4px; cursor: pointer;
  color: #d1d5db; background: #111827; border: 1px solid #374151;
}
.nav-btn:hover { border-color: #60a5fa; }
.nav-btn.active { color: #fff; background: #1d4ed8; border-color: #3b82f6; }
.tab-panel { display: none; }
.tab-panel.active { display: block; }
.note { color: #9ca3af; font-size: 12px; margin: 0 0 12px; }
.sec-layout { display: grid; grid-template-columns: minmax(0,1fr) 340px; gap: 14px; align-items: start; }
@media (max-width: 960px) { .sec-layout { grid-template-columns: 1fr; } }
.sec-lists { display: flex; flex-direction: column; gap: 18px; min-width: 0; }
.sec-h { display: flex; justify-content: space-between; align-items: baseline; gap: 8px; margin: 0 0 8px; }
.sec-h h2 { font-size: 13px; letter-spacing: 0.08em; text-transform: uppercase; margin: 0; color: #9ca3af; }
.sec-h .hint { font-size: 11px; color: #6b7280; }
.sec-filter { width: 100%; max-width: 280px; margin: 0 0 10px; padding: 6px 8px;
  background: #0b0f14; color: #e5e7eb; border: 1px solid #1f2937; border-radius: 4px; font: 12px/1.3 inherit; }
.sec-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(250px, 1fr)); gap: 8px; }
.sec-card {
  border: 1px solid #1f2937; background: #111827; border-radius: 8px;
  padding: 10px 12px; cursor: pointer; min-height: 118px;
}
.sec-card:hover { border-color: #3b82f6; }
.sec-card.active { border-color: #60a5fa; box-shadow: inset 0 0 0 1px #1d4ed8; }
.sec-kicker { font-size: 10px; color: #6b7280; text-transform: uppercase; letter-spacing: 0.04em; }
.sec-name { font-size: 13px; font-weight: 650; margin: 2px 0 8px; }
.sec-metrics { display: grid; grid-template-columns: repeat(4, 1fr); gap: 4px 6px; margin: 0; }
.sec-metrics div { min-width: 0; }
.sec-metrics dt { color: #6b7280; font-size: 9px; text-transform: uppercase; letter-spacing: 0.04em; }
.sec-metrics dd { margin: 0; font-variant-numeric: tabular-nums; font-size: 12px; }
.pos { color: #34d399; }
.neg { color: #fb7185; }
.muted { color: #6b7280; }
.tb { display: flex; justify-content: space-between; gap: 8px; margin-top: 8px; font-size: 11px; }
.tb .up { color: #6ee7b7; }
.tb .dn { color: #fda4af; }
.tb span { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.score-bar { height: 3px; background: #1f2937; border-radius: 2px; margin: 0 0 8px; overflow: hidden; }
.score-bar > i { display: block; height: 100%; background: #3b82f6; }
.drill {
  border: 1px solid #1f2937; background: #0f172a; border-radius: 8px;
  padding: 12px 14px; position: sticky; top: 12px; min-height: 160px;
}
.drill h3 { margin: 0 0 4px; font-size: 14px; }
.drill .sub { color: #9ca3af; font-size: 11px; margin-bottom: 10px; }
.drill table { width: 100%; border-collapse: collapse; font-size: 12px; font-variant-numeric: tabular-nums; }
.drill th { text-align: left; color: #6b7280; font-size: 10px; text-transform: uppercase; font-weight: 650; padding: 4px 4px 6px 0; }
.drill td { padding: 4px 6px 4px 0; border-top: 1px solid #1f2937; }
.drill tr.hi td:first-child { color: #6ee7b7; }
.drill tr.lo td:first-child { color: #fda4af; }
.drill .empty { color: #6b7280; font-size: 12px; }
"""


def tab_js() -> str:
    return r"""
(function () {
  function qs(sel, root) { return (root || document).querySelector(sel); }
  function qsa(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }
  function showTab(id) {
    qsa(".tab-panel").forEach(function (p) { p.classList.toggle("active", p.id === "tab-" + id); });
    qsa(".nav-btn").forEach(function (b) { b.classList.toggle("active", b.getAttribute("data-tab") === id); });
    try { if (history.replaceState) history.replaceState(null, "", "#" + id); } catch (e) {}
  }
  qsa(".nav-btn").forEach(function (btn) {
    btn.addEventListener("click", function () { showTab(btn.getAttribute("data-tab")); });
  });
  var hash = (location.hash || "").replace("#", "");
  if (hash && qs("#tab-" + hash)) showTab(hash);

  function fmtPct(x) {
    if (x === null || x === undefined || x === "") return "—";
    var v = Number(x) * 100;
    if (!isFinite(v)) return "—";
    var s = (v >= 0 ? "+" : "") + v.toFixed(1) + "%";
    return s;
  }
  function clsPct(x) {
    if (x === null || x === undefined) return "muted";
    if (x > 0) return "pos";
    if (x < 0) return "neg";
    return "muted";
  }
  function fmtScore(x) {
    if (x === null || x === undefined) return "—";
    var v = Number(x);
    return isFinite(v) ? v.toFixed(1) : "—";
  }
  function tickersLine(arr) {
    if (!arr || !arr.length) return "—";
    return arr.map(function (m) { return m.short || m.ticker || ""; }).filter(Boolean).join(" · ");
  }
  function loadDb() {
    var el = qs("#sectors-db");
    if (!el) return { sectors: [], sub_industries: [], meta: {} };
    try { return JSON.parse(el.textContent || "{}"); } catch (e) { return { sectors: [], sub_industries: [], meta: {} }; }
  }
  window.__SECTORS = loadDb();

  function cardHtml(row, level) {
    var parent = row.parent_sector ? '<div class="sec-kicker">' + esc(row.parent_sector) + "</div>" : "";
    var n = row.n != null ? row.n : 0;
    var bar = row.trend_score == null ? 0 : Math.max(0, Math.min(100, Number(row.trend_score)));
    var top = tickersLine(row.top);
    var bot = tickersLine(row.bottom);
    var botRow = (row.bottom && row.bottom.length)
      ? '<div class="tb"><span class="dn">▼ ' + esc(bot) + "</span></div>"
      : "";
    return (
      '<article class="sec-card" data-level="' + esc(level) + '" data-key="' + esc(row.key) + '" tabindex="0">' +
        parent +
        '<div class="sec-name">' + esc(row.name) + ' <span class="muted">· ' + n + "</span></div>" +
        '<div class="score-bar"><i style="width:' + bar.toFixed(1) + '%"></i></div>' +
        '<dl class="sec-metrics">' +
          metric("Score", fmtScore(row.trend_score), "muted") +
          metric("1W", fmtPct(row.ret_1w), clsPct(row.ret_1w)) +
          metric("1M", fmtPct(row.ret_1m), clsPct(row.ret_1m)) +
          metric("YTD", fmtPct(row.ret_ytd), clsPct(row.ret_ytd)) +
        "</dl>" +
        '<div class="tb"><span class="up">▲ ' + esc(top) + "</span></div>" +
        botRow +
      "</article>"
    );
  }
  function metric(label, val, cls) {
    return "<div><dt>" + label + '</dt><dd class="' + cls + '">' + val + "</dd></div>";
  }
  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  function lookup(level, key) {
    var db = window.__SECTORS || {};
    var list = level === "sector" ? (db.sectors || []) : (db.sub_industries || []);
    for (var i = 0; i < list.length; i++) if (list[i].key === key) return list[i];
    return null;
  }

  function memberRows(title, arr, kind) {
    if (!arr || !arr.length) return "";
    var body = arr.map(function (m) {
      return "<tr class='" + kind + "'><td>" + esc(m.short || m.ticker) + "</td><td>" +
        fmtScore(m.trend_score) + '</td><td class="' + clsPct(m.ret_1w) + '">' + fmtPct(m.ret_1w) +
        '</td><td class="' + clsPct(m.ret_1m) + '">' + fmtPct(m.ret_1m) +
        '</td><td class="' + clsPct(m.ret_ytd) + '">' + fmtPct(m.ret_ytd) + "</td></tr>";
    }).join("");
    return "<h4 class='sec-kicker' style='margin:12px 0 4px'>" + title + "</h4>" +
      "<table><thead><tr><th>Name</th><th>Score</th><th>1W</th><th>1M</th><th>YTD</th></tr></thead>" +
      "<tbody>" + body + "</tbody></table>";
  }

  function openDrill(level, key) {
    qsa(".sec-card").forEach(function (c) {
      c.classList.toggle("active", c.getAttribute("data-level") === level && c.getAttribute("data-key") === key);
    });
    var row = lookup(level, key);
    var pane = qs("#sec-drill");
    if (!pane) return;
    if (!row) {
      pane.innerHTML = '<p class="empty">Select a sector or sub-industry.</p>';
      return;
    }
    var parent = row.parent_sector ? esc(row.parent_sector) + " · " : "";
    pane.innerHTML =
      "<h3>" + esc(row.name) + "</h3>" +
      '<div class="sub">' + parent + esc(level === "sector" ? "GICS sector" : "GICS sub-industry") +
        " · " + (row.n || 0) + " names · " + esc(row.weighting || "equal") + "-weight</div>" +
      '<dl class="sec-metrics">' +
        metric("Score", fmtScore(row.trend_score), "muted") +
        metric("1W", fmtPct(row.ret_1w), clsPct(row.ret_1w)) +
        metric("1M", fmtPct(row.ret_1m), clsPct(row.ret_1m)) +
        metric("YTD", fmtPct(row.ret_ytd), clsPct(row.ret_ytd)) +
      "</dl>" +
      memberRows("Top " + ((row.top && row.top.length) || 0), row.top, "hi") +
      memberRows("Bottom " + ((row.bottom && row.bottom.length) || 0), row.bottom, "lo");
  }

  function renderSectors() {
    var db = window.__SECTORS || {};
    var sectors = db.sectors || [];
    var subs = db.sub_industries || [];
    var sg = qs("#sec-sector-grid");
    var ug = qs("#sec-sub-grid");
    var meta = qs("#sec-meta");
    if (meta) {
      var m = db.meta || {};
      meta.textContent = (sectors.length ? sectors.length + " sectors" : "no sectors") +
        " · " + (subs.length ? subs.length + " sub-industries" : "no sub-industries") +
        " · " + (m.n_classified || 0) + " classified / " + (m.n_names || 0) +
        " book · equal-weight · early-trend score";
    }
    if (sg) {
      sg.innerHTML = sectors.length
        ? sectors.map(function (r) { return cardHtml(r, "sector"); }).join("")
        : '<p class="empty">No GICS sector names yet. Refresh on Desktop pulls GICS_SECTOR_NAME / GICS_SUB_INDUSTRY_NAME via DAPI and writes sectors.json. Unclassified names are skipped — no invented map.</p>';
    }
    if (ug) {
      ug.innerHTML = subs.length
        ? subs.map(function (r) { return cardHtml(r, "sub_industry"); }).join("")
        : '<p class="empty">No GICS sub-industries in this book snapshot.</p>';
    }
    qsa(".sec-card").forEach(function (card) {
      function go() { openDrill(card.getAttribute("data-level"), card.getAttribute("data-key")); }
      card.addEventListener("click", go);
      card.addEventListener("keydown", function (ev) { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); go(); } });
    });
    var filter = qs("#sec-filter");
    if (filter) {
      filter.oninput = function () {
        var q = (filter.value || "").toLowerCase();
        qsa("#sec-sub-grid .sec-card").forEach(function (c) {
          var name = (c.querySelector(".sec-name") || {}).textContent || "";
          var kick = (c.querySelector(".sec-kicker") || {}).textContent || "";
          c.style.display = (!q || (name + " " + kick).toLowerCase().indexOf(q) >= 0) ? "" : "none";
        });
      };
    }
  }

  renderSectors();

  function hydrateFromSidecar() {
    fetch("http://127.0.0.1:8765/sectors.json", { cache: "no-store" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (!data || !(data.sectors || data.sub_industries)) return;
        if ((data.sectors || []).length || (data.sub_industries || []).length) {
          window.__SECTORS = data;
          renderSectors();
        }
      })
      .catch(function () { /* file:// or sidecar down — embedded JSON stands */ });
  }
  hydrateFromSidecar();
  document.addEventListener("keydown", function (ev) {
    if (ev.key === "Escape") {
      qsa(".sec-card.active").forEach(function (c) { c.classList.remove("active"); });
      var pane = qs("#sec-drill");
      if (pane) pane.innerHTML = '<p class="empty">Select a sector or sub-industry.</p>';
    }
  });
})();
"""


def panel_markup(book: Mapping[str, Any] | None) -> str:
    asof = ""
    if isinstance(book, Mapping):
        asof = str(book.get("asof") or "")
    return f"""
    <p class="note" id="sec-meta">equal-weight · early-trend score · asof {asof or "—"}</p>
    <div class="sec-layout">
      <div class="sec-lists">
        <section>
          <div class="sec-h"><h2>GICS sectors</h2><span class="hint">full / top level</span></div>
          <div class="sec-grid" id="sec-sector-grid"></div>
        </section>
        <section>
          <div class="sec-h"><h2>GICS sub-industries</h2><span class="hint">every sub-industry in the book</span></div>
          <input class="sec-filter" id="sec-filter" type="search" placeholder="Filter sub-industries…" />
          <div class="sec-grid" id="sec-sub-grid"></div>
        </section>
      </div>
      <aside class="drill" id="sec-drill">
        <p class="empty">Select a sector or sub-industry.</p>
      </aside>
    </div>
    """


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="Factor Desk sectors book (GICS + early trend score)")
    p.add_argument("--tickers", default="", help="Comma-separated tickers")
    p.add_argument("--dry-run", action="store_true", help="No DAPI; classify from enrich/prices only")
    p.add_argument("--out", default="", help="Output JSON path")
    p.add_argument("--root", default="", help="Desk root")
    args = p.parse_args(argv)

    root = Path(args.root) if args.root else HERE
    extra = [t.strip() for t in args.tickers.split(",") if t.strip()]
    tickers = extra or de.discover_tickers(root)
    session: de.RefDataSession | None
    if args.dry_run:
        session = de.NullSession()
    else:
        try:
            session = de.open_dapi_session()
        except de.CapacityError:
            session = de.NullSession()

    enrich = de.load_enrichment(root / de.ENRICH_FILENAME)
    out = Path(args.out) if args.out else default_out_path(root)
    book = build_sectors_book(
        tickers,
        session,
        enrich_book=enrich,
        out_path=out,
        root=root,
    )
    print(
        json.dumps(
            {
                "asof": book["asof"],
                "n_sectors": len(book["sectors"]),
                "n_sub_industries": len(book["sub_industries"]),
                "n_classified": book["meta"].get("n_classified"),
                "n_unclassified": book["meta"].get("n_unclassified"),
                "path": str(out),
                "weighting": book.get("weighting"),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
