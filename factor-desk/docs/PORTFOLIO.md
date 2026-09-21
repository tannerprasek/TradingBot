# Portfolio upload

Look-here scorecard of an uploaded book vs the **live Factor Desk**. Not a trade. Tanner decides.

## Upload

Top-nav **Portfolio**. Paste or upload a two-column table, then **Run**.

```
position,ticker
100,AAPL
-40,MSFT US Equity
```

- `position` = **signed shares** (long positive, short negative)
- `ticker` = Bloomberg yellow key. Bare `AAPL` → `AAPL US Equity` (same `dapi_enrich.normalize_ticker` as the rest of the desk)
- Header aliases: `shares`/`qty`/`pos`, `symbol`/`yellow`/`name`
- Duplicate tickers are summed after normalize

Sidecar (Desktop `:8765`):

```
POST http://127.0.0.1:8765/api/portfolio
{"csv": "position,ticker\n100,AAPL\n-40,MSFT"}

GET  http://127.0.0.1:8765/api/portfolio        # last run
GET  http://127.0.0.1:8765/api/quote/AAPL       # last px
```

If the sidecar is down, the tab still scores against the last Refresh snapshot embedded in `factorbook.html` (no live DAPI for names missing a print).

## Weights

Gross-normalized **signed** weights (standard long-short book weights for factor exposure):

```
notional_i = shares_i × last_i
weight_i   = notional_i / sum_j |notional_j|

sum(|weight|) = 1
sum(weight)   = net / gross
```

A short flips the sign of that name’s factor / mom / metric contribution. The strip also shows net $, gross $, long $, short $, net/gross.

Unpriced names (no last print) are **out of the denominator** and listed separately.

## Last price

No new vendor. First hit:

1. Desk snapshot — `dapi_enrichment.json` `px_last` / MOM-FLAGS card last / `prices.csv`
2. Existing DAPI sidecar `PX_LAST` / `LAST_PRICE` / `PX_CLOSE` (same candidates as enrich)

`GET /api/quote/{ticker}` is that lookup. Refresh pipeline is unchanged.

## Outputs

1. **Factor decomposition** — existing SparsePCA / v0 loadings already on disk (`v0_loadings.csv`, `factor_loadings.csv`, …) and FLAGS membership if present. Does **not** fit a new model. Exposure_k = Σ weight_i × loading_i,k.
2. **Portfolio mom_score** — same definition as FLAGS / MOM Up-Down cards (`mom_score` / `momentum_score` / `mom_rank` / `trend_rank`, else `score` in `[0, 20]`). Never options `score_v2`. Gross-weighted; shorts flip sign. Coverage = % of gross with a mom print.
3. **Metrics scorecard** — every numeric already on name cards (mom, r20, RS63, ATR%, beta, residual_20d, SI, INST, EVT days, vol, options `score_v2` / skew if present) as weighted average, % of gross in the desk’s top/bottom quintile, largest contributors. Categorical: GICS, FLAGS factor, enrich pills.

Names **not in the current desk universe** show as **missing** (count + list). They can still price via DAPI; factor / mom stay n/a until they are in the book.

## Persist

Last run → gitignored `portfolio_last.json` next to `dapi_enrichment.json`, or `runtime/portfolio_last.json` if that folder exists. UI also keeps `localStorage` `fd-portfolio-last-v1`. Do not commit uploaded CSVs (`*.csv` already gitignored).

## Desktop sync

Recopy `portfolio.py` into `C:\Users\MLP\Desktop\factorbook`. Paste `portfolio.ensure_embedded` on the live `write_combined` tail. Do **not** wholesale replace live `desk_dash.py`. See [DEPLOY-HOOKS.md](../DEPLOY-HOOKS.md) §5.
