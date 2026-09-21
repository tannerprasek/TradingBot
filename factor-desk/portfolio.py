"""Factor Desk Portfolio Upload — look-here scorecard vs the live book.

Tanner pastes or uploads ``position,ticker`` (signed shares). The tab scores
that book against the **existing** FLAGS / WATCH / MOM factor set, ``mom_score``
definition, and name metrics already on the desk. Not a trade. Not a new model.

Weights (gross-normalized, signed)::

    notional_i = shares_i × last_i
    weight_i   = notional_i / sum_j |notional_j|

    sum(|weight|) = 1
    sum(weight)   = net / gross

Factor exposures and metric averages use these signed gross weights so a short
flips the contribution. Names without a last print are unpriced (out of the
denom). Names not in the current desk universe are **missing** — still priced
via DAPI when possible; factor / mom stay n/a until they are in the book.

Last px: desk snapshot (``dapi_enrichment.json`` / MOM cards / ``prices.csv``)
then the existing DAPI sidecar (``PX_LAST`` candidates). No new vendor.

Live deploy: copy this module next to ``desk_dash.py`` and paste
``portfolio.ensure_embedded`` on the ``write_combined`` tail. Do **not**
wholesale replace live ``desk_dash.py``. See ``DEPLOY-HOOKS.md``.
"""

from __future__ import annotations

import csv
import html
import io
import json
import logging
import math
import os
import re
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import parse_qs, unquote, urlparse

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import dapi_enrich  # noqa: E402

LOG = logging.getLogger("portfolio")

BOOK_FILENAME = "portfolio_last.json"
BOOK_VERSION = 1
SIDECAR_ORIGIN = "http://127.0.0.1:8765"

DB_SCRIPT_ID = "fd-portfolio-db"
DESK_SCRIPT_ID = "fd-portfolio-desk-db"
JS_SCRIPT_ID = "fd-portfolio-js"
CSS_STYLE_ID = "fd-portfolio-css"
VIEW_ID = "view-portfolio"
NAV_ID = "fd-nav-portfolio"
HID_CLASS = "fd-pf-hid"

BTN_PORTFOLIO = (
    f'<button type="button" class="btn nav-btn" id="{NAV_ID}" '
    'data-view="portfolio" data-fd-portfolio="1">Portfolio</button>'
)
SETVIEW_MARKER = "/*fd-pf-setview*/"
HIDEALL_MARKER = "/*fd-pf-hideall*/"
PAINTVIEW_MARKER = "/*fd-pf-paintview*/"

POSITION_ALIASES = (
    "position",
    "shares",
    "qty",
    "quantity",
    "size",
    "n",
    "pos",
    "signed_shares",
    "share",
)
TICKER_ALIASES = (
    "ticker",
    "symbol",
    "yellow",
    "name",
    "bbg",
    "security",
    "t",
    "ric",
    "undl",
)

PX_KEYS: tuple[str, ...] = (
    "px_last",
    "PX_LAST",
    "LAST_PRICE",
    "last_price",
    "last",
    "price",
    "px",
    "close",
    "px_close",
    "PX_CLOSE",
    "mark",
    "mark_px",
    "last_px",
    "refresh_px",
    "pxLast",
    "lastPrice",
)

# Same rank the FLAGS / MOM Up-Down cards already show (mom_streak resolver).
CARD_SCORE_KEYS: tuple[str, ...] = (
    "mom_score",
    "momentum_score",
    "mom_rank",
    "trend_rank",
)
OPTIONAL_SMALL_SCORE_KEYS: tuple[str, ...] = ("score", "trend_score")
SKIP_SCORE_KEYS: frozenset[str] = frozenset({"score_v2", "opt_score", "options_score"})

NUMERIC_METRICS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("mom_score", CARD_SCORE_KEYS + OPTIONAL_SMALL_SCORE_KEYS),
    ("r20", ("r20", "R20", "ret_20d", "ret20", "r_20d")),
    ("rs63", ("rs63", "RS63", "rs_63", "rel_str_63")),
    ("atr_pct", ("atr_pct", "atr%", "ATRS", "atrs", "atr", "ATR", "atr_pct_disp")),
    ("beta", ("beta",)),
    ("residual_20d", ("residual_20d", "resid_20d")),
    ("si_ratio", ("si_ratio",)),
    ("si_pct_float", ("si_pct_float",)),
    ("inst_pct", ("inst_pct",)),
    ("event_days", ("event_days",)),
    ("vol_30d", ("vol_30d",)),
    ("iv_mid", ("iv_mid",)),
    ("score_v2", ("score_v2",)),
    ("skew_25d_proxy", ("skew_25d_proxy",)),
    ("mom_streak", ("mom_streak", "mom_streak_n")),
)

CATEGORICAL_METRICS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("gics", ("gics_sector_name", "gics", "sector", "gics_name")),
    ("factor", ("factor", "factor_name", "pc", "group", "flag", "factor_id", "sparse_factor")),
    ("vol_regime", ("vol_regime",)),
    ("liq", ("liq",)),
    ("mom_streak_side", ("mom_streak_side", "streak_side")),
)

LOADINGS_CANDIDATES: tuple[str, ...] = (
    "sparse_pca_loadings.csv",
    "sparsepca_loadings.csv",
    "pca_loadings.csv",
    "factor_loadings.csv",
    "v0_loadings.csv",
    "loadings.csv",
    "sparse_pca_loadings.json",
    "pca_loadings.json",
    "factor_loadings.json",
    "v0_loadings.json",
    "clean/loadings.csv",
    "clean/v0_loadings.csv",
)
MEMBERSHIP_CANDIDATES: tuple[str, ...] = (
    "factor_membership.csv",
    "membership.csv",
    "clean/membership.csv",
    "flags_membership.csv",
)
MOM_HIST_CANDIDATES: tuple[str, ...] = (
    "mom_score_hist.json",
    "mom_scores.csv",
    "score_hist.csv",
    "v0_scores.csv",
    "clean/mom_scores.csv",
)
PRICE_CANDIDATES: tuple[str, ...] = (
    "prices.csv",
    "px.csv",
    "closes.csv",
    "clean/prices.csv",
    "clean/px.csv",
)
CARD_JSON_CANDIDATES: tuple[str, ...] = (
    "mom_cards.json",
    "mom.json",
    "flags.json",
    "watch.json",
    "cards.json",
    dapi_enrich.OPTIONS_ABNORMAL_FILENAME,
)
UNIVERSE_FILES: tuple[str, ...] = ("universe.txt", "tickers.txt")

NATIVE_VIEW_IDS: tuple[str, ...] = (
    "home",
    "view-mom-up",
    "view-mom-down",
    "view-outliers",
    "view-options",
    "view-sectors",
    "search-pane",
    "view-breakout",
    "view-breakdown",
    "view-experimental",
    "view-paper",
    VIEW_ID,
)

_ALLOWLIST_RE = re.compile(
    r"home\|mom-up\|mom-down\|outliers\|options"
    r"(?:\|sectors)?(?:\|breakout\|breakdown)?(?:\|experimental)?(?:\|paper)?"
    r"(?!\|portfolio)",
    re.I,
)
_SETVIEW_FN_RE = re.compile(
    r"(function\s+setView\s*\(\s*(\w+)\s*(?:,[^)]*)?\)\s*\{)",
    re.I,
)
_PAINTVIEW_FN_RE = re.compile(
    r"(function\s+paintView\s*\(\s*(\w+)\s*(?:,[^)]*)?\)\s*\{)",
    re.I,
)
_HIDEALL_FN_RE = re.compile(
    r"(function\s+hideAllPanes\s*\(\s*\)\s*\{)",
    re.I,
)
_VIEW_ID_ARRAY_RE = re.compile(
    r"""((?:\[\s*["']home["']\s*,\s*["']view-mom-up["']\s*,\s*["']view-mom-down["']"""
    r"""\s*,\s*["']view-outliers["']\s*,\s*["']view-options["']))"""
    r"""(?![^\]]*(?:view-portfolio))(\s*\])""",
    re.I,
)
_VIEW_SEL_RE = re.compile(
    r"(#home\s*,\s*#view-mom-up\s*,\s*#view-mom-down\s*,\s*#view-outliers\s*,\s*"
    r"#view-options)"
    r"(?![^\"';)]*(?:#view-portfolio))",
    re.I,
)
_DIV_TOKEN_RE = re.compile(r"<\s*(/)?\s*div\b([^>]*)>", re.I)
_OPTIONS_VIEW_BTN_RE = re.compile(
    r'(<button\b(?=[^>]*data-view=["\']options["\'])[^>]*>\s*Options\s*</button>)',
    re.I | re.S,
)
_EXPERIMENTAL_BTN_RE = re.compile(
    r'(<button\b(?=[^>]*(?:data-view=["\']experimental["\']|data-fd-sscore))[^>]*>.*?</button>)',
    re.I | re.S,
)
_PAPER_BTN_RE = re.compile(
    r'(<button\b(?=[^>]*data-view=["\']paper["\'])[^>]*>\s*Paper\s*</button>)',
    re.I | re.S,
)
_BREAKDOWN_BTN_RE = re.compile(
    r'(<button\b(?=[^>]*(?:data-view=["\']breakdown["\']|>\s*Breakdown))[^>]*>\s*Breakdown\s*</button>)',
    re.I | re.S,
)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _short(ticker: str) -> str:
    parts = (ticker or "").split()
    return parts[0].upper() if parts else ""


def _ticker_key(ticker: str) -> str:
    return dapi_enrich.name_key(ticker) if ticker else ""


def _finite(value: Any) -> float | None:
    num = dapi_enrich.as_float(value)
    if num is None:
        return None
    if not math.isfinite(num):
        return None
    return num


def in_home_score_range(value: float | None) -> bool:
    if value is None:
        return False
    return 0.0 <= float(value) <= 20.0


def default_book_path(root: Path | None = None) -> Path:
    base = Path(root) if root is not None else resolve_root()
    runtime = base / "runtime"
    if runtime.is_dir():
        return runtime / BOOK_FILENAME
    return base / BOOK_FILENAME


def resolve_root(root: Path | None = None) -> Path:
    if root is not None:
        return Path(root)
    env = os.environ.get("FACTOR_DESK_ROOT") or os.environ.get("FACTORBOOK_ROOT")
    if env:
        return Path(env)
    return HERE


def empty_scorecard() -> dict[str, Any]:
    return {
        "version": BOOK_VERSION,
        "asof": None,
        "ok": False,
        "advice": "look-here scorecard · not a trade",
        "weight_def": WEIGHT_DEF,
        "rows": [],
        "missing": [],
        "unpriced": [],
        "exposure": {},
        "mom": {},
        "factors": {},
        "metrics": [],
        "categoricals": [],
        "n": 0,
        "error": None,
    }


WEIGHT_DEF = (
    "Gross-normalized signed weights: weight_i = notional_i / sum(|notional|), "
    "notional_i = shares_i × last_i. Factor / metric exposures use these weights "
    "(a short flips the sign). Net/gross = sum(notional) / sum(|notional|). "
    "Unpriced names are out of the denominator."
)


# ---------------------------------------------------------------------------
# Parse CSV / paste
# ---------------------------------------------------------------------------


class ParseError(ValueError):
    """User-facing parse failure (bad header, empty, …)."""


def _norm_header(cell: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (cell or "").strip().lower()).strip("_")


def _is_position_header(cell: str) -> bool:
    return _norm_header(cell) in POSITION_ALIASES


def _is_ticker_header(cell: str) -> bool:
    return _norm_header(cell) in TICKER_ALIASES


def _looks_numeric(cell: str) -> bool:
    text = (cell or "").strip().replace(",", "").replace("_", "")
    if not text:
        return False
    return _finite(text) is not None


