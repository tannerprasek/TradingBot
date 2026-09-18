# Desktop merge hooks (live tree)

Copy `dapi_enrich.py` next to the live `add_server.py` at `C:\Users\MLP\Desktop\factorbook`. **Do not replace** the live `add_server.py` / `desk_dash.py` / `momentum_screen.py` / `pull_options_pulse.py`. Paste only the blocks below.

Writes `dapi_enrichment.json` beside `options_abnormal.json`. Missing file is fine. Capacity (`BLOOMBERG_LIMIT`) must not abort Refresh.

---

## 1) `add_server.py`

### Import (top of file, after existing imports)

```python
import dapi_enrich
```

### Parse `intraday=1` (default off)

Call from the live Refresh handler (query **or** JSON body). Keep your existing request parser; add this line.

```python
# query: dict[str, list[str]] from urlparse; body: dict | None from JSON POST
intraday = False
if query and "intraday" in query:
    intraday = dapi_enrich.parse_intraday_flag(query.get("intraday", [""])[0])
elif isinstance(body, dict) and "intraday" in body:
    intraday = dapi_enrich.parse_intraday_flag(body.get("intraday"))
```

Also: `GET /refresh?intraday=1` and `POST /refresh` `{"intraday": 1}`.

### Call site — inside `run_refresh_live` (prices → enrich → rebuild)

Main **Refresh** does **not** run the full-book options pulse. Skip / gate `pull_options_pulse` on that path. Progress **~50–60%** is still `dapi_enrich`, after prices, **before** `run_v0` / `write_dash` / `desk_dash`.

Pass the live Bloomberg session if you already have one (`session=`). Otherwise `dapi_enrich.open_dapi_session()` reuses `dapi_keepalive.get_session` / `ensure_session` / `session`. `options_by_name` may be `None` on main Refresh (skew stays whatever the last Options Refresh wrote).

```python
# --- dapi_enrich stage (50–60%) ---
# tickers: list[str] already used for prices / options
# prices_ctx: optional {ticker: {"px_last": ..., "ret_20d": ...}} from pull_blpapi_live
# options_by_name: optional {ticker: {"contracts": [...]}} from pull_options_pulse
# progress: your existing set_progress / status writer
try:
    if progress:
        progress(50, "dapi_enrich")
    _sess = session  # live blpapi.Session if you have it; else:
    if _sess is None:
        try:
            _sess = dapi_enrich.open_dapi_session()
        except dapi_enrich.CapacityError:
            _sess = None
            if progress:
                progress(60, "dapi_enrich skipped capacity")
    dapi_enrich.enrich_book(
        tickers,
        _sess,
        prices_ctx=prices_ctx if isinstance(prices_ctx, dict) else None,
        options_by_name=options_by_name if isinstance(options_by_name, dict) else None,
        intraday=intraday,
        root=Path(r"C:\Users\MLP\Desktop\factorbook"),  # or Path(__file__).resolve().parent
        progress_cb=(lambda frac, stage: progress(int(frac * 100), stage)) if progress else None,
    )
except dapi_enrich.CapacityError:
    # same degrade path as options pulse — continue Refresh
    if progress:
        progress(60, "dapi_enrich skipped capacity")
except Exception:
    # one-field / enrich failure must not kill Refresh
    logging.getLogger("add_server").exception("dapi_enrich non-fatal")
    if progress:
        progress(60, "dapi_enrich failed")
# --- then your existing rebuild: run_v0 / write_dash / desk_dash ---
```

Functions used: `dapi_enrich.parse_intraday_flag`, `dapi_enrich.open_dapi_session`, `dapi_enrich.enrich_book`, `dapi_enrich.CapacityError`.

---

## 2) `desk_dash.py`

### Import

```python
import dapi_enrich
```

### When you build each card (FLAGS / WATCH / MOM / OUTLIERS / OPTIONS)

After the card dict exists, before HTML. Safe if `dapi_enrichment.json` is missing.

```python
_book = dapi_enrich.load_enrichment()  # None if file absent
_rec = dapi_enrich.lookup_name(_book, card.get("ticker") or card.get("name") or "")
dapi_enrich.attach_card_fields(card, _rec)
# card now has: si_ratio, vol_regime, liq, inst_pct, event_days, beta, credit, enrich_pills, gics_sector_name
```

### Pills on the card (same language as `.badge` / `.spike-chip`)

```python
def enrich_pills_html(pills):
    bits = []
    for p in pills or []:
        bits.append(
            '<span class="badge spike-chip %s" data-key="%s">%s</span>'
            % (p.get("cls") or "", p.get("key") or "", p.get("label") or "")
        )
    return "".join(bits)
```

Insert `enrich_pills_html(card.get("enrich_pills"))` next to existing troughing / OPT SPIKE chips.

Functions used: `dapi_enrich.load_enrichment`, `dapi_enrich.lookup_name`, `dapi_enrich.attach_card_fields`.
Optional: `gics_filter.overlay_sector` (fills `gics_sector_name` from enrich or `gics_sectors.json` cache).

On each card `<article>` (or FLAGS/WATCH/MOM/OUTLIERS/OPTIONS row), add:

```html
data-gics-sector="{{ card.gics_sector_name or '' }}"
```

---

## 3) `momentum_screen.py`

### Import

```python
import dapi_enrich
```

### After each MOM row is built

```python
_book = dapi_enrich.load_enrichment()
_rec = dapi_enrich.lookup_name(_book, row.get("ticker") or row.get("name") or "")
dapi_enrich.attach_card_fields(row, _rec)
row["residual_20d"] = (_rec or {}).get("residual_20d")
row["watch_hint"] = (_rec or {}).get("watch_hint")
row["beta_field"] = (_rec or {}).get("beta_field")
```

