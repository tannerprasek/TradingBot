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
        self.assertEqual(got["on"], 1)
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


if __name__ == "__main__":
    unittest.main()
