"""Momentum Up/Down status chips — patch viewFilt / applyMomFilters / buildFiltBar."""

from __future__ import annotations

import json
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

import desk_dash  # noqa: E402


FILT_JS = r"""
var viewFilt = {
  "mom-up": { score: "all", flows: "all", tags: "all", sort: "Score", pills: {} },
  "mom-down": { score: "all", flows: "all", tags: "all", sort: "Score", pills: {} },
  "outliers": { score: "all", flows: "all", tags: "all", sort: "Score", pills: {} }
};
function grp(label, chips, key, cur) {
  var h = '<div class="fgrp"><span class="flab">' + label + '</span>';
  for (var i = 0; i < chips.length; i++) {
    var v = chips[i][0], lab = chips[i][1];
    var on = String(cur) === String(v) ? " on" : "";
    h += '<button type="button" class="fchip' + on + '" data-k="' + key + '" data-v="' + v + '">' + lab + '</button>';
  }
  return h + '</div>';
}
function applyMomFilters(cards, viewKey) {
  var vf = viewFilt[viewKey] || {};
  var pills = vf.pills || {};
  return (cards || []).filter(function (c) {
    if (vf.score && vf.score !== "all") {
      var n = parseFloat(vf.score);
      if (isFinite(n) && !(Number(c.score) >= n)) return false;
    }
    if (vf.flows && vf.flows !== "all") {
      if (vf.flows === "OPT SPIKE" && !c.optSpike) return false;
      if (vf.flows === "Big flow" && !c.bigFlow) return false;
    }
    if (vf.tags && vf.tags !== "all") {
      if (vf.tags === "OUTLIER" && !c.outlier) return false;
      if (vf.tags === "NEW" && !c.isNew) return false;
    }
    var pk = Object.keys(pills);
    for (var i = 0; i < pk.length; i++) {
      if (pills[pk[i]] && !(c.pills && c.pills[pk[i]])) return false;
    }
    return true;
  });
}
function buildFiltBar(viewKey) {
  var vf = viewFilt[viewKey] || {};
  var html = "";
  html += grp("SCORE", [["all","All"],["5+","5+"],["6+","6+"],["7+","7+"],["8+","8+"]], "score", vf.score);
  html += grp("FLOWS", [["all","All"],["OPT SPIKE","OPT SPIKE"],["Big flow","Big flow"]], "flows", vf.flows);
  html += grp("TAGS", [["all","All"],["OUTLIER","OUTLIER"],["NEW","NEW"]], "tags", vf.tags);
  html += grp("SORT", [["Score","Score"],["Outlier","Outlier"],["Name","Name"],["RS","RS"],["Opt","Opt"]], "sort", vf.sort);
  html += grp("PILLS", [["MA FAN","MA FAN"],["CLOSE HI","CLOSE HI"]], "pills", "");
  return html;
}
"""

APPEND_JS = r"""
function buildFiltBar(viewKey) {
  var tagChips = [["all","All"],["OUTLIER","OUTLIER"],["NEW","NEW"]];
  var sortChips = [["Score","Score"],["Name","Name"],["RS","RS"]];
  var bar = document.getElementById("filt-bar");
  bar.appendChild(grp("TAGS", tagChips, "tags"));
  bar.appendChild(grp("SORT", sortChips, "sort"));
}
"""

ARRAY_JS = r"""
function buildFiltBar(viewKey) {
  return [
    grp("TAGS", [["all","All"],["OUTLIER","OUTLIER"],["NEW","NEW"]], "tags"),
    grp("SORT", [["Score","Score"],["Name","Name"]], "sort")
  ].join("");
}
"""

SHARED_JS = r"""
function ensureFilt(viewKey) {
  if (!viewFilt[viewKey]) {
    viewFilt[viewKey] = { score: "all", flows: "all", tags: "all", sort: "Score", pills: {} };
  }
  return viewFilt[viewKey];
}
"""


def _page(js: str) -> str:
    return f"<!DOCTYPE html><html><body><script>\n{js}\n</script></body></html>"


def _patched_script(js: str) -> str:
    html = desk_dash.ensure_mom_status_filter(_page(js))
    start = html.find("<script>")
    # The page script is the first script; fallback is fd-mom-status-js.
    end = html.find("</script>", start)
    return html[start + len("<script>") : end]


