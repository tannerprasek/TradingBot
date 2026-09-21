# Breakout / Breakdown tabs

Home **Momentum Up** already surfaces worked trends. **Breakout** / **Breakdown** catch **early inflections** — a score that just entered a selective band, a strong 10-day lift or drop, a fresh cross of 5, and the name beating or lagging its factor. Not a dump of the book, and not an opt-spike list.

No Sectors tab. No extra DAPI stage. Ranking reads card `score` / `mom_score`, `mom_score_d10` and the streak fields `mom_streak` already attaches, plus one dispersion field (below).

Tune every cut in the constants block at the top of [`breakout.py`](../breakout.py).

## Hard gates (all required)

| Gate | Breakout | Breakdown |
| --- | --- | --- |
| Score | **[7, 10]** inclusive. ≤6 is mush. 11–13 is already extended. | **[2, 6]** inclusive. 0–1 is already dead. ≥7 is not a breakdown. |
| 10d change | `mom_score_d10` **> 3**. Missing (fewer than 11 score prints) → out. Exact +3 is not enough. | `mom_score_d10` **< −3**. Missing → out. Exact −3 is not enough. |
| Streak | `mom_streak_side == above` and length **1–15**. **1** = just crossed above 5 today. **2–15** = fresh. **N>15** (`↑Nd>5`) is a baked trend → out. | Mirror: `below`, length **1–15**. **1** = just broke below 5. **N>15** baked down → out. A live score of **6 is still above 5**, so this gate keeps it out until the print is actually below 5. |
| Dispersion | **Clearly positive** (strictly > 0). | **Clearly negative** (strictly < 0). |

High score + long ↑ streak + flat or mild 10d is continuation, not Breakout. Low score + long ↓ streak + still falling hard is already dead, not Breakdown. An opt spike by itself is not a breakout and does not add rank.

## Dispersion field

1. **Factor residual** from the residual panel / `residual_last` already on disk, then enrich **`residual_20d`**, then `vs_group` / `vs_sleeve` on the card or `metrics`. A present **0 does not fall through**.
2. **RS63** when none of those exist. Live Mom cards show it as a labeled stat (`RS63` / `RS 63`, value before or after the label, or `data-k="rs63"`). Also `data-rs63`, card / `metrics.rs_63`. A trailing `%` is percent (`4.0%` → 0.04); a bare `0.17` stays `0.17`. `ensure_embedded` stamps that number on `data-dispersion` so the next Refresh keeps it. A name with no residual and no RS63 is skipped.

No synthetic residual is computed for this gate.

## Inflection score

Gates decide membership. Sort uses an inflection score (higher magnitude = earlier / more interesting):

```
magnitude =
    3.0 * band_early          # 1.0 on the early edge (breakout 7, breakdown 6), ~0.55 on the far edge
  + 1.6 * clip(|d10|, 0, 8) / 8
  + 2.0 * freshness           # 1.0 for streak 5–12; lower for a 1d cross and for 13–15
  + 1.4 * clip(|dispersion|, 0, 0.15) / 0.15
  + 0.35 * structure          # FLAGS + hop/leader only. Not required. Opt spike = 0.
```

`inflection_score` is **+magnitude** on Breakout and **−magnitude** on Breakdown. Breakout sorts descending. Breakdown sorts by the most negative inflection (same order as magnitude descending). `breakout_score` / `breakdown_score` stay the positive magnitude.

Display cap is **24** per side. That cap is a backstop — the gates are what keep the list to tens of names, not hundreds.

## UI

Top-nav **Breakout** / **Breakdown** sit beside Momentum Up / Down. Tabs **only rank/filter**. Clicking a tab fills `#view-breakout` / `#view-breakdown` (`.ph` + `.grid.dense` `#breakout-grid` / `#breakdown-grid`) with the **same card chrome as Momentum Up/Down**. Each of those two panes also has a quiet right-hand note (`.fd-bb-note`) that names both sides: Breakout is score 7–10, 10d Δ > +3, streak 1–15d above 5, dispersion > 0; Breakdown is the opposite (score 2–6, 10d Δ < −3, streak 1–15d below 5, dispersion < 0). It is inside the view, so it shows only while that tab is open — not on Home or Momentum.

