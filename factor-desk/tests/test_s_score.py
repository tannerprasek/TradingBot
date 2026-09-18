"""Experimental residual S-score math, empty-panel, and write_combined survival."""

from __future__ import annotations

import json
import math
import re
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import dapi_enrich as de  # noqa: E402
import desk_dash  # noqa: E402
import s_score as ss  # noqa: E402


def _weekdays(start: date, n: int) -> list[date]:
    days: list[date] = []
    d = start
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def _ou_levels(
    *,
    n: int = 80,
    kappa: float = 0.12,
    m: float = 0.0,
    sigma: float = 0.02,
    x0: float = 0.4,
) -> list[float]:
    """Deterministic AR(1) with a small repeating shock (no RNG)."""
    b = math.exp(-kappa)
    a = m * (1.0 - b)
    x = [float(x0)]
    shocks = (0.4, -0.2, -0.1, 0.0, -0.1)
    for i in range(n - 1):
        x.append(a + b * x[-1] + sigma * shocks[i % len(shocks)])
    return x


def _ou_open(*, n: int = 60, kappa: float = 0.12, last_extra: float = 0.03) -> list[float]:
    """Stationary AR(1) plus a last innovation so |s| clears the open band."""
    b = math.exp(-kappa)
    x = [0.0]
    for i in range(n - 1):
        shock = 0.01 * math.sin(i)
        if i == n - 2:
            shock += last_extra
        x.append(b * x[-1] + shock)
    return x


class MathTests(unittest.TestCase):
    def test_ou_recovers_kappa_and_signed_s(self) -> None:
        levels = _ou_levels(n=40, kappa=0.12, m=0.0, x0=0.4)
        fit = ss.fit_ou(levels)
        self.assertIsNotNone(fit)
        self.assertTrue(fit["ok"])
        self.assertAlmostEqual(fit["kappa"], 0.12, delta=0.03)
        self.assertLess(fit["half_life"], ss.HALF_LIFE_MAX_DAYS)
        opened = ss.fit_ou(_ou_open(last_extra=0.03))
        self.assertTrue(opened["ok"])
        self.assertGreater(opened["s_score"], ss.S_OPEN)
        row = ss.row_from_fit("AAPL US Equity", opened)
        self.assertIsNotNone(row)
        self.assertEqual(row["side_hint"], "fade residual high")
        self.assertEqual(row["band"], "open")
        self.assertEqual(row["entry"], ss.S_OPEN)
        self.assertEqual(row["exit"], ss.S_CLOSE)

    def test_cumsum_window_then_s_score(self) -> None:
        levels = _ou_open(n=60, kappa=0.15, last_extra=-0.03)
        eps = [levels[0]] + [levels[i] - levels[i - 1] for i in range(1, len(levels))]
        scored = ss.score_residuals(eps, already_levels=False, window=60)
        self.assertIsNotNone(scored)
        self.assertTrue(scored["ok"])
        self.assertLess(scored["s_score"], -ss.S_OPEN)
        row = ss.row_from_fit("XYZ US Equity", scored)
        self.assertEqual(row["side_hint"], "buy residual low")

    def test_row_from_fit_uses_literature_bands(self) -> None:
        fit = {
            "ok": True,
            "s_score": 1.82,
            "kappa": 0.08,
            "half_life": 8.7,
            "m": 0.0,
            "sigma_eq": 0.02,
            "x": 0.036,
            "b": 0.92,
            "n_obs": 60,
        }
        row = ss.row_from_fit("AAPL US Equity", fit)
        self.assertEqual(row["band"], "open")
        self.assertEqual(row["side_hint"], "fade residual high")
        fit["s_score"] = -1.4
        self.assertEqual(ss.row_from_fit("AAPL US Equity", fit)["side_hint"], "buy residual low")
        fit["s_score"] = 0.2
        self.assertEqual(ss.row_from_fit("AAPL US Equity", fit)["band"], "close")
        fit["s_score"] = 1.82
        fit["half_life"] = 45.0
        fit["kappa"] = math.log(2.0) / 45.0
        self.assertEqual(ss.row_from_fit("AAPL US Equity", fit)["band"], "reject_slow_kappa")

    def test_slow_kappa_rejected(self) -> None:
        # b ≈ 0.995 → half-life ~139d, still technically mean-reverting.
        levels = _ou_levels(n=80, kappa=0.005, m=0.0, sigma=0.01, x0=0.2)
        fit = ss.fit_ou(levels)
        self.assertIsNotNone(fit)
        if fit.get("ok"):
            row = ss.row_from_fit("SLOW US Equity", fit)
            self.assertEqual(row["band"], "reject_slow_kappa")
            self.assertEqual(row["side_hint"], "reject slow κ")
        else:
            self.assertIn(fit.get("reason"), {"not_mean_reverting", "kappa_nonpositive"})

    def test_random_walk_does_not_score(self) -> None:
        # Unit-root drift: X_{t+1} = X_t + 0.01 → b ≈ 1, not mean-reverting.
        x = [0.01 * i for i in range(51)]
        fit = ss.fit_ou(x)
        row = ss.row_from_fit("RW US Equity", fit)
        self.assertIsNone(row)

    def test_short_series_none(self) -> None:
        self.assertIsNone(ss.score_residuals([0.01] * (ss.MIN_OBS - 1)))
        self.assertIsNone(ss.fit_ou([0.1, 0.2, 0.15]))


