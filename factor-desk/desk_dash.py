"""Assemble the Factor Desk dashboard.

Thin ENRICH HOOK: ``attach_enrichment`` / ``pills_html`` drop onto existing
FLAGS/WATCH/MOM/OUTLIERS/OPTIONS cards. Refresh without ``dapi_enrichment.json``
still renders. No news layer. No earnings calendar UI.
"""

from __future__ import annotations

import html
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping, MutableMapping

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import dapi_enrich  # noqa: E402
import gics_filter  # noqa: E402
import mom_streak  # noqa: E402

LOG = logging.getLogger("desk_dash")
HTML_NAME = "factorbook.html"

# Live factorbook.html is ~2.7MB. Never replace that class of file with the
# skinny enrich-only grid — patch GICS + streak into the existing HTML.
LIVE_MIN_BYTES = 1_000_000
LIVE_NAV_MARKERS = (
    "Refresh",
    "Momentum Up",
    "Momentum Down",
    "Outliers",
    "Options",
)

OPTIONS_REFRESH_BTN = (
    '<button type="button" id="options-refresh" class="nav-btn options-refresh" '
    'data-sidecar-options-refresh="1">Options Refresh</button>'
)

PROGRESS_HTML = """
<div id="sidecar-progress" class="sidecar-progress" hidden aria-live="polite">
  <span class="sidecar-progress-label" id="sidecar-progress-label">idle</span>
  <div class="sidecar-progress-track" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0" id="sidecar-progress-track">
    <div class="sidecar-progress-bar" id="sidecar-progress-bar"></div>
  </div>
</div>
""".strip()

NAV_HTML = f"""
<nav class="topnav" aria-label="Factor Desk">
  <button type="button" id="refresh" class="nav-btn refresh" data-sidecar-refresh="1">Refresh</button>
  {OPTIONS_REFRESH_BTN}
  <button type="button" class="nav-btn" data-view="mom-up">Momentum Up</button>
  <button type="button" class="nav-btn" data-view="mom-down">Momentum Down</button>
  <button type="button" class="nav-btn" data-view="outliers">Outliers</button>
  <button type="button" class="nav-btn" data-view="options">Options</button>
  {PROGRESS_HTML}
</nav>
""".strip()

NAV_CSS = """
.topnav {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  margin: 0 0 12px;
}
.nav-btn, #refresh, .refresh, #options-refresh, .options-refresh {
  display: inline-block;
  font: 650 11px/1.2 "Segoe UI", "DejaVu Sans", "Noto Sans", ui-sans-serif, system-ui, sans-serif;
  letter-spacing: 0.04em;
  padding: 4px 10px;
  border-radius: 3px;
  border: 1px solid #4b5563;
  color: #e5e7eb;
  background: #1f2937;
  cursor: pointer;
}
.nav-btn:hover, #refresh:hover { border-color: #93c5fd; color: #fff; }
#options-refresh, .options-refresh { border-color: #9d174d; color: #f9a8d4; }
#options-refresh:hover, .options-refresh:hover { border-color: #f9a8d4; color: #fff; }
.sidecar-progress {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 180px;
  flex: 1 1 160px;
  margin-left: 4px;
}
.sidecar-progress[hidden] { display: none !important; }
.sidecar-progress-label {
  font: 650 10px/1.2 "Segoe UI", "DejaVu Sans", "Noto Sans", ui-sans-serif, system-ui, sans-serif;
  color: #93c5fd;
  letter-spacing: 0.04em;
  white-space: nowrap;
}
.sidecar-progress.kind-options .sidecar-progress-label { color: #f9a8d4; }
.sidecar-progress-track {
  flex: 1;
  height: 6px;
  background: #1f2937;
  border: 1px solid #374151;
  border-radius: 3px;
  overflow: hidden;
  min-width: 80px;
}
.sidecar-progress-bar {
  height: 100%;
  width: 0%;
  background: #3b82f6;
  transition: width 0.2s linear;
}
.sidecar-progress.kind-options .sidecar-progress-bar { background: #ec4899; }
"""

