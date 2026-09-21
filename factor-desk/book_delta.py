"""Book-delta strip: what changed since last successful Refresh.

Persists a small gitignored ``desk_snapshot.json`` after each write. The next
write diffs FLAGS / WATCH / outliers / mom scores / streak sides and paints a
one-row chip strip. Missing prior snapshot → quiet ``baseline set``.
"""

from __future__ import annotations

import html
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import dapi_enrich  # noqa: E402
import mom_streak  # noqa: E402
from breakout import card_lists  # noqa: E402

LOG = logging.getLogger("book_delta")

SNAPSHOT_FILENAME = "desk_snapshot.json"
SCORE_JUMP = 2.0
LONG_STREAK = 15
DB_SCRIPT_ID = "fd-book-delta-db"
JS_SCRIPT_ID = "fd-book-delta-js"
CSS_STYLE_ID = "fd-book-delta-css"
HOST_ID = "fd-book-delta"


def default_snapshot_path(root: Path | None = None) -> Path:
    base = Path(root) if root is not None else HERE
    return base / SNAPSHOT_FILENAME


def _short(ticker: str) -> str:
    parts = (ticker or "").split()
    return parts[0].upper() if parts else ""


_ARROW_GAP_RE = re.compile(r"\s*→\s*")
_DIR_GAP_RE = re.compile(r"([↓↑])\s*")


def format_streak_fragment(text: Any) -> str:
    """Reading spaces after ↓/↑ and around →. Does not change streak meaning."""
    s = str(text or "").strip()
    s = _ARROW_GAP_RE.sub(" → ", s)
    s = _DIR_GAP_RE.sub(r"\1 ", s)
    return re.sub(r" {2,}", " ", s).strip()


def format_streak_chip_label(ticker: Any, was: Any, now: Any) -> str:
    return f"stk {ticker} {format_streak_fragment(was)} → {format_streak_fragment(now)}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def snapshot_from_cards(
    cards: Iterable[Mapping[str, Any]] | None,
    *,
    asof: str | None = None,
) -> dict[str, Any]:
    flags: list[str] = []
    watch: list[str] = []
    outliers: list[str] = []
    names: dict[str, Any] = {}
    for card in cards or []:
        if not isinstance(card, Mapping):
            continue
        ticker = mom_streak.card_ticker(card)
        if not ticker:
            continue
        lists = card_lists(card)
        if "FLAGS" in lists:
            flags.append(ticker)
        if "WATCH" in lists:
            watch.append(ticker)
        if "OUTLIERS" in lists:
            outliers.append(ticker)
        score = dapi_enrich.as_float(card.get("mom_score"))
        if score is None:
            score, _src = mom_streak.resolve_card_score(card)
        try:
            streak = int(card.get("mom_streak") or 0)
        except (TypeError, ValueError):
            streak = 0
        names[ticker] = {
            "t": _short(ticker),
            "score": score,
            "side": card.get("mom_streak_side"),
            "streak": streak,
            "label": card.get("mom_streak_label"),
            "lists": sorted(lists),
        }
    return {
        "asof": asof,
        "written_at": _now_iso(),
        "flags": sorted(set(flags), key=lambda t: _short(t)),
        "watch": sorted(set(watch), key=lambda t: _short(t)),
        "outliers": sorted(set(outliers), key=lambda t: _short(t)),
        "names": names,
    }


def load_snapshot(path: Path | str | None = None, root: Path | None = None) -> dict[str, Any] | None:
    p = Path(path) if path is not None else default_snapshot_path(root)
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


def write_snapshot(
    snap: Mapping[str, Any],
    path: Path | str | None = None,
    root: Path | None = None,
) -> Path:
    p = Path(path) if path is not None else default_snapshot_path(root)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(dict(snap), indent=2, default=str) + "\n", encoding="utf-8")
    os.replace(tmp, p)
    return p


