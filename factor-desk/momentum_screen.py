"""Momentum screen for Factor Desk.

Thin ENRICH HOOK: attach ``si_ratio`` / ``vol_regime`` / ``liq`` / ``inst_pct`` /
``event_days`` / ``beta`` / ``credit`` / ``enrich_pills`` onto MOM rows. Residual
vs market is computed in ``dapi_enrich`` when 20d returns live in prices context;
this module only surfaces what enrich already attached.

No trade recommendations — chips/hints only.
"""

from __future__ import annotations

import logging
import sys
from typing import Any, Iterable, Mapping, MutableMapping

HERE = __file__
if str(__import__("pathlib").Path(HERE).resolve().parent) not in sys.path:
    sys.path.insert(0, str(__import__("pathlib").Path(HERE).resolve().parent))

import dapi_enrich  # noqa: E402
import desk_dash  # noqa: E402
import gics_filter  # noqa: E402
import mom_streak  # noqa: E402

LOG = logging.getLogger("momentum_screen")


def attach_enrichment(
    row: MutableMapping[str, Any],
    book: Mapping[str, Any] | None = None,
    rec: Mapping[str, Any] | None = None,
    cache: Mapping[str, Any] | None = None,
) -> MutableMapping[str, Any]:
    ticker = str(row.get("ticker") or row.get("name") or "")
    if rec is None and book is not None:
        rec = dapi_enrich.lookup_name(book, ticker)
    dapi_enrich.attach_card_fields(row, rec)
    gics_filter.overlay_sector(row, rec, cache=cache, book=book)
    mom_streak.attach_card(row)
    if rec:
        row["residual_20d"] = rec.get("residual_20d")
        row["watch_hint"] = rec.get("watch_hint")
        row["beta_field"] = rec.get("beta_field")
    else:
        row.setdefault("residual_20d", None)
        row.setdefault("watch_hint", None)
        row.setdefault("beta_field", None)
    return row


def attach_book(rows: Iterable[MutableMapping[str, Any]], book: Mapping[str, Any] | None = None) -> list[MutableMapping[str, Any]]:
    if book is None:
        book = desk_dash.load_enrichment()
    cache = dapi_enrich.load_gics_cache()
    return [attach_enrichment(row, book, cache=cache) for row in rows]


def screen(
    rows: Iterable[Mapping[str, Any]] | None = None,
    book: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Pass-through MOM rows + enrich fields. Empty input still returns []."""
    src = [dict(r) for r in (rows or [])]
    if not src and book and isinstance(book.get("names"), dict):
        for ticker, rec in book["names"].items():
            if not isinstance(rec, Mapping):
                continue
            src.append(
                {
                    "ticker": ticker,
                    "ret_20d": None,
                    "residual_20d": rec.get("residual_20d"),
                    "beta": rec.get("beta"),
                }
            )
    attached = attach_book(src, book)
    return [dict(r) for r in attached]


def main(argv: list[str] | None = None) -> int:
    import json

    book = desk_dash.load_enrichment()
    rows = screen(book=book)
    print(json.dumps({"n": len(rows), "rows": rows[:20]}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
