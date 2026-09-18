"""Patch live Desktop ``factorbook.html`` with the PR#12/#13 paintPxChart wrap.

Tanner's desk is the large patched ``factorbook.html`` (~4.8MB) that already
has ``paintPxChart``, Paper / Experimental / Breakout, and ``#fd-paper-marks``.
Copying ``chart_marks.py`` alone does nothing until something calls
``chart_marks.ensure_embedded``. Live ``desk_dash.write_combined`` often
lacks that hook, and running cloud ``desk_dash.py`` / ``write_dash.py``
would emit skinny generator HTML.

This script **only** injects chart CSS + overlay JS:

    python sync_live_paintpx.py
    python sync_live_paintpx.py --html C:\\Users\\MLP\\Desktop\\factorbook\\factorbook.html

It refuses to write when the file is missing, smaller than 1MB, or not a
live ``paintPxChart`` desk. It never imports ``desk_dash``.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path
from typing import Any, Mapping

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import chart_marks  # noqa: E402

MIN_BYTES = 1_000_000
LIVE_MARKERS = ("paintPxChart", "data-px-svg", "data-px-json")
PRESERVE_TOKENS = (
    "Paper",
    "Experimental",
    "Breakout",
    "fd-paper-marks",
)
DESKTOP_DEFAULT = Path(r"C:\Users\MLP\Desktop\factorbook\factorbook.html")
BACKUP_SUFFIX = ".bak-paintpx"


class SyncRefused(RuntimeError):
    """Live HTML is missing, too small, or not safe to patch."""


def resolve_html_path(html: Path | str | None = None) -> Path:
    if html is not None:
        return Path(html)
    local = HERE / "factorbook.html"
    if local.is_file():
        return local
    if DESKTOP_DEFAULT.is_file():
        return DESKTOP_DEFAULT
    return local


def _size(text: str) -> int:
    return len(text.encode("utf-8"))


def looks_like_live_paintpx(html_text: str) -> bool:
    if not html_text:
        return False
    return all(marker in html_text for marker in LIVE_MARKERS)


def present_preserve_tokens(html_text: str) -> tuple[str, ...]:
    return tuple(token for token in PRESERVE_TOKENS if token in (html_text or ""))


def inject_paintpx(html_text: str, mapping: Mapping[str, Any] | None = None) -> str:
    """Inject wrap CSS/JS via ``chart_marks.ensure_embedded``.

    ``mapping=None`` (default) keeps existing ``#fd-chart-db``.
    """
    if mapping is None:
        return chart_marks.inject_paintpx(html_text)
    return chart_marks.ensure_embedded(html_text, mapping)


def validate_live_html(html_text: str, *, path: Path | None = None) -> None:
    label = str(path) if path is not None else "HTML"
    n = _size(html_text)
    if n < MIN_BYTES:
        raise SyncRefused(
            f"refuse: {label} is {n} bytes (< {MIN_BYTES}). "
            "This is not the live ~4.8MB desk — will not replace with skinny output."
        )
    if not looks_like_live_paintpx(html_text):
        raise SyncRefused(
            f"refuse: {label} is missing paintPxChart / [data-px-svg] / [data-px-json]. "
            "Refusing to patch a non-live or generator file."
        )


def validate_patched(
    before: str,
    after: str,
    *,
    path: Path | None = None,
) -> None:
    label = str(path) if path is not None else "HTML"
    n_after = _size(after)
    if n_after < MIN_BYTES:
        raise SyncRefused(
            f"refuse: patched {label} would be {n_after} bytes (< {MIN_BYTES}). "
            "Aborting so skinny desk_dash output cannot replace the live desk."
        )
    if _size(after) < int(_size(before) * 0.9):
        raise SyncRefused(
            f"refuse: patched {label} shrank from {_size(before)} to {n_after} bytes. "
            "Aborting to preserve Paper/Experimental/Breakout/#fd-paper-marks."
        )
    if not looks_like_live_paintpx(after):
        raise SyncRefused(
            f"refuse: patched {label} lost paintPxChart / data-px-svg / data-px-json."
        )
    for token in present_preserve_tokens(before):
        if token not in after:
            raise SyncRefused(
                f"refuse: patched {label} dropped {token!r}. "
                "Paper/Experimental/Breakout/#fd-paper-marks must survive."
            )
    if "wrapPaintPxChart" not in after or "fd-chart-marks-js" not in after:
        raise SyncRefused(
            f"refuse: patched {label} does not contain the paintPxChart wrap."
        )


def patch_live_html(
    path: Path | str | None = None,
    *,
    mapping: Mapping[str, Any] | None = None,
    dry_run: bool = False,
    backup: bool = True,
) -> dict[str, Any]:
    """Read live ``factorbook.html``, inject the wrap, write it back.

    Never calls ``desk_dash.write_combined`` / ``render_html``.
    """
    dest = resolve_html_path(path)
    if not dest.is_file():
        raise SyncRefused(f"refuse: {dest} does not exist.")
    before = dest.read_text(encoding="utf-8")
    validate_live_html(before, path=dest)
    after = inject_paintpx(before, mapping=mapping)
    validate_patched(before, after, path=dest)
    info: dict[str, Any] = {
        "path": str(dest),
        "bytes_before": _size(before),
        "bytes_after": _size(after),
        "preserved": list(present_preserve_tokens(after)),
        "wrote": False,
        "dry_run": dry_run,
        "backup": None,
    }
    if dry_run:
        return info
    if backup:
        bak = dest.with_name(dest.name + BACKUP_SUFFIX)
        shutil.copy2(dest, bak)
        info["backup"] = str(bak)
    tmp = dest.with_name(dest.name + ".tmp-paintpx")
    tmp.write_text(after, encoding="utf-8")
    tmp.replace(dest)
    info["wrote"] = True
    return info


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=(
            "Patch live factorbook.html with paintPxChart MA-regime wrap. "
            "Does not run desk_dash / write_dash (no skinny HTML)."
        )
    )
    p.add_argument(
        "--html",
        default="",
        help="Path to live factorbook.html (default: next to this script, else Desktop factorbook)",
    )
    p.add_argument("--dry-run", action="store_true", help="Validate and inject in memory; do not write")
    p.add_argument("--no-backup", action="store_true", help="Do not write factorbook.html.bak-paintpx")
    args = p.parse_args(argv)
    dest = resolve_html_path(args.html or None)
    try:
        info = patch_live_html(
            dest,
            dry_run=args.dry_run,
            backup=not args.no_backup,
        )
    except SyncRefused as exc:
        print(str(exc), file=sys.stderr)
        return 2
    action = "would patch" if info["dry_run"] else "patched"
    print(
        f"{action} {info['path']} "
        f"({info['bytes_before']} → {info['bytes_after']} bytes); "
        f"preserved {info['preserved']}"
    )
    if info.get("backup"):
        print(f"backup: {info['backup']}")
    print("Hard-reload the desk. Do not copy skinny generator factorbook.html.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