# Same visual language as live-desk .badge / .spike-chip
PILL_CSS = """
.badge, .spike-chip {
  display: inline-block;
  font: 650 10px/1.15 "Segoe UI", "Segoe UI Symbol", "DejaVu Sans", "Noto Sans", ui-sans-serif, system-ui, sans-serif;
  letter-spacing: 0.04em;
  padding: 2px 6px;
  margin: 0 3px 3px 0;
  border-radius: 3px;
  border: 1px solid #6b7280;
  color: #d1d5db;
  background: #111827;
  text-transform: none;
  vertical-align: middle;
}
.badge.si-crowded, .spike-chip.si-crowded { color: #f59e0b; border-color: #f59e0b; }
.badge.si-light, .spike-chip.si-light { color: #34d399; border-color: #34d399; }
.badge.iv-rich, .spike-chip.iv-rich { color: #f472b6; border-color: #f472b6; }
.badge.iv-cheap, .spike-chip.iv-cheap { color: #60a5fa; border-color: #60a5fa; }
.badge.illiquid, .spike-chip.illiquid { color: #fb7185; border-color: #fb7185; }
.badge.inst-high, .spike-chip.inst-high { color: #c084fc; border-color: #c084fc; }
.badge.evt-near, .spike-chip.evt-near { color: #fbbf24; border-color: #fbbf24; }
.badge.evt-watch, .spike-chip.evt-watch { color: #fde68a; border-color: #a3a3a3; }
.badge.beta, .spike-chip.beta { color: #93c5fd; border-color: #3b82f6; }
.badge.credit, .spike-chip.credit { color: #fda4af; border-color: #fb7185; }
.badge.credit-stress, .spike-chip.credit-stress { color: #fecaca; border-color: #ef4444; background: #3f1d1d; }
.badge.trough, .spike-chip.trough { color: #6ee7b7; border-color: #34d399; }
.badge.opt-spike, .spike-chip.opt-spike { color: #f9a8d4; border-color: #ec4899; }
.badge.mom-streak-up, .spike-chip.mom-streak-up { color: #6ee7b7; border-color: #34d399; }
.badge.mom-streak-down, .spike-chip.mom-streak-down { color: #fda4af; border-color: #fb7185; }
.badge.mom-streak-at, .spike-chip.mom-streak-at { color: #fde68a; border-color: #a3a3a3; }
"""


def load_enrichment(root: Path | None = None) -> dict[str, Any] | None:
    base = Path(root) if root is not None else HERE
    return dapi_enrich.load_enrichment(base / dapi_enrich.ENRICH_FILENAME)


def load_gics_cache(root: Path | None = None) -> dict[str, Any] | None:
    base = Path(root) if root is not None else HERE
    return dapi_enrich.load_gics_cache(root=base)


def attach_enrichment(
    card: MutableMapping[str, Any],
    rec: Mapping[str, Any] | None = None,
    book: Mapping[str, Any] | None = None,
    ticker: str | None = None,
    cache: Mapping[str, Any] | None = None,
    hist: Mapping[str, Any] | None = None,
    series_by_ticker: Mapping[str, list] | None = None,
) -> MutableMapping[str, Any]:
    """Attach ``si_ratio``, ``vol_regime``, ``liq``, ``inst_pct``, ``event_days``,
    ``beta``, ``credit``, ``enrich_pills``, ``gics_sector_name``, mom streak.
    Missing enrich file → nulls + empty pills.
    """
    if rec is None and book is not None:
        rec = dapi_enrich.lookup_name(book, ticker or str(card.get("ticker") or card.get("name") or ""))
    if rec is None and ticker:
        rec = None
    dapi_enrich.attach_card_fields(card, rec)
    gics_filter.overlay_sector(card, rec, cache=cache, book=book)
    return mom_streak.attach_card(card, hist=hist, series_by_ticker=series_by_ticker)