def _node_eval(js: str, expr: str) -> dict:
    node = shutil.which("node")
    if not node:
        raise unittest.SkipTest("node not installed")
    with tempfile.TemporaryDirectory() as tmp:
        code = Path(tmp) / "filt.js"
        ask = Path(tmp) / "expr.js"
        runner = Path(tmp) / "run.js"
        code.write_text(js, encoding="utf-8")
        ask.write_text(expr, encoding="utf-8")
        runner.write_text(
            """
const vm = require("vm");
const fs = require("fs");
const sandbox = { console: console };
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), sandbox);
const result = vm.runInContext(fs.readFileSync(process.argv[3], "utf8"), sandbox);
process.stdout.write(JSON.stringify(result));
""",
            encoding="utf-8",
        )
        proc = subprocess.run(
            [node, str(runner), str(code), str(ask)],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
        if proc.returncode != 0:
            raise AssertionError(proc.stderr or proc.stdout)
        return json.loads(proc.stdout)


class MomStatusPatchTests(unittest.TestCase):
    def test_viewfilt_status_all_on_mom_only(self) -> None:
        js = _patched_script(FILT_JS)
        up = js.split('"mom-up":', 1)[1].split('"mom-down":', 1)[0]
        down = js.split('"mom-down":', 1)[1].split('"outliers":', 1)[0]
        out = js.split('"outliers":', 1)[1].split("};", 1)[0]
        self.assertIn('status:"all"', up)
        self.assertIn('status:"all"', down)
        self.assertNotIn("status", out)

    def test_shared_assign_gains_status_all(self) -> None:
        js = _patched_script(SHARED_JS)
        self.assertIn('viewFilt[viewKey] = {status:"all", score: "all"', js)

    def test_apply_predicate_and_build_markers(self) -> None:
        js = _patched_script(FILT_JS)
        self.assertIn("fd-status-pred", js)
        self.assertIn('vf.status !== "all"', js)
        self.assertIn("c.status !== vf.status", js)
        self.assertIn("Strong momentum", js)
        self.assertIn("Momentum building", js)
        self.assertIn("Constructive", js)
        self.assertIn("Weak / fading", js)
        self.assertIn("Softening", js)
        self.assertNotIn("Range-bound", js)
        self.assertNotIn("In transition", js)
        self.assertNotIn("Neutral", js)
        self.assertIn('(viewKey==="mom-up"||viewKey==="mom-down")', js)

    def test_status_group_sits_after_tags_before_sort(self) -> None:
        js = _patched_script(FILT_JS)
        build = js.split("function buildFiltBar", 1)[1].split("function ", 1)[0]
        tags_at = build.find('grp("TAGS"')
        status_at = build.find('grp("STATUS"')
        sort_at = build.find('grp("SORT"')
        pills_at = build.find('grp("PILLS"')
        self.assertGreater(tags_at, 0)
        self.assertGreater(status_at, tags_at)
        self.assertGreater(sort_at, status_at)
        self.assertGreater(pills_at, sort_at)

    def test_append_and_array_builders(self) -> None:
        appended = _patched_script(APPEND_JS)
        self.assertIn('grp("STATUS"', appended)
        self.assertIn("appendChild", appended)
        self.assertIn("Strong momentum", appended)
        self.assertIn("Weak / fading", appended)
        self.assertEqual(appended.count('grp("STATUS"'), 1)
        arrayed = _patched_script(ARRAY_JS)
        self.assertIn('grp("STATUS"', arrayed)
        self.assertIn('(viewKey==="mom-up"||viewKey==="mom-down")?grp("STATUS"', arrayed)
        sort_at = arrayed.find('grp("SORT"')
        status_at = arrayed.find('grp("STATUS"')
        tags_at = arrayed.find('grp("TAGS"')
        self.assertGreater(status_at, tags_at)
        self.assertGreater(sort_at, status_at)

    def test_patch_is_idempotent(self) -> None:
        once = desk_dash.ensure_mom_status_filter(_page(FILT_JS))
        twice = desk_dash.ensure_mom_status_filter(once)
        self.assertEqual(once.count('status:"all"'), twice.count('status:"all"'))
        self.assertEqual(once.count("fd-status-pred"), twice.count("fd-status-pred"))
        self.assertEqual(once.count('grp("STATUS"'), twice.count('grp("STATUS"'))
        self.assertEqual(once.count("fd-change-pred"), twice.count("fd-change-pred"))
        self.assertEqual(once.count('grp("CHANGE"'), twice.count('grp("CHANGE"'))
        self.assertEqual(once.count('id="fd-mom-status-js"'), 1)
        self.assertEqual(twice.count('id="fd-mom-status-js"'), 1)

    def test_skinny_render_has_no_status_script(self) -> None:
        html = desk_dash.render_html(cards=[], book=None)
        self.assertNotIn('id="fd-mom-status-js"', html)
        self.assertNotIn("fd-status-pred", html)
        self.assertNotIn('id="gics-filter-strip"', html)

    def test_filter_composes_with_score_flows_pills(self) -> None:
        js = _patched_script(FILT_JS)
        expr = r"""
(function () {
  var cards = [
    {t:"AMD", score:12, status:"Strong momentum", optSpike:true, outlier:true, pills:{ "MA FAN": true }},
    {t:"MU", score:12, status:"Constructive", optSpike:true, pills:{}},
    {t:"TEAM", score:8, status:"Momentum building", pills:{}},
    {t:"WEAK", score:12, status:"Weak / fading", pills:{}},
    {t:"LOW", score:4, status:"Strong momentum", optSpike:true, pills:{}},
    {t:"QUIET", score:11, status:"Strong momentum", optSpike:false, pills:{}}
  ];
  viewFilt["mom-up"].status = "Strong momentum";
  viewFilt["mom-up"].score = "5+";
  var both = applyMomFilters(cards, "mom-up").map(function (c) { return c.t; });
  viewFilt["mom-up"].status = "all";
  var scoreOnly = applyMomFilters(cards, "mom-up").map(function (c) { return c.t; });
  viewFilt["mom-up"].score = "all";
  viewFilt["mom-up"].status = "Constructive";
  viewFilt["mom-up"].flows = "OPT SPIKE";
  var flow = applyMomFilters(cards, "mom-up").map(function (c) { return c.t; });
  viewFilt["mom-up"].flows = "all";
  viewFilt["mom-up"].status = "all";
  viewFilt["mom-up"].pills = { "MA FAN": true };
  var pills = applyMomFilters(cards, "mom-up").map(function (c) { return c.t; });
  viewFilt["mom-up"].pills = {};
  viewFilt["mom-down"].status = "Softening";
  var down = applyMomFilters([{t:"S", status:"Softening"}, {t:"W", status:"Weak / fading"}, {t:"U", status:"Strong momentum"}], "mom-down").map(function (c) { return c.t; });
  var outliers = applyMomFilters(cards, "outliers").map(function (c) { return c.t; });
  viewFilt["mom-up"].status = "Strong momentum";
  var upHtml = buildFiltBar("mom-up");
  var downHtml = buildFiltBar("mom-down");
  viewFilt["mom-up"].status = "all";
  var outHtml = buildFiltBar("outliers");
  function seg(html, a, b) {
    var i = html.indexOf(">" + a + "<");
    var j = html.indexOf(">" + b + "<");
    return html.slice(i, j);
  }
  return {
    both: both,
    scoreOnly: scoreOnly,
    flow: flow,
    pills: pills,
    down: down,
    outliers: outliers,
    upStatus: seg(upHtml, "STATUS", "SORT"),
    downStatus: seg(downHtml, "STATUS", "SORT"),
    outHasStatus: outHtml.indexOf(">STATUS<") >= 0,
    upOrder: [upHtml.indexOf(">TAGS<"), upHtml.indexOf(">STATUS<"), upHtml.indexOf(">SORT<"), upHtml.indexOf(">PILLS<")]
  };
})()
"""
        got = _node_eval(js, expr)
        self.assertEqual(got["both"], ["AMD", "QUIET"])
        self.assertEqual(got["scoreOnly"], ["AMD", "MU", "TEAM", "WEAK", "QUIET"])
        self.assertEqual(got["flow"], ["MU"])
        self.assertEqual(got["pills"], ["AMD"])
        self.assertEqual(got["down"], ["S"])
        self.assertEqual(got["outliers"], ["AMD", "MU", "TEAM", "WEAK", "LOW", "QUIET"])
        up = got["upStatus"]
        self.assertIn(">All<", up)
        self.assertIn(">Strong momentum<", up)
        self.assertIn(">Momentum building<", up)
        self.assertIn(">Constructive<", up)
        self.assertNotIn("Weak / fading", up)
        self.assertNotIn("Softening", up)
        self.assertIn('data-v="all"', up)
        self.assertIn('class="fchip" data-k="status" data-v="all"', up)
        self.assertIn('class="fchip on" data-k="status" data-v="Strong momentum"', up)
        down = got["downStatus"]
        self.assertIn(">All<", down)
        self.assertIn(">Weak / fading<", down)
        self.assertIn(">Softening<", down)
        self.assertNotIn("Strong momentum", down)
        self.assertNotIn("Constructive", down)
        self.assertNotIn("Momentum building", down)
        self.assertFalse(got["outHasStatus"])
        order = got["upOrder"]
        self.assertTrue(all(i >= 0 for i in order), order)
        self.assertEqual(order, sorted(order))

    def test_change_row_filters_mom_score_d10(self) -> None:
        js = _patched_script(FILT_JS)
        self.assertIn("fd-change-pred", js)
        self.assertIn('change:"all"', js)
        self.assertNotIn("Up (+)", js)
        build_src = js.split("function buildFiltBar", 1)[1]
        for label in ("+>3", "+\u22643", "Flat", "\u2212\u22643", "\u2212>3"):
            self.assertIn(label, build_src)
        up = js.split('"mom-up":', 1)[1].split('"mom-down":', 1)[0]
        out = js.split('"outliers":', 1)[1].split("};", 1)[0]
        self.assertIn('change:"all"', up)
        self.assertNotIn("change", out)
        build = js.split("function buildFiltBar", 1)[1].split("function ", 1)[0]
        score_at = build.find('grp("SCORE"')
        change_at = build.find('grp("CHANGE"')
        flows_at = build.find('grp("FLOWS"')
        self.assertGreater(score_at, 0)
        self.assertGreater(change_at, score_at)
        self.assertGreater(flows_at, change_at)
        self.assertIn('(viewKey==="mom-up"||viewKey==="mom-down")', build)
        appended = _patched_script(APPEND_JS)
        self.assertNotIn('grp("CHANGE"', appended)
        expr = r"""
(function () {
  var cards = [
    {t:"BIG", score:9, mom_score_d10: 4, status:"Strong momentum"},
    {t:"THREE", score:9, mom_score_d10: 3, status:"Strong momentum"},
    {t:"SMALL", score:9, mom_score_d10: 1, status:"Strong momentum"},
    {t:"FLAT", score:9, mom_score_d10: 0, status:"Constructive"},
    {t:"N3", score:9, mom_score_d10: -3, status:"Strong momentum"},
    {t:"N2", score:9, mom_score_d10: -2, status:"Strong momentum"},
    {t:"N4", score:9, mom_score_d10: -4, status:"Strong momentum"},
    {t:"MISS", score:9, status:"Strong momentum"},
    {t:"BLANK", score:9, mom_score_d10: "", status:"Strong momentum"},
    {t:"NAN", score:9, mom_score_d10: "nope", status:"Strong momentum"}
  ];
  function names(list) { return list.map(function (c) { return c.t; }); }
  viewFilt["mom-up"].change = "gt3";
  var gt3 = names(applyMomFilters(cards, "mom-up"));
  viewFilt["mom-up"].change = "le3";
  var le3 = names(applyMomFilters(cards, "mom-up"));
  viewFilt["mom-up"].change = "flat";
  var flatNames = names(applyMomFilters(cards, "mom-up"));
  viewFilt["mom-up"].change = "nle3";
  var nle3 = names(applyMomFilters(cards, "mom-up"));
  viewFilt["mom-up"].change = "ngt3";
  var ngt3 = names(applyMomFilters(cards, "mom-up"));
  viewFilt["mom-up"].change = "all";
  viewFilt["mom-up"].status = "Constructive";
  var statusNames = names(applyMomFilters(cards, "mom-up"));
  viewFilt["mom-up"].status = "all";
  var allNames = names(applyMomFilters(cards, "mom-up"));
  viewFilt["mom-up"].change = "gt3";
  var upHtml = buildFiltBar("mom-up");
  var outHtml = buildFiltBar("outliers");
  var changeHtml = upHtml.slice(upHtml.indexOf(">CHANGE<"), upHtml.indexOf(">FLOWS<"));
  var on = (changeHtml.match(/class="fchip on"/g) || []).length;
  return {
    gt3: gt3,
    le3: le3,
    flatNames: flatNames,
    nle3: nle3,
    ngt3: ngt3,
    statusNames: statusNames,
    allNames: allNames,
    on: on,
    hasGt: changeHtml.indexOf(">+>3<") >= 0,
    hasLe: changeHtml.indexOf(">+\u22643<") >= 0,
    hasFlat: changeHtml.indexOf(">Flat<") >= 0,
    hasNle: changeHtml.indexOf(">\u2212\u22643<") >= 0,
    hasNgt: changeHtml.indexOf(">\u2212>3<") >= 0,
    outChange: outHtml.indexOf(">CHANGE<") >= 0
  };
})()
"""
        got = _node_eval(js, expr)
        self.assertEqual(got["gt3"], ["BIG"])
        self.assertEqual(got["le3"], ["THREE", "SMALL"])
        self.assertEqual(got["flatNames"], ["FLAT"])
        self.assertEqual(got["nle3"], ["N3", "N2"])
        self.assertEqual(got["ngt3"], ["N4"])
        self.assertEqual(got["statusNames"], ["FLAT"])
        self.assertEqual(
            got["allNames"],
            ["BIG", "THREE", "SMALL", "FLAT", "N3", "N2", "N4", "MISS", "BLANK", "NAN"],
        )
        self.assertEqual(got["on"], 0)
        self.assertTrue(got["hasGt"] and got["hasLe"] and got["hasFlat"] and got["hasNle"] and got["hasNgt"])
        self.assertFalse(got["outChange"])

    def test_dom_fallback_filters_when_builder_is_unknown(self) -> None:
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        jsdom_root = Path("/tmp/fd-jsdom")
        jsdom_mod = jsdom_root / "node_modules" / "jsdom"
        if not jsdom_mod.is_dir():
            npm = shutil.which("npm")
            if not npm:
                self.skipTest("npm not installed")
            jsdom_root.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                [npm, "install", "--prefix", str(jsdom_root), "jsdom@24"],
                check=True,
                capture_output=True,
                text=True,
                timeout=90,
            )
        live = """<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body>
<div id="view-mom-up">
  <h2>MOMENTUM UP</h2>
  <div class="filt-bar">
    <div class="fgrp"><span class="flab">SCORE</span><button type="button" class="fchip on">All</button><button type="button" class="fchip">5+</button></div>
    <div class="fgrp"><span class="flab">FLOWS</span><button type="button" class="fchip on">All</button><button type="button" class="fchip">OPT SPIKE</button><button type="button" class="fchip">Big flow</button></div>
    <div class="fgrp"><span class="flab">TAGS</span><button type="button" class="fchip on">All</button><button type="button" class="fchip">OUTLIER</button><button type="button" class="fchip">NEW</button></div>
    <div class="fgrp"><span class="flab">SORT</span><button type="button" class="fchip on">Score</button><button type="button" class="fchip">Name</button><button type="button" class="fchip">RS</button><button type="button" class="fchip">Opt</button></div>
  </div>
  <div class="grid dense" id="mom-up-grid">
    <article class="card"><h2>AMD</h2><span class="status">Strong momentum</span><span data-mom-score-d10="5">+5</span></article>
    <article class="card"><h2>MU</h2><span class="status">Constructive</span><span data-mom-score-d10="-2">−2</span></article>
    <article class="card"><h2>SOFT</h2><span class="status">Softening</span><span data-mom-score-d10="0">0</span></article>
  </div>
</div>
<div id="view-mom-down" class="hide">
  <h2>MOMENTUM DOWN</h2>
  <div class="filt-bar">
    <div class="fgrp"><span class="flab">SCORE</span><button type="button" class="fchip on">All</button><button type="button" class="fchip">5+</button></div>
    <div class="fgrp"><span class="flab">FLOWS</span><button type="button" class="fchip on">All</button><button type="button" class="fchip">OPT SPIKE</button><button type="button" class="fchip">Big flow</button></div>
    <div class="fgrp"><span class="flab">TAGS</span><button type="button" class="fchip on">All</button><button type="button" class="fchip">OUTLIER</button><button type="button" class="fchip">NEW</button></div>
    <div class="fgrp"><span class="flab">SORT</span><button type="button" class="fchip on">Score</button><button type="button" class="fchip">Name</button><button type="button" class="fchip">RS</button><button type="button" class="fchip">Opt</button></div>
  </div>
</div>
<script>function buildFiltBar(){return "";}</script>
</body></html>"""
        html = desk_dash.ensure_mom_status_filter(live)
        self.assertIn('id="fd-mom-status-js"', html)
        with tempfile.TemporaryDirectory() as tmp:
            page = Path(tmp) / "page.html"
            runner = Path(tmp) / "run.js"
            page.write_text(html, encoding="utf-8")
            runner.write_text(
                f"""
const {{ JSDOM }} = require({json.dumps(str(jsdom_mod))});
const fs = require("fs");
const dom = new JSDOM(fs.readFileSync({json.dumps(str(page))}, "utf8"), {{
  runScripts: "dangerously",
  url: "http://127.0.0.1/factorbook.html"
}});
function tick() {{
  return new Promise(function (resolve) {{ setTimeout(resolve, 30); }});
}}
(async function () {{
  const document = dom.window.document;
  await tick();
  const bar = document.querySelector("#view-mom-up .filt-bar");
  const kids = Array.from(bar.children).map(function (el) {{
    var lab = el.querySelector(".flab");
    return lab ? lab.textContent.trim() : "";
  }});
  const row = document.querySelector("#view-mom-up [data-fd-status-row]");
  const labels = row ? Array.from(row.querySelectorAll("[data-fd-status-val]")).map(function (b) {{
    return b.getAttribute("data-fd-status-val");
  }}) : [];
  function hidden() {{
    return Array.from(document.querySelectorAll("#view-mom-up article.card")).filter(function (el) {{
      return el.classList.contains("fd-status-hid");
    }}).map(function (el) {{ return (el.querySelector("h2").textContent || "").trim(); }});
  }}
  const strong = row.querySelector('[data-fd-status-val="Strong momentum"]');
  strong.click();
  const afterStrong = hidden();
  row.querySelector('[data-fd-status-val="all"]').click();
  const afterAll = hidden();
  const changeRow = document.querySelector("#view-mom-up [data-fd-change-row]");
  const changeLabels = changeRow ? Array.from(changeRow.querySelectorAll("[data-fd-change-val]")).map(function (b) {{
    return b.textContent.trim();
  }}) : [];
  changeRow.querySelector('[data-fd-change-val="gt3"]').click();
  function changeHidden() {{
    return Array.from(document.querySelectorAll("#view-mom-up article.card")).filter(function (el) {{
      return el.classList.contains("fd-change-hid");
    }}).map(function (el) {{ return (el.querySelector("h2").textContent || "").trim(); }});
  }}
  const afterUp = changeHidden();
  changeRow.querySelector('[data-fd-change-val="flat"]').click();
  const afterFlat = changeHidden();
  changeRow.querySelector('[data-fd-change-val="all"]').click();
  const afterChangeAll = changeHidden();
  document.getElementById("view-mom-up").classList.add("hide");
  document.getElementById("view-mom-down").classList.remove("hide");
  document.body.appendChild(document.createElement("i"));
  await tick();
  const downRow = document.querySelector("#view-mom-down [data-fd-status-row]");
  const downLabels = downRow ? Array.from(downRow.querySelectorAll("[data-fd-status-val]")).map(function (b) {{
    return b.getAttribute("data-fd-status-val");
  }}) : [];
  const report = {{ kids: kids, labels: labels, afterStrong: afterStrong, afterAll: afterAll, downLabels: downLabels, changeLabels: changeLabels, afterUp: afterUp, afterFlat: afterFlat, afterChangeAll: afterChangeAll }};
  process.stdout.write(JSON.stringify(report));
}})().catch(function (err) {{
  console.error(err);
  process.exit(1);
}});
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
        self.assertEqual(report["kids"], ["SCORE", "CHANGE", "FLOWS", "TAGS", "STATUS", "SORT"])
        self.assertEqual(report["changeLabels"], ["All", "+>3", "+\u22643", "Flat", "\u2212\u22643", "\u2212>3"])
        self.assertEqual(sorted(report["afterUp"]), ["MU", "SOFT"])
        self.assertEqual(report["afterFlat"], ["AMD", "MU"])
        self.assertEqual(report["afterChangeAll"], [])
        self.assertEqual(
            report["labels"],
            ["all", "Strong momentum", "Momentum building", "Constructive"],
        )
        self.assertEqual(sorted(report["afterStrong"]), ["MU", "SOFT"])
        self.assertEqual(report["afterAll"], [])
        self.assertEqual(report["downLabels"], ["all", "Weak / fading", "Softening"])


LEGACY_CHANGE_JS = r"""
var viewFilt = {
  "mom-up": { score: "all", flows: "all", tags: "all", sort: "Score", pills: {} },
  "mom-down": { score: "all", flows: "all", tags: "all", sort: "Score", pills: {} },
  "outliers": { score: "all", flows: "all", tags: "all", sort: "Score", pills: {} }
};
function grp(label, chips, key, cur) {
  var h = '<div class="fgrp"><span class="flab">' + label + '</span>';
  for (var i = 0; i < chips.length; i++) {
    var v = chips[i][0], lab = chips[i][1];
    var on = String(cur) === String(v) ? " on" : "";
    h += '<button type="button" class="fchip' + on + '" data-k="' + key + '" data-v="' + v + '">' + lab + '</button>';
  }
  return h + '</div>';
}
function mkGrp(label, chips, key, cur) { return grp(label, chips, key, cur); }
function applyMomFilters(cards, viewKey) {
  var vf = viewFilt[viewKey] || {};
  return (cards || []).filter(function (c) {
    if (vf.change === "up" && !(Number(c.mom_score_d10) > 0)) return false;
    if (vf.change === "down" && !(Number(c.mom_score_d10) < 0)) return false;
    if (vf.change === "flat" && Number(c.mom_score_d10) !== 0) return false;
    if (vf.score && vf.score !== "all") {
      var n = parseFloat(vf.score);
      if (isFinite(n) && !(Number(c.score) >= n)) return false;
    }
    return true;
  });
}
function buildFiltBar(viewKey) {
  var vf = viewFilt[viewKey] || {};
  var html = "";
  html += mkGrp("SCORE", [["all","All"],["5+","5+"],["6+","6+"],["7+","7+"],["8+","8+"]], "score", vf.score);
  html += grp("FLOWS", [["all","All"],["OPT SPIKE","OPT SPIKE"]], "flows", vf.flows);
  html += grp("TAGS", [["all","All"],["OUTLIER","OUTLIER"],["NEW","NEW"]], "tags", vf.tags);
  if (viewKey === "mom-up" || viewKey === "mom-down") {
    html += mkGrp("Change", [["all","All"],["up","Up (+)"],["down","Down (\u2212)"],["flat","Flat"]], "change", vf.change);
  }
  html += grp("SORT", [["Score","Score"],["Name","Name"],["RS","RS"],["Opt","Opt"]], "sort", vf.sort);
  return html;
}
function paintView(view) {
  window.__paintCount = (window.__paintCount || 0) + 1;
  window.__paintView = view;
  window.__paintChange = viewFilt[view] && viewFilt[view].change;
}
"""

BOTH_CHANGE_JS = r"""
var viewFilt = {
  "mom-up": { score: "all", flows: "all", tags: "all", sort: "Score", change: "all" },
  "mom-down": { score: "all", flows: "all", tags: "all", sort: "Score", change: "all" },
  "outliers": { score: "all", flows: "all", tags: "all", sort: "Score" }
};
function grp(label, chips, key, cur) {
  var h = '<div class="fgrp"><span class="flab">' + label + '</span>';
  for (var i = 0; i < chips.length; i++) {
    var v = chips[i][0], lab = chips[i][1];
    h += '<button type="button" class="fchip" data-k="' + key + '" data-v="' + v + '">' + lab + '</button>';
  }
  return h + '</div>';
}
function mkGrp(label, chips, key, cur) { return grp(label, chips, key, cur); }
function applyMomFilters(cards, viewKey) {
  var vf = viewFilt[viewKey] || {};
  return (cards || []).filter(function (c) { return true; });
}
function buildFiltBar(viewKey) {
  var vf = viewFilt[viewKey] || {};
  var html = "";
  html += grp("SCORE", [["all","All"],["5+","5+"]], "score", vf.score);
  if (viewKey === "mom-up" || viewKey === "mom-down") {
    html += grp("CHANGE", [["all","All"],["gt3","+>3"],["le3","+\u22643"],["flat","Flat"],["nle3","\u2212\u22643"],["ngt3","\u2212>3"]], "change", vf.change);
  }
  html += grp("FLOWS", [["all","All"]], "flows", vf.flows);
  html += grp("TAGS", [["all","All"],["OUTLIER","OUTLIER"],["NEW","NEW"]], "tags", vf.tags);
  html += mkGrp("Change", [["all","All"],["up","Up (+)"],["down","Down (\u2212)"],["flat","Flat"]], "change", vf.change);
  html += grp("SORT", [["Score","Score"],["Name","Name"]], "sort", vf.sort);
  return html;
}
function paintView(view) {}
"""


class LegacyChangeRowTests(unittest.TestCase):
    def test_legacy_mkgrp_becomes_one_granular_row(self) -> None:
        js = _patched_script(LEGACY_CHANGE_JS)
        build = js.split("function buildFiltBar", 1)[1].split("function paintView", 1)[0]
        self.assertNotIn("Up (+)", build)
        self.assertNotIn('"up"', build)
        self.assertNotIn('"down"', build)
        self.assertEqual(build.count('mkGrp("Change"') + build.count('grp("CHANGE"') + build.count('grp("Change"'), 1)
        self.assertIn('mkGrp("SCORE"', build)
        self.assertIn("gt3", build)
        self.assertIn("+>3", build)
        self.assertIn("nle3", build)
        self.assertIn("ngt3", build)
        self.assertIn('(viewKey==="mom-up"||viewKey==="mom-down")', build)
        apply = js.split("function applyMomFilters", 1)[1].split("function buildFiltBar", 1)[0]
        self.assertNotIn('.change === "up"', apply)
        self.assertNotIn('.change === "down"', apply)
        self.assertIn("fd-change-pred", apply)
        self.assertIn("__fdCh === \"gt3\"", apply)
        once = desk_dash.ensure_mom_status_filter(_page(LEGACY_CHANGE_JS))
        twice = desk_dash.ensure_mom_status_filter(once)
        once_build = once.split("function buildFiltBar", 1)[1].split("function paintView", 1)[0]
        twice_build = twice.split("function buildFiltBar", 1)[1].split("function paintView", 1)[0]
        self.assertNotIn("Up (+)", once_build)
        self.assertNotIn("Up (+)", twice_build)
        self.assertEqual(once_build.count('mkGrp("Change"'), twice_build.count('mkGrp("Change"'))
        self.assertEqual(once.count("fd-change-pred"), twice.count("fd-change-pred"))
        self.assertEqual(once.count("fd-change-click"), twice.count("fd-change-click"))
        self.assertEqual(once.count("fd-change-click"), 1)

    def test_already_injected_row_drops_legacy_sibling(self) -> None:
        js = _patched_script(BOTH_CHANGE_JS)
        build = js.split("function buildFiltBar", 1)[1].split("function paintView", 1)[0]
        self.assertNotIn("Up (+)", build)
        self.assertEqual(build.count('grp("CHANGE"'), 1)
        self.assertEqual(build.count('mkGrp("Change"'), 0)
        expr = r"""
(function () {
  var up = buildFiltBar("mom-up");
  var out = buildFiltBar("outliers");
  function labs(html) {
    var outLabs = [];
    var re = /class="flab">([^<]+)</g;
    var m;
    while ((m = re.exec(html))) outLabs.push(m[1]);
    return outLabs;
  }
  return { up: labs(up), out: labs(out), upPlus: up.indexOf("Up (+)") };
})()
"""
        # The placeholder above is invalid. Replaced below if this lands.
        got = _node_eval(js, expr)
        self.assertEqual(got["up"].count("CHANGE") + got["up"].count("Change"), 1)
        self.assertNotIn("Change", got["out"])
        self.assertNotIn("CHANGE", got["out"])
        self.assertEqual(got["upPlus"], -1)

    def test_legacy_buckets_filter_mom_score_d10(self) -> None:
        js = _patched_script(LEGACY_CHANGE_JS)
        expr = r"""
(function () {
  var cards = [
    {t:"BIG", score:9, mom_score_d10: 4},
    {t:"THREE", score:9, mom_score_d10: 3},
    {t:"SMALL", score:9, mom_score_d10: 1},
    {t:"FLAT", score:9, mom_score_d10: 0},
    {t:"N3", score:9, mom_score_d10: -3},
    {t:"N2", score:9, mom_score_d10: -2},
    {t:"N4", score:9, mom_score_d10: -4},
    {t:"MISS", score:9},
    {t:"BLANK", score:9, mom_score_d10: ""},
    {t:"NAN", score:9, mom_score_d10: "nope"}
  ];
  function names(v) {
    viewFilt["mom-up"].change = v;
    return applyMomFilters(cards, "mom-up").map(function (c) { return c.t; });
  }
  var upHtml = buildFiltBar("mom-up");
  var outHtml = buildFiltBar("outliers");
  var changeHtml = upHtml.slice(upHtml.indexOf(">Change<") >= 0 ? upHtml.indexOf(">Change<") : upHtml.indexOf(">CHANGE<"), upHtml.indexOf(">FLOWS<") >= 0 ? upHtml.indexOf(">FLOWS<") : upHtml.length);
  return {
    gt3: names("gt3"),
    le3: names("le3"),
    flat: names("flat"),
    nle3: names("nle3"),
    ngt3: names("ngt3"),
    labelGt: names("+>3"),
    labelFlat: names("Flat"),
    labelNgt: names("\u2212>3"),
    all: names("all"),
    up: names("up"),
    changeCount: (upHtml.match(/>Change<|>CHANGE</g) || []).length,
    outChange: outHtml.indexOf(">Change<") >= 0 || outHtml.indexOf(">CHANGE<") >= 0,
    upPlus: upHtml.indexOf("Up (+)")
  };
})()
"""
        got = _node_eval(js, expr)
        self.assertEqual(got["gt3"], ["BIG"])
        self.assertEqual(got["le3"], ["THREE", "SMALL"])
        self.assertEqual(got["flat"], ["FLAT"])
        self.assertEqual(got["nle3"], ["N3", "N2"])
        self.assertEqual(got["ngt3"], ["N4"])
        self.assertEqual(got["labelGt"], ["BIG"])
        self.assertEqual(got["labelFlat"], ["FLAT"])
        self.assertEqual(got["labelNgt"], ["N4"])
        self.assertEqual(
            got["all"],
            ["BIG", "THREE", "SMALL", "FLAT", "N3", "N2", "N4", "MISS", "BLANK", "NAN"],
        )
        self.assertEqual(got["up"], [])
        self.assertEqual(got["changeCount"], 1)
        self.assertFalse(got["outChange"])
        self.assertEqual(got["upPlus"], -1)

    def test_chip_click_sets_change_and_paints(self) -> None:
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        jsdom_root = Path("/tmp/fd-jsdom")
        jsdom_mod = jsdom_root / "node_modules" / "jsdom"
        if not jsdom_mod.is_dir():
            npm = shutil.which("npm")
            if not npm:
                self.skipTest("npm not installed")
            jsdom_root.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                [npm, "install", "--prefix", str(jsdom_root), "jsdom@24"],
                check=True,
                capture_output=True,
                text=True,
                timeout=90,
            )
        page_js = LEGACY_CHANGE_JS + r"""
var DATA = [
  {t:"BIG", mom_score_d10: 4},
  {t:"FLAT", mom_score_d10: 0},
  {t:"N4", mom_score_d10: -4},
  {t:"MISS"}
];
function render() {
  var cards = applyMomFilters(DATA, "mom-up");
  document.getElementById("grid").innerHTML = cards.map(function (c) {
    return '<article class="card"><h2>' + c.t + '</h2></article>';
  }).join("");
  document.getElementById("filt").innerHTML = buildFiltBar("mom-up");
}
paintView = function (view) {
  window.__paintCount = (window.__paintCount || 0) + 1;
  window.__paintView = view;
  window.__paintChange = viewFilt[view] && viewFilt[view].change;
  render();
};
document.addEventListener("click", function (ev) {
  var b = ev.target && ev.target.closest && ev.target.closest("[data-k]");
  if (!b) return;
  var k = b.getAttribute("data-k");
  if (k === "change") return;
  viewFilt["mom-up"][k] = b.getAttribute("data-v");
  paintView("mom-up");
});
"""
        live = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body>
<div id="view-mom-up">
  <h2>MOMENTUM UP</h2>
  <div id="filt">
    <div class="fgrp"><span class="flab">Change</span>
      <button type="button" class="fchip">All</button>
      <button type="button" class="fchip">Up (+)</button>
      <button type="button" class="fchip">Down (−)</button>
      <button type="button" class="fchip">Flat</button>
    </div>
  </div>
  <div id="grid"></div>
</div>
<div id="view-mom-down" class="hide"></div>
<script>
{page_js}
document.getElementById("filt").insertAdjacentHTML("beforeend", buildFiltBar("mom-up"));
</script>
</body></html>"""
        html = desk_dash.ensure_mom_status_filter(live)
        self.assertIn("fd-change-click", html)
        self.assertNotIn("Up (+)", html.split("function buildFiltBar", 1)[1].split("function paintView", 1)[0])
        with tempfile.TemporaryDirectory() as tmp:
            page = Path(tmp) / "page.html"
            runner = Path(tmp) / "run.js"
            page.write_text(html, encoding="utf-8")
            runner.write_text(
                f"""
const {{ JSDOM }} = require({json.dumps(str(jsdom_mod))});
const fs = require("fs");
const dom = new JSDOM(fs.readFileSync({json.dumps(str(page))}, "utf8"), {{
  runScripts: "dangerously",
  url: "http://127.0.0.1/factorbook.html"
}});
function tick() {{
  return new Promise(function (resolve) {{ setTimeout(resolve, 40); }});
}}
(async function () {{
  const document = dom.window.document;
  await tick();
  function labs() {{
    return Array.from(document.querySelectorAll("#filt .flab")).map(function (el) {{
      return (el.textContent || "").trim();
    }});
  }}
  function names() {{
    return Array.from(document.querySelectorAll("#grid h2")).map(function (el) {{
      return (el.textContent || "").trim();
    }});
  }}
  const before = labs();
  const flat = Array.from(document.querySelectorAll("#filt button")).find(function (b) {{
    return (b.textContent || "").trim() === "Flat";
  }});
  flat.click();
  const afterFlat = names();
  const flatFilt = dom.window.viewFilt["mom-up"].change;
  const gt = Array.from(document.querySelectorAll("#filt button")).find(function (b) {{
    return (b.textContent || "").trim() === "+>3";
  }});
  gt.click();
  const afterGt = names();
  const gtFilt = dom.window.viewFilt["mom-up"].change;
  const afterLabs = labs();
  const upPlus = (document.getElementById("filt").textContent || "").indexOf("Up (+)");
  process.stdout.write(JSON.stringify({{
    before: before,
    afterFlat: afterFlat,
    flatFilt: flatFilt,
    afterGt: afterGt,
    gtFilt: gtFilt,
    afterLabs: afterLabs,
    upPlus: upPlus,
    paints: dom.window.__paintCount || 0
  }}));
}})().catch(function (err) {{
  console.error(err);
  process.exit(1);
}});
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
        self.assertNotIn("Up (+)", "".join(report["before"]))
        self.assertEqual(report["before"].count("Change") + report["before"].count("CHANGE"), 1)
        self.assertEqual(report["afterFlat"], ["FLAT"])
        self.assertEqual(report["flatFilt"], "flat")
        self.assertEqual(report["afterGt"], ["BIG"])
        self.assertEqual(report["gtFilt"], "gt3")
        self.assertEqual(report["afterLabs"].count("Change") + report["afterLabs"].count("CHANGE"), 1)
        self.assertLess(report["upPlus"], 0)
        self.assertGreaterEqual(report["paints"], 2)


CHANGE_CONTRACT_JS = r"""
var viewFilt = {
  "mom-up": { score: "all", tags: "all", change: "all" },
  "mom-down": { score: "all", tags: "all", change: "all" }
};
var DATA = [
  {t:"+5", mom_score_d10: 5},
  {t:"+2", mom_score_d10: 2},
  {t:"0", mom_score_d10: 0},
  {t:"-2", mom_score_d10: -2},
  {t:"-5", mom_score_d10: -5},
  {t:"null", mom_score_d10: null}
];
function grp(label, chips, key, cur) {
  var h = '<div class="fgrp"><span class="flab">' + label + '</span>';
  for (var i = 0; i < chips.length; i++) {
    var v = chips[i][0], lab = chips[i][1];
    var on = String(cur) === String(v) ? " on" : "";
    h += '<button type="button" class="fchip' + on + '" data-k="' + key + '" data-v="' + v + '">' + lab + '</button>';
  }
  return h + '</div>';
}
function applyMomFilters(cards, viewKey) {
  var vf = viewFilt[viewKey] || {};
  return (cards || []).filter(function (c) {
    if (vf.score && vf.score !== "all") {
      var n = parseFloat(vf.score);
      if (isFinite(n) && !(Number(c.score) >= n)) return false;
    }
    return true;
  });
}
function buildFiltBar(viewKey) {
  var vf = viewFilt[viewKey] || {};
  var html = "";
  html += grp("SCORE", [["all","All"],["5+","5+"],["6+","6+"],["7+","7+"],["8+","8+"]], "score", vf.score);
  if (viewKey === "mom-up" || viewKey === "mom-down") {
    html += grp("Change", [["all","All"],["up","Up (+)"],["down","Down (\u2212)"],["flat","Flat"]], "change", vf.change);
  }
  return html;
}
function paintView(view) {
  window.__paintViews = window.__paintViews || [];
  window.__paintViews.push(view);
  window.__change = viewFilt[view] && viewFilt[view].change;
  window.__shown = applyMomFilters(DATA, view).map(function (c) { return c.t; });
}
"""


def _node_eval_prelude(prelude: str, js: str, expr: str) -> dict:
    node = shutil.which("node")
    if not node:
        raise unittest.SkipTest("node not installed")
    with tempfile.TemporaryDirectory() as tmp:
        pre = Path(tmp) / "pre.js"
        code = Path(tmp) / "filt.js"
        ask = Path(tmp) / "expr.js"
        runner = Path(tmp) / "run.js"
        pre.write_text(prelude, encoding="utf-8")
        code.write_text(js, encoding="utf-8")
        ask.write_text(expr, encoding="utf-8")
        runner.write_text(
            """
const vm = require("vm");
const fs = require("fs");
const sandbox = { console: console };
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), sandbox);
vm.runInContext(fs.readFileSync(process.argv[3], "utf8"), sandbox);
const result = vm.runInContext(fs.readFileSync(process.argv[4], "utf8"), sandbox);
process.stdout.write(JSON.stringify(result));
""",
            encoding="utf-8",
        )
        proc = subprocess.run(
            [node, str(runner), str(pre), str(code), str(ask)],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
        if proc.returncode != 0:
            raise AssertionError(proc.stderr or proc.stdout)
        return json.loads(proc.stdout)


class MomChangeFilterContractTests(unittest.TestCase):
    """Mom Change chips filter mom_score_d10 inside applyMomFilters."""

    def test_applyMomFilters_change_buckets_keep_known_deltas(self) -> None:
        js = _patched_script(FILT_JS)
        expr = r"""
