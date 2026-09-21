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
import card_render  # noqa: E402
import breakout  # noqa: E402
import book_delta  # noqa: E402
import desk_hitch  # noqa: E402
import paper_trade  # noqa: E402
import s_score  # noqa: E402

LOG = logging.getLogger("desk_dash")
HTML_NAME = "factorbook.html"

# Live factorbook.html is ~2.7MB. Never replace that class of file with the
# skinny enrich-only grid — patch streak / tabs into the existing HTML.
# GICS sector-filter strip + book-delta strip are BINNED (removed, not polished).
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
    score_html = _score_html(card, score)
    spark = chart_marks.render_svg(card)
    mark = paper_trade.mark_of(card)
    px_attr = f' data-px="{mark}"' if mark is not None else ""
    return f"""
            <article class="card" data-t="{ticker}" data-ticker="{ticker}" data-gics-sector="{sector_attr}"{px_attr}>
              <header>
                <h2>{ticker}</h2>
                {score_html}
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
            </article>
            """


def _score_html(card: Mapping[str, Any], score: Any) -> str:
    """Big composite score plus the compact 10-trading-day change."""
    if score is None:
        return ""
    shown: Any = score
    try:
        f = float(score)
        if f.is_integer():
            shown = int(f)
    except (TypeError, ValueError):
        shown = score
    near = ""
    label = card.get("mom_score_d10_short") or card.get("mom_score_d10_label")
    delta = card.get("mom_score_d10")
    if label or delta is not None:
        side = "flat"
        try:
            dv = float(delta)
            side = "up" if dv > 0 else ("down" if dv < 0 else "flat")
            label = mom_streak.d10_short(dv)
        except (TypeError, ValueError):
            side = "flat"
            label = str(label or "").removeprefix("10d").strip()
        title = ""
        prior = card.get("mom_score_d10_prior")
        when = card.get("mom_score_d10_date")
        if prior is not None and when and delta is not None and score is not None:
            try:
                title = mom_streak.d10_title(float(score), float(prior), str(when), float(delta))
            except (TypeError, ValueError):
                title = ""
        title_attr = f' title="{html.escape(title, quote=True)}"' if title else ""
        delta_attr = ""
        if delta is not None:
            delta_attr = f' data-mom-score-d10="{html.escape(str(delta), quote=True)}"'
        near = (
            f'<span class="mom-score-d10-near {side}" data-key="mom-score-d10-near"{delta_attr}{title_attr}>'
            f"{html.escape(str(label))}</span>"
        )
    return f'<span class="score sc">{html.escape(str(shown))}{near}</span>'


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


# Badge strings from momentum_screen.status_badge. Direction-specific chips only.
# Range-bound / In transition / Neutral are not direction-specific — leave them out.
MOM_STATUS_UP = ("Strong momentum", "Momentum building", "Constructive")
MOM_STATUS_DOWN = ("Weak / fading", "Softening")
_STATUS_JS_ID = "fd-mom-status-js"
_STATUS_CSS_ID = "fd-mom-status-css"
_SCRIPT_TAG_RE = re.compile(r"(<script\b)([^>]*)>(.*?)</script>", re.I | re.S)


def _skip_js_string(src: str, i: int) -> int:
    """Index just past the string/template that starts at ``src[i]``."""
    q = src[i]
    i += 1
    n = len(src)
    while i < n:
        ch = src[i]
        if ch == "\\":
            i += 2
            continue
        if q == "`" and ch == "$" and i + 1 < n and src[i + 1] == "{":
            end = _match_js_bracket(src, i + 1)
            i = n if end < 0 else end + 1
            continue
        if ch == q:
            return i + 1
        i += 1
    return n


def _match_js_bracket(src: str, open_idx: int) -> int:
    """Index of the bracket matching ``src[open_idx]``, or -1."""
    pairs = {"(": ")", "{": "}", "[": "]"}
    open_ch = src[open_idx]
    close_ch = pairs.get(open_ch)
    if not close_ch:
        return -1
    depth = 0
    i = open_idx
    n = len(src)
    while i < n:
        ch = src[i]
        if ch in "\"'`":
            i = _skip_js_string(src, i)
            continue
        if ch == "/" and i + 1 < n and src[i + 1] == "/":
            nl = src.find("\n", i)
            i = n if nl < 0 else nl + 1
            continue
        if ch == "/" and i + 1 < n and src[i + 1] == "*":
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _split_js_args(src: str, open_idx: int, close_idx: int) -> list[tuple[int, int]]:
    """Top-level comma spans inside ``src[open_idx:close_idx+1]`` (brackets included)."""
    spans: list[tuple[int, int]] = []
    i = open_idx + 1
    start = i
    stack: list[str] = []
    n = close_idx
    while i < n:
        ch = src[i]
        if ch in "\"'`":
            i = _skip_js_string(src, i)
            continue
        if ch == "/" and i + 1 < len(src) and src[i + 1] == "/":
            nl = src.find("\n", i)
            i = n if nl < 0 else nl + 1
            continue
        if ch == "/" and i + 1 < len(src) and src[i + 1] == "*":
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        if ch in "([{":
            stack.append(ch)
        elif ch in ")]}" and stack:
            stack.pop()
        elif ch == "," and not stack:
            spans.append((start, i))
            start = i + 1
        i += 1
    spans.append((start, n))
    return [(a, b) for a, b in spans if src[a:b].strip()]


def _js_string_literals(src: str) -> list[tuple[int, int, str]]:
    out: list[tuple[int, int, str]] = []
    i = 0
    n = len(src)
    while i < n:
        ch = src[i]
        if ch in "\"'":
            j = _skip_js_string(src, i)
            content = src[i + 1 : j - 1].replace("\\" + ch, ch).replace("\\\\", "\\")
            out.append((i, j, content))
            i = j
            continue
        if ch == "`":
            i = _skip_js_string(src, i)
            continue
        i += 1
    return out


def _full_js_string(text: str) -> str | None:
    s = text.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        q = s[0]
        return s[1:-1].replace("\\" + q, q).replace("\\\\", "\\")
    return None


def _js_quote(text: str, quote: str = '"') -> str:
    if quote == "'":
        return "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"
    return json.dumps(text, ensure_ascii=False)


def _status_label_for(tags_label: str) -> str:
    if tags_label.isupper():
        return "STATUS"
    if tags_label[:1].isupper():
        return "Status"
    return "status"


def _has_status_key(inner: str) -> bool:
    return re.search(r"(^|[,{])\s*[\"']?status[\"']?\s*:", inner) is not None


def _is_filt_state(inner: str) -> bool:
    has_score = re.search(r"(^|[,{])\s*[\"']?score[\"']?\s*:", inner) is not None
    has_tags = re.search(r"(^|[,{])\s*[\"']?tags?[\"']?\s*:", inner) is not None
    return has_score and has_tags


def _patch_viewfilt(src: str) -> str:
    """Give mom-up / mom-down filter state ``status:"all"``. Shared assigns too."""
    if "viewFilt" not in src:
        return src
    braces: list[int] = []
    patterns = (
        re.compile(r"""(["'])mom-up\1\s*:\s*\{"""),
        re.compile(r"""(["'])mom-down\1\s*:\s*\{"""),
        re.compile(r"""viewFilt\s*\[\s*(["'])mom-up\1\s*\]\s*=\s*\{"""),
        re.compile(r"""viewFilt\s*\[\s*(["'])mom-down\1\s*\]\s*=\s*\{"""),
        re.compile(r"""viewFilt\s*\[\s*[A-Za-z_$][\w$]*\s*\]\s*=\s*\{"""),
    )
    for pat in patterns:
        for m in pat.finditer(src):
            brace = src.find("{", m.start())
            if brace >= 0:
                braces.append(brace)
    for brace in sorted(set(braces), reverse=True):
        end = _match_js_bracket(src, brace)
        if end < 0 or end - brace > 2500:
            continue
        inner = src[brace + 1 : end]
        if not _is_filt_state(inner) or _has_status_key(inner):
            continue
        src = src[: brace + 1] + 'status:"all",' + src[brace + 1 :]
    return src