class PanelTests(unittest.TestCase):
    def test_empty_panel_ranks_empty_with_reason(self) -> None:
        ranked = ss.rank_panel(ss.empty_panel())
        self.assertEqual(ranked["rows"], [])
        self.assertTrue(ranked["experimental"])
        self.assertIn("residual", str(ranked["empty_reason"]).lower())
        self.assertFalse(ranked["pit"])
        self.assertNotIn("sharpe", json.dumps(ranked).lower())

    def test_residual_last_only_too_thin_for_ou(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "residual_last.csv").write_text(
                "ticker,residual\nAAPL US Equity,0.04\nMSFT US Equity,-0.03\n",
                encoding="utf-8",
            )
            panel = ss.materialize_panel(root, write=True)
            self.assertEqual(panel["panel_depth"], 1)
            self.assertEqual(panel["look_ahead_risk"], "residual_last_only")
            self.assertFalse(panel["pit"])
            ranked = ss.rank_panel(panel)
            self.assertEqual(ranked["rows"], [])
            self.assertTrue(
                "depth" in str(panel.get("empty_reason")).lower()
                or "thin" in str(ranked["empty_reason"] or "").lower()
                or "20" in str(ranked["empty_reason"] or "") + str(panel.get("empty_reason") or "")
            )
            self.assertTrue((root / ss.PANEL_FILENAME).is_file())

    def test_v0_residuals_panel_scores_open_name(self) -> None:
        levels = _ou_open(n=60, kappa=0.14, last_extra=0.03)
        eps = [levels[0]] + [levels[i] - levels[i - 1] for i in range(1, len(levels))]
        days = _weekdays(date(2026, 6, 1), len(eps))
        lines = ["date,ticker,residual"]
        for d, v in zip(days, eps):
            lines.append(f"{d.isoformat()},AAPL US Equity,{v:.8f}")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "v0_residuals.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
            panel = ss.materialize_panel(root, write=True)
            self.assertGreaterEqual(panel["panel_depth"], ss.MIN_OBS)
            ranked = ss.rank_panel(panel)
            self.assertGreaterEqual(len(ranked["rows"]), 1)
            row = ranked["rows"][0]
            self.assertEqual(row["t"], "AAPL")
            self.assertEqual(row["band"], "open")
            self.assertEqual(row["rank"], 1)
            self.assertIn("fade residual high", row["side_hint"])
            self.assertNotIn("sharpe", json.dumps(ranked).lower())

    def test_rank_panel_sorts_by_abs_s(self) -> None:
        high = _ou_open(n=60, kappa=0.14, last_extra=0.05)
        mild = _ou_open(n=60, kappa=0.14, last_extra=0.02)
        panel = {
            "already_levels": True,
            "source": "test",
            "names": {
                "MILD US Equity": {"series": [{"residual": v} for v in mild]},
                "HIGH US Equity": {"series": [{"residual": v} for v in high]},
            },
            "panel_depth": 60,
            "panel_names": 2,
            "pit": False,
        }
        ranked = ss.rank_panel(panel)
        self.assertGreaterEqual(len(ranked["rows"]), 2)
        abs_s = [abs(float(r["s_score"])) for r in ranked["rows"]]
        self.assertEqual(abs_s, sorted(abs_s, reverse=True))
        self.assertEqual([r["rank"] for r in ranked["rows"]], list(range(1, len(ranked["rows"]) + 1)))
        self.assertEqual(ranked["rows"][0]["t"], "HIGH")

    def test_reconstruct_from_returns_and_loadings(self) -> None:
        days = _weekdays(date(2026, 6, 1), 40)
        # Two-name one-factor: residual is mean-reverting leftover after the factor.
        factor = [0.01 * (1 if i % 3 else -1) for i in range(len(days))]
        levels = _ou_levels(n=len(days), kappa=0.18, x0=0.35, sigma=0.01)
        eps_a = [levels[0]] + [levels[i] - levels[i - 1] for i in range(1, len(levels))]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            px_lines = ["date,ticker,close"]
            px0_a, px0_b = 100.0, 50.0
            for i, d in enumerate(days):
                r_a = 0.5 * factor[i] + eps_a[i]
                r_b = 0.5 * factor[i]
                px0_a *= 1.0 + r_a
                px0_b *= 1.0 + r_b
                px_lines.append(f"{d.isoformat()},AAPL US Equity,{px0_a:.6f}")
                px_lines.append(f"{d.isoformat()},MSFT US Equity,{px0_b:.6f}")
                px_lines.append(f"{d.isoformat()},GOOG US Equity,{100.0 * (1.3 + 0.01 * i):.6f}")
            (root / "prices_long.csv").write_text("\n".join(px_lines) + "\n", encoding="utf-8")
            (root / "sparse_pca_loadings.csv").write_text(
                "ticker,pc1\nAAPL US Equity,0.5\nMSFT US Equity,0.5\nGOOG US Equity,0.5\n",
                encoding="utf-8",
            )
            panel = ss.materialize_panel(root, write=True)
            self.assertIn("reconstructed", panel["source"])
            self.assertEqual(panel["look_ahead_risk"], "full_sample_loadings")
            self.assertFalse(panel["pit"])
            self.assertGreaterEqual(panel["panel_depth"], ss.MIN_OBS)
            ranked = ss.rank_panel(panel)
            self.assertIsInstance(ranked["rows"], list)