def attach_all(
    cards: Iterable[MutableMapping[str, Any]],
    book: Mapping[str, Any] | None,
    cache: Mapping[str, Any] | None = None,
    hist: Mapping[str, Any] | None = None,
    series_by_ticker: Mapping[str, list] | None = None,
) -> list[MutableMapping[str, Any]]:
    out: list[MutableMapping[str, Any]] = []
    for card in cards:
        ticker = str(card.get("ticker") or card.get("name") or "")
        rec = dapi_enrich.lookup_name(book, ticker) if book else None
        out.append(
            attach_enrichment(
                card,
                rec,
                cache=cache,
                hist=hist,
                series_by_ticker=series_by_ticker,
            )
        )
    return out


def pills_html(pills: Any) -> str:
    if not isinstance(pills, list):
        return ""
    bits: list[str] = []
    for pill in pills:
        if not isinstance(pill, Mapping):
            continue
        label = html.escape(str(pill.get("label") or ""))
        cls = html.escape(str(pill.get("cls") or ""))
        key = html.escape(str(pill.get("key") or ""))
        title = html.escape(str(pill.get("title") or ""), quote=True)
        if not label:
            continue
        title_attr = f' title="{title}"' if title else ""
        bits.append(f'<span class="badge spike-chip {cls}" data-key="{key}"{title_attr}>{label}</span>')
    return "".join(bits)


