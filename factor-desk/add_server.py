"""Sidecar HTTP server for Factor Desk (port 8765).

Refresh pipeline (matches live desk)::

    prices → dapi_enrich (~50–60%) → rebuild / write_dash

Options pulse is **off** on main Refresh. Manual **Options Refresh** runs the
full-book options stage then rebuilds Options / OPT SPIKE / abnormal scores::

    GET/POST /options-refresh
    GET/POST /refresh?options=1
    POST     /api/refresh_live   {"options": 1}

Intraday (default off)::

    GET  /refresh?intraday=1
    POST /refresh          JSON body {"intraday": 1}  or query string

When ``intraday=1``, enrich also pulls session volume vs ADV. When off, enrich
behavior is unchanged. Capacity (``BLOOMBERG_LIMIT``) skips enrich the same way
options pulse degrades — Refresh still finishes.

Thin ENRICH HOOK: Desktop copies ``run_dapi_enrich_stage`` / ``parse_intraday``
into the live ``add_server.py`` if this file is not the one serving :8765.

Add-to-book (``POST /api/add``, :func:`do_add`)::

    history → merge prices → universe_extra → enrich-or-stub → rebuild

Enrichment is best-effort. A DAPI miss still stubs ``dapi_enrichment.json``
so the desk card universe (and ``#fd-search-book``) includes the short symbol.
After rebuild, Add verifies the short symbol is in the live search payload and
lands it if missing. Prices/residual alone (TSEM “integrated into the rest”)
is not treated as success.
"""

from __future__ import annotations

import json
import logging
import sys
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import dapi_enrich  # noqa: E402
import desk_dash  # noqa: E402

LOG = logging.getLogger("add_server")
HOST = "127.0.0.1"
PORT = 8765

_STATE_LOCK = threading.Lock()
_STATE: dict[str, Any] = {
    "busy": False,
    "pct": 0,
    "stage": "idle",
    "error": None,
    "last": None,
    "intraday": False,
    "options": False,
    "kind": "idle",
}

REFRESH_PATHS = frozenset({"/refresh", "/api/refresh", "/api/refresh_live"})
ADD_PATHS = frozenset({"/api/add", "/add"})
SEARCH_PATHS = frozenset({"/api/search", "/search"})
OPTIONS_REFRESH_PATHS = frozenset(
    {
        "/options-refresh",
        "/options_refresh",
        "/api/options_refresh",
        "/api/options-refresh",
    }
)


def parse_intraday(query: dict[str, list[str]], body: Any | None = None) -> bool:
    """Accept query/body flag ``intraday=1`` (also true/yes/on). Default off."""
    return parse_flag(query, body, "intraday")


def parse_options(query: dict[str, list[str]], body: Any | None = None) -> bool:
    """Accept query/body flag ``options=1`` (also true/yes/on). Default off."""
    return parse_flag(query, body, "options")


def parse_flag(query: dict[str, list[str]], body: Any | None, key: str) -> bool:
    if key in query:
        return dapi_enrich.parse_intraday_flag(query.get(key, [""])[0])
    if isinstance(body, dict) and key in body:
        return dapi_enrich.parse_intraday_flag(body.get(key))
    return False


def _tickers_from(query: dict[str, list[str]], body: Any | None) -> list[str]:
    tickers: list[str] = []
    if isinstance(body, dict) and body.get("tickers"):
        raw = body["tickers"]
        tickers = raw if isinstance(raw, list) else str(raw).split(",")
        tickers = [t.strip() for t in tickers if str(t).strip()]
    if query.get("tickers"):
        tickers.extend(t.strip() for t in query["tickers"][0].split(",") if t.strip())
    return tickers


def _set_progress(pct: float, stage: str) -> None:
    with _STATE_LOCK:
        _STATE["pct"] = int(max(0, min(100, round(pct * 100 if pct <= 1 else pct))))
        _STATE["stage"] = stage


def _progress_cb(frac: float, stage: str) -> None:
    _set_progress(frac, stage)


def _try_call_strict(module_name: str, func_names: tuple[str, ...], **kwargs: Any) -> tuple[Any, str | None]:
    """Call ``module.fn(**kwargs)`` only. Never retry with an empty argument list.

    An empty retry would turn a one-name Add into a full-book Bloomberg pull.
    """
    try:
        mod = __import__(module_name)
    except ImportError as exc:
        return None, f"{module_name}_missing:{exc}"
    for name in func_names:
        fn = getattr(mod, name, None)
        if not callable(fn):
            continue
        try:
            return fn(**kwargs), None
        except TypeError:
            continue
        except Exception as exc:  # noqa: BLE001
            return None, f"{module_name}.{name}:{exc}"
    return None, f"{module_name}_no_entry:{','.join(func_names)}"


