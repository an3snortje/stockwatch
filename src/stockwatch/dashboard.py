"""Build a self-contained, interactive stock dashboard (no external deps —
vanilla SVG + JS).

Shows raw-material movements by cost type and finished-goods movements by
product, with opening/closing balances and a reconciliation check per stock
type. All movement data is embedded aggregated by day + category, so the
in-page date pickers re-total the charts and the reconciliation live; opening
and closing balances come from the nearest nightly baselines to the chosen
dates.
"""

from __future__ import annotations

import json

import pandas as pd

from .analysis import classify_movements
from .config import Config

# Diverging pair (built vs drawn down); validated palette slots blue / red.
POS_LIGHT, POS_DARK = "#2a78d6", "#3987e5"
NEG_LIGHT, NEG_DARK = "#e34948", "#e66767"

TOP_N = 20  # categories shown individually; the rest fold into "Other".


def _signed_value(df: pd.DataFrame, cfg: Config) -> pd.Series:
    """Signed Rand value mirroring signed_qty (issues negative)."""
    if "value" not in df.columns:
        return pd.Series(0.0, index=df.index)
    val = df["value"].astype(float)
    if cfg.issues_stored_positive:
        val = val.where(df["direction"] != "issue", -val.abs())
    return val


def _movement_rows(mov: pd.DataFrame, cfg: Config) -> tuple[list[dict], list[str]]:
    """Aggregate one movement set by (day, category, direction) into embed rows.

    Returns (rows, category_order). Categories beyond TOP_N by |net value|
    fold into "Other".
    """
    if mov.empty:
        return [], []
    df = classify_movements(mov, cfg)
    df["cat"] = df.get("category", "").astype(str).str.strip().replace("", "(unspecified)")
    df["signed_val"] = _signed_value(df, cfg)
    df["day"] = df["movement_date"].dt.strftime("%Y-%m-%d")

    rank = df.groupby("cat")["signed_val"].apply(lambda s: s.abs().sum())
    if rank.sum() == 0:  # no value data — rank by unit throughput instead
        rank = df.groupby("cat")["signed_qty"].apply(lambda s: s.abs().sum())
    top = set(rank.sort_values(ascending=False).head(TOP_N).index)
    df["cat"] = df["cat"].where(df["cat"].isin(top), "Other")

    grp = df.groupby(["day", "cat", "direction"]).agg(
        qty=("signed_qty", "sum"), val=("signed_val", "sum")
    ).reset_index()

    rows: dict[tuple[str, str], dict] = {}
    keymap = {"receipt": "r", "issue": "i", "adjustment": "a", "other": "o"}
    for rec in grp.itertuples(index=False):
        row = rows.setdefault((rec.day, rec.cat), {"d": rec.day, "c": rec.cat,
            "ru": 0.0, "iu": 0.0, "au": 0.0, "ou": 0.0,
            "rv": 0.0, "iv": 0.0, "av": 0.0, "ov": 0.0})
        k = keymap.get(rec.direction, "o")
        row[k + "u"] += round(float(rec.qty), 1)
        row[k + "v"] += round(float(rec.val), 2)
    order = [c for c in rank.sort_values(ascending=False).index if c in top]
    if (df["cat"] == "Other").any():
        order.append("Other")
    return list(rows.values()), order


def build_dashboard(
    rm_mov: pd.DataFrame,
    fg_mov: pd.DataFrame,
    cfg: Config,
    baselines: dict[str, list[dict]],
) -> dict:
    """Assemble the embed payload for the dashboard."""
    rm_rows, rm_cats = _movement_rows(rm_mov, cfg)
    fg_rows, fg_cats = _movement_rows(fg_mov, cfg)
    return {
        "movements": {"rm": rm_rows, "fg": fg_rows},
        "categories": {"rm": rm_cats, "fg": fg_cats},
        "baselines": baselines,  # {rm|fg|wip: [{d, q, v}]}
        "tolerance_pct": 0.5,    # reconcile pass if |variance| <= this % of closing
    }


def render_html(data: dict, title: str, subtitle: str) -> str:
    return (
        HTML_TEMPLATE
        .replace("__TITLE__", title)
        .replace("__SUBTITLE__", subtitle)
        .replace("__POS_L__", POS_LIGHT).replace("__POS_D__", POS_DARK)
        .replace("__NEG_L__", NEG_LIGHT).replace("__NEG_D__", NEG_DARK)
        .replace("__DATA__", json.dumps(data))
    )


