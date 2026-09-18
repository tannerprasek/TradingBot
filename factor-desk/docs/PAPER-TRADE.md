# Paper trading on dense MOM cards

Generic **Buy** / **Sell** on every dense MOM-style card that goes through live `cardHTML` (home FLAGS/WATCH, Momentum Up/Down, Breakout/Breakdown, search). Paper only — no brokerage, no size UI. One unit notional.

A top-nav **Paper** tab (`#fd-nav-paper`, `data-view="paper"`) hosts the book — not a home chrome strip. Recopy [`paper_trade.py`](../paper_trade.py) to Desktop `C:\Users\MLP\Desktop\factorbook\`. Do **not** wholesale replace live `desk_dash.py`. See [DEPLOY-HOOKS.md](../DEPLOY-HOOKS.md) §10.

## Click rules

| State | Buy | Sell |
| --- | --- | --- |
| Flat | open **long** at current mark | open **short** at current mark |
| Long | **no-op + toast** (`Already long — sell to close`) | **close** long (does not flip) |
| Short | **close** short (does not flip) | **no-op + toast** (`Already short — buy to close`) |

Same-side click never adds size. Paper-tab **Close** is the opposite click (long → sell, short → buy) against the same `fd-paper-book` rules.

## P&L

Signed percent of the 1-unit entry:

- Long: `(mark - entry) / entry` — price up is **+**
- Short: `(entry - mark) / entry` — price down is **+**

Closed history on the card uses the same sign. Dates are the close stamp (`YYYY-MM-DD`).

## Mark price

First finite `> 0` among: `paper_mark`, `px_last` / `PX_LAST` / `LAST_PRICE`, `price` / `px` / `last` / `close`, last Refresh print, last point of `px_series`, then `#fd-paper-marks`, then `window.MOM.cards`. If none: both buttons **disabled**, `title` = `No mark price — need card price / PX_LAST / last Refresh print`. Tab Buy/Sell / Close reuse this resolver.

## Persistence

`localStorage` key `fd-paper-book` (survives Refresh / reload). Optional JSON Schema: `paper_trade.sidecar_schema()` for a later sidecar `paper_book.json` — not written on Refresh. The Paper tab reads this same key; it does not add another store.

## UI

- Generic Buy (teal) / Sell (rose) on the card; clicks do not drill `selectTicker`.
- Open line: `LONG @ 12.50  +4.00%` (live % when a mark exists).
- `<details>` **Previous trades** — collapsed by default; each row is date · side · signed %.
- Paper tab (`#view-paper`, after Options / near Experimental — not replacing Mom Up/Down):
  - **Open** — ticker field + Buy / Sell at the current mark (same mark path as card buttons).
  - **open** — every open long/short with live P&L % and inline **Close**. Click ticker → `selectTicker` if present. Empty: `no open paper`.
  - **week** — scorecard of closes **since Monday 00:00 America/Edmonton**: closed count, hit rate (% with positive signed return), avg win %, avg loss %. Empty: `no closed yet this week`.
  - Collapsible **Closed trades** for the full local book.

Leftover `#fd-paper-home` in top chrome is stripped and CSS-hidden. Mom Up/Down chrome is otherwise unchanged. `desk_dash.write_combined` always re-embeds `#fd-paper-marks` + `#view-paper` + wrap JS (`paper_trade.ensure_embedded`) so a Refresh rewrite cannot drop the tab.

Paper nav capture calls `stopImmediatePropagation` **before** `showPaper(true)` so a later Experimental listener cannot unhide `#home`. `setView("paper")` still hits `__FD_PAPER_SHOW__`.
