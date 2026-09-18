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
import chart_marks  # noqa: E402
import breakout  # noqa: E402
import book_delta  # noqa: E402
import desk_hitch  # noqa: E402
import paper_trade  # noqa: E402
import s_score  # noqa: E402

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
  {PROGRESS_HTML}
  <button type="button" class="nav-btn" data-view="mom-up">Momentum Up</button>
  <button type="button" class="nav-btn" data-view="mom-down">Momentum Down</button>
  <button type="button" class="btn nav-btn" id="fd-nav-breakout" data-view="breakout" data-fd-breakout="1">Breakout</button>
  <button type="button" class="btn nav-btn" id="fd-nav-breakdown" data-view="breakdown" data-fd-breakdown="1">Breakdown</button>
  <button type="button" class="nav-btn" data-view="outliers">Outliers</button>
  <button type="button" class="nav-btn" data-view="options">Options</button>
  <button type="button" class="btn nav-btn" id="fd-nav-paper" data-view="paper" data-fd-paper-nav="1">Paper</button>
  <button type="button" class="btn nav-btn" id="fd-nav-experimental" data-view="experimental" data-fd-sscore="1">Experimental</button>
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
  display: none;
  align-items: center;
  gap: 8px;
  min-width: 220px;
  flex: 1 1 200px;
  margin: 0 8px 0 2px;
}
.sidecar-progress.is-on { display: flex !important; }
.sidecar-progress-label {
  font: 650 11px/1.2 "Segoe UI", "DejaVu Sans", "Noto Sans", ui-sans-serif, system-ui, sans-serif;
  color: #93c5fd;
  letter-spacing: 0.04em;
  white-space: nowrap;
}
.sidecar-progress.kind-options .sidecar-progress-label { color: #f9a8d4; }
.sidecar-progress-track {
  flex: 1;
  height: 8px;
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
.badge.desk-hitch, .spike-chip.desk-hitch { color: #c4b5fd; border-color: #8b5cf6; }
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
        rec = dapi_enrich.lookup_name(book, ticker or mom_streak.card_ticker(card))
    ticker = ticker or mom_streak.card_ticker(card)
    if ticker and not str(card.get("ticker") or "").strip():
        card["ticker"] = ticker
    if rec is None and ticker:
        rec = None
    dapi_enrich.attach_card_fields(card, rec)
    gics_filter.overlay_sector(card, rec, cache=cache, book=book)
    paper_trade.attach_mark(card, rec)
    return mom_streak.attach_card(card, hist=hist, series_by_ticker=series_by_ticker)


def attach_all(
    cards: Iterable[MutableMapping[str, Any]],
    book: Mapping[str, Any] | None = None,
    cache: Mapping[str, Any] | None = None,
    hist: Mapping[str, Any] | None = None,
    series_by_ticker: Mapping[str, list] | None = None,
    hitch_index: Mapping[str, Any] | None = None,
    *,
    root: Path | None = None,
) -> list[MutableMapping[str, Any]]:
    """Attach enrich + mom streak + hitch. Missing ``hist`` rebuilds ``mom_score_hist.json``."""
    cards_list = list(cards)
    base = Path(root) if root is not None else HERE
    if hist is None:
        hist = mom_streak.rebuild_hist_for_cards(cards_list, root=base, write=True)
        if series_by_ticker is None:
            series_by_ticker, _ = mom_streak.discover_score_series(base, hist=hist)
    if hitch_index is None:
        hitch_index = desk_hitch.load_index(root=base)
    out: list[MutableMapping[str, Any]] = []
    for card in cards_list:
        ticker = mom_streak.card_ticker(card)
        rec = dapi_enrich.lookup_name(book, ticker) if book else None
        attached = attach_enrichment(
            card,
            rec,
            cache=cache,
            hist=hist,
            series_by_ticker=series_by_ticker,
        )
        desk_hitch.attach_card(attached, hitch_index)
        out.append(attached)
    return out


def _article_html(card: Mapping[str, Any], cache: Mapping[str, Any] | None = None) -> str:
    ticker_raw = str(mom_streak.card_ticker(card) or card.get("ticker") or card.get("name") or "")
    ticker = html.escape(ticker_raw)
    pills = pills_html(card.get("enrich_pills"))
    sector = gics_filter.sector_of(card, cache) or ""
    sector_attr = html.escape(sector, quote=True)
    score = card.get("mom_score")
    if score is None:
        score, _src = mom_streak.resolve_card_score(card)
    spark = chart_marks.render_svg(card)
    mark = paper_trade.mark_of(card)
    px_attr = f' data-px="{mark}"' if mark is not None else ""
    paper = paper_trade.chrome_html(ticker_raw, mark=mark)
    return f"""
            <article class="card" data-t="{ticker}" data-ticker="{ticker}" data-gics-sector="{sector_attr}"{px_attr}>
              <header>
                <h2>{ticker}</h2>
                <div class="pills">{pills}</div>
              </header>
              {spark}
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
              {paper}
            </article>
            """


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
            "tag_triggers": rec.get("tag_triggers") or rec.get("tags"),
            "px_last": rec.get("px_last"),
            "px_series": rec.get("px_series") or rec.get("prices") or rec.get("closes"),
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

  function origLabel(btn, fallback) {
    if (!btn) return fallback;
    var saved = btn.getAttribute("data-orig-label");
    if (saved) return saved;
    var cur = (btn.textContent || fallback).trim();
    if (cur.indexOf(" · ") >= 0) cur = fallback;
    btn.setAttribute("data-orig-label", cur || fallback);
    return btn.getAttribute("data-orig-label");
  }

  function markButtons(isOptions, text) {
    var opt = document.getElementById("options-refresh");
    var ref = document.querySelector("[data-sidecar-refresh='1']");
    if (opt) {
      origLabel(opt, "Options Refresh");
      opt.textContent = isOptions ? text : origLabel(opt, "Options Refresh");
      opt.setAttribute("aria-busy", isOptions ? "true" : "false");
    }
    if (ref) {
      origLabel(ref, "Refresh");
      ref.textContent = (!isOptions) ? text : origLabel(ref, "Refresh");
      ref.setAttribute("aria-busy", (!isOptions) ? "true" : "false");
    }
  }

  function paint(st) {
    st = st || {};
    var host = hostEl();
    var bar = barEl();
    var track = trackEl();
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
    var shown = label + "  " + Math.round(pct) + "%";
    if (host) {
      host.classList.toggle("kind-options", !!isOpt);
      host.classList.add("is-on");
      host.hidden = false;
      host.removeAttribute("hidden");
      if (bar) bar.style.width = pct + "%";
      if (track) track.setAttribute("aria-valuenow", String(Math.round(pct)));
    }
    setLabel(shown);
    markButtons(isOpt, (isOpt ? "Options Refresh · " : "Refresh · ") + shown);
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
        window.setTimeout(function () { window.location.reload(); }, 2500);
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
      host.classList.add("is-on");
      host.hidden = false;
      host.removeAttribute("hidden");
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

  document.addEventListener("click", function (ev) {
    var t = ev.target && ev.target.closest ? ev.target.closest("button, [data-sidecar-refresh], [data-sidecar-options-refresh]") : ev.target;
    if (!t || !t.getAttribute) return;
    if (t.id === "options-refresh" || t.getAttribute("data-sidecar-options-refresh") === "1") {
      ev.preventDefault();
      ev.stopPropagation();
      startJob(true);
      return;
    }
    if (t.getAttribute("data-sidecar-refresh") === "1") {
      ev.preventDefault();
      ev.stopPropagation();
      startJob(false);
    }
  }, true);
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
    css = f'<style id="sidecar-refresh-css">\n{NAV_CSS}\n</style>\n'
    html_text, n_css = re.subn(
        r'<style\b[^>]*\bid=["\']sidecar-refresh-css["\'][^>]*>.*?</style>\s*',
        lambda _m: css,
        html_text,
        count=1,
        flags=re.I | re.S,
    )
    if n_css == 0:
        if "</head>" in html_text:
            html_text = html_text.replace("</head>", css + "</head>", 1)
        else:
            html_text = css + html_text
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
    script = f'<script id="sidecar-refresh-js">\n{sidecar_js()}\n</script>\n'
    html_text, n_js = re.subn(
        r'<script\b[^>]*\bid=["\']sidecar-refresh-js["\'][^>]*>.*?</script>\s*',
        lambda _m: script,
        html_text,
        count=1,
        flags=re.I | re.S,
    )
    if n_js == 0:
        if "</body>" in html_text:
            html_text = html_text.replace("</body>", script + "</body>", 1)
        else:
            html_text = html_text + "\n" + script
    return html_text


def _pack_views(
    cards: list[MutableMapping[str, Any]],
    *,
    book: Mapping[str, Any] | None,
    hist: Mapping[str, Any] | None,
    root: Path | None,
    ranked: Mapping[str, Any] | None = None,
    delta: Mapping[str, Any] | None = None,
    hitch_index: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Rank breakout/breakdown, diff the book snapshot, index hitch pills."""
    base = Path(root) if root is not None else HERE
    if hitch_index is None:
        hitch_index = desk_hitch.load_index(root=base)
        desk_hitch.attach_all(cards, hitch_index)
    if ranked is None:
        ranked = breakout.rank_book(cards, hist=hist)
    if delta is None:
        prior = book_delta.load_snapshot(root=base)
        snap = book_delta.snapshot_from_cards(
            cards, asof=str((hist or {}).get("asof") or (book or {}).get("asof") or "")
        )
        delta = book_delta.diff_snapshots(prior, snap)
    return dict(ranked), dict(delta), dict(hitch_index or {})


def render_html(
    cards: list[Mapping[str, Any]] | None = None,
    *,
    book: Mapping[str, Any] | None = None,
    cache: Mapping[str, Any] | None = None,
    hist: Mapping[str, Any] | None = None,
    series_by_ticker: Mapping[str, list] | None = None,
    title: str = "Factor Desk",
    root: Path | None = None,
    ranked: Mapping[str, Any] | None = None,
    delta: Mapping[str, Any] | None = None,
    hitch_index: Mapping[str, Any] | None = None,
) -> str:
    book = book if book is not None else load_enrichment()
    if cache is None:
        cache = dapi_enrich.load_gics_cache()
    if cards is None:
        cards = cards_from_enrichment(book)
    cards = attach_all(
        list(cards),
        book,
        cache=cache,
        hist=hist,
        series_by_ticker=series_by_ticker,
        hitch_index=hitch_index,
        root=root,
    )
    ranked, delta, hitch_index = _pack_views(
        cards, book=book, hist=hist, root=root, ranked=ranked, delta=delta, hitch_index=hitch_index
    )
    rows: list[str] = [_article_html(card, cache) for card in cards]
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
    streak_map = mom_streak.streak_db(cards, hist=hist)
    chart_map = chart_marks.chart_db(cards)
    hitch_map = desk_hitch.hitch_db(cards, hitch_index)
    paper_marks = paper_trade.marks_db(cards, book=book)
    ss_ranked = s_score.rank_book(cards, root=root, write_panel_file=True)
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
    {chart_marks.strip_css()}
    {breakout.strip_css()}
    {book_delta.strip_css()}
    {desk_hitch.strip_css()}
    {paper_trade.strip_css()}
    {s_score.strip_css()}
  </style>
</head>
<body>
  {NAV_HTML}
  <h1>{html.escape(title)}</h1>
  <p class="meta">asof {asof or "—"} · Refresh / Momentum Up / Down / Breakout / Breakdown · GICS chips + mom streak vs 5 · intraday={"on" if intra else "off"}</p>
  {gics_filter.render_strip(sectors)}
    {book_delta.host_html(delta)}
  {gics_note}
  {empty}
  {breakout.panes_html(ranked)}
  {s_score.panes_html(ss_ranked)}
  {paper_trade.panes_html()}
  <div id="home">
  <div class="grid">
    {"".join(rows)}
  </div>
  </div>
  {gics_filter.embed_db(db)}
  {mom_streak.embed_db(streak_map)}
  {chart_marks.embed_db(chart_map)}
  {breakout.embed_db(ranked)}
  {book_delta.embed_db(delta)}
  {desk_hitch.embed_db(hitch_map)}
  {paper_trade.embed_db(paper_marks)}
  {s_score.embed_db(ss_ranked)}
  <script>
  {gics_filter.strip_js()}
  </script>
  <script>
  {mom_streak.strip_js()}
  </script>
  <script>
  {chart_marks.overlay_js()}
  </script>
</body>
</html>
"""
    html_text = gics_filter.ensure_embedded(html_text, db)
    html_text = mom_streak.ensure_embedded(html_text, streak_map)
    html_text = chart_marks.ensure_embedded(html_text, chart_map)
    html_text = breakout.ensure_embedded(html_text, ranked)
    html_text = book_delta.ensure_embedded(html_text, delta)
    html_text = desk_hitch.ensure_embedded(html_text, hitch_map)
    html_text = paper_trade.ensure_embedded(html_text, paper_marks)
    html_text = s_score.ensure_embedded(html_text, ss_ranked)
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
    series, _src = mom_streak.discover_score_series(base, hist=hist)
    hitch_index = desk_hitch.load_index(root=base)
    cards = attach_all(cards, book, cache=cache, hist=hist, series_by_ticker=series, hitch_index=hitch_index, root=base)
    ranked = breakout.rank_book(cards, hist=hist)
    snap = book_delta.snapshot_from_cards(
        cards, asof=str((hist or {}).get("asof") or (book or {}).get("asof") or "")
    )
    delta = book_delta.diff_snapshots(book_delta.load_snapshot(root=base), snap)
    hitch_map = desk_hitch.hitch_db(cards, hitch_index)
    paper_marks = paper_trade.marks_db(cards, book=book)
    ss_ranked = s_score.rank_book(cards, root=base, write_panel_file=True)

    existing = ""
    if html is not None:
        existing = html
    elif dest.is_file():
        existing = dest.read_text(encoding="utf-8")

    if existing and looks_like_live_desk(existing):
        text = existing
        LOG.info("write_combined: patching live desk HTML (%s bytes)", len(existing.encode("utf-8")))
    else:
        text = render_html(
            cards,
            book=book,
            cache=cache,
            hist=hist,
            series_by_ticker=series,
            root=base,
            ranked=ranked,
            delta=delta,
            hitch_index=hitch_index,
        )

    text = _ensure_nav(text)
    text = _ensure_options_refresh_ui(text)
    mapping = gics_filter.filled_sector_db(cards, cache=cache, book=book)
    if gics_filter.GICS_SECTOR_DB_PLACEHOLDER in text:
        text = text.replace(gics_filter.GICS_SECTOR_DB_PLACEHOLDER, _gics_sector_db_json(book, cards, cache, base))
    text = gics_filter.ensure_embedded(text, mapping)
    text = mom_streak.ensure_embedded(text, mom_streak.streak_db(cards, hist=hist))
    text = chart_marks.ensure_embedded(text, chart_marks.chart_db(cards))
    text = breakout.ensure_embedded(text, ranked)
    text = book_delta.ensure_embedded(text, delta)
    text = desk_hitch.ensure_embedded(text, hitch_map)
    text = paper_trade.ensure_embedded(text, paper_marks)
    text = s_score.ensure_embedded(text, ss_ranked)
    dest.write_text(text, encoding="utf-8")
    try:
        book_delta.write_snapshot(snap, root=base)
    except OSError:
        LOG.warning("could not persist %s", book_delta.SNAPSHOT_FILENAME)
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
