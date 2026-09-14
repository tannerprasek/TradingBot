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
  add_server.py          # sidecar HTTP on :8765
  desk_dash.py           # dashboard assembly
  write_dash.py          # write factorbook.html
  pull_options_pulse.py  # options pulse + score v2
  momentum_screen.py
  early_warning.py
  clean_ingest.py
  ingest_cov.py
  dapi_keepalive.py
  troughing.py
  docs/
  scripts/
```

Stubs are marked `# TODO: sync from Desktop factorbook` until the desktop tree is copied in.