Same card fields as desk: `si_ratio`, `vol_regime`, `liq`, `inst_pct`, `event_days`, `beta`, `credit`, `enrich_pills`.

---

## 4) `pull_options_pulse.py`

**Do not change score v2.** After the name record and `score_v2` are computed, attach skew from the chain you already have (DELTA / IVOL_MID / VOLUME / OI). Null Greeks stay null.

### Import

```python
import dapi_enrich
```

### After score v2 on each name (do not recompute score)

```python
# contracts: list[dict] already on the name (15C+15P). Do not invent DELTA/IV.
_skew = dapi_enrich.summarize_skew(contracts)
name_rec["skew"] = _skew
name_rec["iv_call_atm"] = _skew.get("call_iv_atm")
name_rec["iv_put_atm"] = _skew.get("put_iv_atm")
name_rec["skew_25d_proxy"] = _skew.get("skew_25d_proxy")
# leave name_rec["score_v2"] untouched
```

If Refresh already passes `options_by_name` into `enrich_book`, this drill summary is optional (enrich also stores `skew` per name). Keep it for the options view.

Function used: `dapi_enrich.summarize_skew`.

---

## 5) GICS sector chips (filter strip only)

**Do not** add a Sectors tab, `sectors.py`, `sectors.json`, or a Refresh stage. Copy `gics_filter.py` next to live `dapi_enrich.py`. Copy the updated `dapi_enrich.py` (GICS parse / `--gics-once` / cache). Paste the blocks below into live `desk_dash.py` / `factorbook.html`.

### `desk_dash.py`

```python
import gics_filter

# after attach_card_fields(card, _rec)
gics_filter.overlay_sector(card, _rec, cache=dapi_enrich.load_gics_cache())
```

When emitting each card/row, set `data-t` **and** `data-ticker` (already present) and `data-gics-sector`:

```python
sector = card.get("gics_sector_name") or ""
# <article class="card" data-t="..." data-ticker="..." data-gics-sector="{sector}">
```

In the **existing top filter strip** (next to G1–G12 / tags), add an empty host — JS fills chips:

```html
<span id="gics-filter-strip" class="filter-strip gics-chips" role="toolbar" aria-label="GICS sector filter"></span>
```

**Every HTML write** (end of live `write_combined` / `write_dash`) must re-embed a **filled** map + strip JS. Host/CSS surviving is not enough — `#gics-sector-db` and `STRIP_ID` were getting wiped.

```python
# after the combined HTML string exists, BEFORE dest.write_text
db = json.loads(desk_dash._gics_sector_db_json(book=book, cards=cards, cache=dapi_enrich.load_gics_cache(), root=root))
# or: db = gics_filter.filled_sector_db(cards, cache=..., book=book)
if "__GICS_SECTOR_DB__" in html:
    html = html.replace("__GICS_SECTOR_DB__", json.dumps(db, separators=(",", ":")))
html = gics_filter.ensure_embedded(html, db)
```

If live `factorbook.html` is already ~2.7MB with Refresh / Momentum Up / Momentum Down / Outliers / Options, **patch that file**. Do not replace it with the cloud skinny grid.

Functions used: `gics_filter.overlay_sector`, `gics_filter.filled_sector_db`, `gics_filter.ensure_embedded`, `gics_filter.strip_css`, `gics_filter.strip_js`, `desk_dash._gics_sector_db_json`, `desk_dash.write_combined`.

### live `factorbook.html` — CSS (paste with other G-chip rules)

```css
.filter-strip, .gics-chips {
  display: inline-flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px;
  margin: 0 0 10px;
  vertical-align: middle;
}
.filter-chip, .gchip {
  display: inline-block;
  font: 650 10px/1.15 "Segoe UI", "Segoe UI Symbol", "DejaVu Sans", "Noto Sans", ui-sans-serif, system-ui, sans-serif;
  letter-spacing: 0.04em;
  padding: 2px 7px;
  margin: 0;
  border-radius: 3px;
  border: 1px solid #6b7280;
  color: #d1d5db;
  background: #111827;
  text-transform: none;
  vertical-align: middle;
  cursor: pointer;
}
.filter-chip:hover, .gchip:hover { border-color: #9ca3af; color: #f3f4f6; }
.filter-chip.active, .gchip.active {
  color: #93c5fd;
  border-color: #60a5fa;
  background: #1e3a5f;
}
.filter-chip[data-gics-chip=""] { letter-spacing: 0.06em; }
.gics-hid { display: none !important; }
```

### live `factorbook.html` — JS

Paste `gics_filter.strip_js()` before `</body>` (or copy `gics_filter.py` and emit it from `desk_dash`). The script only toggles `.gics-hid` on cards/rows. It does not change Momentum Up/Down, Outliers, Options, Refresh, or FLAGS/WATCH selection.

Host: `#gics-filter-strip` inside the existing filter row. Map: `<script type="application/json" id="gics-sector-db">{...}</script>` ticker → `gics_sector_name`.

### Optional one-shot DAPI (not Refresh)

If `gics_sector_name` is missing on the book, run **once**:

```
python dapi_enrich.py --gics-once
```

Optional sidecar (do **not** call from `run_refresh_live`):

```python
# GET/POST /gics-fill  — paste only as a separate handler
dapi_enrich.fill_gics_sectors(tickers=tickers or None, root=Path(r"C:\Users\MLP\Desktop\factorbook"))
```

