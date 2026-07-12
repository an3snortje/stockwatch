"""Build parameterized SELECTs from the dataset config.

Identifiers come from config/tables.yml and are validated against a safe
character set at load time; date/item filters are bound parameters.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from .config import ColumnMap, DatasetConfig


def _bracket(identifier: str) -> str:
    return ".".join(f"[{part}]" for part in identifier.split("."))


def _expr(mapping: ColumnMap) -> str:
    """SQL expression for a column mapping (column, composite key, product, or NULL)."""
    if mapping is None:
        return "NULL"
    if isinstance(mapping, list):
        parts = ", ".join(_bracket(c) for c in mapping)
        return f"CONCAT_WS('|', {parts})"
    if isinstance(mapping, dict):
        factors = (
            _bracket(c) if isinstance(c, str) else repr(float(c))
            for c in mapping["product"]
        )
        return "(" + " * ".join(factors) + ")"
    return _bracket(mapping)


def build_select(
    ds: DatasetConfig,
    date_from: date | None = None,
    date_to: date | None = None,
    item_code: str | None = None,
    warehouse: str | None = None,
) -> tuple[str, dict]:
    select_list = ", ".join(
        f"{_expr(src)} AS [{canon}]" for canon, src in ds.columns.items()
    )
    sql = f"SELECT {select_list} FROM {_bracket(ds.table)}"

    where: list[str] = []
    params: dict = {}
    if ds.has_date:
        date_col = _expr(ds.columns[ds.date_column])
        if date_from is not None:
            where.append(f"{date_col} >= :date_from")
            params["date_from"] = date_from
        if date_to is not None:
            where.append(f"{date_col} < :date_to_excl")
            params["date_to_excl"] = date_to
    if item_code:
        where.append(f"{_expr(ds.columns['item_code'])} = :item_code")
        params["item_code"] = item_code
    if warehouse:
        if ds.columns.get("warehouse") is None:
            raise ValueError(f"Dataset {ds.name} has no warehouse column to filter on")
        where.append(f"{_expr(ds.columns['warehouse'])} = :warehouse")
        params["warehouse"] = warehouse
    if where:
        sql += " WHERE " + " AND ".join(where)
    return sql, params


def normalize(df: pd.DataFrame, ds: DatasetConfig) -> pd.DataFrame:
    """Coerce canonical dtypes after fetch; fill gaps from null mappings."""
    df = df.copy()
    df[ds.date_column] = pd.to_datetime(df[ds.date_column])
    if ds.kind == "balance" and not ds.has_date:
        # Current-state view: stamp the fetch time as the snapshot date.
        df[ds.date_column] = pd.Timestamp.now().floor("s")
    df["quantity"] = pd.to_numeric(df["quantity"], errors="coerce").fillna(0.0)
    for col in ("item_code", "warehouse"):
        df[col] = df[col].fillna("-").astype(str).str.strip()
    # Strip whitespace around composite-key separators too — source columns
    # can carry trailing spaces ("M/OLIVE |30" vs "M/OLIVE|30").
    df["item_code"] = df["item_code"].str.replace(r"\s*\|\s*", "|", regex=True)
    for col in ("item_description", "reference"):
        if col in df.columns:
            df[col] = df[col].fillna("").astype(str).str.strip()
    if "movement_type" in df.columns:
        df["movement_type"] = df["movement_type"].astype(str).str.strip().str.upper()
    return df
