# Factor Desk — expected modules

Until a dedicated repo exists, this folder is the cloud source of truth. Modules below are stubs until synced from Desktop `C:\Users\MLP\Desktop\factorbook`.

## Python (repo root of this folder)

| Module | Role |
| --- | --- |
| `add_server.py` | Sidecar HTTP server on `:8765` |
| `desk_dash.py` | Assemble Factor Desk dashboard |
| `write_dash.py` | Write generated `factorbook.html` |
| `pull_options_pulse.py` | Options pulse pull + score v2 |
| `momentum_screen.py` | Momentum screen |
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

`factorbook.html`, `factorbook_desktop_latest.html`, `*.csv`, `*.xlsx`, `*.png`, `digest_db.json`, `options_abnormal.json`, `options_pulse*.json`, `clean/*.csv`, `.dapi*`, `.venv/`.