def _try_call(module_name: str, func_names: tuple[str, ...], **kwargs: Any) -> tuple[Any, str | None]:
    try:
        mod = __import__(module_name)
    except ImportError as exc:
        return None, f"{module_name}_missing:{exc}"
    for name in func_names:
        fn = getattr(mod, name, None)
        if callable(fn):
            try:
                return fn(**kwargs), None
            except TypeError:
                try:
                    return fn(), None
                except Exception as exc:  # noqa: BLE001
                    return None, f"{module_name}.{name}:{exc}"
            except Exception as exc:  # noqa: BLE001
                return None, f"{module_name}.{name}:{exc}"
    return None, f"{module_name}_no_entry:{','.join(func_names)}"


def run_prices_stage(tickers: list[str], progress_cb: Callable[[float, str], None]) -> tuple[dict[str, Any] | None, str | None]:
    progress_cb(0.15, "prices")
    result, err = _try_call(
        "pull_blpapi_live",
        ("pull_live", "run", "main_pull"),
        tickers=tickers,
    )
    if err and err.startswith("pull_blpapi_live_missing"):
        LOG.info("prices stage skipped (Desktop module not in this tree)")
        progress_cb(0.40, "prices skipped")
        return None, None
    if err:
        LOG.warning("prices stage: %s", err)
        progress_cb(0.40, "prices degraded")
        return None, err
    progress_cb(0.40, "prices done")
    return result if isinstance(result, dict) else None, None


def run_options_stage(
    tickers: list[str],
    progress_cb: Callable[[float, str], None],
    root: Path | None = None,
) -> tuple[Any, str | None]:
    progress_cb(0.42, "options pulse")
    result, err = _try_call(
        "pull_options_pulse",
        ("pull_pulse", "run_pulse", "run"),
        tickers=tickers,
        progress_cb=progress_cb,
        root=root,
    )
    if err:
        LOG.warning("options pulse: %s — continue", err)
        progress_cb(0.50, "options degraded")
        return None, err
    progress_cb(0.50, "options done")
    return result, None


