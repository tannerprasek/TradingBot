"""Write factorbook.html via desk_dash.write_combined.

Thin wrapper only. There is no embedded FLAGS/PAIRS template and no
keyword fallback that writes a replacement shell.

Desktop layout (Tanner): this file lives in ``Desktop/factorbook/`` and the
desk that opens is ``Desktop/factorbook.html`` (the parent of this directory),
not the nested ``factorbook/factorbook.html``. ``python write_dash.py`` with
no ``--out`` writes that parent file when it already looks like the live desk.

``--out`` always wins. The process exits 2 when the written file is under
``desk_dash.LIVE_MIN_BYTES`` (1_000_000), so a ~188KB legacy shell cannot
report success.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import desk_dash  # noqa: E402

LOG = logging.getLogger("write_dash")


def write(path: Path | str | None = None, root: Path | None = None) -> Path:
    """Patch the live desk HTML. Never falls back to an embedded template.

    ``path`` / ``--out`` wins. When omitted, a fat live file at
    ``<root>/../factorbook.html`` is the dest (Desktop layout).
    """
    base = Path(root) if root is not None else HERE
    dest = desk_dash.resolve_live_dest(base, path)
    out = desk_dash.write_combined(dest, root=base)
    if out.is_file():
        written_html = out.read_text(encoding="utf-8")
        prior = out.stat().st_size
        desk_dash.assert_nav_integrity(
            written_html,
            prior_bytes=prior if prior >= desk_dash.LIVE_MIN_BYTES else 0,
        )
    if _is_desktop_live(out, base) and out.is_file():
        size = out.stat().st_size
        if size < desk_dash.LIVE_MIN_BYTES:
            raise desk_dash.LiveDeskShrinkError(
                f"HTML_OUT legacy-sized: {out} ({size} bytes < {desk_dash.LIVE_MIN_BYTES})"
            )
    return out


def _is_desktop_live(out: Path, root: Path) -> bool:
    parent = root.parent / desk_dash.HTML_NAME
    try:
        return out.resolve() == parent.resolve()
    except OSError:
        return False


def main(argv: list[str] | None = None) -> int:
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="Write factorbook.html")
    p.add_argument(
        "--out",
        default="",
        help=(
            "HTML dest. Default: <root>/../factorbook.html when that file "
            "exists and looks like the live desk (Tanner: Desktop/factorbook.html "
            "while cwd is Desktop/factorbook/). Otherwise <root>/factorbook.html."
        ),
    )
    p.add_argument("--root", default="", help="Package root. Default: this file's directory.")
    args = p.parse_args(argv)
    root = Path(args.root) if args.root else HERE
    explicit = Path(args.out) if args.out else None
    try:
        written = write(explicit, root=root)
    except (desk_dash.LiveDeskShrinkError, RuntimeError) as exc:
        LOG.error("%s", exc)
        return 2
    size = written.stat().st_size if written.is_file() else 0
    if size < desk_dash.LIVE_MIN_BYTES:
        LOG.error(
            "refusing legacy-sized factorbook.html (%s bytes < %s): %s",
            size,
            desk_dash.LIVE_MIN_BYTES,
            written,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
