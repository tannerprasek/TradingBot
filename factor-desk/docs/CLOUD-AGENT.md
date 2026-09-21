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

## GICS names (filter strip BINNED)

Card `gics_sector_name` from enrichment is fine. Do **not** add a Sectors tab, `sectors.json`, a GICS pull on Refresh, or a `#gics-filter-strip`. Optional one-shot: `python dapi_enrich.py --gics-once`.

`write_dash` / `desk_dash.write_combined` calls `gics_filter.ensure_embedded` to **remove** leftover GICS strip UI. Live ~2.7MB factorbook is **patched**, never replaced by the skinny grid. G1–G12 stay. See [GICS-FILTER.md](GICS-FILTER.md).

## Breakout / Breakdown + hitch (book-delta strip BINNED)

Early-inflection Breakout (score 7–10, `mom_score_d10` > +3, streak 1–15d above 5, dispersion > 0) and Breakdown (score 2–6, `mom_score_d10` < −3, streak 1–15d below 5, dispersion < 0). Dispersion is `residual_20d`, else RS63. `card_render.ensure_embedded` (shared MOM `cardHTML` wrap) then `breakout.ensure_embedded` + `book_delta.ensure_embedded` (now **removes** leftover `#fd-book-delta`) + `desk_hitch.ensure_embedded` on every HTML write. Breakout/Breakdown paint the same Mom `cardHTML` chrome. `#fd-bb-mom-full-db` holds the full card; thin `#fd-bb-mom-db` is gates only. See [BREAKOUT-BREAKDOWN.md](BREAKOUT-BREAKDOWN.md). Do **not** reintroduce a Sectors tab. Recopy `card_render.py` and `breakout.py`; do not wholesale replace live `desk_dash.py`.

## Paper trading

Generic Buy/Sell on dense `cardHTML` cards, plus a top-nav **Paper** tab (`#view-paper`) for the open book, inline Close, ticker+Buy/Sell, week scorecard, and closed trades. Paper only, 1 unit, localStorage `fd-paper-book` only. Same-side click is a no-op + toast (does not flip). Week scorecard is closed trades since Monday 00:00 America/Edmonton. Do **not** inject `#fd-paper-home` into top chrome. `paper_trade.ensure_embedded` on every HTML write. Recopy `paper_trade.py`; do not wholesale replace live `desk_dash.py`. See [PAPER-TRADE.md](PAPER-TRADE.md).

## Experimental residual S-score

Avellaneda–Lee S-score + κ on the **existing** SparsePCA residual panel (not a new PCA engine). Dedicated top-nav **Experimental** tab only — do not paint S-score onto live MOM / FLAGS / home. Pointers only — no auto-enter, no paper auto-open, no paper Sharpes as KPIs. PIT is **not** claimed. `s_score.ensure_embedded` on every HTML write. Recopy `s_score.py`; do not wholesale replace live `desk_dash.py`. See [S-SCORE.md](S-SCORE.md).

## Momentum streak tag

Home UP/DOWN cards: consecutive trading-day streak of the card momentum rank vs **5**. Fields `mom_score` / `momentum_score` first; else on-disk hist / prices 13-window count (0–13). Exactly 5 → streak 0, tag `=5`. Chart overlay marks streak start and open end without burying the price series; existing tag-trigger labels are clustered. Name-drill charts color the close path green/red by SMA50/SMA200 (Positive: close above both; else Negative) with 50-day (blue) and 200-day (maroon) overlays. See [MOM-STREAK.md](MOM-STREAK.md).

## Secrets and dumps

No Bloomberg secrets. No csv/json dumps (`dapi_enrichment.json`, `options_abnormal.json`, …). Those paths are gitignored (see `factor-desk/.gitignore`).
