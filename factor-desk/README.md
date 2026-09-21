# Factor Desk

Source-of-truth folder for Factor Desk **inside this TradingBot repo** until a dedicated repository exists.

Cursor SCM cannot create a new GitHub repo (`createRepos` is unavailable). Cloud agents must edit files under `factor-desk/` only. Do not rewrite TradingBot app code outside this folder.

## Desktop deploy

Canonical desktop copy:

- `C:\Users\MLP\Desktop\factorbook`
- Generated dashboard: `factorbook.html` (same folder)

Sidecar: `add_server.py` on port **8765**.

## Cloud agents

- Edit Python and docs under `factor-desk/` only.
- Regenerate HTML via `desk_dash` / `write_dash` (never commit generated HTML).
- **No Bloomberg secrets.** Do not commit credentials or DAPI tokens.
- **No csv/json dumps.** Data products stay local / gitignored.

See [docs/CLOUD-AGENT.md](docs/CLOUD-AGENT.md) and [STRUCTURE.md](STRUCTURE.md).

## Layout

```
factor-desk/
  dapi_enrich.py         # DAPI enrichment pack (9 layers, no news)
  add_server.py          # sidecar HTTP on :8765 — Refresh + quote + portfolio
  desk_dash.py           # dashboard assembly + enrich pills + Portfolio embed
  portfolio.py           # Portfolio upload tab (parse, weights, scorecard)
  write_dash.py          # write factorbook.html
  pull_options_pulse.py  # options pulse + score v2 + skew
  momentum_screen.py     # MOM rows + enrich attach
  early_warning.py
  clean_ingest.py
  ingest_cov.py
  dapi_keepalive.py
  troughing.py
  docs/DAPI-ENRICH.md
  docs/
  scripts/
  tests/
```

Refresh: `prices → options pulse → dapi_enrich (~50–60%) → rebuild`.
Optional: `GET/POST /refresh?intraday=1` (default off). See [docs/DAPI-ENRICH.md](docs/DAPI-ENRICH.md).

Desktop copies `dapi_enrich.py` / `portfolio.py` into `C:\Users\MLP\Desktop\factorbook` and merges the thin hooks. Remaining ingest/troughing modules stay Desktop-owned until synced.

Portfolio tab: paste or upload `position,ticker` (signed shares) → scorecard vs the live book. See [docs/PORTFOLIO.md](docs/PORTFOLIO.md).