HTML_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
  :root {
    --surface-1:#fcfcfb; --surface-2:#f1f0ee; --text-primary:#0b0b0b;
    --text-secondary:#52514e; --text-muted:#77766f; --border:#dedcd7;
    --pos:__POS_L__; --neg:__NEG_L__;
    --good:#008300; --bad:#e34948;
  }
  @media (prefers-color-scheme: dark) {
    :root { --surface-1:#1a1a19; --surface-2:#242423; --text-primary:#fff;
            --text-secondary:#c3c2b7; --text-muted:#96958c; --border:#3a3936;
            --pos:__POS_D__; --neg:__NEG_D__; --good:#3caf4f; --bad:#e66767; }
  }
  * { box-sizing:border-box; margin:0; }
  body { background:var(--surface-1); color:var(--text-primary);
         font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif; padding:24px; }
  h1 { font-size:18px; font-weight:600; }
  .sub { color:var(--text-secondary); margin:2px 0 18px; font-size:13px; }
  .controls { display:flex; flex-wrap:wrap; gap:16px 24px; align-items:flex-end;
              padding:14px 16px; background:var(--surface-2);
              border:1px solid var(--border); border-radius:8px; margin-bottom:22px; }
  .controls label { display:block; font-size:12px; color:var(--text-secondary); margin-bottom:4px; }
  .controls input[type=date] { font:13px system-ui; padding:5px 8px; color:var(--text-primary);
              background:var(--surface-1); border:1px solid var(--border); border-radius:6px; }
  .seg { display:inline-flex; border:1px solid var(--border); border-radius:6px; overflow:hidden; }
  .seg button { font:13px system-ui; padding:6px 12px; border:0; cursor:pointer;
              background:var(--surface-1); color:var(--text-secondary); }
  .seg button.on { background:var(--pos); color:#fff; }
  .cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(260px,1fr));
           gap:14px; margin-bottom:26px; }
  .card { border:1px solid var(--border); border-radius:8px; padding:14px 16px; background:var(--surface-2); }
  .card h3 { font-size:13px; font-weight:600; margin-bottom:10px; display:flex;
             justify-content:space-between; align-items:center; }
  .badge { font-size:11px; font-weight:600; padding:2px 8px; border-radius:999px; }
  .badge.ok { color:#fff; background:var(--good); }
  .badge.off { color:#fff; background:var(--bad); }
  .badge.na { color:var(--text-secondary); background:var(--border); }
  .row { display:flex; justify-content:space-between; font-size:12.5px; padding:3px 0; }
  .row.total { border-top:1px solid var(--border); margin-top:4px; padding-top:6px; font-weight:600; }
  .row span:last-child { font-variant-numeric:tabular-nums; }
  .muted { color:var(--text-muted); }
  h2 { font-size:14px; font-weight:600; margin:22px 0 6px; }
  .panels { display:grid; grid-template-columns:repeat(auto-fit,minmax(340px,1fr)); gap:24px; }
  .panel svg { width:100%; height:auto; display:block; }
  .tip { position:fixed; pointer-events:none; background:var(--surface-2);
         border:1px solid var(--border); border-radius:6px; padding:8px 10px;
         font-size:12.5px; color:var(--text-primary); display:none; z-index:10;
         box-shadow:0 2px 10px rgba(0,0,0,.15); max-width:300px; }
  .tip b { display:block; margin-bottom:2px; }
  .tip .k { color:var(--text-secondary); }
  .note { color:var(--text-muted); font-size:12px; margin-top:16px; max-width:900px; }
  svg text { fill:var(--text-primary); }
  .axis { stroke:var(--border); }
  .zero { stroke:var(--text-muted); }
</style>
</head>
<body>
<h1>__TITLE__</h1>
<div class="sub">__SUBTITLE__</div>

<div class="controls">
  <div><label for="from">From</label><input type="date" id="from"></div>
  <div><label for="to">To</label><input type="date" id="to"></div>
  <div><label>Measure</label>
    <div class="seg" id="measure">
      <button data-m="units" class="on">Units</button><button data-m="value">Rand</button>
    </div>
  </div>
</div>

<h2>Opening &amp; closing stock — does it reconcile?</h2>
<div class="cards" id="cards"></div>

<div class="panels">
  <div class="panel"><h2>Raw materials by cost type</h2><div id="rmchart"></div></div>
  <div class="panel"><h2>Finished goods by product</h2><div id="fgchart"></div></div>
</div>

<div class="tip" id="tip"></div>
<p class="note" id="footnote"></p>

<script>
const DATA = __DATA__;
const dark = matchMedia("(prefers-color-scheme: dark)").matches;
const POS = getComputedStyle(document.documentElement).getPropertyValue("--pos").trim();
const NEG = getComputedStyle(document.documentElement).getPropertyValue("--neg").trim();
const tip = document.getElementById("tip");
let measure = "units";

const nf = new Intl.NumberFormat(undefined, {maximumFractionDigits:0});
const fmt = (v, m) => (m === "value")
  ? "R " + nf.format(v)
  : nf.format(v) + " u";
const fmtSigned = (v, m) => (v >= 0 ? "+" : "") + fmt(v, m);

// ---- date range: constrain pickers to the embedded data span --------------
function dataSpan() {
  const days = [];
  for (const t of ["rm","fg"]) DATA.movements[t].forEach(r => days.push(r.d));
  for (const k in DATA.baselines) DATA.baselines[k].forEach(b => days.push(b.d));
  days.sort();
  return [days[0], days[days.length-1]];
}
const [minD, maxD] = dataSpan();
const fromEl = document.getElementById("from"), toEl = document.getElementById("to");
[fromEl, toEl].forEach(el => { el.min = minD; el.max = maxD; });
fromEl.value = minD; toEl.value = maxD;

// ---- movement aggregation over the selected window -------------------------
function aggregate(tag, from, to) {
  const byCat = new Map();
  let net = 0;                       // net units over the window (for reconcile)
  for (const r of DATA.movements[tag]) {
    if (r.d < from || r.d > to) continue;
    const u = r.ru + r.iu + r.au + r.ou;
    net += u;
    const c = byCat.get(r.c) || {cat:r.c, ru:0,iu:0,au:0,ou:0, rv:0,iv:0,av:0,ov:0};
    c.ru+=r.ru; c.iu+=r.iu; c.au+=r.au; c.ou+=r.ou;
    c.rv+=r.rv; c.iv+=r.iv; c.av+=r.av; c.ov+=r.ov;
    byCat.set(r.c, c);
  }
  return {byCat, net};
}

// nearest baseline dated on or before `date`
function baselineAt(ds, date) {
  let pick = null;
  for (const b of DATA.baselines[ds] || []) if (b.d <= date && (!pick || b.d > pick.d)) pick = b;
  return pick;
}

// ---- reconciliation cards --------------------------------------------------
const STORES = [
  {tag:"rm", bal:"rm", label:"Raw materials", unit:"units"},
  {tag:"fg", bal:"fg", label:"Finished goods", unit:"units"},
  {tag:null, bal:"wip", label:"Work in progress", unit:"R"},
];

function renderCards(from, to) {
  const host = document.getElementById("cards"); host.innerHTML = "";
  const notes = [];
  for (const s of STORES) {
    const open = baselineAt(s.bal, from), close = baselineAt(s.bal, to);
    const div = document.createElement("div"); div.className = "card";
    const rows = [];
    const q = b => b == null ? null : b.q;

    if (open && open.d !== from) notes.push(`${s.label} opening uses baseline ${open.d}`);
    if (close && close.d !== to) notes.push(`${s.label} closing uses baseline ${close.d}`);

    let badge = `<span class="badge na">no baseline</span>`;
    if (s.tag === null) {
      // WIP: no movement ledger feeds it — show opening/closing only.
      rows.push(cardRow("Opening", open ? fmt(open.q,"value") : "—"));
      rows.push(cardRow("Closing", close ? fmt(close.q,"value") : "—"));
      if (open && close) rows.push(cardRow("Change", fmtSigned(close.q-open.q,"value"), "total"));
      badge = `<span class="badge na">no ledger</span>`;
    } else if (open && close) {
      const {net} = aggregate(s.tag, open.d, close.d);   // window between the two baselines
      const expected = open.q + net;
      const variance = close.q - expected;
      const tol = Math.max(1, Math.abs(close.q) * DATA.tolerance_pct/100);
      const ok = Math.abs(variance) <= tol;
      badge = ok ? `<span class="badge ok">reconciles</span>`
                 : `<span class="badge off">off by ${fmt(Math.abs(variance),"units")}</span>`;
      rows.push(cardRow("Opening", fmt(open.q,"units")));
      rows.push(cardRow("+ net movement", fmtSigned(net,"units")));
      rows.push(cardRow("= expected closing", fmt(expected,"units"), "total"));
      rows.push(cardRow("Actual closing", fmt(close.q,"units")));
      rows.push(cardRow("Variance", fmtSigned(variance,"units")));
    } else {
      rows.push(cardRow("Opening", open ? fmt(open.q,"units") : "—"));
      rows.push(cardRow("Closing", close ? fmt(close.q,"units") : "—"));
    }
    div.innerHTML = `<h3>${s.label} ${badge}</h3>` + rows.join("");
    host.appendChild(div);
  }
  const fn = document.getElementById("footnote");
  fn.textContent = "Reconciliation window runs between the nearest nightly baselines to your dates; "
    + "movements are netted (issues negative). WIP has no movement ledger, so only its balances are shown. "
    + (notes.length ? "Note: " + [...new Set(notes)].join("; ") + "." : "");
}
function cardRow(k, v, cls="") {
  return `<div class="row ${cls}"><span class="muted">${k}</span><span>${v}</span></div>`;
}

// ---- horizontal diverging bar chart ---------------------------------------
function catNet(c, m) {
  return (m === "value") ? c.rv+c.iv+c.av+c.ov : c.ru+c.iu+c.au+c.ou;
}
function renderChart(hostId, tag, from, to) {
  const {byCat} = aggregate(tag, from, to);
  let items = [...byCat.values()].map(c => ({c, net: catNet(c, measure)}))
      .filter(x => Math.abs(x.net) > 0.5)
      .sort((a,b) => Math.abs(b.net) - Math.abs(a.net));
  const host = document.getElementById(hostId); host.innerHTML = "";
  const svgNS = "http://www.w3.org/2000/svg";
  if (!items.length) { host.innerHTML = `<p class="muted">No movements in range.</p>`; return; }

  const rowH = 26, padT = 8, padB = 26, W = Math.max(340, host.clientWidth || 420);
  const labelW = 160, valW = 100, plotL = labelW, plotR = W - valW, plotW = plotR - plotL;
  const H = padT + items.length*rowH + padB;
  const maxAbs = Math.max(...items.map(x => Math.abs(x.net)));
  const zero = plotL + plotW/2;
  // Reserve room at each tip so the value label never reaches the label column.
  const half = Math.max(20, plotW/2 - 62);
  const x = v => zero + (v/maxAbs) * half;

  const svg = document.createElementNS(svgNS,"svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`); svg.setAttribute("width","100%");
  const el = (n,a,txt) => { const e=document.createElementNS(svgNS,n);
    for(const k in a) e.setAttribute(k,a[k]); if(txt!=null) e.textContent=txt; svg.appendChild(e); return e; };

  el("line",{x1:zero,y1:padT,x2:zero,y2:H-padB,class:"zero","stroke-width":1});
  items.forEach((it,i) => {
    const y = padT + i*rowH, bh = rowH-8;
    const v = it.net, xv = x(v), w = Math.abs(xv-zero);
    const rx = document.createElementNS(svgNS,"rect");
    rx.setAttribute("x", v>=0?zero:xv); rx.setAttribute("y", y+2);
    rx.setAttribute("width", Math.max(1,w)); rx.setAttribute("height", bh);
    rx.setAttribute("rx",3); rx.setAttribute("fill", v>=0?POS:NEG);
    rx.addEventListener("mousemove", e => showBar(e, it.c));
    rx.addEventListener("mouseleave", () => tip.style.display="none");
    svg.appendChild(rx);
    // category label (truncated) in the fixed left column
    const label = it.c.cat;
    const name = label.length > 24 ? label.slice(0,23)+"…" : label;
    const tn = el("text",{x:plotL-8,y:y+rowH/2+4,"text-anchor":"end","font-size":12}, name);
    if (name !== label) { const ti=document.createElementNS(svgNS,"title"); ti.textContent=label; tn.appendChild(ti); }
    // value just beyond the bar tip (never in the label column)
    el("text",{x: v>=0?xv+6:xv-6, y:y+rowH/2+4,
               "text-anchor": v>=0?"start":"end", "font-size":11.5,
               fill:"var(--text-secondary)"}, fmtSigned(v, measure));
  });
  host.appendChild(svg);
}
function showBar(e, c) {
  const g = (u,v) => measure==="value" ? fmt(v,"value") : fmt(u,"units");
  tip.style.display="block";
  tip.innerHTML = `<b>${c.cat}</b>`
    + `<div class="k">Receipts: ${g(c.ru,c.rv)}</div>`
    + `<div class="k">Issues: ${g(c.iu,c.iv)}</div>`
    + `<div class="k">Adjustments: ${g(c.au,c.av)}</div>`
    + `<div class="k">Net: ${fmtSigned(catNet(c,measure), measure)}</div>`;
  tip.style.left = Math.min(e.clientX+14, innerWidth-320)+"px";
  tip.style.top = (e.clientY+12)+"px";
}

// ---- wire up ---------------------------------------------------------------
function renderAll() {
  let from = fromEl.value, to = toEl.value;
  if (from > to) { [from, to] = [to, from]; }
  renderCards(from, to);
  renderChart("rmchart","rm",from,to);
  renderChart("fgchart","fg",from,to);
}
fromEl.addEventListener("change", renderAll);
toEl.addEventListener("change", renderAll);
document.querySelectorAll("#measure button").forEach(b =>
  b.addEventListener("click", () => {
    measure = b.dataset.m;
    document.querySelectorAll("#measure button").forEach(x => x.classList.toggle("on", x===b));
    renderAll();
  }));
addEventListener("resize", renderAll);
renderAll();
</script>
</body>
</html>
"""