- Ranked JSON (`#fd-breakout-db`) carries top-level numeric `day` / `r20` / `rs63` / `atr_pct` **and** a nested `metrics` blob (`r20_pct` / `rs_63` / `atr_pct`) — the shape live Momentum `cardHTML` actually reads (`c.metrics.r20_pct`, not top-level `r20`). Returns are decimals (`0.1277` → desk `fmtPct` → `12.8%`). `atr_pct` is already percent points (~2–5); do **not** `*100` again (`2.3` stays `2.3%`, not `230%`). Prefer `card.metrics` from the live book; else compute from `px_series` / `prices_long.csv` / live `px.by`.
- Live Desktop has **no** `#fd-mom-db`. `ensure_embedded` scrapes the ~396 embedded MOM card objects (`"metrics":{..."r20_pct":...}`) out of factorbook HTML and stamps those numbers onto ranked BB rows at rank/embed time (and onto an existing `#fd-breakout-db` when `ranked=None`).
- Breakout/Breakdown cards are the **same** Momentum Up/Down cards. Live desk chrome is `<div class="card">` (not only `<article class="card">`). `show` filters `#view-mom-up` / `#view-mom-down` (`article.card`, `div.card`, `.card[data-t]`, `.fd-card`) and `MOM.up|down|cards` when those panes have no cards, then **clones** each match into the grid, so `data-t`, status, streak, digest, and filter attributes stay on the card. A name with no DOM article is rendered with the same `cardHTML(card)`. A missing gate field skips that card. Dispersion is read from the card object, then from the live stat chrome (`RS63` / `data-dispersion`), not only from a single `RS63 <number>` regex. When the Mom universe is non-empty and nothing passes, the empty line is `No early inflections (0 of N passed)`. An empty `#fd-breakout-db` does not blank a full Mom book. Do **not** use the skinny `__FD_BB_PORTABLE_FIX__` / `denseHTML` substitute.
- **Card click opens the company name-drill** via `selectTicker(data-t)` — same as Mom Up. Capture-phase listener returns on `article.card` so it cannot `setView("breakdown")`.
- `breakout.ensure_embedded(html, ranked=None)` **does not skip the JS patch**. `#fd-breakout-js` is always replaced (version stamp `pr32-div-card`). `window.__FD_BB_SHOW__ =` is assigned whenever the Breakout/Breakdown nav is present (same binder rule as the live-desk nav check). `__FD_BB_BOUND__` is stamped only after that assignment. The same pass stamps `data-dispersion` on Mom cards and writes `#fd-dispersion-db`. Paper clicks are ignored (`kindOf` → `""`, marker `__FD_BB_IGNORE_PAPER__`); `#view-paper` is not in BB `NATIVE_VIEWS`.

Drill is `article.card.onclick → selectTicker`. Legacy `#fd-bb-breakout` / `#fd-bb-breakdown` stay empty and hidden so old CSS cannot paint skinny stub articles. `desk_dash.write_combined` always re-embeds `#fd-card-js` then `#fd-breakout-db` + nav + JS (`card_render.ensure_embedded` then `breakout.ensure_embedded`) so a Refresh rewrite cannot drop the renderer or the tabs. Live ~2.7–4.8MB `factorbook.html` is **patched**, never replaced.

**Desktop sync:** recopy `card_render.py` and `breakout.py` next to live `desk_dash.py`, then run `ensure_embedded` against the live ~4.8MB `factorbook.html` only. Do **not** wholesale replace live `desk_dash.py` and do **not** emit skinny ~190KB generator HTML. No CoS Desktop hot-patches — this pack is the durable path.

## Related

- Book-delta snapshot persist (strip UI BINNED): [`book_delta.py`](../book_delta.py), gitignored `desk_snapshot.json`
- Desk Analyst hitch pills: [`desk_hitch.py`](../desk_hitch.py), `FACTOR_DESK_IDEAS_DIR`
- Desktop copy list: [DEPLOY-HOOKS.md](../DEPLOY-HOOKS.md) §9
