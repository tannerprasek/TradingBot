# DAPI enrichment pack

Nine pointer layers. **No news / headline feed. No earnings calendar UI. No EDGAR. No invented Bloomberg numbers.**

Refresh pipeline (live desk)::

    prices (pull_blpapi_live)
      → options pulse (pull_options_pulse, full book)
      → **dapi_enrich**          ← new stage ~50–60%
      → rebuild (run_v0 / write_dash / desk_dash)

Output: `dapi_enrichment.json` next to `options_abnormal.json`::

    { "asof": "...Z", "names": { "AAPL US Equity": { ... } }, "meta": { ... } }

Cards still render when the file is missing. One field or one name failing does not abort Refresh. Batch refdata runs in chunks (`DEFAULT_CHUNK_SIZE = 25`). Capacity (`BLOOMBERG_LIMIT` / daily limit) **skips remaining enrich** the same way options pulse degrades.

## Desktop drop-in

Canonical live tree: `C:\Users\MLP\Desktop\factorbook`

1. Copy `dapi_enrich.py` into that folder.
2. Merge the **ENRICH HOOK** in `add_server.py` (`run_dapi_enrich_stage`, `parse_intraday`) into the live sidecar after options pulse, progress **50–60%**.
3. Call `desk_dash.attach_enrichment` / `momentum_screen.attach_enrichment` on existing cards (fields listed below).
4. Options path: `pull_options_pulse.attach_skew` — additive; **do not change score v2**.
5. Keep `dapi_enrichment.json` gitignored (already in `factor-desk/.gitignore`).

This cloud tree is a drop-in pack, not a rewrite of FLAGS/WATCH/MOM/OUTLIERS/OPTIONS.

## Refresh `intraday=1`

Default **off**. Unchanged enrich when omitted.

```
GET  http://127.0.0.1:8765/refresh?intraday=1
POST http://127.0.0.1:8765/refresh
     Content-Type: application/json
     {"intraday": 1}
```

Also accepted: `true` / `yes` / `on`. CLI: `python dapi_enrich.py --intraday` or `python add_server.py --once --intraday`.

When on, enrich also requests session volume / VWAP / turnover and computes `session_vs_adv = VOLUME / VOLUME_AVG_20D` when both resolve. When off, `intraday` on each name is `null` and those fields are not requested (capacity-safe).

Status: `GET /status` → `{pct, stage, busy, last, intraday}`.

## Field candidates (first success wins)

`FIELD_CANDIDATES` in `dapi_enrich.py`. A mnemonic is recorded in `fields_used` only when the value parses. Otherwise `null_reasons[dest]` is set (`no_candidate_resolved`, `candidates_present_but_invalid`, `date_in_past`, …). **Dry tests in this cloud VM cannot resolve live FLDS** — Desktop should confirm winners on a logged-in terminal.