class EmbedTests(unittest.TestCase):
    def test_ensure_embedded_injects_experimental_not_mom_pills(self) -> None:
        html = """<!DOCTYPE html><html><head></head><body>
<nav>
  <button id="refresh">Refresh</button>
  <button>Momentum Up</button>
  <button>Momentum Down</button>
  <button data-view="options">Options</button>
</nav>
<article class="card" data-t="AAPL">AAPL MOM</article>
</body></html>"""
        levels = _ou_open(n=60, kappa=0.14, last_extra=0.03)
        fit = ss.fit_ou(levels)
        row = ss.row_from_fit("AAPL US Equity", fit)
        ranked = {
            "kind": "experimental-sscore",
            "experimental": True,
            "asof": "2026-09-17",
            "source": "v0_residuals.csv",
            "panel_depth": 70,
            "panel_names": 1,
            "pit": False,
            "pit_note": "PIT is not claimed",
            "look_ahead_risk": "unknown_sparsepca_fit",
            "defaults": ss.experimental_defaults(),
            "rows": [row],
            "empty_reason": None,
        }
        out = ss.ensure_embedded(html, ranked)
        self.assertRegex(out, r'<button\b[^>]*data-view=["\']experimental["\']')
        self.assertIn(">Experimental</button>", out)
        self.assertIn('id="view-experimental"', out)
        self.assertIn('id="sscore-grid"', out)
        self.assertIn('id="fd-sscore-db"', out)
        self.assertIn("fade residual high", out)
        self.assertIn("pointer only", out)
        self.assertIn("PIT not claimed", out)
        self.assertIn("not a proven edge", out.lower().replace("—", "-"))
        self.assertNotIn("sharpe", out.lower())
        self.assertIn('data-key="s_score"', out)
        self.assertIn("fd-ss-card", out)
        self.assertIn("κ / HL", out)
        self.assertIn("open / close", out)
        self.assertRegex(out, r'id="view-experimental"[^>]*\bhidden\b')
        css = ss.strip_css()
        self.assertIn("display: none !important", css)
        self.assertIn('body[data-fd-ss="1"]', css)
        self.assertIn(":not(.hide):not([hidden])", css)
        self.assertNotIn("SHOW_S_SCORE_PILL_ON_MOM", ss.__dict__)
        self.assertFalse(hasattr(ss, "attach_pill_to_card"))
        self.assertNotIn("cardHTML", ss.strip_js())
        # Live MOM card is untouched — S-score lives in Experimental only.
        mom = re.search(r'<article class="card" data-t="AAPL">AAPL MOM</article>', out)
        self.assertIsNotNone(mom)
        self.assertNotIn("s_score", mom.group(0))
        self.assertNotIn("fd-ss-card", mom.group(0))
        again = ss.ensure_embedded(out, None)
        self.assertEqual(
            len(re.findall(r'<button\b[^>]*data-view=["\']experimental["\']', again, re.I)),
            1,
        )
        db = json.loads(
            re.search(
                r'<script\b[^>]*id=["\']fd-sscore-db["\'][^>]*>(.*?)</script>',
                again,
                re.I | re.S,
            ).group(1)
        )
        self.assertEqual(db["rows"][0]["t"], "AAPL")

    def test_empty_panel_still_injects_tab(self) -> None:
        html = "<html><body><nav><button>Options</button></nav></body></html>"
        out = ss.ensure_embedded(html, ss.rank_panel(ss.empty_panel()))
        self.assertIn('id="view-experimental"', out)
        self.assertIn("No residual panel", out)
        self.assertIn('data-view="experimental"', out)

    def test_setview_allowlist_and_hideall(self) -> None:
        html = """<!DOCTYPE html><html><body>
<nav id="topnav">
  <button class="btn" data-view="home">Home</button>
  <button class="btn" data-view="options">Options</button>
</nav>
<div id="home">FLAGS</div>
<script>
function hideAllPanes() {
  ["home","view-mom-up","view-mom-down","view-outliers","view-options","view-sectors","search-pane"].forEach(function(id){
    var el = document.getElementById(id);
    if (el) el.classList.add("hide");
  });
}
function paintView(v) {
  if (!/^(home|mom-up|mom-down|outliers|options|sectors)$/.test(v)) v = "home";
}
function setView(v) {
  if (!/^(home|mom-up|mom-down|outliers|options|sectors)$/.test(v)) v = "home";
  hideAllPanes();
  paintView(v);
  paint();
}
</script>
</body></html>"""
        out = ss.ensure_embedded(html, ss.rank_panel(ss.empty_panel()))
        self.assertRegex(out, r"home\|mom-up\|mom-down\|outliers\|options\|sectors\|experimental")
        self.assertIn("view-experimental", out)
        self.assertIn("window.__FD_SS_SHOW__", out)
        self.assertIn("/*fd-ss-setview*/", out)
        self.assertIn("fd-ss-on", out)
        self.assertIn('removeAttribute("data-fd-ss")', out)

    def test_write_combined_patches_live_html(self) -> None:
        body = """<!DOCTYPE html>
<html><head><title>Factor Desk</title>
<style>.gchip{} .gics-hid{display:none!important}</style></head>
<body>
<nav>
  <button id="refresh">Refresh</button>
  <button>Momentum Up</button>
  <button>Momentum Down</button>
  <button data-view="options">Options</button>
  <button>Outliers</button>
</nav>
<div id="gics-filter-strip" class="filter-strip gics-chips"></div>
<article class="card" data-t="AAPL" data-ticker="AAPL US Equity">AAPL</article>
</body></html>"""
        pad = 2_700_000 - len(body.encode("utf-8"))
        live = body + ("<!--" + ("P" * max(pad, 1)) + "-->")
        levels = _ou_open(n=60, kappa=0.14, last_extra=0.03)
        eps = [levels[0]] + [levels[i] - levels[i - 1] for i in range(1, len(levels))]
        days = _weekdays(date(2026, 6, 1), len(eps))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dest = root / "factorbook.html"
            dest.write_text(live, encoding="utf-8")
            lines = ["date,ticker,residual"] + [
                f"{d.isoformat()},AAPL US Equity,{v:.8f}" for d, v in zip(days, eps)
            ]
            (root / "v0_residuals.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
            rec = de.build_name_record("AAPL US Equity", {"GICS_SECTOR_NAME": "Information Technology"})
            rec["mom_score"] = 9
            book = {"asof": "2026-09-17", "names": {"AAPL US Equity": rec}, "meta": {}}
            out = desk_dash.write_combined(dest, root=root, book=book)
            text = out.read_text(encoding="utf-8")
            self.assertGreater(out.stat().st_size, 2_000_000)
            self.assertIn(">Experimental</button>", text)
            self.assertIn('id="view-experimental"', text)
            self.assertIn('id="fd-sscore-db"', text)
            self.assertIn("AAPL", text)
            self.assertIn("Momentum Up", text)
            self.assertIn("Momentum Down", text)
            self.assertNotIn("sharpe", text.lower())
            self.assertTrue((root / ss.PANEL_FILENAME).is_file())
            # S-score lives on Experimental cards only, not the live MOM article.
            mom_article = re.search(
                r'<article class="card" data-t="AAPL"[^>]*>AAPL</article>',
                text,
            )
            self.assertIsNotNone(mom_article)
            self.assertNotIn("s_score", mom_article.group(0))
            self.assertNotIn("fd-ss-card", mom_article.group(0))
            self.assertRegex(text, r'id="view-experimental"[^>]*\bhidden\b')
        self.assertNotIn("SHOW_S_SCORE_PILL_ON_MOM", ss.__dict__)
        # Paper Buy/Sell hydrator must skip Experimental cards.
        import paper_trade as pt
        js = pt.strip_js()
        self.assertIn("fd-ss-card", js)
        self.assertIn("view-experimental", js)


if __name__ == "__main__":
    unittest.main()
