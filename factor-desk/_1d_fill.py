"""Bloomberg 1-day change for the Factor Desk title chip.

``CHG_PCT_1D`` is percent points (``9.95`` → ``+9.95%``). The chip stores that
as a decimal (``0.0995``).

Last-two ``prices_long.adj_close`` is not that number. On 2026-09-21 AMD
printed PX 615.52, Bloomberg CHG +55.70 / +9.95% (prior close 559.82). The
CSV pair 547.54 → 615.52 is +12.415% and must not be baked into
``#fd-chg-1d-db``. When the CSV return and ``CHG_PCT_1D`` differ by more than
half a percentage point, the chip keeps CHG. When CHG is missing, the chip
is omitted — the CSV return is never a fill.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import dapi_enrich

# Percentage points. AMD's Friday CSV print was ~2.5pp away from CHG_PCT_1D.
CSV_DIVERGE_PP = 0.5

_PCT_POINT_KEYS = ("CHG_PCT_1D", "chg_pct_1d")
_DAY_KEYS = ("day_pct", "r1_pct", "ret_1d", "day", "Day", "d1", "r1", "R1", "ret1")
_CHG_SOURCES = frozenset({"CHG_PCT_1D", "chg_pct_1d"})


def _num(src: Mapping[str, Any] | None, keys: tuple[str, ...]) -> float | None:
    if not isinstance(src, Mapping):
        return None
    for key in keys:
        if key not in src:
            continue
        raw = src.get(key)
        if isinstance(raw, str):
            raw = raw.strip().replace("%", "").replace(",", "")
        num = dapi_enrich.as_float(raw)
        if num is not None:
            return num
    return None


def _metrics(src: Mapping[str, Any]) -> Mapping[str, Any] | None:
    raw = src.get("metrics")
    return raw if isinstance(raw, Mapping) else None


def chg_points(src: Mapping[str, Any] | None) -> float | None:
    """Bloomberg ``CHG_PCT_1D`` in percent points, or ``None``."""
    if not isinstance(src, Mapping):
        return None
    for blob in (src, _metrics(src)):
        pts = _num(blob, _PCT_POINT_KEYS)
        if pts is not None:
            return pts
    return None


def _marked_from_chg(src: Mapping[str, Any]) -> bool:
    used = src.get("fields_used")
    if not isinstance(used, Mapping):
        return False
    token = str(used.get("chg_pct_1d") or used.get("day") or used.get("ret_1d") or "")
    return token in _CHG_SOURCES


def decimal_from_chg(src: Mapping[str, Any] | None) -> float | None:
    """1-day return as a decimal, only when it came from ``CHG_PCT_1D``.

    Percent points win over a conflicting ``day`` / ``ret_1d`` (the AMD case:
    CSV day ``0.124`` vs CHG ``9.95``). A bare decimal with no CHG source is
    not a chip value.
    """
    if not isinstance(src, Mapping):
        return None
    pts = chg_points(src)
    if pts is not None:
        return pts / 100.0
    if not _marked_from_chg(src):
        return None
    for blob in (_metrics(src), src):
        dec = _num(blob, _DAY_KEYS)
        if dec is not None:
            return dec
    return None


def last_two_decimal(prev_close: float, last_close: float) -> float | None:
    """Naive last-two close return. Not a Bloomberg 1-day change."""
    try:
        prev = float(prev_close)
        last = float(last_close)
    except (TypeError, ValueError):
        return None
    if prev == 0.0:
        return None
    return last / prev - 1.0


def load_last_two(root: Path | str | None) -> dict[str, float]:
    """Ticker → last-two ``adj_close`` return. Empty when no prices file.

    Used only to detect a divergence from ``CHG_PCT_1D``. Never a chip fill.
    """
    if root is None:
        return {}
    import mom_streak

    panel, _rel = mom_streak.load_price_panel(Path(root))
    out: dict[str, float] = {}
    for ticker, series in panel.items():
        if len(series) < 2:
            continue
        dec = last_two_decimal(series[-2][1], series[-1][1])
        if dec is None:
            continue
        key = str(ticker)
        out[key] = dec
        short = key.split()[0].upper() if key else ""
        if short:
            out[short] = dec
    return out


def guard_csv(present: Mapping[str, float], csv_map: Mapping[str, float] | None) -> dict[str, float]:
    """Keep every CHG decimal. Do not copy a last-two return into the map.

    When the CSV return and CHG differ by more than ``CSV_DIVERGE_PP``, CHG
    stays. When they are close, CHG still stays. Keys that exist only on the
    CSV are omitted.
    """
    csv_map = csv_map or {}
    kept: dict[str, float] = {}
    for key, dec in present.items():
        csv_dec = csv_map.get(key)
        if csv_dec is not None and abs(float(dec) - float(csv_dec)) * 100.0 > CSV_DIVERGE_PP:
            kept[str(key)] = float(dec)
            continue
        kept[str(key)] = float(dec)
    return kept


def _keys(ticker: Any) -> list[str]:
    key = str(ticker or "").strip()
    if not key:
        return []
    short = key.split()[0].upper()
    if short and short != key:
        return [key, short]
    return [key]


def _remember(
    present: dict[str, float],
    missing: set[str],
    ticker: Any,
    src: Mapping[str, Any] | None,
    *,
    authoritative: bool,
    clear_missing: bool = False,
) -> None:
    if not isinstance(src, Mapping):
        return
    keys = _keys(ticker)
    if not keys:
        return
    dec = decimal_from_chg(src)
    if dec is None:
        if authoritative:
            for key in keys:
                if clear_missing or key not in present:
                    missing.add(key)
                if clear_missing:
                    present.pop(key, None)
        return
    val = round(float(dec), 8)
    for key in keys:
        present[key] = val
        missing.discard(key)


def _names_of(book: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(book, Mapping):
        return None
    names = book.get("names")
    return names if isinstance(names, dict) else None


def bake_chg_db(
    cards: Any = None,
    book: Mapping[str, Any] | None = None,
    *,
    root: Path | str | None = None,
) -> tuple[dict[str, float], set[str]]:
    """Full-book ticker → CHG decimal, plus tickers whose CHG is missing.

    Disk ``dapi_enrichment.json`` (when ``root`` is set) is the universe.
    ``book`` overlays it. Cards add a CHG value when they have one, and do
    not mark a name missing just because that card object has no CHG.

    The returned missing set is what Refresh deletes from ``#fd-chg-1d-db``
    so a stale CSV percent cannot survive. Last-two closes are loaded only
    for the divergence guard.
    """
    present: dict[str, float] = {}
    missing: set[str] = set()

    disk = None
    if root is not None:
        disk = dapi_enrich.load_enrichment(dapi_enrich.default_out_path(Path(root)))
    # Disk is the universe. The in-memory book overlays it, including a name
    # whose CHG is now null (that name is omitted, not filled from CSV).
    for src_book, clear_missing in ((disk, False), (book, True)):
        names = _names_of(src_book)
        if not names:
            continue
        for ticker, rec in names.items():
            _remember(
                present,
                missing,
                ticker,
                rec if isinstance(rec, Mapping) else None,
                authoritative=True,
                clear_missing=clear_missing,
            )

    if cards:
        for card in cards:
            if not isinstance(card, Mapping):
                continue
            ticker = card.get("ticker") or card.get("t") or card.get("name") or card.get("d")
            _remember(present, missing, ticker, card, authoritative=False)

    csv_map = load_last_two(root)
    return guard_csv(present, csv_map), missing