def _set_of(snap: Mapping[str, Any] | None, key: str) -> set[str]:
    if not snap:
        return set()
    raw = snap.get(key) or []
    return {str(x) for x in raw if x}


def _names(snap: Mapping[str, Any] | None) -> dict[str, Any]:
    if not snap:
        return {}
    names = snap.get("names")
    return dict(names) if isinstance(names, dict) else {}


def _key_match(ticker: str, pool: Mapping[str, Any]) -> str | None:
    if ticker in pool:
        return ticker
    short = _short(ticker)
    for key in pool:
        if _short(str(key)) == short:
            return str(key)
    return None


def _added_removed(old: set[str], new: set[str]) -> tuple[list[str], list[str]]:
    old_short = {_short(x): x for x in old}
    new_short = {_short(x): x for x in new}
    added = [new_short[k] for k in new_short if k and k not in old_short]
    removed = [old_short[k] for k in old_short if k and k not in new_short]
    added.sort(key=_short)
    removed.sort(key=_short)
    return added, removed


def diff_snapshots(
    prior: Mapping[str, Any] | None,
    current: Mapping[str, Any] | None,
    *,
    score_jump: float = SCORE_JUMP,
    long_streak: int = LONG_STREAK,
) -> dict[str, Any]:
    """Structured delta + chips. ``baseline=True`` when no prior snapshot."""
    current = current or {}
    if prior is None:
        return {
            "baseline": True,
            "new_flags": [],
            "dropped_flags": [],
            "watch_on": [],
            "watch_off": [],
            "new_outliers": [],
            "streak_breaks": [],
            "score_jumps": [],
            "chips": [
                {
                    "label": "baseline set",
                    "cls": "delta-baseline",
                    "title": "First snapshot this book — deltas start on the next Refresh",
                }
            ],
            "extra": [],
        }

    new_flags, dropped_flags = _added_removed(_set_of(prior, "flags"), _set_of(current, "flags"))
    watch_on, watch_off = _added_removed(_set_of(prior, "watch"), _set_of(current, "watch"))
    new_outliers, _dropped_out = _added_removed(_set_of(prior, "outliers"), _set_of(current, "outliers"))

    streak_breaks: list[dict[str, Any]] = []
    score_jumps: list[dict[str, Any]] = []
    old_names = _names(prior)
    new_names = _names(current)
    seen: set[str] = set()
    for ticker, rec in new_names.items():
        if not isinstance(rec, Mapping):
            continue
        prev_key = _key_match(ticker, old_names)
        if prev_key is None:
            continue
        prev = old_names.get(prev_key)
        if not isinstance(prev, Mapping):
            continue
        short = _short(ticker)
        if short in seen:
            continue
        seen.add(short)
        old_side = prev.get("side")
        new_side = rec.get("side")
        try:
            old_streak = int(prev.get("streak") or 0)
        except (TypeError, ValueError):
            old_streak = 0
        try:
            new_streak = int(rec.get("streak") or 0)
        except (TypeError, ValueError):
            new_streak = 0
        side_changed = old_side and new_side and old_side != new_side
        long_ended = old_streak >= long_streak and (
            side_changed or (new_side == old_side and new_streak < max(2, old_streak // 2))
        )
        if side_changed or long_ended:
            streak_breaks.append(
                {
                    "t": short,
                    "ticker": ticker,
                    "from": old_side,
                    "to": new_side,
                    "was_streak": old_streak,
                    "now_streak": new_streak,
                    "label_was": prev.get("label"),
                    "label_now": rec.get("label"),
                }
            )
        old_score = dapi_enrich.as_float(prev.get("score"))
        new_score = dapi_enrich.as_float(rec.get("score"))
        if old_score is not None and new_score is not None:
            delta = new_score - old_score
            if abs(delta) >= score_jump:
                score_jumps.append(
                    {
                        "t": short,
                        "ticker": ticker,
                        "from": old_score,
                        "to": new_score,
                        "delta": round(delta, 4),
                    }
                )

    chips: list[dict[str, str]] = []
    extra: list[dict[str, str]] = []

    def chip(label: str, cls: str, title: str, *, overflow: bool = False) -> None:
        item = {"label": label, "cls": cls, "title": title}
        (extra if overflow else chips).append(item)

    if new_flags:
        shown = new_flags[:4]
        more = len(new_flags) - len(shown)
        lab = "FLAGS +" + " +".join(_short(t) for t in shown)
        if more > 0:
            lab += f" +{more}"
        chip(lab, "delta-flags", "New FLAGS: " + ", ".join(_short(t) for t in new_flags))
    if dropped_flags:
        chip(
            "FLAGS −" + " −".join(_short(t) for t in dropped_flags[:3]),
            "delta-flags-off",
            "Left FLAGS: " + ", ".join(_short(t) for t in dropped_flags),
            overflow=len(dropped_flags) > 2,
        )
    if watch_on or watch_off:
        bits = []
        if watch_on:
            bits.append("+" + " +".join(_short(t) for t in watch_on[:3]))
        if watch_off:
            bits.append("−" + " −".join(_short(t) for t in watch_off[:3]))
        chip(
            "WATCH " + " ".join(bits),
            "delta-watch",
            "WATCH on: " + ", ".join(_short(t) for t in watch_on) + " · off: " + ", ".join(_short(t) for t in watch_off),
        )
    if new_outliers:
        chip(
            "OUT +" + " +".join(_short(t) for t in new_outliers[:4]),
            "delta-out",
            "New outliers: " + ", ".join(_short(t) for t in new_outliers),
        )
    for br in streak_breaks[:4]:
        was = br.get("label_was") or f"{br.get('from')} {br.get('was_streak')}d"
        now = br.get("label_now") or f"{br.get('to')} {br.get('now_streak')}d"
        chip(
            format_streak_chip_label(br.get("t"), was, now),
            "delta-streak",
            f"{br.get('ticker')} streak {was} → {now}",
        )
    for br in streak_breaks[4:]:
        chip(f"stk {br.get('t')}", "delta-streak", str(br.get("ticker")), overflow=True)
    for jmp in score_jumps[:3]:
        sign = "+" if float(jmp["delta"]) >= 0 else ""
        chip(
            f"{jmp['t']} {sign}{jmp['delta']:g}",
            "delta-jump",
            f"{jmp.get('ticker')} score {jmp['from']:g} → {jmp['to']:g}",
        )
    for jmp in score_jumps[3:]:
        sign = "+" if float(jmp["delta"]) >= 0 else ""
        chip(f"{jmp['t']} {sign}{jmp['delta']:g}", "delta-jump", str(jmp.get("ticker")), overflow=True)

    if not chips and not extra:
        chip("no book moves", "delta-quiet", "No FLAGS / WATCH / outlier / streak / score jumps vs last Refresh")

    return {
        "baseline": False,
        "new_flags": new_flags,
        "dropped_flags": dropped_flags,
        "watch_on": watch_on,
        "watch_off": watch_off,
        "new_outliers": new_outliers,
        "streak_breaks": streak_breaks,
        "score_jumps": score_jumps,
        "chips": chips,
        "extra": extra,
    }


def _script_json(blob: str) -> str:
    return (blob or "").replace("</", "<\\/")


def embed_db(diff: Mapping[str, Any] | None) -> str:
    payload = {
        "baseline": bool((diff or {}).get("baseline")),
        "chips": list((diff or {}).get("chips") or []),
        "extra": list((diff or {}).get("extra") or []),
    }
    blob = json.dumps(payload, separators=(",", ":"), ensure_ascii=True, default=str)
    return f'<script type="application/json" id="{DB_SCRIPT_ID}">{_script_json(blob)}</script>'


def strip_css() -> str:
    return f"""
#{HOST_ID}, .fd-book-delta {{
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  width: 100%;
  box-sizing: border-box;
  flex: 0 0 100%;
  margin: 0;
  padding: 0;
  min-height: 20px;
  overflow: visible;
}}
.fd-book-delta-kicker {{
  display: flex;
  align-items: center;
  box-sizing: border-box;
  flex: 0 0 100%;
  width: 100%;
  min-height: 20px;
  height: auto;
  margin: 0;
  padding: 0;
  font: 650 10px/1.15 "Segoe UI", "Segoe UI Symbol", "DejaVu Sans", "Noto Sans", ui-sans-serif, system-ui, sans-serif;
  letter-spacing: 0.04em;
  color: #9ca3af;
  text-transform: none;
  white-space: nowrap;
  overflow: visible;
}}
.fd-dchip {{
  display: inline-flex;
  align-items: center;
  justify-content: center;
  box-sizing: border-box;
  height: 20px;
  margin: 0;
  padding: 2px 8px;
  border-radius: 3px;
  border: 1px solid #4b5563;
  color: #d1d5db;
  background: #111827;
  font: 650 10px/1.15 "Segoe UI", "Segoe UI Symbol", "DejaVu Sans", "Noto Sans Symbols 2", "Noto Sans Symbols", "Noto Sans", ui-sans-serif, system-ui, sans-serif;
  font-variant-numeric: tabular-nums;
  letter-spacing: 0.04em;
  white-space: nowrap;
  flex: 0 0 auto;
}}
.fd-dchip[hidden], .fd-delta-extra-chip[hidden] {{ display: none !important; }}
.fd-dchip.delta-flags {{ color: #93c5fd; border-color: #3b82f6; }}
.fd-dchip.delta-flags-off {{ color: #9ca3af; border-color: #4b5563; }}
.fd-dchip.delta-watch {{ color: #fde68a; border-color: #a3a3a3; }}
.fd-dchip.delta-out {{ color: #c084fc; border-color: #a855f7; }}
.fd-dchip.delta-streak {{ color: #fda4af; border-color: #fb7185; }}
.fd-dchip.delta-jump {{ color: #6ee7b7; border-color: #34d399; }}
.fd-dchip.delta-baseline, .fd-dchip.delta-quiet {{ color: #6b7280; border-color: #374151; }}
.fd-book-delta-more {{
  display: inline-flex;
  align-items: center;
  justify-content: center;
  box-sizing: border-box;
  height: 20px;
  margin: 0;
  padding: 2px 8px;
  font: 650 10px/1.15 "Segoe UI", "Segoe UI Symbol", "DejaVu Sans", "Noto Sans", ui-sans-serif, system-ui, sans-serif;
  letter-spacing: 0.04em;
  color: #9ca3af;
  background: #111827;
  border: 1px dashed #4b5563;
  border-radius: 3px;
  white-space: nowrap;
  cursor: pointer;
  appearance: none;
  -webkit-appearance: none;
  flex: 0 0 auto;
}}
.fd-book-delta-extra {{
  display: none;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  width: 100%;
  margin: 0;
  padding: 0;
}}
.fd-book-delta-extra.is-on {{ display: flex; }}
""".strip()


def strip_js() -> str:
    return r"""
(function () {
  if (window.__FD_DELTA_BOUND__) return;
  window.__FD_DELTA_BOUND__ = true;
  var HOST = "fd-book-delta";
  var DB = "fd-book-delta-db";
  function $(id) { return document.getElementById(id); }
  function payload() {
    var el = $(DB);
    if (!el) return { chips: [], extra: [] };
    try { return JSON.parse(el.textContent || "{}") || { chips: [], extra: [] }; }
    catch (e) { return { chips: [], extra: [] }; }
  }
  function chipEl(c) {
    var span = document.createElement("span");
    span.className = "fd-dchip " + (c.cls || "");
    span.title = c.title || "";
    span.textContent = c.label || "";
    return span;
  }
  function paint() {
    var host = $(HOST);
    if (!host) return;
    var data = payload();
    host.innerHTML = "";
    var kicker = document.createElement("span");
    kicker.className = "fd-book-delta-kicker";
    kicker.textContent = data.baseline ? "book" : "since last Refresh";
    host.appendChild(kicker);
    (data.chips || []).forEach(function (c) { host.appendChild(chipEl(c)); });
    var extra = data.extra || [];
    if (extra.length) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "fd-book-delta-more";
      btn.textContent = "more";
      var wrap = document.createElement("div");
      wrap.className = "fd-book-delta-extra";
      extra.forEach(function (c) { wrap.appendChild(chipEl(c)); });
      btn.addEventListener("click", function () {
        var on = wrap.classList.toggle("is-on");
        btn.textContent = on ? "less" : "more";
      });
      host.appendChild(btn);
      host.appendChild(wrap);
    }
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", paint);
  else paint();
})();
""".strip()


def host_html(diff: Mapping[str, Any] | None = None) -> str:
    diff = diff or {}
    chips = list(diff.get("chips") or [])
    extra = list(diff.get("extra") or [])
    kicker = "book" if diff.get("baseline") else "since last Refresh"
    bits = [
        f'<div id="{HOST_ID}" class="fd-book-delta" role="status" aria-label="Book changes since last Refresh">',
        f'<span class="fd-book-delta-kicker">{html.escape(kicker)}</span>',
    ]
    for chip in chips:
        label = html.escape(str(chip.get("label") or ""))
        cls = html.escape(str(chip.get("cls") or ""))
        title = html.escape(str(chip.get("title") or ""), quote=True)
        bits.append(f'<span class="fd-dchip {cls}" title="{title}">{label}</span>')
    if extra:
        bits.append('<button type="button" class="fd-book-delta-more">more</button>')
        for chip in extra:
            label = html.escape(str(chip.get("label") or ""))
            cls = html.escape(str(chip.get("cls") or ""))
            title = html.escape(str(chip.get("title") or ""), quote=True)
            bits.append(f'<span class="fd-dchip {cls} fd-delta-extra-chip" hidden title="{title}">{label}</span>')
    bits.append("</div>")
    return "".join(bits)


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


def _ensure_host(html_text: str, diff: Mapping[str, Any] | None) -> str:
    host = host_html(diff)
    if re.search(rf'id=["\']{HOST_ID}["\']', html_text, re.I):
        return re.sub(
            rf'<div\b[^>]*\bid=["\']{HOST_ID}["\'][^>]*>.*?</div>',
            host,
            html_text,
            count=1,
            flags=re.I | re.S,
        )
    for pat in (
        r'(<div\b[^>]*\bid=["\']gics-filter-strip["\'][^>]*>.*?</div>)',
        r"(<nav\b[^>]*>.*?</nav>)",
        r'(<div\b[^>]*class=["\'][^"\']*(?:filter-strip|filters|chip-row)[^"\']*["\'][^>]*>.*?</div>)',
        r"(<h1\b[^>]*>.*?</h1>)",
        r"(<body\b[^>]*>)",
    ):
        match = re.search(pat, html_text, re.I | re.S)
        if match:
            return html_text[: match.end()] + "\n" + host + html_text[match.end() :]
    return host + "\n" + html_text


def _ensure_db(html_text: str, diff: Mapping[str, Any] | None) -> str:
    tag = embed_db(diff)
    if re.search(rf'id=["\']{DB_SCRIPT_ID}["\']', html_text, re.I):
        return re.sub(
            rf'<script\b[^>]*\bid=["\']{DB_SCRIPT_ID}["\'][^>]*>.*?</script>',
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


def ensure_embedded(html_text: str, diff: Mapping[str, Any] | None = None) -> str:
    """Strip host + chips + JS after every HTML write."""
    text = html_text or ""
    text = _ensure_css(text)
    text = _ensure_host(text, diff)
    text = _ensure_db(text, diff)
    text = _ensure_js(text)
    return text
