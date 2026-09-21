# GICS sector names (filter strip BINNED)

Card `gics_sector_name` / `data-gics-sector` may stay. The **top GICS filter strip** (`#gics-filter-strip`, All / EN / IT / …) is **BINNED** — removed, not polished (Tanner request). G1–G12 group chips are a separate host and stay. This is **not** the Sectors tab. There is **no** `sectors.json` Refresh stage and **no** second full-book GICS pull on Refresh.

## Data

Prefer `gics_sector_name` already on each name in `dapi_enrichment.json`. Never invent a ticker→sector map. A numeric GICS *code* is not a name (`code_only_no_name`).

Refresh (`prices → dapi_enrich → rebuild`) does **not** request `GICS_SECTOR_NAME`. A previously resolved name is kept across Refresh via the enrich file and optional `gics_sectors.json` cache. Options pulse is a separate Options Refresh.

## One-shot fill (optional / manual)

If names are missing `gics_sector_name`, run **once** (or infrequently) — not on every Refresh:

```
python dapi_enrich.py --gics-once
python add_server.py --gics-once
GET/POST http://127.0.0.1:8765/gics-fill
```

That path pulls `GICS_SECTOR_NAME` then `GICS_SECTOR` (first success), writes gitignored `gics_sectors.json`, and stamps the enrich file. Capacity degrades like enrich. Dry-run / no session caches whatever names already have and does not fabricate sectors.

## UI

**No GICS filter strip.** Recopy `gics_filter.py`; `ensure_embedded` **deletes** leftover `#gics-filter-strip` / `#gics-filter-css` / `#gics-filter-js` / `#gics-sector-db` and unhides `.gics-hid` so a prior filter cannot stick. Do not paste a new host.

G1–G12 group chips (FLAGS/PAIRS/WATCH) stay.

Cards may keep `data-gics-sector` **and** `data-t` / `data-ticker`. Unclassified names are not hidden by a sector filter (there isn't one).

## Survive `write_dash` / `write_combined`

Every HTML write must **leave no GICS filter strip**. Call `gics_filter.ensure_embedded(html)` at the **end** of `desk_dash.write_combined` (strip-remover). If a live ~2.7MB `factorbook.html` already has Refresh / Momentum Up / Momentum Down / Outliers / Options, **patch it** — do not replace it with the skinny enrich-only grid.

See [DEPLOY-HOOKS.md](../DEPLOY-HOOKS.md) §5–6.
