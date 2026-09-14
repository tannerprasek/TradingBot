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

## Secrets and dumps

No Bloomberg secrets. No csv/json dumps. Those paths are gitignored (see `factor-desk/.gitignore`).
