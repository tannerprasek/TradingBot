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

### Call site — after options pulse, inside `run_refresh_live`

Progress **~50–60%**. After `pull_options_pulse` (full book), **before** `run_v0` / `write_dash` / `desk_dash`.

Pass the live Bloomberg session if you already have one (`session=`). Otherwise `dapi_enrich.open_dapi_session()` reuses `dapi_keepalive.get_session` / `ensure_session` / `session`.

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

When emitting each card/row, set `data-ticker` (already present) and `data-gics-sector`:

```python
sector = card.get("gics_sector_name") or ""
# <article class="card" data-ticker="..." data-gics-sector="{sector}">
```

In the **existing top filter strip** (next to G1–G12 / tags), add an empty host — JS fills chips:

```html
<span id="gics-filter-strip" class="filter-strip gics-chips" role="toolbar" aria-label="GICS sector filter"></span>
```

Embed the ticker→sector map once per rebuild (from enrich / cache, not a hardcoded map):

```python
# after cards are attached
db = gics_filter.sector_db(cards)  # or gics_filter.sector_db(book)
html += gics_filter.embed_db(db)
html += "<script>\n" + gics_filter.strip_js() + "\n</script>"
```

Add `gics_filter.strip_css()` to the existing dark-desk stylesheet (or paste the CSS below).

Functions used: `gics_filter.overlay_sector`, `gics_filter.sector_db`, `gics_filter.embed_db`, `gics_filter.strip_css`, `gics_filter.strip_js`.

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