Writes gitignored `gics_sectors.json` and stamps `dapi_enrichment.json`. Refresh continues to skip GICS fields so this does not become a second full-book pull.

---

## 6) Momentum streak tag + `write_combined`

Copy `mom_streak.py` next to live `desk_dash.py`. Do **not** change options score v2 or FLAGS/WATCH/MOM ranking.

Live FLAGS/WATCH/MOM cards use **`t`** and **`score`** (not `ticker` / `mom_score`). `attach_card` reads `ticker or name or t or symbol` and sets `card["ticker"]` when missing. `resolve_card_score` already accepts `score` in `[0, 20]`.

### After the FLAGS / WATCH / MOM card list exists

Do **not** call `attach_all(cards)` with no hist — that ignores `mom_score_hist.json` and stamps `1d>5`. Rebuild hist first (auto-backfills from `prices_long.csv` when missing/thin):

```python
import mom_streak

# PATHS["fb_root"] is the live factorbook folder, e.g. Path(r"C:\Users\MLP\Desktop\factorbook")
hist = mom_streak.rebuild_hist_for_cards(cards, root=PATHS["fb_root"], write=True)
# cards now have mom_score, mom_streak, mom_streak_side, mom_streak_label
# and a .badge.spike-chip pill (↑12d>5 / ↓8d<5 / =5). Live card score wins for today.
```

Equivalent: `hist = mom_streak.load_hist(root=PATHS["fb_root"]); mom_streak.attach_all(cards, hist)`.

Score is the home UP/DOWN rank (`mom_score` first, then card `score` in 0–20). Threshold is literal **5**. Exactly 5 → streak 0, tag `=5`. See `docs/MOM-STREAK.md`.

### End of live `write_combined` / `write_dash.write`

Same tail as GICS — always re-embed, and **never clobber** a ~2.7MB live file:

```python
html = gics_filter.ensure_embedded(html, db)
html = mom_streak.ensure_embedded(html, mom_streak.streak_db(cards))
# then write factorbook.html
```

Cards should keep `data-t` (and `data-ticker`) so both GICS chips and streak tags resolve after a Refresh write.

### CoS deploy checklist (Desktop `C:\Users\MLP\Desktop\factorbook`)

| Copy into Desktop | Notes |
| --- | --- |
| `mom_streak.py` | new |
| `gics_filter.py` | replace with this version (`ensure_embedded`, `data-t`) |
| `desk_dash.py` hooks | `write_combined` tail + `_gics_sector_db_json`; do not replace FLAGS/WATCH/MOM |
| `write_dash.py` | `write()` → `desk_dash.write_combined` |
| `momentum_screen.py` hook | `mom_streak.attach_card(row)` after enrich attach |
| `dapi_enrich.py` | already has `gics_sector_name` parse / cache |
| `docs/MOM-STREAK.md`, `docs/GICS-FILTER.md` | optional, for the desk |

Do **not** copy generated `factorbook.html`, `dapi_enrichment.json`, `gics_sectors.json`, `mom_score_hist.json`. After drop-in, run Refresh once and confirm GICS chips + streak tags are still in the HTML source (`#gics-sector-db` filled, `STRIP_ID` present, `↑`/`↓`/`=5` pills).

Empty / thin `mom_score_hist.json` (missing, median series length < 5, or only today’s asof) **auto-backfills** from `prices_long.csv` using `trend_window_score` over the last ~120 trading days on `write_combined` / Refresh. Copy updated `mom_streak.py`. Do not commit the hist file.

---

## 7) Options Refresh button (Desktop, no CoS)

Main **Refresh** = prices + enrich + rebuild. Full-book options pulse is **manual only**.

### `add_server.py` — skip options on Refresh; add Options Refresh routes

In live `run_refresh_live`, do **not** call `pull_options_pulse` unless `options=1`. Smallest paste: treat query/body `options=1` (and `POST /options-refresh`) as pulse + rebuild, not prices/enrich.

```python
# query/body flag; default OFF
options = False
if query and "options" in query:
    options = dapi_enrich.parse_intraday_flag(query.get("options", [""])[0])
elif isinstance(body, dict) and "options" in body:
    options = dapi_enrich.parse_intraday_flag(body.get("options"))

# GET/POST /options-refresh  (also /api/options_refresh)
# GET/POST /refresh?options=1
# POST     /api/refresh_live  {"options": 1}
if options or path in ("/options-refresh", "/options_refresh", "/api/options_refresh", "/api/options-refresh"):
    # reuse your existing progress / busy lock (label stages with "options · …")
    set_progress(5, "options start")
    pull_options_pulse.run_pulse(tickers=tickers or None, progress_cb=...)  # full book
    # then the same rebuild you already use: run_v0 / write_dash / desk_dash
    # so Options tab / OPT SPIKE / abnormal scores refresh
```

Status JSON should include `kind` (`refresh` | `options`) and `options` (bool) so the progress bar can say it is an options run.

### `desk_dash.py` / live `factorbook.html` — button beside Refresh

Do **not** add `"Options Refresh"` to live-nav detection (that would make a patch look like a full replace). Always inject the button next to `#refresh`:

```html
<button type="button" id="options-refresh" class="nav-btn options-refresh" data-sidecar-options-refresh="1">Options Refresh</button>
<div id="sidecar-progress" class="sidecar-progress" hidden>
  <span id="sidecar-progress-label" class="sidecar-progress-label">idle</span>
  <div class="sidecar-progress-track"><div id="sidecar-progress-bar" class="sidecar-progress-bar"></div></div>
</div>
```