(function () {
  var cards = [
    {t:"+5", mom_score_d10: 5},
    {t:"+2", mom_score_d10: 2},
    {t:"0", mom_score_d10: 0},
    {t:"-2", mom_score_d10: -2},
    {t:"-5", mom_score_d10: -5},
    {t:"null", mom_score_d10: null}
  ];
  function names(v) {
    viewFilt["mom-up"].change = v;
    return applyMomFilters(cards, "mom-up").map(function (c) { return c.t; });
  }
  return {
    gt3: names("gt3"),
    labelGt: names("+>3"),
    le3: names("le3"),
    flat: names("flat"),
    nle3: names("nle3"),
    ngt3: names("ngt3"),
    all: names("all")
  };
})()
"""
        got = _node_eval(js, expr)
        self.assertEqual(got["gt3"], ["+5"])
        self.assertEqual(got["labelGt"], ["+5"])
        self.assertEqual(got["le3"], ["+2"])
        self.assertEqual(got["flat"], ["0"])
        self.assertEqual(got["nle3"], ["-2"])
        self.assertEqual(got["ngt3"], ["-5"])
        self.assertEqual(got["all"], ["+5", "+2", "0", "-2", "-5", "null"])
        for bucket in ("gt3", "le3", "flat", "nle3", "ngt3"):
            self.assertNotIn("null", got[bucket])

    def test_buildFiltBar_has_exactly_one_change_group(self) -> None:
        expr = r"""
