# Desktop merge hooks (live tree)

Copy `dapi_enrich.py` and `sectors.py` next to the live `add_server.py` at `C:\Users\MLP\Desktop\factorbook`. **Do not replace** the live `add_server.py` / `desk_dash.py` / `momentum_screen.py` / `pull_options_pulse.py`. Paste only the blocks below.

Writes `dapi_enrichment.json` and `sectors.json` beside `options_abnormal.json`. Missing files are fine. Capacity (`BLOOMBERG_LIMIT`) must not abort Refresh.

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
# card now has: si_ratio, vol_regime, liq, inst_pct, event_days, beta, credit, enrich_pills
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

## 5) Sectors tab (`sectors.py`)

Copy `sectors.py` into the live factorbook folder. Do **not** rewrite FLAGS / WATCH / MOM / OPTIONS.

### `add_server.py` — after dapi_enrich, before rebuild (progress ~60–65%)

```python
import sectors

# tickers, prices_ctx, enrich `book` already in hand from run_dapi_enrich_stage
try:
    if progress:
        progress(60, "sectors")
    sectors.run_sectors_stage(
        tickers,
        prices_ctx=prices_ctx if isinstance(prices_ctx, dict) else None,
        enrich_book=book,
        root=Path(r"C:\Users\MLP\Desktop\factorbook"),
        progress_cb=(lambda frac, stage: progress(int(frac * 100), stage)) if progress else None,
    )
except Exception:
    logging.getLogger("add_server").exception("sectors non-fatal")
    if progress:
        progress(65, "sectors failed")
# --- then existing rebuild: run_v0 / write_dash / desk_dash ---
```

Optional sidecar read for the HTML (file:// dashboards cannot fetch local JSON):

```python
# in do_GET, path /sectors or /sectors.json
payload = sectors.load_sectors() or sectors.empty_book(reason="missing")
# write JSON response, same CORS as /status
```

### `desk_dash.py` / live `factorbook.html`

1. Add a **Sectors** nav button next to Home / Momentum / Options (`data-tab="sectors"`).
2. Add `<section id="tab-sectors">` using `sectors.panel_markup(book)`.
3. Embed the book: `sectors.embed_json("sectors-db", book)` then `<script>` + `sectors.tab_js()`.
4. Include `sectors.tab_css()` in the existing dark-desk stylesheet.

`write_dash` / `desk_dash.assemble_and_write` already load `sectors.json` when this cloud `desk_dash.py` is the one writing HTML. If Desktop keeps its own writer, paste the four steps above; keep existing FLAGS/WATCH/MOM/OPTIONS markup untouched.

Functions used: `sectors.run_sectors_stage`, `sectors.load_sectors`, `sectors.embed_json`, `sectors.panel_markup`, `sectors.tab_css`, `sectors.tab_js`.

