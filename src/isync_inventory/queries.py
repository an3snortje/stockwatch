"""Build parameterized SELECTs from the dataset config.

Identifiers come from config/tables.yml and are validated against a safe
character set at load time; date/item filters are bound parameters.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from .config import DatasetConfig


def _bracket(identifier: str) -> str:
    return ".".join(f"[{part}]" for part in identifier.split("."))


def build_select(
    ds: DatasetConfig,
    date_from: date | None = None,
    date_to: date | None = None,
    item_code: str | None = None,
    warehouse: str | None = None,
) -> tuple[str, dict]:
    select_list = ", ".join(
        f"{_bracket(src)} AS [{canon}]" for canon, src in ds.columns.items()
    )
    sql = f"SELECT {select_list} FROM {_bracket(ds.table)}"

    where: list[str] = []
    params: dict = {}
    date_col = _bracket(ds.columns[ds.date_column])
    if date_from is not None:
        where.append(f"{date_col} >= :date_from")
        params["date_from"] = date_from
    if date_to is not None:
        where.append(f"{date_col} < :date_to_excl")
        params["date_to_excl"] = date_to
    if item_code:
        where.append(f"{_bracket(ds.columns['item_code'])} = :item_code")
        params["item_code"] = item_code
    if warehouse:
        where.append(f"{_bracket(ds.columns['warehouse'])} = :warehouse")
        params["warehouse"] = warehouse
    if where:
        sql += " WHERE " + " AND ".join(where)
    return sql, params


def normalize(df: pd.DataFrame, ds: DatasetConfig) -> pd.DataFrame:
    """Coerce canonical dtypes after fetch."""
    df = df.copy()
    df[ds.date_column] = pd.to_datetime(df[ds.date_column])
    df["quantity"] = pd.to_numeric(df["quantity"], errors="coerce").fillna(0.0)
    for col in ("item_code", "warehouse"):
        df[col] = df[col].astype(str).str.strip()
    if "movement_type" in df.columns:
        df["movement_type"] = df["movement_type"].astype(str).str.strip().str.upper()
    return df
