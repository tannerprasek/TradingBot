# Daily pipe

On-demand Refresh (not a polling loop):

```
prices → options pulse → dapi_enrich → rebuild / write_dash
```

Enrich is capacity-safe (skip on `BLOOMBERG_LIMIT`). Optional mid-session: `/refresh?intraday=1`.
See [DAPI-ENRICH.md](DAPI-ENRICH.md). Ingest / screens / troughing remain Desktop-owned until synced.