def run_dapi_enrich_stage(
    tickers: list[str],
    *,
    intraday: bool = False,
    prices_ctx: dict[str, Any] | None = None,
    options_by_name: dict[str, Any] | None = None,
    progress_cb: Callable[[float, str], None] | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    """ENRICH HOOK — Refresh stage ~50–60%.

    Skip / degrade on DAPI capacity. Never raises out of Refresh.
    """
    cb = progress_cb or _progress_cb
    cb(0.50, "dapi_enrich")
    session = None
    try:
        session = dapi_enrich.open_dapi_session()
    except dapi_enrich.CapacityError as exc:
        LOG.warning("enrich skipped (capacity at open): %s", exc.reason)
        book = dapi_enrich.enrich_book(
            tickers,
            dapi_enrich.NullSession(),
            prices_ctx=prices_ctx,
            options_by_name=options_by_name,
            intraday=intraday,
            progress_cb=cb,
            root=root,
        )
        book.setdefault("meta", {})["capacity_skipped"] = True
        book["meta"]["skips"] = list(book["meta"].get("skips") or []) + [f"capacity:{exc.reason}"]
        cb(0.60, "dapi_enrich skipped capacity")
        return book
    except Exception as exc:  # noqa: BLE001
        LOG.warning("enrich session open failed: %s — null book", exc)
        session = None

    try:
        return dapi_enrich.enrich_book(
            tickers,
            session,
            prices_ctx=prices_ctx,
            options_by_name=options_by_name,
            intraday=intraday,
            progress_cb=cb,
            root=root,
        )
    except dapi_enrich.CapacityError as exc:
        LOG.warning("enrich skipped (capacity): %s", exc.reason)
        book = dapi_enrich.enrich_book(
            tickers,
            dapi_enrich.NullSession(),
            prices_ctx=prices_ctx,
            options_by_name=options_by_name,
            intraday=intraday,
            progress_cb=cb,
            root=root,
        )
        book.setdefault("meta", {})["capacity_skipped"] = True
        book["meta"]["skips"] = list(book["meta"].get("skips") or []) + [f"capacity:{exc.reason}"]
        return book
    except Exception as exc:  # noqa: BLE001
        LOG.warning("enrich failed (non-fatal): %s", exc)
        cb(0.60, "dapi_enrich failed")
        return {
            "asof": dapi_enrich._now_iso(),
            "names": {},
            "meta": {
                "intraday": bool(intraday),
                "capacity_skipped": False,
                "skips": [f"enrich_error:{exc}"],
                "source": "dapi_enrich",
            },
        }


def html_out(root: Path | None = None) -> Path | None:
    """Desktop live desk Tanner opens, when this root is that layout.

    ``<Desktop>/factorbook/`` is the package cwd. ``HTML_OUT`` is
    ``<Desktop>/factorbook.html``. Returns None when no parent live file is
    in play (unit-test temps, repo ``factor-desk/`` with no sibling HTML).
    """
    base = Path(root) if root is not None else HERE
    parent = base.parent / desk_dash.HTML_NAME
    if not parent.is_file():
        return None
    if base.name.lower() == "factorbook":
        return parent
    try:
        size = parent.stat().st_size
    except OSError:
        return None
    if size >= desk_dash.LIVE_MIN_BYTES:
        return parent
    text = desk_dash._read_html(parent)
    if text and desk_dash.looks_like_live_desk(text):
        return parent
    return None


def assert_html_out_live(root: Path | None = None) -> None:
    """Fail the rebuild when Desktop ``factorbook.html`` is legacy-sized.

    A ~188KB FLAGS/PAIRS shell is under ``LIVE_MIN_BYTES`` (1_000_000).
    """
    dest = html_out(root)
    if dest is None:
        return
    size = dest.stat().st_size if dest.is_file() else 0
    if size < desk_dash.LIVE_MIN_BYTES:
        raise desk_dash.LiveDeskShrinkError(
            f"HTML_OUT legacy-sized: {dest} ({size} bytes < {desk_dash.LIVE_MIN_BYTES})"
        )


def run_rebuild(progress_cb: Callable[[float, str], None] | None = None, root: Path | None = None) -> str | None:
    """Add/Refresh rebuild. cwd is the factorbook package directory.

    Calls this tree's ``write_dash.write`` (same dest rules as
    ``python write_dash.py``). Never ``rebuild_bundle/write_dash.py``.
    """
    return run_rebuild_stage(progress_cb or _progress_cb, root=root)


def run_rebuild_stage(progress_cb: Callable[[float, str], None], root: Path | None = None) -> str | None:
    root = Path(root) if root is not None else HERE
    progress_cb(0.65, "rebuild")
    _, err_v0 = _try_call("run_v0", ("run", "main"), root=root)
    if err_v0 and not err_v0.startswith("run_v0_missing"):
        LOG.warning("run_v0: %s", err_v0)
    # Experimental residual panel (S-score). Non-fatal. Not a DAPI stage.
    progress_cb(0.72, "experimental residual panel")
    _, err_ss = _try_call("s_score", ("materialize_panel",), root=root, write=True)
    if err_ss and not str(err_ss).startswith("s_score_missing"):
        LOG.warning("s_score panel: %s — continue", err_ss)
    progress_cb(0.80, "write_dash")
    # In-package write_dash only. Do not shell out to rebuild_bundle/write_dash.py.
    _, err_wd = _try_call("write_dash", ("write", "main"), root=root)
    if err_wd:
        LOG.warning("write_dash: %s", err_wd)
    try:
        assert_html_out_live(root)
    except desk_dash.LiveDeskShrinkError as exc:
        LOG.error("%s", exc)
        progress_cb(1.0, "failed")
        return str(exc)
    progress_cb(0.90, "desk_dash")
    _, err_dd = _try_call("desk_dash", ("rebuild", "assemble_and_write", "main"), root=root)
    if err_dd and not str(err_dd).startswith("desk_dash"):
        LOG.warning("desk_dash: %s", err_dd)
    try:
        assert_html_out_live(root)
    except desk_dash.LiveDeskShrinkError as exc:
        LOG.error("%s", exc)
        progress_cb(1.0, "failed")
        return str(exc)
    progress_cb(1.0, "done")
    return err_wd or err_dd


def _label_options_progress(cb: Callable[[float, str], None]) -> Callable[[float, str], None]:
    def wrapped(frac: float, stage: str) -> None:
        if "options" not in (stage or "").lower():
            stage = f"options · {stage}"
        cb(frac, stage)

    return wrapped


def run_refresh(
    *,
    tickers: list[str] | None = None,
    intraday: bool = False,
    options: bool = False,
    root: Path | None = None,
    progress_cb: Callable[[float, str], None] | None = None,
) -> dict[str, Any]:
    """Main Refresh: prices → enrich → rebuild. Options pulse is off unless ``options``.

    Prefer :func:`run_options_refresh` for the manual Options Refresh button.
    Enrich file missing afterwards is OK — dash still builds.
    """
    if options:
        return run_options_refresh(tickers=tickers, root=root, progress_cb=progress_cb)
    cb = progress_cb or _progress_cb
    root = Path(root) if root is not None else HERE
    names = list(tickers or dapi_enrich.discover_tickers(root))
    cb(0.05, "start")
    prices_ctx, price_err = run_prices_stage(names, cb)
    # Full-book options pulse is *not* on main Refresh (manual Options Refresh).
    book = run_dapi_enrich_stage(
        names,
        intraday=intraday,
        prices_ctx=prices_ctx if isinstance(prices_ctx, dict) else None,
        options_by_name=None,
        progress_cb=cb,
        root=root,
    )
    # GICS sector chips read gics_sector_name from the enrich file / gics_sectors.json
    # cache. Do NOT add a sectors Refresh stage here.
    rebuild_err = run_rebuild_stage(cb, root=root)
    if rebuild_err and "legacy-sized" in str(rebuild_err):
        raise desk_dash.LiveDeskShrinkError(rebuild_err)
    return {
        "ok": True,
        "kind": "refresh",
        "n": len(book.get("names") or {}),
        "intraday": bool(intraday),
        "options": False,
        "enrich_path": str(dapi_enrich.default_out_path(root)),
        "capacity_skipped": bool((book.get("meta") or {}).get("capacity_skipped")),
        "skips": list((book.get("meta") or {}).get("skips") or []),
        "price_err": price_err,
        "opt_err": None,
        "rebuild_err": rebuild_err,
        "asof": book.get("asof"),
    }


def run_options_refresh(
    *,
    tickers: list[str] | None = None,
    root: Path | None = None,
    progress_cb: Callable[[float, str], None] | None = None,
) -> dict[str, Any]:
    """Manual full-book options pulse + rebuild. No prices / enrich.

    Writes ``options_abnormal.json`` then rebuilds Options / OPT SPIKE / scores.
    """
    cb = _label_options_progress(progress_cb or _progress_cb)
    root = Path(root) if root is not None else HERE
    names = list(tickers or dapi_enrich.discover_tickers(root))
    cb(0.05, "options start")
    options_res, opt_err = run_options_stage(names, cb, root=root)
    rebuild_err = run_rebuild_stage(cb, root=root)
    if rebuild_err and "legacy-sized" in str(rebuild_err):
        raise desk_dash.LiveDeskShrinkError(rebuild_err)
    n = 0
    if isinstance(options_res, dict):
        names_map = options_res.get("names") or options_res
        if isinstance(names_map, dict):
            n = len(names_map)
    return {
        "ok": True,
        "kind": "options",
        "options": True,
        "n": n or len(names),
        "opt_err": opt_err,
        "rebuild_err": rebuild_err,
        "abnormal_path": str(root / "options_abnormal.json"),
    }


def _one_ticker(query: dict[str, list[str]], body: Any | None) -> str:
    if isinstance(body, dict):
        for key in ("ticker", "symbol", "name", "t"):
            raw = body.get(key)
            if raw:
                return str(raw).strip()
        if body.get("tickers"):
            raw = body["tickers"]
            if isinstance(raw, list) and raw:
                return str(raw[0]).strip()
            if isinstance(raw, str) and raw.strip():
                return raw.split(",")[0].strip()
    for key in ("ticker", "symbol", "t"):
        if query.get(key):
            return str(query[key][0]).strip()
    return ""


def _pull_add_history(ticker: str, root: Path) -> tuple[Any, str | None]:
    """Desktop Bloomberg history when ``pull_blpapi_live`` exposes it. Missing module is not an error."""
    result, err = _try_call_strict(
        "pull_blpapi_live",
        ("pull_history", "pull_hist", "fetch_history", "add_history"),
        tickers=[ticker],
        ticker=ticker,
        root=root,
    )
    if err and ("_missing" in err or "_no_entry" in err):
        return None, None
    return result, err


def _merge_add_prices(ticker: str, root: Path) -> str | None:
    """Merge the new history into ``prices_long.csv`` when a Desktop helper exists."""
    for module_name, names in (
        ("pull_blpapi_live", ("merge_prices", "merge_into_prices_long", "append_prices")),
        ("clean_ingest", ("merge_prices", "merge_long", "append_prices")),
    ):
        _result, err = _try_call_strict(
            module_name,
            names,
            ticker=ticker,
            tickers=[ticker],
            root=root,
        )
        if err and ("_missing" in err or "_no_entry" in err):
            continue
        return err
    return None


def _add_message(
    short: str,
    *,
    in_book: bool,
    enrich_ok: bool,
    px: Any,
    rebuild_err: str | None,
    searchable: bool | None = None,
) -> str:
    if not in_book:
        return f"{short or 'symbol'} was not added to the book."
    if rebuild_err and "legacy-sized" in str(rebuild_err):
        return (
            f"{short} is in the enrichment book, but the live desk HTML was not updated "
            f"({rebuild_err})."
        )
    if searchable is False:
        return (
            f"{short} prices/residual may have merged into the book, but the live desk "
            "search payload never received the short symbol. Recopy desk_dash.py / "
            "add_server.py / dapi_enrich.py and Add again."
        )
    if enrich_ok:
        return f"Added {short}."
    px_note = " Last price was attached from the price file." if px is not None else ""
    return (
        f"{short} is in the book.{px_note} "
        "DAPI enrichment did not fill fields; the card is limited-history and still searchable."
    )


def _force_stub_book(
    key: str,
    *,
    root: Path,
    prices_ctx: Mapping[str, Any] | None,
    enrich_error: str | None,
) -> dict[str, Any]:
    """Guarantee ``names[key]`` even when the primary enrich path raised."""
    book = dapi_enrich.upsert_add_names(
        [key],
        root=root,
        session=dapi_enrich.NullSession(),
        prices_ctx=prices_ctx,
        open_session=False,
    )
    meta = book.setdefault("meta", {})
    if not isinstance(meta, dict):
        meta = {}
        book["meta"] = meta
    if enrich_error:
        skips = list(meta.get("skips") or [])
        skips.append(f"add_enrich:{enrich_error}")
        meta["skips"] = skips
        meta["add_enrich_error"] = enrich_error
        dapi_enrich.write_enrichment(book, dapi_enrich.default_out_path(root))
    return book


def _live_html_text(root: Path) -> tuple[Path | None, str]:
    dest = desk_dash.resolve_live_dest(root)
    if not dest.is_file():
        return dest, ""
    return dest, desk_dash._read_html(dest)


def _ensure_searchable(short: str, root: Path, *, land: bool) -> tuple[bool, str | None]:
    """Confirm the short symbol is in ``#fd-search-book``; optionally land it."""
    cards = desk_dash.cards_from_enrichment(desk_dash.load_enrichment(root), root=root)
    in_cards = any(
        dapi_enrich.short_symbol(str(c.get("t") or c.get("ticker") or "")) == short for c in cards
    )
    dest, text = _live_html_text(root)
    if dest is None or not dest.is_file() or not desk_dash.looks_like_live_desk(text):
        return in_cards, None if in_cards else f"{short} missing from card universe"
    if desk_dash.search_book_has_symbol(text, short):
        return True, None
    if not land:
        return False, f"{short} missing from #fd-search-book"
    try:
        desk_dash.land_search_book(root)
    except Exception as exc:  # noqa: BLE001
        LOG.warning("land_search_book failed: %s", exc)
        return False, str(exc)
    text = desk_dash._read_html(dest)
    if desk_dash.search_book_has_symbol(text, short):
        return True, None
    return False, f"{short} missing from #fd-search-book after land"


def do_add(
    ticker: str,
    *,
    root: Path | None = None,
    session: Any | None = None,
    progress_cb: Callable[[float, str], None] | None = None,
    rebuild: bool = True,
) -> dict[str, Any]:
    """Add one symbol: history → prices → ``universe_extra`` → enrich or stub → rebuild.

    Bloomberg history and the price merge run only when the Desktop modules
    are importable. They are skipped in this tree (no Bloomberg). Enrichment
    is attempted for the new name and merged into ``dapi_enrichment.json``.
    If DAPI does not resolve fields, a stub ``names[ticker]`` is still written
    and the status stays honest: the name is in the book.

    Success also requires the short symbol to appear in the live desk
    ``#fd-search-book``. Prices/residual alone (the TSEM failure mode) is not
    enough.
    """
    cb = progress_cb or _progress_cb
    base = Path(root) if root is not None else HERE
    raw = str(ticker or "").strip()
    key = dapi_enrich.canonical_ticker(raw) if raw else ""
    short = dapi_enrich.short_symbol(key)
    if not key:
        return {
            "ok": False,
            "in_book": False,
            "enrich_ok": False,
            "stubbed": False,
            "ticker": "",
            "short": "",
            "message": "ticker is required.",
            "error": "ticker_required",
        }

    cb(0.10, "history")
    hist_result, hist_err = _pull_add_history(key, base)
    if hist_err:
        LOG.warning("add history: %s", hist_err)
    cb(0.30, "merge prices")
    merge_err = _merge_add_prices(key, base)
    if merge_err:
        LOG.warning("add merge prices: %s", merge_err)

    cb(0.40, "universe_extra")
    dapi_enrich.append_universe_extra(base, key)

    cb(0.50, "dapi_enrich")
    prices_ctx = hist_result if isinstance(hist_result, dict) else None
    enrich_error: str | None = None
    try:
        book = dapi_enrich.upsert_add_names(
            [key],
            root=base,
            session=session,
            prices_ctx=prices_ctx,
            open_session=session is None,
        )
    except Exception as exc:  # noqa: BLE001
        LOG.warning("add enrich raised; forcing stub: %s", exc)
        enrich_error = str(exc)
        try:
            book = _force_stub_book(key, root=base, prices_ctx=prices_ctx, enrich_error=enrich_error)
        except Exception as stub_exc:  # noqa: BLE001
            LOG.warning("force stub failed: %s", stub_exc)
            book = dapi_enrich.load_enrichment(dapi_enrich.default_out_path(base)) or {
                "names": {},
                "meta": {},
            }

    names = book.get("names") if isinstance(book, Mapping) else None
    rec = None
    if isinstance(names, dict):
        rec = names.get(key) or names.get(short)
        if not isinstance(rec, Mapping):
            # Guarantee a discrete enrich row even when only extras/residual know it.
            try:
                book = _force_stub_book(key, root=base, prices_ctx=prices_ctx, enrich_error=enrich_error)
                names = book.get("names") if isinstance(book, Mapping) else None
                if isinstance(names, dict):
                    rec = names.get(key) or names.get(short)
            except Exception as stub_exc:  # noqa: BLE001
                LOG.warning("ensure enrich row failed: %s", stub_exc)
    in_book = isinstance(rec, Mapping) or key in {
        dapi_enrich.name_key(t) for t in dapi_enrich.book_extra_tickers(base)
    }
    enrich_ok = dapi_enrich.dapi_fields_resolved(rec if isinstance(rec, Mapping) else None)
    stubbed = in_book and not enrich_ok
    meta = book.get("meta") if isinstance(book, Mapping) else {}
    if isinstance(meta, Mapping) and meta.get("add_enrich_error") and not enrich_error:
        enrich_error = str(meta.get("add_enrich_error"))
    px = rec.get("px_last") if isinstance(rec, Mapping) else None

    rebuild_err = None
    searchable: bool | None = None
    search_err: str | None = None
    if rebuild and in_book:
        cb(0.70, "rebuild")
        rebuild_err = run_rebuild(cb, root=base)
        cb(0.92, "verify search book")
        searchable, search_err = _ensure_searchable(short, base, land=True)
        if search_err and not rebuild_err:
            rebuild_err = search_err
    elif rebuild:
        cb(0.70, "rebuild skipped")
    else:
        # No HTML rewrite requested — confirm the card universe only.
        searchable, search_err = _ensure_searchable(short, base, land=False)

    live_failed = bool(rebuild_err) and "legacy-sized" in str(rebuild_err)
    search_failed = searchable is False
    message = _add_message(
        short,
        in_book=in_book,
        enrich_ok=enrich_ok,
        px=px,
        rebuild_err=rebuild_err,
        searchable=searchable,
    )
    ok = bool(in_book) and not live_failed and not search_failed
    cb(1.0, "done" if ok else "failed")
    return {
        "ok": ok,
        "in_book": bool(in_book),
        "enrich_ok": bool(enrich_ok),
        "stubbed": bool(stubbed),
        "searchable": searchable,
        "ticker": key,
        "short": short,
        "message": message,
        "history_err": hist_err,
        "merge_err": merge_err,
        "enrich_error": enrich_error,
        "rebuild_err": rebuild_err,
        "search_err": search_err,
        "px_last": px,
        "kind": "add",
    }


def _read_json_body(handler: BaseHTTPRequestHandler) -> Any:
    length = int(handler.headers.get("Content-Length") or 0)
    if length <= 0:
        return None
    raw = handler.rfile.read(length)
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


class DeskHandler(BaseHTTPRequestHandler):
    server_version = "FactorDeskEnrich/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        LOG.info("%s - %s", self.address_string(), fmt % args)

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json(self, code: int, payload: Any) -> None:
        blob = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(blob)))
        self.end_headers()
        self.wfile.write(blob)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = parse_qs(parsed.query)
        if path in ("/", "/health"):
            self._json(200, {"ok": True, "service": "factor-desk", "port": PORT})
            return
        if path == "/status":
            with _STATE_LOCK:
                self._json(200, dict(_STATE))
            return
        if path in OPTIONS_REFRESH_PATHS:
            self._start_options_refresh(query, None)
            return
        if path in REFRESH_PATHS:
            self._start_refresh(query, None)
            return
        if path in ("/gics-fill", "/gics_once", "/gics-once"):
            self._start_gics_fill(query, None)
            return
        if path in ADD_PATHS:
            self._start_add(query, None)
            return
        if path in SEARCH_PATHS:
            self._search(query, None)
            return
        self._json(404, {"ok": False, "error": "not_found", "path": path})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = parse_qs(parsed.query)
        body = _read_json_body(self)
        if path in OPTIONS_REFRESH_PATHS:
            self._start_options_refresh(query, body)
            return
        if path in REFRESH_PATHS:
            self._start_refresh(query, body)
            return
        if path in ("/gics-fill", "/gics_once", "/gics-once"):
            self._start_gics_fill(query, body)
            return
        if path in ADD_PATHS:
            self._start_add(query, body)
            return
        if path in SEARCH_PATHS:
            self._search(query, body)
            return
        self._json(404, {"ok": False, "error": "not_found", "path": path})

    def _claim_busy(self, *, kind: str, **fields: Any) -> bool:
        with _STATE_LOCK:
            if _STATE["busy"]:
                self._json(409, {"ok": False, "error": "busy", "status": dict(_STATE)})
                return False
            _STATE["busy"] = True
            _STATE["error"] = None
            _STATE["pct"] = 0
            _STATE["stage"] = "queued"
            _STATE["kind"] = kind
            for key, value in fields.items():
                _STATE[key] = value
        return True

    def _start_refresh(self, query: dict[str, list[str]], body: Any) -> None:
        if parse_options(query, body):
            self._start_options_refresh(query, body)
            return
        if not self._claim_busy(kind="refresh", options=False):
            return

        tickers = _tickers_from(query, body)
        intraday = parse_intraday(query, body)
        with _STATE_LOCK:
            _STATE["intraday"] = intraday

        def worker() -> None:
            try:
                result = run_refresh(tickers=tickers or None, intraday=intraday, options=False)
                with _STATE_LOCK:
                    _STATE["last"] = result
                    _STATE["error"] = None
                    _STATE["stage"] = "done"
                    _STATE["pct"] = 100
                    _STATE["kind"] = "refresh"
                    _STATE["options"] = False
            except Exception as exc:  # noqa: BLE001
                LOG.exception("refresh failed")
                with _STATE_LOCK:
                    _STATE["error"] = str(exc)
                    _STATE["stage"] = "error"
                    _STATE["last"] = {"ok": False, "error": str(exc), "trace": traceback.format_exc()}
            finally:
                with _STATE_LOCK:
                    _STATE["busy"] = False

        threading.Thread(target=worker, name="factor-desk-refresh", daemon=True).start()
        self._json(
            202,
            {
                "ok": True,
                "accepted": True,
                "kind": "refresh",
                "intraday": intraday,
                "options": False,
                "n_tickers": len(tickers),
            },
        )

    def _start_options_refresh(self, query: dict[str, list[str]], body: Any) -> None:
        if not self._claim_busy(kind="options", options=True, intraday=False):
            return

        tickers = _tickers_from(query, body)

        def worker() -> None:
            try:
                result = run_options_refresh(tickers=tickers or None)
                with _STATE_LOCK:
                    _STATE["last"] = result
                    _STATE["error"] = None
                    _STATE["stage"] = "done"
                    _STATE["pct"] = 100
                    _STATE["kind"] = "options"
                    _STATE["options"] = True
            except Exception as exc:  # noqa: BLE001
                LOG.exception("options refresh failed")
                with _STATE_LOCK:
                    _STATE["error"] = str(exc)
                    _STATE["stage"] = "error"
                    _STATE["last"] = {"ok": False, "error": str(exc), "trace": traceback.format_exc()}
            finally:
                with _STATE_LOCK:
                    _STATE["busy"] = False

        threading.Thread(target=worker, name="factor-desk-options-refresh", daemon=True).start()
        self._json(
            202,
            {
                "ok": True,
                "accepted": True,
                "kind": "options",
                "options": True,
                "n_tickers": len(tickers),
            },
        )

    def _search(self, query: dict[str, list[str]], body: Any) -> None:
        q = ""
        if isinstance(body, dict):
            for key in ("q", "query", "ticker", "symbol"):
                raw = body.get(key)
                if raw:
                    q = str(raw).strip()
                    break
        if not q:
            for key in ("q", "query", "ticker", "symbol"):
                if query.get(key):
                    q = str(query[key][0]).strip()
                    break
        import desk_dash  # local: search reads the desk book, not the refresh pipeline

        self._json(200, desk_dash.search_desk(q))

    def _start_add(self, query: dict[str, list[str]], body: Any) -> None:
        ticker = _one_ticker(query, body)
        if not ticker:
            self._json(400, {"ok": False, "error": "ticker_required", "message": "ticker is required."})
            return
        if not self._claim_busy(kind="add", options=False):
            return

        def worker() -> None:
            try:
                result = do_add(ticker)
                with _STATE_LOCK:
                    _STATE["last"] = result
                    _STATE["error"] = None if result.get("ok") else result.get("message")
                    _STATE["stage"] = "done" if result.get("ok") else "error"
                    _STATE["pct"] = 100
                    _STATE["kind"] = "add"
            except Exception as exc:  # noqa: BLE001
                LOG.exception("add failed")
                with _STATE_LOCK:
                    _STATE["error"] = str(exc)
                    _STATE["stage"] = "error"
                    _STATE["last"] = {"ok": False, "error": str(exc), "trace": traceback.format_exc()}
            finally:
                with _STATE_LOCK:
                    _STATE["busy"] = False

        threading.Thread(target=worker, name="factor-desk-add", daemon=True).start()
        self._json(
            202,
            {
                "ok": True,
                "accepted": True,
                "kind": "add",
                "ticker": dapi_enrich.canonical_ticker(ticker),
                "short": dapi_enrich.short_symbol(dapi_enrich.canonical_ticker(ticker)),
            },
        )

    def _start_gics_fill(self, query: dict[str, list[str]], body: Any) -> None:
        """Optional one-shot. Not invoked from /refresh."""
        if not self._claim_busy(kind="gics", options=False):
            return

        tickers = _tickers_from(query, body)

        def worker() -> None:
            try:
                book = dapi_enrich.fill_gics_sectors(
                    tickers=tickers or None,
                    progress_cb=_progress_cb,
                )
                gics_meta = (book.get("meta") or {}).get("gics_once") or {}
                result = {
                    "ok": True,
                    "gics_once": True,
                    "n": len(book.get("names") or {}),
                    "gics_filled": gics_meta.get("gics_filled"),
                    "gics_requested": gics_meta.get("gics_requested"),
                    "gics_capacity_skipped": gics_meta.get("gics_capacity_skipped"),
                }
                with _STATE_LOCK:
                    _STATE["last"] = result
                    _STATE["error"] = None
                    _STATE["stage"] = "done"
                    _STATE["pct"] = 100
            except Exception as exc:  # noqa: BLE001
                LOG.exception("gics-once failed")
                with _STATE_LOCK:
                    _STATE["error"] = str(exc)
                    _STATE["stage"] = "error"
                    _STATE["last"] = {"ok": False, "error": str(exc), "trace": traceback.format_exc()}
            finally:
                with _STATE_LOCK:
                    _STATE["busy"] = False

        threading.Thread(target=worker, name="factor-desk-gics-once", daemon=True).start()
        self._json(202, {"ok": True, "accepted": True, "gics_once": True, "n_tickers": len(tickers)})