`write_combined` already calls `desk_dash._ensure_options_refresh_ui`. If you are not swapping `desk_dash.py`, paste that helper + `sidecar_js()` before `</body>`, and bind **only** `#options-refresh` (leave live `#refresh` alone unless it has `data-sidecar-refresh=1`).

Button talks to `http://127.0.0.1:8765` like Refresh. Restart the sidecar after the paste.

### Desktop checklist

| Copy / paste | Notes |
| --- | --- |
| Skip `pull_options_pulse` on main Refresh | prices → enrich → rebuild |
| `/options-refresh` + `options=1` on `/api/refresh_live` | pulse + rebuild; progress `kind=options` |
| `#options-refresh` beside Refresh | `desk_dash._ensure_options_refresh_ui` / `sidecar_js` |
| Restart `:8765` | no CoS; local sidecar only |

---

## 8) Chart tag-trigger polish + streak bounds

Copy `chart_marks.py` next to live `desk_dash.py`. End of live `write_combined`:

```python
import chart_marks

html = gics_filter.ensure_embedded(html, db)
html = mom_streak.ensure_embedded(html, mom_streak.streak_db(cards))
html = chart_marks.ensure_embedded(html, chart_marks.chart_db(cards))
# _ensure_options_refresh_ui(html)  # keep Options Refresh on Refresh rewrites
```

Overlay JS polishes existing `.tag-label` / `[data-tag-trigger]` / `.chart-anno` captions (cluster + collapse), draws streak start/end diamonds from `#fd-chart-db`, and recolors live name-drill polylines green/red by SMA50/SMA200 (Positive: close > both MAs) with thin 50-day (blue) / 200-day (maroon) overlays plus a `Positive ↑ / Negative ↓` legend. Generator `svg.fd-chart` is already painted in Python (`data-fd-trend=1`). It does not change score v2. Recopy `chart_marks.py` after this change.

Also copy `mom_streak.py` (now has `streak_span` / `mom_streak_start` / `mom_streak_end`).

---

## 9) Breakout / Breakdown tabs + book-delta strip + Desk Analyst hitch

Copy `card_render.py`, `breakout.py`, `book_delta.py`, `desk_hitch.py` next to live `desk_dash.py`. **Do not** reintroduce a Sectors tab. **Do not** add a DAPI stage. **Recopy `card_render.py` and `breakout.py`** after this hot-fix; **do not wholesale replace** live `desk_dash.py` (paste the `write_combined` tail below if that hook is not already there).

Live FLAGS/WATCH/MOM cards use **`t`** and **`score`**. Stamp `list` (`FLAGS` / `WATCH` / `OUTLIERS` / `MOM`) on each card dict when you already know the bucket — boosts and the delta strip read that field (plus `flags` / `watch` / `outlier` booleans and hop/leader / OPT SPIKE already on the card).

### End of live `write_combined` / `write_dash.write`

After the existing GICS / streak / chart `ensure_embedded` tail:

```python
import card_render
import breakout
import book_delta
import desk_hitch

# cards already attached (enrich + mom_streak). Hitch is optional / no-op if ideas dir missing.
hitch_index = desk_hitch.load_index(root=PATHS["fb_root"])
desk_hitch.attach_all(cards, hitch_index)
ranked = breakout.rank_book(cards, hist=hist)
snap = book_delta.snapshot_from_cards(cards, asof=str((hist or {}).get("asof") or ""))
delta = book_delta.diff_snapshots(book_delta.load_snapshot(root=PATHS["fb_root"]), snap)

html = gics_filter.ensure_embedded(html, db)
html = mom_streak.ensure_embedded(html, mom_streak.streak_db(cards))
html = chart_marks.ensure_embedded(html, chart_marks.chart_db(cards))
html = card_render.ensure_embedded(html)           # wrap live cardHTML; portable chip CSS
html = breakout.ensure_embedded(html, ranked)      # Breakout/Breakdown nav + view shells + #fd-breakout-db
html = book_delta.ensure_embedded(html, delta)      # #fd-book-delta strip
html = desk_hitch.ensure_embedded(html, desk_hitch.hitch_db(cards, hitch_index))
# dest.write_text(html)
book_delta.write_snapshot(snap, root=PATHS["fb_root"])  # gitignored desk_snapshot.json
```

**Never** call `breakout.ensure_embedded(html)` / `ensure_embedded(html, None)` on the live dash **as the only write** — that leaves `#fd-breakout-db` alone, but Desktop must still pass `ranked` so the JSON stays filled. `ranked=None` **does** refresh `#fd-breakout-js` (it must not skip the JS patch).

Breakout/Breakdown must render **the same `cardHTML` chrome as Momentum Up** (pills, status, streak, metrics, blurb, Buy/Sell) — **not** skinny `article.fd-bb-card` stubs and **not** a tab-specific dense fallback. `renderRow` finds the live MOM card by ticker and calls `cardHTML(card)`. `card_render.ensure_embedded` (before `breakout.ensure_embedded`) emits:

- `#fd-card-css` chip chrome on `article.card` (not scoped to a tab) so MA FAN / CLOSE HI / 52W HI / enrich pills match Momentum Up `.badge.spike-chip`
- `#fd-card-js` wrapping live `window.cardHTML` plus `window.__FD_RENDER_CARD__(card)` / `window.__FD_RENDER_ROW__(row)`
- Card lookup across `MOM.cards` **and** `MOM.up` / `MOM.down` / FLAGS / WATCH (empty `MOM.cards = []` must not hide the rest)

