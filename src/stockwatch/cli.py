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


def _movements(cfg: Config, scope: str, date_from: date, date_to: date, item: str | None, wh: str | None) -> pd.DataFrame:
    names = MOVEMENT_SETS[scope]
    frames = [
        _fetch(cfg, n, date_from=date_from, date_to=date_to, item_code=item, warehouse=wh).assign(dataset=n)
        for n in names
    ]
    return pd.concat(frames, ignore_index=True)


def _snapshot_at(balances: pd.DataFrame, ts: pd.Timestamp) -> pd.DataFrame:
    """Latest balance row per item/warehouse on or before ts."""
    eligible = balances[balances["balance_date"] <= ts]
    return (
        eligible.sort_values("balance_date").groupby(analysis.KEY, as_index=False).tail(1)
    )


def _print(df: pd.DataFrame, title: str, csv: Path | None) -> None:
    if csv:
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
    date_from: datetime = FROM_OPT,
    date_to: datetime = TO_OPT,
    item: str = ITEM_OPT,
    warehouse: str = WH_OPT,
    csv: Path = CSV_OPT,
    config: Path = CONFIG_OPT,
):
    """Check that opening balance + movements explains the closing balance."""
    if scope not in ("fg", "rm"):
        raise typer.BadParameter("reconcile works per store: choose fg or rm")
    cfg = load_config(config)
    movement_ds = MOVEMENT_SETS[scope][0]
    balances = _fetch(cfg, BALANCE_FOR[movement_ds], date_to=date_to.date(), item_code=item, warehouse=warehouse)
    opening = _snapshot_at(balances, pd.Timestamp(date_from))
    closing = _snapshot_at(balances, pd.Timestamp(date_to))
    mov = _movements(cfg, scope, date_from.date(), date_to.date(), item, warehouse)
    result = analysis.reconcile(opening, mov, closing, cfg)
    _print(result, f"Reconciliation {scope.upper()} {date_from:%Y-%m-%d} → {date_to:%Y-%m-%d}", csv)
    _print_lines(explain.explain_reconciliation(result))


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
    balance_frames = [
        _fetch(cfg, BALANCE_FOR[n], date_to=date_to.date(), item_code=item, warehouse=warehouse)
        for n in MOVEMENT_SETS[scope]
    ]
    balances = _snapshot_at(pd.concat(balance_frames, ignore_index=True), pd.Timestamp(date_to))
    result = analysis.detect_anomalies(mov, balances, cfg, as_of=pd.Timestamp(date_to))
    _print(result, "Anomalies", csv)
    _print_lines(explain.explain_anomalies(result))


@app.command()
def snapshot(
    dataset: str = typer.Argument(..., help="wip_balance | fg_balance | rm_balance"),
    as_of: datetime = typer.Option(..., "--as-of", help="Snapshot date, YYYY-MM-DD."),
    warehouse: str = WH_OPT,
    csv: Path = CSV_OPT,
    config: Path = CONFIG_OPT,
):
    """Latest balance per item/warehouse as of a date (incl. WIP)."""
    cfg = load_config(config)
    if cfg.datasets[dataset].kind != "balance":
        raise typer.BadParameter(f"{dataset} is not a balance dataset")
    df = _fetch(cfg, dataset, date_to=as_of.date() + pd.Timedelta(days=1), warehouse=warehouse)
    result = _snapshot_at(df, pd.Timestamp(as_of))
    _print(result.sort_values("quantity", ascending=False), f"{dataset} as of {as_of:%Y-%m-%d}", csv)


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
