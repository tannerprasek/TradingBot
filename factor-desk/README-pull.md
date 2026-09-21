# Pull / refresh

On-demand refresh only. No background polling. No credentials in this tree.

Pipeline:

```
prices → dapi_enrich (~50–60%) → rebuild / write_dash
```

Manual **Options Refresh** (not on every Refresh):

```
options pulse (full book) → rebuild / write_dash
```

Sidecar `:8765` (`add_server.py`):

| Endpoint | Role |
| --- | --- |
| `GET /health` | liveness |
| `GET /status` | `{pct, stage, busy, last, intraday, options, kind}` |
| `GET /refresh` | start Refresh (options pulse **off**) |
| `POST /refresh` | start Refresh (JSON body) |
| `GET/POST /options-refresh` | start Options Refresh (full-book pulse + rebuild) |
| `POST /api/refresh_live` `{"options": 1}` | same as Options Refresh |
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
python add_server.py --once --options --tickers AAPL,MSFT
python dapi_enrich.py --dry-run --tickers AAPL,MSFT
python dapi_enrich.py --gics-once --tickers AAPL,MSFT
```

`--dry-run` writes a null book + reasons (does not invent numbers). Live DAPI on Desktop writes resolved fields into `dapi_enrichment.json`.

`--gics-once` is a **manual** GICS sector fill. It is not on the Refresh pipeline. See [docs/GICS-FILTER.md](docs/GICS-FILTER.md).

Rebuild (`write_dash` / `desk_dash.write_combined`) patches mom streak tags and the Experimental residual S-score tab, and **removes** leftover GICS / book-delta strip UI. Live ~2.7MB `factorbook.html` is patched, not replaced. Residual S-score is Experimental-only (confirm panel depth; PIT not claimed). See [docs/S-SCORE.md](docs/S-SCORE.md).
