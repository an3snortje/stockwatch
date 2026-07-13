"""Build a stock-flow graph from movements and render it as a self-contained
interactive Sankey (no external dependencies — vanilla SVG + JS).

Flow model (left to right):
    Vendors -> RM stores -> Production (DO/IC jobs) -> FG stores -> Customers
with adjustment/sales/RTV hubs as side branches. Reverse movements (returns,
cancellations) are netted against their forward edge so the graph stays a DAG;
inter-store transfers and product migrations net to zero and appear only in
the components table.
"""

from __future__ import annotations

import json

import pandas as pd

from .analysis import classify_movements
from .config import Config

PROD = "Production (DO/IC jobs)"
VENDORS = "Vendors"
CUSTOMERS = "Customers"

# node role -> (column, light hex, dark hex)  — categorical order is fixed;
# hubs are neutral gray (labels carry their identity).
ROLES = {
    "vendors": (0, "#2a78d6", "#3987e5"),
    "rm_store": (1, "#1baf7a", "#199e70"),
    "production": (2, "#eda100", "#c98500"),
    "fg_store": (3, "#008300", "#008300"),
    "customers": (4, "#4a3aa7", "#9085e9"),
    "hub": (None, "#77766f", "#96958c"),  # column set per hub below
}

HUBS = {  # name -> column
    "Adj in (RM)": 0,
    "Adj out (RM)": 2,
    "Direct sales (RM)": 2,
    "Returned to vendor": 2,
    "Adj in (FG)": 2,
    "Adj out (FG)": 4,
}

SKIP_TYPES = {  # net to zero at store level; table-only
    "WAREHOUSE TRANSFER IN", "WAREHOUSE TRANSFER OUT",
    "TRANSFERRED IN", "TRANSFERRED OUT",
}


def _route(dataset: str, mtype: str, direction: str, qty: float, store: str) -> tuple[str, str, int] | None:
    """Return (source, target, sign) for one movement row; None = not in the graph."""
    if mtype in SKIP_TYPES:
        return None
    rm = dataset == "rm_movements"
    if rm:
        if mtype == "RECEIPT":
            return (VENDORS, store, 1)
        if mtype == "DESPATCH":
            return (store, PROD, 1)
        if mtype == "CANCELLED DESPATCH":
            return (store, PROD, -1 if qty > 0 else 1)
        if mtype == "RETURN":
            return (store, PROD, -1)  # job returns reduce net issues to production
        if mtype == "SALE STOCK":
            return (store, "Direct sales (RM)", 1)
        if mtype == "RTV":
            return (store, "Returned to vendor", 1)
    else:
        if mtype == "RECEIVED":
            return (PROD, store, 1)
        if mtype in ("INVOICED", "PICKED"):
            return (store, CUSTOMERS, 1)
        if mtype == "RETURNED":
            return (store, CUSTOMERS, -1)  # customer returns reduce net despatch
        if mtype.startswith("DESPATCH CANCELLED"):
            return (store, CUSTOMERS, -1 if qty > 0 else 1)
    if direction == "adjustment":
        side = "RM" if rm else "FG"
        if qty > 0:
            return (f"Adj in ({side})", store, 1)
        return (store, f"Adj out ({side})", 1)
    return None


def build_flow(mov: pd.DataFrame, cfg: Config, measure: str = "value") -> dict:
    """Aggregate movements into {nodes, links, components, measure}."""
    df = classify_movements(mov, cfg)
    if measure == "value" and ("value" not in df.columns or df["value"].abs().sum() == 0):
        measure = "units"
    df["weight"] = (df["value"] if measure == "value" else df["quantity"]).abs()
    df["units"] = df["quantity"].abs()

    contrib: dict[tuple[str, str], dict] = {}
    for row in df.itertuples(index=False):
        store = ("RM · " if row.dataset == "rm_movements" else "FG · ") + row.warehouse
        routed = _route(row.dataset, row.movement_type, row.direction, row.quantity, store)
        if routed is None:
            continue
        src, dst, sign = routed
        agg = contrib.setdefault((src, dst), {"weight": 0.0, "units": 0.0})
        agg["weight"] += sign * row.weight
        agg["units"] += sign * row.units

    # net opposing directions, flip negatives, drop dust
    links = []
    seen = set()
    for (src, dst), agg in contrib.items():
        if (src, dst) in seen:
            continue
        rev = contrib.get((dst, src), {"weight": 0.0, "units": 0.0})
        seen.update({(src, dst), (dst, src)})
        w = agg["weight"] - rev["weight"]
        u = agg["units"] - rev["units"]
        if w < 0:
            src, dst, w, u = dst, src, -w, -u
        if w > 0.5:
            links.append({"source": src, "target": dst, "weight": round(w, 2), "units": round(abs(u), 1)})

    nodes = {}
    for link in links:
        for name in (link["source"], link["target"]):
            if name in nodes:
                continue
            if name == VENDORS:
                role = "vendors"
            elif name == PROD:
                role = "production"
            elif name == CUSTOMERS:
                role = "customers"
            elif name in HUBS:
                role = "hub"
            elif name.startswith("RM · "):
                role = "rm_store"
            else:
                role = "fg_store"
            col, light, dark = ROLES[role]
            nodes[name] = {
                "name": name, "col": HUBS.get(name, col),
                "light": light, "dark": dark, "role": role,
            }

    components = (
        df.groupby(["dataset", "movement_type"])
        .agg(rows=("weight", "size"), units=("quantity", "sum"), value=("weight", "sum"))
        .reset_index()
        .sort_values("value", ascending=False)
    )
    return {
        "nodes": list(nodes.values()),
        "links": links,
        "components": components.to_dict("records"),
        "measure": measure,
    }


