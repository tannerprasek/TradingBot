"""Live desk must survive Add/Refresh. No legacy __PAYLOAD__ fallback."""

from __future__ import annotations

import inspect
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import add_server  # noqa: E402
import desk_dash  # noqa: E402
import write_dash  # noqa: E402


def _fat_desk_html() -> str:
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
<article class="card" data-t="AAPL">AAPL live chrome</article>
</body></html>"""
    pad = 1_200_000 - len(body.encode("utf-8"))
    return body + ("<!--" + ("P" * max(pad, 1)) + "-->")


def _book() -> dict:
    return {"asof": "2026-09-21", "names": {}, "meta": {}}


class LiveDeskWriteTests(unittest.TestCase):
    def test_write_combined_keeps_fat_chrome_markers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dest = root / "factorbook.html"
            dest.write_text(_fat_desk_html(), encoding="utf-8")
            before = dest.stat().st_size
            out = desk_dash.write_combined(dest, root=root, book=_book())
            text = out.read_text(encoding="utf-8")
            self.assertGreaterEqual(out.stat().st_size, desk_dash.LIVE_MIN_BYTES)
            self.assertGreaterEqual(out.stat().st_size, before - 1024)
            self.assertIn("Momentum Up", text)
            self.assertIn("Refresh", text)
            self.assertIn("Momentum Down", text)
            self.assertIn("Outliers", text)
            self.assertIn("Options", text)
            self.assertIn("AAPL live chrome", text)

    def test_desktop_layout_patches_parent_not_nested(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            desktop = Path(tmp)
            pkg = desktop / "factorbook"
            pkg.mkdir()
            live = desktop / "factorbook.html"
            live.write_text(_fat_desk_html(), encoding="utf-8")
            nested = pkg / "factorbook.html"
            nested.write_text("<html><body>skinny nested</body></html>", encoding="utf-8")
            before = live.stat().st_size
            out = desk_dash.write_combined(None, root=pkg, book=_book())
            self.assertEqual(out.resolve(), live.resolve())
            text = live.read_text(encoding="utf-8")
            self.assertGreaterEqual(live.stat().st_size, desk_dash.LIVE_MIN_BYTES)
            self.assertGreaterEqual(live.stat().st_size, before - 1024)
            self.assertIn("Momentum Up", text)
            self.assertIn("Refresh", text)
            self.assertIn("AAPL live chrome", text)
            self.assertIn("skinny nested", nested.read_text(encoding="utf-8"))
            self.assertLess(nested.stat().st_size, desk_dash.LIVE_MIN_BYTES)

    def test_write_dash_targets_desktop_parent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            desktop = Path(tmp)
            pkg = desktop / "factorbook"
            pkg.mkdir()
            live = desktop / "factorbook.html"
            live.write_text(_fat_desk_html(), encoding="utf-8")
            out = write_dash.write(None, root=pkg)
            self.assertEqual(out.resolve(), live.resolve())
            self.assertGreaterEqual(live.stat().st_size, desk_dash.LIVE_MIN_BYTES)
            text = live.read_text(encoding="utf-8")
            self.assertIn("Momentum Up", text)
            self.assertIn("Refresh", text)
            code = write_dash.main(["--root", str(pkg)])
            self.assertEqual(code, 0)
            self.assertGreaterEqual(live.stat().st_size, desk_dash.LIVE_MIN_BYTES)

    def test_write_dash_exits_nonzero_when_output_is_legacy_sized(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            code = write_dash.main(["--root", str(root)])
            self.assertEqual(code, 2)
            written = root / "factorbook.html"
            self.assertTrue(written.is_file())
            self.assertLess(written.stat().st_size, desk_dash.LIVE_MIN_BYTES)

    def test_explicit_out_wins_over_desktop_parent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            desktop = Path(tmp)
            pkg = desktop / "factorbook"
            pkg.mkdir()
            live = desktop / "factorbook.html"
            live.write_text(_fat_desk_html(), encoding="utf-8")
            custom = pkg / "custom.html"
            custom.write_text(_fat_desk_html(), encoding="utf-8")
            code = write_dash.main(["--root", str(pkg), "--out", str(custom)])
            self.assertEqual(code, 0)
            self.assertGreaterEqual(custom.stat().st_size, desk_dash.LIVE_MIN_BYTES)
            self.assertIn("AAPL live chrome", custom.read_text(encoding="utf-8"))

    def test_refuses_render_html_over_fat_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dest = root / "factorbook.html"
            original = _fat_desk_html()
            dest.write_text(original, encoding="utf-8")
            with patch.object(desk_dash, "looks_like_live_desk", return_value=False):
                with self.assertRaises(desk_dash.LiveDeskShrinkError) as ctx:
                    desk_dash.write_combined(dest, root=root, book=_book())
            self.assertIn("refusing to shrink", str(ctx.exception))
            self.assertEqual(dest.read_text(encoding="utf-8"), original)
            self.assertGreaterEqual(dest.stat().st_size, desk_dash.LIVE_MIN_BYTES)

    def test_factor_payload_is_not_the_api(self) -> None:
        sig = inspect.signature(desk_dash.write_combined)
        self.assertNotIn("factor_payload", sig.parameters)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dest = root / "factorbook.html"
            original = _fat_desk_html()
            dest.write_text(original, encoding="utf-8")
            with self.assertRaises(TypeError):
                desk_dash.write_combined(dest, root=root, factor_payload={"flags": []})  # type: ignore[call-arg]
            self.assertEqual(dest.read_text(encoding="utf-8"), original)
            self.assertNotIn("__PAYLOAD__", dest.read_text(encoding="utf-8"))

    def test_write_dash_has_no_legacy_template(self) -> None:
        src = Path(write_dash.__file__).read_text(encoding="utf-8")
        self.assertNotIn("__PAYLOAD__", src)
        self.assertNotIn("falling back to legacy", src.lower())
        self.assertNotIn("factor_payload", src)
        self.assertNotIn("replace(\"__PAYLOAD__\"", src)
        dash = Path(desk_dash.__file__).read_text(encoding="utf-8")
        self.assertNotIn("def write_combined(\n    path: Path | str | None = None,\n    *,\n    root: Path | None = None,\n    cards: list[Mapping[str, Any]] | None = None,\n    book: Mapping[str, Any] | None = None,\n    html: str | None = None,\n    factor_payload", dash)


class AddServerLiveOutTests(unittest.TestCase):
    def test_rebuild_does_not_point_at_rebuild_bundle(self) -> None:
        src = Path(add_server.__file__).read_text(encoding="utf-8")
        for line in src.splitlines():
            if "rebuild_bundle" not in line:
                continue
            stripped = line.strip()
            self.assertTrue(
                stripped.startswith("#") or "Never" in line or "Do not" in line,
                msg=line,
            )

    def test_run_rebuild_patches_desktop_html_out(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            desktop = Path(tmp)
            pkg = desktop / "factorbook"
            pkg.mkdir()
            live = desktop / "factorbook.html"
            live.write_text(_fat_desk_html(), encoding="utf-8")
            err = add_server.run_rebuild(lambda *_a, **_k: None, root=pkg)
            self.assertTrue(err is None or "legacy-sized" not in str(err), msg=err)
            text = live.read_text(encoding="utf-8")
            self.assertGreaterEqual(live.stat().st_size, desk_dash.LIVE_MIN_BYTES)
            self.assertIn("Momentum Up", text)
            self.assertIn("Refresh", text)
            self.assertEqual(add_server.html_out(pkg).resolve(), live.resolve())

    def test_run_rebuild_fails_when_desktop_html_is_legacy_sized(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            desktop = Path(tmp)
            pkg = desktop / "factorbook"
            pkg.mkdir()
            live = desktop / "factorbook.html"
            live.write_text(
                "<html><body>FLAGS PAIRS super old loading</body></html>",
                encoding="utf-8",
            )
            err = add_server.run_rebuild_stage(lambda *_a, **_k: None, root=pkg)
            self.assertIsNotNone(err)
            self.assertIn("legacy-sized", str(err))
            self.assertIn("super old loading", live.read_text(encoding="utf-8"))
            self.assertLess(live.stat().st_size, desk_dash.LIVE_MIN_BYTES)
            with self.assertRaises(desk_dash.LiveDeskShrinkError):
                add_server.run_refresh(tickers=["AAPL"], root=pkg)
            self.assertIn("super old loading", live.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
