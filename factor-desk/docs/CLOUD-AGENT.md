# Cloud agent rules (Factor Desk)

Edit **Python under `factor-desk/` only**. Do not change TradingBot app modules outside this folder.

## HTML

Regenerate the dashboard HTML via `desk_dash` / `write_dash`. Do not hand-edit or commit generated `factorbook.html`.

## Options score v2

```
0.5 * mean(top-3 log1p(vol/avg20))
+ 0.3 * log(opt_vol / stock_ADV)
+ 0.2 * log(vol / OI)
```

- `DEFAULT_MAX_NAMES = 0` (no name cap in the pull)
- Contract set: **15C + 15P**
- UI: top **20 × 2** (calls and puts)
- **Refresh on-demand only** (no background polling loop)
- **No credentials** in repo, env samples, or commits

Skew / IV summary (`attach_skew`, `skew_25d_proxy`) is **additive**. Do not change the v2 mix.

## DAPI enrich

Nine pointer layers in `dapi_enrich.py` (no news). Refresh stage after options pulse (~50–60%). Query/body `intraday=1` is optional and default off.

See [DAPI-ENRICH.md](DAPI-ENRICH.md) for field candidates, chip thresholds, and Desktop copy steps.

Never invent Bloomberg numbers. Null + reason. Capacity → skip enrich, continue Refresh.

## GICS sector chips

Home filter-strip chips (G1–G12 language). Read `gics_sector_name` from enrichment. Do **not** add a Sectors tab, `sectors.json`, or a GICS pull on Refresh. Optional one-shot: `python dapi_enrich.py --gics-once`. See [GICS-FILTER.md](GICS-FILTER.md).

## Secrets and dumps

No Bloomberg secrets. No csv/json dumps (`dapi_enrichment.json`, `options_abnormal.json`, …). Those paths are gitignored (see `factor-desk/.gitignore`).