def render_html(flow: dict, title: str, subtitle: str) -> str:
    payload = json.dumps({k: flow[k] for k in ("nodes", "links", "measure")})
    comp_rows = "".join(
        f"<tr><td>{c['dataset'].replace('_movements','').upper()}</td><td>{c['movement_type']}</td>"
        f"<td class='num'>{c['rows']:,}</td><td class='num'>{c['units']:+,.0f}</td>"
        f"<td class='num'>{c['value']:,.0f}</td></tr>"
        for c in flow["components"]
    )
    unit_label = "R" if flow["measure"] == "value" else "units"
    return (
        HTML_TEMPLATE
        .replace("__TITLE__", title)
        .replace("__SUBTITLE__", subtitle)
        .replace("__UNIT__", unit_label)
        .replace("__COMPONENT_ROWS__", comp_rows)
        .replace("__DATA__", payload)
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
  }
  @media (prefers-color-scheme: dark) {
    :root { --surface-1:#1a1a19; --surface-2:#242423; --text-primary:#fff;
            --text-secondary:#c3c2b7; --text-muted:#96958c; --border:#3a3936; }
  }
  * { box-sizing:border-box; margin:0; }
  body { background:var(--surface-1); color:var(--text-primary);
         font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif; padding:24px; }
  h1 { font-size:18px; font-weight:600; }
  .sub { color:var(--text-secondary); margin:2px 0 20px; font-size:13px; }
  #chart { width:100%; overflow-x:auto; }
  svg text { font:12px system-ui,sans-serif; fill:var(--text-primary); }
  svg .nodeval { fill:var(--text-secondary); font-size:11px; }
  .tip { position:fixed; pointer-events:none; background:var(--surface-2);
         border:1px solid var(--border); border-radius:6px; padding:8px 10px;
         font-size:12.5px; color:var(--text-primary); display:none; z-index:10;
         box-shadow:0 2px 10px rgba(0,0,0,.15); max-width:300px; }
  .tip b { display:block; }
  .tip span { color:var(--text-secondary); }
  h2 { font-size:14px; font-weight:600; margin:28px 0 8px; }
  table { border-collapse:collapse; width:100%; max-width:760px; font-size:12.5px; }
  th,td { text-align:left; padding:5px 10px; border-bottom:1px solid var(--border); }
  th { color:var(--text-secondary); font-weight:500; }
  td.num,th.num { text-align:right; font-variant-numeric:tabular-nums; }
  .note { color:var(--text-muted); font-size:12px; margin-top:10px; max-width:760px; }
</style>
</head>
<body>
<h1>__TITLE__</h1>
<div class="sub">__SUBTITLE__</div>
<div id="chart"></div>
<div class="tip" id="tip"></div>
<h2>Movement components (__UNIT__)</h2>
<table>
  <thead><tr><th>Store</th><th>Movement type</th><th class="num">Rows</th>
  <th class="num">Net units</th><th class="num">Gross __UNIT__</th></tr></thead>
  <tbody>__COMPONENT_ROWS__</tbody>
</table>
<p class="note">Flows are netted: returns and cancellations reduce their forward
edge rather than flowing backwards. Inter-store transfers and product-record
migrations net to zero at store level and appear only in the table.</p>
<script>
const DATA = __DATA__;
const dark = matchMedia("(prefers-color-scheme: dark)").matches;
const fmt = v => DATA.measure === "value"
  ? "R " + v.toLocaleString(undefined, {maximumFractionDigits: 0})
  : v.toLocaleString(undefined, {maximumFractionDigits: 0}) + " units";

function layout() {
  const nodes = new Map(DATA.nodes.map(n => [n.name, {...n, in:0, out:0}]));
  DATA.links.forEach(l => { nodes.get(l.source).out += l.weight; nodes.get(l.target).in += l.weight; });
  nodes.forEach(n => n.total = Math.max(n.in, n.out));

  const cols = [...new Set([...nodes.values()].map(n => n.col))].sort((a,b)=>a-b);
  const W = Math.max(940, document.body.clientWidth - 60), H = 560,
        PADX = 150, nodeW = 14, gap = 14;
  const colX = c => PADX + (W - 2*PADX) * cols.indexOf(c) / Math.max(1, cols.length - 1) - nodeW/2;
  const grand = [...nodes.values()].filter(n=>n.col===cols[0]).reduce((s,n)=>s+n.total,0) ||
                Math.max(...[...nodes.values()].map(n=>n.total));
  const scale = (H - 120) / Math.max(1, [...cols].map(c =>
      [...nodes.values()].filter(n=>n.col===c).reduce((s,n)=>s+n.total,0)
    ).reduce((a,b)=>Math.max(a,b),0));

  cols.forEach(c => {
    const list = [...nodes.values()].filter(n => n.col === c).sort((a,b)=>b.total-a.total);
    let y = 40;
    list.forEach(n => {
      n.h = Math.max(4, n.total*scale); n.x = colX(c); n.y = y;
      // small nodes carry a one-line label; leave room for it
      y += n.h + (n.h < 50 ? Math.max(gap, 20) : gap);
    });
  });
  return nodes;
}

function draw() {
  const nodes = layout();
  const W = Math.max(940, document.body.clientWidth - 60), H = 560;
  const svgNS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(svgNS, "svg");
  svg.setAttribute("width", W); svg.setAttribute("height", H);
  const tip = document.getElementById("tip");
  const show = (e, html) => { tip.style.display="block"; tip.innerHTML=html;
    tip.style.left=Math.min(e.clientX+14, innerWidth-320)+"px"; tip.style.top=(e.clientY+12)+"px"; };
  const hide = () => tip.style.display = "none";

  const offOut = new Map(), offIn = new Map();
  const links = [...DATA.links].sort((a,b)=>b.weight-a.weight);
  links.forEach(l => {
    const s = nodes.get(l.source), t = nodes.get(l.target);
    const wS = Math.max(1.5, l.weight * s.h / Math.max(s.out, s.total, 1e-9));
    const wT = Math.max(1.5, l.weight * t.h / Math.max(t.in, t.total, 1e-9));
    const y0 = s.y + (offOut.get(s.name)||0) + wS/2, y1 = t.y + (offIn.get(t.name)||0) + wT/2;
    offOut.set(s.name,(offOut.get(s.name)||0)+wS); offIn.set(t.name,(offIn.get(t.name)||0)+wT);
    const x0 = s.x + 14, x1 = t.x, mx = (x0+x1)/2;
    const p = document.createElementNS(svgNS,"path");
    p.setAttribute("d",`M${x0},${y0} C${mx},${y0} ${mx},${y1} ${x1},${y1}`);
    p.setAttribute("stroke", dark ? s.dark : s.light);
    p.setAttribute("stroke-opacity","0.32");
    p.setAttribute("stroke-width", Math.min(wS,wT));
    p.setAttribute("fill","none");
    p.addEventListener("mousemove", e => { p.setAttribute("stroke-opacity","0.6");
      show(e, `<b>${l.source} → ${l.target}</b>${fmt(l.weight)}<br><span>${l.units.toLocaleString()} physical units</span>`); });
    p.addEventListener("mouseleave", () => { p.setAttribute("stroke-opacity","0.32"); hide(); });
    svg.appendChild(p);
  });

  nodes.forEach(n => {
    const r = document.createElementNS(svgNS,"rect");
    r.setAttribute("x",n.x); r.setAttribute("y",n.y);
    r.setAttribute("width",14); r.setAttribute("height",n.h);
    r.setAttribute("rx",3); r.setAttribute("fill", dark ? n.dark : n.light);
    r.addEventListener("mousemove", e =>
      show(e, `<b>${n.name}</b>in ${fmt(n.in)} · out ${fmt(n.out)}`));
    r.addEventListener("mouseleave", hide);
    svg.appendChild(r);
    const leftSide = n.col >= 3 || (n.role === "hub" && n.col >= 2);
    const small = n.h < 50;
    const mkText = (x, y, cls, anchor, content) => {
      const t = document.createElementNS(svgNS,"text");
      t.setAttribute("x", x); t.setAttribute("y", y);
      if (cls) t.setAttribute("class", cls);
      if (anchor) t.setAttribute("text-anchor", anchor);
      t.textContent = content;
      svg.appendChild(t);
    };
    if (n.role === "production") {
      // tall middle node flanked by labels on both sides — label it above
      mkText(n.x+7, n.y-24, null, "middle", n.name);
      mkText(n.x+7, n.y-10, "nodeval", "middle", fmt(n.total));
    } else if (small) {
      // one line: name · value (stacked two-liners collide at this size)
      mkText(leftSide ? n.x-6 : n.x+20, n.y + n.h/2 + 4, null,
             leftSide ? "end" : null, `${n.name} · ${fmt(n.total)}`);
    } else {
      mkText(leftSide ? n.x-6 : n.x+20, n.y + Math.min(n.h/2, 14), null,
             leftSide ? "end" : null, n.name);
      mkText(leftSide ? n.x-6 : n.x+20, n.y + Math.min(n.h/2, 14) + 15, "nodeval",
             leftSide ? "end" : null, fmt(n.total));
    }
  });
  const holder = document.getElementById("chart");
  holder.innerHTML = ""; holder.appendChild(svg);
}
draw();
addEventListener("resize", draw);
</script>
</body>
</html>
"""