def _function_bodies(src: str, name: str) -> list[tuple[int, int, str]]:
    patterns = (
        re.compile(rf"\bfunction\s+{name}\s*\(([^)]*)\)\s*\{{"),
        re.compile(rf"\b{name}\s*=\s*function\s*\(([^)]*)\)\s*\{{"),
        re.compile(rf"\b(?:var|let|const)\s+{name}\s*=\s*function\s*\(([^)]*)\)\s*\{{"),
        re.compile(rf"\b{name}\s*=\s*\(([^)]*)\)\s*=>\s*\{{"),
        re.compile(rf"\b(?:var|let|const)\s+{name}\s*=\s*\(([^)]*)\)\s*=>\s*\{{"),
    )
    found: dict[int, tuple[int, int, str]] = {}
    for pat in patterns:
        for m in pat.finditer(src):
            brace = m.end() - 1
            if brace < 0 or brace >= len(src) or src[brace] != "{":
                brace = src.find("{", m.start())
            if brace < 0:
                continue
            close = _match_js_bracket(src, brace)
            if close < 0:
                continue
            found[brace] = (brace, close, m.group(1))
    return [found[k] for k in sorted(found)]


def _patch_named_function(src: str, name: str, patcher: Any) -> str:
    bodies = _function_bodies(src, name)
    for brace, close, params in reversed(bodies):
        body = src[brace + 1 : close]
        new_body = patcher(body, params, src)
        if new_body != body:
            src = src[: brace + 1] + new_body + src[close:]
    return src


def _vf_name(body: str) -> str | None:
    """Local that holds the view's filter state (``vf.status``)."""
    m = re.search(
        r"\b(?:var|let|const)\s+([A-Za-z_$][\w$]*)\s*=\s*[^;]*\bviewFilt\b",
        body,
    )
    if m:
        return m.group(1)
    m = re.search(
        r"\b([A-Za-z_$][\w$]*)\s*=\s*[^;\n]*\bviewFilt\b",
        body,
    )
    if m and m.group(1) not in {"if", "return", "function", "typeof"}:
        return m.group(1)
    fields_by_name: dict[str, set[str]] = {}
    for name, field in re.findall(
        r"\b([A-Za-z_$][\w$]*)\.(score|tags|flows|sort)\b",
        body,
    ):
        fields_by_name.setdefault(name, set()).add(field)
    callback_names = set(
        re.findall(
            r"\.filter\s*\(\s*(?:function\s*\(\s*)?\(?\s*([A-Za-z_$][\w$]*)",
            body,
        )
    )
    ranked = sorted(
        (
            (name, fields)
            for name, fields in fields_by_name.items()
            if name not in callback_names and len(fields) >= 2
        ),
        key=lambda item: len(item[1]),
        reverse=True,
    )
    if ranked:
        return ranked[0][0]
    return None


def _filter_callbacks(body: str) -> list[tuple[int, str, int, int]]:
    """(insert_at, card_param, brace, close) for ``.filter`` callbacks with a block body."""
    patterns = (
        re.compile(r"\.filter\s*\(\s*function\s*\(\s*([A-Za-z_$][\w$]*)\b[^)]*\)\s*\{"),
        re.compile(r"\.filter\s*\(\s*\(\s*([A-Za-z_$][\w$]*)\b[^)]*\)\s*=>\s*\{"),
        re.compile(r"\.filter\s*\(\s*([A-Za-z_$][\w$]*)\s*=>\s*\{"),
    )
    found: dict[int, tuple[int, str, int, int]] = {}
    for pat in patterns:
        for m in pat.finditer(body):
            brace = m.end() - 1
            if brace < 0 or body[brace] != "{":
                continue
            close = _match_js_bracket(body, brace)
            if close < 0:
                continue
            found[brace] = (m.end(), m.group(1), brace, close)
    return [found[k] for k in sorted(found)]


def _patch_apply_body(body: str, _params: str, _script: str) -> str:
    if "fd-status-pred" in body:
        return body
    vf = _vf_name(body)
    if not vf:
        return body
    callbacks = _filter_callbacks(body)
    if not callbacks:
        return body

    def rank(item: tuple[int, str, int, int]) -> int:
        _ins, _card, brace, close = item
        chunk = body[brace:close]
        score = 0
        for key in (vf, "score", "tags", "flows", "pills", "outlier", "sort"):
            if key and key in chunk:
                score += 1
        return score

    chosen = max(callbacks, key=rank)
    if rank(chosen) <= 0 and len(callbacks) != 1:
        return body
    insert_at, card, _brace, _close = chosen
    pred = (
        " /* fd-status-pred */ if ("
        + vf
        + " && "
        + vf
        + '.status && '
        + vf
        + '.status !== "all" && '
        + card
        + ".status !== "
        + vf
        + ".status) return false;"
    )
    return body[:insert_at] + pred + body[insert_at:]


def _lookup_js_array(script: str, name: str) -> str | None:
    m = re.search(rf"\b(?:var|let|const)\s+{re.escape(name)}\s*=\s*\[", script)
    if not m:
        m = re.search(rf"\b{re.escape(name)}\s*=\s*\[", script)
    if not m:
        return None
    idx = script.find("[", m.start())
    end = _match_js_bracket(script, idx)
    if end < 0:
        return None
    return script[idx : end + 1]


def _arg_role(raw: str, script: str) -> str:
    s = raw.strip()
    lit = _full_js_string(s)
    if lit in ("TAGS", "Tags"):
        return "label"
    if lit == "tags":
        return "key"
    if re.search(r"\.tags\b", s) or re.search(r"\[\s*['\"]tags['\"]\s*\]", s):
        return "state"
    if "OUTLIER" in s and ("NEW" in s or "All" in s or "all" in s):
        return "chips"
    if re.fullmatch(r"[A-Za-z_$][\w$]*", s):
        arr = _lookup_js_array(script, s)
        if arr and "OUTLIER" in arr:
            return "chips"
    return ""


def _replace_js_lit(src: str, start: int, end: int, content: str) -> str:
    quote = src[start] if src[start] in "\"'" else '"'
    return src[:start] + _js_quote(content, quote) + src[end:]


def _normalize_all_chip(el: str) -> str:
    lits = _js_string_literals(el)
    if any(content == "all" for _a, _b, content in lits):
        return el.strip()
    for start, end, content in lits:
        if content.lower() == "all":
            return _replace_js_lit(el, start, end, "all").strip()
    return el.strip()


def _fill_badge_chip(el: str, value: str, label: str) -> str | None:
    lits = _js_string_literals(el)
    if not lits:
        return None
    if len(lits) >= 2:
        el = _replace_js_lit(el, lits[1][0], lits[1][1], label)
        lits = _js_string_literals(el)
        el = _replace_js_lit(el, lits[0][0], lits[0][1], value)
        return el.strip()
    return _replace_js_lit(el, lits[0][0], lits[0][1], value).strip()


def _array_from_template(arr: str, badges: tuple[str, ...] | list[str]) -> str | None:
    if not arr.startswith("["):
        return None
    close = _match_js_bracket(arr, 0)
    if close < 0:
        return None
    elements = [arr[a:b].strip() for a, b in _split_js_args(arr, 0, close)]
    all_el = None
    badge_el = None
    for el in elements:
        contents = [content for _a, _b, content in _js_string_literals(el)]
        if any(content.lower() == "all" for content in contents):
            all_el = el
        if any(content.upper() == "OUTLIER" for content in contents):
            badge_el = el
    if all_el is None or badge_el is None:
        return None
    parts = [_normalize_all_chip(all_el)]
    for badge in badges:
        filled = _fill_badge_chip(badge_el, badge, badge)
        if not filled:
            return None
        parts.append(filled)
    return "[" + ",".join(parts) + "]"


def _chips_expr(raw: str, script: str, view: str) -> str | None:
    s = raw.strip()
    if re.fullmatch(r"[A-Za-z_$][\w$]*", s):
        found = _lookup_js_array(script, s)
        if not found:
            return None
        s = found
    brace = s.find("[")
    if brace < 0:
        return None
    s = s[brace:]
    up = _array_from_template(s, MOM_STATUS_UP)
    down = _array_from_template(s, MOM_STATUS_DOWN)
    if not up or not down:
        return None
    return f'({view}==="mom-down"?{down}:{up})'


