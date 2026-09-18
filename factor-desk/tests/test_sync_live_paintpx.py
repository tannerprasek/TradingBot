"""Desktop sync_live_paintpx: inject wrap, never emit skinny desk_dash HTML."""

from __future__ import annotations

import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import chart_marks as cm  # noqa: E402
import sync_live_paintpx as sync  # noqa: E402


LIVE_BODY = """<!DOCTYPE html>
<html><head><title>Factor Desk</title></head>
<body>
<nav>
  <button id="refresh">Refresh</button>
  <button>Momentum Up</button>
  <button>Momentum Down</button>
  <button>Outliers</button>
  <button>Options</button>
  <button>Breakout</button>
  <button>Breakdown</button>
  <button>Paper</button>
  <button>Experimental</button>
</nav>
<section id="detail-chart" data-px-chart="1">
  <div>
    <div><span>1W</span><b>+1.69%</b></div>
    <div><span>1M</span><b>+1.95%</b></div>
    <div><span>YTD</span><b>-28.08%</b></div>
    <div><span>Trend</span><b>+0.5</b></div>
  </div>
  <script type="application/json" data-px-json>{"px":[10,11,12],"s50":[9,10,11],"s200":[8,9,10]}</script>
  <svg data-px-svg viewBox="0 0 760 220" width="760" height="220"></svg>
  <div data-px-leg>Price SMA20 SMA50 SMA200 52w high</div>
</section>
<script type="application/json" id="fd-paper-marks">{"CNH":13.63,"PWR":629.65}</script>
<script type="application/json" id="fd-chart-db">{"AAPL":{"start":"2026-09-02","label":"↑4d>5"}}</script>
<script>
function paintPxChart(wrap){
  var W=760, H=220, padR=8, padL=44;
  var svg = wrap.querySelector('[data-px-svg]');
  var S = JSON.parse(wrap.querySelector('[data-px-json]').textContent);
  function pathFrom(arr){
    var d='';
    for (var i=0;i<arr.length;i++) d += (d?'L':'M')+(padL+i)+','+(100-arr[i]);
    return d;
  }
  svg.innerHTML = '<path stroke="#e6edf3" fill="none" d="'+pathFrom(S.px)+'"/>';
}
</script>
</body></html>"""


def _pad(body: str, nbytes: int) -> str:
    pad = nbytes - len(body.encode("utf-8"))
    return body + ("<!--" + ("P" * max(pad, 1)) + "-->")


class EnsureEmbeddedNoneTests(unittest.TestCase):
    def test_none_keeps_existing_chart_db(self) -> None:
        html = (
            "<html><head></head><body>"
            '<script type="application/json" id="fd-chart-db">{"KEEP":1}</script>'
            "</body></html>"
        )
        out = cm.ensure_embedded(html, None)
        self.assertIn('{"KEEP":1}', out)
        self.assertIn("wrapPaintPxChart", out)
        self.assertIn("fd-chart-marks-js", out)
        self.assertEqual(out.count('id="fd-chart-db"'), 1)
        self.assertEqual(out.count('id="fd-chart-marks-js"'), 1)

    def test_inject_paintpx_is_ensure_embedded_none(self) -> None:
        html = "<html><body>paintPxChart</body></html>"
        self.assertEqual(cm.inject_paintpx(html), cm.ensure_embedded(html, None))