(function () {
  function labs(html) {
    var out = [];
    var re = /class="flab">([^<]+)</g;
    var m;
    while ((m = re.exec(html))) out.push(m[1]);
    return out;
  }
  var up = buildFiltBar("mom-up");
  var down = buildFiltBar("mom-down");
  var outliers = buildFiltBar("outliers");
  return {
    up: labs(up),
    down: labs(down),
    outliers: labs(outliers),
    upPlus: up.indexOf("Up (+)"),
    downWord: up.indexOf("Down")
  };
})()
"""
        clean = _node_eval(_patched_script(FILT_JS), expr)
        legacy = _node_eval(_patched_script(LEGACY_CHANGE_JS), expr)
        for got in (clean, legacy):
            self.assertEqual(got["up"].count("CHANGE") + got["up"].count("Change"), 1)
            self.assertEqual(got["down"].count("CHANGE") + got["down"].count("Change"), 1)
            self.assertEqual(got["outliers"].count("CHANGE") + got["outliers"].count("Change"), 0)
            self.assertEqual(got["upPlus"], -1)
            self.assertEqual(got["downWord"], -1)
        up_html = _node_eval(
            _patched_script(FILT_JS),
            'buildFiltBar("mom-up")',
        )
        self.assertIsInstance(up_html, str)
        for label in ("+>3", "+\u22643", "Flat", "\u2212\u22643", "\u2212>3"):
            self.assertIn(label, up_html)

    def test_change_chip_click_sets_viewfilt_and_calls_paintView(self) -> None:
        js = _patched_script(CHANGE_CONTRACT_JS)
        self.assertEqual(js.count("fd-change-click"), 1)
        self.assertIn("viewFilt[view].change = val", js)
        self.assertIn("paintView(view)", js)
        self.assertNotIn("fd-change-hid", js.split("/* fd-change-click */", 1)[1])
        prelude = r"""