def _find_label_call(src: str, labels: tuple[str, ...]) -> tuple[int, int, str, list[tuple[int, int]]] | None:
    """First call whose string arg equals ``labels``. End index is past ``)``."""
    want = set(labels)
    i = 0
    n = len(src)
    while i < n:
        m = re.search(r"\b([A-Za-z_$][\w$]*)\s*\(", src[i:])
        if not m:
            return None
        name_start = i + m.start(1)
        paren = i + m.end() - 1
        close = _match_js_bracket(src, paren)
        if close < 0:
            i = paren + 1
            continue
        args = _split_js_args(src, paren, close)
        if any(_full_js_string(src[a:b]) in want for a, b in args):
            return (name_start, close + 1, m.group(1), args)
        i = paren + 1
    return None


def _status_call(src: str, call: tuple[int, int, str, list[tuple[int, int]]], view: str, script: str) -> str | None:
    _start, _end, name, args = call
    new_args: list[str] = []
    replaced = False
    for a, b in args:
        raw = src[a:b]
        role = _arg_role(raw, script)
        if role == "label":
            lit = _full_js_string(raw.strip()) or "TAGS"
            quote = raw.strip()[0]
            new_args.append(_js_quote(_status_label_for(lit), quote))
        elif role == "key":
            quote = raw.strip()[0]
            new_args.append(_js_quote("status", quote))
        elif role == "state":
            updated = re.sub(r"\.tags\b", ".status", raw)
            updated = re.sub(
                r"\[\s*(['\"])tags\1\s*\]",
                lambda m: "[" + m.group(1) + "status" + m.group(1) + "]",
                updated,
            )
            new_args.append(updated)
        elif role == "chips":
            expr = _chips_expr(raw, script, view)
            if not expr:
                return None
            lead = re.match(r"\s*", raw).group(0)  # type: ignore[union-attr]
            trail = re.search(r"\s*$", raw).group(0)  # type: ignore[union-attr]
            new_args.append(lead + expr + trail)
            replaced = True
        else:
            new_args.append(raw)
    if not replaced:
        return None
    return name + "(" + ",".join(new_args) + ")"


