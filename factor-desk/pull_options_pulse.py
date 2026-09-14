"""Pull options pulse and compute score v2.

Score v2 is unchanged::

    0.5 * mean(top-3 log1p(vol/avg20))
    + 0.3 * log(opt_vol / stock_ADV)
    + 0.2 * log(vol / OI)

Skew / IV summary is additive (``skew``, ``iv_call_atm``, ``iv_put_atm``,
``skew_25d_proxy``). Missing DELTA / IV stay null — never invented.

Contract set: 15C + 15P. UI top 20×2. ``DEFAULT_MAX_NAMES = 0`` (no cap).
"""

from __future__ import annotations

import json
import logging
import math
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import dapi_enrich  # noqa: E402

LOG = logging.getLogger("pull_options_pulse")

DEFAULT_MAX_NAMES = 0  # no name cap in the pull
CONTRACTS_CALLS = 15
CONTRACTS_PUTS = 15
UI_TOP_EACH = 20
OPTIONS_ABNORMAL = "options_abnormal.json"
EPS = 1e-12


def score_v2(
    top_contracts: Sequence[Mapping[str, Any]],
    opt_vol: float | None,
    stock_adv: float | None,
    *,
    vol_oi_contract: Mapping[str, Any] | None = None,
) -> float | None:
    """Exact v2 mix. Returns None when inputs cannot be logged (no invention)."""
    ratios: list[float] = []
    for row in top_contracts:
        vol = dapi_enrich.as_float(row.get("volume") or row.get("VOLUME") or row.get("vol"))
        avg20 = dapi_enrich.as_float(row.get("avg20") or row.get("volume_avg_20d") or row.get("avg_volume"))
        if vol is None or avg20 is None or avg20 <= 0:
            continue
        ratios.append(math.log1p(vol / avg20))
    ratios.sort(reverse=True)
    top3 = ratios[:3]
    if not top3:
        term_a = None
    else:
        term_a = sum(top3) / len(top3)

    term_b = None
    ov = dapi_enrich.as_float(opt_vol)
    adv = dapi_enrich.as_float(stock_adv)
    if ov is not None and adv is not None and ov > 0 and adv > 0:
        term_b = math.log(ov / adv)

    ref = vol_oi_contract or (top_contracts[0] if top_contracts else None)
    term_c = None
    if ref:
        vol = dapi_enrich.as_float(ref.get("volume") or ref.get("VOLUME") or ref.get("vol"))
        oi = dapi_enrich.as_float(ref.get("oi") or ref.get("OPEN_INT") or ref.get("open_int"))
        if vol is not None and oi is not None and vol > 0 and oi > 0:
            term_c = math.log(vol / oi)

    parts = [term_a, term_b, term_c]
    if all(p is None for p in parts):
        return None
    return (
        0.5 * (term_a if term_a is not None else 0.0)
        + 0.3 * (term_b if term_b is not None else 0.0)
        + 0.2 * (term_c if term_c is not None else 0.0)
    )


def enrich_contract(row: Mapping[str, Any]) -> dict[str, Any]:
    """Keep VOLUME/OI; attach DELTA / IVOL_MID only when present."""
    out = dict(row)
    delta = None
    for key in ("delta", "DELTA", "opt_delta"):
        delta = dapi_enrich.as_float(out.get(key))
        if delta is not None:
            break
    iv = None
    for key in ("iv", "iv_mid", "IVOL_MID", "OPT_IMPLIED_VOLATILITY", "implied_vol"):
        iv = dapi_enrich.as_float(out.get(key))
        if iv is not None:
            break
    vol = dapi_enrich.as_float(out.get("volume") or out.get("VOLUME") or out.get("vol"))
    oi = dapi_enrich.as_float(out.get("oi") or out.get("OPEN_INT") or out.get("open_int"))
    out["delta"] = delta
    out["iv"] = iv
    out["volume"] = vol
    out["oi"] = oi
    return out


def attach_skew(name_rec: dict[str, Any], contracts: Sequence[Mapping[str, Any]] | None) -> dict[str, Any]:
    """Add skew/IV summary fields without touching score v2."""
    enriched = [enrich_contract(c) for c in (contracts or [])]
    skew = dapi_enrich.summarize_skew(enriched)
    name_rec["contracts"] = enriched
    name_rec["skew"] = skew
    name_rec["iv_call_atm"] = skew.get("call_iv_atm")
    name_rec["iv_put_atm"] = skew.get("put_iv_atm")
    name_rec["skew_25d_proxy"] = skew.get("skew_25d_proxy")
    return name_rec


def pulse_name(
    ticker: str,
    contracts: Sequence[Mapping[str, Any]] | None,
    *,
    opt_vol: float | None = None,
    stock_adv: float | None = None,
    score: float | None = None,
) -> dict[str, Any]:
    enriched = [enrich_contract(c) for c in (contracts or [])]
    rec: dict[str, Any] = {
        "ticker": ticker,
        "score_v2": score if score is not None else score_v2(enriched, opt_vol, stock_adv),
        "opt_vol": dapi_enrich.as_float(opt_vol),
        "stock_adv": dapi_enrich.as_float(stock_adv),
        "n_contracts": len(enriched),
    }
    attach_skew(rec, enriched)
    return rec


def write_abnormal(book: Mapping[str, Any], path: Path | str | None = None) -> Path:
    dest = Path(path) if path is not None else HERE / OPTIONS_ABNORMAL
    dest.write_text(json.dumps(book, indent=2, default=str) + "\n", encoding="utf-8")
    return dest


def run_pulse(
    tickers: Iterable[str] | None = None,
    *,
    session: Any | None = None,
    chains: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    progress_cb: Any | None = None,
    root: Path | None = None,
    write: bool = True,
) -> dict[str, Any]:
    """On-demand pulse. Without DAPI chains, writes a structured empty/passthrough book."""
    if progress_cb:
        progress_cb(0.42, "options pulse")
    names = [dapi_enrich.name_key(t) for t in (tickers or [])]
    out_names: dict[str, Any] = {}
    src = chains or {}
    for t in names:
        contracts = src.get(t) or src.get(dapi_enrich.name_key(t)) or []
        out_names[t] = pulse_name(t, contracts)
    book = {
        "asof": dapi_enrich._now_iso(),
        "names": out_names,
        "meta": {
            "score": "v2",
            "max_names": DEFAULT_MAX_NAMES,
            "contracts": f"{CONTRACTS_CALLS}C+{CONTRACTS_PUTS}P",
            "ui_top": UI_TOP_EACH,
            "source": "pull_options_pulse",
            "session": None if session is None else type(session).__name__,
        },
    }
    if write:
        write_abnormal(book, (Path(root) if root else HERE) / OPTIONS_ABNORMAL)
    if progress_cb:
        progress_cb(0.50, "options pulse done")
    return book


def pull_pulse(**kwargs: Any) -> dict[str, Any]:
    return run_pulse(**kwargs)


def run(**kwargs: Any) -> dict[str, Any]:
    return run_pulse(**kwargs)


def main(argv: list[str] | None = None) -> int:
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="Options pulse + score v2 + skew summary")
    p.add_argument("--tickers", default="")
    args = p.parse_args(argv)
    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
    book = run_pulse(tickers or dapi_enrich.discover_tickers())
    print(json.dumps({"asof": book["asof"], "n": len(book["names"])}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
