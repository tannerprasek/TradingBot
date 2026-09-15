# Daily pipe

On-demand Refresh (not a polling loop):

```
prices → options pulse → dapi_enrich → rebuild / write_dash
```

Enrich is capacity-safe (skip on `BLOOMBERG_LIMIT`). Optional mid-session: `/refresh?intraday=1`.
GICS sector chips are **not** a Refresh stage. Optional infrequent fill: `python dapi_enrich.py --gics-once`.
See [DAPI-ENRICH.md](DAPI-ENRICH.md) and [GICS-FILTER.md](GICS-FILTER.md). Ingest / screens / troughing remain Desktop-owned until synced.