`breakout.ensure_embedded` emits:

- `#view-breakout` / `#view-breakdown` with `.ph` + `.grid.dense` `#breakout-grid` / `#breakdown-grid`
- empty hidden `#fd-bb-breakout` / `#fd-bb-breakdown` so old CSS cannot paint stubs
- `#fd-breakout-db` rows with top-level numeric `day`/`r20`/`rs63`/`atr_pct` **and** nested `metrics.r20_pct` / `rs_63` / `atr_pct` (the shape live `cardHTML` reads). Stats come from scraping the ~396 live HTML MOM card objects at embed time (there is **no** `#fd-mom-db`), else `px_series` / `prices_long.csv`. Nested `card` is never left `null` when a ticker exists.
- JS `window.__FD_BB_SHOW__(kind)` that reads `#fd-breakout-db`, looks up the MOM card by ticker, and calls **`cardHTML(card)`** (same function Mom Up uses). Capture-phase card click → `selectTicker`. `atr_pct` is already percent points — do not `*100` again.

### Live `setView` / `hideAllPanes` / `paintView` (required on Desktop)

Live factorbook `setView` only knows `home|mom-up|mom-down|outliers|options|sectors`. A Breakout click falls through → coerced to `home` → FLAGS/WATCH stay up. `hideAllPanes` that omits `#view-breakout` leaks the dense grid onto other tabs. `breakout.ensure_embedded` now:

- Appends `|breakout|breakdown` to that allowlist string in **`setView` and `paintView`** if present
- Injects an early-return at the top of `function setView(v)` that calls `window.__FD_BB_SHOW__(v)`, then `syncNav()`, and **does not** `paint()` and **does not** toggle legacy `#fd-bb-*` panes
- Appends `view-breakout` / `view-breakdown` to `hideAllPanes` id arrays / `#view-mom-*` selector lists, and injects an extra hide of those two ids
- Capture-phase click handler `preventDefault` + `stopPropagation` + `stopImmediatePropagation` so the native `#topnav` listener cannot reset to home
- `__FD_BB_SHOW__` hides `#home`, `#view-mom-up`, `#view-mom-down`, `#view-outliers`, `#view-options`, `#view-sectors`, `#search-pane` **and** shows the matching `#view-breakout` / `#view-breakdown` pane (class `hide`)

If you are not swapping `breakout.py` yet, paste into live `setView` / `hideAllPanes` / `paintView`:

```javascript
function setView(v) {
  if (v === "breakout" || v === "breakdown") {
    if (window.__FD_BB_SHOW__) window.__FD_BB_SHOW__(v);
    if (typeof syncNav === "function") syncNav();
    return; // do not paint(); do not toggle #fd-bb-* 
  }
  if (!/^(home|mom-up|mom-down|outliers|options|sectors|breakout|breakdown)$/.test(v)) v = "home";
  // ... existing hide/show + paint()
}

function hideAllPanes() {
  ["view-breakout","view-breakdown"].forEach(function(id){
    var el = document.getElementById(id); if (el) el.classList.add("hide");
  });
  // ... existing ids must include view-breakout / view-breakdown
}

function paintView(v) {
  if (v === "breakout" || v === "breakdown") {
    if (window.__FD_BB_SHOW__) window.__FD_BB_SHOW__(v);
    return;
  }
  if (!/^(home|mom-up|mom-down|outliers|options|sectors|breakout|breakdown)$/.test(v)) v = "home";
  // ...
}
```

Injected nav buttons use live chrome: `class="btn nav-btn"` (not bare `nav-btn`).

Cloud `desk_dash.write_combined` already does this. If you are **not** swapping live `desk_dash.py`, paste the tail. `breakout.ensure_embedded` injects **Breakout** / **Breakdown** buttons after Momentum Down even when the live nav already exists.

Dense cards come from the shared `card_render` wrap of live `cardHTML` — do not clone skinny stub articles and do not re-skin tags per tab. Formula / thresholds: `docs/BREAKOUT-BREAKDOWN.md` and the constants block in `breakout.py`.

### Desk Analyst hitch — ideas path

Pills (`DA·A` / `DA·B` / `DA·C` / `DA`) appear when a recent `YYYY-MM-DD-*.md` note exists for that ticker. Missing folder = silent no-op.

```python
# env wins; otherwise sibling ideas/ next to factorbook
# Desktop:
#   set FACTOR_DESK_IDEAS_DIR=C:\Users\MLP\Desktop\jr-analysts\ideas
# or copy/sync that folder to C:\Users\MLP\Desktop\factorbook\ideas
```

`desk_hitch.py` also looks at `fd_root/ideas`, `../ideas`, `../jr-analysts/ideas`, and `%USERPROFILE%\Desktop\jr-analysts\ideas`. Parse is conservative (frontmatter `ticker` / Action line / `$TICKER` / a single `**TICKER**`). Title = idea date + first thesis line + file path (`file://` when no URL).

### Desktop checklist

| Copy into `C:\Users\MLP\Desktop\factorbook` | Notes |
| --- | --- |
| `card_render.py` | **recopy** — wrap live `cardHTML`; portable chip CSS; `__FD_RENDER_CARD__` / `__FD_RENDER_ROW__`; Day/R20/RS63/ATR% aliases |
| `breakout.py` | **recopy** — `renderRow` = find MOM card by ticker → `cardHTML(card)` (full Mom chrome); scrape live HTML `"metrics":{r20_pct,rs_63,atr_pct}` onto `#fd-breakout-db`; capture click on BB cards → `selectTicker` (not `show(breakdown)`); `fmtAtr` does not `*100` when `abs(atr_pct)>=1` |
| `book_delta.py` | new — since-last-Refresh strip |
| `desk_hitch.py` | new — DA hitch pills |
| `desk_dash.py` hooks | paste `write_combined` tail above; **do not wholesale replace** live FLAGS/WATCH/MOM `desk_dash.py` |
| `docs/BREAKOUT-BREAKDOWN.md` | optional, for the desk |
| gitignore `desk_snapshot.json` | local, like `mom_score_hist.json` |