def _split_js_statements(src: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    n = len(src)
    i = 0
    start = 0
    stack: list[str] = []
    while i < n:
        ch = src[i]
        if ch in "\"'`":
            i = _skip_js_string(src, i)
            continue
        if ch == "/" and i + 1 < n and src[i + 1] == "/":
            nl = src.find("\n", i)
            i = n if nl < 0 else nl + 1
            continue
        if ch == "/" and i + 1 < n and src[i + 1] == "*":
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        if ch in "([{":
            stack.append(ch)
        elif ch in ")]}" and stack:
            stack.pop()
        elif ch == ";" and not stack:
            spans.append((start, i + 1))
            start = i + 1
        i += 1
    if start < n and src[start:].strip():
        spans.append((start, n))
    return spans


def _span_containing(spans: list[tuple[int, int]], idx: int) -> tuple[int, int] | None:
    for span in spans:
        if span[0] <= idx < span[1]:
            return span
    return None


def _view_param(params: str, body: str) -> str:
    m = re.search(r"\b([A-Za-z_$][\w$]*)\s*===?\s*['\"]mom-up['\"]", body)
    if m:
        return m.group(1)
    first = params.split(",")[0].split("=")[0].strip()
    if re.fullmatch(r"[A-Za-z_$][\w$]*", first):
        return first
    return "viewKey"


def _patch_build_body(body: str, params: str, script: str) -> str:
    if "Strong momentum" in body and "Weak / fading" in body:
        return body
    tags = _find_label_call(body, ("TAGS", "Tags"))
    sort = _find_label_call(body, ("SORT", "Sort"))
    if not tags or not sort or not (tags[1] <= sort[0]):
        return body
    view = _view_param(params, body)
    new_call = _status_call(body, tags, view, script)
    if not new_call:
        return body
    cond = f'({view}==="mom-up"||{view}==="mom-down")'
    spans = _split_js_statements(body)
    tags_stmt = _span_containing(spans, tags[0])
    sort_stmt = _span_containing(spans, sort[0])
    if tags_stmt and sort_stmt and tags_stmt == sort_stmt:
        gap = body[tags[1] : sort[0]]
        expr = "(" + cond + "?" + new_call + ':"")'
        if re.match(r"\s*,", gap):
            return body[: tags[1]] + ", " + expr + body[tags[1] :]
        if re.match(r"\s*\+", gap):
            return body[: tags[1]] + " + " + expr + body[tags[1] :]
        return body
    if not tags_stmt:
        return body
    start, end = tags_stmt
    rel = tags[0] - start
    cloned = body[start:end][:rel] + new_call + body[start:end][rel + (tags[1] - tags[0]) :]
    cloned = cloned.strip()
    if not cloned.endswith(";"):
        cloned += ";"
    inject = " if (" + cond + ") { " + cloned + " }"
    return body[:end] + inject + body[end:]


def _has_change_key(inner: str) -> bool:
    return re.search(r"(^|[,{])\s*[\"']?change[\"']?\s*:", inner) is not None


def _patch_viewfilt_change(src: str) -> str:
    """Give mom-up / mom-down filter state ``change:"all"``. Shared assigns too."""
    if "viewFilt" not in src:
        return src
    braces: list[int] = []
    patterns = (
        re.compile(r"""(["'])mom-up\1\s*:\s*\{"""),
        re.compile(r"""(["'])mom-down\1\s*:\s*\{"""),
        re.compile(r"""viewFilt\s*\[\s*(["'])mom-up\1\s*\]\s*=\s*\{"""),
        re.compile(r"""viewFilt\s*\[\s*(["'])mom-down\1\s*\]\s*=\s*\{"""),
        re.compile(r"""viewFilt\s*\[\s*[A-Za-z_$][\w$]*\s*\]\s*=\s*\{"""),
    )
    for pat in patterns:
        for m in pat.finditer(src):
            brace = src.find("{", m.start())
            if brace >= 0:
                braces.append(brace)
    for brace in sorted(set(braces), reverse=True):
        end = _match_js_bracket(src, brace)
        if end < 0 or end - brace > 2500:
            continue
        inner = src[brace + 1 : end]
        if not _is_filt_state(inner) or _has_change_key(inner):
            continue
        src = src[:end] + ',change:"all"' + src[end:]
    return src


_CHANGE_LABELS = ("CHANGE", "Change")
_CHANGE_CHIPS = (
    '[["all","All"],["gt3","+>3"],["le3","+\u22643"],["flat","Flat"],'
    '["nle3","\u2212\u22643"],["ngt3","\u2212>3"]]'
)
_CHANGE_CLICK_MARK = "/* fd-change-click */"


def _change_pred(vf: str, card: str) -> str:
    """Fail closed unless ``mom_score_d10`` is a finite number in the selected bucket."""
    return (
        " /* fd-change-pred */ var __fdD10 = Number("
        + card
        + ".mom_score_d10); var __fdD10ok = "
        + card
        + ".mom_score_d10 != null && "
        + card
        + '.mom_score_d10 !== "" && isFinite(__fdD10); var __fdCh = ('
        + vf
        + " && "
        + vf
        + '.change) || "all"; if (__fdCh === "+>3") __fdCh = "gt3"; else if (__fdCh === "+\u22643") __fdCh = "le3"; else if (__fdCh === "Flat") __fdCh = "flat"; else if (__fdCh === "\u2212\u22643" || __fdCh === "-\u22643") __fdCh = "nle3"; else if (__fdCh === "\u2212>3" || __fdCh === "->3") __fdCh = "ngt3"; if ('
        + vf
        + ' && __fdCh !== "all" && !(__fdD10ok && ((__fdCh === "gt3" && __fdD10 > 3) || (__fdCh === "le3" && __fdD10 > 0 && __fdD10 <= 3) || (__fdCh === "flat" && __fdD10 === 0) || (__fdCh === "nle3" && __fdD10 < 0 && __fdD10 >= -3) || (__fdCh === "ngt3" && __fdD10 < -3)))) return false;'
    )


def _strip_stale_change_pred(body: str) -> str:
    """Drop an up/down/flat change predicate so the six buckets own the filter."""
    marker = "/* fd-change-pred */"
    while marker in body:
        idx = body.find(marker)
        end = body.find("return false;", idx)
        if end < 0:
            break
        end += len("return false;")
        block = body[idx:end]
        if "+>3" in block and "gt3" in block and "nle3" in block and "ngt3" in block:
            break
        body = body[:idx] + body[end:]
    body = re.sub(
        r"""if\s*\(\s*[A-Za-z_$][\w$]*\.change\s*===?\s*['"](?:up|down|flat)['"][\s\S]*?\)\s*return\s+false\s*;""",
        "",
        body,
    )
    return body


def _patch_change_apply_body(body: str, _params: str, _script: str) -> str:
    body = _strip_stale_change_pred(body)
    if "fd-change-pred" in body and "+>3" in body and "gt3" in body:
        return body
    vf = _vf_name(body)
    if not vf:
        return body
    callbacks = _filter_callbacks(body)
    if not callbacks:
        return body

    def rank(item: tuple[int, str, int, int]) -> int:
        _ins, _card, brace, close = item
        chunk = body[brace:close]
        score = 0
        for key in (vf, "score", "tags", "flows", "pills", "outlier", "sort", "status"):
            if key and key in chunk:
                score += 1
        return score

    chosen = max(callbacks, key=rank)
    if rank(chosen) <= 0 and len(callbacks) != 1:
        return body
    insert_at, card, _brace, _close = chosen
    return body[:insert_at] + _change_pred(vf, card) + body[insert_at:]


def _find_all_label_calls(src: str, labels: tuple[str, ...]) -> list[tuple[int, int, str, list[tuple[int, int]]]]:
    """Every call whose string arg equals ``labels``. End index is past ``)``."""
    want = set(labels)
    found: list[tuple[int, int, str, list[tuple[int, int]]]] = []
    i = 0
    n = len(src)
    while i < n:
        m = re.search(r"\b([A-Za-z_$][\w$]*)\s*\(", src[i:])
        if not m:
            break
        name_start = i + m.start(1)
        paren = i + m.end() - 1
        close = _match_js_bracket(src, paren)
        if close < 0:
            i = paren + 1
            continue
        args = _split_js_args(src, paren, close)
        if any(_full_js_string(src[a:b]) in want for a, b in args):
            found.append((name_start, close + 1, m.group(1), args))
            i = close + 1
            continue
        i = paren + 1
    return found


def _change_call_kind(src: str, call: tuple[int, int, str, list[tuple[int, int]]]) -> str:
    chunk = src[call[0] : call[1]]
    if any(tok in chunk for tok in ("gt3", "+>3", "nle3", "ngt3", "fd-change-row")):
        return "granular"
    return "legacy"


def _skip_ws_left(src: str, i: int) -> int:
    while i >= 0 and src[i] in " \t\r\n":
        i -= 1
    return i


def _skip_ws_right(src: str, i: int) -> int:
    n = len(src)
    while i < n and src[i] in " \t\r\n":
        i += 1
    return i


def _stmt_bounds_around(src: str, start: int, end: int) -> tuple[int, int]:
    """Innermost statement containing ``src[start:end]`` (previous brace/semicolon through next semicolon)."""
    n = len(src)
    depth = 0
    i = 0
    breaks: dict[int, int] = {0: 0}
    while i < start:
        ch = src[i]
        if ch in "\"'`":
            i = _skip_js_string(src, i)
            continue
        if ch == "/" and i + 1 < n and src[i + 1] == "/":
            nl = src.find("\n", i)
            i = n if nl < 0 else nl + 1
            continue
        if ch == "/" and i + 1 < n and src[i + 1] == "*":
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        if ch in "([{":
            depth += 1
            breaks[depth] = i + 1
        elif ch in ")]}":
            breaks.pop(depth, None)
            depth = max(0, depth - 1)
        elif ch == ";":
            breaks[depth] = i + 1
        i += 1
    stmt_start = breaks.get(depth, 0)
    depth2 = depth
    i = end
    while i < n:
        ch = src[i]
        if ch in "\"'`":
            i = _skip_js_string(src, i)
            continue
        if ch == "/" and i + 1 < n and src[i + 1] == "/":
            nl = src.find("\n", i)
            i = n if nl < 0 else nl + 1
            continue
        if ch == "/" and i + 1 < n and src[i + 1] == "*":
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        if ch in "([{":
            depth2 += 1
        elif ch in ")]}":
            depth2 = max(0, depth2 - 1)
        elif ch == ";" and depth2 == depth:
            return stmt_start, i + 1
        i += 1
    return stmt_start, n


def _remove_call_expr(src: str, start: int, end: int) -> str:
    """Drop a call and the ``+`` / comma that joins it. A lone statement goes with it."""
    left = _skip_ws_left(src, start - 1)
    right = _skip_ws_right(src, end)
    if left >= 0 and src[left] == "+" and not (left >= 1 and src[left - 1] in "=+"):
        return src[:left] + src[end:]
    if left >= 0 and src[left] == ",":
        return src[:left] + src[end:]
    if right < len(src) and src[right] == "+" and not (right + 1 < len(src) and src[right + 1] == "+"):
        cut = right + 1
        if cut < len(src) and src[cut] == "=":
            pass
        else:
            return src[:start] + src[cut:]
    if right < len(src) and src[right] == ",":
        return src[:start] + src[right + 1 :]
    stmt_start, stmt_end = _stmt_bounds_around(src, start, end)
    return src[:stmt_start] + src[stmt_end:]


def _rewrite_change_chips(src: str, call: tuple[int, int, str, list[tuple[int, int]]]) -> str | None:
    """Replace the chips argument of a Change call with the six granular buckets."""
    start, end, name, args = call
    new_args: list[str] = []
    replaced = False
    for a, b in args:
        raw = src[a:b]
        stripped = raw.strip()
        if _full_js_string(stripped) is not None:
            new_args.append(raw)
            continue
        if not replaced and (
            stripped.startswith("[") or re.fullmatch(r"[A-Za-z_$][\w$]*", stripped)
        ):
            lead = re.match(r"\s*", raw).group(0)  # type: ignore[union-attr]
            trail = re.search(r"\s*$", raw).group(0)  # type: ignore[union-attr]
            new_args.append(lead + _CHANGE_CHIPS + trail)
            replaced = True
            continue
        new_args.append(raw)
    if not replaced:
        return None
    return src[:start] + name + "(" + ",".join(new_args) + ")" + src[end:]


def _enclosing_brace(src: str, start: int) -> int | None:
    """Index of the ``{`` that contains ``start``, scanning from the beginning of ``src``."""
    n = len(src)
    stack: list[int] = []
    i = 0
    while i < start and i < n:
        ch = src[i]
        if ch in "\"'`":
            i = _skip_js_string(src, i)
            continue
        if ch == "/" and i + 1 < n and src[i + 1] == "/":
            nl = src.find("\n", i)
            i = n if nl < 0 else nl + 1
            continue
        if ch == "/" and i + 1 < n and src[i + 1] == "*":
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        if ch == "{":
            stack.append(i)
        elif ch == "}" and stack:
            stack.pop()
        i += 1
    return stack[-1] if stack else None


def _call_is_mom_gated(body: str, start: int) -> bool:
    brace = _enclosing_brace(body, start)
    if brace is None:
        return False
    head = body[max(0, brace - 240) : brace]
    return "mom-up" in head and "mom-down" in head


def _strip_up_plus_statements(body: str) -> str:
    spans = _split_js_statements(body)
    for start, end in reversed(spans):
        if "Up (+)" in body[start:end]:
            body = body[:start] + body[end:]
    return body


def _inject_granular_change(body: str, params: str) -> str:
    if _find_label_call(body, _CHANGE_LABELS):
        return body
    score = _find_label_call(body, ("SCORE", "Score"))
    if not score:
        return body
    view = _view_param(params, body)
    vf = _vf_name(body) or "vf"
    name = score[2]
    new_call = f'{name}("CHANGE",{_CHANGE_CHIPS},"change",{vf}.change)'
    cond = f'({view}==="mom-up"||{view}==="mom-down")'
    spans = _split_js_statements(body)
    score_stmt = _span_containing(spans, score[0])
    if not score_stmt:
        return body
    after = body[score[1] : score_stmt[1]]
    if re.match(r"\s*,", after):
        expr = "(" + cond + "?" + new_call + ':"")'
        return body[: score[1]] + ", " + expr + body[score[1] :]
    if re.match(r"\s*\+", after):
        expr = "(" + cond + "?" + new_call + ':"")'
        return body[: score[1]] + " + " + expr + body[score[1] :]
    start, end = score_stmt
    rel = score[0] - start
    cloned = body[start:end][:rel] + new_call + body[start:end][rel + (score[1] - score[0]) :]
    cloned = cloned.strip()
    if not cloned.endswith(";"):
        cloned += ";"
    inject = " if (" + cond + ") { " + cloned + " }"
    return body[:end] + inject + body[end:]


def _patch_change_build_body(body: str, params: str, _script: str) -> str:
    """One Change row: All / +>3 / +≤3 / Flat / −≤3 / −>3. Legacy Up/Down is removed."""
    calls = _find_all_label_calls(body, _CHANGE_LABELS)
    granular = [c for c in calls if _change_call_kind(body, c) == "granular"]
    legacy = [c for c in calls if _change_call_kind(body, c) != "granular"]
    if granular:
        for call in reversed(legacy):
            body = _remove_call_expr(body, call[0], call[1])
    elif legacy:
        keep = legacy[0]
        for call in reversed(legacy[1:]):
            body = _remove_call_expr(body, call[0], call[1])
        if _call_is_mom_gated(body, keep[0]):
            rewritten = _rewrite_change_chips(body, keep)
            body = rewritten if rewritten is not None else _remove_call_expr(body, keep[0], keep[1])
        else:
            body = _remove_call_expr(body, keep[0], keep[1])
    body = _strip_up_plus_statements(body)
    return _inject_granular_change(body, params)


def _change_click_js() -> str:
    return r"""
/* fd-change-click */
if (typeof document !== "undefined") document.addEventListener("click", function (ev) {
  if (ev.__fdChangeClick) return;
  var t = ev.target;
  var b = t && t.closest ? t.closest("[data-k='change']") : null;
  if (!b) return;
  var view = "";
  var host = b.closest ? b.closest("#view-mom-up, #view-mom-down") : null;
  if (host && host.id === "view-mom-down") view = "mom-down";
  else if (host && host.id === "view-mom-up") view = "mom-up";
  var upEl = document.getElementById("view-mom-up");
  var downEl = document.getElementById("view-mom-down");
  function shown(el) {
    if (!el) return false;
    if (el.classList && (el.classList.contains("hide") || el.classList.contains("hidden"))) return false;
    if (el.hasAttribute && el.hasAttribute("hidden")) return false;
    return true;
  }
  if (view !== "mom-up" && view !== "mom-down") {
    if (shown(upEl) && !shown(downEl)) view = "mom-up";
    else if (shown(downEl) && !shown(upEl)) view = "mom-down";
    else if (typeof curView === "string" && (curView === "mom-up" || curView === "mom-down")) view = curView;
  }
  if (view !== "mom-up" && view !== "mom-down") return;
  ev.__fdChangeClick = 1;
  var val = b.getAttribute("data-v") || "all";
  if (val === "+>3") val = "gt3";
  else if (val === "+\u22643") val = "le3";
  else if (val === "Flat") val = "flat";
  else if (val === "\u2212\u22643" || val === "-\u22643") val = "nle3";
  else if (val === "\u2212>3" || val === "->3") val = "ngt3";
  if (typeof viewFilt !== "undefined" && viewFilt && viewFilt[view]) viewFilt[view].change = val;
  if (typeof paintView === "function") paintView(view);
}, true);
""".strip()


def _patch_mom_filt_script(src: str) -> str:
    src = _patch_viewfilt(src)
    src = _patch_viewfilt_change(src)
    src = _patch_named_function(src, "applyMomFilters", _patch_apply_body)
    src = _patch_named_function(src, "applyMomFilters", _patch_change_apply_body)
    src = _patch_named_function(src, "buildFiltBar", _patch_build_body)
    src = _patch_named_function(src, "buildFiltBar", _patch_change_build_body)
    if _CHANGE_CLICK_MARK not in src and "viewFilt" in src and "paintView" in src:
        src = src.rstrip() + "\n" + _change_click_js() + "\n"
    return src


def _patch_status_scripts(html_text: str) -> str:
    def repl(match: re.Match[str]) -> str:
        attrs = match.group(2)
        body = match.group(3)
        if re.search(r"\bid\s*=\s*['\"]fd-mom-status-js['\"]", attrs, re.I):
            return match.group(0)
        if re.search(r"\btype\s*=\s*['\"]application/(?:ld\+)?json['\"]", attrs, re.I):
            return match.group(0)
        if "viewFilt" not in body and "applyMomFilters" not in body and "buildFiltBar" not in body:
            return match.group(0)
        try:
            patched = _patch_mom_filt_script(body)
        except Exception:
            LOG.warning("mom status filter patch skipped", exc_info=True)
            return match.group(0)
        return match.group(1) + attrs + ">" + patched + "</script>"

    return _SCRIPT_TAG_RE.sub(repl, html_text)


def _fallback_js() -> str:
    up = json.dumps(list(MOM_STATUS_UP), ensure_ascii=False)
    down = json.dumps(list(MOM_STATUS_DOWN), ensure_ascii=False)
    return r"""
(function () {
  if (window.__FD_MOM_STATUS_BOUND__) return;
  window.__FD_MOM_STATUS_BOUND__ = 1;
  var UP = __UP__;
  var DOWN = __DOWN__;
  var sel = {};
  sel["mom-up"] = "all";
  sel["mom-down"] = "all";
  var selChange = {};
  selChange["mom-up"] = "all";
  selChange["mom-down"] = "all";

  function otherScripts() {
    var t = "";
    var nodes = document.getElementsByTagName("script");
    for (var i = 0; i < nodes.length; i++) {
      if (nodes[i].id === "fd-mom-status-js") continue;
      t += nodes[i].textContent || "";
    }
    return t;
  }
  function sourcePatched() {
    var t = otherScripts();
    if (t.indexOf("buildFiltBar") < 0) return false;
    if (t.indexOf("Strong momentum") < 0 || t.indexOf("Momentum building") < 0) return false;
    if (t.indexOf("Constructive") < 0 || t.indexOf("Weak / fading") < 0) return false;
    if (t.indexOf("Softening") < 0 || t.indexOf("fd-status-pred") < 0) return false;
    if (t.indexOf("+>3") < 0 || t.indexOf("fd-change-pred") < 0) return false;
    return true;
  }
  function shown(el) {
    if (!el) return false;
    if (el.classList && (el.classList.contains("hide") || el.classList.contains("hidden"))) return false;
    if (el.hasAttribute && el.hasAttribute("hidden")) return false;
    try {
      var st = window.getComputedStyle(el);
      if (st && (st.display === "none" || st.visibility === "hidden")) return false;
    } catch (e0) {}
    return true;
  }
  function viewOf() {
    var up = document.getElementById("view-mom-up");
    var down = document.getElementById("view-mom-down");
    var u = shown(up), d = shown(down);
    if (u && !d) return "mom-up";
    if (d && !u) return "mom-down";
    var marked = document.querySelectorAll("[data-view]");
    for (var i = 0; i < marked.length; i++) {
      var v = marked[i].getAttribute("data-view") || "";
      if ((v === "mom-up" || v === "mom-down") && shown(marked[i]) && marked[i].tagName !== "BUTTON") return v;
    }
    var heads = document.querySelectorAll("h1, h2, h3, .title, .view-title");
    for (var h = 0; h < heads.length; h++) {
      if (!shown(heads[h])) continue;
      var tx = (heads[h].textContent || "").replace(/\s+/g, " ").trim().toUpperCase();
      if (tx === "MOMENTUM UP") return "mom-up";
      if (tx === "MOMENTUM DOWN") return "mom-down";
    }
    return "";
  }
  function smallest(root, pred) {
    var nodes = root.querySelectorAll("div, section, nav, header, ul, ol");
    var best = null;
    var bestLen = 1e15;
    for (var i = 0; i < nodes.length; i++) {
      if (!pred(nodes[i])) continue;
      var len = (nodes[i].textContent || "").length;
      if (len < bestLen) { best = nodes[i]; bestLen = len; }
    }
    return best;
  }
  function isBar(el) {
    if (el.closest && el.closest("article, .card")) return false;
    if (el.getAttribute && (el.getAttribute("data-fd-status-row") || el.getAttribute("data-fd-change-row"))) return false;
    var t = el.textContent || "";
    if (t.indexOf("OUTLIER") < 0 || t.indexOf("NEW") < 0) return false;
    if (t.indexOf("5+") < 0 && t.indexOf("6+") < 0 && t.indexOf("7+") < 0) return false;
    if (t.indexOf("SCORE") < 0 && t.indexOf("Score") < 0 && t.indexOf("FLOWS") < 0 && t.indexOf("TAGS") < 0 && t.indexOf("Tags") < 0) return false;
    return true;
  }
  function isTags(el) {
    if (el.getAttribute && (el.getAttribute("data-fd-status-row") || el.getAttribute("data-fd-change-row"))) return false;
    var t = el.textContent || "";
    if (t.indexOf("OUTLIER") < 0 || t.indexOf("NEW") < 0) return false;
    if (t.indexOf("5+") >= 0 || t.indexOf("6+") >= 0) return false;
    if (t.indexOf("Big flow") >= 0 || t.indexOf("OPT SPIKE") >= 0) return false;
    return true;
  }
  function barHasForeignStatus(bar) {
    if (bar.querySelector("[data-fd-status-row]")) return false;
    var nodes = bar.querySelectorAll("span, label, b, h3, h4, p");
    for (var i = 0; i < nodes.length; i++) {
      if (nodes[i].children.length) continue;
      var tx = (nodes[i].textContent || "").replace(/\s+/g, " ").trim();
      if (tx === "STATUS" || tx === "Status") return true;
    }
    return false;
  }
  function isScoreGroup(el) {
    if (!el || (el.getAttribute && (el.getAttribute("data-fd-status-row") || el.getAttribute("data-fd-change-row")))) return false;
    var t = el.textContent || "";
    if (t.indexOf("5+") < 0 && t.indexOf("6+") < 0) return false;
    if (t.indexOf("OUTLIER") >= 0 || t.indexOf("NEW") >= 0 || t.indexOf("+>3") >= 0) return false;
    return true;
  }
  function isSort(el) {
    if (el.getAttribute && (el.getAttribute("data-fd-status-row") || el.getAttribute("data-fd-change-row"))) return false;
    var t = el.textContent || "";
    if (t.indexOf("NEW") >= 0 || t.indexOf("OUTLIER") >= 0) return false;
    var hasName = t.indexOf("Name") >= 0;
    var hasRS = t.indexOf("RS") >= 0 || t.indexOf("Opt") >= 0;
    return hasName && hasRS;
  }
  function chipEls(group) {
    var nodes = group.querySelectorAll("button, a, [class*='chip'], [class*='Chip']");
    var out = [];
    for (var i = 0; i < nodes.length; i++) {
      var tx = (nodes[i].textContent || "").replace(/\s+/g, " ").trim();
      if (!tx || tx.length > 40) continue;
      if (nodes[i].querySelector("button, a")) continue;
      out.push(nodes[i]);
    }
    return out;
  }
  function relabel(group) {
    var nodes = group.querySelectorAll("*");
    for (var i = 0; i < nodes.length; i++) {
      if (nodes[i].children.length) continue;
      var tx = (nodes[i].textContent || "").replace(/\s+/g, " ").trim();
      if (tx === "TAGS") { nodes[i].textContent = "STATUS"; return; }
      if (tx === "Tags") { nodes[i].textContent = "Status"; return; }
    }
  }
  function cardStatus(el) {
    var s = el.querySelector(".status, .badge-status, [data-status]");
    if (s) {
      var attr = s.getAttribute && s.getAttribute("data-status");
      if (attr) return String(attr).replace(/\s+/g, " ").trim();
      return (s.textContent || "").replace(/\s+/g, " ").trim();
    }
    var own = el.getAttribute && el.getAttribute("data-status");
    return own ? String(own).replace(/\s+/g, " ").trim() : "";
  }
  function cardsIn(view) {
    var root = document.getElementById(view === "mom-down" ? "view-mom-down" : "view-mom-up") || document.body;
    var nodes = root.querySelectorAll("article, .card");
    var out = [];
    for (var i = 0; i < nodes.length; i++) {
      if (nodes[i].closest && nodes[i].closest("[data-fd-status-row]")) continue;
      if (nodes[i].querySelector("article, .card")) continue;
      out.push(nodes[i]);
    }
    return out;
  }
  function clearHide() {
    var nodes = document.querySelectorAll(".fd-status-hid, .fd-change-hid");
    for (var i = 0; i < nodes.length; i++) {
      nodes[i].classList.remove("fd-status-hid");
      nodes[i].classList.remove("fd-change-hid");
    }
  }
  function cardDelta(el) {
    var n = el.querySelector("[data-mom-score-d10]");
    if (n) {
      var raw = n.getAttribute("data-mom-score-d10");
      if (raw != null && raw !== "") {
        var v = Number(raw);
        if (isFinite(v)) return v;
      }
    }
    var cap = el.querySelector(".mom-score-d10-near, .score-d10, [data-key='mom-score-d10-near']");
    if (!cap) return null;
    var t = (cap.textContent || "").replace(/\s+/g, "").replace(/\u2212/g, "-");
    if (!t) return null;
    var parsed = Number(t);
    return isFinite(parsed) ? parsed : null;
  }
  function normChange(v) {
    if (v === "+>3") return "gt3";
    if (v === "+\u22643") return "le3";
    if (v === "Flat" || v === "flat") return "flat";
    if (v === "\u2212\u22643" || v === "-\u22643") return "nle3";
    if (v === "\u2212>3" || v === "->3") return "ngt3";
    if (v === "All" || v === "ALL") return "all";
    return v || "all";
  }
  function changeMiss(delta, want) {
    want = normChange(want);
    if (!want || want === "all") return false;
    if (delta == null || !isFinite(delta)) return true;
    if (want === "gt3") return !(delta > 3);
    if (want === "le3") return !(delta > 0 && delta <= 3);
    if (want === "flat") return delta !== 0;
    if (want === "nle3") return !(delta < 0 && delta >= -3);
    if (want === "ngt3") return !(delta < -3);
    return true;
  }
  function changeOwnedByApply() {
    var t = otherScripts();
    return t.indexOf("fd-change-pred") >= 0 && t.indexOf("mom_score_d10") >= 0;
  }
  function applyHide(view) {
    if (view !== "mom-up" && view !== "mom-down") { clearHide(); return; }
    var want = sel[view] || "all";
    var ch = selChange[view] || "all";
    var cards = cardsIn(view);
    var skipChange = changeOwnedByApply();
    for (var i = 0; i < cards.length; i++) {
      var st = cardStatus(cards[i]);
      var hide = !!(want && want !== "all" && st !== want);
      cards[i].classList.toggle("fd-status-hid", hide);
      if (skipChange) cards[i].classList.remove("fd-change-hid");
      else cards[i].classList.toggle("fd-change-hid", changeMiss(cardDelta(cards[i]), ch));
    }
  }
  function remember(view, val) {
    sel[view] = val;
    try {
      if (window.viewFilt && window.viewFilt[view] && typeof window.viewFilt[view] === "object") {
        window.viewFilt[view].status = val;
      }
    } catch (e1) {}
  }
  function rememberChange(view, val) {
    selChange[view] = val;
    try {
      if (window.viewFilt && window.viewFilt[view] && typeof window.viewFilt[view] === "object") {
        window.viewFilt[view].change = val;
      }
    } catch (e2) {}
  }
  function paint(view) {
    var root = document.getElementById(view === "mom-down" ? "view-mom-down" : "view-mom-up") || document.body;
    var bar = smallest(root, isBar);
    if (!bar) return;
    if (barHasForeignStatus(bar)) return;
    var existing = bar.querySelector("[data-fd-status-row]");
    if (existing && existing.getAttribute("data-fd-status-row") === view) {
      markRow(existing, view);
      return;
    }
    if (existing && existing.parentNode) existing.parentNode.removeChild(existing);
    var tags = smallest(bar, isTags);
    var sort = smallest(bar, isSort);
    if (!tags) return;
    var g = tags.cloneNode(true);
    g.setAttribute("data-fd-status-row", view);
    relabel(g);
    var chips = chipEls(g);
    if (!chips.length) return;
    var parent = chips[0].parentNode;
    var onCls = chips[0].className || "";
    var offCls = onCls;
    var sample = chips[0];
    for (var i = 0; i < chips.length; i++) {
      var tx = (chips[i].textContent || "").replace(/\s+/g, " ").trim();
      if (tx === "All" || tx === "ALL") onCls = chips[i].className || onCls;
      else offCls = chips[i].className || offCls;
    }
    for (var j = chips.length - 1; j >= 0; j--) {
      if (chips[j].parentNode) chips[j].parentNode.removeChild(chips[j]);
    }
    var list = view === "mom-down" ? DOWN : UP;
    var items = [{v: "all", l: "All"}];
    for (var k = 0; k < list.length; k++) items.push({v: list[k], l: list[k]});
    var cur = sel[view] || "all";
    for (var n = 0; n < items.length; n++) {
      (function (val, lab) {
        var b = sample.cloneNode(false);
        b.className = (val === cur) ? onCls : offCls;
        b.textContent = lab;
        b.setAttribute("data-fd-status-val", val);
        if (b.tagName === "BUTTON" && !b.getAttribute("type")) b.type = "button";
        b.addEventListener("click", function (ev) {
          ev.preventDefault();
          ev.stopPropagation();
          remember(view, val);
          applyHide(view);
          var row = bar.querySelector("[data-fd-status-row]");
          if (row) markRow(row, view);
        });
        parent.appendChild(b);
      })(items[n].v, items[n].l);
    }
    if (sort && sort.parentNode) sort.parentNode.insertBefore(g, sort);
    else if (tags.parentNode) tags.parentNode.insertBefore(g, tags.nextSibling);
    else bar.appendChild(g);
  }
  function markRow(row, view) {
    var cur = sel[view] || "all";
    var chips = chipEls(row);
    var onCls = "", offCls = "";
    for (var i = 0; i < chips.length; i++) {
      var cls = chips[i].className || "";
      if (!offCls) offCls = cls;
      if ((chips[i].textContent || "").replace(/\s+/g, " ").trim() === "All") onCls = cls;
    }
    if (!onCls) onCls = offCls;
    for (var j = 0; j < chips.length; j++) {
      var val = chips[j].getAttribute("data-fd-status-val") || "";
      if (onCls && offCls && onCls !== offCls) chips[j].className = (val === cur) ? onCls : offCls;
    }
  }
  function changeLabelText(el) {
    var nodes = el.querySelectorAll("span, label, b, h3, h4, p");
    for (var i = 0; i < nodes.length; i++) {
      if (nodes[i].children.length) continue;
      var tx = (nodes[i].textContent || "").replace(/\s+/g, " ").trim();
      if (tx === "CHANGE" || tx === "Change") return true;
    }
    return false;
  }
  function dropLegacyChange(root) {
    if (!root || !root.querySelectorAll) return;
    var nodes = root.querySelectorAll("div, section, li");
    var victims = [];
    for (var i = 0; i < nodes.length; i++) {
      var el = nodes[i];
      if (el.getAttribute && el.getAttribute("data-fd-change-row")) continue;
      if (!changeLabelText(el)) continue;
      var text = el.textContent || "";
      if (text.indexOf("+>3") >= 0) continue;
      if (text.indexOf("Up (+)") < 0 && text.indexOf("Down (\u2212)") < 0 && text.indexOf("Down (-)") < 0) continue;
      var smaller = false;
      for (var k = 0; k < nodes.length; k++) {
        if (nodes[k] === el || !el.contains(nodes[k])) continue;
        if (nodes[k].getAttribute && nodes[k].getAttribute("data-fd-change-row")) continue;
        if (changeLabelText(nodes[k])) { smaller = true; break; }
      }
      if (!smaller) victims.push(el);
    }
    for (var j = 0; j < victims.length; j++) {
      if (victims[j].parentNode) victims[j].parentNode.removeChild(victims[j]);
    }
  }
  function relabelChange(group) {
    var nodes = group.querySelectorAll("*");
    for (var i = 0; i < nodes.length; i++) {
      if (nodes[i].children.length) continue;
      var tx = (nodes[i].textContent || "").replace(/\s+/g, " ").trim();
      if (tx === "SCORE" || tx === "Score") { nodes[i].textContent = "CHANGE"; return; }
    }
  }
  function markChange(row, view) {
    var cur = selChange[view] || "all";
    var chips = chipEls(row);
    var onCls = "", offCls = "";
    for (var i = 0; i < chips.length; i++) {
      var cls = chips[i].className || "";
      if (!offCls) offCls = cls;
      if ((chips[i].textContent || "").replace(/\s+/g, " ").trim() === "All") onCls = cls;
    }
    if (!onCls) onCls = offCls;
    for (var j = 0; j < chips.length; j++) {
      var val = chips[j].getAttribute("data-fd-change-val") || "";
      if (onCls && offCls && onCls !== offCls) chips[j].className = (val === cur) ? onCls : offCls;
    }
  }
  function hasGranularChange(bar) {
    if (!bar || (bar.textContent || "").indexOf("+>3") < 0) return false;
    var nodes = bar.querySelectorAll("div, section, li");
    for (var i = 0; i < nodes.length; i++) {
      if (!changeLabelText(nodes[i])) continue;
      if ((nodes[i].textContent || "").indexOf("+>3") < 0) continue;
      return true;
    }
    return false;
  }
  function viewFromEl(el) {
    var host = el && el.closest ? el.closest("#view-mom-up, #view-mom-down") : null;
    if (host && host.id === "view-mom-down") return "mom-down";
    if (host && host.id === "view-mom-up") return "mom-up";
    return viewOf();
  }
  function paintChange(view) {
    var root = document.getElementById(view === "mom-down" ? "view-mom-down" : "view-mom-up") || document.body;
    var bar = smallest(root, isBar);
    if (!bar) return;
    dropLegacyChange(bar);
    if (hasGranularChange(bar)) return;
    var existing = bar.querySelector("[data-fd-change-row]");
    if (existing && existing.getAttribute("data-fd-change-row") === view) {
      markChange(existing, view);
      return;
    }
    if (existing && existing.parentNode) existing.parentNode.removeChild(existing);
    var score = smallest(bar, isScoreGroup);
    if (!score) return;
    var g = score.cloneNode(true);
    g.setAttribute("data-fd-change-row", view);
    relabelChange(g);
    var chips = chipEls(g);
    if (!chips.length) return;
    var parent = chips[0].parentNode;
    var onCls = chips[0].className || "";
    var offCls = onCls;
    var sample = chips[0];
    for (var i = 0; i < chips.length; i++) {
      var tx = (chips[i].textContent || "").replace(/\s+/g, " ").trim();
      if (tx === "All" || tx === "ALL") onCls = chips[i].className || onCls;
      else offCls = chips[i].className || offCls;
    }
    for (var j = chips.length - 1; j >= 0; j--) {
      if (chips[j].parentNode) chips[j].parentNode.removeChild(chips[j]);
    }
    var items = [{v:"all", l:"All"}, {v:"gt3", l:"+>3"}, {v:"le3", l:"+\u22643"}, {v:"flat", l:"Flat"}, {v:"nle3", l:"\u2212\u22643"}, {v:"ngt3", l:"\u2212>3"}];
    var cur = selChange[view] || "all";
    for (var n = 0; n < items.length; n++) {
      (function (val, lab) {
        var b = sample.cloneNode(false);
        b.className = (val === cur) ? onCls : offCls;
        b.textContent = lab;
        b.setAttribute("data-fd-change-val", val);
        if (b.tagName === "BUTTON" && !b.getAttribute("type")) b.type = "button";
        b.addEventListener("click", function (ev) {
          ev.preventDefault();
          ev.stopPropagation();
          rememberChange(view, val);
          if (typeof window.paintView === "function") {
            try { window.paintView(view); } catch (e4) {}
            return;
          }
          if (changeOwnedByApply()) return;
          applyHide(view);
          var row = bar.querySelector("[data-fd-change-row]");
          if (row) markChange(row, view);
        });
        parent.appendChild(b);
      })(items[n].v, items[n].l);
    }
    if (score.parentNode) score.parentNode.insertBefore(g, score.nextSibling);
    else bar.appendChild(g);
  }
  function onChangeClick(ev) {
    if (ev.__fdChangeClick) return;
    var t = ev.target;
    if (!t || !t.closest) return;
    var b = t.closest("[data-k='change'], [data-fd-change-val]");
    if (!b) return;
    var view = viewFromEl(b);
    if (view !== "mom-up" && view !== "mom-down") return;
    ev.__fdChangeClick = 1;
    var raw = b.getAttribute("data-v");
    if (raw == null || raw === "") raw = b.getAttribute("data-fd-change-val") || "";
    var val = normChange(raw);
    rememberChange(view, val);
    if (typeof window.paintView === "function") {
      try { window.paintView(view); } catch (e3) {}
      return;
    }
    if (changeOwnedByApply()) return;
    applyHide(view);
    var row = document.querySelector("[data-fd-change-row]");
    if (row) markChange(row, view);
  }
  function tick() {
    var view = viewOf();
    if (view !== "mom-up" && view !== "mom-down") { clearHide(); return; }
    paint(view);
    paintChange(view);
    applyHide(view);
  }
  var scheduled = false;
  function schedule() {
    if (scheduled) return;
    scheduled = true;
    window.setTimeout(function () { scheduled = false; tick(); }, 0);
  }
  var booted = false;
  function boot() {
    if (booted) return;
    booted = true;
    document.addEventListener("click", onChangeClick, true);
    dropLegacyChange(document);
    if (sourcePatched()) {
      if (window.MutationObserver && document.documentElement) {
        var legacyObs = new MutationObserver(function () { dropLegacyChange(document); });
        legacyObs.observe(document.documentElement, {childList: true, subtree: true});
      }
      return;
    }
    tick();
    if (window.MutationObserver && document.documentElement) {
      var obs = new MutationObserver(function () { schedule(); });
      obs.observe(document.documentElement, {childList: true, subtree: true});
    }
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
""".replace("__UP__", up).replace("__DOWN__", down).strip()


def _html_has_mom_filt(html_text: str) -> bool:
    for match in _SCRIPT_TAG_RE.finditer(html_text):
        attrs = match.group(2)
        if re.search(r"\bid\s*=\s*['\"]fd-mom-status-js['\"]", attrs, re.I):
            continue
        body = match.group(3)
        if "buildFiltBar" in body or "applyMomFilters" in body or "viewFilt" in body:
            return True
    return False


def _inject_status_fallback(html_text: str) -> str:
    css = (
        f'<style id="{_STATUS_CSS_ID}">\n'
        ".fd-status-hid, .fd-change-hid { display: none !important; }\n"
        "</style>\n"
    )
    js = f'<script id="{_STATUS_JS_ID}">\n{_fallback_js()}\n</script>\n'
    html_text, _n_css = re.subn(
        rf'<style\b[^>]*\bid=["\']{_STATUS_CSS_ID}["\'][^>]*>.*?</style>\s*',
        lambda _m: css,
        html_text,
        count=1,
        flags=re.I | re.S,
    )
    if f'id="{_STATUS_CSS_ID}"' not in html_text and f"id='{_STATUS_CSS_ID}'" not in html_text:
        if "</head>" in html_text:
            html_text = html_text.replace("</head>", css + "</head>", 1)
        else:
            html_text = css + html_text
    html_text, _n_js = re.subn(
        rf'<script\b[^>]*\bid=["\']{_STATUS_JS_ID}["\'][^>]*>.*?</script>\s*',
        lambda _m: js,
        html_text,
        count=1,
        flags=re.I | re.S,
    )
    if f'id="{_STATUS_JS_ID}"' not in html_text and f"id='{_STATUS_JS_ID}'" not in html_text:
        if "</body>" in html_text:
            html_text = html_text.replace("</body>", js + "</body>", 1)
        else:
            html_text += js
    return html_text


def ensure_mom_status_filter(html_text: str) -> str:
    """Patch embedded ``viewFilt`` / ``applyMomFilters`` / ``buildFiltBar``.

    Mom Up gains All + Strong momentum + Momentum building + Constructive.
    Mom Down gains All + Weak / fading + Softening. Single-select, after Tags
    and before Sort. Outliers keeps no Status row. Does not restyle the bar.

    Mom Up and Mom Down also keep a single Change row (All / +>3 / +≤3 / Flat /
    −≤3 / −>3) filtered on ``mom_score_d10`` inside ``applyMomFilters``. A legacy
    Up (+) / Down (−) group is removed rather than left beside the granular chips.
    """
    if not html_text:
        return html_text
    html_text = _patch_status_scripts(html_text)
    if not _html_has_mom_filt(html_text):
        return html_text
    return _inject_status_fallback(html_text)


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
    streak_map = mom_streak.streak_db(cards, hist=hist)
    chart_map = chart_marks.chart_db(cards)
    hitch_map = desk_hitch.hitch_db(cards, hitch_index)
    paper_marks = paper_trade.marks_db(cards, book=book)
    ss_ranked = s_score.rank_book(cards, root=root, write_panel_file=True)
    drill = ""
    for card in cards:
        detail = chart_marks.render_detail_svg(card)
        if detail:
            ticker = html.escape(str(card.get("ticker") or card.get("name") or ""))
            drill = (
                f'<section id="fd-name-drill" class="is-on" data-fd-drill-chart="1" data-t="{ticker}">'
                f"{detail}</section>"
            )
            break
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
    .card > header {{ display: flex; flex-wrap: wrap; align-items: flex-start; gap: 6px 8px; }}
    .card > header h2 {{ flex: 1 1 auto; margin: 0; }}
    .score {{ margin-left: auto; text-align: right; font: 700 22px/1 "Segoe UI", sans-serif; font-variant-numeric: tabular-nums; }}
    .pills {{ flex: 1 0 100%; min-height: 18px; }}
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
    {card_render.strip_css()}
    {mom_streak.streak_css()}
    {chart_marks.strip_css()}
    {breakout.strip_css()}
    {desk_hitch.strip_css()}
    {s_score.strip_css()}
  </style>
</head>
<body>
  {NAV_HTML}
  <h1>{html.escape(title)}</h1>
  <p class="meta">asof {asof or "—"} · Refresh / Momentum Up / Down / Breakout / Breakdown · mom streak vs 5 · intraday={"on" if intra else "off"}</p>
  {empty}
  {drill}
  {breakout.panes_html(ranked)}
  {s_score.panes_html(ss_ranked)}
  <div id="home">
  <div class="grid">
    {"".join(rows)}
  </div>
  </div>
  {mom_streak.embed_db(streak_map)}
  {chart_marks.embed_db(chart_map)}
  {breakout.embed_db(ranked)}
  {desk_hitch.embed_db(hitch_map)}
  {s_score.embed_db(ss_ranked)}
  <script>
  {mom_streak.strip_js()}
  </script>
  <script>
  {chart_marks.overlay_js()}
  </script>
</body>
</html>
"""
    html_text = gics_filter.ensure_embedded(html_text, None)  # BINNED: remove leftover GICS strip
    html_text = mom_streak.ensure_embedded(html_text, streak_map)
    html_text = chart_marks.ensure_embedded(html_text, chart_map)
    html_text = card_render.ensure_embedded(html_text)
    html_text = breakout.ensure_embedded(html_text, ranked)
    html_text = book_delta.ensure_embedded(html_text, None)  # BINNED: remove leftover book-delta strip
    html_text = desk_hitch.ensure_embedded(html_text, hitch_map)
    html_text = paper_trade.ensure_embedded(html_text, paper_marks)
    html_text = s_score.ensure_embedded(html_text, ss_ranked)
    return ensure_mom_status_filter(_ensure_options_refresh_ui(html_text))


def write_combined(
    path: Path | str | None = None,
    *,
    root: Path | None = None,
    cards: list[Mapping[str, Any]] | None = None,
    book: Mapping[str, Any] | None = None,
    html: str | None = None,
) -> Path:
    """Write ``factorbook.html``. Patch live fat chrome; never emit skinny grid.

    GICS sector-filter strip and book-delta strip are BINNED: leftover hosts
    are removed on every write. G1–G12 group chips stay.
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
    text = gics_filter.ensure_embedded(text, None)  # BINNED: remove leftover GICS strip
    text = mom_streak.ensure_embedded(text, mom_streak.streak_db(cards, hist=hist))
    text = chart_marks.ensure_embedded(text, chart_marks.chart_db(cards))
    text = card_render.ensure_embedded(text)
    text = breakout.ensure_embedded(text, ranked)
    text = book_delta.ensure_embedded(text, None)  # BINNED: remove leftover book-delta strip
    text = desk_hitch.ensure_embedded(text, hitch_map)
    text = paper_trade.ensure_embedded(text, paper_marks)
    text = s_score.ensure_embedded(text, ss_ranked)
    text = ensure_mom_status_filter(text)
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
