"""Desk Analyst hitch pills — optional, no-op if the ideas folder is missing.

Scans ``YYYY-MM-DD-*.md`` from the last ~10 calendar days. Ticker parse is
conservative (frontmatter / Action / ``$TICKER`` / a single bold ticker). Never
raises on a missing directory.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping, MutableMapping

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import mom_streak  # noqa: E402

LOG = logging.getLogger("desk_hitch")

IDEAS_ENV = "FACTOR_DESK_IDEAS_DIR"
IDEAS_ENV_ALT = "FD_IDEAS_DIR"
LOOKBACK_DAYS = 10
PILL_KEY = "desk-hitch"
DB_SCRIPT_ID = "fd-hitch-db"
JS_SCRIPT_ID = "fd-hitch-js"
CSS_STYLE_ID = "fd-hitch-css"

FILENAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})[-_].+\.md$", re.I)
FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)
TICKER_FM_RE = re.compile(
    r"(?im)^(?:ticker|tickers|name|names|yellow|symbol|symbols)\s*:\s*(.+)$"
)
ANALYST_FM_RE = re.compile(
    r"(?im)^(?:analyst|desk_analyst|desk|author|writer|da)\s*:\s*([ABC])\b"
)
ACTION_RE = re.compile(
    r"(?im)^(?:#{1,6}\s*)?(?:\*\*)?action(?:\*\*)?\s*[:\-]\s*(.+)$"
)
DOLLAR_RE = re.compile(r"\$([A-Z]{1,5}(?:\.[A-Z]{1,2})?)(?:\b|(?=\s))")
BOLD_RE = re.compile(r"\*\*([A-Z]{1,5}(?:\.[A-Z]{1,2})?(?:\s+US(?:\s+Equity)?)?)\*\*")
TICKER_TOKEN_RE = re.compile(r"\b([A-Z]{1,5}(?:\.[A-Z]{1,2})?)(?:\s+US(?:\s+Equity)?)?\b")
HEADING_RE = re.compile(r"^(#{1,6}|\*\*)\s*")

STOPWORDS = frozenset(
    {
        "THE",
        "AND",
        "FOR",
        "FROM",
        "WITH",
        "THIS",
        "THAT",
        "NOTE",
        "BUY",
        "SELL",
        "HOLD",
        "IDEA",
        "RISK",
        "DATE",
        "ACTION",
        "THESIS",
        "LONG",
        "SHORT",
        "CALL",
        "PUT",
        "MOM",
        "FLAG",
        "WATCH",
        "TODO",
        "WIP",
        "TBD",
        "CEO",
        "CFO",
        "USD",
        "GDP",
        "EPS",
        "JAN",
        "FEB",
        "MAR",
        "APR",
        "MAY",
        "JUN",
        "JUL",
        "AUG",
        "SEP",
        "OCT",
        "NOV",
        "DEC",
        "VS",
        "OR",
        "IF",
        "ON",
        "TO",
        "IN",
        "AT",
        "BY",
        "OF",
        "A",
        "B",
        "C",
        "DA",
    }
)


def _today() -> date:
    return datetime.now().date()


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _short(ticker: str) -> str:
    parts = (ticker or "").split()
    return parts[0].upper() if parts else ""


def looks_like_ticker(sym: str, *, allow_single: bool = False) -> bool:
    raw = (sym or "").strip().upper()
    if not raw:
        return False
    token = raw.split()[0]
    if token in STOPWORDS:
        return False
    if not re.fullmatch(r"[A-Z]{1,5}(?:\.[A-Z]{1,2})?", token):
        return False
    core = token.split(".")[0]
    if len(core) < 2 and not allow_single:
        return False
    return True


def _tokens_from_line(line: str, *, allow_single: bool = False) -> list[str]:
    out: list[str] = []
    for match in TICKER_TOKEN_RE.finditer(line.upper()):
        token = match.group(1)
        if looks_like_ticker(token, allow_single=allow_single):
            out.append(token)
    return out


def _parse_fm_tickers(blob: str) -> list[str]:
    found: list[str] = []
    for match in TICKER_FM_RE.finditer(blob):
        raw = match.group(1).strip().strip("[]")
        parts = re.split(r"[,;\s]+", raw)
        for part in parts:
            token = part.strip().strip("\"'").upper()
            if looks_like_ticker(token, allow_single=True):
                found.append(token.split()[0] if token.split() else token)
    return found


def parse_tickers(text: str, filename: str = "") -> list[str]:
    """Conservative ticker list. Frontmatter / Action win; body is last resort."""
    body = text or ""
    fm_tickers: list[str] = []
    fm = FRONTMATTER_RE.match(body)
    if fm:
        fm_tickers = _parse_fm_tickers(fm.group(1))
        body = body[fm.end() :]
    if fm_tickers:
        return _uniq(fm_tickers)

    action_hits: list[str] = []
    for match in ACTION_RE.finditer(body):
        action_hits.extend(_tokens_from_line(match.group(1), allow_single=True))
    if action_hits:
        return _uniq(action_hits)

    dollars = [m.group(1) for m in DOLLAR_RE.finditer(body) if looks_like_ticker(m.group(1), allow_single=True)]
    if dollars:
        return _uniq(dollars)

    bolds = []
    for match in BOLD_RE.finditer(body):
        token = match.group(1).split()[0]
        if looks_like_ticker(token, allow_single=False):
            bolds.append(token)
    # A single bold ticker is ok; many bold words are prose — skip.
    uniq_bold = _uniq(bolds)
    if len(uniq_bold) == 1:
        return uniq_bold
    return []


def parse_analyst(text: str, path: Path | None = None) -> str | None:
    body = text or ""
    fm = FRONTMATTER_RE.match(body)
    if fm:
        m = ANALYST_FM_RE.search(fm.group(1))
        if m:
            return m.group(1).upper()
    m = ANALYST_FM_RE.search(body)
    if m:
        return m.group(1).upper()
    name = path.name if path is not None else ""
    m = re.search(r"(?:^|[-_])(?:analyst[-_])?([ABC])(?:[-_.]|$)", name, re.I)
    if m:
        letter = m.group(1).upper()
        if letter in {"A", "B", "C"}:
            return letter
    if path is not None:
        for part in path.parts:
            p = part.strip().upper()
            if p in {"A", "B", "C"}:
                return p
            m = re.fullmatch(r"ANALYST[-_]?([ABC])", p)
            if m:
                return m.group(1).upper()
    return None


def first_thesis_line(text: str) -> str:
    body = text or ""
    fm = FRONTMATTER_RE.match(body)
    if fm:
        body = body[fm.end() :]
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("---"):
            continue
        if ACTION_RE.match(stripped):
            continue
        if stripped.startswith("#"):
            # heading — skip unless it looks like a thesis after the hash
            rest = stripped.lstrip("#").strip()
            if rest and not rest.lower().startswith("action"):
                # skip the title-ish first heading; keep hunting
                continue
            continue
        if stripped.startswith("<!--"):
            continue
        return stripped[:160]
    return ""


def _uniq(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = _short(item)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def ideas_dir(root: Path | None = None) -> Path | None:
    env = os.environ.get(IDEAS_ENV) or os.environ.get(IDEAS_ENV_ALT)
    if env:
        p = Path(env).expanduser()
        return p if p.is_dir() else None
    base = Path(root) if root is not None else HERE
    candidates = (
        base / "ideas",
        base.parent / "ideas",
        base.parent / "jr-analysts" / "ideas",
        Path.home() / "Desktop" / "jr-analysts" / "ideas",
        Path(r"C:\Users\MLP\Desktop\jr-analysts\ideas"),
    )
    for cand in candidates:
        try:
            if cand.is_dir():
                return cand
        except OSError:
            continue
    return None


def _file_url(path: Path) -> str:
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path
    return resolved.as_uri()


def load_index(
    root: Path | None = None,
    *,
    ideas: Path | None = None,
    asof: date | None = None,
    lookback: int = LOOKBACK_DAYS,
) -> dict[str, dict[str, Any]]:
    """``short ticker → hitch rec``. Missing dir → empty dict, no error."""
    folder = ideas if ideas is not None else ideas_dir(root)
    if folder is None:
        return {}
    today = asof or _today()
    start = today - timedelta(days=max(1, int(lookback)))
    out: dict[str, dict[str, Any]] = {}
    try:
        files = sorted(folder.rglob("*.md"))
    except OSError as exc:
        LOG.info("desk hitch: ideas dir unreadable (%s): %s", folder, exc)
        return {}
    for path in files:
        m = FILENAME_RE.match(path.name)
        if not m:
            continue
        day = _as_date(m.group(1))
        if day is None or day < start or day > today:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        tickers = parse_tickers(text, path.name)
        if not tickers:
            continue
        analyst = parse_analyst(text, path)
        thesis = first_thesis_line(text)
        rec = {
            "ticker": tickers[0],
            "tickers": tickers,
            "analyst": analyst,
            "date": day.isoformat(),
            "thesis": thesis,
            "path": str(path),
            "url": _file_url(path),
            "file": path.name,
        }
        for ticker in tickers:
            prev = out.get(ticker)
            if prev is None or str(rec["date"]) >= str(prev.get("date") or ""):
                out[ticker] = rec
    return out


def pill_for(rec: Mapping[str, Any] | None) -> dict[str, str] | None:
    if not rec:
        return None
    analyst = str(rec.get("analyst") or "").upper()
    label = f"DA·{analyst}" if analyst in {"A", "B", "C"} else "DA"
    date_s = str(rec.get("date") or "")
    thesis = str(rec.get("thesis") or "").strip()
    path = str(rec.get("path") or rec.get("url") or rec.get("file") or "")
    title_bits = [date_s, thesis]
    if path:
        title_bits.append(path)
    title = " · ".join(b for b in title_bits if b)
    return {
        "key": PILL_KEY,
        "label": label,
        "cls": "desk-hitch",
        "title": title,
        "href": str(rec.get("url") or ""),
        "analyst": analyst,
        "date": date_s,
    }


def hitch_for(ticker: str, index: Mapping[str, Mapping[str, Any]] | None) -> dict[str, Any] | None:
    if not ticker or not index:
        return None
    short = _short(ticker)
    rec = index.get(short) or index.get(ticker)
    if rec:
        return dict(rec)
    for key, val in index.items():
        if _short(str(key)) == short:
            return dict(val)
    return None


def attach_card(
    card: MutableMapping[str, Any],
    index: Mapping[str, Mapping[str, Any]] | None,
) -> MutableMapping[str, Any]:
    ticker = mom_streak.card_ticker(card)
    rec = hitch_for(ticker, index)
    card["desk_hitch"] = rec
    pills = card.get("enrich_pills")
    if not isinstance(pills, list):
        pills = []
        card["enrich_pills"] = pills
    pills = [p for p in pills if not (isinstance(p, Mapping) and p.get("key") == PILL_KEY)]
    pill = pill_for(rec)
    if pill:
        pills.append(pill)
        card["desk_hitch_label"] = pill["label"]
    card["enrich_pills"] = pills
    return card


def attach_all(
    cards: Iterable[MutableMapping[str, Any]],
    index: Mapping[str, Mapping[str, Any]] | None,
) -> list[MutableMapping[str, Any]]:
    return [attach_card(c, index) for c in cards]


def hitch_db(
    cards: Iterable[Mapping[str, Any]] | None,
    index: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for card in cards or []:
        if not isinstance(card, Mapping):
            continue
        ticker = mom_streak.card_ticker(card)
        rec = card.get("desk_hitch") if isinstance(card.get("desk_hitch"), Mapping) else hitch_for(ticker, index)
        pill = pill_for(rec) if rec else None
        if not ticker or not pill:
            continue
        short = _short(ticker)
        payload = {
            "label": pill["label"],
            "cls": pill["cls"],
            "title": pill["title"],
            "href": pill.get("href") or "",
            "analyst": pill.get("analyst") or "",
            "date": pill.get("date") or "",
        }
        out[ticker] = payload
        out.setdefault(short, payload)
    if index:
        for key, rec in index.items():
            pill = pill_for(rec)
            if not pill:
                continue
            short = _short(str(key))
            payload = {
                "label": pill["label"],
                "cls": pill["cls"],
                "title": pill["title"],
                "href": pill.get("href") or "",
                "analyst": pill.get("analyst") or "",
                "date": pill.get("date") or "",
            }
            out.setdefault(str(key), payload)
            out.setdefault(short, payload)
    return out


def _script_json(blob: str) -> str:
    return (blob or "").replace("</", "<\\/")


def embed_db(mapping: Mapping[str, Any] | None) -> str:
    blob = json.dumps(dict(mapping or {}), separators=(",", ":"), ensure_ascii=True, default=str)
    return f'<script type="application/json" id="{DB_SCRIPT_ID}">{_script_json(blob)}</script>'


def strip_css() -> str:
    return """
