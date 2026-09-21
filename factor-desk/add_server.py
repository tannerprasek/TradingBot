"""Sidecar HTTP server for Factor Desk (port 8765).

Refresh pipeline (matches live desk)::

    prices → options pulse → dapi_enrich (~50–60%) → rebuild / write_dash

Intraday (default off)::

    GET  /refresh?intraday=1
    POST /refresh          JSON body {"intraday": 1}  or query string

Portfolio (does not change Refresh)::

    GET  /api/quote/{ticker}
    POST /api/portfolio     JSON body {"csv": "position,ticker\\n100,AAPL"}
    GET  /api/portfolio     last uploaded scorecard


When ``intraday=1``, enrich also pulls session volume vs ADV. When off, enrich
behavior is unchanged. Capacity (``BLOOMBERG_LIMIT``) skips enrich the same way
options pulse degrades — Refresh still finishes.

Thin ENRICH HOOK: Desktop copies ``run_dapi_enrich_stage`` / ``parse_intraday``
into the live ``add_server.py`` if this file is not the one serving :8765.
"""

from __future__ import annotations

import json
import logging
import sys
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import dapi_enrich  # noqa: E402
import portfolio  # noqa: E402

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
}


def parse_intraday(query: dict[str, list[str]], body: Any | None = None) -> bool:
    """Accept query/body flag ``intraday=1`` (also true/yes/on). Default off."""
    if "intraday" in query:
        return dapi_enrich.parse_intraday_flag(query.get("intraday", [""])[0])
    if isinstance(body, dict) and "intraday" in body:
        return dapi_enrich.parse_intraday_flag(body.get("intraday"))
    return False


def _set_progress(pct: float, stage: str) -> None:
    with _STATE_LOCK:
        _STATE["pct"] = int(max(0, min(100, round(pct * 100 if pct <= 1 else pct))))
        _STATE["stage"] = stage


def _progress_cb(frac: float, stage: str) -> None:
    _set_progress(frac, stage)


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


def run_options_stage(tickers: list[str], progress_cb: Callable[[float, str], None]) -> tuple[Any, str | None]:
    progress_cb(0.42, "options pulse")
    result, err = _try_call(
        "pull_options_pulse",
        ("pull_pulse", "run_pulse", "run"),
        tickers=tickers,
        progress_cb=progress_cb,
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


def run_rebuild_stage(progress_cb: Callable[[float, str], None], root: Path | None = None) -> str | None:
    progress_cb(0.65, "rebuild")
    _, err_v0 = _try_call("run_v0", ("run", "main"), root=root)
    if err_v0 and not err_v0.startswith("run_v0_missing"):
        LOG.warning("run_v0: %s", err_v0)
    progress_cb(0.80, "write_dash")
    _, err_wd = _try_call("write_dash", ("write", "main"), root=root)
    if err_wd:
        LOG.warning("write_dash: %s", err_wd)
    progress_cb(0.90, "desk_dash")
    _, err_dd = _try_call("desk_dash", ("rebuild", "assemble_and_write", "main"), root=root)
    if err_dd and not str(err_dd).startswith("desk_dash"):
        LOG.warning("desk_dash: %s", err_dd)
    progress_cb(1.0, "done")
    return err_wd or err_dd


def run_refresh(
    *,
    tickers: list[str] | None = None,
    intraday: bool = False,
    root: Path | None = None,
    progress_cb: Callable[[float, str], None] | None = None,
) -> dict[str, Any]:
    """Full Refresh. Enrich file missing afterwards is OK — dash still builds."""
    cb = progress_cb or _progress_cb
    root = Path(root) if root is not None else HERE
    names = list(tickers or dapi_enrich.discover_tickers(root))
    cb(0.05, "start")
    prices_ctx, price_err = run_prices_stage(names, cb)
    options_res, opt_err = run_options_stage(names, cb)
    options_by_name = None
    if isinstance(options_res, dict):
        options_by_name = options_res.get("names") or options_res.get("chains") or options_res
        if not isinstance(options_by_name, dict):
            options_by_name = None
    book = run_dapi_enrich_stage(
        names,
        intraday=intraday,
        prices_ctx=prices_ctx if isinstance(prices_ctx, dict) else None,
        options_by_name=options_by_name,
        progress_cb=cb,
        root=root,
    )
    rebuild_err = run_rebuild_stage(cb, root=root)
    return {
        "ok": True,
        "n": len(book.get("names") or {}),
        "intraday": bool(intraday),
        "enrich_path": str(dapi_enrich.default_out_path(root)),
        "capacity_skipped": bool((book.get("meta") or {}).get("capacity_skipped")),
        "skips": list((book.get("meta") or {}).get("skips") or []),
        "price_err": price_err,
        "opt_err": opt_err,
        "rebuild_err": rebuild_err,
        "asof": book.get("asof"),
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
        if path == "/refresh":
            self._start_refresh(query, None)
            return
        if path.startswith("/api/quote") or path.startswith("/api/quotes"):
            portfolio.handle_quote_request(self, parsed)
            return
        if path in ("/api/portfolio", "/api/portfolio/last"):
            portfolio.handle_portfolio_get(self)
            return
        self._json(404, {"ok": False, "error": "not_found", "path": path})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = parse_qs(parsed.query)
        body = _read_json_body(self)
        if path == "/refresh":
            self._start_refresh(query, body)
            return
        if path in ("/api/portfolio", "/api/portfolio/run"):
            portfolio.handle_portfolio_run(self, body)
            return
        self._json(404, {"ok": False, "error": "not_found", "path": path})

    def _start_refresh(self, query: dict[str, list[str]], body: Any) -> None:
        with _STATE_LOCK:
            if _STATE["busy"]:
                self._json(409, {"ok": False, "error": "busy", "status": dict(_STATE)})
                return
            _STATE["busy"] = True
            _STATE["error"] = None
            _STATE["pct"] = 0
            _STATE["stage"] = "queued"

        tickers: list[str] = []
        if isinstance(body, dict) and body.get("tickers"):
            raw = body["tickers"]
            tickers = raw if isinstance(raw, list) else str(raw).split(",")
            tickers = [t.strip() for t in tickers if str(t).strip()]
        if query.get("tickers"):
            tickers.extend(t.strip() for t in query["tickers"][0].split(",") if t.strip())
        intraday = parse_intraday(query, body)
        with _STATE_LOCK:
            _STATE["intraday"] = intraday

        def worker() -> None:
            try:
                result = run_refresh(tickers=tickers or None, intraday=intraday)
                with _STATE_LOCK:
                    _STATE["last"] = result
                    _STATE["error"] = None
                    _STATE["stage"] = "done"
                    _STATE["pct"] = 100
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
        self._json(202, {"ok": True, "accepted": True, "intraday": intraday, "n_tickers": len(tickers)})


def serve(host: str = HOST, port: int = PORT) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    httpd = ThreadingHTTPServer((host, port), DeskHandler)
    LOG.info("Factor Desk sidecar http://%s:%s  (Refresh: GET/POST /refresh?intraday=1)", host, port)
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
    p.add_argument("--tickers", default="")
    args = p.parse_args(argv)
    if args.once:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
        tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
        result = run_refresh(tickers=tickers or None, intraday=args.intraday)
        print(json.dumps(result, indent=2, default=str))
        return 0
    serve(args.host, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
