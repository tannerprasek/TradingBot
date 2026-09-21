# Daily pipe

On-demand Refresh (not a polling loop):

```
prices → dapi_enrich → rebuild / write_dash
```

Manual Options Refresh: `options pulse → rebuild`. Not on every Refresh.

Enrich is capacity-safe (skip on `BLOOMBERG_LIMIT`). Optional mid-session: `/refresh?intraday=1`.
GICS sector names are **not** a Refresh stage. The GICS filter strip and book-delta strip are **BINNED**. Optional infrequent fill: `python dapi_enrich.py --gics-once`.
`write_dash` / `desk_dash.write_combined` re-embeds mom streak tags + the Options Refresh button + Experimental S-score, and **removes** leftover GICS / book-delta strip UI. See [GICS-FILTER.md](GICS-FILTER.md), [MOM-STREAK.md](MOM-STREAK.md), and [S-SCORE.md](S-SCORE.md). Ingest / screens / troughing remain Desktop-owned until synced.

Residual S-score is **Experimental-only**. Confirm Desktop residual panel depth (`v0_residuals.csv` / `residual_panel.json`). If only `residual_last` exists, Refresh appends one day; κ still needs history. PIT is not claimed unless SparsePCA is a rolling window (full-sample loadings are look-ahead).
