"""Assemble the Factor Desk dashboard.

Thin ENRICH HOOK: ``attach_enrichment`` / ``pills_html`` drop onto existing
FLAGS/WATCH/MOM/OUTLIERS/OPTIONS cards. Refresh without ``dapi_enrichment.json``
still renders. No news layer. No earnings calendar UI.
"""

from __future__ import annotations

import html
import json
import logging
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping, MutableMapping

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import dapi_enrich  # noqa: E402

LOG = logging.getLogger("desk_dash")
HTML_NAME = "factorbook.html"

# Same visual language as live-desk .badge / .spike-chip
PILL_CSS = """
.badge, .spike-chip {
  display: inline-block;
  font: 650 10px/1.15 ui-sans-serif, system-ui, sans-serif;
  letter-spacing: 0.04em;
  padding: 2px 6px;
  margin: 0 3px 3px 0;
  border-radius: 3px;
  border: 1px solid #6b7280;
  color: #d1d5db;
  background: #111827;
  text-transform: uppercase;
  vertical-align: middle;
}
.badge.si-crowded, .spike-chip.si-crowded { color: #f59e0b; border-color: #f59e0b; }
.badge.si-light, .spike-chip.si-light { color: #34d399; border-color: #34d399; }
.badge.iv-rich, .spike-chip.iv-rich { color: #f472b6; border-color: #f472b6; }
.badge.iv-cheap, .spike-chip.iv-cheap { color: #60a5fa; border-color: #60a5fa; }
.badge.illiquid, .spike-chip.illiquid { color: #fb7185; border-color: #fb7185; }
.badge.inst-high, .spike-chip.inst-high { color: #c084fc; border-color: #c084fc; }
.badge.evt-near, .spike-chip.evt-near { color: #fbbf24; border-color: #fbbf24; }
.badge.evt-watch, .spike-chip.evt-watch { color: #fde68a; border-color: #a3a3a3; }
.badge.beta, .spike-chip.beta { color: #93c5fd; border-color: #3b82f6; }
.badge.credit, .spike-chip.credit { color: #fda4af; border-color: #fb7185; }
.badge.credit-stress, .spike-chip.credit-stress { color: #fecaca; border-color: #ef4444; background: #3f1d1d; }
.badge.trough, .spike-chip.trough { color: #6ee7b7; border-color: #34d399; }
.badge.opt-spike, .spike-chip.opt-spike { color: #f9a8d4; border-color: #ec4899; }
"""


def load_enrichment(root: Path | None = None) -> dict[str, Any] | None:
    base = Path(root) if root is not None else HERE
    return dapi_enrich.load_enrichment(base / dapi_enrich.ENRICH_FILENAME)


def attach_enrichment(
    card: MutableMapping[str, Any],
    rec: Mapping[str, Any] | None = None,
    book: Mapping[str, Any] | None = None,
    ticker: str | None = None,
) -> MutableMapping[str, Any]:
    """Attach ``si_ratio``, ``vol_regime``, ``liq``, ``inst_pct``, ``event_days``,
    ``beta``, ``credit``, ``enrich_pills``. Missing enrich file → nulls + empty pills.
    """
    if rec is None and book is not None:
        rec = dapi_enrich.lookup_name(book, ticker or str(card.get("ticker") or card.get("name") or ""))
    if rec is None and ticker:
        rec = None
    return dapi_enrich.attach_card_fields(card, rec)


def attach_all(cards: Iterable[MutableMapping[str, Any]], book: Mapping[str, Any] | None) -> list[MutableMapping[str, Any]]:
    out: list[MutableMapping[str, Any]] = []
    for card in cards:
        ticker = str(card.get("ticker") or card.get("name") or "")
        rec = dapi_enrich.lookup_name(book, ticker) if book else None
        out.append(attach_enrichment(card, rec))
    return out


def pills_html(pills: Any) -> str:
    if not isinstance(pills, list):
        return ""
    bits: list[str] = []
    for pill in pills:
        if not isinstance(pill, Mapping):
            continue
        label = html.escape(str(pill.get("label") or ""))
        cls = html.escape(str(pill.get("cls") or ""))
        key = html.escape(str(pill.get("key") or ""))
        if not label:
            continue
        bits.append(f'<span class="badge spike-chip {cls}" data-key="{key}">{label}</span>')
    return "".join(bits)


