"""Convert iSync SOH exports (Excel, one sheet per stock type) into
canonical baseline snapshots usable with `reconcile --opening-csv`.

Reuses the column mappings from tables.yml, applied pandas-side.
"""

from __future__ import annotations

import pandas as pd

from .config import ColumnMap, DatasetConfig

KEY = ["item_code", "warehouse"]

DEFAULT_SHEET_MAP = {
    "RM": "rm_balance",
    "Product_Stock": "fg_balance",
    "WIP": "wip_balance",
}


def _cell_str(value) -> str:
    """Stringify a key cell; Excel turns '58' into 58.0 — undo that."""
    if pd.isna(value):
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _series(df: pd.DataFrame, mapping: ColumnMap, context: str) -> pd.Series:
    if mapping is None:
        return pd.Series(pd.NA, index=df.index)
    if isinstance(mapping, list):
        _require(df, mapping, context)
        return df[mapping].apply(lambda r: "|".join(_cell_str(v) for v in r), axis=1)
    if isinstance(mapping, dict):
        cols = [f for f in mapping["product"] if isinstance(f, str)]
        _require(df, cols, context)
        out = pd.Series(1.0, index=df.index)
        for factor in mapping["product"]:
            if isinstance(factor, str):
                out = out * pd.to_numeric(df[factor], errors="coerce")
            else:
                out = out * factor
        return out
    _require(df, [mapping], context)
    return df[mapping]


def _require(df: pd.DataFrame, cols: list[str], context: str) -> None:
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise KeyError(f"{context}: sheet is missing column(s) {missing}")


def baseline_from_sheet(
    sheet: pd.DataFrame, ds: DatasetConfig, as_of: pd.Timestamp
) -> pd.DataFrame:
    """Aggregate one export sheet into a per-item/warehouse baseline."""
    df = pd.DataFrame(
        {
            canon: _series(sheet, ds.columns[canon], f"{ds.name}.{canon}")
            for canon in ("item_code", "item_description", "warehouse", "quantity")
        }
    )
    for col in ("item_code", "warehouse"):
        df[col] = df[col].map(_cell_str)
    df["item_description"] = df["item_description"].map(_cell_str)
    df["quantity"] = pd.to_numeric(df["quantity"], errors="coerce").fillna(0.0)
    # Exports often carry blank filler rows; a blank composite key is all '|'s.
    df = df[df["item_code"].str.replace("|", "", regex=False).str.strip() != ""]
    df.loc[df["warehouse"] == "", "warehouse"] = "-"

    out = df.groupby(KEY, as_index=False).agg(
        item_description=("item_description", "first"),
        quantity=("quantity", "sum"),
    )
    out.insert(2, "balance_date", as_of)
    return out
