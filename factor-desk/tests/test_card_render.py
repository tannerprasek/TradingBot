"""Portable MOM card renderer — why chips, normalize, embed wrap."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import card_render as cr  # noqa: E402
import desk_dash  # noqa: E402
import mom_streak as ms  # noqa: E402


class WhyPillsTests(unittest.TestCase):
    def test_splits_band_dump_into_short_chips(self) -> None:
        row = {
            "score": 10,
            "delta": 3,
            "lookback": 7,
            "streak": 3,
            "side": "above",
            "why": "band 10 · +3 / 7d · above 3d",
        }
        pills = cr.why_pills(row)
        labels = [p["label"] for p in pills]
        self.assertEqual(labels, ["band 10", "+3/7d"])
        self.assertFalse(any(" · " in lab for lab in labels))
        self.assertEqual(pills[0]["key"], "fd-bb-band")
        self.assertEqual(pills[1]["cls"], "fd-bb-delta-up")

    def test_negative_delta_chip(self) -> None:
        pills = cr.why_pills({"score": 4, "delta": -2.5, "lookback": 7, "why": "band 4 · -2.5 / 7d · below 4d"})
        labels = [p["label"] for p in pills]
        self.assertEqual(labels, ["band 4", "-2.5/7d"])
        self.assertEqual(pills[1]["cls"], "fd-bb-delta-down")


class NormalizeTests(unittest.TestCase):
    def test_keeps_mom_fields_and_drops_band_why(self) -> None:
        mom = {
            "t": "SPCX",
            "d": "SPCX",
            "score": 10,
            "tags": ["MA FAN", "CLOSE HI", "52W HI"],
            "r20": 1.2,
            "rs63": 0.4,
            "atrs": 2.1,
            "enrich_pills": [{"key": "si", "label": "SI", "cls": "si-crowded"}],
            "why": "band 10 · +3 / 7d · above 3d",
        }
        row = {
            "t": "SPCX",
            "ticker": "SPCX US Equity",
            "score": 10,
            "delta": 3,
            "lookback": 7,
            "label": "↑3d>5",
            "side": "above",
            "why": mom["why"],
        }
        out = cr.normalize_card(mom, row)
        self.assertNotIn("why", out)
        self.assertEqual(out["tags"], ["MA FAN", "CLOSE HI", "52W HI"])
        self.assertEqual(out["r20"], 1.2)
        keys = [p["key"] for p in out["enrich_pills"]]
        labels = [p["label"] for p in out["enrich_pills"]]
        self.assertIn("si", keys)
        self.assertIn("fd-bb-band", keys)
        self.assertIn("fd-bb-delta", keys)
        self.assertIn("mom-streak", keys)
        self.assertNotIn("fd-bb", keys)
        self.assertIn("band 10", labels)
        self.assertIn("+3/7d", labels)
        self.assertIn("↑3d>5", labels)
        self.assertFalse(any(" · " in lab for lab in labels))
        self.assertIsNot(out, mom)
        self.assertEqual(mom["why"], row["why"])

    def test_merge_drops_dump_pill(self) -> None:
        card = {
            "enrich_pills": [
                {"key": "fd-bb", "label": "band 10 · +3 / 7d · above 3d", "cls": "fd-bb-why"},
                {"key": "si", "label": "SI", "cls": "si-crowded"},
            ]
        }
        merged = cr.merge_pills(card, cr.why_pills({"score": 10, "delta": 3, "lookback": 7}))
        keys = [p["key"] for p in merged]
        self.assertNotIn("fd-bb", keys)
        self.assertIn("si", keys)
        self.assertIn("fd-bb-band", keys)


class TagAndStatTests(unittest.TestCase):
    def test_catalog_key_matches_screenshot_labels(self) -> None:
        self.assertEqual(cr.catalog_key("ma fan"), "MA FAN")
        self.assertEqual(cr.catalog_key("HM HL"), "HM/HL")
        self.assertEqual(cr.catalog_key("V EMA"), "V.EMA")
        self.assertEqual(cr.catalog_key("TREND^"), "TREND↑")
        self.assertEqual(cr.catalog_key("not a tag"), "")

    def test_active_tags_skips_off_entries(self) -> None:
        card = {
            "tags": [
                {"label": "MA FAN", "on": 1},
                {"label": "GAP", "off": True},
                "BREAKOUT",
            ],
            "tg": {"CLOSE HI": True, "SQUEEZE": 0},
        }
        self.assertEqual(cr.active_tags(card), ["MA FAN", "BREAKOUT", "CLOSE HI"])

    def test_portable_card_keeps_stats_drops_series(self) -> None:
        mom = {
            "t": "SPCX",
            "r20": 1.2,
            "rs63": 0.4,
            "atr_pct": 2.1,
            "tags": ["MA FAN", "BREAKOUT"],
            "px_series": [1, 2, 3, 4],
            "mom_score_series": [{"date": "2026-09-01", "score": 8}],
        }
        row = {"t": "SPCX", "score": 9, "delta": 2, "lookback": 7, "why": "band 9 · +2 / 7d"}
        out = cr.portable_card(mom, row)
        self.assertEqual(out["r20"], 1.2)
        self.assertEqual(out["rs63"], 0.4)
        self.assertEqual(out["atr_pct"], 2.1)
        self.assertNotIn("px_series", out)
        self.assertNotIn("mom_score_series", out)
        self.assertNotIn("why", out)
        self.assertEqual(out["tags"], ["MA FAN", "BREAKOUT"])
        keys = [p["key"] for p in out["enrich_pills"]]
        self.assertIn("fd-bb-band", keys)
        self.assertIn("fd-bb-delta", keys)

    def test_alias_stats_from_ret_20d(self) -> None:
        card: dict = {"ret_20d": 0.8, "RS_63": 1.1, "ATR": 3.4}
        cr.alias_stats(card)
        self.assertEqual(card["r20"], 0.8)
        self.assertEqual(card["rs63"], 1.1)
        self.assertEqual(card["atr_pct"], 3.4)


class EmbedTests(unittest.TestCase):
    def test_ensure_embedded_wraps_cardhtml_and_exposes_render(self) -> None:
        html = """<!DOCTYPE html><html><head></head><body>
