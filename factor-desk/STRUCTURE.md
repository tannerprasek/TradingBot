# Factor Desk — expected modules

Until a dedicated repo exists, this folder is the cloud source of truth. Desktop live tree: `C:\Users\MLP\Desktop\factorbook`. Drop in `dapi_enrich.py` plus the thin Refresh/card hooks; do not rewrite FLAGS/WATCH/MOM.

## Python (repo root of this folder)

| Module | Role |
| --- | --- |
| `dapi_enrich.py` | DAPI enrichment pack (9 layers, chips, JSON) |
| `add_server.py` | Sidecar HTTP server on `:8765` (Refresh + `intraday=1`) |
| `desk_dash.py` | Assemble Factor Desk dashboard + enrich pills |
| `write_dash.py` | Write generated `factorbook.html` |
| `pull_options_pulse.py` | Options pulse pull + score v2 + skew summary |
| `momentum_screen.py` | Momentum screen + enrich attach |
| `early_warning.py` | Early-warning signals |
| `clean_ingest.py` | Clean ingest path |
| `ingest_cov.py` | Covariance / coverage ingest |
| `dapi_keepalive.py` | DAPI session keepalive |
| `troughing.py` | Troughing / mean-reversion helpers |

## Docs

| Path | Role |
| --- | --- |
| `README.md` | Folder source-of-truth + desktop deploy |
| `docs/CLOUD-AGENT.md` | Cloud edit / score / UI rules |
| `docs/DAPI-ENRICH.md` | Field packs, chips, Refresh `intraday=1` |
| `docs/OPTIONS-PULSE.md` | Options pulse notes |
| `docs/DAILY-PIPE.md` | Daily pipeline notes |
| `README-pull.md` | Pull / refresh notes |

## Scripts

| Path | Role |
| --- | --- |
| `scripts/dapi_pipe.ps1` | DAPI pipe |
| `scripts/install_dapi_pipe.ps1` | Install scheduled DAPI pipe |
| `scripts/_dapi_check.ps1` | DAPI health check |

## Generated / local (gitignored)

`factorbook.html`, `factorbook_desktop_latest.html`, `*.csv`, `*.xlsx`, `*.png`, `digest_db.json`, `options_abnormal.json`, `options_pulse*.json`, `dapi_enrichment.json`, `clean/*.csv`, `.dapi*`, `.venv/`.