var window = {};
var document = {
  _handlers: [],
  getElementById: function (id) {
    if (id === "view-mom-up") return { id: id, classList: { contains: function () { return false; } }, hasAttribute: function () { return false; } };
    if (id === "view-mom-down") return { id: id, classList: { contains: function (c) { return c === "hide" || c === "hidden"; } }, hasAttribute: function () { return false; } };
    return null;
  },
  addEventListener: function (_type, fn) { document._handlers.push(fn); }
};
"""
        expr = r"""
(function () {
  var chip = {
    getAttribute: function (k) {
      if (k === "data-v") return "+>3";
      if (k === "data-k") return "change";
      return "";
    },
    closest: function (sel) {
      if (String(sel).indexOf("view-mom-up") >= 0) return { id: "view-mom-up" };
      return null;
    }
  };
  var ev = {
    target: {
      closest: function (sel) {
        if (String(sel).indexOf("data-k") >= 0) return chip;
        return null;
      }
    }
  };
  if (!document._handlers.length) return { error: "no click handler" };
  document._handlers[0](ev);
  var afterGt = { change: viewFilt["mom-up"].change, shown: window.__shown, paints: (window.__paintViews || []).slice() };
  chip.getAttribute = function (k) {
    if (k === "data-v") return "flat";
    if (k === "data-k") return "change";
    return "";
  };
  ev.__fdChangeClick = 0;
  document._handlers[0](ev);
  return { afterGt: afterGt, afterFlat: { change: viewFilt["mom-up"].change, shown: window.__shown, paints: window.__paintViews } };
})()
"""
        got = _node_eval_prelude(prelude, js, expr)
        self.assertEqual(got["afterGt"]["change"], "gt3")
        self.assertEqual(got["afterGt"]["shown"], ["+5"])
        self.assertEqual(got["afterGt"]["paints"], ["mom-up"])
        self.assertEqual(got["afterFlat"]["change"], "flat")
        self.assertEqual(got["afterFlat"]["shown"], ["0"])
        self.assertEqual(got["afterFlat"]["paints"], ["mom-up", "mom-up"])

    def test_mkgrp_change_omits_value_map_and_click_sets_gt3(self) -> None:
        """Live mkGrp(label, items, key, map) must not receive vf.change as map."""
        js = _patched_script(r"""
