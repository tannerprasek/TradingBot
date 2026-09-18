# Experimental residual S-score (Avellaneda–Lee)

**Experimental only.** S-score + κ (mean-reversion speed) + OU-style close hints on the **existing SparsePCA / residual pipeline**. This is **not** a new PCA engine, **not** a FLAGS / WATCH / MOM rebrand, and **not** a trade.

Pointers only — no auto-enter, no paper auto-open. Recopy [`s_score.py`](../s_score.py) to Desktop `C:\Users\MLP\Desktop\factorbook\`. Do **not** wholesale replace live `desk_dash.py`. See [DEPLOY-HOOKS.md](../DEPLOY-HOOKS.md) §11.

Avellaneda–Lee (2008) paper Sharpes are **historical claims only**. They are never desk KPIs.

## What it reads (honest)

Cloud `factor-desk/` does not ship residual dumps (gitignored). On Desktop, Refresh / `write_combined` **discovers** whatever is already in the factorbook folder:

| Kind | Candidate files |
| --- | --- |
| Residual **panel** (preferred) | `v0_residuals.csv`, `residuals.csv`, `residual_panel.json` / `.csv`, `sparse_pca_residuals.csv`, `clean/…` |
| `residual_last` snapshot | `residual_last.csv` / `.json`, `v0_residual_last.csv` |
| SparsePCA loadings | `sparse_pca_loadings.csv`, `pca_loadings.csv`, `loadings.csv`, `v0_loadings.csv`, JSON twins |
| Membership | `sparse_pca_membership.csv`, `membership.csv`, `v0_universe.csv` |
| Returns (to rebuild ε) | `returns_long.csv` / `returns.csv`, else simple returns from `prices_long.csv` |

`residual_20d` (beta vs SPY in the enrich pack) is a **different engine** and is never used as a SparsePCA residual.

**Confirm panel depth on Desktop.** If only `residual_last` exists, this pack **appends one day** into gitignored `residual_panel.json` so history can accumulate — κ / S-score still need ≥20 prints (60-day window preferred). Until then the Experimental tab shows an empty reason, not fake scores.

If a panel is missing but returns + loadings exist, Refresh **reconstructs** ε_t = r_t − L F_t with F_t = (L′L)⁻¹ L′ r_t. That is still Experimental-only.

## PIT / look-ahead

**PIT is not claimed.** Full-sample SparsePCA membership/loadings leak future covariance into every residual. The Experimental banner says `PIT not claimed` plus a look-ahead tag:

- `full_sample_loadings` — loadings file has no window/asof
- `rolling_window_unverified` — file exposes a window, still not certified PIT
- `residual_last_only` — no time series yet
- `unknown_sparsepca_fit` — Desktop `v0_residuals.csv` without fit metadata

Prefer a rolling SparsePCA window on Desktop. Do not silently stamp PIT.

## Formula (experimental defaults)

Windowed cumulative residual X = cumsum(ε) over the last **60** trading days (need ≥ **20**). AR(1):

```
X_{n+1} = a + b X_n + ζ
κ = −log(b)          (daily)
m = a / (1 − b)
σ_eq = std(ζ) / √(1 − b²)
s = (X_last − m) / σ_eq
half-life = ln(2) / κ
```

Reject unless `0.01 < b < 0.99` (mean-reverting). **κ gate:** half-life must be ≤ **30** trading days (κ ≥ ln(2)/30 ≈ 0.0231). Slow names are listed as `reject slow κ`, not as opens.

Literature thresholds, labeled **experimental defaults, not a proven edge**:

| | default |
| --- | --- |
| open \|s\| | **1.25** |
| close \|s\| | **0.75** (tight 0.50 documented, not the primary close) |
| side | s ≥ +1.25 → **fade residual high**; s ≤ −1.25 → **buy residual low** |
| close hint | \|s\| ≤ 0.75 → **OU close toward mean** |

Tune constants at the top of `s_score.py`.

## UI

Top-nav **Experimental** (after Options — home FLAGS→WATCH→MOM is unchanged). Own dense cards: ticker, `s` pill, κ / half-life, side hint, entry/exit bands. **Not** live `cardHTML` (so production paper Buy/Sell chrome is not reused here). Click ticker → `selectTicker` when that function exists.

Optional `s_score` pill on live MOM cards is **`SHOW_S_SCORE_PILL_ON_MOM = False`**. Do not force it on.

`desk_dash.write_combined` always re-embeds `#fd-sscore-db` + nav + JS (`s_score.ensure_embedded`). Live ~2.7MB `factorbook.html` is **patched**, never replaced.

## Desktop sync

Copy `s_score.py` next to live `desk_dash.py`. Paste the `write_combined` tail in [DEPLOY-HOOKS.md](../DEPLOY-HOOKS.md) §11. Do not replace FLAGS/WATCH/MOM ranking.
