# Paper trading (1-unit Buy / Sell)

Dense MOM-style cards (home FLAGS/WATCH/MOM, Momentum Up/Down, Breakout/Breakdown, search drill — anything that uses live `cardHTML`) get generic **Buy** and **Sell**. Paper only. No brokerage. No size UI: each click is **1 unit** notional.

Mom Up/Down chrome stays. This is additive.

## Position rules

| State | Click | Result |
| --- | --- | --- |
| Flat | **Buy** | open **LONG** at current mark |
| Flat | **Sell** | open **SHORT** at current mark |
| Long | **Sell** | **close** the long (do not flip to short in that click) |
| Short | **Buy** | **close** the short (do not flip to long in that click) |
| Long | **Buy** | **no-op + toast** `already LONG — Sell to close` |
| Short | **Sell** | **no-op + toast** `already SHORT — Buy to close` |

## P&L %

```
long  = (mark - entry) / entry     # price up → +
short = (entry - mark) / entry     # price down → +
```

Open state on the card: `LONG` / `SHORT` + entry + live P&L % when a mark is available. Closed trades sit in a collapsed **previous closed trades** list (date opened → date closed + signed return %). Shorts that profit from a drop show **+**.

## Mark price

Best available, never invented: card `px_last` / `PX_LAST` / `last` / `price` / last Refresh price field already on the card or `window.MOM.cards`, else last `px_series` print. Missing mark → both buttons **disabled** with title `no mark price (need card px_last / PX_LAST / last Refresh price)`.

## Persist across Refresh

| Store | Role |
| --- | --- |
| **`localStorage` key `fd-paper-trade-v1`** | UI source of truth (survives Refresh rewrite of `factorbook.html`) |
| **`paper_trades.json`** | optional sidecar schema (same JSON). Seed `#fd-paper-db` on write. Gitignored. |

Schema:

```json
{
  "version": 1,
  "paper": true,
  "unit": 1,
  "names": {
    "AAPL": {
      "t": "AAPL",
      "ticker": "AAPL US Equity",
      "open": { "side": "long", "entry": 150.25, "qty": 1, "opened_at": "2026-09-17T21:00:00Z" },
      "closed": [
        {
          "side": "short",
          "entry": 160.0,
          "exit": 155.0,
          "qty": 1,
          "pnl_pct": 0.03125,
          "opened_at": "2026-09-10T14:00:00Z",
          "closed_at": "2026-09-12T15:30:00Z"
        }
      ]
    }
  }
}
```

Sidecar POST of this blob can come later (`POST /paper-trade`). UI does not require it.

## Live HTML

`paper_trade.ensure_embedded` wraps live `cardHTML` and paints existing `article.card` nodes. Recopy `paper_trade.py`. Paste the `write_combined` tail. **Do not wholesale replace** live `desk_dash.py`. See [DEPLOY-HOOKS.md](../DEPLOY-HOOKS.md) §10.