def _parse_shares(raw: Any) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return _finite(raw)
    text = str(raw).strip().replace("_", "").replace(" ", "")
    if not text or text in {"-", "—", "n/a", "NA"}:
        return None
    # "(100)" → -100 ; "1,000" → 1000
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]
    text = text.replace(",", "")
    return _finite(text)


def _detect_dialect(sample: str) -> csv.Dialect:
    try:
        return csv.Sniffer().sniff(sample, delimiters=",\t;|")
    except csv.Error:
        class _Comma(csv.Dialect):
            delimiter = ","
            quotechar = '"'
            doublequote = True
            skipinitialspace = True
            lineterminator = "\n"
            quoting = csv.QUOTE_MINIMAL

        return _Comma()


def _split_lines(text: str) -> list[str]:
    blob = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    if blob.startswith("\ufeff"):
        blob = blob[1:]
    lines: list[str] = []
    for line in blob.split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        lines.append(line)
    return lines


def parse_table(text: str) -> list[dict[str, Any]]:
    """Parse CSV / TSV / paste into ``[{ticker, position, raw_ticker}, ...]``.

    Requires columns ``position`` (signed shares) and ``ticker``. Header aliases
    accepted. Duplicate tickers are summed after Bloomberg-style normalize
    (bare ``AAPL`` → ``AAPL US Equity``).
    """
    lines = _split_lines(text)
    if not lines:
        raise ParseError("empty table — need position,ticker")
    sample = "\n".join(lines[:40])
    dialect = _detect_dialect(sample)
    reader = csv.reader(io.StringIO("\n".join(lines)), dialect)
    rows = [list(r) for r in reader if any(str(c).strip() for c in r)]
    if not rows:
        raise ParseError("empty table — need position,ticker")

    header = rows[0]
    pos_idx = None
    tkr_idx = None
    has_header = False
    for i, cell in enumerate(header):
        if _is_position_header(str(cell)):
            pos_idx = i
            has_header = True
        elif _is_ticker_header(str(cell)):
            tkr_idx = i
            has_header = True

    body = rows[1:] if has_header else rows
    if has_header and (pos_idx is None or tkr_idx is None):
        raise ParseError("need columns position,ticker (signed shares, Bloomberg yellow key)")
    if not has_header:
        if len(header) < 2:
            raise ParseError("need two columns: position,ticker")
        # First numeric cell is position.
        if _looks_numeric(str(header[0])) and not _looks_numeric(str(header[1])):
            pos_idx, tkr_idx = 0, 1
        elif _looks_numeric(str(header[1])) and not _looks_numeric(str(header[0])):
            pos_idx, tkr_idx = 1, 0
        else:
            raise ParseError("need columns position,ticker (signed shares, Bloomberg yellow key)")
        body = rows

    if pos_idx is None or tkr_idx is None:
        raise ParseError("need columns position,ticker")

    merged: dict[str, dict[str, Any]] = {}
    skipped: list[str] = []
    for raw in body:
        if pos_idx >= len(raw) or tkr_idx >= len(raw):
            skipped.append("short_row")
            continue
        raw_ticker = str(raw[tkr_idx] or "").strip()
        shares = _parse_shares(raw[pos_idx])
        if not raw_ticker:
            skipped.append("blank_ticker")
            continue
        if shares is None:
            skipped.append(raw_ticker)
            continue
        if shares == 0:
            continue
        key = _ticker_key(raw_ticker)
        if not key:
            skipped.append(raw_ticker)
            continue
        if key in merged:
            merged[key]["position"] = float(merged[key]["position"]) + float(shares)
        else:
            merged[key] = {
                "ticker": key,
                "t": _short(key),
                "raw_ticker": raw_ticker,
                "position": float(shares),
            }
    out = [rec for rec in merged.values() if rec["position"] != 0]
    if not out:
        raise ParseError("no usable rows — position must be signed shares, ticker a yellow key")
    if skipped:
        LOG.info("parse skipped %s rows", len(skipped))
    return out


