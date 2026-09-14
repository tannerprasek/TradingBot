"""Write generated factorbook.html via desk_dash."""

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
    return desk_dash.assemble_and_write(path, root=root)


def main(argv: list[str] | None = None) -> int:
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="Write factorbook.html")
    p.add_argument("--out", default="")
    p.add_argument("--root", default="")
    args = p.parse_args(argv)
    root = Path(args.root) if args.root else HERE
    dest = Path(args.out) if args.out else None
    write(dest, root=root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
