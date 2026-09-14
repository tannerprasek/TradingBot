# Options Pulse

Score v2, 15C+15P, UI top 20×2, on-demand refresh. No name cap (`DEFAULT_MAX_NAMES = 0`).

```
0.5 * mean(top-3 log1p(vol/avg20))
+ 0.3 * log(opt_vol / stock_ADV)
+ 0.2 * log(vol / OI)
```

Skew / term (additive, does not change v2):

- Contract fields used when present: `DELTA`, `IVOL_MID` (or `OPT_IMPLIED_VOLATILITY`), `VOLUME`, `OPEN_INT`
- `skew_25d_proxy` = put IV − call IV near \|Δ\|≈0.25; else ATM call vs put IV
- Null Greeks / IV stay null

Output: `options_abnormal.json` (gitignored). Enrich reads chains from that book when Refresh passes them into `enrich_book(..., options_by_name=...)`.
