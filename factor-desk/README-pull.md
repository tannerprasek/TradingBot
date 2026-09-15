# Pull / refresh

On-demand refresh only. No background polling. No credentials in this tree.

Pipeline:

```
prices → options pulse → dapi_enrich (~50–60%) → rebuild / write_dash
```

Sidecar `:8765` (`add_server.py`):

| Endpoint | Role |
| --- | --- |
| `GET /health` | liveness |
| `GET /status` | `{pct, stage, busy, last, intraday}` |
| `GET /refresh` | start Refresh |
| `POST /refresh` | start Refresh (JSON body) |
| `GET/POST /gics-fill` | optional one-shot GICS sector fill (**not** Refresh) |

Optional flag (default **off**):

```
GET  /refresh?intraday=1
POST /refresh   {"intraday": 1}
```

When `intraday=1`, enrich also pulls session volume vs ADV. When omitted, enrich is unchanged.

CLI (no server):

```
python add_server.py --once --tickers AAPL,MSFT
python dapi_enrich.py --dry-run --tickers AAPL,MSFT
python dapi_enrich.py --gics-once --tickers AAPL,MSFT
```

`--dry-run` writes a null book + reasons (does not invent numbers). Live DAPI on Desktop writes resolved fields into `dapi_enrichment.json`.

`--gics-once` is a **manual** GICS sector fill. It is not on the Refresh pipeline. See [docs/GICS-FILTER.md](docs/GICS-FILTER.md).
