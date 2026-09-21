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
<article class="card" data-t="AAA US Equity"><header><h2>AAA</h2><span class="score">9</span><div class="pills"></div></header></article>
<script type="application/json" id="mom-streak-db">{"AAA US Equity":{"label":"↑4d>5","cls":"mom-streak-up","title":"streak","score":9,"mom_score_d10":3,"mom_score_d10_prior":6,"mom_score_d10_date":"2026-09-01","mom_score_d10_label":"10d +3","d10_cls":"mom-score-d10-up","d10_side":"up","d10_title":"composite score 10 trading days: was 6 on 2026-09-01 → now 9 (Δ +3)"}}</script>
<script>function cardHTML(c){
  var t = (c && (c.t || c.d)) || "";
  return '<article class="card" data-t="'+t+'"><header><h2>'+t+'</h2><span class="score">'+(c.score!=null?c.score:"")+'</span><div class="pills"></div></header></article>';
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
const report = {{
  rendered: rendered,
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
            self.assertIn('data-key="mom-score-d10-near"', rendered)
            self.assertNotRegex(rendered, r'data-key="mom-score-d10"(?!-)')
            self.assertIn("mom-score-d10-near down", rendered)
            self.assertIn("10 trading days", rendered)
            static_html = report["staticHtml"]
            self.assertIn("+3", static_html)
            self.assertNotIn("10d +3", static_html)
            self.assertIn('data-key="mom-streak"', static_html)
            self.assertIn('data-key="mom-score-d10-near"', static_html)
            self.assertNotRegex(static_html, r'data-key="mom-score-d10"(?!-)')
            self.assertIn("10 trading days", static_html)


if __name__ == "__main__":
    unittest.main()