.badge.desk-hitch, .spike-chip.desk-hitch {
  color: #c4b5fd;
  border-color: #8b5cf6;
}
.badge.desk-hitch a, .spike-chip.desk-hitch a {
  color: inherit;
  text-decoration: none;
}
""".strip()


def strip_js() -> str:
    return r"""
(function () {
  var el = document.getElementById("fd-hitch-db");
  if (!el) return;
  var db = {};
  try { db = JSON.parse(el.textContent || "{}") || {}; }
  catch (e) { return; }
  function tickerOf(node) {
    return (node.getAttribute("data-t") || node.getAttribute("data-ticker") || node.getAttribute("data-name") || "").trim();
  }
  function recOf(t) {
    if (!t) return null;
    if (db[t]) return db[t];
    var short = t.split(/\s+/)[0];
    if (db[short]) return db[short];
    var keys = Object.keys(db);
    for (var i = 0; i < keys.length; i++) {
      if (keys[i].split(/\s+/)[0] === short) return db[keys[i]];
    }
    return null;
  }
  function apply() {
    var nodes = document.querySelectorAll("[data-t], [data-ticker], article.card, .card");
    for (var i = 0; i < nodes.length; i++) {
      var node = nodes[i];
      if (node.closest && node.closest("nav, .topnav, #gics-filter-strip, #fd-book-delta, #fd-paper-home, #refresh")) continue;
      var rec = recOf(tickerOf(node));
      if (!rec || !rec.label) continue;
      if (node.querySelector('[data-key="desk-hitch"]')) continue;
      var host = node.querySelector(".pills, .chips, .badges") || node;
      var span = document.createElement("span");
      span.className = "badge spike-chip desk-hitch";
      span.setAttribute("data-key", "desk-hitch");
      span.title = rec.title || "Desk Analyst note";
      span.textContent = rec.label;
      host.appendChild(span);
    }
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", apply);
  else apply();
})();
""".strip()


def ensure_embedded(
    html_text: str,
    mapping: Mapping[str, Any] | None = None,
) -> str:
    """Filled hitch db + pill CSS/JS. Empty map is still embedded (no-op apply)."""
    text = html_text or ""
    css = f'<style id="{CSS_STYLE_ID}">\n{strip_css()}\n</style>\n'
    text, n_css = re.subn(
        rf'<style\b[^>]*\bid=["\']{CSS_STYLE_ID}["\'][^>]*>.*?</style>\s*',
        lambda _m: css,
        text,
        count=1,
        flags=re.I | re.S,
    )
    if n_css == 0:
        if "</head>" in text:
            text = text.replace("</head>", css + "</head>", 1)
        else:
            text = css + text
    if ".desk-hitch" not in text:
        if "</style>" in text:
            idx = text.rfind("</style>")
            text = text[:idx] + strip_css() + "\n" + text[idx:]
    tag = embed_db(mapping)
    if re.search(rf'id=["\']{DB_SCRIPT_ID}["\']', text, re.I):
        text = re.sub(
            rf'<script\b[^>]*\bid=["\']{DB_SCRIPT_ID}["\'][^>]*>.*?</script>',
            lambda _m: tag,
            text,
            count=1,
            flags=re.I | re.S,
        )
    elif "</body>" in text:
        text = text.replace("</body>", tag + "\n</body>", 1)
    else:
        text += tag
    script = f'<script id="{JS_SCRIPT_ID}">\n{strip_js()}\n</script>\n'
    text, n_js = re.subn(
        rf'<script\b[^>]*\bid=["\']{JS_SCRIPT_ID}["\'][^>]*>.*?</script>\s*',
        lambda _m: script,
        text,
        count=1,
        flags=re.I | re.S,
    )
    if n_js == 0:
        if "</body>" in text:
            text = text.replace("</body>", script + "</body>", 1)
        else:
            text += script
    return text