Do **not** copy generated `factorbook.html`, `desk_snapshot.json`, `mom_score_hist.json`, or the ideas markdown. After drop-in, call `card_render.ensure_embedded` then `breakout.ensure_embedded(html, ranked)` on the **live ~4.8MB** `factorbook.html` only — never replace it with skinny `desk_dash` generator HTML (~190KB). Refresh once and confirm: Breakout / Breakdown sit beside Momentum Down; cards are dense MOM chrome (no gray digest matrix); Day/R20/RS63/ATR% are numbers; **clicking a card opens the company name-drill** (`selectTicker`) and does **not** jump to the Breakdown tab; `#fd-card-js` + `#fd-breakout-db` are filled; Paper (`#fd-paper-marks`) and Experimental stay; `#fd-book-delta` shows `baseline set` on the first write. No CoS Desktop hot-patches — recopy the modules.

Suggested Desktop sync paths:

- Code: `factor-desk/*.py` → `C:\Users\MLP\Desktop\factorbook\`
- Then patch live HTML in place (`ensure_embedded`); do not emit a new skinny desk
- Ideas: `C:\Users\MLP\Desktop\jr-analysts\ideas\` → env `FACTOR_DESK_IDEAS_DIR` or a synced copy at `C:\Users\MLP\Desktop\factorbook\ideas\`

---

## 10) Paper trading (Buy / Sell on dense MOM cards + Paper tab)

Copy `paper_trade.py` next to live `desk_dash.py`. **Do not wholesale replace** live `desk_dash.py` (FLAGS / WATCH / MOM chrome stays). Paper only — no brokerage. 1 unit notional; no size UI. No new DAPI stage and no extra data feed.

Same-side click while a position is open is a **no-op + brief toast** (`Already long — sell to close` / `Already short — buy to close`). It does not add size and does not flip. Opposite click **closes** (flat + Buy = long, flat + Sell = short).

The book lives on a top-nav **Paper** tab (`#fd-nav-paper`, `data-view="paper"`, `#view-paper`) — **not** a home chrome strip next to BOOK / baseline:

- **Open from the tab** — ticker field + Buy / Sell at the current mark (same `#fd-paper-marks` / MOM / last-print resolver as card buttons).
- **Open book** — `table.fd-paper-table` of every open paper long/short from `localStorage` key `fd-paper-book` (same store as the card Buy/Sell). Columns: ticker, `LONG`/`SHORT`, entry (tabular/mono), live P&L % (green ≥0, rose &lt;0), plus inline **Close** (opposite click: long → sell, short → buy). Click ticker → live `selectTicker` when that function exists. Not a pill/chip stack.
- **Week scorecard** — closed trades **since Monday 00:00 `America/Edmonton`**: closed count, hit rate (% of those closes with **positive signed** return), avg win % and avg loss % (shorts profit when price falls; losses stay negative).
- **Closed trades** — collapsible `table.fd-paper-table` of the full local book (date, ticker, side, entry, exit, P&L %).
- Empty copy: no opens → `no open paper`; no closes this week → `no closed yet this week`.
- Persistence is unchanged: **only** `fd-paper-book`. Do not add a sidecar write on Refresh.
- `ensure_embedded` **strips** leftover `#fd-paper-home` from top chrome so a previous Home-strip recopy cannot jam chips next to BOOK delta.

### End of live `write_combined` / `write_dash.write`

After the existing GICS / streak / chart / breakout / hitch `ensure_embedded` tail:

```python
import card_render
import paper_trade

# cards already attached (enrich + mom_streak). Marks come from card px / PX_LAST / last Refresh print.
paper_marks = paper_trade.marks_db(cards, book=book)

html = gics_filter.ensure_embedded(html, db)
html = mom_streak.ensure_embedded(html, mom_streak.streak_db(cards))
html = chart_marks.ensure_embedded(html, chart_marks.chart_db(cards))
html = card_render.ensure_embedded(html)           # wrap live cardHTML; portable chips
html = breakout.ensure_embedded(html, ranked)
html = book_delta.ensure_embedded(html, delta)
html = desk_hitch.ensure_embedded(html, desk_hitch.hitch_db(cards, hitch_index))
html = paper_trade.ensure_embedded(html, paper_marks)  # wraps cardHTML + Paper tab; localStorage fd-paper-book
# dest.write_text(html)
```

Optional on the enrich attach path (cloud `_article_html` already does this):

```python
paper_trade.attach_mark(card, _rec)
# card.paper_mark / px_last when a print exists
```

`ensure_embedded` wraps live `window.cardHTML` so home / Momentum Up / Down / Breakout / Breakdown / search cards that go through `cardHTML` get generic **Buy** / **Sell**, an open-position line (`LONG`/`SHORT` + entry + live P&L %), and a collapsed **Previous trades** `<details>`. It injects a **Paper** nav button (after Experimental if present, else after Options) plus `#view-paper`, and patches live `setView` / `hideAllPanes` / `paintView` with `|paper` (capture-phase click bridge, same pattern as Experimental / Breakout). Leftover `#fd-paper-home` is removed. Missing mark disables both buttons (`title` = need card price / `PX_LAST` / last Refresh print). Persistence is **localStorage** (`fd-paper-book`); the module also exports `sidecar_schema()` if a later `paper_book.json` sidecar is wanted. Do not add a brokerage hook.

