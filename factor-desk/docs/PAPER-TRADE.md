# Paper trading on dense MOM cards

Generic **Buy** / **Sell** on every dense MOM-style card that goes through live `cardHTML` (home FLAGS/WATCH, Momentum Up/Down, Breakout/Breakdown, search). Paper only — no brokerage, no size UI. One unit notional.

Home also shows a **tiny paper-book strip** (`#fd-paper-home`) next to BOOK / baseline — not a new tab. Recopy [`paper_trade.py`](../paper_trade.py) to Desktop `C:\Users\MLP\Desktop\factorbook\`. Do **not** wholesale replace live `desk_dash.py`. See [DEPLOY-HOOKS.md](../DEPLOY-HOOKS.md) §10.

## Click rules

| State | Buy | Sell |
| --- | --- | --- |
| Flat | open **long** at current mark | open **short** at current mark |
| Long | **no-op + toast** (`Already long — sell to close`) | **close** long (does not flip) |
| Short | **close** short (does not flip) | **no-op + toast** (`Already short — buy to close`) |

Same-side click never adds size.

## P&L

Signed percent of the 1-unit entry:

- Long: `(mark - entry) / entry` — price up is **+**
- Short: `(entry - mark) / entry` — price down is **+**

Closed history on the card uses the same sign. Dates are the close stamp (`YYYY-MM-DD`).

## Mark price

First finite `> 0` among: `paper_mark`, `px_last` / `PX_LAST` / `LAST_PRICE`, `price` / `px` / `last` / `close`, last Refresh print, last point of `px_series`, then `#fd-paper-marks`, then `window.MOM.cards`. If none: both buttons **disabled**, `title` = `No mark price — need card price / PX_LAST / last Refresh print`.

## Persistence

`localStorage` key `fd-paper-book` (survives Refresh / reload). Optional JSON Schema: `paper_trade.sidecar_schema()` for a later sidecar `paper_book.json` — not written on Refresh. The Home strip reads this same key; it does not add another store.

## UI

- Generic Buy (teal) / Sell (rose) on the card; clicks do not drill `selectTicker`.
- Open line: `LONG @ 12.50  +4.00%` (live % when a mark exists).
- `<details>` **Previous trades** — collapsed by default; each row is date · side · signed %.
- Home strip (`#fd-paper-home`, after `#fd-book-delta`):
  - **paper** — open longs/shorts (ticker · side · entry · live %). Click ticker → `selectTicker` if present. Empty: `no open paper`. ≤12 opens shown; extra behind `more N`.
  - **week** — scorecard of closes **since Monday 00:00 America/Edmonton**: closed count, hit rate (% with positive signed return), avg win %, avg loss %. Empty: `no closed yet this week`.

Mom Up/Down chrome is otherwise unchanged. `desk_dash.write_combined` always re-embeds `#fd-paper-marks` + `#fd-paper-home` + wrap JS (`paper_trade.ensure_embedded`) so a Refresh rewrite cannot drop the strip.