def parse_rows(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    lines = ["position,ticker"]
    for raw in rows:
        if not isinstance(raw, Mapping):
            continue
        pos = raw.get("position")
        if pos is None:
            for alias in POSITION_ALIASES:
                if alias in raw:
                    pos = raw[alias]
                    break
        tkr = raw.get("ticker")
        if tkr is None:
            for alias in TICKER_ALIASES:
                if alias in raw:
                    tkr = raw[alias]
                    break
        lines.append(f"{pos},{tkr}")
    return parse_table("\n".join(lines))


# ---------------------------------------------------------------------------
# Desk snapshot — reuse live name cards / enrich / loadings / mom hist
# ---------------------------------------------------------------------------


def resolve_card_score(item: Mapping[str, Any] | None) -> tuple[float | None, str | None]:
    """Same mom_score the FLAGS / MOM Up-Down cards already show.

    Prefer ``mom_score`` / ``momentum_score`` / ``mom_rank`` / ``trend_rank``.
    ``score`` / ``trend_score`` only when in ``[0, 20]``. Never options
    ``score_v2``.
    """
    if not item:
        return None, None
    for key in CARD_SCORE_KEYS:
        val = _finite(item.get(key))
        if val is not None:
            return val, key
    for key in OPTIONAL_SMALL_SCORE_KEYS:
        val = _finite(item.get(key))
        if val is not None and in_home_score_range(val):
            return val, key
    return None, None


def _first_key(rec: Mapping[str, Any], keys: Sequence[str]) -> tuple[Any, str | None]:
    for key in keys:
        if key in rec and rec.get(key) not in (None, "", "—"):
            return rec.get(key), key
    return None, None


def _as_vector(value: Any) -> list[float] | None:
    if isinstance(value, (list, tuple)):
        nums = [_finite(v) for v in value]
        if any(n is None for n in nums):
            return None
        return [float(n) for n in nums if n is not None]
    if isinstance(value, Mapping):
        items = sorted(value.items(), key=lambda kv: str(kv[0]))
        nums = [_finite(v) for _, v in items]
        if any(n is None for n in nums):
            return None
        return [float(n) for n in nums if n is not None]
    return None


def _blank_name(ticker: str) -> dict[str, Any]:
    key = _ticker_key(ticker)
    return {
        "ticker": key,
        "t": _short(key),
        "in_universe": False,
        "px_last": None,
        "px_source": None,
        "mom_score": None,
        "mom_source": None,
        "metrics": {},
        "pills": [],
        "gics": None,
        "factor": None,
        "loadings": None,
    }


def _merge_rec(dst: dict[str, Any], src: Mapping[str, Any]) -> dict[str, Any]:
    dst["in_universe"] = True
    if dst.get("px_last") is None:
        px, src_key = _first_key(src, PX_KEYS)
        px_f = _finite(px)
        if px_f is not None and px_f > 0:
            dst["px_last"] = px_f
            dst["px_source"] = src_key or "desk"
    mom, mom_src = resolve_card_score(src)
    if dst.get("mom_score") is None and mom is not None:
        dst["mom_score"] = mom
        dst["mom_source"] = mom_src
    metrics = dst.setdefault("metrics", {})
    for dest_key, keys in NUMERIC_METRICS:
        if metrics.get(dest_key) is not None:
            continue
        if dest_key == "mom_score":
            if dst.get("mom_score") is not None:
                metrics["mom_score"] = dst["mom_score"]
            continue
        val, _ = _first_key(src, keys)
        num = _finite(val)
        if num is not None:
            metrics[dest_key] = num
    if dst.get("mom_score") is not None:
        metrics["mom_score"] = dst["mom_score"]
    for dest_key, keys in CATEGORICAL_METRICS:
        if dest_key == "gics" and dst.get("gics"):
            continue
        if dest_key == "factor" and dst.get("factor"):
            continue
        val, _ = _first_key(src, keys)
        if val is None or isinstance(val, (list, dict)):
            continue
        text = str(val).strip()
        if not text or text in {"None", "null", "—"}:
            continue
        if dest_key == "gics":
            dst["gics"] = text
        elif dest_key == "factor":
            dst["factor"] = text
        else:
            metrics[dest_key] = text
    pills = src.get("enrich_pills") or src.get("pills") or []
    if isinstance(pills, list) and pills and not dst.get("pills"):
        dst["pills"] = [
            p.get("label") if isinstance(p, Mapping) else str(p)
            for p in pills
            if p
        ]
    loadings = _as_vector(src.get("loadings") or src.get("factor_loadings") or src.get("pcs"))
    if loadings and dst.get("loadings") is None:
        dst["loadings"] = loadings
    factors_map = src.get("factors") or src.get("factor_scores")
    if isinstance(factors_map, Mapping) and dst.get("loadings") is None:
        vec = _as_vector(factors_map)
        if vec:
            dst["loadings"] = vec
    return dst


def _ingest_name_map(book: dict[str, dict[str, Any]], blob: Any, *, in_universe: bool = True) -> None:
    if isinstance(blob, Mapping):
        items: Iterable[Any]
        if "names" in blob and isinstance(blob["names"], Mapping):
            items = blob["names"].items()
        elif "cards" in blob and isinstance(blob["cards"], list):
            items = [(c.get("ticker") or c.get("name") or c.get("t"), c) for c in blob["cards"] if isinstance(c, Mapping)]
        elif "rows" in blob and isinstance(blob["rows"], list):
            items = [(c.get("ticker") or c.get("t"), c) for c in blob["rows"] if isinstance(c, Mapping)]
        else:
            items = blob.items()
        for raw_key, rec in items:
            if raw_key in {"asof", "meta", "version", "kind", "names", "cards", "rows"}:
                if not isinstance(rec, Mapping):
                    continue
                if raw_key in {"asof", "meta", "version", "kind"}:
                    continue
            if not isinstance(rec, Mapping):
                rec = {"ticker": raw_key, "value": rec}
            ticker = str(rec.get("ticker") or rec.get("name") or rec.get("t") or raw_key or "")
            key = _ticker_key(ticker)
            if not key:
                continue
            dst = book.setdefault(key, _blank_name(key))
            if in_universe:
                dst["in_universe"] = True
            _merge_rec(dst, rec)
        return
    if isinstance(blob, list):
        for rec in blob:
            if not isinstance(rec, Mapping):
                continue
            ticker = str(rec.get("ticker") or rec.get("name") or rec.get("t") or "")
            key = _ticker_key(ticker)
            if not key:
                continue
            dst = book.setdefault(key, _blank_name(key))
            if in_universe:
                dst["in_universe"] = True
            _merge_rec(dst, rec)


def _load_json(path: Path) -> Any | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        LOG.warning("skip %s: %s", path, exc)
        return None
    return data


def _load_csv_rows(path: Path) -> list[dict[str, str]]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            return list(csv.DictReader(fh))
    except OSError as exc:
        LOG.warning("skip %s: %s", path, exc)
        return []


def load_loadings(root: Path | None = None) -> tuple[dict[str, list[float]], list[str], str | None]:
    """``ticker → loading vector`` from the desk's existing SparsePCA / v0 files.

    Does not fit a new model. Missing files → empty.
    """
    base = Path(root) if root is not None else HERE
    for rel in LOADINGS_CANDIDATES:
        path = base / rel
        if not path.is_file():
            continue
        if path.suffix.lower() == ".json":
            blob = _load_json(path)
            if not isinstance(blob, (Mapping, list)):
                continue
            names: dict[str, list[float]] = {}
            factor_names: list[str] = []
            raw = blob
            if isinstance(blob, Mapping):
                factor_names = [str(x) for x in (blob.get("factors") or blob.get("factor_names") or [])]
                raw = blob.get("loadings") or blob.get("names") or blob
            if isinstance(raw, Mapping):
                for ticker, vec in raw.items():
                    if str(ticker).lower() in {"asof", "meta", "factors", "factor_names", "kind", "version", "loadings", "names"}:
                        continue
                    nums = _as_vector(vec)
                    if nums:
                        names[_ticker_key(str(ticker))] = nums
            elif isinstance(raw, list):
                for rec in raw:
                    if not isinstance(rec, Mapping):
                        continue
                    ticker = rec.get("ticker") or rec.get("name") or rec.get("t")
                    nums = _as_vector(rec.get("loadings") or rec.get("values") or rec.get("pcs"))
                    if ticker and nums:
                        names[_ticker_key(str(ticker))] = nums
            if names:
                width = max(len(v) for v in names.values())
                if not factor_names or len(factor_names) != width:
                    factor_names = [f"F{i+1}" for i in range(width)]
                return names, factor_names, rel
            continue
        rows = _load_csv_rows(path)
        if not rows:
            continue
        header = [(_norm_header(h) if h else "") for h in rows[0].keys()]
        long_fmt = any(h in {"loading", "load", "value", "weight"} for h in header) and any(
            h in {"factor", "pc", "component"} for h in header
        )
        names = {}
        if long_fmt:
            buckets: dict[str, dict[str, float]] = {}
            factors: set[str] = set()
            for row in rows:
                ticker = _ticker_key(
                    str(row.get("ticker") or row.get("name") or row.get("symbol") or row.get("t") or "")
                )
                fac = str(row.get("factor") or row.get("pc") or row.get("component") or row.get("name") or "").strip()
                val = _finite(row.get("loading") or row.get("load") or row.get("value") or row.get("weight"))
                if not ticker or not fac or val is None:
                    continue
                buckets.setdefault(ticker, {})[fac] = val
                factors.add(fac)
            factor_names = sorted(factors)
            for ticker, vec in buckets.items():
                names[ticker] = [float(vec.get(f, 0.0)) for f in factor_names]
            if names:
                return names, factor_names, rel
        else:
            tkr_col = next(
                (c for c in rows[0].keys() if _norm_header(c) in TICKER_ALIASES),
                next(iter(rows[0].keys()), None),
            )
            if not tkr_col:
                continue
            fac_cols = [c for c in rows[0].keys() if c != tkr_col]
            factor_names = list(fac_cols)
            for row in rows:
                ticker = _ticker_key(str(row.get(tkr_col) or ""))
                if not ticker:
                    continue
                nums: list[float] = []
                ok = True
                for col in fac_cols:
                    val = _finite(row.get(col))
                    if val is None:
                        ok = False
                        break
                    nums.append(val)
                if ok and nums:
                    names[ticker] = nums
            if names:
                return names, factor_names or [f"F{i+1}" for i in range(len(next(iter(names.values()))))], rel
    return {}, [], None


def _load_membership(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for rel in MEMBERSHIP_CANDIDATES:
        path = root / rel
        if not path.is_file():
            continue
        for row in _load_csv_rows(path):
            ticker = _ticker_key(str(row.get("ticker") or row.get("name") or row.get("t") or ""))
            fac = str(row.get("factor") or row.get("group") or row.get("pc") or row.get("flag") or "").strip()
            if ticker and fac:
                out[ticker] = fac
        if out:
            return out
    return out


def _load_mom_hist(root: Path) -> dict[str, float]:
    out: dict[str, float] = {}
    for rel in MOM_HIST_CANDIDATES:
        path = root / rel
        if not path.is_file():
            continue
        if path.suffix.lower() == ".json":
            blob = _load_json(path)
            if not isinstance(blob, Mapping):
                continue
            names = blob.get("names") if isinstance(blob.get("names"), Mapping) else blob
            if not isinstance(names, Mapping):
                continue
            for ticker, rec in names.items():
                if str(ticker).lower() in {"asof", "meta", "version", "kind"}:
                    continue
                key = _ticker_key(str(ticker))
                score = None
                if isinstance(rec, Mapping):
                    score, _ = resolve_card_score(rec)
                    if score is None:
                        score = _finite(rec.get("value"))
                    series = rec.get("series") or rec.get("hist") or rec.get("values")
                    if score is None and isinstance(series, list) and series:
                        last = series[-1]
                        if isinstance(last, Mapping):
                            score = _finite(last.get("score") or last.get("value") or last.get("mom_score"))
                        elif isinstance(last, (list, tuple)) and len(last) >= 2:
                            score = _finite(last[1])
                        else:
                            score = _finite(last)
                else:
                    score = _finite(rec)
                if key and score is not None:
                    out[key] = score
            if out:
                return out
            continue
        for row in _load_csv_rows(path):
            ticker = _ticker_key(str(row.get("ticker") or row.get("name") or row.get("t") or ""))
            score, _ = resolve_card_score(row)
            if ticker and score is not None:
                out[ticker] = score
        if out:
            return out
    return out


def _load_prices_csv(root: Path) -> dict[str, float]:
    out: dict[str, float] = {}
    for rel in PRICE_CANDIDATES:
        path = root / rel
        if not path.is_file():
            continue
        rows = _load_csv_rows(path)
        if not rows:
            continue
        cols = list(rows[0].keys())
        tkr_col = next((c for c in cols if _norm_header(c) in TICKER_ALIASES), None)
        px_col = next((c for c in cols if _norm_header(c) in {k.lower() for k in PX_KEYS} | {"adj_close", "adjclose"}), None)
        if tkr_col and px_col:
            for row in rows:
                ticker = _ticker_key(str(row.get(tkr_col) or ""))
                px = _finite(row.get(px_col))
                if ticker and px is not None and px > 0:
                    out[ticker] = px
            if out:
                return out
        # Wide: date + ticker columns
        date_col = next((c for c in cols if _norm_header(c) in {"date", "asof", "day", "dt"}), None)
        if date_col:
            last = rows[-1]
            for col in cols:
                if col == date_col:
                    continue
                ticker = _ticker_key(col)
                px = _finite(last.get(col))
                if ticker and px is not None and px > 0:
                    out[ticker] = px
            if out:
                return out
    return out


def load_desk_book(root: Path | None = None) -> dict[str, Any]:
    """Universe + last px + mom + metrics + loadings already on the desk."""
    base = Path(root) if root is not None else HERE
    names: dict[str, dict[str, Any]] = {}

    enrich = dapi_enrich.load_enrichment(base / dapi_enrich.ENRICH_FILENAME)
    if enrich:
        _ingest_name_map(names, enrich)

    for rel in CARD_JSON_CANDIDATES:
        path = base / rel
        if path.is_file():
            blob = _load_json(path)
            if blob is not None:
                _ingest_name_map(names, blob)

    for rel in UNIVERSE_FILES:
        path = base / rel
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            key = _ticker_key(line)
            if key:
                names.setdefault(key, _blank_name(key))["in_universe"] = True

    discovered = dapi_enrich.discover_tickers(base)
    for t in discovered:
        key = _ticker_key(t)
        if key:
            names.setdefault(key, _blank_name(key))["in_universe"] = True

    for ticker, score in _load_mom_hist(base).items():
        rec = names.setdefault(ticker, _blank_name(ticker))
        rec["in_universe"] = True
        if rec.get("mom_score") is None:
            rec["mom_score"] = score
            rec["mom_source"] = rec.get("mom_source") or "mom_score_hist"
            rec.setdefault("metrics", {})["mom_score"] = score

    for ticker, px in _load_prices_csv(base).items():
        rec = names.setdefault(ticker, _blank_name(ticker))
        rec["in_universe"] = True
        if rec.get("px_last") is None and px > 0:
            rec["px_last"] = px
            rec["px_source"] = rec.get("px_source") or "prices.csv"

    loadings, factor_names, loadings_source = load_loadings(base)
    for ticker, vec in loadings.items():
        rec = names.setdefault(ticker, _blank_name(ticker))
        rec["in_universe"] = True
        rec["loadings"] = vec

    for ticker, fac in _load_membership(base).items():
        rec = names.setdefault(ticker, _blank_name(ticker))
        rec["in_universe"] = True
        if not rec.get("factor"):
            rec["factor"] = fac

    universe = {k for k, v in names.items() if v.get("in_universe")}
    return {
        "asof": (enrich or {}).get("asof") if isinstance(enrich, Mapping) else None,
        "names": names,
        "universe": sorted(universe),
        "universe_n": len(universe),
        "factor_names": factor_names,
        "loadings_source": loadings_source,
        "weight_def": WEIGHT_DEF,
    }


def lookup_px_local(ticker: str, desk: Mapping[str, Any] | None) -> tuple[float | None, str | None]:
    key = _ticker_key(ticker)
    names = (desk or {}).get("names") if isinstance(desk, Mapping) else None
    rec = None
    if isinstance(names, Mapping):
        rec = names.get(key) or names.get(ticker)
    if isinstance(rec, Mapping):
        px = _finite(rec.get("px_last"))
        if px is not None and px > 0:
            return px, str(rec.get("px_source") or "desk")
    return None, None


def quote_last(
    ticker: str,
    *,
    root: Path | None = None,
    desk: Mapping[str, Any] | None = None,
    session: Any | None = None,
    allow_dapi: bool = True,
) -> dict[str, Any]:
    """Last px from desk snapshot, then existing DAPI ``PX_LAST`` candidates."""
    key = _ticker_key(ticker)
    if desk is None:
        desk = load_desk_book(root)
    px, src = lookup_px_local(key, desk)
    if px is not None:
        return {"ok": True, "ticker": key, "t": _short(key), "px_last": px, "source": src}
    if not allow_dapi:
        return {
            "ok": False,
            "ticker": key,
            "t": _short(key),
            "px_last": None,
            "source": None,
            "error": "no_desk_px",
        }
    wrapped = dapi_enrich.wrap_session(session) if session is not None else None
    if wrapped is None:
        try:
            wrapped = dapi_enrich.open_dapi_session()
        except dapi_enrich.CapacityError as exc:
            return {
                "ok": False,
                "ticker": key,
                "t": _short(key),
                "px_last": None,
                "source": None,
                "error": f"capacity:{exc.reason}",
            }
        except Exception as exc:  # noqa: BLE001
            wrapped = None
            LOG.info("quote session open failed: %s", exc)
    if wrapped is None:
        return {
            "ok": False,
            "ticker": key,
            "t": _short(key),
            "px_last": None,
            "source": None,
            "error": "no_dapi_session",
        }
    fields = dapi_enrich.flatten_candidates(["px_last"])
    try:
        rows = wrapped.refdata([key], fields)
    except dapi_enrich.CapacityError as exc:
        return {
            "ok": False,
            "ticker": key,
            "t": _short(key),
            "px_last": None,
            "source": None,
            "error": f"capacity:{exc.reason}",
        }
    except Exception as exc:  # noqa: BLE001
        LOG.warning("quote refdata failed for %s: %s", key, exc)
        return {
            "ok": False,
            "ticker": key,
            "t": _short(key),
            "px_last": None,
            "source": None,
            "error": str(exc),
        }
    raw = rows.get(key) or rows.get(ticker) or {}
    value, field, reason = dapi_enrich.first_success(raw, "px_last")
    if value is None or _finite(value) is None or float(value) <= 0:
        return {
            "ok": False,
            "ticker": key,
            "t": _short(key),
            "px_last": None,
            "source": None,
            "error": reason or "no_px_last",
        }
    return {
        "ok": True,
        "ticker": key,
        "t": _short(key),
        "px_last": float(value),
        "source": field or "dapi",
    }


def quote_many(
    tickers: Sequence[str],
    *,
    root: Path | None = None,
    desk: Mapping[str, Any] | None = None,
    session: Any | None = None,
    allow_dapi: bool = True,
) -> dict[str, dict[str, Any]]:
    if desk is None:
        desk = load_desk_book(root)
    out: dict[str, dict[str, Any]] = {}
    need: list[str] = []
    for raw in tickers:
        key = _ticker_key(raw)
        if not key or key in out:
            continue
        px, src = lookup_px_local(key, desk)
        if px is not None:
            out[key] = {"ok": True, "ticker": key, "t": _short(key), "px_last": px, "source": src}
        else:
            need.append(key)
    if not need or not allow_dapi:
        for key in need:
            out[key] = {
                "ok": False,
                "ticker": key,
                "t": _short(key),
                "px_last": None,
                "source": None,
                "error": "no_desk_px",
            }
        return out
    wrapped = dapi_enrich.wrap_session(session) if session is not None else None
    if wrapped is None:
        try:
            wrapped = dapi_enrich.open_dapi_session()
        except Exception as exc:  # noqa: BLE001
            LOG.info("batch quote session: %s", exc)
            wrapped = None
    if wrapped is None:
        for key in need:
            out[key] = {
                "ok": False,
                "ticker": key,
                "t": _short(key),
                "px_last": None,
                "source": None,
                "error": "no_dapi_session",
            }
        return out
    fields = dapi_enrich.flatten_candidates(["px_last"])
    try:
        rows = wrapped.refdata(need, fields)
    except Exception as exc:  # noqa: BLE001
        LOG.warning("batch quote failed: %s", exc)
        for key in need:
            out[key] = {
                "ok": False,
                "ticker": key,
                "t": _short(key),
                "px_last": None,
                "source": None,
                "error": str(exc),
            }
        return out
    for key in need:
        raw = rows.get(key) or {}
        value, field, reason = dapi_enrich.first_success(raw, "px_last")
        px = _finite(value)
        if px is None or px <= 0:
            out[key] = {
                "ok": False,
                "ticker": key,
                "t": _short(key),
                "px_last": None,
                "source": None,
                "error": reason or "no_px_last",
            }
        else:
            out[key] = {
                "ok": True,
                "ticker": key,
                "t": _short(key),
                "px_last": px,
                "source": field or "dapi",
            }
    return out


# ---------------------------------------------------------------------------
# Weights + scorecard
# ---------------------------------------------------------------------------


def apply_weights(
    rows: Sequence[Mapping[str, Any]],
    quotes: Mapping[str, Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    """Attach notional / gross weight. Unpriced names stay listed, weight 0."""
    priced: list[dict[str, Any]] = []
    unpriced: list[str] = []
    for raw in rows:
        rec = dict(raw)
        key = _ticker_key(str(rec.get("ticker") or ""))
        rec["ticker"] = key
        rec["t"] = _short(key)
        q = quotes.get(key) or {}
        px = _finite(q.get("px_last"))
        rec["px_last"] = px
        rec["px_source"] = q.get("source")
        shares = _finite(rec.get("position")) or 0.0
        rec["position"] = shares
        if px is None or px <= 0:
            rec["notional"] = None
            rec["weight"] = 0.0
            rec["side"] = "long" if shares > 0 else "short"
            unpriced.append(key)
            priced.append(rec)
            continue
        notional = shares * px
        rec["notional"] = notional
        rec["side"] = "long" if notional >= 0 else "short"
        priced.append(rec)

    gross = sum(abs(float(r["notional"])) for r in priced if r.get("notional") is not None)
    net = sum(float(r["notional"]) for r in priced if r.get("notional") is not None)
    long_n = sum(max(float(r["notional"]), 0.0) for r in priced if r.get("notional") is not None)
    short_n = sum(min(float(r["notional"]), 0.0) for r in priced if r.get("notional") is not None)
    for rec in priced:
        if rec.get("notional") is None or gross <= 0:
            rec["weight"] = 0.0
        else:
            rec["weight"] = float(rec["notional"]) / gross
    exposure = {
        "gross": gross,
        "net": net,
        "net_gross": (net / gross) if gross else None,
        "long": long_n,
        "short": short_n,
        "n_priced": sum(1 for r in priced if r.get("notional") is not None),
        "n_unpriced": len(unpriced),
        "n": len(priced),
        "weight_def": WEIGHT_DEF,
    }
    return priced, exposure, unpriced


def _desk_rec(desk: Mapping[str, Any], ticker: str) -> dict[str, Any] | None:
    names = desk.get("names") if isinstance(desk, Mapping) else None
    if not isinstance(names, Mapping):
        return None
    rec = names.get(_ticker_key(ticker)) or names.get(ticker)
    return dict(rec) if isinstance(rec, Mapping) else None


def _quintile_bounds(values: Sequence[float]) -> tuple[float, float] | None:
    nums = sorted(float(v) for v in values if _finite(v) is not None)
    if len(nums) < 5:
        return None
    n = len(nums)

    def _at(p: float) -> float:
        if n == 1:
            return nums[0]
        idx = min(n - 1, max(0, int(round(p * (n - 1)))))
        return nums[idx]

    return _at(0.20), _at(0.80)


def _top_contrib(items: Sequence[Mapping[str, Any]], key: str, n: int = 3) -> list[dict[str, Any]]:
    ranked = sorted(items, key=lambda r: abs(float(r.get(key) or 0.0)), reverse=True)
    out: list[dict[str, Any]] = []
    for rec in ranked[:n]:
        out.append(
            {
                "t": rec.get("t") or _short(str(rec.get("ticker") or "")),
                "ticker": rec.get("ticker"),
                "weight": rec.get("weight"),
                "value": rec.get("value"),
                "contrib": rec.get(key),
            }
        )
    return out


def _factor_decomp(
    rows: Sequence[Mapping[str, Any]],
    desk: Mapping[str, Any],
) -> dict[str, Any]:
    factor_names = list(desk.get("factor_names") or [])
    source = desk.get("loadings_source")
    exposures: dict[str, float] = {}
    contribs: dict[str, list[dict[str, Any]]] = {}
    covered = 0.0
    missing_load = 0.0
    width = 0
    for rec in rows:
        w = float(rec.get("weight") or 0.0)
        drec = _desk_rec(desk, str(rec.get("ticker") or ""))
        vec = (drec or {}).get("loadings") if drec else None
        if not vec:
            missing_load += abs(w)
            continue
        covered += abs(w)
        width = max(width, len(vec))
        if not factor_names or len(factor_names) < width:
            factor_names = [f"F{i+1}" for i in range(width)]
        for i, loading in enumerate(vec):
            name = factor_names[i] if i < len(factor_names) else f"F{i+1}"
            c = w * float(loading)
            exposures[name] = exposures.get(name, 0.0) + c
            contribs.setdefault(name, []).append(
                {
                    "t": rec.get("t"),
                    "ticker": rec.get("ticker"),
                    "weight": w,
                    "value": float(loading),
                    "contrib": c,
                }
            )
    factors = []
    for name in factor_names or sorted(exposures):
        factors.append(
            {
                "name": name,
                "exposure": exposures.get(name, 0.0),
                "contributors": _top_contrib(contribs.get(name, []), "contrib"),
            }
        )
    # Categorical FLAGS-style factor membership (% of gross).
    buckets: dict[str, float] = {}
    cat_covered = 0.0
    for rec in rows:
        w = abs(float(rec.get("weight") or 0.0))
        drec = _desk_rec(desk, str(rec.get("ticker") or ""))
        label = (drec or {}).get("factor") if drec else None
        if not label:
            continue
        cat_covered += w
        buckets[str(label)] = buckets.get(str(label), 0.0) + w
    membership = [
        {"name": k, "gross_pct": v}
        for k, v in sorted(buckets.items(), key=lambda kv: -kv[1])
    ]
    return {
        "source": source,
        "model": "existing desk SparsePCA / v0 loadings — not a new fit",
        "coverage": covered,
        "missing_loadings_gross": missing_load,
        "factors": factors,
        "membership": membership,
        "membership_coverage": cat_covered,
        "empty_reason": None
        if (factors or membership)
        else "no factor loadings / FLAGS membership on disk — not inventing a model",
    }


def _weighted_mom(rows: Sequence[Mapping[str, Any]], desk: Mapping[str, Any]) -> dict[str, Any]:
    covered_abs = 0.0
    acc = 0.0
    contribs: list[dict[str, Any]] = []
    missing = 0.0
    for rec in rows:
        w = float(rec.get("weight") or 0.0)
        drec = _desk_rec(desk, str(rec.get("ticker") or ""))
        mom = None
        src = None
        if drec:
            mom = _finite(drec.get("mom_score"))
            src = drec.get("mom_source")
            if mom is None:
                mom, src = resolve_card_score(drec)
        if mom is None:
            missing += abs(w)
            continue
        covered_abs += abs(w)
        acc += w * mom
        contribs.append(
            {
                "t": rec.get("t"),
                "ticker": rec.get("ticker"),
                "weight": w,
                "value": mom,
                "contrib": w * mom,
                "source": src,
            }
        )
    score = (acc / covered_abs) if covered_abs > 0 else None
    return {
        "score": score,
        "coverage": covered_abs,
        "missing_gross": missing,
        "definition": (
            "Same mom_score as FLAGS / MOM Up-Down cards: mom_score / "
            "momentum_score / mom_rank / trend_rank, else score in [0, 20]. "
            "Not options score_v2. Gross-weighted (shorts flip sign)."
        ),
        "contributors": _top_contrib(contribs, "contrib", n=5),
    }


def _metric_scorecard(rows: Sequence[Mapping[str, Any]], desk: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    names = desk.get("names") if isinstance(desk.get("names"), Mapping) else {}
    universe_vals: dict[str, list[float]] = {k: [] for k, _ in NUMERIC_METRICS}
    for rec in names.values() if isinstance(names, Mapping) else []:
        if not isinstance(rec, Mapping) or not rec.get("in_universe"):
            continue
        metrics = rec.get("metrics") if isinstance(rec.get("metrics"), Mapping) else {}
        for dest, _keys in NUMERIC_METRICS:
            val = _finite((metrics or {}).get(dest))
            if dest == "mom_score" and val is None:
                val = _finite(rec.get("mom_score"))
            if val is not None:
                universe_vals[dest].append(val)

    numeric: list[dict[str, Any]] = []
    for dest, _keys in NUMERIC_METRICS:
        contribs: list[dict[str, Any]] = []
        covered = 0.0
        acc = 0.0
        q1_gross = 0.0
        q5_gross = 0.0
        bounds = _quintile_bounds(universe_vals.get(dest) or [])
        for rec in rows:
            w = float(rec.get("weight") or 0.0)
            drec = _desk_rec(desk, str(rec.get("ticker") or ""))
            metrics = (drec or {}).get("metrics") if isinstance((drec or {}).get("metrics"), Mapping) else {}
            val = _finite((metrics or {}).get(dest))
            if dest == "mom_score" and val is None and drec:
                val = _finite(drec.get("mom_score"))
            if val is None:
                continue
            covered += abs(w)
            acc += w * val
            contribs.append(
                {
                    "t": rec.get("t"),
                    "ticker": rec.get("ticker"),
                    "weight": w,
                    "value": val,
                    "contrib": w * val,
                }
            )
            if bounds:
                lo, hi = bounds
                if val <= lo:
                    q1_gross += abs(w)
                if val >= hi:
                    q5_gross += abs(w)
        if covered <= 0:
            continue
        numeric.append(
            {
                "key": dest,
                "weighted_avg": acc / covered,
                "coverage": covered,
                "q1_gross": q1_gross,
                "q5_gross": q5_gross,
                "desk_n": len(universe_vals.get(dest) or []),
                "contributors": _top_contrib(contribs, "contrib"),
            }
        )

    categoricals: list[dict[str, Any]] = []
    cat_keys = [k for k, _ in CATEGORICAL_METRICS] + ["pill"]
    for dest in cat_keys:
        buckets: dict[str, float] = {}
        covered = 0.0
        examples: dict[str, list[str]] = {}
        for rec in rows:
            w = abs(float(rec.get("weight") or 0.0))
            drec = _desk_rec(desk, str(rec.get("ticker") or ""))
            labels: list[str] = []
            if dest == "gics":
                if drec and drec.get("gics"):
                    labels = [str(drec["gics"])]
            elif dest == "factor":
                if drec and drec.get("factor"):
                    labels = [str(drec["factor"])]
            elif dest == "pill":
                pills = (drec or {}).get("pills") or []
                labels = [str(p) for p in pills if p]
            else:
                metrics = (drec or {}).get("metrics") if isinstance((drec or {}).get("metrics"), Mapping) else {}
                val = (metrics or {}).get(dest)
                if val not in (None, "", "—"):
                    labels = [str(val)]
            if not labels:
                continue
            if dest != "pill":
                covered += w
            for label in labels:
                if dest == "pill":
                    # flag share of gross (a name can contribute to several pills)
                    buckets[label] = buckets.get(label, 0.0) + w
                    examples.setdefault(label, []).append(str(rec.get("t") or ""))
                else:
                    buckets[label] = buckets.get(label, 0.0) + w
                    examples.setdefault(label, []).append(str(rec.get("t") or ""))
            if dest == "pill":
                covered += w
        if not buckets:
            continue
        categoricals.append(
            {
                "key": dest,
                "coverage": covered,
                "buckets": [
                    {
                        "name": name,
                        "gross_pct": pct,
                        "names": examples.get(name, [])[:4],
                    }
                    for name, pct in sorted(buckets.items(), key=lambda kv: -kv[1])
                ],
            }
        )
    return numeric, categoricals


def score_portfolio(
    rows: Sequence[Mapping[str, Any]],
    *,
    root: Path | None = None,
    desk: Mapping[str, Any] | None = None,
    quotes: Mapping[str, Mapping[str, Any]] | None = None,
    session: Any | None = None,
    allow_dapi: bool = True,
    persist: bool = True,
) -> dict[str, Any]:
    """Full look-here scorecard. Never auto-trades."""
    if desk is None:
        desk = load_desk_book(root)
    parsed = [dict(r) for r in rows]
    tickers = [_ticker_key(str(r.get("ticker") or "")) for r in parsed]
    if quotes is None:
        quotes = quote_many(tickers, root=root, desk=desk, session=session, allow_dapi=allow_dapi)
    weighted, exposure, unpriced = apply_weights(parsed, quotes)

    universe = set(desk.get("universe") or [])
    if not universe:
        universe = {k for k, v in (desk.get("names") or {}).items() if isinstance(v, Mapping) and v.get("in_universe")}
    missing = []
    for rec in weighted:
        key = rec["ticker"]
        in_book = key in universe
        rec["in_universe"] = in_book
        drec = _desk_rec(desk, key)
        rec["mom_score"] = (drec or {}).get("mom_score") if drec else None
        rec["mom_source"] = (drec or {}).get("mom_source") if drec else None
        rec["gics"] = (drec or {}).get("gics") if drec else None
        rec["factor"] = (drec or {}).get("factor") if drec else None
        rec["has_loadings"] = bool((drec or {}).get("loadings")) if drec else False
        rec["pills"] = list((drec or {}).get("pills") or []) if drec else []
        if not in_book:
            missing.append(
                {
                    "ticker": key,
                    "t": rec.get("t"),
                    "position": rec.get("position"),
                    "priced": rec.get("notional") is not None,
                    "px_last": rec.get("px_last"),
                    "note": "not in current desk universe — factor/mom n/a until in book",
                }
            )

    mom = _weighted_mom(weighted, desk)
    factors = _factor_decomp(weighted, desk)
    metrics, categoricals = _metric_scorecard(weighted, desk)

    card = {
        "version": BOOK_VERSION,
        "asof": _now_iso(),
        "ok": True,
        "advice": "look-here scorecard · not a trade · Tanner decides",
        "weight_def": WEIGHT_DEF,
        "rows": weighted,
        "missing": missing,
        "unpriced": unpriced,
        "exposure": exposure,
        "mom": mom,
        "factors": factors,
        "metrics": metrics,
        "categoricals": categoricals,
        "n": len(weighted),
        "universe_n": desk.get("universe_n"),
        "desk_asof": desk.get("asof"),
        "error": None,
        "csv": None,
    }
    if persist:
        try:
            save_last(card, root=root)
        except OSError as exc:
            LOG.warning("could not persist %s: %s", BOOK_FILENAME, exc)
            card["persist_error"] = str(exc)
    return card


def save_last(card: Mapping[str, Any], root: Path | None = None) -> Path:
    path = default_book_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(card)
    payload["version"] = BOOK_VERSION
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def load_last(root: Path | None = None) -> dict[str, Any] | None:
    path = default_book_path(root)
    if not path.is_file():
        return None
    blob = _load_json(path)
    return blob if isinstance(blob, dict) else None


def run_from_text(
    text: str,
    *,
    root: Path | None = None,
    session: Any | None = None,
    allow_dapi: bool = True,
    persist: bool = True,
    desk: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    rows = parse_table(text)
    card = score_portfolio(
        rows,
        root=root,
        desk=desk,
        session=session,
        allow_dapi=allow_dapi,
        persist=persist,
    )
    card["csv"] = text
    if persist:
        try:
            save_last(card, root=root)
        except OSError:
            pass
    return card


def desk_snapshot(root: Path | None = None) -> dict[str, Any]:
    """Slim JSON for the HTML tab (client fallback when sidecar is down)."""
    desk = load_desk_book(root)
    names_out: dict[str, Any] = {}
    for ticker, rec in (desk.get("names") or {}).items():
        if not isinstance(rec, Mapping):
            continue
        names_out[ticker] = {
            "t": rec.get("t") or _short(ticker),
            "in_universe": bool(rec.get("in_universe")),
            "px_last": rec.get("px_last"),
            "px_source": rec.get("px_source"),
            "mom_score": rec.get("mom_score"),
            "mom_source": rec.get("mom_source"),
            "metrics": rec.get("metrics") or {},
            "pills": rec.get("pills") or [],
            "gics": rec.get("gics"),
            "factor": rec.get("factor"),
            "loadings": rec.get("loadings"),
        }
    return {
        "asof": desk.get("asof"),
        "universe_n": desk.get("universe_n"),
        "factor_names": desk.get("factor_names") or [],
        "loadings_source": desk.get("loadings_source"),
        "weight_def": WEIGHT_DEF,
        "names": names_out,
    }


# ---------------------------------------------------------------------------
# Sidecar HTTP
# ---------------------------------------------------------------------------


def _send_json(handler: BaseHTTPRequestHandler, code: int, payload: Any) -> None:
    blob = json.dumps(payload, default=str).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type")
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(blob)))
    handler.end_headers()
    handler.wfile.write(blob)


def handle_quote_request(handler: BaseHTTPRequestHandler, parsed) -> None:
    path = parsed.path
    query = parse_qs(parsed.query)
    tickers: list[str] = []
    if path.startswith("/api/quote/"):
        raw = unquote(path[len("/api/quote/") :]).strip()
        if raw:
            tickers.append(raw)
    for key in ("ticker", "tickers"):
        for chunk in query.get(key, []):
            tickers.extend(t.strip() for t in unquote(chunk).split(",") if t.strip())
    if not tickers:
        _send_json(handler, 400, {"ok": False, "error": "need ticker"})
        return
    root = resolve_root()
    if len(tickers) == 1:
        _send_json(handler, 200, quote_last(tickers[0], root=root))
        return
    quotes = quote_many(tickers, root=root)
    _send_json(handler, 200, {"ok": True, "quotes": quotes, "n": len(quotes)})


def handle_portfolio_get(handler: BaseHTTPRequestHandler) -> None:
    last = load_last(resolve_root()) or empty_scorecard()
    _send_json(handler, 200, last)


def handle_portfolio_run(handler: BaseHTTPRequestHandler, body: Any) -> None:
    text = ""
    rows = None
    allow_dapi = True
    root = resolve_root()
    if isinstance(body, dict):
        text = str(body.get("csv") or body.get("text") or body.get("paste") or "")
        if isinstance(body.get("rows"), list):
            rows = body.get("rows")
        if "quote" in body:
            allow_dapi = bool(body.get("quote"))
        if "allow_dapi" in body:
            allow_dapi = bool(body.get("allow_dapi"))
    if not text and not rows:
        _send_json(handler, 400, {"ok": False, "error": "need csv or rows"})
        return
    try:
        parsed = parse_rows(rows) if rows and not text else parse_table(text)
        card = score_portfolio(parsed, root=root, allow_dapi=allow_dapi, persist=True)
        card["csv"] = text or None
        save_last(card, root=root)
        _send_json(handler, 200, card)
    except ParseError as exc:
        _send_json(handler, 400, {"ok": False, "error": str(exc)})
    except Exception as exc:  # noqa: BLE001
        LOG.exception("portfolio run failed")
        _send_json(handler, 500, {"ok": False, "error": str(exc)})


# ---------------------------------------------------------------------------
# HTML embed — Portfolio tab
# ---------------------------------------------------------------------------


def panes_html() -> str:
    note = html.escape("look-here scorecard · not a trade · Tanner decides")
    wdef = html.escape(WEIGHT_DEF)
    return f"""
<div id="{VIEW_ID}" class="view-pane hide" data-view="portfolio" hidden>
  <div class="ph">Portfolio</div>
  <p class="fd-pf-kicker">{note}</p>
  <div class="fd-pf-strip">
    <label class="fd-pf-file">Upload CSV<input type="file" id="fd-pf-file" accept=".csv,text/csv,text/plain"></label>
    <textarea id="fd-pf-paste" rows="6" spellcheck="false" placeholder="position,ticker&#10;100,AAPL&#10;-40,MSFT US Equity"></textarea>
    <button type="button" class="btn" id="fd-pf-run">Run</button>
  </div>
  <p class="fd-pf-hint">Two columns: <code>position</code> = signed shares (long +, short −), <code>ticker</code> = Bloomberg yellow key. Bare <code>AAPL</code> → <code>AAPL US Equity</code>. {wdef}</p>
  <p class="fd-pf-status" id="fd-pf-status"></p>
  <div id="fd-pf-score" class="fd-pf-score"></div>
</div>
""".strip()


def strip_css() -> str:
    return f"""
#{VIEW_ID},
#{VIEW_ID}.hide,
#{VIEW_ID}[hidden] {{
  display: none !important;
}}
body[data-fd-pf="1"] #{VIEW_ID}.fd-pf-on:not(.hide):not([hidden]) {{
  display: block !important;
}}
.{HID_CLASS} {{ display: none !important; }}
#{VIEW_ID}.fd-pf-on {{ margin: 0 0 16px; }}
#{VIEW_ID} .ph {{
  font: 650 13px/1.2 "Segoe UI", "DejaVu Sans", sans-serif;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: #93c5fd;
  margin: 0 0 6px;
}}
.fd-pf-kicker {{
  margin: 0 0 10px;
  color: #fcd34d;
  font: 650 11px/1.35 "Segoe UI", sans-serif;
  letter-spacing: 0.04em;
}}
.fd-pf-strip {{
  display: grid;
  grid-template-columns: auto 1fr auto;
  gap: 8px;
  align-items: stretch;
  border: 1px solid #1f2937;
  padding: 8px;
  margin: 0 0 8px;
  background: #0b0f14;
}}
.fd-pf-file {{
  display: flex;
  flex-direction: column;
  justify-content: center;
  font: 650 10px/1.2 "Segoe UI", sans-serif;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: #9ca3af;
  border: 1px dashed #374151;
  padding: 8px 10px;
  min-width: 110px;
  cursor: pointer;
}}
.fd-pf-file input {{ display: none; }}
#fd-pf-paste {{
  width: 100%;
  min-height: 88px;
  resize: vertical;
  background: #111827;
  color: #e5e7eb;
  border: 1px solid #1f2937;
  font: 12px/1.4 ui-monospace, "Cascadia Mono", "Consolas", monospace;
  padding: 8px;
}}
#fd-pf-run {{
  align-self: end;
  min-width: 72px;
  height: 32px;
  font: 650 12px/1 "Segoe UI", sans-serif;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: #93c5fd;
  border: 1px solid #3b82f6;
  background: #0b1220;
  cursor: pointer;
}}
.fd-pf-hint, .fd-pf-status {{
  margin: 0 0 8px;
  color: #9ca3af;
  font-size: 11px;
}}
.fd-pf-hint code {{ color: #d1d5db; }}
.fd-pf-exp {{
  display: flex;
  flex-wrap: wrap;
  gap: 14px 22px;
  border-top: 1px solid #1f2937;
  border-bottom: 1px solid #1f2937;
  padding: 8px 0;
  margin: 0 0 12px;
  font-variant-numeric: tabular-nums;
}}
.fd-pf-exp .k {{
  display: block;
  font: 650 9px/1.2 "Segoe UI", sans-serif;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: #6b7280;
}}
.fd-pf-exp .v {{
  font: 650 14px/1.3 "Segoe UI", sans-serif;
  color: #e5e7eb;
}}
.fd-pf-miss {{ margin: 0 0 10px; }}
.fd-pf-table {{
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  margin: 0 0 14px;
}}
.fd-pf-table th {{
  text-align: left;
  font: 650 9px/1.2 "Segoe UI", sans-serif;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: #6b7280;
  border-bottom: 1px solid #1f2937;
  padding: 4px 8px 4px 0;
}}
.fd-pf-table td {{
  border-bottom: 1px solid #111827;
  padding: 4px 8px 4px 0;
  color: #d1d5db;
}}
.fd-pf-table td.num, .fd-pf-table th.num {{ text-align: right; }}
.fd-pf-bar {{
  display: inline-block;
  height: 6px;
  background: #1d4ed8;
  vertical-align: middle;
  margin-right: 8px;
}}
.fd-pf-bar.neg {{ background: #be123c; }}
.fd-pf-h {{
  font: 650 11px/1.2 "Segoe UI", sans-serif;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: #93c5fd;
  margin: 14px 0 6px;
}}
.nav-btn[data-view="portfolio"].is-on,
.nav-btn[data-view="portfolio"].on,
.btn.nav-btn[data-view="portfolio"].on,
.nav-btn[data-fd-portfolio="1"].is-on,
.nav-btn[data-fd-portfolio="1"].on {{
  color: #93c5fd; border-color: #3b82f6; background: #0b1220;
}}
.badge.fd-pf-miss, .spike-chip.fd-pf-miss {{ color: #fda4af; border-color: #fb7185; }}
.badge.fd-pf-ok, .spike-chip.fd-pf-ok {{ color: #6ee7b7; border-color: #34d399; }}
.badge.fd-pf-warn, .spike-chip.fd-pf-warn {{ color: #fcd34d; border-color: #f59e0b; }}
""".strip()


def strip_js() -> str:
    native = json.dumps(list(NATIVE_VIEW_IDS))
    return r"""
(function () {
  if (window.__FD_PF_BOUND__) return;
  window.__FD_PF_BOUND__ = true;
  var VIEW = "__VIEW__";
  var NAV = "__NAV__";
  var DB_ID = "__DB__";
  var DESK_ID = "__DESK__";
  var SIDECAR = "__SIDECAR__";
  var LS_KEY = "fd-portfolio-last-v1";
  var NATIVE_VIEWS = __NATIVE__;
  var WEIGHT_DEF = "__WDEF__";

  function $(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c];
    });
  }
  function fmt(v, d) {
    var n = Number(v);
    if (!isFinite(n)) return "—";
    return n.toFixed(d);
  }
  function fmtPx(v) {
    var n = Number(v);
    if (!isFinite(n)) return "—";
    return n.toLocaleString(undefined, { maximumFractionDigits: 2 });
  }
  function fmtUsd(v) {
    var n = Number(v);
    if (!isFinite(n)) return "—";
    var abs = Math.abs(n);
    var sign = n < 0 ? "−" : "";
    if (abs >= 1e9) return sign + "$" + (abs / 1e9).toFixed(2) + "bn";
    if (abs >= 1e6) return sign + "$" + (abs / 1e6).toFixed(2) + "m";
    if (abs >= 1e3) return sign + "$" + (abs / 1e3).toFixed(1) + "k";
    return sign + "$" + abs.toFixed(0);
  }
  function pct(v) {
    var n = Number(v);
    if (!isFinite(n)) return "—";
    return (100 * n).toFixed(1) + "%";
  }
  function readJson(id) {
    var el = $(id);
    if (!el) return {};
    try { return JSON.parse(el.textContent || "{}") || {}; }
    catch (e) { return {}; }
  }
  function desk() { return readJson(DESK_ID); }
  function lastDb() { return readJson(DB_ID); }
  function saveLs(card) {
    try { localStorage.setItem(LS_KEY, JSON.stringify(card)); } catch (e) {}
  }
  function loadLs() {
    try { return JSON.parse(localStorage.getItem(LS_KEY) || "null"); }
    catch (e) { return null; }
  }
  function chip(label, cls) {
    return '<span class="badge spike-chip ' + (cls || "") + '">' + esc(label) + "</span>";
  }
  function status(msg, cls) {
    var el = $("fd-pf-status");
    if (!el) return;
    el.textContent = msg || "";
    el.style.color = cls === "err" ? "#fda4af" : "#9ca3af";
  }
  function contribHtml(list) {
    if (!list || !list.length) return "—";
    return list.map(function (c) {
      var v = Number(c.contrib);
      var sign = v < 0 ? "" : "+";
      return esc(c.t || "") + " " + sign + fmt(v, 3);
    }).join(" · ");
  }
  function paint(card) {
    var host = $("fd-pf-score");
    if (!host) return;
    if (!card || !card.ok) {
      host.innerHTML = card && card.error ? '<p class="fd-pf-status">' + esc(card.error) + "</p>" : "";
      return;
    }
    var miss = (card.missing || []).map(function (m) {
      return chip((m.t || m.ticker) + (m.priced ? " (px ok)" : ""), "fd-pf-miss");
    }).join("");
    var unp = (card.unpriced || []).map(function (t) {
      return chip(String(t).split(/\s+/)[0], "fd-pf-warn");
    }).join("");
    var exp = card.exposure || {};
    var mom = card.mom || {};
    var fac = card.factors || {};
    var html = "";
    html += '<div class="fd-pf-miss">';
    html += chip((card.missing || []).length + " missing", (card.missing || []).length ? "fd-pf-miss" : "fd-pf-ok");
    html += " " + miss;
    if ((card.unpriced || []).length) {
      html += " " + chip(card.unpriced.length + " unpriced", "fd-pf-warn") + " " + unp;
    }
    html += "</div>";
    html += '<div class="fd-pf-exp">';
    html += '<div><span class="k">Net</span><span class="v">' + fmtUsd(exp.net) + '</span></div>';
    html += '<div><span class="k">Gross</span><span class="v">' + fmtUsd(exp.gross) + '</span></div>';
    html += '<div><span class="k">Net / Gross</span><span class="v">' + fmt(exp.net_gross, 3) + '</span></div>';
    html += '<div><span class="k">Long</span><span class="v">' + fmtUsd(exp.long) + '</span></div>';
    html += '<div><span class="k">Short</span><span class="v">' + fmtUsd(exp.short) + '</span></div>';
    html += '<div><span class="k">Names</span><span class="v">' + String(card.n || 0) + '</span></div>';
    html += "</div>";
    html += '<div class="fd-pf-h">Momentum</div>';
    html += '<div class="fd-pf-exp"><div><span class="k">Portfolio mom_score</span><span class="v">' +
      (mom.score == null ? "—" : fmt(mom.score, 2)) + '</span></div>';
    html += '<div><span class="k">Coverage</span><span class="v">' + pct(mom.coverage) + ' gross</span></div></div>';
    html += '<p class="fd-pf-hint">' + esc(mom.definition || "") + " Contributors: " + contribHtml(mom.contributors) + "</p>";
    html += '<div class="fd-pf-h">Factor decomposition</div>';
    html += '<p class="fd-pf-hint">' + esc(fac.model || "") +
      (fac.source ? " · source " + esc(fac.source) : "") +
      (fac.empty_reason ? " · " + esc(fac.empty_reason) : "") + "</p>";
    var factors = fac.factors || [];
    if (factors.length) {
      html += '<table class="fd-pf-table"><thead><tr><th>Factor</th><th class="num">Exposure</th><th>Contributors</th></tr></thead><tbody>';
      var maxAbs = 0;
      factors.forEach(function (f) { maxAbs = Math.max(maxAbs, Math.abs(Number(f.exposure) || 0)); });
      factors.forEach(function (f) {
        var x = Number(f.exposure) || 0;
        var w = maxAbs ? Math.max(4, Math.round(80 * Math.abs(x) / maxAbs)) : 4;
        html += "<tr><td>" + esc(f.name) + '</td><td class="num"><span class="fd-pf-bar' +
          (x < 0 ? " neg" : "") + '" style="width:' + w + 'px"></span>' + fmt(x, 3) +
          "</td><td>" + contribHtml(f.contributors) + "</td></tr>";
      });
      html += "</tbody></table>";
    }
    var mem = fac.membership || [];
    if (mem.length) {
      html += '<p class="fd-pf-hint">FLAGS membership % gross: ' + mem.map(function (m) {
        return esc(m.name) + " " + pct(m.gross_pct);
      }).join(" · ") + "</p>";
    }
    html += '<div class="fd-pf-h">Metrics scorecard</div>';
    html += '<table class="fd-pf-table"><thead><tr><th>Metric</th><th class="num">W.avg</th><th class="num">Q1 %gross</th><th class="num">Q5 %gross</th><th>Largest contrib</th></tr></thead><tbody>';
    (card.metrics || []).forEach(function (m) {
      html += "<tr><td>" + esc(m.key) + '</td><td class="num">' + fmt(m.weighted_avg, 3) +
        '</td><td class="num">' + pct(m.q1_gross) + '</td><td class="num">' + pct(m.q5_gross) +
        "</td><td>" + contribHtml(m.contributors) + "</td></tr>";
    });
    html += "</tbody></table>";
    (card.categoricals || []).forEach(function (c) {
      html += '<p class="fd-pf-hint"><b>' + esc(c.key) + "</b> · " +
        (c.buckets || []).slice(0, 8).map(function (b) {
          return esc(b.name) + " " + pct(b.gross_pct);
        }).join(" · ") + "</p>";
    });
    html += '<div class="fd-pf-h">Positions</div>';
    html += '<table class="fd-pf-table"><thead><tr><th>Ticker</th><th class="num">Shares</th><th class="num">Last</th><th class="num">Notional</th><th class="num">Weight</th><th>Book</th><th>Mom</th></tr></thead><tbody>';
    (card.rows || []).forEach(function (r) {
      html += "<tr><td>" + esc(r.t || r.ticker) + '</td><td class="num">' + fmt(r.position, 0) +
        '</td><td class="num">' + fmtPx(r.px_last) + '</td><td class="num">' + fmtUsd(r.notional) +
        '</td><td class="num">' + pct(r.weight) + "</td><td>" +
        (r.in_universe ? chip("in book", "fd-pf-ok") : chip("missing", "fd-pf-miss")) +
        "</td><td>" + (r.mom_score == null ? "n/a" : fmt(r.mom_score, 1)) + "</td></tr>";
    });
    html += "</tbody></table>";
    html += '<p class="fd-pf-hint">' + esc(card.advice || "") + " · " + esc(WEIGHT_DEF) + "</p>";
    host.innerHTML = html;
    if (card.csv && $("fd-pf-paste") && !$("fd-pf-paste").value) {
      $("fd-pf-paste").value = card.csv;
    }
  }

  function nameKey(t) {
    var s = String(t || "").trim();
    if (!s) return "";
    var u = s.toUpperCase();
    if (/\s(EQUITY|INDEX|COMDTY|CURNCY|CORP|GOVT)$/.test(u)) return s.replace(/equity$/i, "Equity");
    if (/\sU[SWN]$/i.test(s)) return s + " Equity";
    return s + " US Equity";
  }
  function parseLocal(text) {
    var lines = String(text || "").replace(/^\uFEFF/, "").split(/\r?\n/).map(function (l) { return l.trim(); }).filter(function (l) {
      return l && l.charAt(0) !== "#";
    });
    if (!lines.length) throw new Error("empty table — need position,ticker");
    var delim = lines[0].indexOf("\t") >= 0 ? "\t" : (lines[0].indexOf(";") >= 0 ? ";" : ",");
    var split = function (line) {
      return line.split(delim).map(function (c) { return c.trim().replace(/^"|"$/g, ""); });
    };
    var header = split(lines[0]);
    var posIdx = 0, tkrIdx = 1, start = 0;
    var aliasesP = { position: 1, shares: 1, qty: 1, quantity: 1, size: 1, pos: 1, n: 1 };
    var aliasesT = { ticker: 1, symbol: 1, yellow: 1, name: 1, bbg: 1, t: 1, security: 1 };
    header.forEach(function (h, i) {
      var k = h.toLowerCase().replace(/[^a-z0-9]+/g, "_");
      if (aliasesP[k]) { posIdx = i; start = 1; }
      if (aliasesT[k]) { tkrIdx = i; start = 1; }
    });
    var merged = {};
    for (var i = start; i < lines.length; i++) {
      var cols = split(lines[i]);
      var t = nameKey(cols[tkrIdx] || "");
      var p = Number(String(cols[posIdx] || "").replace(/[(),]/g, function (ch) {
        return ch === "(" || ch === ")" ? "" : "";
      }).replace(/^\((.*)\)$/, "-$1"));
      if (String(cols[posIdx] || "").trim().charAt(0) === "(") p = -Math.abs(p);
      if (!t || !isFinite(p) || p === 0) continue;
      if (!merged[t]) merged[t] = { ticker: t, t: t.split(/\s+/)[0], position: 0 };
      merged[t].position += p;
    }
    var rows = Object.keys(merged).map(function (k) { return merged[k]; }).filter(function (r) { return r.position !== 0; });
    if (!rows.length) throw new Error("no usable rows");
    return rows;
  }
  function localScore(rows) {
    var d = desk() || {};
    var names = d.names || {};
    var quotes = {};
    rows.forEach(function (r) {
      var rec = names[r.ticker] || {};
      quotes[r.ticker] = { px_last: rec.px_last, source: rec.px_source || "desk-embed" };
    });
    var priced = [];
    var unpriced = [];
    rows.forEach(function (r) {
      var rec = Object.assign({}, r);
      var q = quotes[r.ticker] || {};
      rec.px_last = Number(q.px_last);
      if (!isFinite(rec.px_last) || rec.px_last <= 0) {
        rec.px_last = null; rec.notional = null; rec.weight = 0; unpriced.push(r.ticker);
      } else {
        rec.notional = rec.position * rec.px_last;
      }
      rec.px_source = q.source;
      priced.push(rec);
    });
    var gross = 0, net = 0, lng = 0, sht = 0;
    priced.forEach(function (r) {
      if (r.notional == null) return;
      gross += Math.abs(r.notional); net += r.notional;
      if (r.notional >= 0) lng += r.notional; else sht += r.notional;
    });
    priced.forEach(function (r) {
      r.weight = (r.notional == null || !gross) ? 0 : r.notional / gross;
      var rec = names[r.ticker] || {};
      r.in_universe = !!rec.in_universe;
      r.mom_score = rec.mom_score;
      r.gics = rec.gics;
      r.factor = rec.factor;
      r.pills = rec.pills || [];
      r.has_loadings = !!(rec.loadings && rec.loadings.length);
    });
    var missing = priced.filter(function (r) { return !r.in_universe; }).map(function (r) {
      return { ticker: r.ticker, t: r.t, priced: r.notional != null, px_last: r.px_last, note: "not in current desk universe" };
    });
    var covered = 0, acc = 0, momC = [];
    priced.forEach(function (r) {
      if (r.mom_score == null || !isFinite(Number(r.mom_score))) return;
      covered += Math.abs(r.weight); acc += r.weight * Number(r.mom_score);
      momC.push({ t: r.t, ticker: r.ticker, weight: r.weight, value: r.mom_score, contrib: r.weight * Number(r.mom_score) });
    });
    momC.sort(function (a, b) { return Math.abs(b.contrib) - Math.abs(a.contrib); });
    var facNames = d.factor_names || [];
    var exposures = {}, contribs = {};
    priced.forEach(function (r) {
      var rec = names[r.ticker] || {};
      var vec = rec.loadings || [];
      vec.forEach(function (loading, i) {
        var name = facNames[i] || ("F" + (i + 1));
        exposures[name] = (exposures[name] || 0) + r.weight * Number(loading);
        (contribs[name] = contribs[name] || []).push({ t: r.t, contrib: r.weight * Number(loading), value: loading, weight: r.weight });
      });
    });
    var factors = Object.keys(exposures).map(function (name) {
      var list = (contribs[name] || []).slice().sort(function (a, b) { return Math.abs(b.contrib) - Math.abs(a.contrib); }).slice(0, 3);
      return { name: name, exposure: exposures[name], contributors: list };
    });
    var metrics = [];
    var keys = ["mom_score", "r20", "rs63", "atr_pct", "beta", "residual_20d", "si_ratio", "inst_pct", "event_days", "vol_30d", "score_v2"];
    keys.forEach(function (k) {
      var accM = 0, cov = 0, c = [];
      priced.forEach(function (r) {
        var rec = names[r.ticker] || {};
        var val = rec[k];
        if (val == null && rec.metrics) val = rec.metrics[k];
        if (k === "mom_score" && val == null) val = rec.mom_score;
        val = Number(val);
        if (!isFinite(val)) return;
        cov += Math.abs(r.weight); accM += r.weight * val;
        c.push({ t: r.t, contrib: r.weight * val, value: val, weight: r.weight });
      });
      if (!cov) return;
      c.sort(function (a, b) { return Math.abs(b.contrib) - Math.abs(a.contrib); });
      metrics.push({ key: k, weighted_avg: accM / cov, coverage: cov, q1_gross: null, q5_gross: null, contributors: c.slice(0, 3) });
    });
    return {
      ok: true, version: 1, asof: new Date().toISOString(),
      advice: "look-here scorecard · not a trade · Tanner decides · sidecar down, used last Refresh snapshot",
      weight_def: WEIGHT_DEF, rows: priced, missing: missing, unpriced: unpriced,
      exposure: { gross: gross, net: net, net_gross: gross ? net / gross : null, long: lng, short: sht, n: priced.length },
      mom: { score: covered ? acc / covered : null, coverage: covered, contributors: momC.slice(0, 5),
        definition: "Same mom_score as FLAGS / MOM cards. Gross-weighted; shorts flip sign." },
      factors: { source: d.loadings_source, model: "existing desk loadings — not a new fit", factors: factors,
        empty_reason: factors.length ? null : "no factor loadings on the embedded desk snapshot" },
      metrics: metrics, categoricals: [], n: priced.length, error: null
    };
  }

  async function run() {
    var text = ($("fd-pf-paste") && $("fd-pf-paste").value) || "";
    if (!text.trim()) { status("paste or upload position,ticker first", "err"); return; }
    status("running…");
    try {
      var resp = await fetch(SIDECAR + "/api/portfolio", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ csv: text, allow_dapi: true })
      });
      var card = await resp.json();
      if (!resp.ok || (card && card.ok === false && card.error && !card.rows)) {
        throw new Error((card && card.error) || ("HTTP " + resp.status));
      }
      saveLs(card);
      paint(card);
      status("sidecar " + SIDECAR + (card.desk_asof ? " · desk " + card.desk_asof : ""));
      return;
    } catch (e) {
      try {
        var rows = parseLocal(text);
        var card2 = localScore(rows);
        card2.csv = text;
        saveLs(card2);
        paint(card2);
        status("sidecar unreachable — scored vs last Refresh snapshot (" + (e && e.message ? e.message : "offline") + ")");
      } catch (e2) {
        status(String(e2 && e2.message || e2), "err");
      }
    }
  }

  function hideNativeViews() {
    for (var i = 0; i < NATIVE_VIEWS.length; i++) {
      var el = $(NATIVE_VIEWS[i]);
      if (!el) continue;
      if (NATIVE_VIEWS[i] === VIEW) continue;
      el.classList.add("hide");
    }
  }
  function setOn(btn, on) {
    if (!btn) return;
    btn.classList.toggle("is-on", !!on);
    btn.classList.toggle("on", !!on);
    btn.setAttribute("aria-pressed", on ? "true" : "false");
  }
  function navButtons() {
    return document.querySelectorAll("#topnav .btn, #topnav .nav-btn, .topnav .nav-btn, nav .btn, nav .nav-btn, [data-view], [data-fd-portfolio]");
  }
  function oursOf(b) {
    var view = (b.getAttribute("data-view") || "");
    return view === "portfolio" || b.getAttribute("data-fd-portfolio") === "1" || b.id === NAV;
  }
  function syncNav(on) {
    var buttons = navButtons();
    for (var i = 0; i < buttons.length; i++) {
      var b = buttons[i];
      if (on) setOn(b, oursOf(b));
      else if (oursOf(b)) setOn(b, false);
    }
  }
  function show(on) {
    var pane = $(VIEW);
    if (on) {
      hideNativeViews();
      if (pane) {
        pane.classList.remove("hide");
        pane.classList.add("fd-pf-on");
        pane.removeAttribute("hidden");
      }
      document.body.setAttribute("data-view", "portfolio");
      document.body.setAttribute("data-fd-pf", "1");
      syncNav(true);
      var card = lastDb();
      if (!card || !card.ok) card = loadLs() || card;
      if (card && card.ok) paint(card);
      return;
    }
    if (pane) {
      pane.classList.add("hide");
      pane.classList.remove("fd-pf-on");
      pane.setAttribute("hidden", "hidden");
    }
    var home = $("home");
    if (home) home.classList.remove("hide");
    document.body.removeAttribute("data-fd-pf");
    if (document.body.getAttribute("data-view") === "portfolio") document.body.removeAttribute("data-view");
    syncNav(false);
  }
  window.__FD_PF_SHOW__ = function () { show(true); };
  window.__FD_PF_HIDE__ = function () { show(false); };
  function kindOf(btn) {
    if (!btn || !btn.getAttribute) return "";
    var view = (btn.getAttribute("data-view") || "").toLowerCase();
    if (view === "portfolio" || btn.getAttribute("data-fd-portfolio") === "1" || btn.id === NAV) return "portfolio";
    var label = (btn.textContent || "").replace(/\s+/g, " ").trim();
    if (label === "Portfolio") return "portfolio";
    if (btn.id === "refresh" || btn.id === "options-refresh" || btn.id === "fd-pf-run") return "";
    if (btn.closest && btn.closest("#topnav, nav, .topnav") && (btn.classList.contains("btn") || btn.classList.contains("nav-btn") || view)) return "other";
    if (btn.classList && (btn.classList.contains("nav-btn") || view)) return "other";
    return "";
  }
  document.addEventListener("click", function (ev) {
    var t = ev.target && ev.target.closest ? ev.target.closest("button, [data-view], [data-fd-portfolio]") : ev.target;
    if (t && t.id === "fd-pf-run") {
      ev.preventDefault();
      run();
      return;
    }
    var kind = kindOf(t);
    if (!kind) return;
    if (kind === "portfolio") {
      ev.preventDefault();
      ev.stopPropagation();
      if (ev.stopImmediatePropagation) ev.stopImmediatePropagation();
      show(true);
      return;
    }
    if (kind === "other") show(false);
  }, true);
  document.addEventListener("change", function (ev) {
    var t = ev.target;
    if (!t || t.id !== "fd-pf-file") return;
    var file = t.files && t.files[0];
    if (!file) return;
    var reader = new FileReader();
    reader.onload = function () {
      if ($("fd-pf-paste")) $("fd-pf-paste").value = String(reader.result || "");
    };
    reader.readAsText(file);
  });
  function installSetViewBridge() {
    var orig = window.setView;
    if (typeof orig !== "function" || orig.__fdPf) return;
    window.setView = function (v) {
      var kind = String(v || "").toLowerCase();
      if (kind === "portfolio" || kind === "pf") { show(true); return; }
      show(false);
      return orig.apply(this, arguments);
    };
    window.setView.__fdPf = true;
  }
  installSetViewBridge();
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", installSetViewBridge);
})();
""".replace("__VIEW__", VIEW_ID).replace("__NAV__", NAV_ID).replace("__DB__", DB_SCRIPT_ID).replace(
        "__DESK__", DESK_SCRIPT_ID
    ).replace("__SIDECAR__", SIDECAR_ORIGIN).replace("__NATIVE__", native).replace(
        "__WDEF__", WEIGHT_DEF.replace("\\", "\\\\").replace('"', '\\"')
    ).strip()


def _find_tag_span(html_text: str, elem_id: str) -> tuple[int, int] | None:
    text = html_text or ""
    opener = re.compile(
        rf'<div\b(?=[^>]*\bid=["\']{re.escape(elem_id)}["\'])[^>]*>',
        re.I,
    )
    match = opener.search(text)
    if not match:
        return None
    start = match.start()
    depth = 1
    for tok in _DIV_TOKEN_RE.finditer(text, match.end()):
        closing = bool(tok.group(1))
        rest = tok.group(2) or ""
        self_close = rest.rstrip().endswith("/")
        if closing:
            depth -= 1
            if depth == 0:
                return start, tok.end()
        elif self_close:
            continue
        else:
            depth += 1
    return None


def _has_nav_button(html_text: str) -> bool:
    text = html_text or ""
    patterns = (
        rf'<button\b[^>]*\bdata-view=["\']portfolio["\']',
        rf'<button\b[^>]*\bdata-fd-portfolio=["\']1["\']',
        rf'<button\b[^>]*\bid=["\']{re.escape(NAV_ID)}["\']',
    )
    return any(re.search(pat, text, re.I | re.S) for pat in patterns)


def _ensure_nav(html_text: str) -> str:
    if _has_nav_button(html_text):
        return html_text
    pair = "\n  " + BTN_PORTFOLIO
    for cre in (_PAPER_BTN_RE, _EXPERIMENTAL_BTN_RE, _OPTIONS_VIEW_BTN_RE, _BREAKDOWN_BTN_RE):
        m = cre.search(html_text)
        if m:
            return html_text[: m.end()] + pair + html_text[m.end() :]
    if "<nav" in html_text.lower():
        return re.sub(r"(</nav>)", pair + r"\n\1", html_text, count=1, flags=re.I)
    if "<body" in html_text.lower():
        nav = f'<nav class="topnav" id="topnav">\n  {BTN_PORTFOLIO}\n</nav>\n'
        return re.sub(r"(<body\b[^>]*>)", r"\1\n" + nav, html_text, count=1, flags=re.I)
    return pair + "\n" + html_text


def _ensure_css(html_text: str) -> str:
    css = f'<style id="{CSS_STYLE_ID}">\n{strip_css()}\n</style>\n'
    text, n = re.subn(
        rf'<style\b[^>]*\bid=["\']{CSS_STYLE_ID}["\'][^>]*>.*?</style>\s*',
        lambda _m: css,
        html_text,
        count=1,
        flags=re.I | re.S,
    )
    if n:
        return text
    if "</head>" in html_text:
        return html_text.replace("</head>", css + "</head>", 1)
    return css + html_text


def _early_return_snippet(marker: str, param: str) -> str:
    return (
        f"{marker}"
        f"if({param}===\"portfolio\"||{param}===\"pf\"){{"
        f"if(window.__FD_PF_SHOW__)window.__FD_PF_SHOW__();"
        f"if(typeof syncNav===\"function\")syncNav();"
        f"return;}}"
    )


def _replace_or_inject_early_return(
    html_text: str,
    *,
    marker: str,
    fn_re: re.Pattern[str],
) -> str:
    text = html_text or ""
    existing = re.search(
        re.escape(marker) + r'if\((\w+)==="portfolio".*?return;\}',
        text,
        re.S,
    )
    if existing:
        return text[: existing.start()] + _early_return_snippet(marker, existing.group(1)) + text[existing.end() :]

    def inject(match: re.Match[str]) -> str:
        head, param = match.group(1), match.group(2)
        return f"{head}{_early_return_snippet(marker, param)}"

    return fn_re.sub(inject, text, count=1)


def _hideall_snippet() -> str:
    return (
        f"{HIDEALL_MARKER}"
        '["view-portfolio"].forEach(function(id){'
        "var el=document.getElementById(id);"
        'if(el){el.classList.add("hide");el.classList.remove("fd-pf-on");'
        'el.setAttribute("hidden","hidden");}'
        'if(document.body)document.body.removeAttribute("data-fd-pf");'
        "});"
    )


def _patch_hideall_panes(html_text: str) -> str:
    text = html_text or ""
    snippet = _hideall_snippet()
    existing = re.search(re.escape(HIDEALL_MARKER) + r".*?}\);", text, re.S)
    if existing:
        return text[: existing.start()] + snippet + text[existing.end() :]
    return _HIDEALL_FN_RE.sub(lambda m: m.group(1) + snippet, text, count=1)


def _patch_setview_allowlist(html_text: str) -> str:
    text = html_text or ""
    if re.search(
        r"home\|mom-up\|mom-down\|outliers\|options(?:\|[^\"'\s]+)*\|portfolio",
        text,
        re.I,
    ):
        return text
    return _ALLOWLIST_RE.sub(lambda m: m.group(0) + "|portfolio", text)


def _patch_view_id_lists(html_text: str) -> str:
    text = _VIEW_ID_ARRAY_RE.sub(
        r'\1, "view-portfolio"\2',
        html_text,
    )
    text = _VIEW_SEL_RE.sub(
        r"\1, #view-portfolio",
        text,
    )
    return text


def _patch_setview(html_text: str) -> str:
    text = _patch_setview_allowlist(html_text)
    text = _replace_or_inject_early_return(html_text=text, marker=SETVIEW_MARKER, fn_re=_SETVIEW_FN_RE)
    text = _replace_or_inject_early_return(html_text=text, marker=PAINTVIEW_MARKER, fn_re=_PAINTVIEW_FN_RE)
    text = _patch_hideall_panes(text)
    return _patch_view_id_lists(text)


def _insert_host(html_text: str, host: str) -> str:
    for elem_id in (
        "view-experimental",
        "view-paper",
        "view-breakdown",
        "view-breakout",
        "view-mom-down",
        "view-mom-up",
    ):
        span = _find_tag_span(html_text, elem_id)
        if span:
            return html_text[: span[1]] + "\n" + host + html_text[span[1] :]
    for pat in (
        r"(<nav\b[^>]*>.*?</nav>)",
        r"(<h1\b[^>]*>.*?</h1>)",
        r"(<body\b[^>]*>)",
    ):
        match = re.search(pat, html_text, re.I | re.S)
        if match:
            return html_text[: match.end()] + "\n" + host + html_text[match.end() :]
    return host + "\n" + html_text


def _ensure_panes(html_text: str, *, replace: bool = True) -> str:
    has_view = _find_tag_span(html_text, VIEW_ID) is not None
    if has_view and not replace:
        return html_text
    host = panes_html()
    span = _find_tag_span(html_text, VIEW_ID)
    if span:
        return html_text[: span[0]] + host + html_text[span[1] :]
    return _insert_host(html_text, host)


def _script_tag(elem_id: str, payload: Any) -> str:
    body = json.dumps(payload, default=str)
    return f'<script type="application/json" id="{elem_id}">{body}</script>'


def _ensure_json_script(html_text: str, elem_id: str, payload: Any | None, *, fill_if_missing: Any) -> str:
    if payload is None:
        if re.search(rf'id=["\']{re.escape(elem_id)}["\']', html_text, re.I):
            return html_text
        payload = fill_if_missing
    tag = _script_tag(elem_id, payload)
    if re.search(rf'id=["\']{re.escape(elem_id)}["\']', html_text, re.I):
        return re.sub(
            rf'<script\b[^>]*\bid=["\']{re.escape(elem_id)}["\'][^>]*>.*?</script>',
            lambda _m: tag,
            html_text,
            count=1,
            flags=re.I | re.S,
        )
    if "</body>" in html_text:
        return html_text.replace("</body>", tag + "\n</body>", 1)
    return html_text + tag


def _ensure_js(html_text: str) -> str:
    script = f'<script id="{JS_SCRIPT_ID}">\n{strip_js()}\n</script>\n'
    text, n = re.subn(
        rf'<script\b[^>]*\bid=["\']{JS_SCRIPT_ID}["\'][^>]*>.*?</script>\s*',
        lambda _m: script,
        html_text,
        count=1,
        flags=re.I | re.S,
    )
    if n:
        return text
    if "</body>" in html_text:
        return html_text.replace("</body>", script + "</body>", 1)
    return html_text + script


def ensure_embedded(
    html_text: str,
    scorecard: Mapping[str, Any] | None = None,
    desk: Mapping[str, Any] | None = None,
) -> str:
    """Portfolio nav + pane + desk snapshot + last scorecard + JS.

    ``scorecard=None`` / ``desk=None`` must not wipe existing ``#fd-portfolio-db``
    / ``#fd-portfolio-desk-db``. Safe on live ~2.7–4.8MB ``factorbook.html``.
    Does not wrap ``cardHTML``. Does not change FLAGS/WATCH/MOM ranking.
    """
    text = html_text or ""
    text = _ensure_nav(text)
    text = _ensure_css(text)
    text = _patch_setview(text)
    text = _ensure_panes(text, replace=True)
    text = _ensure_json_script(text, DESK_SCRIPT_ID, desk, fill_if_missing={"names": {}, "factor_names": []})
    text = _ensure_json_script(text, DB_SCRIPT_ID, scorecard, fill_if_missing=empty_scorecard())
    text = _ensure_js(text)
    return text
