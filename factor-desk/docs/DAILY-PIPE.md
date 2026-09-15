# Daily pipe

On-demand Refresh (not a polling loop):

```
prices → options pulse → dapi_enrich → sectors → rebuild / write_dash
```

Enrich and sectors are capacity-safe (skip on `BLOOMBERG_LIMIT`). Optional mid-session: `/refresh?intraday=1`.
See [DAPI-ENRICH.md](DAPI-ENRICH.md) and [SECTORS.md](SECTORS.md). Ingest / screens / troughing remain Desktop-owned until synced.
