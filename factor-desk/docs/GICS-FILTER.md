# GICS sector filter chips

Light **top filter strip** chips for main GICS sectors present in the book. Tapping a sector shows the same FLAGS / WATCH / MOM / OUTLIERS / OPTIONS cards, filtered to names in that sector. This is **not** the Sectors tab (scrapped as too DAPI-heavy). There is **no** `sectors.json` Refresh stage and **no** second full-book GICS pull on Refresh.

## Data

Prefer `gics_sector_name` already on each name in `dapi_enrichment.json`. Never invent a ticker→sector map. A numeric GICS *code* is not a name (`code_only_no_name`).

Refresh (`prices → options pulse → dapi_enrich → rebuild`) does **not** request `GICS_SECTOR_NAME`. A previously resolved name is kept across Refresh via the enrich file and optional `gics_sectors.json` cache.

## One-shot fill (optional / manual)

If names are missing `gics_sector_name`, run **once** (or infrequently) — not on every Refresh:

```
python dapi_enrich.py --gics-once
python add_server.py --gics-once
GET/POST http://127.0.0.1:8765/gics-fill
```

That path pulls `GICS_SECTOR_NAME` then `GICS_SECTOR` (first success), writes gitignored `gics_sectors.json`, and stamps the enrich file. Capacity degrades like enrich. Dry-run / no session caches whatever names already have and does not fabricate sectors.

## UI

Chips sit in the existing top filter row (G1–G12 / tags language: `.filter-chip` / `.gchip`, dark desk). **All** clears the filter. Short labels (IT, FIN, …) with the official GICS name as `title`. Client JS adds `.gics-hid` only — Momentum Up/Down, Outliers, Options, Refresh, FLAGS/WATCH behavior is unchanged.

Cards need `data-gics-sector` (or a ticker the `#gics-sector-db` map can resolve). Unclassified names stay visible on All and hide when a sector is selected.

See [DEPLOY-HOOKS.md](../DEPLOY-HOOKS.md) §5 for live `factorbook.html` / `desk_dash.py` paste blocks.