def cards_from_enrichment(book: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    names = (book or {}).get("names") if isinstance(book, Mapping) else None
    if not isinstance(names, dict):
        return cards
    for ticker, rec in names.items():
        if not isinstance(rec, Mapping):
            rec = {}
        card: dict[str, Any] = {
            "ticker": ticker,
            "name": ticker,
            "si_ratio": rec.get("si_ratio"),
            "vol_regime": rec.get("vol_regime"),
            "liq": rec.get("liq"),
            "inst_pct": rec.get("inst_pct"),
            "event_days": rec.get("event_days"),
            "beta": rec.get("beta"),
            "credit": rec.get("credit"),
            "enrich_pills": list(rec.get("enrich_pills") or []),
        }
        cards.append(card)
    return cards


def _fmt(value: Any, digits: int = 2) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return html.escape(str(value))


def render_html(
    cards: list[Mapping[str, Any]] | None = None,
    *,
    book: Mapping[str, Any] | None = None,
    title: str = "Factor Desk",
) -> str:
    book = book if book is not None else load_enrichment()
    if cards is None:
        cards = cards_from_enrichment(book)
    cards = attach_all(list(cards), book)
    rows: list[str] = []
    for card in cards:
        ticker = html.escape(str(card.get("ticker") or card.get("name") or ""))
        pills = pills_html(card.get("enrich_pills"))
        rows.append(
            f"""
            <article class="card" data-ticker="{ticker}">
              <header>
                <h2>{ticker}</h2>
                <div class="pills">{pills}</div>
              </header>
              <dl>
                <div><dt>si_ratio</dt><dd>{_fmt(card.get("si_ratio"))}</dd></div>
                <div><dt>vol_regime</dt><dd>{_fmt(card.get("vol_regime"))}</dd></div>
                <div><dt>liq</dt><dd>{_fmt(card.get("liq"))}</dd></div>
                <div><dt>inst_pct</dt><dd>{_fmt(card.get("inst_pct"))}</dd></div>
                <div><dt>event_days</dt><dd>{_fmt(card.get("event_days"), 0)}</dd></div>
                <div><dt>beta</dt><dd>{_fmt(card.get("beta"))}</dd></div>
                <div><dt>credit</dt><dd>{_fmt(card.get("credit"))}</dd></div>
              </dl>
            </article>
            """
        )
    empty = ""
    if not rows:
        empty = (
            '<p class="empty">No enrichment file — Refresh still works. '
            "Cards will gain SI / IV / ILLIQ / INST / EVT / β / CRD pills after "
            "<code>dapi_enrich</code> writes <code>dapi_enrichment.json</code>.</p>"
        )
    asof = html.escape(str((book or {}).get("asof") or ""))
    meta = (book or {}).get("meta") if isinstance(book, Mapping) else {}
    intra = bool((meta or {}).get("intraday")) if isinstance(meta, Mapping) else False
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <title>{html.escape(title)}</title>
  <style>
    :root {{ color-scheme: dark; }}
    body {{
      margin: 0; padding: 24px;
      font: 14px/1.4 ui-sans-serif, system-ui, sans-serif;
      background: #0b0f14; color: #e5e7eb;
    }}
    h1 {{ font-size: 18px; font-weight: 650; margin: 0 0 6px; }}
    .meta {{ color: #9ca3af; font-size: 12px; margin-bottom: 18px; }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
      gap: 12px;
    }}
    .card {{
      border: 1px solid #1f2937;
      background: #111827;
      border-radius: 8px;
      padding: 12px 14px;
    }}
    .card h2 {{ font-size: 14px; margin: 0 0 8px; letter-spacing: 0.02em; }}
    .pills {{ min-height: 18px; }}
    dl {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 6px 12px;
      margin: 10px 0 0;
    }}
    dt {{ color: #6b7280; font-size: 10px; text-transform: uppercase; }}
    dd {{ margin: 0; font-variant-numeric: tabular-nums; }}
    .empty {{ color: #9ca3af; }}
    {PILL_CSS}
  </style>
</head>
<body>
  <h1>{html.escape(title)}</h1>
  <p class="meta">asof {asof or "—"} · enrich pills only · no news · no earnings calendar · intraday={"on" if intra else "off"}</p>
  {empty}
  <div class="grid">
    {"".join(rows)}
  </div>
</body>
</html>
"""


def assemble_and_write(
    path: Path | str | None = None,
    *,
    root: Path | None = None,
    cards: list[Mapping[str, Any]] | None = None,
    book: Mapping[str, Any] | None = None,
) -> Path:
    base = Path(root) if root is not None else HERE
    dest = Path(path) if path is not None else base / HTML_NAME
    if book is None:
        book = load_enrichment(base)
    text = render_html(cards, book=book)
    dest.write_text(text, encoding="utf-8")
    LOG.info("wrote %s", dest)
    return dest


def rebuild(path: Path | str | None = None, root: Path | None = None) -> Path:
    return assemble_and_write(path, root=root)


def main(argv: list[str] | None = None) -> int:
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="Assemble Factor Desk HTML")
    p.add_argument("--root", default="")
    p.add_argument("--out", default="")
    args = p.parse_args(argv)
    root = Path(args.root) if args.root else HERE
    out = Path(args.out) if args.out else root / HTML_NAME
    assemble_and_write(out, root=root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