Cloud `desk_dash.write_combined` already calls this. If you are **not** swapping live `desk_dash.py`, paste the tail. Recopy `paper_trade.py` after this drop-in.

### Desktop checklist

| Copy into `C:\Users\MLP\Desktop\factorbook` | Notes |
| --- | --- |
| `paper_trade.py` | **recopy** — Buy/Sell on dense `cardHTML` cards **and** Paper tab (inline open + Close); strips `#fd-paper-home`; localStorage `fd-paper-book` only |
| `card_render.py` | **recopy** — must wrap `cardHTML` *before* paper so Buy/Sell hydrate polished cards |
| `breakout.py` | **recopy** — hide `#view-paper` from Breakout/Breakdown; grids call `__FD_RENDER_ROW__` (titles from `d` / ticker) |
| `desk_dash.py` hooks | paste `write_combined` tail above; **do not wholesale replace** live FLAGS/WATCH/MOM `desk_dash.py` |
| `docs/PAPER-TRADE.md` | optional, for the desk |

Do **not** copy generated `factorbook.html` or a later `paper_book.json`. After drop-in, Refresh once (or hard-reload) and confirm:

1. Home chrome next to BOOK / baseline has **no** paper LONG/SHORT chips. Paper is a top-nav tab after Options / near Experimental.
2. **Paper** shows opens + live %, inline **Close**, ticker + Buy/Sell, week scorecard, and collapsible closed trades.
3. Dense cards still show Buy/Sell (disabled with a title if no mark), a long/short line + live % when a position is open, and collapsed previous trades with the correct short sign (short profits when price falls).
4. Buy/Sell on a card and Close / Buy / Sell on the tab share `fd-paper-book`.
5. Week scorecard counts closes since Monday 00:00 America/Edmonton; empty copy is `no closed yet this week`.
6. Reload / Refresh keeps the book (localStorage). Mom Up/Down chrome is otherwise unchanged.
7. Breakout / Breakdown cards show real tickers (`AMGN`, …), not `"undefined"`.

---

## 11) Experimental residual S-score (Avellaneda–Lee on SparsePCA residuals)

Copy `s_score.py` next to live `desk_dash.py`. **Do not wholesale replace** live `desk_dash.py` (FLAGS / WATCH / MOM chrome stays; home still opens FLAGS→WATCH→MOM). **Not** a new PCA engine. **Not** a FLAGS rebrand. Pointers only — no auto-enter, no paper auto-open. Do **not** show A–L paper Sharpes as desk KPIs.

**Isolation:** S-score / κ UI is the Experimental tab only. Do **not** add S-score pills onto live MOM / FLAGS / home cards. Do **not** change the default home ritual or production chrome.

Also recopy `breakout.py` (hides `#view-experimental` when Breakout/Breakdown is selected so the Experimental pane cannot leak onto those tabs).

### Residual panel (confirm on Desktop)

Refresh / `write_combined` discovers, in order:

1. Residual **history**: `v0_residuals.csv`, `residuals.csv`, `residual_panel.json` / `.csv`, `sparse_pca_residuals.csv`, `clean/…`
2. Else reconstruct ε from `prices_long.csv` (or `returns*.csv`) **plus** existing SparsePCA loadings (`sparse_pca_loadings.csv`, `pca_loadings.csv`, `loadings.csv`, `v0_loadings.csv`, …) and optional membership
3. `residual_last.csv` / `.json` is appended as **one day** so a panel can accumulate — it is **not** enough to fit κ / S-score by itself

Writes gitignored `residual_panel.json`. `residual_20d` (beta vs SPY) is a different engine and is **not** used.

**PIT is not claimed.** Full-sample SparsePCA membership/loadings are look-ahead. The Experimental banner says `PIT not claimed` plus `full_sample_loadings` / `unknown_sparsepca_fit` / `residual_last_only`. Prefer a rolling window on Desktop; do not silently stamp PIT.

Literature defaults (labeled experimental, not a proven edge): open `|s|~1.25`, close `~0.75`, reject slow κ (half-life > 30 trading days).

### End of live `write_combined` / `write_dash.write`

After the existing GICS / streak / chart / breakout / hitch / paper `ensure_embedded` tail:

```python
import s_score

# Experimental-only. Materializes residual_panel.json when history is thin.
ss_ranked = s_score.rank_book(cards, root=PATHS["fb_root"], write_panel_file=True)

html = gics_filter.ensure_embedded(html, db)
html = mom_streak.ensure_embedded(html, mom_streak.streak_db(cards))
html = chart_marks.ensure_embedded(html, chart_marks.chart_db(cards))
html = breakout.ensure_embedded(html, ranked)
html = book_delta.ensure_embedded(html, delta)
html = desk_hitch.ensure_embedded(html, desk_hitch.hitch_db(cards, hitch_index))
html = paper_trade.ensure_embedded(html, paper_marks)
html = s_score.ensure_embedded(html, ss_ranked)  # Experimental tab; does not wrap cardHTML
# dest.write_text(html)
```

`ensure_embedded` injects a top-nav **Experimental** button (after Options, else after Breakdown), `#view-experimental` (hidden until selected) + `#sscore-grid` dense cards ranked by `|S-score|` (ticker, S-score, κ/half-life, fade-high / buy-low, entry/exit bands), `#fd-sscore-db`, and JS that patches live `setView` / `hideAllPanes` with `|experimental`. It does **not** wrap `cardHTML`, so production paper Buy/Sell chrome is unchanged.

