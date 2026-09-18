# Momentum score streak tag

Compact home-card tag for how many **trading days** a name has been **above or below a momentum score of 5**. Same `.badge` / `.spike-chip` language as SI / IV / EVT pills. **No Sectors tab.**

## Score definition

The number on Momentum Up / Down cards is a small **0–13-style rank**, not options `score_v2` and not the scrapped Sectors 0–100 `trend_score`.

Resolver (first hit), implemented in `mom_streak.resolve_card_score`:

1. Card/row fields already used for UP/DOWN ranking: `mom_score`, `momentum_score`, `mom_rank`, `trend_rank`. Live FLAGS/WATCH/MOM cards often store the name as **`t`** and the rank as **`score`** (0–20) rather than `ticker` / `mom_score` — `attach_card` reads `ticker or name or t or symbol` and sets `card["ticker"]` when missing.
2. `score` or `trend_score` **only if** the value is in `[0, 20]` (rejects 0–100 percentiles and huge option scores).
3. Durable history already on disk:
   - `mom_score_hist.json` (written each rebuild)
   - score CSVs: `mom_scores.csv`, `score_hist.csv`, `v0_scores.csv`, `clean/…`
   - **v0 residual files are used only when they have a `score` / `mom_score` column.** A residual-only CSV is not this rank.
4. If still missing **or hist is empty/thin** (median series length < 5, or only today’s asof): **13-horizon trend-window count** from `prices_long.csv` / `prices.csv` (date, ticker, `adj_close`/`close`). Score = how many of `TREND_WINDOWS = (5, 10, 15, 20, 25, 30, 40, 50, 60, 80, 100, 150, 200)` have a positive total return → **0–13**. Auto-backfill writes ~120 trading days into `mom_score_hist.json` (`meta.source` like `prices_long.trend_windows_backfill`) so the first Refresh after deploy is not every name `1d>5`. Live card `mom_score` still wins for *today’s* point.

Live FLAGS/WATCH/MOM HTML is Desktop-owned; this pack does not invent a second rank when the card already shows one.

## Streak rules

Threshold is literal **5**.

| Today | Side | Streak | Tag |
| --- | --- | --- | --- |
| `score > 5` | above | consecutive trading days `> 5`, including today | `↑12d>5` |
| `score < 5` | below | consecutive trading days `< 5`, including today | `↓8d<5` |
| `score == 5` | **neither** | **0** | `=5` (visible at-cut, not missing data) |
| null | — | no tag | — |

A day at 5, or a missing print, breaks an above/below run.

## Chart marks

Home-card charts keep **tag-trigger** marks (already on the live desk) and add **streak bounds**. Name-drill / detail charts (and card sparks when a close series is present) use the same **2/10 yield-curve** visual language: green/red path + 50/200-day MA overlays.

- Tag triggers: amber triangles along the **top** of the plot. Labels within ~12px collapse to one caption (`G4 · EVT`) so they do not stack over the price line.
- Streak begin / end: teal (above) or rose (below) **diamonds on the price series** (no full-height stem), tiny `s` / `now` (or `e`) captions along the **bottom** gutter — a different glyph/color than tag triangles. Open (still-running) end is a **hollow** diamond; start and a closed end are filled.
- If the run is still live, the end mark is the latest print (`open`). A 1-day run is a **single** diamond (start = end).
- `=5` is not a streak — no bound marks (the card pill still shows `=5`).

### MA trend coloring (v1)

Daily close series. **Positive (green):** `close > SMA50 AND close > SMA200`. **Negative (red):** otherwise (close at or below either MA). The path is split into contiguous green/red segments so color flips over time. SMA50 is a thin blue overlay; SMA200 is maroon. If the series is shorter than 200, SMA200 is omitted and color is close vs SMA50 only. If shorter than 50, the default stroke stays (no regime). No arrows or callouts. Legend on the name-drill chart: `Positive ↑ / Negative ↓ Trend Signals`.

`desk_dash.write_combined` always re-embeds `#fd-chart-db` + overlay JS (`chart_marks.ensure_embedded`) **and** `_ensure_options_refresh_ui`, so a Refresh rewrite cannot drop the marks or the Options Refresh button. See [chart_marks.py](../chart_marks.py). Live Desktop name-drill charts are **`paintPxChart`** (SVG `[data-px-svg]` + JSON `[data-px-json]`, not generator `svg.fd-chart`). Overlay JS **wraps `paintPxChart`**: hide the white `#e6edf3` price path, draw green/red segments from `S.px` vs `S.s50`/`S.s200`, enlarge `padR`, and polish 1W/1M/YTD/Trend chips. Do not wholesale-replace live ~4.8MB HTML. Generator `svg.fd-chart` is painted in Python.

## Rebuild path

`desk_dash.write_combined` / `write_dash.write` (Refresh rebuild) always:

1. If `mom_score_hist.json` is missing or thin, auto-backfill from `prices_long.csv` (`trend_window_score`, last ~120 trading days)
2. Resolves today’s score + history (live card `mom_score` wins for today)
3. Attaches `mom_score`, `mom_streak`, `mom_streak_side`, `mom_streak_label`, `mom_streak_start` / `_end` / `_open`, and a pill on the card
4. Embeds a **filled** `#mom-streak-db` JSON + JS from attached cards so live cards with `data-t` / `data-ticker` still get the tag after an HTML write

`mom_score_hist.json` is gitignored (Desktop local).

## Desktop sync

Copy `mom_streak.py` next to live `desk_dash.py`. See [DEPLOY-HOOKS.md](../DEPLOY-HOOKS.md) §6. Do not replace FLAGS/WATCH/MOM ranking logic.

For MA-regime name-drill coloring, after merge CoS runs:

```bat
python factor-desk\sync_live_paintpx.py --deploy-desktop
```

That copies **`chart_marks.py`** + **`sync_live_paintpx.py`** into `C:\Users\MLP\Desktop\factorbook` and patches live `factorbook.html` via `chart_marks.ensure_embedded` / `inject_paintpx`. It refuses HTML &lt; 1MB and keeps Paper / Experimental / Breakout / `#fd-paper-marks`. Do **not** wholesale-replace live `factorbook.html` from the skinny generator and do **not** run `desk_dash.py` / `write_dash.py` on the live file. See [DEPLOY-HOOKS.md](../DEPLOY-HOOKS.md) §8.