<script>function cardHTML(c){return '<article class="card"><div class="why">'+(c.why||'')+'</div><div class="tags"><span class="tg on">MA FAN</span><span class="tg off">GAP</span></div></article>';}</script>
</body></html>"""
        out = cr.ensure_embedded(html)
        self.assertIn('id="fd-card-css"', out)
        self.assertIn('id="fd-card-js"', out)
        self.assertIn("window.__FD_RENDER_CARD__", out)
        self.assertIn("window.__FD_RENDER_ROW__", out)
        self.assertIn("window.__FD_NORMALIZE_CARD__", out)
        self.assertIn("polishNode", out)
        self.assertIn("fillStats", out)
        self.assertIn("rewriteAtr", out)
        self.assertIn("HM/HL", out)
        self.assertIn("V.EMA", out)
        self.assertIn("ATR%", out)
        self.assertIn("window.__FD_POLISH_NODE__", out)
        self.assertIn("fmtAtr", out)
        self.assertIn("fd-bb-band", out)
        self.assertIn("labels[t]", out)
        self.assertIn("article.card .badge", out)
        self.assertIn("Day ", out)
        self.assertIn("mom_score_d10_label", out)
        self.assertIn("paintScoreD10", out)
        self.assertIn("mom-score-d10", out)
        self.assertIn(cr.JS_VER, out)
        again = cr.ensure_embedded(out)
        self.assertEqual(len(re.findall(r'id="fd-card-js"', again)), 1)
        self.assertEqual(len(re.findall(r'id="fd-card-css"', again)), 1)

    def test_write_combined_injects_renderer_before_breakout(self) -> None:
        body = """<!DOCTYPE html>
<html><head><title>Factor Desk</title></head>
<body>
<nav>
  <button id="refresh">Refresh</button>
  <button>Momentum Up</button>
  <button>Momentum Down</button>
  <button>Outliers</button>
  <button>Options</button>