| Layer | Dest | Candidates (order) | Skip if |
| --- | --- | --- | --- |
| 1 Short / borrow | `short_int` | `SHORT_INT`, `EQY_SHORT_INTEREST`, `SHORT_INTEREST` | invalid |
| | `si_ratio` | `SHORT_INT_RATIO` (days-to-cover) | invalid |
| | `si_pct_float` | `SI_PERCENT_EQUITY_FLOAT`, `SHORT_INT_FLOAT`, `SHORT_INT_PCT_FLOAT` | invalid |
| | `borrow_fee` | `INDICATIVE_FEE`, `EQY_LEND_RATE`, `STOCK_LOAN_FEE`, `BORROW_COST` | **none resolve — skip gracefully** |
| | `borrow_util` | `UTILIZATION`, `EQY_LEND_UTILIZATION`, `STOCK_LOAN_UTILIZATION`, `SI_UTILIZATION` | **none resolve — skip** |
| 2 Vol regime | `vol_30d` | `VOLATILITY_30D`, `VOLATILITY_20D`, `REALIZED_VOL_30D`, `VOLATILITY_21D` | closest working |
| | `vol_90d` | `VOLATILITY_90D`, `VOLATILITY_60D`, `VOLATILITY_120D`, `VOLATILITY_180D`, `REALIZED_VOL_90D` | closest working |
| | `iv_call` | `HIST_CALL_IMP_VOL`, `CALL_IMP_VOL_30D`, `IVOL_CALL` | Equity hist IV |
| | `iv_put` | `HIST_PUT_IMP_VOL`, `PUT_IMP_VOL_30D`, `IVOL_PUT` | |
| | `iv_mid` | `IVOL_MID`, `IVOL_MEAN`, `30DAY_IMPVOL_100.0%MNY_DF`, `IMP_VOL_30D`, `HIST_IMPVOL_30D` | |
| 3 Skew / term | option `DELTA`, `IVOL_MID` / `OPT_IMPLIED_VOLATILITY`; keep `VOLUME` / `OPEN_INT` | 25Δ-ish = put IV − call IV near \|Δ\|≈0.25; else ATM proxy | **no invented Greeks** |
| 4 Liquidity | `volume_avg_20d` | `VOLUME_AVG_20D`, `VOLUME_AVG_30D`, `AVG_DAILY_VOLUME_20D` | |
| | `px_last` | `PX_LAST`, `LAST_PRICE`, `PX_CLOSE` (or prices context) | |
| | `adv_usd` | `PX_LAST * VOLUME_AVG_20D` when both present | else null |
| | `free_float_pct` | `EQY_FREE_FLOAT_PCT`, `EQY_FREE_FLOAT_PERCENT` | |
| 5 Ownership | `inst_pct` | `EQY_INST_PCT_SH_OUT` | |
| | `etf_pct` | `EQY_ETF_PCT_SH_OUT` only | **skip if it does not resolve** (often won't) |
| 6 Event badge | `event_date` / `event_days` | `EARNINGS_ANNOUNCEMENT_DT`, `EXPECTED_REPORT_DT`, `EQY_FUND_CRNCY_ADJ_NEXT_EPS_DT`, `NEXT_EARNINGS_ANNOUNCEMENT_DT`, `ANNOUNCEMENT_DT` | past date → null + `date_in_past` |
| 7 Credit | `cds_oas` | `CDS_SPREAD_5Y`, `CDS_SPREAD_MID`, `CDS_SPREAD`, `FIVE_YEAR_CDS_SPREAD` on the **equity** | usually null on common stock |
| | `bond_oas` | `OAS_SPREAD`, `OAS_TO_WORST`, `OAS_SPREAD_MID`, `IDX_OAS` | usually null on equity |
| | mapped CDS/bond | `EQUITY_CREDIT_TICKERS` (empty by default; Desktop may fill) | never invent OAS |
| 8 Intraday | `session_volume`, `vwap`, `turnover` | `VOLUME`/`PX_VOLUME`, `EQY_WEIGHTED_AVG_PX`/`VWAP`, `EQY_TURNOVER`/`TURNOVER` | only if `intraday=1` |
| 9 Beta / residual | `beta` | **`BETA_ADJ_OVERRIDABLE`** then `BETA_PRIMES` then `EQY_BETA` then `EQY_RAW_BETA` | winner in `beta_field` |
| | `residual_20d` | `r_stock_20d − β × r_mkt_20d` if both 20d returns are in `prices_ctx` (mkt default `SPY US Equity`) | else **beta-only** — no extra hist pull |

`meta.fields_attempted` / `fields_resolved` / `fields_failed` list what this run saw. `meta.skips` logs chunk / capacity / session skips.

## Chip meanings (pointers, not trades)

Same language as existing `.badge` / `.spike-chip`. Each pill is `{key, label, cls}`.

| Pill | `label` | `cls` | When |
| --- | --- | --- | --- |
| Short | `SI` | `si-crowded` | `SHORT_INT_RATIO` ≥ **8** dtc **or** SI% float ≥ **15** |
| | `SI` | `si-light` | ratio ≤ **1** or SI% float ≤ **2** |
| Vol | `IV↑` | `iv-rich` | implied / realized_30d ≥ **1.15** (need both) |
| | `IV↓` | `iv-cheap` | implied / realized_30d ≤ **0.85** |
| Liq | `ILLIQ` | `illiquid` | ADV$ < **$5,000,000** or ADV shares < **200,000** |
| Own | `INST` | `inst-high` | `EQY_INST_PCT_SH_OUT` ≥ **85** |
| Event | `EVT≤5d` | `evt-near` | next earnings in 0–5 days |
| | `EVT≤20d` | `evt-watch` | 6–20 days. **Badge only** |
| Beta | `β` | `beta` | a beta field resolved |
| Credit | `CRD` | `credit` | cds_oas or bond_oas resolved |
| | `CRD` | `credit-stress` | `watch_hint=equity_mom_vs_credit_stress`: \|20d ret\| ≥ **8%** **and** OAS ≥ **200 bps** |

Implied vol for the IV chip = `iv_mid` if present, else mean(call, put) hist IV, else whichever side exists.

## Card fields

`desk_dash.attach_enrichment` and `momentum_screen.attach_enrichment` set:

`si_ratio`, `vol_regime`, `liq`, `inst_pct`, `event_days`, `beta`, `credit`, `enrich_pills`

MOM rows also get `residual_20d`, `watch_hint`, `beta_field` when the name exists in the book.

## Options score v2 (unchanged)

```
0.5 * mean(top-3 log1p(vol/avg20))
+ 0.3 * log(opt_vol / stock_ADV)
+ 0.2 * log(vol / OI)
```

`DEFAULT_MAX_NAMES = 0`, 15C+15P, UI top 20×2, on-demand Refresh only.

## Capacity

If open or any chunk raises a capacity-like error (`BLOOMBERG_LIMIT`, daily limit, not logged in, …):

- remaining chunks are not sent
- already-received rows are kept
- `meta.capacity_skipped = true`
- Refresh continues to rebuild

## What this pack does **not** do

- News / story counts / headlines
- Earnings calendar UI, preload folders, EDGAR
- Trade recommendations (chips / `watch_hint` are pointers)
- Secrets or DAPI tokens in repo
- Invented OAS, Greeks, or vol when DAPI is null
