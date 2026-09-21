"""Factor Desk DAPI enrichment pack.

Pipeline slot (after prices, before rebuild; options pulse is a separate Options Refresh)::

    prices → dapi_enrich → rebuild / write_dash

Never invent Bloomberg numbers. A missing or invalid field is ``None`` plus a
reason string. One field or one name failing must not abort Refresh.

Public API
----------
- ``enrich_book(tickers, session=None, ...) -> dict``
- ``write_enrichment(book, path=None) -> Path``
- ``load_enrichment(path=None) -> dict | None``
- ``build_enrich_pills(rec) -> list[dict]``
- ``attach_card_fields(card, rec) -> dict``
- ``fill_gics_sectors(book=None, session=None, ...) -> dict``  (one-shot; not Refresh)
- ``parse_gics_sector_name(value) -> (name, reason)``

Output: ``dapi_enrichment.json`` next to ``options_abnormal.json``.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, MutableMapping, Sequence

HERE = Path(__file__).resolve().parent
LOG = logging.getLogger("dapi_enrich")

ENRICH_FILENAME = "dapi_enrichment.json"
OPTIONS_ABNORMAL_FILENAME = "options_abnormal.json"
GICS_CACHE_FILENAME = "gics_sectors.json"

# ---------------------------------------------------------------------------
# Thresholds (document in docs/DAPI-ENRICH.md). Pointers only — not signals.
# ---------------------------------------------------------------------------

SI_CROWDED_RATIO = 8.0  # days-to-cover
SI_LIGHT_RATIO = 1.0
SI_CROWDED_PCT_FLOAT = 15.0
SI_LIGHT_PCT_FLOAT = 2.0

IV_RICH_RATIO = 1.15  # implied / realized
IV_CHEAP_RATIO = 0.85

ILLIQUID_ADV_USD = 5_000_000.0  # $5m ADV$
ILLIQUID_ADV_SHARES = 200_000.0

INST_HIGH_PCT = 85.0

EVT_NEAR_DAYS = 5
EVT_WATCH_DAYS = 20

CREDIT_OAS_STRESS_BPS = 200.0
MOM_STRONG_ABS = 0.08  # |20d return|

DEFAULT_CHUNK_SIZE = 25
DEFAULT_MARKET_TICKER = "SPY US Equity"
REFDATA_SERVICE = "//blp/refdata"

CAPACITY_MARKERS = (
    "BLOOMBERG_LIMIT",
    "LIMIT REACHED",
    "DAILY LIMIT",
    "REQUEST LIMIT",
    "CAPACITY",
    "TOO MANY REQUESTS",
    "MAX HITS",
    "NOT LOGGED IN",
    "SESSION TERMINATED",
)

NA_STRINGS = {
    "",
    "N/A",
    "N.A.",
    "NA",
    "#N/A",
    "#N/A N/A",
    "#N/A FIELD NOT APPLICABLE",
    "#N/A INVALID SECURITY",
    "#N/A UNKNOWN FIELD",
    "#N/A TIMED OUT",
    "#N/A PERM REQ",
    "#N/A SECURITY",
    "NAN",
    "NONE",
    "NULL",
}

# ---------------------------------------------------------------------------
# Field candidates. First mnemonic that returns a usable value wins.
# Aliases are attempted so Desktop FLDS can confirm which resolve.
# ---------------------------------------------------------------------------

FIELD_CANDIDATES: dict[str, tuple[str, ...]] = {
    # 1. Short / borrow
    "short_int": ("SHORT_INT", "EQY_SHORT_INTEREST", "SHORT_INTEREST"),
    "si_ratio": ("SHORT_INT_RATIO",),
    "si_pct_float": ("SI_PERCENT_EQUITY_FLOAT", "SHORT_INT_FLOAT", "SHORT_INT_PCT_FLOAT"),
    "borrow_fee": (
        "INDICATIVE_FEE",
        "EQY_LEND_RATE",
        "STOCK_LOAN_FEE",
        "BORROW_COST",
    ),
    "borrow_util": (
        "UTILIZATION",
        "EQY_LEND_UTILIZATION",
        "STOCK_LOAN_UTILIZATION",
        "SI_UTILIZATION",
    ),
    # 2. Vol regime
    "vol_30d": (
        "VOLATILITY_30D",
        "VOLATILITY_20D",
        "REALIZED_VOL_30D",
        "VOLATILITY_21D",
    ),
    "vol_90d": (
        "VOLATILITY_90D",
        "VOLATILITY_60D",
        "VOLATILITY_120D",
        "VOLATILITY_180D",
        "REALIZED_VOL_90D",
    ),
    "iv_call": ("HIST_CALL_IMP_VOL", "CALL_IMP_VOL_30D", "IVOL_CALL"),
    "iv_put": ("HIST_PUT_IMP_VOL", "PUT_IMP_VOL_30D", "IVOL_PUT"),
    "iv_mid": (
        "IVOL_MID",
        "IVOL_MEAN",
        "30DAY_IMPVOL_100.0%MNY_DF",
        "IMP_VOL_30D",
        "HIST_IMPVOL_30D",
    ),
    # 3. Options contract (used on option ticks, not the equity)
    "opt_delta": ("DELTA", "OPT_DELTA", "DELTA_MID"),
    "opt_iv": ("IVOL_MID", "OPT_IMPLIED_VOLATILITY", "IVOL_MEAN"),
    "opt_volume": ("VOLUME", "OPT_VOLUME"),
    "opt_oi": ("OPEN_INT", "OPEN_INTEREST", "OPT_OPEN_INT"),
    # 4. Liquidity / friction
    "volume_avg_20d": ("VOLUME_AVG_20D", "VOLUME_AVG_30D", "AVG_DAILY_VOLUME_20D"),
    "px_last": ("PX_LAST", "LAST_PRICE", "PX_CLOSE"),
    # 1-day price change, percent points (1.2 = +1.2%). Not a decimal return.
    "chg_pct_1d": ("CHG_PCT_1D",),
    "free_float_pct": ("EQY_FREE_FLOAT_PCT", "EQY_FREE_FLOAT_PERCENT"),
    # 5. Ownership
    "inst_pct": ("EQY_INST_PCT_SH_OUT",),
    "etf_pct": ("EQY_ETF_PCT_SH_OUT",),  # skip gracefully if it never resolves
    # 6. Event (badge only)
    "earn_dt": (
        "EARNINGS_ANNOUNCEMENT_DT",
        "EXPECTED_REPORT_DT",
        "EQY_FUND_CRNCY_ADJ_NEXT_EPS_DT",
        "NEXT_EARNINGS_ANNOUNCEMENT_DT",
        "ANNOUNCEMENT_DT",
    ),
    # 7. Credit on the equity (never invent OAS)
    "cds_oas": (
        "CDS_SPREAD_5Y",
        "CDS_SPREAD_MID",
        "CDS_SPREAD",
        "FIVE_YEAR_CDS_SPREAD",
    ),
    "bond_oas": ("OAS_SPREAD", "OAS_TO_WORST", "OAS_SPREAD_MID", "IDX_OAS"),
    # 8. Intraday (only requested when Refresh intraday=1)
    "session_volume": ("VOLUME", "PX_VOLUME"),
    "vwap": ("EQY_WEIGHTED_AVG_PX", "VWAP"),
    "turnover": ("EQY_TURNOVER", "TURNOVER"),
    # 9. Beta
    "beta": ("BETA_ADJ_OVERRIDABLE", "BETA_PRIMES", "EQY_BETA", "EQY_RAW_BETA"),
    # GICS sector name for home filter chips. NOT in EQUITY_PACK_KEYS — Refresh
    # must not pull this on every run. One-shot: fill_gics_sectors / --gics-once.
    "gics_sector_name": ("GICS_SECTOR_NAME", "GICS_SECTOR"),
}

# Equity-only packs pulled on the name. Option-contract fields stay separate.
EQUITY_PACK_KEYS = (
    "short_int",
    "si_ratio",
    "si_pct_float",
    "borrow_fee",
    "borrow_util",
    "vol_30d",
    "vol_90d",
    "iv_call",
    "iv_put",
    "iv_mid",
    "volume_avg_20d",
    "px_last",
    "chg_pct_1d",
    "free_float_pct",
    "inst_pct",
    "etf_pct",
    "earn_dt",
    "cds_oas",
    "bond_oas",
    "beta",
)

INTRADAY_PACK_KEYS = ("session_volume", "vwap", "turnover")

OPTION_PACK_KEYS = ("opt_delta", "opt_iv", "opt_volume", "opt_oi")

# One-shot / cache only. Never appended to the Refresh equity pack.
GICS_PACK_KEYS = ("gics_sector_name",)

# Optional local equity → CDS/bond yellow-key map. Empty by default so we never
# pretend a mapped ticker has an OAS we did not pull. Desktop may extend.
EQUITY_CREDIT_TICKERS: dict[str, dict[str, str]] = {}

CARD_FIELDS = (
    "si_ratio",
    "vol_regime",
    "liq",
    "inst_pct",
    "event_days",
    "beta",
    "credit",
    "enrich_pills",
    "gics_sector_name",
)


class CapacityError(RuntimeError):
    """DAPI daily/session capacity — enrich must skip like options pulse."""

    def __init__(self, reason: str = "BLOOMBERG_LIMIT") -> None:
        super().__init__(reason)
        self.reason = reason


class EnrichSkip(RuntimeError):
    """Non-fatal skip of the enrich stage."""


# ---------------------------------------------------------------------------
# Parsing helpers — no invented numbers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _today() -> date:
    return datetime.now(timezone.utc).date()


def is_na(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return True
    if isinstance(value, str) and value.strip().upper() in NA_STRINGS:
        return True
    return False


def as_float(value: Any) -> float | None:
    if is_na(value):
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        return None
    if isinstance(value, date):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(out) or math.isinf(out):
        return None
    return out


def as_str(value: Any) -> str | None:
    """Text field helper (GICS names). Never maps a code to a name."""
    if is_na(value):
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        if value.is_integer():
            return str(int(value))
        text = str(value).strip()
        return text or None
    if isinstance(value, int):
        return str(value)
    text = str(value).strip()
    if not text or text.upper() in NA_STRINGS:
        return None
    return text


def parse_gics_sector_name(value: Any) -> tuple[str | None, str | None]:
    """Return ``(name, null_reason)``. Numeric GICS codes are not names."""
    text = as_str(value)
    if text is None:
        return None, "no_gics_name"
    compact = text.replace(" ", "")
    if compact.isdigit() and len(compact) <= 8:
        return None, "code_only_no_name"
    return text, None


def as_date(value: Any) -> date | None:
    if is_na(value):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%m/%d/%Y", "%m/%d/%y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text[:10] if fmt == "%Y-%m-%d" else text, fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def looks_like_capacity(text: Any) -> bool:
    blob = str(text or "").upper()
    return any(marker in blob for marker in CAPACITY_MARKERS)


def flatten_candidates(keys: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for key in keys:
        for mnemonic in FIELD_CANDIDATES.get(key, ()):
            if mnemonic not in seen:
                seen.add(mnemonic)
                out.append(mnemonic)
    return out


def first_success(
    raw: Mapping[str, Any] | None,
    key: str,
    *,
    as_type: str = "float",
) -> tuple[Any, str | None, str | None]:
    """Return ``(value, field_used, null_reason)``. Never fabricates a value."""
    if raw is None:
        return None, None, "no_refdata_row"
    for mnemonic in FIELD_CANDIDATES.get(key, ()):
        if mnemonic not in raw:
            continue
        value = raw[mnemonic]
        if is_na(value):
            continue
        if as_type == "date":
            parsed = as_date(value)
            if parsed is None:
                continue
            return parsed, mnemonic, None
        if as_type == "str":
            parsed = as_str(value)
            if parsed is None:
                continue
            return parsed, mnemonic, None
        parsed = as_float(value)
        if parsed is None:
            continue
        return parsed, mnemonic, None
    attempted = FIELD_CANDIDATES.get(key, ())
    if not attempted:
        return None, None, "no_candidates"
    if raw and any(m in raw for m in attempted):
        return None, None, "candidates_present_but_invalid"
    return None, None, "no_candidate_resolved"


def normalize_ticker(ticker: str, default_yellow: str = "US Equity") -> str:
    text = (ticker or "").strip()
    if not text:
        return text
    upper = text.upper()
    yellow = (
        "EQUITY",
        "INDEX",
        "COMDTY",
        "CURNCY",
        "CORP",
        "GOVT",
        "MTGE",
        "MUNI",
        "PFD",
    )
    if any(upper.endswith(" " + y) or upper.endswith(y) for y in yellow):
        # Canonicalize "Equity" spelling only; leave other yellow keys.
        if upper.endswith(" EQUITY"):
            return text[: -len("Equity")] + "Equity" if text.endswith("equity") else text
        return text
    if upper.endswith(" US") or upper.endswith(" UW") or upper.endswith(" UN"):
        return text + " Equity"
    return f"{text} {default_yellow}"


def name_key(ticker: str) -> str:
    return normalize_ticker(ticker)


# ---------------------------------------------------------------------------
# Session abstraction
# ---------------------------------------------------------------------------


class RefDataSession:
    """Minimal session protocol: ``refdata(tickers, fields) -> {ticker: {field: val}}``."""

    def refdata(self, tickers: Sequence[str], fields: Sequence[str]) -> dict[str, dict[str, Any]]:
        raise NotImplementedError


class NullSession(RefDataSession):
    """Dry-run / no-DAPI session. Every field is absent — never invents values."""

    def refdata(self, tickers: Sequence[str], fields: Sequence[str]) -> dict[str, dict[str, Any]]:
        return {t: {} for t in tickers}


class DictSession(RefDataSession):
    """Test double. ``table[ticker][field] = value``."""

    def __init__(self, table: Mapping[str, Mapping[str, Any]] | None = None) -> None:
        self.table = {k: dict(v) for k, v in (table or {}).items()}

    def refdata(self, tickers: Sequence[str], fields: Sequence[str]) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for t in tickers:
            row = self.table.get(t) or self.table.get(name_key(t)) or {}
            out[t] = {f: row[f] for f in fields if f in row}
        return out


class BlpapiSession(RefDataSession):
    """Thin wrapper around an existing ``blpapi.Session`` (Desktop keepalive)."""

    def __init__(self, session: Any, service: str = REFDATA_SERVICE) -> None:
        self.session = session
        self.service = service

    def refdata(self, tickers: Sequence[str], fields: Sequence[str]) -> dict[str, dict[str, Any]]:
        return _blpapi_refdata(self.session, tickers, fields, self.service)


def wrap_session(session: Any | None) -> RefDataSession | None:
    if session is None:
        return None
    if isinstance(session, RefDataSession):
        return session
    if hasattr(session, "refdata") and callable(session.refdata):
        return session  # type: ignore[return-value]
    return BlpapiSession(session)


def open_dapi_session() -> RefDataSession | None:
    """Reuse ``dapi_keepalive`` if present; else try a fresh blpapi start."""
    try:
        import dapi_keepalive as keepalive  # type: ignore
    except ImportError:
        keepalive = None
    if keepalive is not None:
        for name in ("get_session", "ensure_session", "session"):
            obj = getattr(keepalive, name, None)
            if callable(obj):
                try:
                    sess = obj()
                except Exception as exc:  # noqa: BLE001
                    LOG.warning("dapi_keepalive.%s failed: %s", name, exc)
                    sess = None
                if sess is not None:
                    return wrap_session(sess)
            elif obj is not None:
                return wrap_session(obj)
    try:
        import blpapi  # type: ignore
    except ImportError:
        LOG.info("blpapi not installed — enrich will run without DAPI")
        return None
    try:
        sess = blpapi.Session()
        if not sess.start():
            LOG.warning("blpapi.Session.start() failed")
            return None
        if not sess.openService(REFDATA_SERVICE):
            LOG.warning("could not open %s", REFDATA_SERVICE)
            return None
        return BlpapiSession(sess)
    except Exception as exc:  # noqa: BLE001
        if looks_like_capacity(exc):
            raise CapacityError(str(exc)) from exc
        LOG.warning("blpapi session open failed: %s", exc)
        return None


def _element_to_py(el: Any) -> Any:
    try:
        if el.isNull():
            return None
    except Exception:  # noqa: BLE001
        pass
    try:
        dtype = el.datatype()
    except Exception:  # noqa: BLE001
        dtype = None
    name = str(getattr(dtype, "name", lambda: "")() or dtype or "").upper()
    getters = (
        ("DATE", el.getValueAsDatetime),
        ("TIME", el.getValueAsDatetime),
        ("FLOAT", el.getValueAsFloat),
        ("DOUBLE", el.getValueAsFloat),
        ("INT", el.getValueAsInteger),
        ("INT32", el.getValueAsInteger),
        ("INT64", el.getValueAsInteger),
        ("BOOL", el.getValueAsBool),
    )
    for token, getter in getters:
        if token in name:
            try:
                return getter()
            except Exception:  # noqa: BLE001
                break
    try:
        return el.getValue()
    except Exception:  # noqa: BLE001
        try:
            return el.getValueAsString()
        except Exception:  # noqa: BLE001
            return None


def _blpapi_refdata(
    session: Any,
    tickers: Sequence[str],
    fields: Sequence[str],
    service: str = REFDATA_SERVICE,
) -> dict[str, dict[str, Any]]:
    import blpapi  # type: ignore

    svc = session.getService(service)
    req = svc.createRequest("ReferenceDataRequest")
    for t in tickers:
        req.append("securities", t)
    for f in fields:
        req.append("fields", f)
    cid = session.sendRequest(req)
    out: dict[str, dict[str, Any]] = {t: {} for t in tickers}

    while True:
        ev = session.nextEvent(5000)
        ev_type = ev.eventType()
        for msg in ev:
            if looks_like_capacity(msg):
                raise CapacityError(str(msg))
            if msg.hasElement("responseError"):
                err = msg.getElement("responseError")
                text = err.getElementAsString("message") if err.hasElement("message") else str(err)
                if looks_like_capacity(text):
                    raise CapacityError(text)
                LOG.warning("refdata responseError: %s", text)
                continue
            if not msg.hasElement("securityData"):
                continue
            sec_data = msg.getElement("securityData")
            n = sec_data.numValues() if sec_data.isArray() else 1
            for i in range(n):
                row = sec_data.getValueAsElement(i) if sec_data.isArray() else sec_data
                ticker = row.getElementAsString("security") if row.hasElement("security") else ""
                if row.hasElement("securityError"):
                    serr = row.getElement("securityError")
                    text = serr.getElementAsString("message") if serr.hasElement("message") else str(serr)
                    if looks_like_capacity(text):
                        raise CapacityError(text)
                    LOG.info("skip security %s: %s", ticker, text)
                    continue
                if row.hasElement("fieldExceptions"):
                    ex = row.getElement("fieldExceptions")
                    n_ex = ex.numValues() if ex.isArray() else 0
                    for j in range(n_ex):
                        item = ex.getValueAsElement(j)
                        fname = item.getElementAsString("fieldId") if item.hasElement("fieldId") else "?"
                        info = item.getElement("errorInfo") if item.hasElement("errorInfo") else None
                        msg_txt = ""
                        if info is not None and info.hasElement("message"):
                            msg_txt = info.getElementAsString("message")
                        if looks_like_capacity(msg_txt):
                            raise CapacityError(msg_txt)
                        LOG.debug("field skip %s %s: %s", ticker, fname, msg_txt)
                parsed: dict[str, Any] = {}
                if row.hasElement("fieldData"):
                    fd = row.getElement("fieldData")
                    for k in range(fd.numElements()):
                        el = fd.getElement(k)
                        parsed[str(el.name())] = _element_to_py(el)
                out[ticker or list(out)[i] if i < len(out) else ticker] = parsed
                if ticker and ticker not in out:
                    out[ticker] = parsed
        if ev_type in (blpapi.Event.RESPONSE, blpapi.Event.TIMEOUT):
            if ev_type == blpapi.Event.TIMEOUT:
                LOG.warning("refdata timeout after partial results")
            break
        # PARTIAL_RESPONSE: keep looping
    _ = cid
    return out


def batch_refdata(
    session: RefDataSession,
    tickers: Sequence[str],
    fields: Sequence[str],
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    progress_cb: Callable[[float, str], None] | None = None,
    progress_lo: float = 0.0,
    progress_hi: float = 1.0,
) -> tuple[dict[str, dict[str, Any]], list[str], bool]:
    """Pull refdata in chunks. Returns ``(rows, skips, capacity_hit)``.

    A single name/field failure is logged and skipped. Capacity aborts remaining
    chunks but keeps rows already received.
    """
    rows: dict[str, dict[str, Any]] = {}
    skips: list[str] = []
    capacity_hit = False
    clean_tickers = [t for t in tickers if t]
    clean_fields = [f for f in fields if f]
    if not clean_tickers or not clean_fields:
        return rows, skips, False

    chunks = [
        clean_tickers[i : i + chunk_size] for i in range(0, len(clean_tickers), chunk_size)
    ]
    for idx, chunk in enumerate(chunks):
        frac = progress_lo + (progress_hi - progress_lo) * (idx / max(len(chunks), 1))
        if progress_cb:
            progress_cb(frac, f"enrich chunk {idx + 1}/{len(chunks)}")
        try:
            got = session.refdata(chunk, clean_fields)
        except CapacityError as exc:
            LOG.warning("DAPI capacity during enrich — skipping remaining chunks (%s)", exc.reason)
            skips.append(f"capacity:{exc.reason}")
            capacity_hit = True
            break
        except Exception as exc:  # noqa: BLE001
            if looks_like_capacity(exc):
                LOG.warning("DAPI capacity during enrich — skipping remaining (%s)", exc)
                skips.append(f"capacity:{exc}")
                capacity_hit = True
                break
            LOG.warning("enrich chunk %s failed (%s) — skip chunk", idx + 1, exc)
            skips.append(f"chunk:{idx + 1}:{exc}")
            continue
        for t in chunk:
            rows[t] = got.get(t) or {}
        for extra_t, extra_row in got.items():
            rows.setdefault(extra_t, extra_row)
    return rows, skips, capacity_hit


# ---------------------------------------------------------------------------
# Layer builders
# ---------------------------------------------------------------------------


def _put(
    rec: MutableMapping[str, Any],
    used: MutableMapping[str, str],
    reasons: MutableMapping[str, str],
    dest: str,
    raw: Mapping[str, Any] | None,
    key: str,
    *,
    as_type: str = "float",
) -> Any:
    value, field, reason = first_success(raw, key, as_type=as_type)
    rec[dest] = value.isoformat() if isinstance(value, date) else value
    if field:
        used[dest] = field
    if reason:
        reasons[dest] = reason
    return value


def classify_si(si_ratio: float | None, si_pct_float: float | None) -> str | None:
    if si_ratio is not None:
        if si_ratio >= SI_CROWDED_RATIO:
            return "crowded"
        if si_ratio <= SI_LIGHT_RATIO:
            return "light"
    if si_pct_float is not None:
        if si_pct_float >= SI_CROWDED_PCT_FLOAT:
            return "crowded"
        if si_pct_float <= SI_LIGHT_PCT_FLOAT:
            return "light"
    return None


def classify_vol_regime(realized: float | None, implied: float | None) -> str | None:
    if realized is None or implied is None or realized <= 0:
        return None
    ratio = implied / realized
    if ratio >= IV_RICH_RATIO:
        return "iv_rich"
    if ratio <= IV_CHEAP_RATIO:
        return "iv_cheap"
    return "neutral"


def classify_liq(adv_shares: float | None, adv_usd: float | None) -> str | None:
    illiq = False
    have = False
    if adv_usd is not None:
        have = True
        if adv_usd < ILLIQUID_ADV_USD:
            illiq = True
    if adv_shares is not None:
        have = True
        if adv_shares < ILLIQUID_ADV_SHARES:
            illiq = True
    if not have:
        return None
    return "illiquid" if illiq else "ok"


def event_days_from(earn: date | None, asof: date | None = None) -> tuple[int | None, str | None]:
    if earn is None:
        return None, "no_earnings_date"
    asof = asof or _today()
    delta = (earn - asof).days
    if delta < 0:
        return None, "date_in_past"
    return delta, None


def implied_vol(iv_mid: float | None, iv_call: float | None, iv_put: float | None) -> float | None:
    if iv_mid is not None:
        return iv_mid
    if iv_call is not None and iv_put is not None:
        return (iv_call + iv_put) / 2.0
    return iv_call if iv_call is not None else iv_put


def residual_20d(
    rec_beta: float | None,
    prices_ctx: Mapping[str, Any] | None,
    ticker: str,
    market: str = DEFAULT_MARKET_TICKER,
) -> float | None:
    if rec_beta is None or not prices_ctx:
        return None
    mine = prices_ctx.get(ticker) or prices_ctx.get(name_key(ticker)) or {}
    mkt = prices_ctx.get(market) or prices_ctx.get(name_key(market)) or {}
    r_s = as_float(mine.get("ret_20d") if isinstance(mine, Mapping) else None)
    r_m = as_float(mkt.get("ret_20d") if isinstance(mkt, Mapping) else None)
    if r_s is None or r_m is None:
        return None
    return r_s - rec_beta * r_m


def credit_watch_hint(
    ret_20d: float | None,
    oas_bps: float | None,
) -> str | None:
    """Pointer only: equity momentum vs elevated credit stress. Not a trade."""
    if ret_20d is None or oas_bps is None:
        return None
    if abs(ret_20d) >= MOM_STRONG_ABS and oas_bps >= CREDIT_OAS_STRESS_BPS:
        return "equity_mom_vs_credit_stress"
    return None


def summarize_skew(contracts: Sequence[Mapping[str, Any]] | None) -> dict[str, Any]:
    """25Δ-ish skew proxy from an available chain. Nulls if Greeks/IV absent.

    Prefers contracts with ``|delta|`` near 0.25. Falls back to near-ATM
    (``|delta|`` ~ 0.50 or missing delta) call IV vs put IV. Does not invent
    delta or IV when DAPI left them null.
    """
    empty = {
        "call_iv_25d": None,
        "put_iv_25d": None,
        "call_iv_atm": None,
        "put_iv_atm": None,
        "skew_25d_proxy": None,
        "n_with_iv": 0,
        "n_with_delta": 0,
        "null_reason": "no_contracts",
    }
    if not contracts:
        return empty

    def _cp(row: Mapping[str, Any]) -> str:
        raw = str(row.get("cp") or row.get("put_call") or row.get("right") or "").upper()
        if raw.startswith("P"):
            return "P"
        if raw.startswith("C"):
            return "C"
        delta = as_float(row.get("delta") or row.get("DELTA"))
        if delta is not None and delta < 0:
            return "P"
        return "C" if delta is not None else ""

    def _iv(row: Mapping[str, Any]) -> float | None:
        for key in ("iv", "iv_mid", "IVOL_MID", "opt_iv", "implied_vol", "OPT_IMPLIED_VOLATILITY"):
            v = as_float(row.get(key))
            if v is not None:
                return v
        return None

    def _delta(row: Mapping[str, Any]) -> float | None:
        for key in ("delta", "DELTA", "opt_delta"):
            v = as_float(row.get(key))
            if v is not None:
                return v
        return None

    with_iv = []
    n_delta = 0
    for row in contracts:
        iv = _iv(row)
        if iv is None:
            continue
        dlt = _delta(row)
        if dlt is not None:
            n_delta += 1
        with_iv.append((_cp(row), dlt, iv))

    out = dict(empty)
    out["n_with_iv"] = len(with_iv)
    out["n_with_delta"] = n_delta
    if not with_iv:
        out["null_reason"] = "no_iv_on_chain"
        return out

    def nearest(side: str, target: float) -> float | None:
        pool = [(abs(abs(d) - target), iv) for cp, d, iv in with_iv if cp == side and d is not None]
        if not pool:
            return None
        pool.sort(key=lambda x: x[0])
        return pool[0][1]

    call_25 = nearest("C", 0.25)
    put_25 = nearest("P", 0.25)
    call_atm = nearest("C", 0.50)
    put_atm = nearest("P", 0.50)
    if call_atm is None:
        call_ivs = [iv for cp, d, iv in with_iv if cp == "C" and d is None]
        call_atm = call_ivs[0] if call_ivs else None
    if put_atm is None:
        put_ivs = [iv for cp, d, iv in with_iv if cp == "P" and d is None]
        put_atm = put_ivs[0] if put_ivs else None

    out["call_iv_25d"] = call_25
    out["put_iv_25d"] = put_25
    out["call_iv_atm"] = call_atm
    out["put_iv_atm"] = put_atm
    if call_25 is not None and put_25 is not None:
        out["skew_25d_proxy"] = put_25 - call_25
        out["null_reason"] = None
    elif call_atm is not None and put_atm is not None:
        out["skew_25d_proxy"] = put_atm - call_atm
        out["null_reason"] = "atm_proxy_no_25d"
    else:
        out["null_reason"] = "insufficient_cp_iv"
    return out


def build_name_record(
    ticker: str,
    raw: Mapping[str, Any] | None,
    *,
    prices_ctx: Mapping[str, Any] | None = None,
    options_chain: Sequence[Mapping[str, Any]] | None = None,
    credit_raw: Mapping[str, Any] | None = None,
    intraday: bool = False,
    asof: date | None = None,
) -> dict[str, Any]:
    used: dict[str, str] = {}
    reasons: dict[str, str] = {}
    rec: dict[str, Any] = {
        "ticker": ticker,
        "short_int": None,
        "si_ratio": None,
        "si_pct_float": None,
        "borrow_fee": None,
        "borrow_util": None,
        "vol_30d": None,
        "vol_90d": None,
        "iv_call": None,
        "iv_put": None,
        "iv_mid": None,
        "vol_regime": None,
        "adv_shares": None,
        "adv_usd": None,
        "free_float_pct": None,
        "liq": None,
        "inst_pct": None,
        "etf_pct": None,
        "event_date": None,
        "event_days": None,
        "beta": None,
        "beta_field": None,
        "residual_20d": None,
        "cds_oas": None,
        "bond_oas": None,
        "credit": None,
        "watch_hint": None,
        "intraday": None,
        "skew": None,
        "gics_sector_name": None,
        "fields_used": used,
        "null_reasons": reasons,
        "enrich_pills": [],
    }

    px_ctx_row: Mapping[str, Any] = {}
    if prices_ctx:
        px_ctx_row = prices_ctx.get(ticker) or prices_ctx.get(name_key(ticker)) or {}
        if not isinstance(px_ctx_row, Mapping):
            px_ctx_row = {}

    short_int = _put(rec, used, reasons, "short_int", raw, "short_int")
    si_ratio = _put(rec, used, reasons, "si_ratio", raw, "si_ratio")
    si_pct = _put(rec, used, reasons, "si_pct_float", raw, "si_pct_float")
    _put(rec, used, reasons, "borrow_fee", raw, "borrow_fee")
    _put(rec, used, reasons, "borrow_util", raw, "borrow_util")
    rec["si_class"] = classify_si(si_ratio, si_pct)
    _ = short_int

    vol_30 = _put(rec, used, reasons, "vol_30d", raw, "vol_30d")
    _put(rec, used, reasons, "vol_90d", raw, "vol_90d")
    iv_call = _put(rec, used, reasons, "iv_call", raw, "iv_call")
    iv_put = _put(rec, used, reasons, "iv_put", raw, "iv_put")
    iv_mid = _put(rec, used, reasons, "iv_mid", raw, "iv_mid")
    iv = implied_vol(iv_mid, iv_call, iv_put)
    rec["vol_regime"] = classify_vol_regime(vol_30, iv)
    if rec["vol_regime"] is None and (vol_30 is None or iv is None):
        reasons.setdefault("vol_regime", "need_realized_and_implied")

    adv = _put(rec, used, reasons, "adv_shares", raw, "volume_avg_20d")
    px = _put(rec, used, reasons, "px_last", raw, "px_last")
    chg = _put(rec, used, reasons, "chg_pct_1d", raw, "chg_pct_1d")
    if chg is not None:
        dec = chg / 100.0
        rec["day"] = dec
        rec["ret_1d"] = dec
    else:
        rec["day"] = None
        rec["ret_1d"] = None
    if px is None:
        px = as_float(px_ctx_row.get("px_last") or px_ctx_row.get("PX_LAST"))
        if px is not None:
            rec["px_last"] = px
            used["px_last"] = "prices_ctx"
            reasons.pop("px_last", None)
    rec["adv_usd"] = px * adv if px is not None and adv is not None else None
    if rec["adv_usd"] is None:
        reasons.setdefault("adv_usd", "need_px_last_and_volume_avg_20d")
    _put(rec, used, reasons, "free_float_pct", raw, "free_float_pct")
    rec["liq"] = classify_liq(rec["adv_shares"], rec["adv_usd"])
    if rec["liq"] is None:
        reasons.setdefault("liq", "need_adv_shares_or_adv_usd")

    _put(rec, used, reasons, "inst_pct", raw, "inst_pct")
    etf = _put(rec, used, reasons, "etf_pct", raw, "etf_pct")
    if etf is None:
        # Unreliable / often unresolved — omit from chips, keep null.
        rec["etf_pct"] = None

    earn = None
    value, field, reason = first_success(raw, "earn_dt", as_type="date")
    earn = value
    if field:
        used["event_date"] = field
    days, day_reason = event_days_from(earn, asof)
    rec["event_date"] = earn.isoformat() if isinstance(earn, date) else None
    rec["event_days"] = days
    if day_reason:
        reasons["event_days"] = day_reason
    elif reason:
        reasons["event_days"] = reason

    beta = _put(rec, used, reasons, "beta", raw, "beta")
    rec["beta_field"] = used.get("beta")
    rec["residual_20d"] = residual_20d(beta, prices_ctx, ticker)
    if rec["residual_20d"] is None:
        reasons.setdefault(
            "residual_20d",
            "beta_only" if beta is not None else "no_beta_or_prices",
        )

    cds = _put(rec, used, reasons, "cds_oas", credit_raw or raw, "cds_oas")
    bond = _put(rec, used, reasons, "bond_oas", credit_raw or raw, "bond_oas")
    oas = cds if cds is not None else bond
    rec["credit"] = oas
    if oas is None:
        reasons.setdefault("credit", "no_oas_resolved")
    ret_20d = as_float(px_ctx_row.get("ret_20d"))
    rec["watch_hint"] = credit_watch_hint(ret_20d, oas)

    if intraday:
        sess_vol = None
        vwap = None
        turnover = None
        if raw:
            sess_vol, f1, r1 = first_success(raw, "session_volume")
            vwap, f2, r2 = first_success(raw, "vwap")
            turnover, f3, r3 = first_success(raw, "turnover")
            if f1:
                used["session_volume"] = f1
            if f2:
                used["vwap"] = f2
            if f3:
                used["turnover"] = f3
            if r1:
                reasons["session_volume"] = r1
            if r2:
                reasons["vwap"] = r2
            if r3:
                reasons["turnover"] = r3
        vs_adv = None
        if sess_vol is not None and adv is not None and adv > 0:
            vs_adv = sess_vol / adv
        rec["intraday"] = {
            "session_volume": sess_vol,
            "vwap": vwap,
            "turnover": turnover,
            "session_vs_adv": vs_adv,
        }
        if vs_adv is None:
            reasons.setdefault("intraday_session_vs_adv", "need_session_volume_and_adv")
    else:
        rec["intraday"] = None

    rec["skew"] = summarize_skew(options_chain) if options_chain is not None else None

    # GICS sector name — parse only when a candidate is already on this row
    # (one-shot fill or a prior cache merge). Refresh equity pack does not
    # request these fields, so empty raw does not spam null_reasons.
    rec["gics_sector_name"] = None
    gics_mnemonics = FIELD_CANDIDATES.get("gics_sector_name", ())
    if raw and any(m in raw for m in gics_mnemonics):
        _put(rec, used, reasons, "gics_sector_name", raw, "gics_sector_name", as_type="str")
        name, extra = parse_gics_sector_name(rec.get("gics_sector_name"))
        rec["gics_sector_name"] = name
        if extra and name is None:
            reasons["gics_sector_name"] = extra
            used.pop("gics_sector_name", None)

    rec["enrich_pills"] = build_enrich_pills(rec)
    return rec


def build_enrich_pills(rec: Mapping[str, Any]) -> list[dict[str, str]]:
    """Card chips — same visual language as ``.badge`` / ``.spike-chip``."""
    pills: list[dict[str, str]] = []
    si_class = rec.get("si_class")
    if si_class == "crowded":
        pills.append({"key": "si", "label": "SI", "cls": "si-crowded"})
    elif si_class == "light":
        pills.append({"key": "si", "label": "SI", "cls": "si-light"})

    regime = rec.get("vol_regime")
    if regime == "iv_rich":
        pills.append({"key": "iv", "label": "IV↑", "cls": "iv-rich"})
    elif regime == "iv_cheap":
        pills.append({"key": "iv", "label": "IV↓", "cls": "iv-cheap"})

    if rec.get("liq") == "illiquid":
        pills.append({"key": "liq", "label": "ILLIQ", "cls": "illiquid"})

    inst = as_float(rec.get("inst_pct"))
    if inst is not None and inst >= INST_HIGH_PCT:
        pills.append({"key": "inst", "label": "INST", "cls": "inst-high"})

    days = rec.get("event_days")
    if isinstance(days, (int, float)) and not isinstance(days, bool):
        if days <= EVT_NEAR_DAYS:
            pills.append({"key": "evt", "label": "EVT≤5d", "cls": "evt-near"})
        elif days <= EVT_WATCH_DAYS:
            pills.append({"key": "evt", "label": "EVT≤20d", "cls": "evt-watch"})

    if as_float(rec.get("beta")) is not None:
        pills.append({"key": "beta", "label": "β", "cls": "beta"})

    if rec.get("watch_hint"):
        pills.append({"key": "credit", "label": "CRD", "cls": "credit-stress"})
    elif as_float(rec.get("credit")) is not None:
        pills.append({"key": "credit", "label": "CRD", "cls": "credit"})
    return pills


def attach_card_fields(card: MutableMapping[str, Any], rec: Mapping[str, Any] | None) -> MutableMapping[str, Any]:
    """Expose card fields. Safe when the enrich file or name is missing."""
    rec = rec or {}
    card["si_ratio"] = rec.get("si_ratio")
    card["vol_regime"] = rec.get("vol_regime")
    card["liq"] = rec.get("liq")
    card["inst_pct"] = rec.get("inst_pct")
    card["event_days"] = rec.get("event_days")
    card["beta"] = rec.get("beta")
    card["credit"] = rec.get("credit")
    pills = rec.get("enrich_pills")
    card["enrich_pills"] = list(pills) if isinstance(pills, list) else []
    name, _reason = parse_gics_sector_name(rec.get("gics_sector_name"))
    card["gics_sector_name"] = name
    _attach_day(card, rec)
    return card


def _blank(value: Any) -> bool:
    return value is None or value == ""


def _attach_day(card: MutableMapping[str, Any], rec: Mapping[str, Any]) -> None:
    """Map Bloomberg ``CHG_PCT_1D`` into ``day`` / ``ret_1d`` / ``metrics.day_pct`` when empty.

    Stored decimals match ``r20_pct`` (``1.2`` percent points → ``0.012``). Does not
    overwrite a day the live card already has.
    """
    chg = as_float(rec.get("chg_pct_1d"))
    if chg is not None:
        card["chg_pct_1d"] = chg
    dec = as_float(rec.get("day"))
    if dec is None and chg is not None:
        dec = chg / 100.0
    if dec is None:
        dec = as_float(rec.get("ret_1d"))
    if dec is None:
        return
    if _blank(card.get("day")) and _blank(card.get("ret_1d")) and _blank(card.get("Day")):
        card["day"] = dec
        card["ret_1d"] = dec
    metrics = card.get("metrics")
    if isinstance(metrics, Mapping) and not isinstance(metrics, dict):
        metrics = dict(metrics)
        card["metrics"] = metrics
    elif not isinstance(metrics, dict):
        metrics = {}
        card["metrics"] = metrics
    if _blank(metrics.get("day_pct")) and _blank(metrics.get("r1_pct")):
        src = as_float(card.get("day"))
        if src is None:
            src = as_float(card.get("ret_1d"))
        metrics["day_pct"] = src if src is not None else dec


# ---------------------------------------------------------------------------
# Book assemble / IO
# ---------------------------------------------------------------------------


def default_out_path(root: Path | None = None) -> Path:
    base = Path(root) if root is not None else HERE
    return base / ENRICH_FILENAME


def load_enrichment(path: Path | str | None = None) -> dict[str, Any] | None:
    p = Path(path) if path is not None else default_out_path()
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        LOG.warning("could not read %s: %s", p, exc)
        return None
    if not isinstance(data, dict):
        return None
    return data


def write_enrichment(book: Mapping[str, Any], path: Path | str | None = None) -> Path:
    p = Path(path) if path is not None else default_out_path()
    tmp = p.with_suffix(p.suffix + ".tmp")
    text = json.dumps(book, indent=2, default=str)
    tmp.write_text(text + "\n", encoding="utf-8")
    os.replace(tmp, p)
    return p


def default_gics_cache_path(root: Path | None = None) -> Path:
    base = Path(root) if root is not None else HERE
    return base / GICS_CACHE_FILENAME


def load_gics_cache(path: Path | str | None = None, root: Path | None = None) -> dict[str, Any] | None:
    p = Path(path) if path is not None else default_gics_cache_path(root)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        LOG.warning("could not read %s: %s", p, exc)
        return None
    if not isinstance(data, dict):
        return None
    return data


def write_gics_cache(cache: Mapping[str, Any], path: Path | str | None = None, root: Path | None = None) -> Path:
    p = Path(path) if path is not None else default_gics_cache_path(root)
    tmp = p.with_suffix(p.suffix + ".tmp")
    text = json.dumps(cache, indent=2, default=str)
    tmp.write_text(text + "\n", encoding="utf-8")
    os.replace(tmp, p)
    return p


def gics_from_cache(cache: Mapping[str, Any] | None, ticker: str) -> str | None:
    if not cache:
        return None
    names = cache.get("names") if isinstance(cache, Mapping) else None
    blob: Mapping[str, Any]
    if isinstance(names, dict):
        blob = names
    else:
        blob = cache
    raw = blob.get(ticker) or blob.get(name_key(ticker))
    if isinstance(raw, Mapping):
        raw = raw.get("gics_sector_name") or raw.get("sector")
    name, _reason = parse_gics_sector_name(raw)
    return name


def merge_cached_gics(
    rec: MutableMapping[str, Any],
    ticker: str,
    prev_book: Mapping[str, Any] | None,
    cache: Mapping[str, Any] | None,
) -> MutableMapping[str, Any]:
    """Keep a previously resolved sector across Refresh (no GICS pull)."""
    current, _reason = parse_gics_sector_name(rec.get("gics_sector_name"))
    if current:
        rec["gics_sector_name"] = current
        return rec
    prev = lookup_name(prev_book, ticker) if prev_book else None
    name = None
    source = None
    if prev:
        name, _reason = parse_gics_sector_name(prev.get("gics_sector_name"))
        if name:
            source = "prev_enrichment"
    if not name:
        name = gics_from_cache(cache, ticker)
        if name:
            source = "gics_cache"
    rec["gics_sector_name"] = name
    if name:
        used = rec.setdefault("fields_used", {})
        if isinstance(used, dict) and "gics_sector_name" not in used:
            used["gics_sector_name"] = source or "gics_cache"
        reasons = rec.get("null_reasons")
        if isinstance(reasons, dict):
            reasons.pop("gics_sector_name", None)
    return rec


def cache_from_book(book: Mapping[str, Any] | None) -> dict[str, Any]:
    names_out: dict[str, str] = {}
    names = (book or {}).get("names") if isinstance(book, Mapping) else None
    if isinstance(names, dict):
        for ticker, rec in names.items():
            if not isinstance(rec, Mapping):
                continue
            name, _reason = parse_gics_sector_name(rec.get("gics_sector_name"))
            if name:
                names_out[ticker] = name
    return {
        "asof": (book or {}).get("asof") if isinstance(book, Mapping) else None,
        "source": "gics_cache",
        "names": names_out,
        "meta": {"n": len(names_out)},
    }


def fill_gics_sectors(
    book: Mapping[str, Any] | None = None,
    session: Any | None = None,
    *,
    tickers: Sequence[str] | None = None,
    root: Path | None = None,
    out_path: Path | str | None = None,
    cache_path: Path | str | None = None,
    write: bool = True,
    only_missing: bool = True,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    progress_cb: Callable[[float, str], None] | None = None,
) -> dict[str, Any]:
    """One-shot GICS sector fill. Not part of Refresh.

    Pulls ``GICS_SECTOR_NAME`` (then ``GICS_SECTOR``) for names missing a
    sector. Writes ``gics_sectors.json`` and stamps ``dapi_enrichment.json``.
    Does not invent a ticker→sector map. Capacity degrades like enrich.
    """
    root = Path(root) if root is not None else HERE
    dest = Path(out_path) if out_path is not None else default_out_path(root)
    working: dict[str, Any]
    if book is None:
        loaded = load_enrichment(dest)
        working = dict(loaded) if loaded else {"asof": _now_iso(), "names": {}, "meta": {}}
    else:
        working = {
            "asof": book.get("asof") or _now_iso(),
            "names": dict(book.get("names") or {}),
            "meta": dict(book.get("meta") or {}),
        }
        working["names"] = {
            k: (dict(v) if isinstance(v, Mapping) else {"ticker": k})
            for k, v in working["names"].items()
        }

    cache = load_gics_cache(cache_path, root=root)
    names: dict[str, Any] = working.setdefault("names", {})
    if not isinstance(names, dict):
        names = {}
        working["names"] = names

    ordered: list[str] = []
    seen: set[str] = set()
    src_tickers = list(tickers) if tickers else list(names.keys())
    if not src_tickers:
        src_tickers = discover_tickers(root)
    for t in src_tickers:
        k = name_key(str(t))
        if k and k not in seen:
            seen.add(k)
            ordered.append(k)
            names.setdefault(k, {"ticker": k})

    for t in ordered:
        rec = names.get(t) or {}
        if not isinstance(rec, dict):
            rec = {"ticker": t}
            names[t] = rec
        have, _reason = parse_gics_sector_name(rec.get("gics_sector_name"))
        if not have:
            have = gics_from_cache(cache, t)
            if have:
                rec["gics_sector_name"] = have

    need = []
    for t in ordered:
        rec = names.get(t) or {}
        have, _reason = parse_gics_sector_name(rec.get("gics_sector_name") if isinstance(rec, Mapping) else None)
        if only_missing and have:
            continue
        need.append(t)

    meta = working.setdefault("meta", {})
    if not isinstance(meta, dict):
        meta = {}
        working["meta"] = meta
    gics_meta: dict[str, Any] = {
        "gics_once": True,
        "gics_requested": len(need),
        "gics_skips": [],
        "gics_capacity_skipped": False,
    }

    wrapped = wrap_session(session)
    if need and wrapped is None:
        try:
            wrapped = open_dapi_session()
        except CapacityError as exc:
            gics_meta["gics_capacity_skipped"] = True
            gics_meta["gics_skips"].append(f"capacity:{exc.reason}")
            wrapped = None

    if need and wrapped is None:
        gics_meta["gics_skips"].append("no_dapi_session")
        LOG.info("gics-once: no DAPI session — cache existing names only")
    elif need:
        if progress_cb:
            progress_cb(0.10, "gics_once")
        fields = flatten_candidates(GICS_PACK_KEYS)
        try:
            rows, skips, capacity_hit = batch_refdata(
                wrapped,
                need,
                fields,
                chunk_size=chunk_size,
                progress_cb=progress_cb,
                progress_lo=0.10,
                progress_hi=0.90,
            )
            gics_meta["gics_skips"].extend(skips)
            gics_meta["gics_capacity_skipped"] = bool(capacity_hit)
        except CapacityError as exc:
            rows, skips, capacity_hit = {}, [f"capacity:{exc.reason}"], True
            gics_meta["gics_skips"].append(f"capacity:{exc.reason}")
            gics_meta["gics_capacity_skipped"] = True
            LOG.warning("gics-once skipped (capacity): %s", exc.reason)
        for t in need:
            rec = names.get(t)
            if not isinstance(rec, dict):
                rec = {"ticker": t}
                names[t] = rec
            raw = rows.get(t) or {}
            value, field, reason = first_success(raw, "gics_sector_name", as_type="str")
            name, extra = parse_gics_sector_name(value)
            used = rec.setdefault("fields_used", {})
            reasons = rec.setdefault("null_reasons", {})
            if not isinstance(used, dict):
                used = {}
                rec["fields_used"] = used
            if not isinstance(reasons, dict):
                reasons = {}
                rec["null_reasons"] = reasons
            if name:
                rec["gics_sector_name"] = name
                if field:
                    used["gics_sector_name"] = field
                reasons.pop("gics_sector_name", None)
            else:
                if not parse_gics_sector_name(rec.get("gics_sector_name"))[0]:
                    rec["gics_sector_name"] = None
                    reasons["gics_sector_name"] = extra or reason or "no_gics_name"

    filled = sum(
        1
        for rec in names.values()
        if isinstance(rec, Mapping) and parse_gics_sector_name(rec.get("gics_sector_name"))[0]
    )
    gics_meta["gics_filled"] = filled
    meta["gics_once"] = gics_meta

    cache_blob = cache_from_book(working)
    cache_blob["source"] = "dapi_gics_once"
    cache_blob["meta"] = {
        "n": len(cache_blob.get("names") or {}),
        "capacity_skipped": gics_meta["gics_capacity_skipped"],
        "skips": list(gics_meta["gics_skips"]),
        "requested": gics_meta["gics_requested"],
    }

    if write:
        write_enrichment(working, dest)
        write_gics_cache(cache_blob, cache_path, root=root)
        LOG.info(
            "gics-once wrote %s names with sector (%s requested)",
            cache_blob["meta"]["n"],
            gics_meta["gics_requested"],
        )
    if progress_cb:
        progress_cb(1.0, "gics_once done")
    return working


def _chains_for(ticker: str, options_by_name: Mapping[str, Any] | None) -> list[Mapping[str, Any]] | None:
    if not options_by_name:
        return None
    raw = options_by_name.get(ticker) or options_by_name.get(name_key(ticker))
    if raw is None:
        return None
    if isinstance(raw, Mapping) and "contracts" in raw:
        raw = raw.get("contracts")
    if isinstance(raw, list):
        return [r for r in raw if isinstance(r, Mapping)]
    return None


def _prices_from_ctx(ticker: str, prices_ctx: Mapping[str, Any] | None) -> dict[str, Any]:
    if not prices_ctx:
        return {}
    row = prices_ctx.get(ticker) or prices_ctx.get(name_key(ticker)) or {}
    return dict(row) if isinstance(row, Mapping) else {}


def mapped_credit_tickers(equity: str) -> dict[str, str]:
    return dict(EQUITY_CREDIT_TICKERS.get(name_key(equity)) or EQUITY_CREDIT_TICKERS.get(equity) or {})


def enrich_book(
    tickers: Sequence[str],
    session: Any | None = None,
    *,
    prices_ctx: Mapping[str, Any] | None = None,
    options_by_name: Mapping[str, Any] | None = None,
    intraday: bool = False,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    out_path: Path | str | None = None,
    write: bool = True,
    progress_cb: Callable[[float, str], None] | None = None,
    asof: datetime | date | str | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    """Batch-enrich ``tickers``. Returns the book dict (and optionally writes JSON).

    ``session`` may be a ``blpapi.Session``, a ``RefDataSession``, or ``None``.
    ``None`` still returns a structured book with nulls + reasons (no invented
    numbers) and writes JSON so Desktop can see the attempt.
    """
    if progress_cb:
        progress_cb(0.50, "dapi_enrich start")

    asof_date: date
    if isinstance(asof, datetime):
        asof_date = asof.date()
        asof_iso = asof.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    elif isinstance(asof, date):
        asof_date = asof
        asof_iso = asof.isoformat()
    elif isinstance(asof, str) and asof:
        asof_iso = asof
        asof_date = as_date(asof) or _today()
    else:
        asof_iso = _now_iso()
        asof_date = _today()

    names = [name_key(t) for t in tickers if str(t).strip()]
    # de-dupe preserve order
    seen: set[str] = set()
    ordered: list[str] = []
    for t in names:
        if t not in seen:
            seen.add(t)
            ordered.append(t)

    dest = Path(out_path) if out_path is not None else default_out_path(root)
    prev_book = load_enrichment(dest) if dest.is_file() else None
    gics_cache = load_gics_cache(root=root)

    meta: dict[str, Any] = {
        "intraday": bool(intraday),
        "capacity_skipped": False,
        "chunk_size": chunk_size,
        "fields_attempted": [],
        "fields_resolved": [],
        "fields_failed": [],
        "skips": [],
        "source": "dapi_enrich",
        "n_names": len(ordered),
        "session": "none",
    }

    pack_keys = list(EQUITY_PACK_KEYS)
    if intraday:
        pack_keys.extend(INTRADAY_PACK_KEYS)
    fields = flatten_candidates(pack_keys)
    meta["fields_attempted"] = list(fields)

    wrapped = wrap_session(session)
    rows: dict[str, dict[str, Any]] = {}
    credit_rows: dict[str, dict[str, Any]] = {}
    capacity_hit = False

    if wrapped is None:
        meta["skips"].append("no_dapi_session")
        meta["session"] = "none"
        LOG.info("enrich: no DAPI session — writing null book (no invented numbers)")
        rows = {t: {} for t in ordered}
    else:
        meta["session"] = type(wrapped).__name__
        try:
            rows, skips, capacity_hit = batch_refdata(
                wrapped,
                ordered,
                fields,
                chunk_size=chunk_size,
                progress_cb=progress_cb,
                progress_lo=0.50,
                progress_hi=0.57,
            )
            meta["skips"].extend(skips)
        except CapacityError as exc:
            capacity_hit = True
            meta["skips"].append(f"capacity:{exc.reason}")
            rows = {t: {} for t in ordered}
            LOG.warning("enrich skipped entirely (capacity): %s", exc.reason)

        if not capacity_hit:
            credit_secs: list[str] = []
            credit_owner: dict[str, str] = {}
            for t in ordered:
                mapped = mapped_credit_tickers(t)
                for kind, sec in mapped.items():
                    if sec and sec not in credit_owner:
                        credit_owner[sec] = t
                        credit_secs.append(sec)
            if credit_secs:
                c_fields = flatten_candidates(("cds_oas", "bond_oas"))
                try:
                    c_rows, c_skips, c_cap = batch_refdata(
                        wrapped,
                        credit_secs,
                        c_fields,
                        chunk_size=chunk_size,
                    )
                    meta["skips"].extend(c_skips)
                    if c_cap:
                        capacity_hit = True
                    for sec, row in c_rows.items():
                        owner = credit_owner.get(sec)
                        if owner:
                            credit_rows[owner] = row
                except CapacityError as exc:
                    capacity_hit = True
                    meta["skips"].append(f"capacity_credit:{exc.reason}")

    meta["capacity_skipped"] = capacity_hit

    resolved: set[str] = set()
    for row in rows.values():
        for fname, val in row.items():
            if not is_na(val):
                resolved.add(fname)
    meta["fields_resolved"] = sorted(resolved)
    meta["fields_failed"] = [f for f in fields if f not in resolved]

    book_names: dict[str, Any] = {}
    for t in ordered:
        raw = rows.get(t) or {}
        rec = build_name_record(
            t,
            raw,
            prices_ctx=prices_ctx,
            options_chain=_chains_for(t, options_by_name),
            credit_raw=credit_rows.get(t),
            intraday=intraday,
            asof=asof_date,
        )
        merge_cached_gics(rec, t, prev_book, gics_cache)
        book_names[t] = rec

    book = {
        "asof": asof_iso,
        "names": book_names,
        "meta": meta,
    }
    if write:
        write_enrichment(book, dest)
        meta["path"] = str(dest)
        LOG.info("wrote %s (%s names)", dest, len(book_names))
    if progress_cb:
        progress_cb(0.60, "dapi_enrich done")
    return book


def lookup_name(book: Mapping[str, Any] | None, ticker: str) -> dict[str, Any] | None:
    if not book:
        return None
    names = book.get("names") if isinstance(book, Mapping) else None
    if not isinstance(names, dict):
        return None
    return names.get(ticker) or names.get(name_key(ticker))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _read_universe(path: Path) -> list[str]:
    tickers: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            tickers.append(line)
    return tickers


def discover_tickers(root: Path | None = None, extra: Sequence[str] | None = None) -> list[str]:
    base = Path(root) if root is not None else HERE
    found: list[str] = []
    if extra:
        found.extend(extra)
    for candidate in (
        base / "universe.txt",
        base / "tickers.txt",
        HERE / "universe.txt",
        HERE / "tickers.txt",
    ):
        if candidate.is_file():
            found.extend(_read_universe(candidate))
            break
    if not found:
        for json_name in (OPTIONS_ABNORMAL_FILENAME, ENRICH_FILENAME):
            p = base / json_name
            if not p.is_file():
                continue
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            names = data.get("names") if isinstance(data, dict) else None
            if isinstance(names, dict):
                found.extend(names.keys())
                break
    # de-dupe
    seen: set[str] = set()
    out: list[str] = []
    for t in found:
        k = name_key(t)
        if k and k not in seen:
            seen.add(k)
            out.append(k)
    return out


def parse_intraday_flag(value: Any) -> bool:
    if value is True or value == 1:
        return True
    if value is False or value == 0 or value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on", "y"}


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="Factor Desk DAPI enrichment (no news layer)")
    p.add_argument("--tickers", default="", help="Comma-separated tickers")
    p.add_argument("--universe", default="", help="Path to ticker list")
    p.add_argument("--intraday", action="store_true", help="Also pull session volume vs ADV")
    p.add_argument("--dry-run", action="store_true", help="No DAPI; write null book + reasons")
    p.add_argument(
        "--gics-once",
        action="store_true",
        help="One-shot GICS sector fill (not Refresh). Writes gics_sectors.json.",
    )
    p.add_argument("--out", default="", help="Output JSON path")
    p.add_argument("--root", default="", help="Desk root (default: this folder)")
    p.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE)
    args = p.parse_args(argv)

    root = Path(args.root) if args.root else HERE
    extra: list[str] = []
    if args.tickers:
        extra.extend(t.strip() for t in args.tickers.split(",") if t.strip())
    if args.universe:
        extra.extend(_read_universe(Path(args.universe)))
    tickers = discover_tickers(root, extra)
    if not tickers:
        LOG.error("no tickers — pass --tickers or provide universe.txt")
        return 2

    session: RefDataSession | None
    if args.dry_run:
        session = NullSession()
    else:
        try:
            session = open_dapi_session()
        except CapacityError as exc:
            LOG.warning("capacity at session open (%s) — skip enrich", exc.reason)
            session = None

    out = Path(args.out) if args.out else default_out_path(root)
    if args.gics_once:
        book = fill_gics_sectors(
            session=session,
            tickers=tickers,
            root=root,
            out_path=out,
            write=True,
        )
        gics_meta = (book.get("meta") or {}).get("gics_once") or {}
        print(
            json.dumps(
                {
                    "asof": book.get("asof"),
                    "n": len(book.get("names") or {}),
                    "path": str(out),
                    "gics_once": True,
                    "gics_filled": gics_meta.get("gics_filled"),
                    "gics_requested": gics_meta.get("gics_requested"),
                    "gics_capacity_skipped": gics_meta.get("gics_capacity_skipped"),
                    "gics_skips": gics_meta.get("gics_skips"),
                },
                indent=2,
            )
        )
        return 0

    book = enrich_book(
        tickers,
        session,
        intraday=args.intraday,
        chunk_size=args.chunk_size,
        out_path=out,
        root=root,
    )
    n_null = sum(
        1
        for rec in book["names"].values()
        if rec.get("beta") is None and rec.get("si_ratio") is None
    )
    print(
        json.dumps(
            {
                "asof": book["asof"],
                "n": len(book["names"]),
                "path": str(out),
                "capacity_skipped": book["meta"].get("capacity_skipped"),
                "fields_resolved": book["meta"].get("fields_resolved"),
                "fields_failed": book["meta"].get("fields_failed"),
                "mostly_null_names": n_null,
                "intraday": book["meta"].get("intraday"),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