def serve(host: str = HOST, port: int = PORT) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    httpd = ThreadingHTTPServer((host, port), DeskHandler)
    LOG.info(
        "Factor Desk sidecar http://%s:%s  (Refresh: /refresh  Options Refresh: /options-refresh)",
        host,
        port,
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        LOG.info("shutdown")
    finally:
        httpd.server_close()


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(description="Factor Desk sidecar :8765")
    p.add_argument("--host", default=HOST)
    p.add_argument("--port", type=int, default=PORT)
    p.add_argument("--once", action="store_true", help="Run one Refresh on stdout and exit")
    p.add_argument("--intraday", action="store_true")
    p.add_argument(
        "--options",
        action="store_true",
        help="With --once: run Options Refresh (pulse + rebuild) instead of main Refresh",
    )
    p.add_argument(
        "--gics-once",
        action="store_true",
        help="One-shot GICS sector fill (not Refresh) and exit",
    )
    p.add_argument("--tickers", default="")
    args = p.parse_args(argv)
    if args.gics_once:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
        tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
        book = dapi_enrich.fill_gics_sectors(tickers=tickers or None)
        gics_meta = (book.get("meta") or {}).get("gics_once") or {}
        print(
            json.dumps(
                {
                    "ok": True,
                    "gics_once": True,
                    "n": len(book.get("names") or {}),
                    "gics_filled": gics_meta.get("gics_filled"),
                    "gics_requested": gics_meta.get("gics_requested"),
                },
                indent=2,
                default=str,
            )
        )
        return 0
    if args.once:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
        tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
        if args.options:
            result = run_options_refresh(tickers=tickers or None)
        else:
            result = run_refresh(tickers=tickers or None, intraday=args.intraday)
        print(json.dumps(result, indent=2, default=str))
        return 0
    serve(args.host, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