</nav>
<script>function cardHTML(c){return '<article class="card" data-t="'+(c.t||'')+'">'+ (c.t||'') +'</article>';}</script>
</body></html>"""
        pad = 2_700_000 - len(body.encode("utf-8"))
        live = body + ("<!--" + ("P" * max(pad, 1)) + "-->")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dest = root / "factorbook.html"
            dest.write_text(live, encoding="utf-8")
            out = desk_dash.write_combined(dest, root=root, book={"asof": "2026-09-17", "names": {}, "meta": {}})
            text = out.read_text(encoding="utf-8")
            self.assertIn('id="fd-card-js"', text)
            self.assertIn("__FD_RENDER_ROW__", text)
            card_at = text.find('id="fd-card-js"')
            bb_at = text.find('id="fd-breakout-js"')
            paper_at = text.find('id="fd-paper-js"')
            self.assertGreater(card_at, 0)
            self.assertGreater(bb_at, card_at)
            if paper_at > 0:
                self.assertGreater(paper_at, card_at)
            ast_ok = compile(Path(desk_dash.__file__).read_text(encoding="utf-8"), desk_dash.__file__, "exec")
            self.assertIsNotNone(ast_ok)

    def test_js_keeps_mom_digest_and_rewrites_atr(self) -> None:
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        jsdom_root = Path("/tmp/fd-jsdom")
        jsdom_mod = jsdom_root / "node_modules" / "jsdom"
        if not jsdom_mod.is_dir():
            jsdom_root.mkdir(parents=True, exist_ok=True)
            npm = shutil.which("npm")
            if not npm:
                self.skipTest("npm not installed")
            subprocess.run(
                [npm, "install", "--prefix", str(jsdom_root), "jsdom@24"],
                check=True,
                capture_output=True,
                text=True,
                timeout=60,
            )
        tags = [
            "MA FAN",
            "CLOSE HI",
            "52W HI",
            "HM/HL",
            "V.EMA",
            "ABOVE 50",
            "ABOVE 200",
            "MOM+",
            "TREND↑",
            "SQUEEZE",
            "RS+",
            "BREAKOUT",
        ]
        cells = "".join(f"<div>{t}</div>" for t in tags)
        live = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body>
<script>
window.MOM = {{ cards: [{{
  t:"SPCX", d:"SPCX", ticker:"SPCX US Equity", score:9,
  r20:1.2, rs63:0.4, atr_pct:2.1,
  metrics:{{r20_pct:0.1277, rs_63:0.4, atr_pct:2.1}},
  tags:["MA FAN","BREAKOUT"]
}}] }};
function cardHTML(c) {{
  var t = (c && (c.t || c.d)) || "";
  var m = (c && c.metrics) || {{}};
  function fmtPct(x) {{
    if (x == null || x === "") return "—";
    var n = Number(x);
    if (!isFinite(n)) return "—";
    return (n * 100).toFixed(1) + "%";
  }}
  function fmtN(x) {{
    if (x == null || x === "") return "—";
    return String(x);
  }}
  return '<article class="card" data-t="'+t+'">' +
    '<header><h2>'+t+'</h2><span class="sc">'+(c && c.score != null ? c.score : '')+'</span>' +
    '<span class="status">Weak / fading</span></header>' +
    '<div class="digest" style="display:grid;grid-template-columns:repeat(4,1fr)">{cells}</div>' +
    '<div class="stats"><span>R20 '+fmtPct(m.r20_pct)+'</span><span>RS63 '+fmtN(m.rs_63)+'</span><span>ATR% '+fmtPct(m.atr_pct)+'</span></div>' +
    '<p class="blurb">Trending down. Score 9/12.</p>' +
    '</article>';
}}
</script>
</body></html>"""
        html = cr.ensure_embedded(live)
        with tempfile.TemporaryDirectory() as tmp:
            page = Path(tmp) / "page.html"
            runner = Path(tmp) / "run.js"
            page.write_text(html, encoding="utf-8")
            runner.write_text(
                f"""
const {{ JSDOM }} = require({json.dumps(str(jsdom_mod))});
const fs = require("fs");
const html = fs.readFileSync({json.dumps(str(page))}, "utf8");
const dom = new JSDOM(html, {{ runScripts: "dangerously", url: "http://127.0.0.1/factorbook.html" }});
const window = dom.window;
const row = {{
  t: "SPCX", ticker: "SPCX US Equity", score: 9
}};
const node = window.__FD_RENDER_ROW__(row);
if (!node) {{ console.log(JSON.stringify({{error:"no node"}})); process.exit(2); }}
const report = {{
  text: (node.innerText || node.textContent || "").replace(/\\s+/g, " ").trim(),
  stripped: node.getAttribute("data-fd-ghost-stripped"),
  html: node.outerHTML
}};
console.log(JSON.stringify(report));
""",
                encoding="utf-8",
            )
            proc = subprocess.run(
                [node, str(runner)],
                check=False,
                capture_output=True,
                text=True,
                timeout=20,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
            report = json.loads(proc.stdout.strip().splitlines()[-1])
            text = report["text"]
            self.assertNotEqual(report.get("stripped"), "1", msg=report)
            self.assertIn("SPCX", text)
            self.assertIn("Weak / fading", text)
            self.assertIn("MA FAN", text)
            self.assertIn("CLOSE HI", text)
            self.assertIn("HM/HL", text)
            self.assertIn("V.EMA", text)
            self.assertIn("SQUEEZE", text)
            self.assertIn("Trending down", text)
            self.assertIn("12.8%", text)
            self.assertIn("ATR% 2.1%", text)
            self.assertNotIn("210%", text)
            self.assertNotIn("230%", text)
            self.assertIn("fmtAtr", cr.strip_js())

    def test_cardhtml_paints_d10_beside_score(self) -> None:
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        jsdom_root = Path("/tmp/fd-jsdom")
        jsdom_mod = jsdom_root / "node_modules" / "jsdom"
        if not jsdom_mod.is_dir():
            jsdom_root.mkdir(parents=True, exist_ok=True)
            npm = shutil.which("npm")
            if not npm:
                self.skipTest("npm not installed")
            subprocess.run(
                [npm, "install", "--prefix", str(jsdom_root), "jsdom@24"],
                check=True,
                capture_output=True,
                text=True,
                timeout=60,
            )
        live = """<!DOCTYPE html><html><head></head><body>
<article class="card" data-t="AAA US Equity"><header><h2>AAA</h2><span class="score">9<span class="score-d10">+3</span><span class="mom-score-d10-near up">+3</span></span><div class="pills"></div></header></article>
<script type="application/json" id="mom-streak-db">{"AAA US Equity":{"label":"↑4d>5","cls":"mom-streak-up","title":"streak","score":9,"mom_score_d10":3,"mom_score_d10_prior":6,"mom_score_d10_date":"2026-09-01","mom_score_d10_label":"10d +3","d10_cls":"mom-score-d10-up","d10_side":"up","d10_title":"composite score 10 trading days: was 6 on 2026-09-01 → now 9 (Δ +3)"}}</script>
<script>function cardHTML(c){
  var t = (c && (c.t || c.d)) || "";
  return '<article class="card" data-t="'+t+'"><header><h2>'+t+'</h2><span class="score">'+(c.score!=null?c.score:"")+'<span class="score-d10">'+(c.mom_score_d10_label||"")+'</span></span><div class="pills"></div></header></article>';
}</script>
</body></html>"""
        html = ms.ensure_embedded(
            live,
            {
                "AAA US Equity": {
                    "label": "↑4d>5",
                    "cls": "mom-streak-up",
                    "title": "streak",
                    "score": 9,
                    "mom_score_d10": 3,
                    "mom_score_d10_prior": 6,
                    "mom_score_d10_date": "2026-09-01",
                    "mom_score_d10_label": "10d +3",
                    "d10_cls": "mom-score-d10-up",
                    "d10_side": "up",
                    "d10_title": "composite score 10 trading days: was 6 on 2026-09-01 → now 9 (Δ +3)",
                }
            },
        )
        html = cr.ensure_embedded(html)
        with tempfile.TemporaryDirectory() as tmp:
            page = Path(tmp) / "page.html"
            runner = Path(tmp) / "run.js"
            page.write_text(html, encoding="utf-8")
            runner.write_text(
                f"""
const {{ JSDOM }} = require({json.dumps(str(jsdom_mod))});
const fs = require("fs");
const html = fs.readFileSync({json.dumps(str(page))}, "utf8");
const dom = new JSDOM(html, {{ runScripts: "dangerously", url: "http://127.0.0.1/factorbook.html" }});
const window = dom.window;
const rendered = window.cardHTML({{
  t: "BBB", score: 4, mom_score: 4,
  mom_score_d10: -2, mom_score_d10_prior: 6, mom_score_d10_date: "2026-09-01",
  mom_score_d10_label: "10d \\u22122"
}});
const staticCard = window.document.querySelector('[data-t="AAA US Equity"]');
function capCount(root) {{
  if (!root) return 0;
  var host = root.querySelectorAll ? root : null;
  if (!host && root.innerHTML != null) host = root;
  return host ? host.querySelectorAll(".score-d10, .mom-score-d10-near").length : 0;
}}
const hold = window.document.createElement("div");
hold.innerHTML = rendered;
const report = {{
  rendered: rendered,
  renderedCaps: capCount(hold),
  staticCaps: capCount(staticCard),
  staticScoreText: staticCard && staticCard.querySelector(".score") ? staticCard.querySelector(".score").textContent : "",
  staticHtml: staticCard ? staticCard.outerHTML : ""
}};
console.log(JSON.stringify(report));
""",
                encoding="utf-8",
            )
            proc = subprocess.run(
                [node, str(runner)],
                check=False,
                capture_output=True,
                text=True,
                timeout=20,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
            report = json.loads(proc.stdout.strip().splitlines()[-1])
            rendered = report["rendered"]
            self.assertIn('class="score"', rendered)
            self.assertIn("\u22122", rendered)
            self.assertNotIn("10d", rendered)
            self.assertEqual(report["renderedCaps"], 1)
            self.assertIn('data-key="mom-score-d10-near"', rendered)
            self.assertNotRegex(rendered, r'data-key="mom-score-d10"(?!-)')
            self.assertIn("mom-score-d10-near down", rendered)
            self.assertIn("10 trading days", rendered)
            self.assertNotIn("scoreEl.appendChild", ms.strip_js())
            static_html = report["staticHtml"]
            self.assertEqual(report["staticCaps"], 1)
            self.assertEqual(report["staticScoreText"].count("+3"), 1)
            self.assertNotIn("10d +3", static_html)
            self.assertIn('data-key="mom-streak"', static_html)
            self.assertIn("mom-score-d10-near", static_html)
            self.assertNotRegex(static_html, r'data-key="mom-score-d10"(?!-)')


class DivCardColorTests(unittest.TestCase):
    def test_d10_signed_color_matches_div_card(self) -> None:
        css = cr.strip_css()
        self.assertIn(".card .mom-score-d10-near.down", css)
        self.assertIn(".card .mom-score-d10-near.up", css)
        self.assertIn(".card .mom-score-d10-near.flat", css)
        self.assertIn(".score-d10.down", css)
        self.assertNotRegex(css, r"\.score-d10\.dn(?!o)")
        down = css.split(".card .mom-score-d10-near.down", 1)[1].split("}", 1)[0]
        up = css.split(".card .mom-score-d10-near.up", 1)[1].split("}", 1)[0]
        self.assertIn("#fda4af", down)
        self.assertNotIn("#6ee7b7", down)
        self.assertIn("#6ee7b7", up)
        self.assertNotRegex(css, r"article\.card \.mom-score-d10-near\.down")
        streak = ms.d10_css()
        self.assertIn(".card .mom-score-d10-near.down", streak)
        self.assertIn("#fda4af", streak.split(".card .mom-score-d10-near.down", 1)[1].split("}", 1)[0])
        self.assertIn("#6ee7b7", streak.split(".card .mom-score-d10-near.up", 1)[1].split("}", 1)[0])


class Chg1dHeaderTests(unittest.TestCase):
    def test_signed_percent_beside_name_and_distinct_from_d10(self) -> None:
        up = cr.chg_1d_html({"t": "MSTR", "ret_1d": 0.012, "mom_score_d10": 0, "mom_score_d10_short": "0"})
        self.assertIn("+1.2%", up)
        self.assertIn("px-1d", up)
        self.assertIn("chg-1d", up)
        self.assertIn('title="1d CHG_PCT_1D"', up)
        self.assertNotIn("mom-score-d10", up)
        down = cr.chg_1d_html({"CHG_PCT_1D": -0.8})
        self.assertIn("\u22120.8%", down)
        self.assertIn("down", down)
        flat = cr.chg_1d_html({"day": 0})
        self.assertIn("0.0%", flat)
        self.assertIn("flat", flat)
        self.assertEqual(cr.chg_1d_html({"t": "MSTR", "score": 10, "mom_score_d10": 0}), "")
        self.assertNotIn("n/a", up.lower())

    def test_decimal_day_wins_over_bloomberg_points(self) -> None:
        self.assertAlmostEqual(cr.day_decimal({"day": 0.012, "CHG_PCT_1D": 9.0}), 0.012)
        self.assertAlmostEqual(cr.day_decimal({"ret_1d": -0.008}), -0.008)
        self.assertAlmostEqual(cr.day_decimal({"CHG_PCT_1D": -0.8}), -0.008)
        self.assertAlmostEqual(cr.day_decimal({"metrics": {"day_pct": 0.021}}), 0.021)
        self.assertIsNone(cr.day_decimal({"t": "MSTR", "score": 10}))
        stamped: dict = {"CHG_PCT_1D": 1.2}
        cr.alias_stats(stamped)
        self.assertAlmostEqual(stamped["day"], 0.012)
        self.assertAlmostEqual(stamped["ret_1d"], 0.012)
        self.assertAlmostEqual(stamped["metrics"]["day_pct"], 0.012)
        kept = {"day": 0.05, "CHG_PCT_1D": 1.2, "metrics": {"day_pct": 0.05}}
        cr.stamp_day(kept)
        self.assertEqual(kept["day"], 0.05)
        self.assertEqual(kept["metrics"]["day_pct"], 0.05)

    def test_article_header_places_chip_inside_name_not_score(self) -> None:
        card = {
            "ticker": "MSTR",
            "mom_score": 10,
            "ret_1d": 0.012,
            "mom_score_d10": 0,
            "mom_score_d10_prior": 10,
            "mom_score_d10_date": "2026-09-07",
        }
        html = desk_dash._article_html(card)
        self.assertRegex(
            html,
            r'<span class="sym">MSTR</span><span class="px-1d chg-1d up"[^>]*title="1d CHG_PCT_1D">\+1\.2%</span>',
        )
        self.assertIn('class="top"', html)
        score_at = html.find('<span class="score sc">')
        pills_at = html.find('<div class="pills">', score_at)
        score_html = html[score_at:pills_at]
        self.assertGreater(score_at, 0)
        self.assertIn("mom-score-d10-near", score_html)
        self.assertIn(">0<", score_html)
        self.assertNotIn("px-1d", score_html)
        self.assertNotIn("+1.2%", score_html)
        bare = desk_dash._article_html({"ticker": "MSTR", "mom_score": 10, "mom_score_d10": 0})
        self.assertNotIn("px-1d", bare)
        self.assertNotIn("chg-1d", bare)
        self.assertIn("mom-score-d10-near", bare)

    def test_header_title_pipe_when_company_present(self) -> None:
        both = {"ticker": "MSTR", "short_name": "STRATEGY INC", "ret_1d": 0.012, "mom_score": 10}
        self.assertEqual(cr.header_title(both), "MSTR | STRATEGY INC")
        self.assertEqual(cr.company_of(both), "STRATEGY INC")
        html = desk_dash._article_html(both)
        self.assertRegex(
            html,
            r'<div class="top">\s*<span class="sym">MSTR \| STRATEGY INC</span><span class="px-1d chg-1d up"[^>]*title="1d CHG_PCT_1D">\+1\.2%</span>',
        )
        self.assertIn('data-t="MSTR"', html)
        self.assertNotIn('data-t="MSTR |', html)
        score_at = html.find('<span class="score sc">')
        pills_at = html.find('<div class="pills">', score_at)
        self.assertNotIn("px-1d", html[score_at:pills_at])
        self.assertNotIn("+1.2%", html[score_at:pills_at])
        stripped = {"ticker": "MSTR", "name": "MSTR STRATEGY INC", "day": 0.012}
        self.assertEqual(cr.header_title(stripped), "MSTR | STRATEGY INC")
        self.assertEqual(
            cr.header_title({"ticker": "MSTR", "short_name": "MSTR | STRATEGY INC"}),
            "MSTR | STRATEGY INC",
        )
        self.assertEqual(cr.header_title({"d": "MSTR", "NAME": "STRATEGY INC"}), "MSTR | STRATEGY INC")
        symbol_only = desk_dash._article_html({"ticker": "MSTR", "name": "MSTR", "ret_1d": 0.012, "mom_score": 10})
        self.assertRegex(symbol_only, r'<span class="sym">MSTR</span><span class="px-1d chg-1d up"')
        self.assertNotIn("|", symbol_only.split('class="sym">', 1)[1].split("</span>", 1)[0])
        self.assertEqual(cr.header_title({"ticker": "MSTR US Equity", "name": "MSTR US Equity"}), "MSTR")
        self.assertEqual(cr.header_title({"ticker": "MSTR", "name": "MSTR", "short_name": "MSTR"}), "MSTR")
        self.assertEqual(cr.company_of({"ticker": "MSTR", "name": "MSTR US Equity"}), "")
        self.assertEqual(
            cr.company_of({"ticker": "NVDA US Equity", "short_name": "NVIDIA CORP"}),
            "NVIDIA CORP",
        )
        self.assertEqual(cr.company_of({"ticker": "AAPL", "name": "AAPL 3.5 08/15/29 Corp"}), "")
        named = cr.co_name_db([{"ticker": "MSTR US Equity", "short_name": "STRATEGY INC"}])
        self.assertEqual(named.get("MSTR"), "STRATEGY INC")
        self.assertEqual(named.get("MSTR US Equity"), "STRATEGY INC")
        self.assertNotIn("|", named["MSTR"])

    def test_detail_and_live_cardhtml_paint_1d_beside_name(self) -> None:
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        jsdom_root = Path("/tmp/fd-jsdom")
        jsdom_mod = jsdom_root / "node_modules" / "jsdom"
        if not jsdom_mod.is_dir():
            jsdom_root.mkdir(parents=True, exist_ok=True)
            npm = shutil.which("npm")
            if not npm:
                self.skipTest("npm not installed")
            subprocess.run(
                [npm, "install", "--prefix", str(jsdom_root), "jsdom@24"],
                check=True,
                capture_output=True,
                text=True,
                timeout=60,
            )
        live = """<!DOCTYPE html><html><head>
<style>
.score.hi, .score.hi .mom-score-d10-near { color: rgb(61, 204, 138); }
article.card .mom-score-d10-near.down { color: #fda4af !important; }
</style>
</head><body>
<section id="view-detail" data-t="MSTR US Equity">
  <article class="card" data-t="MSTR">
    <header>
      <h2>MSTR</h2>
      <span class="score">10<span class="mom-score-d10-near flat" data-key="mom-score-d10-near">0</span></span>
      <div class="pills"><span class="badge">Momentum building</span></div>
    </header>
  </article>
  <div class="factor-card">
    <header><h2>MSTR STRATEGY INC</h2><span class="badge">LEADER</span></header>
  </div>
</section>
<div class="card" data-t="AMD US Equity">
  <div class="top">
    <span class="sym">AMD</span>
    <span class="score hi">8<span class="mom-score-d10-near down" data-key="mom-score-d10-near">−2</span></span>
  </div>
</div>
<div class="card" data-t="LULU US Equity">
  <div class="top">
    <span class="sym">LULU</span>
    <span class="score hi">6</span>
  </div>
</div>
<script>function cardHTML(c){
  var t = (c && (c.t || c.d)) || "";
  var d10 = (c && c.mom_score_d10_short) ? '<span class="mom-score-d10-near flat" data-key="mom-score-d10-near">'+c.mom_score_d10_short+'</span>' : '';
  return '<article class="card" data-t="'+t+'"><header><h2>'+t+'</h2><span class="score">'+(c.score!=null?c.score:"")+d10+'</span><div class="pills"><span class="badge">OUTLIER</span></div></header></article>';
}</script>
</body></html>"""
        html = cr.ensure_embedded(
            live,
            {
                "MSTR": 0.012,
                "MSTR US Equity": 0.012,
                "AMD": -0.021,
                "AMD US Equity": -0.021,
                "LULU": 0.004,
            },
            {
                "MSTR": "STRATEGY INC",
                "MSTR US Equity": "STRATEGY INC",
                "AMD": "ADVANCED MICRO DEVICES",
                "AMD US Equity": "ADVANCED MICRO DEVICES",
            },
        )
        self.assertIn('id="fd-chg-1d-db"', html)
        self.assertIn('id="fd-co-name-db"', html)
        self.assertIn("paintChg1d", html)
        self.assertIn("paintTitle", html)
        with tempfile.TemporaryDirectory() as tmp:
            page = Path(tmp) / "page.html"
            runner = Path(tmp) / "run.js"
            page.write_text(html, encoding="utf-8")
            runner.write_text(
                f"""
const {{ JSDOM }} = require({json.dumps(str(jsdom_mod))});
const fs = require("fs");
const html = fs.readFileSync({json.dumps(str(page))}, "utf8");
const dom = new JSDOM(html, {{ runScripts: "dangerously", url: "http://127.0.0.1/factorbook.html" }});
const window = dom.window;
const document = window.document;
if (typeof window.__FD_PAINT_CHG_1D__ === "function") {{
  window.__FD_PAINT_CHG_1D__();
  window.__FD_PAINT_CHG_1D__();
}}
function denseInfo(card) {{
  if (!card) return null;
  var sym = card.querySelector(".sym");
  var top = card.querySelector(".top");
  var chip = top ? top.querySelector(".px-1d, .chg-1d") : null;
  var d10 = card.querySelector(".mom-score-d10-near");
  var score = card.querySelector(".score");
  return {{
    sym: sym ? sym.textContent : "",
    chipText: chip ? chip.textContent : null,
    chipCls: chip ? chip.className : "",
    chipParent: chip && chip.parentElement ? chip.parentElement.className : "",
    chipAfterSym: !!(chip && sym && chip.previousElementSibling === sym),
    chipCount: top ? top.querySelectorAll(".px-1d, .chg-1d").length : 0,
    scoreText: score ? score.textContent : "",
    d10Color: d10 ? window.getComputedStyle(d10).color : ""
  }};
}}
function chipInfo(root) {{
  if (!root) return null;
  var el = root.querySelector(".px-1d, .chg-1d, [data-key='chg-1d']");
  if (!el) return null;
  var score = el.closest && el.closest(".score, .sc");
  return {{ text: el.textContent, title: el.title, cls: el.className, inScore: !!score }};
}}
const left = document.querySelector("article.card");
const right = document.querySelector(".factor-card");
const hold = document.createElement("div");
hold.innerHTML = window.cardHTML({{
  t: "QQQ", score: 4, ret_1d: -0.008, short_name: "INVESCO QQQ",
  mom_score_d10: 0, mom_score_d10_short: "0"
}});
const miss = document.createElement("div");
miss.innerHTML = window.cardHTML({{ t: "MISS", score: 3, mom_score_d10: 0, mom_score_d10_short: "0" }});
const bb = document.createElement("div");
bb.innerHTML = window.cardHTML({{
  t: "AMD", score: 8, CHG_PCT_1D: 1.2, day: null,
  mom_score_d10: 1, mom_score_d10_short: "+1"
}});
const zero = document.createElement("div");
zero.innerHTML = window.cardHTML({{
  t: "ZERO", score: 6, day: 0,
  mom_score_d10: 0, mom_score_d10_short: "0"
}});
const report = {{
  leftChip: chipInfo(left),
  leftName: left.querySelector("h2") ? left.querySelector("h2").textContent : "",
  leftScore: left.querySelector(".score") ? left.querySelector(".score").textContent : "",
  leftPills: left.querySelector(".pills") ? left.querySelector(".pills").textContent : "",
  leftD10: left.querySelectorAll(".mom-score-d10-near, [data-key='mom-score-d10-near']").length,
  rightChip: chipInfo(right),
  rightName: right.querySelector("h2") ? right.querySelector("h2").textContent : "",
  rightBadge: right.textContent.indexOf("LEADER") >= 0,
  qqq: hold.innerHTML,
  qqqChip: chipInfo(hold),
  qqqScore: hold.querySelector(".score") ? hold.querySelector(".score").textContent : "",
  missChip: chipInfo(miss),
  missHtml: miss.innerHTML,
  amd: bb.innerHTML,
  amdChip: chipInfo(bb),
  amdScore: bb.querySelector(".score") ? bb.querySelector(".score").textContent : "",
  zeroChip: chipInfo(zero),
  zeroScore: zero.querySelector(".score") ? zero.querySelector(".score").textContent : "",
  amdDense: denseInfo(document.querySelector('div.card[data-t="AMD US Equity"]')),
  luluDense: denseInfo(document.querySelector('div.card[data-t="LULU US Equity"]'))
}};
console.log(JSON.stringify(report));
""",
                encoding="utf-8",
            )
            proc = subprocess.run(
                [node, str(runner)],
                check=False,
                capture_output=True,
                text=True,
                timeout=20,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
            report = json.loads(proc.stdout.strip().splitlines()[-1])
            self.assertEqual(report["leftChip"]["text"], "+1.2%")
            self.assertIn("up", report["leftChip"]["cls"])
            self.assertEqual(report["leftChip"]["title"], "1d CHG_PCT_1D")
            self.assertFalse(report["leftChip"]["inScore"])
            self.assertEqual(report["leftD10"], 1)
            self.assertIn("0", report["leftScore"])
            self.assertNotIn("+1.2%", report["leftScore"])
            self.assertIn("Momentum building", report["leftPills"])
            self.assertIn("MSTR | STRATEGY INC", report["leftName"])
            self.assertIn("+1.2%", report["leftName"])
            self.assertEqual(report["rightChip"]["text"], "+1.2%")
            self.assertFalse(report["rightChip"]["inScore"])
            self.assertIn("MSTR | STRATEGY INC", report["rightName"])
            self.assertNotIn("MSTR STRATEGY INC", report["rightName"])
            self.assertTrue(report["rightBadge"])
            self.assertIn("QQQ | INVESCO QQQ", report["qqq"])
            self.assertEqual(report["qqqChip"]["text"], "\u22120.8%")
            self.assertIn("down", report["qqqChip"]["cls"])
            self.assertNotIn("\u22120.8%", report["qqqScore"])
            self.assertIn("0", report["qqqScore"])
            self.assertIn('data-key="mom-score-d10-near"', report["qqq"])
            self.assertIsNone(report["missChip"])
            self.assertNotIn("px-1d", report["missHtml"])
            self.assertNotIn("|", report["missHtml"])
            self.assertIn("AMD | ADVANCED MICRO DEVICES", report["amd"])
            self.assertNotIn("n/a", report["qqq"].lower())
            self.assertEqual(report["amdChip"]["text"], "+1.2%")
            self.assertIn("+1", report["amdScore"])
            self.assertNotIn("+1.2%", report["amdScore"])
            self.assertEqual(report["zeroChip"]["text"], "0.0%")
            self.assertIn("flat", report["zeroChip"]["cls"])
            self.assertNotIn("0.0%", report["zeroScore"])
            self.assertIn("0", report["zeroScore"])
            amd_dense = report["amdDense"]
            self.assertEqual(amd_dense["sym"], "AMD | ADVANCED MICRO DEVICES")
            self.assertEqual(amd_dense["chipText"], "\u22122.1%")
            self.assertIn("down", amd_dense["chipCls"])
            self.assertEqual(amd_dense["chipParent"], "top")
            self.assertTrue(amd_dense["chipAfterSym"])
            self.assertEqual(amd_dense["chipCount"], 1)
            self.assertNotIn("\u22122.1%", amd_dense["scoreText"])
            self.assertIn("\u22122", amd_dense["scoreText"])
            d10_color = amd_dense["d10Color"].replace(" ", "").lower()
            self.assertTrue("253,164,175" in d10_color or "fda4af" in d10_color, amd_dense["d10Color"])
            lulu_dense = report["luluDense"]
            self.assertEqual(lulu_dense["sym"], "LULU")
            self.assertNotIn("|", lulu_dense["sym"])
            self.assertEqual(lulu_dense["chipText"], "+0.4%")
            self.assertIn("up", lulu_dense["chipCls"])
            self.assertEqual(lulu_dense["chipParent"], "top")
            self.assertTrue(lulu_dense["chipAfterSym"])
            self.assertEqual(lulu_dense["chipCount"], 1)

    def test_write_combined_bakes_pipe_and_1d_into_dense_sym(self) -> None:
        live = """<!DOCTYPE html><html><head></head><body>
<nav>
  <button id="refresh">Refresh</button>
  <button>Momentum Up</button>
  <button>Momentum Down</button>
  <button>Outliers</button>
  <button>Options</button>
</nav>
<div id="mom-up-grid" class="grid dense">
  <div class="card" data-t="MSTR US Equity"><div class="top"><span class="sym">MSTR</span><span class="score hi">10</span></div></div>
  <div class="card" data-t="LULU US Equity"><div class="top"><span class="sym">LULU</span><span class="score hi">4</span></div></div>
</div>
<script>
function cardHTML(c) {
  var t = c.t || "";
  return '<div class="card"><div class="top"><span class="sym">' + t + '</span><span class="score hi"></span></div></div>';
}
</script>
</body></html>"""
        cards = [
            {"ticker": "MSTR US Equity", "short_name": "STRATEGY INC", "day": 0.012, "mom_score": 10},
            {"ticker": "LULU US Equity", "day": -0.008, "mom_score": 4},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dest = root / "factorbook.html"
            dest.write_text(live, encoding="utf-8")
            out = desk_dash.write_combined(
                dest,
                root=root,
                cards=cards,
                book={"asof": "2026-09-21", "names": {}, "meta": {}},
            )
            text = out.read_text(encoding="utf-8")
        self.assertIn("MSTR | STRATEGY INC", text)
        self.assertIn('class="px-1d chg-1d up"', text)
        self.assertIn("+1.2%", text)
        self.assertIn('class="sym">LULU</span>', text)
        self.assertNotIn("LULU |", text)
        self.assertIn("\u22120.8%", text)
        self.assertIn("window.__fdSymTitle", text)
        self.assertIn('id="fd-co-name-db"', text)
        self.assertIn("STRATEGY INC", text)
        self.assertIn('id="fd-sym-helpers"', text)
        rendered = desk_dash._article_html(cards[0])
        self.assertIn(" | ", rendered)
        self.assertIn("px-1d", rendered)
        self.assertIn('class="sym"', rendered)

    def test_drill_title_chip_and_full_book_dbs_survive_refresh(self) -> None:
        live = """<!DOCTYPE html><html><head>
<script type="application/json" id="fd-chg-1d-db">{"QQQ":0.02,"SPY":-0.01}</script>
<script type="application/json" id="fd-co-name-db">{"QQQ":"INVESCO QQQ","SPY":"SPDR S&P 500"}</script>
</head><body>
<nav>
  <button id="refresh">Refresh</button>
  <button>Momentum Up</button>
  <button>Momentum Down</button>
  <button>Outliers</button>
  <button>Options</button>
</nav>
<script>
function factorDrillHTML(b) {
  const coName = b.name || "";
  const title = coName ? `${b.d}  ${coName}` : b.d;
  return `<div class="drill"><div class="sym">${title}</div><div class="sub">${coName} · ${b.d}</div><div class="metrics">20d z</div></div>`;
}
function cardHTML(c) {
  var t = c.t || c.d || "";
  return `<div class="card"><div class="top"><span class="sym">${t}</span><span class="score hi"></span></div></div>`;
}
</script>
</body></html>"""
        book = {
            "asof": "2026-09-21",
            "names": {
                "MSTR US Equity": {"short_name": "STRATEGY INC", "day": 0.012},
                "AAPL US Equity": {"NAME": "APPLE INC", "CHG_PCT_1D": 1.5},
                "NVDA US Equity": {"short_name": "NVIDIA CORP", "chg_pct_1d": -0.8},
            },
            "meta": {},
        }
        cards = [{"ticker": "MSTR US Equity", "short_name": "STRATEGY INC", "day": 0.012, "mom_score": 10}]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dest = root / "factorbook.html"
            dest.write_text(live, encoding="utf-8")
            first = desk_dash.write_combined(dest, root=root, cards=cards, book=book)
            text = first.read_text(encoding="utf-8")
            again = desk_dash.write_combined(dest, root=root, cards=cards, book=book)
            refreshed = again.read_text(encoding="utf-8")
        for html in (text, refreshed):
            self.assertIn("function factorDrillHTML", html)
            self.assertIn("${b.d} | ${coName}", html)
            self.assertNotIn("${b.d}  ${coName}", html)
            self.assertIn("${title}${window.__fdChgSpan(b.d || b.t)}", html)
            self.assertIn("${coName} · ${b.d}", html)
            sub = html.split('class="sub">', 1)[1].split("</div>", 1)[0]
            self.assertNotIn("__fdChgSpan", sub)
            self.assertNotIn("px-1d", sub)
            self.assertIn("window.__fdSymTitle", html)
            self.assertIn("window.__fdChgSpan", html)
            self.assertIn('class="px-1d chg-1d', html)
            day = json.loads(re.search(r'id="fd-chg-1d-db">(.*?)</script>', html, re.S).group(1))
            co = json.loads(re.search(r'id="fd-co-name-db">(.*?)</script>', html, re.S).group(1))
            for ticker in ("QQQ", "SPY", "MSTR", "AAPL", "NVDA"):
                self.assertIn(ticker, day)
                self.assertIn(ticker, co)
            self.assertAlmostEqual(day["QQQ"], 0.02)
            self.assertAlmostEqual(day["MSTR"], 0.012)
            self.assertAlmostEqual(day["AAPL"], 0.015)
            self.assertAlmostEqual(day["NVDA"], -0.008)
            self.assertEqual(co["MSTR"], "STRATEGY INC")
            self.assertEqual(co["AAPL"], "APPLE INC")
            self.assertEqual(co["NVDA"], "NVIDIA CORP")
            self.assertEqual(co["QQQ"], "INVESCO QQQ")
            self.assertGreater(len(day), 1)
            self.assertGreater(len(co), 1)


if __name__ == "__main__":
    unittest.main()
