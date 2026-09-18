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
  gics_filter.py         # GICS sector chips (filter strip, not a Sectors tab)
  mom_streak.py          # home momentum rank streak vs 5
  card_render.py         # portable MOM cardHTML wrap (one card, every tab)
  breakout.py            # Breakout / Breakdown tab ranking
  book_delta.py          # since-last-Refresh chip strip
  desk_hitch.py          # Desk Analyst hitch pills
  paper_trade.py         # paper Buy/Sell on dense cardHTML cards + Paper tab
  s_score.py             # Experimental residual S-score (Avellaneda–Lee on SparsePCA residuals)
  chart_marks.py         # chart tag-trigger polish + streak begin/end + MA trend
  add_server.py          # sidecar HTTP on :8765 — Refresh + intraday=1
  desk_dash.py           # dashboard assembly + write_combined
  write_dash.py          # write factorbook.html via write_combined
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

Refresh: `prices → dapi_enrich (~50–60%) → rebuild`. Options pulse is a separate **Options Refresh** button (`/options-refresh`, or `options=1`).
Optional: `GET/POST /refresh?intraday=1` (default off). See [docs/DAPI-ENRICH.md](docs/DAPI-ENRICH.md).
GICS sector chips read `gics_sector_name` already on the enrich file; optional one-shot `python dapi_enrich.py --gics-once` (not part of Refresh). `write_combined` re-embeds filled `#gics-sector-db` + strip JS on every HTML write. See [docs/GICS-FILTER.md](docs/GICS-FILTER.md).
Home cards: momentum streak vs score 5 (`↑12d>5` / `↓8d<5`). Name-drill charts color close green/red vs SMA50/SMA200. See [docs/MOM-STREAK.md](docs/MOM-STREAK.md).
Breakout / Breakdown tabs (mid-score climbers / crackers), a since-last-Refresh delta strip, and optional Desk Analyst hitch pills: [docs/BREAKOUT-BREAKDOWN.md](docs/BREAKOUT-BREAKDOWN.md).
Paper Buy/Sell on dense MOM cards plus a Home open-book / week-scorecard strip (localStorage, 1 unit, no brokerage): [docs/PAPER-TRADE.md](docs/PAPER-TRADE.md).
Experimental residual S-score (Avellaneda–Lee layer on existing SparsePCA residuals; pointers only, own top-nav tab): [docs/S-SCORE.md](docs/S-SCORE.md).

Desktop copies `dapi_enrich.py` into `C:\Users\MLP\Desktop\factorbook` and merges the thin hooks. Remaining ingest/troughing modules stay Desktop-owned until synced.
