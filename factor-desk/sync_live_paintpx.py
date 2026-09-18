"""Patch live Desktop ``factorbook.html`` with the PR#12/#13 paintPxChart wrap.

Tanner's desk is the large patched ``factorbook.html`` (~4.8MB) that already
has ``paintPxChart``, Paper / Experimental / Breakout, and ``#fd-paper-marks``.
Copying ``chart_marks.py`` alone does nothing until something calls
``chart_marks.ensure_embedded``. Live ``desk_dash.write_combined`` often
lacks that hook, and running cloud ``desk_dash.py`` / ``write_dash.py``
would emit skinny generator HTML.

This script **only** injects chart CSS + overlay JS. After merge, CoS:

    python factor-desk/sync_live_paintpx.py --deploy-desktop

That copies ``chart_marks.py`` + ``sync_live_paintpx.py`` into
``C:\\Users\\MLP\\Desktop\\factorbook`` (the pack next to live ``desk_dash.py``)
and patches live HTML at ``C:\\Users\\MLP\\Desktop\\factorbook.html``
(not ``Desktop\\factorbook\\factorbook.html``).
It never copies ``factorbook.html`` and never imports ``desk_dash``.
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
WRAP_NEEDLES = (
    "wrapPaintPxChart",
    "paintPxChart",
    "alignFlags",
    "close > s50 && close > s200",
    "#e6edf3",
    "restylePxChart",
    "restorePricePath",
    "maUsable",
    "failOpenPricePath",
)
COS_PY_FILES = ("chart_marks.py", "sync_live_paintpx.py")
DESKTOP_DIR_DEFAULT = Path(r"C:\Users\MLP\Desktop")
DESKTOP_PACK_DEFAULT = DESKTOP_DIR_DEFAULT / "factorbook"
DESKTOP_ROOT_DEFAULT = DESKTOP_PACK_DEFAULT
DESKTOP_HTML_DEFAULT = DESKTOP_DIR_DEFAULT / "factorbook.html"
DESKTOP_DEFAULT = DESKTOP_HTML_DEFAULT
BACKUP_SUFFIX = ".bak-paintpx"


class SyncRefused(RuntimeError):
    """Live HTML is missing, too small, or not safe to patch."""


def desktop_html_path(desktop_root: Path | str | None = None) -> Path:
    """Live HTML is ``Desktop\\factorbook.html``, not ``Desktop\\factorbook\\factorbook.html``.

    ``--desktop-root`` still names the pack folder (where CoS copies ``*.py``).
    When that folder is named ``factorbook``, prefer the sibling HTML on Desktop.
    """
    if desktop_root is None:
        return DESKTOP_HTML_DEFAULT
    root = Path(desktop_root)
    if root.suffix.lower() in {".html", ".htm"}:
        return root
    inside = root / "factorbook.html"
    sibling = root.parent / "factorbook.html" if root.name.lower() == "factorbook" else None
    if sibling is not None and file_looks_live(sibling):
        return sibling
    if file_looks_live(inside):
        return inside
    if sibling is not None and sibling.is_file():
        return sibling
    if inside.is_file():
        return inside
    if sibling is not None:
        return sibling
    return inside


def require_wrap_source() -> None:
    """Fail fast if CoS copied an old ``chart_marks.py`` without the PR13 wrap."""
    js = chart_marks.overlay_js()
    missing = [n for n in WRAP_NEEDLES if n not in js]
    if missing:
        raise SyncRefused(
            "refuse: chart_marks.overlay_js is missing "
            + ", ".join(repr(n) for n in missing)
            + ". Recopy chart_marks.py from this pack (PR #13 wrap)."
        )


def looks_like_live_paintpx(html_text: str) -> bool:
    if not html_text:
        return False
    return all(marker in html_text for marker in LIVE_MARKERS)


def file_looks_live(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size < MIN_BYTES:
        return False
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    return looks_like_live_paintpx(text)


def candidate_html_paths(
    html: Path | str | None = None,
    *,
    desktop_root: Path | str | None = None,
) -> list[Path]:
    paths: list[Path] = []
    if html is not None:
        paths.append(Path(html))
    paths.append(desktop_html_path(desktop_root))
    paths.append(HERE / "factorbook.html")
    cwd = Path.cwd() / "factorbook.html"
    if cwd not in paths:
        paths.append(cwd)
    out: list[Path] = []
    seen: set[str] = set()
    for p in paths:
        key = str(p)
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def resolve_html_path(
    html: Path | str | None = None,
    *,
    desktop_root: Path | str | None = None,
) -> Path:
    """Pick live Desktop HTML over a skinny generator file sitting in the pack."""
    cands = candidate_html_paths(html, desktop_root=desktop_root)
    if html is not None:
        return Path(html)
    for path in cands:
        if file_looks_live(path):
            return path
    for path in cands:
        if path.is_file():
            return path
    return cands[0]


def _size(text: str) -> int:
    return len(text.encode("utf-8"))


def present_preserve_tokens(html_text: str) -> tuple[str, ...]:
    return tuple(token for token in PRESERVE_TOKENS if token in (html_text or ""))


def inject_paintpx(html_text: str, mapping: Mapping[str, Any] | None = None) -> str:
    """Inject wrap CSS/JS via ``chart_marks.ensure_embedded``.

    ``mapping=None`` (default) keeps existing ``#fd-chart-db``.
    """
    require_wrap_source()
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


def deploy_py_files(desktop_root: Path | str) -> list[str]:
    """Copy wrap Python onto Desktop. Never copies ``factorbook.html``."""
    root = Path(desktop_root)
    copied: list[str] = []
    for name in COS_PY_FILES:
        if name.lower().endswith(".html"):
            raise SyncRefused("refuse: will not copy HTML onto the live desk.")
        src = HERE / name
        if not src.is_file():
            raise SyncRefused(f"refuse: {src} missing — cannot deploy {name}.")
        dest = root / name
        if dest.resolve() != src.resolve():
            shutil.copy2(src, dest)
        copied.append(str(dest))
    return copied


def patch_live_html(
    path: Path | str | None = None,
    *,
    mapping: Mapping[str, Any] | None = None,
    dry_run: bool = False,
    backup: bool = True,
    desktop_root: Path | str | None = None,
) -> dict[str, Any]:
    """Read live ``factorbook.html``, inject the wrap, write it back.

    Never calls ``desk_dash.write_combined`` / ``render_html``.
    """
    dest = resolve_html_path(path, desktop_root=desktop_root)
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
        "copied": [],
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


def deploy_desktop(
    desktop_root: Path | str | None = None,
    *,
    dry_run: bool = False,
    backup: bool = True,
) -> dict[str, Any]:
    """CoS one-shot: copy wrap Python to the Desktop pack, then patch live HTML.

    Live HTML default is ``C:\\Users\\MLP\\Desktop\\factorbook.html`` (sibling of
    the ``factorbook\\`` pack folder), never a skinny generator rewrite.
    """
    pack = Path(desktop_root) if desktop_root is not None else DESKTOP_PACK_DEFAULT
    html_path = desktop_html_path(pack)
    if not html_path.is_file():
        raise SyncRefused(
            f"refuse: {html_path} does not exist. "
            "CoS patches C:\\Users\\MLP\\Desktop\\factorbook.html "
            "(not Desktop\\factorbook\\factorbook.html); will not write a new file."
        )
    before = html_path.read_text(encoding="utf-8")
    validate_live_html(before, path=html_path)
    require_wrap_source()
    copied: list[str] = []
    if not dry_run:
        copied = deploy_py_files(pack)
    info = patch_live_html(
        html_path,
        dry_run=dry_run,
        backup=backup,
        desktop_root=pack,
    )
    info["copied"] = copied
    info["desktop_root"] = str(pack)
    info["html"] = str(html_path)
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
        help="Path to live factorbook.html (default: C:\\Users\\MLP\\Desktop\\factorbook.html)",
    )
    p.add_argument(
        "--desktop-root",
        default="",
        help=r"Desktop pack folder for *.py copies (default: C:\Users\MLP\Desktop\factorbook). Live HTML is the sibling C:\Users\MLP\Desktop\factorbook.html",
    )
    p.add_argument(
        "--deploy-desktop",
        action="store_true",
        help="CoS: copy chart_marks.py + sync_live_paintpx.py to Desktop\\factorbook, then patch Desktop\\factorbook.html",
    )
    p.add_argument("--dry-run", action="store_true", help="Validate and inject in memory; do not write")
    p.add_argument("--no-backup", action="store_true", help="Do not write factorbook.html.bak-paintpx")
    args = p.parse_args(argv)
    desktop_root = Path(args.desktop_root) if args.desktop_root else DESKTOP_ROOT_DEFAULT
    try:
        if args.deploy_desktop:
            info = deploy_desktop(
                desktop_root,
                dry_run=args.dry_run,
                backup=not args.no_backup,
            )
        else:
            dest = resolve_html_path(args.html or None, desktop_root=desktop_root)
            info = patch_live_html(
                dest,
                dry_run=args.dry_run,
                backup=not args.no_backup,
                desktop_root=desktop_root,
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
    if info.get("copied"):
        print("copied: " + ", ".join(info["copied"]))
    if info.get("backup"):
        print(f"backup: {info['backup']}")
    print("Hard-reload the desk. Do not copy skinny generator factorbook.html.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