class SyncLivePaintPxTests(unittest.TestCase):
    def test_does_not_import_desk_dash(self) -> None:
        src = Path(sync.__file__).read_text(encoding="utf-8")
        self.assertNotRegex(src, r"(?m)^\s*(import|from)\s+desk_dash\b")
        self.assertNotRegex(src, r"(?m)^\s*(import|from)\s+write_dash\b")
        mod = sys.modules.get("sync_live_paintpx")
        self.assertIsNotNone(mod)
        self.assertNotIn("desk_dash", mod.__dict__)
        self.assertNotIn("write_dash", mod.__dict__)

    def test_refuse_html_under_1mb(self) -> None:
        small = _pad(LIVE_BODY, 50_000)
        with self.assertRaises(sync.SyncRefused) as ctx:
            sync.validate_live_html(small)
        self.assertIn("< 1000000", str(ctx.exception).replace(",", ""))
        self.assertIn("refuse", str(ctx.exception).lower())

    def test_refuse_missing_paintpx(self) -> None:
        html = _pad("<html><body><h1>Factor Desk</h1></body></html>", 1_100_000)
        with self.assertRaises(sync.SyncRefused) as ctx:
            sync.validate_live_html(html)
        self.assertIn("paintPxChart", str(ctx.exception))

    def test_patch_injects_wrap_and_keeps_tabs_and_marks(self) -> None:
        live = _pad(LIVE_BODY, 1_100_000)
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "factorbook.html"
            dest.write_text(live, encoding="utf-8")
            info = sync.patch_live_html(dest)
            text = dest.read_text(encoding="utf-8")
            self.assertTrue(info["wrote"])
            self.assertGreater(info["bytes_after"], 1_000_000)
            self.assertGreaterEqual(info["bytes_after"], info["bytes_before"])
            self.assertIn("function paintPxChart", text)
            self.assertIn("var W=760, H=220, padR=8", text)
            self.assertIn("wrapPaintPxChart", text)
            self.assertIn("restylePxChart", text)
            self.assertIn("alignFlags", text)
            self.assertIn("if (drew) hidePricePath", text)
            self.assertIn("__FD_NO_PIXEL_MA__", text)
            self.assertIn("isDetailChart(chart)", text)
            self.assertIn("xMidYMid meet", text)
            self.assertIn("close > s50 && close > s200", text)
            self.assertIn('id="fd-chart-marks-js"', text)
            self.assertIn(">Paper</button>", text)
            self.assertIn(">Experimental</button>", text)
            self.assertIn(">Breakout</button>", text)
            self.assertIn('id="fd-paper-marks"', text)
            self.assertIn('{"CNH":13.63,"PWR":629.65}', text)
            self.assertIn('{"AAPL":{"start":"2026-09-02","label":"↑4d>5"}}', text)
            self.assertIn("data-px-svg", text)
            self.assertIn("data-px-json", text)
            bak = dest.with_name("factorbook.html.bak-paintpx")
            self.assertTrue(bak.is_file())
            self.assertEqual(bak.read_text(encoding="utf-8"), live)

    def test_dry_run_does_not_write(self) -> None:
        live = _pad(LIVE_BODY, 1_100_000)
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "factorbook.html"
            dest.write_text(live, encoding="utf-8")
            info = sync.patch_live_html(dest, dry_run=True)
            self.assertFalse(info["wrote"])
            self.assertEqual(dest.read_text(encoding="utf-8"), live)

    def test_cli_refuse_under_1mb(self) -> None:
        small = _pad(LIVE_BODY, 80_000)
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "factorbook.html"
            dest.write_text(small, encoding="utf-8")
            buf, err = StringIO(), StringIO()
            with redirect_stdout(buf), redirect_stderr(err):
                rc = sync.main(["--html", str(dest)])
            self.assertEqual(rc, 2)
            self.assertEqual(dest.read_text(encoding="utf-8"), small)
            self.assertIn("refuse", err.getvalue().lower())

    def test_cli_patches_live_file(self) -> None:
        live = _pad(LIVE_BODY, 1_100_000)
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "factorbook.html"
            dest.write_text(live, encoding="utf-8")
            buf, err = StringIO(), StringIO()
            with redirect_stdout(buf), redirect_stderr(err):
                rc = sync.main(["--html", str(dest)])
            self.assertEqual(rc, 0)
            self.assertIn("patched", buf.getvalue())
            self.assertIn("fd-paper-marks", buf.getvalue())
            text = dest.read_text(encoding="utf-8")
            self.assertIn("wrapPaintPxChart", text)
            self.assertIn('id="fd-paper-marks"', text)

    def test_require_wrap_source_has_pr13_needles(self) -> None:
        sync.require_wrap_source()
        js = cm.overlay_js()
        for needle in sync.WRAP_NEEDLES:
            self.assertIn(needle, js)

    def test_resolve_prefers_live_desktop_over_skinny_local(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            desk = Path(tmp) / "factorbook"
            desk.mkdir()
            live = desk / "factorbook.html"
            live.write_text(_pad(LIVE_BODY, 1_100_000), encoding="utf-8")
            skinny = Path(tmp) / "skinny.html"
            skinny.write_text(_pad(LIVE_BODY, 80_000), encoding="utf-8")
            got = sync.resolve_html_path(desktop_root=desk)
            self.assertEqual(got.resolve(), live.resolve())
            explicit = sync.resolve_html_path(skinny, desktop_root=desk)
            self.assertEqual(explicit.resolve(), skinny.resolve())

    def test_deploy_desktop_copies_py_not_html(self) -> None:
        live = _pad(LIVE_BODY, 1_100_000)
        with tempfile.TemporaryDirectory() as tmp:
            desk = Path(tmp) / "factorbook"
            desk.mkdir()
            dest = desk / "factorbook.html"
            dest.write_text(live, encoding="utf-8")
            planted = desk / "do-not-touch.html"
            planted.write_text("keep", encoding="utf-8")
            buf, err = StringIO(), StringIO()
            with redirect_stdout(buf), redirect_stderr(err):
                rc = sync.main(["--deploy-desktop", "--desktop-root", str(desk)])
            self.assertEqual(rc, 0, err.getvalue())
            self.assertTrue((desk / "chart_marks.py").is_file())
            self.assertTrue((desk / "sync_live_paintpx.py").is_file())
            self.assertEqual(planted.read_text(encoding="utf-8"), "keep")
            text = dest.read_text(encoding="utf-8")
            self.assertGreater(dest.stat().st_size, 1_000_000)
            self.assertIn("wrapPaintPxChart", text)
            self.assertIn('{"CNH":13.63,"PWR":629.65}', text)
            self.assertIn(">Paper</button>", text)
            self.assertIn("copied:", buf.getvalue())
            self.assertNotIn("factorbook.html", sync.COS_PY_FILES)

    def test_default_live_html_is_desktop_sibling_not_pack_nested(self) -> None:
        html = str(sync.DESKTOP_HTML_DEFAULT).replace("\\", "/")
        pack = str(sync.DESKTOP_PACK_DEFAULT).replace("\\", "/")
        self.assertTrue(html.endswith("Desktop/factorbook.html"), html)
        self.assertFalse(html.endswith("factorbook/factorbook.html"), html)
        self.assertTrue(pack.endswith("Desktop/factorbook"), pack)
        self.assertEqual(sync.desktop_html_path(None), sync.DESKTOP_HTML_DEFAULT)

    def test_deploy_desktop_prefers_sibling_html_over_nested_pack_copy(self) -> None:
        live_body = _pad(LIVE_BODY, 1_100_000)
        nested_body = _pad(LIVE_BODY.replace("CNH", "NESTED"), 1_100_000)
        with tempfile.TemporaryDirectory() as tmp:
            desk = Path(tmp)
            pack = desk / "factorbook"
            pack.mkdir()
            sibling = desk / "factorbook.html"
            nested = pack / "factorbook.html"
            sibling.write_text(live_body, encoding="utf-8")
            nested.write_text(nested_body, encoding="utf-8")
            buf, err = StringIO(), StringIO()
            with redirect_stdout(buf), redirect_stderr(err):
                rc = sync.main(["--deploy-desktop", "--desktop-root", str(pack)])
            self.assertEqual(rc, 0, err.getvalue())
            self.assertIn("wrapPaintPxChart", sibling.read_text(encoding="utf-8"))
            self.assertIn('{"CNH":13.63,"PWR":629.65}', sibling.read_text(encoding="utf-8"))
            self.assertIn("NESTED", nested.read_text(encoding="utf-8"))
            self.assertNotIn("wrapPaintPxChart", nested.read_text(encoding="utf-8"))
            self.assertTrue((pack / "chart_marks.py").is_file())

    def test_deploy_desktop_refuses_skinny_and_does_not_copy(self) -> None:
        small = _pad(LIVE_BODY, 80_000)
        with tempfile.TemporaryDirectory() as tmp:
            desk = Path(tmp) / "factorbook"
            desk.mkdir()
            dest = desk / "factorbook.html"
            dest.write_text(small, encoding="utf-8")
            buf, err = StringIO(), StringIO()
            with redirect_stdout(buf), redirect_stderr(err):
                rc = sync.main(["--deploy-desktop", "--desktop-root", str(desk)])
            self.assertEqual(rc, 2)
            self.assertIn("refuse", err.getvalue().lower())
            self.assertFalse((desk / "chart_marks.py").is_file())
            self.assertEqual(dest.read_text(encoding="utf-8"), small)


if __name__ == "__main__":
    unittest.main()