Cloud `desk_dash.write_combined` already calls this. If you are **not** swapping live `desk_dash.py`, paste the tail. Recopy `s_score.py` after this drop-in.

Optional sidecar paste in live `run_rebuild_stage` (non-fatal, not a DAPI stage) — `write_combined` already materializes the panel, so this is only a progress label:

```python
# after run_v0, before write_dash
try:
    import s_score
    s_score.materialize_panel(root=PATHS["fb_root"], write=True)
except Exception:
    logging.getLogger("add_server").exception("s_score panel non-fatal")
```

### Desktop checklist

| Copy into `C:\Users\MLP\Desktop\factorbook` | Notes |
| --- | --- |
| `s_score.py` | **new** — Experimental S-score tab + residual panel writer |
| `breakout.py` | **recopy** — hide `#view-experimental` from Breakout/Breakdown |
| `paper_trade.py` | **recopy** — skip Buy/Sell hydration on `.fd-ss-card` / `#view-experimental` / `#view-paper` |
| `desk_dash.py` hooks | paste `write_combined` tail above; **do not wholesale replace** live FLAGS/WATCH/MOM `desk_dash.py` |
| `docs/S-SCORE.md` | optional, for the desk |

Do **not** copy generated `factorbook.html` or `residual_panel.json`. After drop-in, Refresh once and confirm:

1. Home still opens FLAGS / WATCH / MOM (Experimental is a separate tab after Options).
2. **Experimental** shows residual S-score ranks (or an honest empty reason if the panel is thin / missing).
3. Production FLAGS / WATCH / MOM / Breakout / paper Buy/Sell chrome is unchanged. Experimental is a separate top-nav tab; no S-score / κ chrome on live MOM / FLAGS / home cards.
4. Banner states **experimental**, panel depth × names, source, and **PIT not claimed**.
5. No Sharpe numbers anywhere on the desk.

Suggested Desktop sync path: `factor-desk/*.py` → `C:\Users\MLP\Desktop\factorbook\`.

---

## 12) Paper topnav — do not bounce to Home

Live diagnosis (2026-09-18): Breakout `kindOf` returned `"other"` for Paper → `show("")` → `hideNativeViews()` (and `NATIVE_VIEWS` included `view-paper`). Experimental S-score, registered **after** Paper on document capture, also treated Paper as `"other"` → `show(false)` **unhid `#home`**. Paper’s handler only `stopPropagation`, so SS still ran. Chart sync later wiped `__FD_BB_IGNORE_PAPER__` / `__FD_SS_IGNORE_FOREIGN_NAV__`.

Durable SoT (matches the Desktop HTML hotfix):

1. `breakout.py` `kindOf`: `data-view===paper` / `#fd-nav-paper` / `data-fd-paper-nav` → `""` (do not `show("")`). Marker `__FD_BB_IGNORE_PAPER__`.
2. BB `NATIVE_VIEWS`: **no** `view-paper` (Paper owns `#view-paper`).
3. `s_score.py` `kindOf`: Paper / Breakout / Breakdown → `""`. Marker `__FD_SS_IGNORE_FOREIGN_NAV__`.
4. `paper_trade.py`: on `kind==="paper"`, `stopImmediatePropagation` **before** `showPaper(true)`. Keep `setView("paper")` → `__FD_PAPER_SHOW__`.

Do **not** wholesale replace live `desk_dash.py`. Do **not** copy skinny generator HTML. Do **not** patch `C:\Users\MLP\Desktop\factorbook\factorbook.html`.

### Copy next to live `desk_dash.py`

Pack folder: `C:\Users\MLP\Desktop\factorbook`

| File | Notes |
| --- | --- |
| `breakout.py` | **recopy** — `__FD_BB_IGNORE_PAPER__`; `kindOf` paper → `""`; drop `view-paper` from `NATIVE_VIEWS`; card→drill `cardHTML` / `selectTicker` unchanged |
| `paper_trade.py` | **recopy** — `stopImmediatePropagation` before `showPaper(true)`; `__FD_PAPER_SHOW__` path intact |
| `s_score.py` | **recopy** — `__FD_SS_IGNORE_FOREIGN_NAV__`; `kindOf` paper/BB → `""` |

### Then patch **`C:\Users\MLP\Desktop\factorbook.html` only**

From `C:\Users\MLP\Desktop\factorbook` (after the copies):

```bat
cd /d C:\Users\MLP\Desktop\factorbook
copy /Y breakout.py .
copy /Y paper_trade.py .
copy /Y s_score.py .
python -c "from pathlib import Path; import breakout, paper_trade, s_score; p=Path(r'C:\Users\MLP\Desktop\factorbook.html'); t=p.read_text(encoding='utf-8'); t=breakout.ensure_embedded(t); t=paper_trade.ensure_embedded(t); t=s_score.ensure_embedded(t); p.write_text(t, encoding='utf-8'); print('patched', p, 'bytes', p.stat().st_size)"
```

`ensure_embedded` order matches live `write_combined`: Breakout, then Paper, then Experimental. Live HTML is the **sibling** `C:\Users\MLP\Desktop\factorbook.html`, not `Desktop\factorbook\factorbook.html`. Hard-reload (Ctrl+F5). Confirm Paper stays on Paper (`#view-paper.fd-paper-on`, `#home` stays hidden) and Breakout card click still opens name-drill.