var viewFilt = {
  "mom-up": { score: "all", tags: "all", change: "all" },
  "mom-down": { score: "all", tags: "all", change: "all" }
};
var DATA = [
  {t:"+5", mom_score_d10: 5},
  {t:"0", mom_score_d10: 0},
  {t:"null", mom_score_d10: null}
];
var __changeMap;
function mkGrp(label, items, key, map) {
  if (key === "change") __changeMap = map;
  var h = '<div class="fgrp"><span class="flab">' + label + '</span>';
  for (var i = 0; i < items.length; i++) {
    h += '<button type="button" class="fchip" data-fk="' + key + '" data-fv="' + items[i][0] + '">' + items[i][1] + '</button>';
  }
  return h + '</div>';
}
function applyMomFilters(cards, viewKey) {
  var vf = viewFilt[viewKey] || {};
  return (cards || []).filter(function (c) { return true; });
}
function buildFiltBar(view) {
  var vf = viewFilt[view] || {};
  var host = { html: "" };
  host.appendChild = function (node) { host.html += node; };
  host.appendChild(mkGrp("SCORE", [["all","All"],["5+","5+"]], "score", function (v) { return +v; }));
  if (view === "mom-up" || view === "mom-down") {
    host.appendChild(mkGrp("Change", [["all","All"],["up","Up (+)"],["down","Down (\u2212)"],["flat","Flat"]], "change", vf.change));
  }
  return host.html;
}
function paintView(view) {
  __shown = applyMomFilters(DATA, view).map(function (c) { return c.t; });
}
""")
        build = js.split("function buildFiltBar", 1)[1].split("function paintView", 1)[0]
        calls = desk_dash._find_all_label_calls(build, ("CHANGE", "Change"))
        self.assertEqual(len(calls), 1)
        _start, _end, _name, args = calls[0]
        self.assertLessEqual(len(args), 4)
        self.assertGreaterEqual(len(args), 3)
        if len(args) == 4:
            fourth = build[args[3][0] : args[3][1]]
            self.assertTrue(
                desk_dash._is_js_function_expr(fourth),
                fourth,
            )
        self.assertNotRegex(build, r"""['"]change['"]\s*,\s*[A-Za-z_$][\w$]*\.change\b""")
        expr = r"""
(function () {
  var threw = false;
  var err = "";
  try {
    buildFiltBar("mom-up");
    var map = __changeMap;
    var val = "gt3";
    viewFilt["mom-up"].change = map ? map(val) : val;
    paintView("mom-up");
  } catch (e) {
    threw = true;
    err = String(e && e.message || e);
  }
  return {
    threw: threw,
    err: err,
    mapType: typeof __changeMap,
    change: viewFilt["mom-up"].change,
    shown: (typeof __shown === "undefined") ? null : __shown
  };
})()
"""
        got = _node_eval(js, expr)
        self.assertFalse(got["threw"], got["err"])
        self.assertEqual(got["mapType"], "undefined")
        self.assertEqual(got["change"], "gt3")
        self.assertEqual(got["shown"], ["+5"])
        prelude = r"""
var window = {};
var document = {
  _handlers: [],
  getElementById: function (id) {
    if (id === "view-mom-up") return { id: id, classList: { contains: function () { return false; } }, hasAttribute: function () { return false; } };
    if (id === "view-mom-down") return { id: id, classList: { contains: function (c) { return c === "hide"; } }, hasAttribute: function () { return false; } };
    return null;
  },
  addEventListener: function (_type, fn) { document._handlers.push(fn); }
};
"""
        click_expr = r"""
(function () {
  var chip = {
    getAttribute: function (k) {
      if (k === "data-fv") return "gt3";
      if (k === "data-fk") return "change";
      return null;
    },
    closest: function (sel) {
      if (String(sel).indexOf("view-mom-up") >= 0) return { id: "view-mom-up" };
      return null;
    }
  };
  var ev = {
    target: {
      closest: function (sel) {
        var s = String(sel);
        if (s.indexOf("data-fk") >= 0 || s.indexOf("data-k") >= 0) return chip;
        return null;
      }
    }
  };
  document._handlers[0](ev);
  return { change: viewFilt["mom-up"].change, paints: window.__paintViews || null, shown: (typeof __shown === "undefined") ? null : __shown };
})()
"""
        clicked = _node_eval_prelude(prelude, js, click_expr)
        self.assertEqual(clicked["change"], "gt3")
        self.assertEqual(clicked["shown"], ["+5"])

    def test_already_patched_change_mkgrp_strips_vf_change_map(self) -> None:
        """A live 4-arg mkGrp("CHANGE", chips, "change", vf.change) loses the map on re-run."""
        page = r"""<!DOCTYPE html><html><body><script>
var viewFilt = {
  "mom-up": { score: "all", tags: "all", change: "all" },
  "mom-down": { score: "all", tags: "all", change: "all" }
};
function mkGrp(label, items, key, map) { return ""; }
function applyMomFilters(cards, viewKey) {
  var vf = viewFilt[viewKey] || {};
  return (cards || []).filter(function (c) { return true; });
}
function buildFiltBar(view) {
  var vf = viewFilt[view] || {};
  var host = { appendChild: function () {} };
  host.appendChild(mkGrp("SCORE", [["all","All"],["5+","5+"]], "score", function (v) { return +v; }));
  if (view === "mom-up" || view === "mom-down") {
    host.appendChild(mkGrp("CHANGE",[["all","All"],["gt3","+>3"],["le3","+\u22643"],["flat","Flat"],["nle3","\u2212\u22643"],["ngt3","\u2212>3"]],"change",vf.change));
  }
}
function paintView(view) {}
</script></body></html>"""
        out = desk_dash.ensure_mom_status_filter(page)
        build = out.split("function buildFiltBar", 1)[1].split("function paintView", 1)[0]
        self.assertIn('mkGrp("CHANGE",', build)
        self.assertNotIn(",vf.change)", build)
        self.assertNotIn(", vf.change)", build)
        self.assertNotIn(",{vf}.change)", build)
        self.assertRegex(build, r'mkGrp\("CHANGE",\[\["all","All"\].*\],"change"\)')
        inject = Path(desk_dash.__file__).read_text(encoding="utf-8")
        inject = inject.split("def _inject_granular_change", 1)[1].split("\ndef ", 1)[0]
        self.assertNotIn("{vf}.change", inject)
        self.assertIn('("CHANGE",{_CHANGE_CHIPS},"change")', inject)

    def test_css_hide_does_not_blank_score_d10_cards(self) -> None:
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        jsdom_root = Path("/tmp/fd-jsdom")
        jsdom_mod = jsdom_root / "node_modules" / "jsdom"
        if not jsdom_mod.is_dir():
            npm = shutil.which("npm")
            if not npm:
                self.skipTest("npm not installed")
            jsdom_root.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                [npm, "install", "--prefix", str(jsdom_root), "jsdom@24"],
                check=True,
                capture_output=True,
                text=True,
                timeout=90,
            )
        live = """<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body>
<div id="view-mom-up">
  <h2>MOMENTUM UP</h2>
  <div id="filt">
    <div class="fgrp"><span class="flab">Change</span>
      <button type="button" class="fchip">All</button>
      <button type="button" class="fchip">Up (+)</button>
      <button type="button" class="fchip">Down (−)</button>
      <button type="button" class="fchip">Flat</button>
    </div>
  </div>
  <div id="grid">
    <article class="card"><h2>+5</h2><span class="score-d10">+5</span></article>
    <article class="card"><h2>+2</h2><span class="score-d10">+2</span></article>
    <article class="card"><h2>0</h2><span class="score-d10">0</span></article>
    <article class="card"><h2>-2</h2><span class="score-d10">−2</span></article>
    <article class="card"><h2>-5</h2><span class="score-d10">−5</span></article>
    <article class="card"><h2>null</h2></article>
  </div>
</div>
<div id="view-mom-down" class="hide"></div>
<script>
""" + CHANGE_CONTRACT_JS + """
var _recordPaint = paintView;
paintView = function (view) {
  _recordPaint(view);
  var cards = applyMomFilters(DATA, view);
  document.getElementById("grid").innerHTML = cards.map(function (c) {
    var cap = c.mom_score_d10 == null ? "" : '<span class="score-d10">' + c.t + '</span>';
    return '<article class="card"><h2>' + c.t + '</h2>' + cap + '</article>';
  }).join("");
};
document.getElementById("filt").insertAdjacentHTML("beforeend", buildFiltBar("mom-up"));
</script>
</body></html>"""
        html = desk_dash.ensure_mom_status_filter(live)
        with tempfile.TemporaryDirectory() as tmp:
            page = Path(tmp) / "page.html"
            runner = Path(tmp) / "run.js"
            page.write_text(html, encoding="utf-8")
            runner.write_text(
                f"""
const {{ JSDOM }} = require({json.dumps(str(jsdom_mod))});
const fs = require("fs");
const dom = new JSDOM(fs.readFileSync({json.dumps(str(page))}, "utf8"), {{
  runScripts: "dangerously",
  url: "http://127.0.0.1/factorbook.html"
}});
function tick() {{
  return new Promise(function (resolve) {{ setTimeout(resolve, 40); }});
}}
(async function () {{
  const document = dom.window.document;
  await tick();
  function changeLabs() {{
    return Array.from(document.querySelectorAll("#filt .flab")).map(function (el) {{
      return (el.textContent || "").trim();
    }}).filter(function (t) {{ return t === "Change" || t === "CHANGE"; }});
  }}
  const before = changeLabs();
  const upPlus = (document.getElementById("filt").textContent || "").indexOf("Up (+)");
  const gt = Array.from(document.querySelectorAll("#filt button")).find(function (b) {{
    return (b.textContent || "").trim() === "+>3";
  }});
  gt.click();
  await tick();
  const names = Array.from(document.querySelectorAll("#grid h2")).map(function (el) {{
    return (el.textContent || "").trim();
  }});
  const hidden = Array.from(document.querySelectorAll("#grid .fd-change-hid")).map(function (el) {{
    var h = el.querySelector("h2");
    return h ? h.textContent.trim() : "";
  }});
  process.stdout.write(JSON.stringify({{
    before: before,
    upPlus: upPlus,
    names: names,
    hidden: hidden,
    change: dom.window.viewFilt["mom-up"].change,
    paints: dom.window.__paintViews || []
  }}));
}})().catch(function (err) {{
  console.error(err);
  process.exit(1);
}});
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
        self.assertEqual(report["before"], ["Change"])
        self.assertLess(report["upPlus"], 0)
        self.assertEqual(report["change"], "gt3")
        self.assertEqual(report["paints"], ["mom-up"])
        self.assertEqual(report["names"], ["+5"])
        self.assertEqual(report["hidden"], [])

    def test_mom_down_change_chips_filter_trending_down(self) -> None:
        """Down Change chips write viewFilt['mom-down'] even when Up is also shown."""
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        jsdom_root = Path("/tmp/fd-jsdom")
        jsdom_mod = jsdom_root / "node_modules" / "jsdom"
        if not jsdom_mod.is_dir():
            npm = shutil.which("npm")
            if not npm:
                self.skipTest("npm not installed")
            jsdom_root.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                [npm, "install", "--prefix", str(jsdom_root), "jsdom@24"],
                check=True,
                capture_output=True,
                text=True,
                timeout=90,
            )
        live = r"""<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body>
<div id="view-mom-up"><h2>MOMENTUM UP</h2><div id="mom-up-grid" class="grid"></div></div>
<div id="view-mom-down"><h2>MOMENTUM DOWN</h2><div id="mom-down-grid" class="grid"></div></div>
<div id="mom-down-filt"></div>
<div id="mom-up-filt"></div>
<script>
/* Strong momentum Momentum building Constructive Weak / fading Softening fd-status-pred */
var viewFilt = {
  "mom-up": { score: "all", tags: "all", change: "all" },
  "mom-down": { score: "all", tags: "all", change: "all" }
};
var DATA = [
  {name:"FLAT", trend:"Trending down", mom_score_d10: 0},
  {name:"N2", trend:"Trending down", mom_score_d10: -2},
  {name:"N5", trend:"Trending down", mom_score_d10: -5},
  {name:"P2", trend:"Trending down", mom_score_d10: 2},
  {name:"UPFLAT", trend:"Trending up", mom_score_d10: 0},
  {name:"MISS", trend:"Trending down", mom_score_d10: null}
];
function applyMomFilters(cards, vf, trend) {
  return (cards || []).filter(function (c) {
    if (trend && c.trend !== trend) return false;
    if (vf && vf.score && vf.score !== "all") {
      var n = parseFloat(vf.score);
      if (isFinite(n) && !(Number(c.score) >= n)) return false;
    }
    if (vf && vf.tags && vf.tags !== "all" && c.tag !== vf.tags) return false;
    return true;
  });
}
function buildFiltBar(view) {
  var vf = viewFilt[view] || {};
  function mkGrp(label, items, key, map) {
    var g = document.createElement("div");
    g.className = "fgrp";
    var lab = document.createElement("span");
    lab.className = "flab";
    lab.textContent = label;
    g.appendChild(lab);
    for (var i = 0; i < items.length; i++) {
      (function (val, text) {
        var b = document.createElement("button");
        b.type = "button";
        b.className = "fchip";
        b.setAttribute("data-fk", key);
        b.setAttribute("data-fv", val);
        b.textContent = text;
        b.onclick = function () {
          vf[key] = (typeof map === "function") ? map(val) : val;
          paintView(view);
        };
        g.appendChild(b);
      })(items[i][0], items[i][1]);
    }
    return g;
  }
  var host = document.getElementById(view === "mom-down" ? "mom-down-filt" : "mom-up-filt");
  while (host.firstChild) host.removeChild(host.firstChild);
  host.appendChild(mkGrp("SCORE", [["all","All"],["5+","5+"]], "score", function (v) { return +v; }));
  if (view === "mom-up" || view === "mom-down") {
    host.appendChild(mkGrp("CHANGE", [["all","All"],["gt3","+>3"],["le3","+\u22643"],["flat","Flat"],["nle3","\u2212\u22643"],["ngt3","\u2212>3"]], "change"));
  }
}
function paintView(view) {
  window.__views = window.__views || [];
  window.__views.push(view);
  var trend = view === "mom-down" ? "Trending down" : "Trending up";
  var cards = applyMomFilters(DATA, viewFilt[view], trend);
  var grid = document.getElementById(view === "mom-down" ? "mom-down-grid" : "mom-up-grid");
  grid.innerHTML = (cards || []).map(function (c) {
    return '<article class="card"><h2>' + c.name + '</h2></article>';
  }).join("");
}
buildFiltBar("mom-down");
buildFiltBar("mom-up");
paintView("mom-down");
paintView("mom-up");
</script>
</body></html>"""
        html = desk_dash.ensure_mom_status_filter(live)
        click_js = html.split("function __fdChangeView", 1)[1].split("function __fdChangeEmpty", 1)[0]
        self.assertIn("#mom-down-filt", click_js)
        self.assertNotIn("viewOf()", click_js)
        fallback = html.split('id="fd-mom-status-js"', 1)[1]
        view_fn = fallback.split("function viewFromEl", 1)[1].split("function paintChange", 1)[0]
        self.assertIn('filt.id === "mom-down-filt"', view_fn)
        self.assertNotIn("viewOf()", view_fn)
        with tempfile.TemporaryDirectory() as tmp:
            page = Path(tmp) / "page.html"
            runner = Path(tmp) / "run.js"
            page.write_text(html, encoding="utf-8")
            runner.write_text(
                f"""
const {{ JSDOM }} = require({json.dumps(str(jsdom_mod))});
const fs = require("fs");
const dom = new JSDOM(fs.readFileSync({json.dumps(str(page))}, "utf8"), {{
  runScripts: "dangerously",
  url: "http://127.0.0.1/factorbook.html"
}});
function tick() {{
  return new Promise(function (resolve) {{ setTimeout(resolve, 40); }});
}}
function names(list) {{
  return (list || []).map(function (c) {{ return c.name; }});
}}
function gridNames(id) {{
  return Array.from(dom.window.document.querySelectorAll("#" + id + " h2")).map(function (el) {{
    return (el.textContent || "").trim();
  }});
}}
(async function () {{
  const document = dom.window.document;
  await tick();
  const downFilt = document.getElementById("mom-down-filt");
  const downView = document.getElementById("view-mom-down");
  const flat = downFilt.querySelector("[data-fk='change'][data-fv='flat']");
  const ngt3 = downFilt.querySelector("[data-fk='change'][data-fv='ngt3']");
  const gt3 = downFilt.querySelector("[data-fk='change'][data-fv='gt3']");
  const fb = document.getElementById("fd-mom-status-js").textContent;
  const start = fb.indexOf("function viewFromEl");
  const end = fb.indexOf("function paintChange");
  dom.window.eval(fb.slice(start, end) + "\\nwindow.__viewFromEl = viewFromEl;\\n");
  const upChip = document.querySelector("#mom-up-filt [data-fk='change'][data-fv='flat']");
  function snap(filtered) {{
    return {{
      down: dom.window.viewFilt["mom-down"].change,
      up: dom.window.viewFilt["mom-up"].change,
      filtered: filtered,
      grid: gridNames("mom-down-grid"),
      views: dom.window.__views.slice()
    }};
  }}
  dom.window.__views = [];
  flat.click();
  const afterFlat = snap(names(dom.window.applyMomFilters(dom.window.DATA, dom.window.viewFilt["mom-down"], "Trending down")));
  dom.window.viewFilt["mom-down"].change = "all";
  dom.window.viewFilt["mom-up"].change = "all";
  dom.window.__views = [];
  ngt3.onclick();
  const afterNgt = snap(names(dom.window.applyMomFilters(dom.window.DATA, dom.window.viewFilt["mom-down"], "Trending down")));
  dom.window.__views = [];
  gt3.click();
  const empty = document.querySelector("#mom-down-grid .empty");
  const upEmpty = document.querySelector("#mom-up-grid [data-fd-change-empty]");
  const afterGt = snap(names(dom.window.applyMomFilters(dom.window.DATA, dom.window.viewFilt["mom-down"], "Trending down")));
  afterGt.cards = document.querySelectorAll("#mom-down-grid article, #mom-down-grid .card").length;
  afterGt.empty = empty ? (empty.textContent || "").trim() : "";
  afterGt.emptyFlag = empty ? empty.getAttribute("data-fd-change-empty") : "";
  afterGt.upEmpty = !!upEmpty;
  afterGt.upGrid = gridNames("mom-up-grid");
  process.stdout.write(JSON.stringify({{
    filtOutsideView: !downView.contains(downFilt),
    viewFromEl: dom.window.__viewFromEl(flat),
    pageView: dom.window.__fdChangeView(flat),
    upViewFromEl: dom.window.__viewFromEl(upChip),
    changeGroups: Array.from(downFilt.querySelectorAll(".flab")).map(function (el) {{ return (el.textContent || "").trim(); }}),
    afterFlat: afterFlat,
    afterNgt: afterNgt,
    afterGt: afterGt
  }}));
}})().catch(function (err) {{
  console.error(err);
  process.exit(1);
}});
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
        self.assertTrue(report["filtOutsideView"])
        self.assertEqual(report["viewFromEl"], "mom-down")
        self.assertEqual(report["pageView"], "mom-down")
        self.assertEqual(report["upViewFromEl"], "mom-up")
        self.assertEqual(report["afterFlat"]["down"], "flat")
        self.assertEqual(report["afterFlat"]["up"], "all")
        self.assertEqual(report["afterFlat"]["filtered"], ["FLAT"])
        self.assertEqual(report["afterFlat"]["grid"], ["FLAT"])
        self.assertTrue(report["afterFlat"]["views"])
        self.assertNotIn("mom-up", report["afterFlat"]["views"])
        self.assertEqual(report["afterNgt"]["down"], "ngt3")
        self.assertEqual(report["afterNgt"]["up"], "all")
        self.assertEqual(report["afterNgt"]["filtered"], ["N5"])
        self.assertEqual(report["afterNgt"]["grid"], ["N5"])
        self.assertEqual(report["afterNgt"]["views"], ["mom-down"])
        self.assertEqual(report["afterGt"]["down"], "gt3")
        self.assertEqual(report["afterGt"]["up"], "all")
        self.assertEqual(report["afterGt"]["filtered"], [])
        self.assertEqual(report["afterGt"]["cards"], 0)
        self.assertEqual(report["afterGt"]["empty"], "No names match this filter.")
        self.assertEqual(report["afterGt"]["emptyFlag"], "1")
        self.assertFalse(report["afterGt"]["upEmpty"])
        self.assertEqual(report["afterGt"]["upGrid"], ["UPFLAT"])


if __name__ == "__main__":
    unittest.main()
