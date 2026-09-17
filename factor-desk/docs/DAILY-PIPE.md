# Daily pipe

On-demand Refresh (not a polling loop):

```
prices → dapi_enrich → rebuild / write_dash
```

Manual Options Refresh: `options pulse → rebuild`. Not on every Refresh.

Enrich is capacity-safe (skip on `BLOOMBERG_LIMIT`). Optional mid-session: `/refresh?intraday=1`.
GICS sector chips are **not** a Refresh stage. Optional infrequent fill: `python dapi_enrich.py --gics-once`.
`write_dash` / `desk_dash.write_combined` re-embeds GICS chips + mom streak tags + the Options Refresh button. See [GICS-FILTER.md](GICS-FILTER.md) and [MOM-STREAK.md](MOM-STREAK.md). Ingest / screens / troughing remain Desktop-owned until synced.
