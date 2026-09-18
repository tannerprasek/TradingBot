# Breakout / Breakdown tabs

Home **Momentum Up** already surfaces maxed names (score ~12–13). **Breakout** / **Breakdown** are the mid-score climbers and crackers — inspiration for Desk Analysts A/B/C, not a second MOM dump.

No Sectors tab. No extra DAPI stage. Ranking reads live card `t` / `score` (or `mom_score`) plus `mom_score_hist.json` / `mom_streak` series.

Tune every cut in the constants block at the top of [`breakout.py`](../breakout.py).

## Selection philosophy

| Tab | Band (0–13 MOM rank) | Direction | Streak | Cap |
| --- | --- | --- | --- | --- |
| **Breakout** | **6–11** (exclude 12–13 already there; ≤5 only if accelerating hard, Δ≥2 and score≥4) | score today **>** score N trading days ago | `mom_streak_side==above`, prefer **2–20d** just flipped/climbing over a 100d baked run | **8–12** max |
| **Breakdown** | **2–7** (exclude 0–1 dead) | falling Δ | `below`, fresh/worsening 2–20d over ancient downs | **8–12** max |

Lookback **N** prefers **5–10** trading days (default **7**).

## `breakout_score` / `breakdown_score`

Single rank key per side. Higher is better. Names below `MIN_COMPOSITE` are dropped so the cap is not filled with junk.

```
breakout_score =
    3.0 * band_fit(score, lo=6, hi=11, sweet=7–10)
  + 1.6 * clip(Δ_7d, -4, +4) / 4
  + 2.0 * streak_freshness(streak, side==above)
  + optional field boosts
  + accel boost (last 3d Δ > prior 3d Δ, and Δ>0)
  + 1.0 if the name only qualifies via the ≤5 hard-accel exception

breakdown_score =
    3.0 * band_fit(score, lo=2, hi=7, sweet=3–6)
  + 1.6 * clip(-Δ_7d, -4, +4) / 4
  + 2.0 * streak_freshness(streak, side==below)
  + optional field boosts
  + accel boost (last 3d Δ < prior 3d Δ, and Δ<0)
```

`band_fit` is 1.0 in the sweet range, linear falloff to 0 just outside the band.

`streak_freshness` is 1.0 on a 2–12d run on the wanted side, 0.75 through 20d, then decays to ~0.05 by 100d (already baked). Wrong side → 0.

Optional boosts (cheap card fields only — **no DAPI pull**):

| Signal | Weight | Source |
| --- | --- | --- |
| FLAGS | +0.8 | `list` / `flags` / `lists` |
| hop / leader | +0.5 | `tag_triggers` / tags text |
| OPT SPIKE | +0.6 | `opt_spike` / pill `opt-spike` |
| outlier newbie | +0.4 | OUTLIERS + streak ≤5d |

## UI

Top-nav **Breakout** / **Breakdown** sit beside Momentum Up / Down. Tabs **only rank/filter**. Clicking a tab fills `#view-breakout` / `#view-breakdown` (`.ph` + `.grid.dense` `#breakout-grid` / `#breakdown-grid`) with **dense MOM-style cards**:

- Ranked JSON (`#fd-breakout-db`) carries top-level numeric `day` / `r20` / `rs63` / `atr_pct` **and** a nested `metrics` blob (`r20_pct` / `rs_63` / `atr_pct`) — the shape live Momentum `cardHTML` actually reads. Returns are decimals (`0.1277` → desk `fmtPct` → `12.8%`). `atr_pct` is already percent points (~2–5). Prefer `card.metrics` from the live book; else compute from `px_series` / `prices_long.csv` / live `px.by`.
- [`card_render.py`](../card_render.py) still wraps live `cardHTML` for Momentum Up/Down / Paper. Breakout JS **never** calls `cardHTML`. If `__FD_RENDER_ROW__` is missing or paints the 12-label gray digest matrix, Breakout builds its own dense card (ticker, score, Day/R20/RS63/ATR% row, spike chips).
- **Card click opens the company name-drill** via `selectTicker(data-t)` — same as Mom Up. The capture-phase nav listener matches **nav buttons only** (`#topnav .nav-btn`, `button[data-view]`, `#fd-nav-breakout` / `#fd-nav-breakdown`). It does **not** match `#view-breakout` / `#view-breakdown` `[data-view]`, so a click on a card inside the pane cannot call `show("breakdown")`.
- BB “why” is extra chips (`band 10`, `+3/7d`) plus the existing `mom-streak` pill — **not** a brown `.why` dump and not a gray tag matrix (MA FAN / CLOSE HI / …).
- `breakout.ensure_embedded(html, ranked=None)` **does not skip the JS patch**. `#fd-breakout-js` is always replaced (version stamp `pr17-metrics-drill`) so an old `__FD_BB_BOUND__` IIFE cannot keep the `cardHTML` fallback alive.

Drill is `article.card.onclick → selectTicker`. Legacy `#fd-bb-breakout` / `#fd-bb-breakdown` stay empty and hidden so old CSS cannot paint skinny stub articles. `desk_dash.write_combined` always re-embeds `#fd-card-js` then `#fd-breakout-db` + nav + JS (`card_render.ensure_embedded` then `breakout.ensure_embedded`) so a Refresh rewrite cannot drop the renderer or the tabs. Live ~2.7–4.8MB `factorbook.html` is **patched**, never replaced.

**Desktop sync:** recopy `card_render.py` and `breakout.py` next to live `desk_dash.py`, then run `ensure_embedded` against the live ~4.8MB `factorbook.html` only. Do **not** wholesale replace live `desk_dash.py` and do **not** emit skinny ~190KB generator HTML. No CoS Desktop hot-patches — this pack is the durable path.

## Related

- Book-delta strip (since last Refresh): [`book_delta.py`](../book_delta.py), gitignored `desk_snapshot.json`
- Desk Analyst hitch pills: [`desk_hitch.py`](../desk_hitch.py), `FACTOR_DESK_IDEAS_DIR`
- Desktop copy list: [DEPLOY-HOOKS.md](../DEPLOY-HOOKS.md) §9