def cards_from_enrichment(book: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    names = (book or {}).get("names") if isinstance(book, Mapping) else None
    if not isinstance(names, dict):
        return cards
    for ticker, rec in names.items():
        if not isinstance(rec, Mapping):
            rec = {}
        card: dict[str, Any] = {
            "ticker": ticker,
            "name": ticker,
            "si_ratio": rec.get("si_ratio"),
            "vol_regime": rec.get("vol_regime"),
            "liq": rec.get("liq"),
            "inst_pct": rec.get("inst_pct"),
            "event_days": rec.get("event_days"),
            "beta": rec.get("beta"),
            "credit": rec.get("credit"),
            "enrich_pills": list(rec.get("enrich_pills") or []),
            "gics_sector_name": rec.get("gics_sector_name"),
            "mom_score": rec.get("mom_score"),
        }
        cards.append(card)
    return cards


def _fmt(value: Any, digits: int = 2) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return html.escape(str(value))


def looks_like_live_desk(html_text: str) -> bool:
    if not html_text:
        return False
    if all(marker in html_text for marker in LIVE_NAV_MARKERS):
        return True
    return len(html_text.encode("utf-8")) >= LIVE_MIN_BYTES


def _gics_sector_db_json(
    book: Mapping[str, Any] | None = None,
    cards: Iterable[Mapping[str, Any]] | None = None,
    cache: Mapping[str, Any] | None = None,
    root: Path | None = None,
) -> str:
    """Filled ticker→sector JSON for ``#gics-sector-db`` / ``__GICS_SECTOR_DB__``."""
    if cache is None and root is not None:
        cache = dapi_enrich.load_gics_cache(root=root)
    return gics_filter._gics_sector_db_json(items=cards, cache=cache, book=book)


def sidecar_js() -> str:
    """Bind Options Refresh (and skinny Refresh) to the local sidecar.

    Does **not** attach to live ``#refresh`` unless it has ``data-sidecar-refresh=1``,
    so Desktop's existing Refresh handler is left alone.
    """
    return r"""
(function () {
  if (window.__FD_SIDECAR_BOUND__) return;
  window.__FD_SIDECAR_BOUND__ = true;
  var ORIGINS = ["http://127.0.0.1:8765", "http://localhost:8765"];
  var pollTimer = null;
  var armed = false;
  var hideTimer = null;

  function origin() { return ORIGINS[0]; }

  function $(id) { return document.getElementById(id); }

  function hostEl() { return $("sidecar-progress"); }
  function barEl() { return $("sidecar-progress-bar"); }
  function labEl() { return $("sidecar-progress-label"); }
  function trackEl() { return $("sidecar-progress-track"); }

  function setLabel(text) {
    var lab = labEl();
    if (lab) lab.textContent = text || "";
  }

  function paint(st) {
    st = st || {};
    var host = hostEl();
    var bar = barEl();
    var track = trackEl();
    if (!host) return;
    var isOpt = st.kind === "options" || !!st.options;
    var pct = Number(st.pct || 0);
    if (isNaN(pct)) pct = 0;
    pct = Math.max(0, Math.min(100, pct));
    var stage = String(st.stage || "");
    var label = stage;
    if (isOpt && stage.toLowerCase().indexOf("options") < 0) {
      label = stage ? ("options · " + stage) : "options";
    } else if (!isOpt && !stage) {
      label = "refresh";
    }
    if (st.error) label = (isOpt ? "options · error" : "error") + ": " + st.error;
    host.classList.toggle("kind-options", !!isOpt);
    host.hidden = false;
    if (bar) bar.style.width = pct + "%";
    if (track) track.setAttribute("aria-valuenow", String(Math.round(pct)));
    setLabel(label + (st.busy ? "  " + Math.round(pct) + "%" : (stage === "done" ? "  100%" : "")));
  }

  function post(paths, body) {
    var payload = JSON.stringify(body || {});
    var i = 0;
    function tryOne() {
      if (i >= paths.length) return Promise.reject(new Error("sidecar offline"));
      var url = origin() + paths[i++];
      return fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: payload
      }).then(function (r) {
        if (r.status === 202 || r.ok) return r.json();
        if (r.status === 404 || r.status === 405) throw new Error("try next");
        return r.json().then(function (j) {
          var err = new Error((j && (j.error || j.stage)) || String(r.status));
          err.status = r.status;
          throw err;
        });
      }).catch(function (err) {
        if (err && err.status === 409) throw err;
        return tryOne();
      });
    }
    return tryOne();
  }

  function stopPoll() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  }

  function beginPoll() {
    stopPoll();
    function tick() {
      fetch(origin() + "/status").then(function (r) { return r.json(); }).then(function (st) {
        paint(st);
        if (st.busy) return;
        stopPoll();
        if (!armed) return;
        armed = false;
        if (st.error) {
          setLabel((st.kind === "options" || st.options ? "options · error: " : "error: ") + st.error);
          return;
        }
        window.setTimeout(function () { window.location.reload(); }, 250);
      }).catch(function () {
        setLabel("sidecar offline");
        stopPoll();
        armed = false;
      });
    }
    tick();
    pollTimer = window.setInterval(tick, 400);
  }

  function startJob(isOptions) {
    var host = hostEl();
    if (host) {
      host.hidden = false;
      host.classList.toggle("kind-options", !!isOptions);
    }
    if (hideTimer) { clearTimeout(hideTimer); hideTimer = null; }
    armed = true;
    paint({ busy: true, pct: 0, stage: "queued", kind: isOptions ? "options" : "refresh", options: !!isOptions });
    var paths = isOptions
      ? ["/options-refresh", "/api/options_refresh", "/api/options-refresh", "/api/refresh_live", "/refresh"]
      : ["/refresh", "/api/refresh", "/api/refresh_live"];
    var body = isOptions ? { options: 1 } : {};
    post(paths, body).then(function () {
      beginPoll();
    }).catch(function (err) {
      armed = false;
      if (err && err.status === 409) setLabel(isOptions ? "options · busy" : "busy");
      else setLabel("sidecar offline");
    });
  }

  document.querySelectorAll("[data-sidecar-refresh='1']").forEach(function (btn) {
    btn.addEventListener("click", function (ev) {
      ev.preventDefault();
      ev.stopPropagation();
      startJob(false);
    });
  });
  var optBtn = document.getElementById("options-refresh");
  if (optBtn && !optBtn.getAttribute("data-fd-bound")) {
    optBtn.setAttribute("data-fd-bound", "1");
    optBtn.addEventListener("click", function (ev) {
      ev.preventDefault();
      ev.stopPropagation();
      startJob(true);
    });
  }
})();
""".strip()


_REFRESH_BTN_RE = re.compile(
    r'(<button\b(?=[^>]*\bid=["\']refresh["\'])[^>]*>.*?</button>)',
    re.I | re.S,
)


def _has_options_refresh_btn(html_text: str) -> bool:
    return bool(re.search(r'id=["\']options-refresh["\']', html_text, re.I))


def _ensure_nav(html_text: str) -> str:
    if all(marker in html_text for marker in LIVE_NAV_MARKERS):
        return html_text
    if "</head>" in html_text and ".topnav" not in html_text:
        html_text = html_text.replace("</head>", f"<style>\n{NAV_CSS}\n</style>\n</head>", 1)
    if "<body" in html_text:
        html_text = re.sub(r"(<body\b[^>]*>)", r"\1\n" + NAV_HTML + "\n", html_text, count=1, flags=re.I)
        return html_text
    return NAV_HTML + "\n" + html_text


def _ensure_options_refresh_ui(html_text: str) -> str:
    """Always keep Options Refresh beside Refresh. Does not change LIVE_NAV_MARKERS."""
    if not html_text:
        return html_text
    if ".sidecar-progress" not in html_text:
        block = f'<style id="sidecar-refresh-css">\n{NAV_CSS}\n</style>\n'
        if "</head>" in html_text:
            html_text = html_text.replace("</head>", block + "</head>", 1)
        else:
            html_text = block + html_text
    if not _has_options_refresh_btn(html_text):
        m = _REFRESH_BTN_RE.search(html_text)
        if m:
            html_text = html_text[: m.end()] + "\n  " + OPTIONS_REFRESH_BTN + html_text[m.end() :]
        elif "<nav" in html_text.lower():
            html_text = re.sub(
                r"(<nav\b[^>]*>)",
                r"\1\n  " + OPTIONS_REFRESH_BTN,
                html_text,
                count=1,
                flags=re.I,
            )
        elif "<body" in html_text.lower():
            html_text = re.sub(
                r"(<body\b[^>]*>)",
                r"\1\n  " + OPTIONS_REFRESH_BTN,
                html_text,
                count=1,
                flags=re.I,
            )
        else:
            html_text = OPTIONS_REFRESH_BTN + "\n" + html_text
    if 'id="sidecar-progress"' not in html_text and "id='sidecar-progress'" not in html_text:
        if _has_options_refresh_btn(html_text):
            html_text = re.sub(
                r'(<button\b(?=[^>]*\bid=["\']options-refresh["\'])[^>]*>.*?</button>)',
                lambda m: m.group(1) + "\n  " + PROGRESS_HTML,
                html_text,
                count=1,
                flags=re.I | re.S,
            )
        else:
            html_text = PROGRESS_HTML + "\n" + html_text
    if 'id="sidecar-refresh-js"' not in html_text:
        script = f'<script id="sidecar-refresh-js">\n{sidecar_js()}\n</script>\n'
        if "</body>" in html_text:
            html_text = html_text.replace("</body>", script + "</body>", 1)
        else:
            html_text = html_text + "\n" + script
    return html_text


def render_html(
    cards: list[Mapping[str, Any]] | None = None,
    *,
    book: Mapping[str, Any] | None = None,
    cache: Mapping[str, Any] | None = None,
    hist: Mapping[str, Any] | None = None,
    series_by_ticker: Mapping[str, list] | None = None,
    title: str = "Factor Desk",
) -> str:
    book = book if book is not None else load_enrichment()
    if cache is None:
        cache = dapi_enrich.load_gics_cache()
    if cards is None:
        cards = cards_from_enrichment(book)
    cards = attach_all(list(cards), book, cache=cache, hist=hist, series_by_ticker=series_by_ticker)
    rows: list[str] = []
    for card in cards:
        ticker = html.escape(str(card.get("ticker") or card.get("name") or ""))
        pills = pills_html(card.get("enrich_pills"))
        sector = gics_filter.sector_of(card, cache) or ""
        sector_attr = html.escape(sector, quote=True)
        score = card.get("mom_score")
        rows.append(
            f"""
            <article class="card" data-t="{ticker}" data-ticker="{ticker}" data-gics-sector="{sector_attr}">
              <header>
                <h2>{ticker}</h2>
                <div class="pills">{pills}</div>
              </header>
              <dl>
                <div><dt>si_ratio</dt><dd>{_fmt(card.get("si_ratio"))}</dd></div>
                <div><dt>vol_regime</dt><dd>{_fmt(card.get("vol_regime"))}</dd></div>
                <div><dt>liq</dt><dd>{_fmt(card.get("liq"))}</dd></div>
                <div><dt>inst_pct</dt><dd>{_fmt(card.get("inst_pct"))}</dd></div>
                <div><dt>event_days</dt><dd>{_fmt(card.get("event_days"), 0)}</dd></div>
                <div><dt>beta</dt><dd>{_fmt(card.get("beta"))}</dd></div>
                <div><dt>credit</dt><dd>{_fmt(card.get("credit"))}</dd></div>
                <div><dt>mom_score</dt><dd>{_fmt(score, 0 if isinstance(score, int) else 2)}</dd></div>
              </dl>
            </article>
            """
        )
    empty = ""
    if not rows:
        empty = (
            '<p class="empty">No enrichment file — Refresh still works. '
            "Cards will gain SI / IV / ILLIQ / INST / EVT / β / CRD pills after "
            "<code>dapi_enrich</code> writes <code>dapi_enrichment.json</code>.</p>"
        )
    asof = html.escape(str((book or {}).get("asof") or ""))
    meta = (book or {}).get("meta") if isinstance(book, Mapping) else {}
    intra = bool((meta or {}).get("intraday")) if isinstance(meta, Mapping) else False
    sectors = gics_filter.sectors_present(cards, cache)
    db = gics_filter.filled_sector_db(cards, cache=cache, book=book)
    streak_map = mom_streak.streak_db(cards)
    gics_note = ""
    if rows and not sectors:
        gics_note = (
            '<p class="meta">No GICS sector names in the book. Optional one-shot: '
            "<code>python dapi_enrich.py --gics-once</code> (not part of Refresh).</p>"
        )
    html_text = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <title>{html.escape(title)}</title>
  <style>
    :root {{ color-scheme: dark; }}
    body {{
      margin: 0; padding: 24px;
      font: 14px/1.4 "Segoe UI", "DejaVu Sans", "Noto Sans", ui-sans-serif, system-ui, sans-serif;
      background: #0b0f14; color: #e5e7eb;
    }}
    h1 {{ font-size: 18px; font-weight: 650; margin: 0 0 6px; }}
    .meta {{ color: #9ca3af; font-size: 12px; margin-bottom: 18px; }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
      gap: 12px;
    }}
    .card {{
      border: 1px solid #1f2937;
      background: #111827;
      border-radius: 8px;
      padding: 12px 14px;
    }}
    .card h2 {{ font-size: 14px; margin: 0 0 8px; letter-spacing: 0.02em; }}
    .pills {{ min-height: 18px; }}
    dl {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 6px 12px;
      margin: 10px 0 0;
    }}
    dt {{ color: #6b7280; font-size: 10px; text-transform: uppercase; }}
    dd {{ margin: 0; font-variant-numeric: tabular-nums; }}
    .empty {{ color: #9ca3af; }}
    {NAV_CSS}
    {PILL_CSS}
    {gics_filter.strip_css()}
    {mom_streak.streak_css()}
  </style>
</head>
<body>
  {NAV_HTML}
  <h1>{html.escape(title)}</h1>
  <p class="meta">asof {asof or "—"} · Refresh / Momentum Up / Down · GICS chips + mom streak vs 5 · intraday={"on" if intra else "off"}</p>
  {gics_filter.render_strip(sectors)}
  {gics_note}
  {empty}
  <div class="grid">
    {"".join(rows)}
  </div>
  {gics_filter.embed_db(db)}
  {mom_streak.embed_db(streak_map)}
  <script>
  {gics_filter.strip_js()}
  </script>
  <script>
  {mom_streak.strip_js()}
  </script>
</body>
</html>
"""
    html_text = gics_filter.ensure_embedded(html_text, db)
    html_text = mom_streak.ensure_embedded(html_text, streak_map)
    return _ensure_options_refresh_ui(html_text)


def write_combined(
    path: Path | str | None = None,
    *,
    root: Path | None = None,
    cards: list[Mapping[str, Any]] | None = None,
    book: Mapping[str, Any] | None = None,
    html: str | None = None,
) -> Path:
    """Write ``factorbook.html``. Always embeds filled GICS db + strip JS.

    If a live (~2.7MB / nav) dashboard already exists, **patch** it in place.
    Never replace it with the skinny enrich-only grid.
    """
    base = Path(root) if root is not None else HERE
    dest = Path(path) if path is not None else base / HTML_NAME
    if book is None:
        book = load_enrichment(base)
    cache = dapi_enrich.load_gics_cache(root=base)
    if cards is None:
        cards = cards_from_enrichment(book)
    cards = [dict(c) for c in cards]
    hist = mom_streak.rebuild_hist_for_cards(cards, root=base, write=True)
    series, _src = mom_streak.discover_score_series(base)
    cards = attach_all(cards, book, cache=cache, hist=hist, series_by_ticker=series)

    existing = ""
    if html is not None:
        existing = html
    elif dest.is_file():
        existing = dest.read_text(encoding="utf-8")

    if existing and looks_like_live_desk(existing):
        text = existing
        LOG.info("write_combined: patching live desk HTML (%s bytes)", len(existing.encode("utf-8")))
    else:
        text = render_html(cards, book=book, cache=cache, hist=hist, series_by_ticker=series)

    text = _ensure_nav(text)
    text = _ensure_options_refresh_ui(text)
    mapping = gics_filter.filled_sector_db(cards, cache=cache, book=book)
    if gics_filter.GICS_SECTOR_DB_PLACEHOLDER in text:
        text = text.replace(gics_filter.GICS_SECTOR_DB_PLACEHOLDER, _gics_sector_db_json(book, cards, cache, base))
    text = gics_filter.ensure_embedded(text, mapping)
    text = mom_streak.ensure_embedded(text, mom_streak.streak_db(cards))
    dest.write_text(text, encoding="utf-8")
    LOG.info("wrote %s (%s bytes)", dest, dest.stat().st_size)
    return dest


def assemble_and_write(
    path: Path | str | None = None,
    *,
    root: Path | None = None,
    cards: list[Mapping[str, Any]] | None = None,
    book: Mapping[str, Any] | None = None,
) -> Path:
    return write_combined(path, root=root, cards=cards, book=book)


def rebuild(path: Path | str | None = None, root: Path | None = None) -> Path:
    return assemble_and_write(path, root=root)


def main(argv: list[str] | None = None) -> int:
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="Assemble Factor Desk HTML")
    p.add_argument("--root", default="")
    p.add_argument("--out", default="")
    args = p.parse_args(argv)
    root = Path(args.root) if args.root else HERE
    out = Path(args.out) if args.out else root / HTML_NAME
    assemble_and_write(out, root=root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
