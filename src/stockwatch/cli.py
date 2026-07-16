"""Command-line interface: stockwatch <command>."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pandas as pd
import typer
from rich.console import Console
from rich.table import Table

from . import analysis, explain, queries
from .config import Config, load_config

app = typer.Typer(help="Analyze and explain iSync inventory movements.", no_args_is_help=True)
console = Console()

CONFIG_OPT = typer.Option("config/tables.yml", "--config", "-c", help="Dataset mapping YAML.")
FROM_OPT = typer.Option(..., "--from", "-f", help="Start date (inclusive), YYYY-MM-DD.")
TO_OPT = typer.Option(..., "--to", "-t", help="End date (exclusive), YYYY-MM-DD.")
ITEM_OPT = typer.Option(None, "--item", help="Filter to one item code.")
WH_OPT = typer.Option(None, "--warehouse", "-w", help="Filter to one warehouse.")
CSV_OPT = typer.Option(None, "--csv", help="Also write the result to this CSV path.")

MOVEMENT_SETS = {"fg": ["fg_movements"], "rm": ["rm_movements"], "all": ["fg_movements", "rm_movements"]}
BALANCE_FOR = {"fg_movements": "fg_balance", "rm_movements": "rm_balance"}


def _fetch(cfg: Config, dataset: str, **filters) -> pd.DataFrame:
    from .db import get_engine, read_sql  # imported lazily so tests don't need pyodbc

    ds = cfg.datasets[dataset]
    sql, params = queries.build_select(ds, **filters)
    df = read_sql(get_engine(), sql, params)
    return queries.normalize(df, ds)


def _movements(cfg: Config, scope: str, date_from: datetime | date, date_to: datetime | date, item: str | None, wh: str | None) -> pd.DataFrame:
    names = MOVEMENT_SETS[scope]
    frames = [
        _fetch(cfg, n, date_from=date_from, date_to=date_to, item_code=item, warehouse=wh).assign(dataset=n)
        for n in names
    ]
    return pd.concat(frames, ignore_index=True)


def _snapshot_at(balances: pd.DataFrame, ts: pd.Timestamp | None = None) -> pd.DataFrame:
    """Aggregate balance per item/warehouse (rows may be per roll/lot/size).

    With ts, restrict to the latest snapshot date per key on or before ts;
    without, aggregate everything (current-state views).
    """
    df = balances
    if ts is not None:
        df = df[df["balance_date"] <= ts]
        latest = df.groupby(analysis.KEY)["balance_date"].transform("max")
        df = df[df["balance_date"] == latest]
    if df.empty:
        return df
    aggs = {
        "item_description": ("item_description", "first"),
        "balance_date": ("balance_date", "max"),
        "quantity": ("quantity", "sum"),
    }
    if "value" in df.columns:
        aggs["value"] = ("value", "sum")
    if "category" in df.columns:
        aggs["category"] = ("category", "first")
    return df.groupby(analysis.KEY, as_index=False).agg(**aggs)


def _read_csv_any(path: Path) -> pd.DataFrame:
    """Read a CSV tolerant of encoding: UTF-8 (with/without BOM) or the cp1252
    that Excel/older Windows writes (en-dash 0x96 etc.). latin-1 never fails."""
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return pd.read_csv(path, encoding=enc)
        except UnicodeDecodeError:
            continue
    return pd.read_csv(path, encoding="latin-1")


def _load_snapshot_csv(path: Path) -> pd.DataFrame:
    """Read a baseline saved earlier by `stockwatch snapshot --csv`."""
    if not path.is_file():
        raise typer.BadParameter(f"snapshot file not found: {path}")
    df = _read_csv_any(path)
    missing = {"item_code", "warehouse", "quantity"} - set(df.columns)
    if missing:
        raise typer.BadParameter(f"{path} is missing columns {sorted(missing)}")
    df["quantity"] = pd.to_numeric(df["quantity"], errors="coerce").fillna(0.0)
    for col in ("item_code", "warehouse"):
        df[col] = df[col].astype(str).str.strip()
    if "item_description" not in df.columns:
        df["item_description"] = ""
    df["balance_date"] = pd.to_datetime(df.get("balance_date"), errors="coerce")
    return df


def _print(df: pd.DataFrame, title: str, csv: Path | None) -> None:
    if csv:
        csv.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(csv, index=False)
        console.print(f"[dim]Wrote {len(df)} rows to {csv}[/dim]")
    table = Table(title=title)
    for col in df.columns:
        table.add_column(str(col))
    for _, row in df.head(50).iterrows():
        table.add_row(*[f"{v:,.1f}" if isinstance(v, float) else str(v) for v in row])
    console.print(table)
    if len(df) > 50:
        console.print(f"[dim]Showing 50 of {len(df)} rows — use --csv for the full set.[/dim]")


def _print_lines(lines: list[str]) -> None:
    for line in lines:
        console.print(f"• {line}")


@app.command()
def summary(
    scope: str = typer.Argument("all", help="fg | rm | all"),
    date_from: datetime = FROM_OPT,
    date_to: datetime = TO_OPT,
    freq: str = typer.Option("MS", help="Period bucket: MS (month) | W | D."),
    item: str = ITEM_OPT,
    warehouse: str = WH_OPT,
    csv: Path = CSV_OPT,
    config: Path = CONFIG_OPT,
):
    """Movement summary (receipts/issues/adjustments/net) per item, warehouse, period."""
    cfg = load_config(config)
    mov = _movements(cfg, scope, date_from.date(), date_to.date(), item, warehouse)
    result = analysis.movement_summary(mov, cfg, freq=freq)
    _print(result, f"Movement summary {date_from:%Y-%m-%d} → {date_to:%Y-%m-%d}", csv)
    _print_lines(explain.explain_summary(result))


@app.command()
def reconcile(
    scope: str = typer.Argument(..., help="fg | rm"),
    date_from: datetime = typer.Option(
        None, "--from", "-f",
        help="Movement window start. With --opening-csv this defaults to the baseline's "
        "own timestamp (avoids double-counting movements from the snapshot day).",
    ),
    date_to: datetime = typer.Option(
        None, "--to", "-t",
        help="Movement window end (exclusive). With --opening-csv this defaults to now, "
        "matching the live closing balance.",
    ),
    item: str = ITEM_OPT,
    warehouse: str = WH_OPT,
    opening_csv: Path = typer.Option(
        None,
        "--opening-csv",
        help="Baseline snapshot CSV (from `stockwatch snapshot --csv` or "
        "`stockwatch import-baseline`) to use as the opening balance. Required when "
        "the balance view is current-state only.",
    ),
    closing_csv: Path = typer.Option(
        None,
        "--closing-csv",
        help="Second baseline CSV to use as the closing balance instead of live stock. "
        "Reconciles two saved snapshots — a fully reproducible, point-to-point check.",
    ),
    csv: Path = CSV_OPT,
    config: Path = CONFIG_OPT,
):
    """Check that opening balance + movements explains the closing balance."""
    if scope not in ("fg", "rm"):
        raise typer.BadParameter("reconcile works per store: choose fg or rm")
    if closing_csv is not None and opening_csv is None:
        raise typer.BadParameter("--closing-csv requires --opening-csv")
    cfg = load_config(config)
    movement_ds = MOVEMENT_SETS[scope][0]
    balance_ds = cfg.datasets[BALANCE_FOR[movement_ds]]
    if opening_csv is not None:
        opening = _load_snapshot_csv(opening_csv)
        if date_from is not None:
            mov_start = pd.Timestamp(date_from)
        elif opening["balance_date"].notna().any():
            mov_start = opening["balance_date"].max()
        else:
            raise typer.BadParameter(f"{opening_csv} has no balance_date column — pass --from")

        if closing_csv is not None:
            closing_raw = _load_snapshot_csv(closing_csv)
            closing = _snapshot_at(closing_raw)
            if date_to is not None:
                mov_end = pd.Timestamp(date_to)
            elif closing_raw["balance_date"].notna().any():
                mov_end = closing_raw["balance_date"].max()
            else:
                raise typer.BadParameter(f"{closing_csv} has no balance_date column — pass --to")
            src = f"{closing_csv} @ {mov_end:%Y-%m-%d %H:%M}"
        else:
            mov_end = pd.Timestamp(date_to) if date_to is not None else pd.Timestamp.now()
            closing = _snapshot_at(_fetch(cfg, balance_ds.name, item_code=item, warehouse=warehouse))
            src = f"live balance now (movements end {mov_end:%Y-%m-%d %H:%M})"
        console.print(
            f"[dim]Opening = {opening_csv} @ {mov_start:%Y-%m-%d %H:%M}; closing = {src}; "
            f"movements {mov_start:%Y-%m-%d %H:%M} → {mov_end:%Y-%m-%d %H:%M}.[/dim]"
        )
    elif balance_ds.has_date:
        if date_from is None or date_to is None:
            raise typer.BadParameter("--from and --to are required without --opening-csv")
        mov_start, mov_end = pd.Timestamp(date_from), pd.Timestamp(date_to)
        balances = _fetch(cfg, balance_ds.name, date_to=date_to.date(), item_code=item, warehouse=warehouse)
        opening = _snapshot_at(balances, mov_start)
        closing = _snapshot_at(balances, mov_end)
    else:
        raise typer.BadParameter(
            f"{balance_ds.name} is a current-state view with no history. Capture a "
            f"baseline first (stockwatch snapshot {balance_ds.name} --csv baseline.csv) "
            f"and pass it back later via --opening-csv."
        )
    mov = _movements(cfg, scope, mov_start.to_pydatetime(), mov_end.to_pydatetime(), item, warehouse)
    mov, excluded = analysis.apply_exclusions(mov, cfg.reconcile_exclusions)
    if len(excluded):
        console.print(
            f"[dim]Excluded {len(excluded)} movement(s) totalling "
            f"{excluded['quantity'].sum():+,.1f} units via reconcile_exclusions rules.[/dim]"
        )
    result = analysis.reconcile(opening, mov, closing, cfg)
    _print(result, f"Reconciliation {scope.upper()} {mov_start:%Y-%m-%d %H:%M} → {mov_end:%Y-%m-%d %H:%M}", csv)
    _print_lines(explain.explain_reconciliation(result))


def _find_baseline(baseline_dir: Path, dataset: str, before: pd.Timestamp) -> Path | None:
    """Newest baseline CSV named <dataset>_YYYYMMDD*.csv dated on or before `before`."""
    candidates = []
    for path in sorted(baseline_dir.glob(f"{dataset}_*.csv")):
        token = path.stem.removeprefix(f"{dataset}_")[:8]
        try:
            stamp = pd.Timestamp(datetime.strptime(token, "%Y%m%d"))
        except ValueError:
            continue
        if stamp <= before:
            candidates.append((stamp, path))
    return max(candidates)[1] if candidates else None


def _list_baselines(baseline_dir: Path, dataset: str) -> list[tuple[pd.Timestamp, Path]]:
    """All baseline CSVs for a dataset, as (date, path), oldest first."""
    out = []
    if not baseline_dir.is_dir():
        return out
    for path in sorted(baseline_dir.glob(f"{dataset}_*.csv")):
        token = path.stem.removeprefix(f"{dataset}_")[:8]
        try:
            out.append((pd.Timestamp(datetime.strptime(token, "%Y%m%d")), path))
        except ValueError:
            continue
    return sorted(out)


@app.command()
def report(
    date_from: datetime = FROM_OPT,
    date_to: datetime = typer.Option(None, "--to", "-t", help="End (exclusive); defaults to now."),
    baseline_dir: Path = typer.Option(
        Path("baselines"), "--baseline-dir",
        help="Folder of baseline CSVs (<dataset>_YYYYMMDD.csv); the newest one on or "
        "before --from becomes the opening balance.",
    ),
    out_dir: Path = typer.Option(None, "--out-dir", help="Also write the three tables as CSVs here."),
    config: Path = CONFIG_OPT,
):
    """Movement breakdown (RM by cost type, FG by product) with opening and
    closing balances for RM, FG and WIP."""
    cfg = load_config(config)
    end = date_to or datetime.now()
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)

    for ds_name, title, fname in (
        ("rm_movements", "Raw material movements by cost type", "rm_by_cost_type.csv"),
        ("fg_movements", "Finished goods movements by product", "fg_by_product.csv"),
    ):
        mov = _fetch(cfg, ds_name, date_from=date_from, date_to=end)
        breakdown = analysis.category_summary(mov, cfg)
        _print(breakdown, f"{title} · {date_from:%Y-%m-%d} → {end:%Y-%m-%d}",
               out_dir / fname if out_dir else None)

    def _col(df: pd.DataFrame, name: str) -> float | None:
        return df[name].sum() if name in df.columns else None

    rows = []
    for ds_name, label, unit in (
        ("rm_balance", "Raw materials", "units"),
        ("fg_balance", "Finished goods", "units"),
        ("wip_balance", "Work in progress", "R"),
    ):
        closing = _snapshot_at(_fetch(cfg, ds_name))
        close_qty, close_val = _col(closing, "quantity"), _col(closing, "value")
        baseline = _find_baseline(baseline_dir, ds_name, pd.Timestamp(date_from))
        opening = _load_snapshot_csv(baseline) if baseline else None
        open_qty = _col(opening, "quantity") if opening is not None else None
        open_val = _col(opening, "value") if opening is not None else None
        rows.append({
            "stock type": label,
            "opening qty": f"{open_qty:,.0f}" if open_qty is not None else "no baseline",
            "closing qty": f"{close_qty:,.0f}",
            "qty change": f"{close_qty - open_qty:+,.0f}" if open_qty is not None else "-",
            "unit": unit,
            "opening R": f"{open_val:,.0f}" if open_val is not None else "-",
            "closing R": f"{close_val:,.0f}" if close_val is not None else "-",
            "R change": f"{close_val - open_val:+,.0f}" if None not in (open_val, close_val) else "-",
            "opening source": baseline.name if baseline else "-",
        })
    balances = pd.DataFrame(rows)
    _print(balances, "Stock balances · opening vs live (qty + Rand value)",
           out_dir / "balances.csv" if out_dir else None)


@app.command()
def dashboard(
    date_from: datetime = FROM_OPT,
    date_to: datetime = typer.Option(None, "--to", "-t", help="End (inclusive); defaults to now."),
    baseline_dir: Path = typer.Option(
        Path("baselines"), "--baseline-dir",
        help="Folder of baseline CSVs (<dataset>_YYYYMMDD.csv) for opening/closing balances.",
    ),
    out: Path = typer.Option(Path("dashboard.html"), "--out", help="Output HTML file."),
    config: Path = CONFIG_OPT,
):
    """Interactive HTML dashboard: RM by cost type, FG by product, with opening/
    closing balances and a reconciliation check. In-page date pickers re-total
    the charts and reconciliation live over the movement window."""
    from .dashboard import build_dashboard, render_html

    cfg = load_config(config)
    end = date_to or datetime.now()
    rm = _fetch(cfg, "rm_movements", date_from=date_from, date_to=end)
    fg = _fetch(cfg, "fg_movements", date_from=date_from, date_to=end)

    # Baselines: every dated snapshot on disk, plus the live balance dated `end`.
    baselines: dict[str, list[dict]] = {}
    for tag, ds_name in (("rm", "rm_balance"), ("fg", "fg_balance"), ("wip", "wip_balance")):
        pts = []
        for stamp, path in _list_baselines(baseline_dir, ds_name):
            snap = _load_snapshot_csv(path)
            pts.append({"d": stamp.strftime("%Y-%m-%d"),
                        "q": float(snap["quantity"].sum()),
                        "v": float(snap["value"].sum()) if "value" in snap.columns else None})
        live = _snapshot_at(_fetch(cfg, ds_name))
        pts.append({"d": end.strftime("%Y-%m-%d"),
                    "q": float(live["quantity"].sum()),
                    "v": float(live["value"].sum()) if "value" in live.columns else None})
        baselines[tag] = pts

    coverage = {"from": date_from.strftime("%Y-%m-%d"), "to": end.strftime("%Y-%m-%d")}
    data = build_dashboard(rm, fg, cfg, baselines, coverage=coverage)
    html = render_html(
        data, "StockWatch dashboard",
        f"Movements {date_from:%Y-%m-%d} → {end:%Y-%m-%d} · balances from {baseline_dir}/ + live",
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    console.print(f"[green]Wrote {out}[/green] — open it in a browser.")


@app.command()
def flow(
    scope: str = typer.Argument("all", help="fg | rm | all"),
    date_from: datetime = FROM_OPT,
    date_to: datetime = typer.Option(None, "--to", "-t", help="End (exclusive); defaults to now."),
    warehouse: str = WH_OPT,
    measure: str = typer.Option("value", "--measure", help="value (Rand) | units"),
    out: Path = typer.Option(Path("flow.html"), "--out", help="Output HTML file."),
    csv: Path = CSV_OPT,
    config: Path = CONFIG_OPT,
):
    """Visualize the flow of stock: Vendors -> RM -> Production -> FG -> Customers.

    Generates a self-contained interactive Sankey HTML (dark/light aware),
    netted so returns and cancellations reduce their forward edge.
    """
    from .flow import build_flow, render_html

    cfg = load_config(config)
    end = date_to or datetime.now()
    mov = _movements(cfg, scope, date_from, end, None, warehouse)
    result = build_flow(mov, cfg, measure=measure)
    if result["measure"] != measure:
        console.print("[yellow]No value column/data — fell back to units.[/yellow]")
    subtitle = (
        f"{date_from:%d %b %Y %H:%M} → {end:%d %b %Y %H:%M} · "
        f"{len(mov):,} movements · measure: {'Rand value' if result['measure']=='value' else 'units'}"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_html(result, "StockWatch — stock flow", subtitle), encoding="utf-8")
    if csv:
        pd.DataFrame(result["links"]).to_csv(csv, index=False)
        console.print(f"[dim]Wrote {len(result['links'])} flow edges to {csv}[/dim]")
    total = sum(l["weight"] for l in result["links"])
    console.print(
        f"Wrote {out} — {len(result['nodes'])} nodes, {len(result['links'])} flows, "
        f"total {'R ' if result['measure']=='value' else ''}{total:,.0f}"
        f"{'' if result['measure']=='value' else ' units'}. Open it in a browser."
    )


@app.command(name="reconcile-chain")
def reconcile_chain(
    scope: str = typer.Argument(..., help="fg | rm"),
    snapshots: list[Path] = typer.Option(
        ..., "--snapshot", "-s",
        help="Baseline CSV, repeated in DATE ORDER (>=2). Consecutive pairs are "
        "reconciled and each item's variance is classified across periods.",
    ),
    csv: Path = CSV_OPT,
    config: Path = CONFIG_OPT,
):
    """Reconcile a chain of snapshots and separate timing noise from real variance.

    Variances that reverse across period boundaries (net ~0) are flagged 'timing';
    variances that survive to the last snapshot are 'persistent' — the real ones.
    """
    if scope not in ("fg", "rm"):
        raise typer.BadParameter("choose fg or rm")
    if len(snapshots) < 2:
        raise typer.BadParameter("need at least two --snapshot CSVs")
    cfg = load_config(config)

    snaps = []
    for path in snapshots:
        snap = _load_snapshot_csv(path)
        if snap["balance_date"].isna().all():
            raise typer.BadParameter(f"{path} has no balance_date — regenerate with import-baseline")
        snaps.append((snap["balance_date"].max(), _snapshot_at(snap), path))
    snaps.sort(key=lambda t: t[0])

    period_rows = []
    for (t0, open_snap, p0), (t1, close_snap, p1) in zip(snaps, snaps[1:]):
        mov = _movements(cfg, scope, t0.to_pydatetime(), t1.to_pydatetime(), None, None)
        mov, _ = analysis.apply_exclusions(mov, cfg.reconcile_exclusions)
        rec = analysis.reconcile(open_snap, mov, close_snap, cfg)
        rec["period"] = f"{t0:%m%d}->{t1:%m%d}"
        period_rows.append(rec[["item_code", "warehouse", "period", "variance", "item_description"]])
        console.print(f"[dim]{t0:%Y-%m-%d %H:%M} → {t1:%Y-%m-%d %H:%M}: "
                      f"{(rec['variance'].abs() > cfg.variance_tolerance).sum()} variance rows[/dim]")

    result = analysis.classify_chain(pd.concat(period_rows, ignore_index=True), cfg.variance_tolerance)
    counts = result["classification"].value_counts().to_dict()
    _print(result[result["classification"] != "clean"],
           f"Chain reconciliation {scope.upper()} ({len(snaps)} snapshots)", csv)
    console.print(
        f"• [bold]{counts.get('persistent', 0)} persistent[/bold] + {counts.get('mixed', 0)} mixed "
        f"= real variances to investigate; {counts.get('timing', 0)} timing (reverse across "
        f"periods — noise). Persistent net: "
        f"{result.loc[result['classification']=='persistent', 'net_variance'].sum():+,.0f} units."
    )


@app.command()
def anomalies(
    scope: str = typer.Argument("all", help="fg | rm | all"),
    date_from: datetime = FROM_OPT,
    date_to: datetime = TO_OPT,
    item: str = ITEM_OPT,
    warehouse: str = WH_OPT,
    csv: Path = CSV_OPT,
    config: Path = CONFIG_OPT,
):
    """Flag negative balances, outlier movements and dormant items — with explanations."""
    cfg = load_config(config)
    mov = _movements(cfg, scope, date_from.date(), date_to.date(), item, warehouse)
    balance_frames = []
    for n in MOVEMENT_SETS[scope]:
        ds = cfg.datasets[BALANCE_FOR[n]]
        fetched = _fetch(
            cfg, ds.name,
            date_to=date_to.date() if ds.has_date else None,
            item_code=item, warehouse=warehouse,
        )
        balance_frames.append(_snapshot_at(fetched, pd.Timestamp(date_to) if ds.has_date else None))
    balances = pd.concat(balance_frames, ignore_index=True)
    result = analysis.detect_anomalies(mov, balances, cfg, as_of=pd.Timestamp(date_to))
    _print(result, "Anomalies", csv)
    _print_lines(explain.explain_anomalies(result))


@app.command()
def snapshot(
    dataset: str = typer.Argument(..., help="wip_balance | fg_balance | rm_balance"),
    as_of: datetime = typer.Option(
        None, "--as-of", help="Snapshot date, YYYY-MM-DD. Ignored for current-state views."
    ),
    warehouse: str = WH_OPT,
    csv: Path = CSV_OPT,
    config: Path = CONFIG_OPT,
):
    """Balance per item/warehouse (incl. WIP); current state or as of a date."""
    cfg = load_config(config)
    ds = cfg.datasets[dataset]
    if ds.kind != "balance":
        raise typer.BadParameter(f"{dataset} is not a balance dataset")
    if ds.has_date:
        as_of = as_of or datetime.now()
        df = _fetch(cfg, dataset, date_to=as_of.date() + pd.Timedelta(days=1), warehouse=warehouse)
        result = _snapshot_at(df, pd.Timestamp(as_of))
        title = f"{dataset} as of {as_of:%Y-%m-%d}"
    else:
        if as_of is not None:
            console.print(f"[yellow]{dataset} is a current-state view — --as-of ignored, showing now.[/yellow]")
        result = _snapshot_at(_fetch(cfg, dataset, warehouse=warehouse))
        title = f"{dataset} (current)"
    _print(result.sort_values("quantity", ascending=False), title, csv)


@app.command(name="snapshot-all")
def snapshot_all(
    out_dir: Path = typer.Option(
        Path("baselines"), "--out-dir", "-o",
        help="Directory for the date-stamped baseline CSVs.",
    ),
    as_of: datetime = typer.Option(
        None, "--as-of",
        help="Date token for the filenames (YYYY-MM-DD). Defaults to today. The CSV "
        "still records the live fetch time as balance_date.",
    ),
    config: Path = CONFIG_OPT,
):
    """Capture rm_balance, fg_balance and wip_balance to baselines/<dataset>_YYYYMMDD.csv
    in one run — the atom for a nightly n8n / scheduled snapshot job.

    Filenames follow the convention `report` and `reconcile-chain` auto-discover.
    """
    cfg = load_config(config)
    stamp = pd.Timestamp(as_of) if as_of else pd.Timestamp.now()
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for ds_name in ("rm_balance", "fg_balance", "wip_balance"):
        ds = cfg.datasets[ds_name]
        if ds.kind != "balance":
            raise typer.BadParameter(f"{ds_name} is not a balance dataset")
        result = _snapshot_at(_fetch(cfg, ds_name))
        path = out_dir / f"{ds_name}_{stamp:%Y%m%d}.csv"
        path.write_text(result.to_csv(index=False), encoding="utf-8")
        summary = f"{len(result):,} rows, qty {result['quantity'].sum():,.1f}"
        if "value" in result.columns:
            summary += f", R{result['value'].sum():,.0f}"
        written.append(f"{path} ({summary})")
    _print_lines(written)


@app.command()
def types(
    scope: str = typer.Argument("all", help="fg | rm | all"),
    date_from: datetime = typer.Option(None, "--from", "-f", help="Start date (inclusive)."),
    date_to: datetime = typer.Option(None, "--to", "-t", help="End date (exclusive)."),
    config: Path = CONFIG_OPT,
):
    """Show distinct movement-type codes with counts and net units — use this to fill
    in the movement_types classification in tables.yml."""
    cfg = load_config(config)
    for name in MOVEMENT_SETS[scope]:
        df = _fetch(
            cfg, name,
            date_from=date_from.date() if date_from else None,
            date_to=date_to.date() if date_to else None,
        )
        agg = (
            df.groupby("movement_type")
            .agg(rows=("quantity", "size"), total_units=("quantity", "sum"),
                 min_qty=("quantity", "min"), max_qty=("quantity", "max"))
            .sort_values("rows", ascending=False)
            .reset_index()
        )
        _print(agg, f"{name} movement types", None)


@app.command()
def movements(
    scope: str = typer.Argument("all", help="fg | rm | all"),
    date_from: datetime = FROM_OPT,
    date_to: datetime = typer.Option(None, "--to", "-t", help="End (exclusive); defaults to now."),
    item: str = ITEM_OPT,
    warehouse: str = WH_OPT,
    csv: Path = CSV_OPT,
    config: Path = CONFIG_OPT,
):
    """Raw movement rows for drill-down — every transaction with type, qty, reference."""
    cfg = load_config(config)
    end = date_to or datetime.now()
    mov = _movements(cfg, scope, date_from, end, item, warehouse)
    cols = ["movement_date", "dataset", "job", "item_code", "warehouse",
            "movement_type", "quantity", "reference", "item_description"]
    cols = [c for c in cols if c in mov.columns]
    mov = mov.sort_values("movement_date")[cols]
    _print(mov, f"Movements {date_from:%Y-%m-%d %H:%M} → {end:%Y-%m-%d %H:%M}", csv)
    console.print(f"[dim]net: {mov['quantity'].sum():+,.1f} units across {len(mov)} transactions[/dim]")


@app.command(name="import-baseline")
def import_baseline(
    xlsx: Path = typer.Argument(..., help="iSync SOH export workbook (one sheet per stock type)."),
    as_of: datetime = typer.Option(..., "--as-of", help="Date the export was taken, YYYY-MM-DD."),
    out_dir: Path = typer.Option("baselines", "--out-dir", help="Directory for the baseline CSVs."),
    config: Path = CONFIG_OPT,
):
    """Convert an iSync SOH Excel export into baseline CSVs for reconcile --opening-csv.

    Expected sheets (any subset): RM -> rm_balance, Product_Stock -> fg_balance,
    WIP -> wip_balance. Column names must match the live views (tables.yml).
    """
    from .baseline import DEFAULT_SHEET_MAP, baseline_from_sheet

    cfg = load_config(config)
    xl = pd.ExcelFile(xlsx)
    matched = {s: d for s, d in DEFAULT_SHEET_MAP.items() if s in xl.sheet_names}
    if not matched:
        raise typer.BadParameter(
            f"No known sheets in {xlsx.name} (found {xl.sheet_names}, "
            f"expected any of {list(DEFAULT_SHEET_MAP)})"
        )
    out_dir.mkdir(parents=True, exist_ok=True)
    for sheet_name, ds_name in matched.items():
        out = baseline_from_sheet(xl.parse(sheet_name), cfg.datasets[ds_name], pd.Timestamp(as_of))
        path = out_dir / f"{ds_name}_{as_of:%Y%m%d}.csv"
        out.to_csv(path, index=False)
        console.print(
            f"{sheet_name} -> {path}: {len(out)} item/warehouse rows, "
            f"total quantity {out['quantity'].sum():,.1f}"
        )


@app.command()
def columns(
    names: list[str] = typer.Argument(..., help="Table/view names (without schema) to describe."),
):
    """List the columns and data types of specific tables or views."""
    from .db import get_engine, read_sql

    placeholders = ", ".join(f":t{i}" for i in range(len(names)))
    sql = (
        "SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, DATA_TYPE "
        "FROM INFORMATION_SCHEMA.COLUMNS "
        f"WHERE TABLE_NAME IN ({placeholders}) "
        "ORDER BY TABLE_SCHEMA, TABLE_NAME, ORDINAL_POSITION"
    )
    df = read_sql(get_engine(), sql, {f"t{i}": n for i, n in enumerate(names)})
    if df.empty:
        console.print("[red]No matching tables or views found.[/red]")
        raise typer.Exit(1)
    for (sch, tbl), grp in df.groupby(["TABLE_SCHEMA", "TABLE_NAME"], sort=False):
        table = Table(title=f"{sch}.{tbl}")
        table.add_column("column")
        table.add_column("type")
        for _, row in grp.iterrows():
            table.add_row(row["COLUMN_NAME"], row["DATA_TYPE"])
        console.print(table)


@app.command()
def discover(
    out: Path = typer.Option(None, "--out", help="Write suggested mapping YAML to this path."),
):
    """Inspect the live database and suggest a tables.yml mapping for the five datasets."""
    from .db import get_engine, read_sql
    from .introspect import SCHEMA_SQL, render_yaml, suggest_datasets

    schema = read_sql(get_engine(), SCHEMA_SQL)
    console.print(
        f"[dim]Found {schema.groupby(['table_schema', 'table_name']).ngroups} tables, "
        f"{len(schema)} columns.[/dim]"
    )
    suggestions = suggest_datasets(schema)

    table = Table(title="Dataset candidates")
    table.add_column("dataset")
    table.add_column("suggested table")
    table.add_column("score")
    table.add_column("unmapped columns")
    for dataset, best in suggestions.items():
        if not best:
            table.add_row(dataset, "[red]none found[/red]", "-", "-")
            continue
        gaps = [c for c, phys in best["columns"].items() if phys is None]
        table.add_row(dataset, best["table"], str(best["score"]), ", ".join(gaps) or "-")
    console.print(table)

    yaml_text = render_yaml(suggestions)
    if out:
        out.write_text(yaml_text)
        console.print(f"Wrote suggested datasets block to {out} — review, then merge into config/tables.yml.")
    else:
        console.print(yaml_text)
    console.print(
        "[dim]Next: verify each mapping, then check movement-type codes with e.g.\n"
        "SELECT DISTINCT <movement_type_col>, COUNT(*) FROM <movement_table> GROUP BY <movement_type_col>[/dim]"
    )


if __name__ == "__main__":
    app()
