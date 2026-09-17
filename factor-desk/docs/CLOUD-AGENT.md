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

Nine pointer layers in `dapi_enrich.py` (no news). Refresh stage after prices (~50–60%). Full-book options pulse is a separate Options Refresh button. Query/body `intraday=1` is optional and default off.

See [DAPI-ENRICH.md](DAPI-ENRICH.md) for field candidates, chip thresholds, and Desktop copy steps.

Never invent Bloomberg numbers. Null + reason. Capacity → skip enrich, continue Refresh.

## GICS sector chips

Home filter-strip chips (G1–G12 language). Read `gics_sector_name` from enrichment. Do **not** add a Sectors tab, `sectors.json`, or a GICS pull on Refresh. Optional one-shot: `python dapi_enrich.py --gics-once`.

`write_dash` / `desk_dash.write_combined` must call `gics_filter.ensure_embedded` so `#gics-sector-db` + strip JS (`STRIP_ID`) survive every HTML write. Live ~2.7MB factorbook with Refresh / Momentum Up / Down / Outliers / Options is **patched**, never replaced by the skinny grid. See [GICS-FILTER.md](GICS-FILTER.md).

## Breakout / Breakdown + book delta + hitch

Mid-score climbers / crackers as home tabs (not maxed MOM). `breakout.ensure_embedded` + `book_delta.ensure_embedded` + `desk_hitch.ensure_embedded` on every HTML write. See [BREAKOUT-BREAKDOWN.md](BREAKOUT-BREAKDOWN.md). Do **not** reintroduce a Sectors tab.

## Paper trading

1-unit Buy/Sell on dense `cardHTML` cards. Paper only. `paper_trade.ensure_embedded` on every HTML write. Recopy `paper_trade.py`; do not wholesale replace live `desk_dash.py`. See [PAPER-TRADE.md](PAPER-TRADE.md) and [DEPLOY-HOOKS.md](../DEPLOY-HOOKS.md) §10.

## Momentum streak tag

Home UP/DOWN cards: consecutive trading-day streak of the card momentum rank vs **5**. Fields `mom_score` / `momentum_score` first; else on-disk hist / prices 13-window count (0–13). Exactly 5 → streak 0, tag `=5`. Chart overlay marks streak start and open end without burying the price series; existing tag-trigger labels are clustered. See [MOM-STREAK.md](MOM-STREAK.md).

## Secrets and dumps

No Bloomberg secrets. No csv/json dumps (`dapi_enrichment.json`, `options_abnormal.json`, …). Those paths are gitignored (see `factor-desk/.gitignore`).
