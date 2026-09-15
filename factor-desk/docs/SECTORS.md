# Sectors tab

GICS sector / sub-industry view of the Factor Desk book (~390 names). **Equal-weight** member aggregation. Refresh writes `sectors.json`; `write_dash` embeds it in `factorbook.html` (`#sectors-db`). HTML also hydrates from `GET http://127.0.0.1:8765/sectors.json` when the sidecar is up.

Never invent a ticker→sector map. A name with no resolved **GICS name** is omitted from that list (`meta.unclassified`).

## Early trend score (predictive-leaning)

Not 12-month momentum. Cross-sectional **percentile ranks** (0–100, average rank, ties share) of each name vs the current book:

| Leg | Weight | Role |
| --- | --- | --- |
| 1D rank | 0.10 | Very early, noisy — capped so a one-day spike cannot dominate |
| **1W rank** | **0.50** | Primary early signal |
| 1M rank | 0.10 | Light persistence context — not enough to reward a move that already ran |
| **Acceleration** | **0.30** | `50 + 0.5 × (R_1W − R_1M)` |

Missing legs are dropped and remaining weights **renormalize**. Score is **null** when the only available horizon is 1M (that would be a lagging chase). YTD is **display-only** and is not in the score.

Acceleration is the turn detector: a name whose 1W rank is already beating its 1M rank scores earlier than a name that has already run for a month. That is the opposite of a long-lookback chase that only fires after the move is done.

```
score = Σ (leg × weight) / Σ weights_present

accel_leg = 50 + 0.5 * (rank_1w - rank_1m)    # in [0, 100]
```

Implemented in `sectors.early_trend_score` / `sectors.percentile_ranks`. Group score = equal-weight mean of member scores (nulls skipped).

## Aggregation

Two lists from the same snapshots:

1. **GICS sector** (`GICS_SECTOR_NAME`) — full / top level
2. **GICS sub-industry** (`GICS_SUB_INDUSTRY_NAME`) — every sub-industry present in the book (keyed `sector::sub` so names cannot collide)

Each row: `trend_score`, **1W / 1M / YTD** equal-weight returns, `n`, condensed **top 3 + bottom 3** members (by member trend score; tiny groups do not overlap top/bottom). Drill pane repeats those members with the same return columns + individual scores.

## DAPI / Refresh

Pipeline:

```
prices → options pulse → dapi_enrich (~50–60%) → sectors (~60–65%) → rebuild / write_dash
```

The 9-layer pointer pack is unchanged. Sectors is a **separate refdata pass** (capacity-safe, same chunking / `BLOOMBERG_LIMIT` degrade as enrich).

Field candidates (first success wins) live in `dapi_enrich.FIELD_CANDIDATES`:

| Dest | Candidates |
| --- | --- |
| `gics_sector_name` | `GICS_SECTOR_NAME`, `GICS_SECTOR` |
| `gics_industry_group_name` | `GICS_INDUSTRY_GROUP_NAME`, `GICS_INDUSTRY_GROUP` |
| `gics_industry_name` | `GICS_INDUSTRY_NAME`, `GICS_INDUSTRY` |
| `gics_sub_industry_name` | `GICS_SUB_INDUSTRY_NAME`, `GICS_SUB_INDUSTRY` |
| `ret_1d` | `CHG_PCT_1D`, `PX_CHG_PCT_1D`, `1D_PCT_CHG` |
| `ret_1w` | `CHG_PCT_5D`, `CHG_PCT_1W`, `1WK_PCT_CHG`, `PX_PCT_CHG_5D` |
| `ret_1m` | `CHG_PCT_1M`, `1MO_PCT_CHG`, `PX_PCT_CHG_1M` |
| `ret_3m` | `CHG_PCT_3M`, `3MO_PCT_CHG` (score unused; optional) |
| `ret_ytd` | `CHG_PCT_YTD`, `YTD_PCT_CHG`, `PX_YTD_PCT_CHG` |

Bloomberg `CHG_PCT_*` is **percent units** (2.5 → 0.025 stored). A numeric GICS *code* is **not** promoted to a name (`code_only_no_name`) — no code→name table.

Return precedence: `prices_ctx` decimals (from `pull_blpapi_live`) → optional close series (`closes`) → DAPI `CHG_PCT_*` → enrich rec.

## Desktop deploy

Canonical tree: `C:\Users\MLP\Desktop\factorbook`

1. Copy `sectors.py` next to live `dapi_enrich.py`.
2. Copy updated `dapi_enrich.py` (new `FIELD_CANDIDATES` / `GICS_PACK_KEYS` / `RETURN_PACK_KEYS` / `as_str` / `as_type="pct"|"str"`).
3. Merge the **SECTORS HOOK** in `add_server.py` (`run_sectors_stage` after enrich, `GET /sectors.json`).
4. Merge the Sectors **nav button + `#tab-sectors` panel + `#sectors-db` embed** into live `factorbook.html` / `desk_dash` (see [DEPLOY-HOOKS.md](../DEPLOY-HOOKS.md)). Do not replace FLAGS / WATCH / MOM / OPTIONS.

`sectors.json` is gitignored (local data product). Cards still render when the file is missing.

CLI::

    python sectors.py --dry-run --tickers AAPL,MSFT
    python add_server.py --once --tickers AAPL,MSFT
