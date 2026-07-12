"""Discover iSync tables and suggest a tables.yml mapping.

Fetches INFORMATION_SCHEMA metadata and scores each table against the five
logical datasets by name keywords, then guesses column mappings by pattern.
Pure functions over DataFrames so the scoring logic is testable offline.
"""

from __future__ import annotations

import pandas as pd

from .config import BALANCE_COLUMNS, MOVEMENT_COLUMNS

SCHEMA_SQL = """
SELECT c.TABLE_SCHEMA AS table_schema,
       c.TABLE_NAME   AS table_name,
       c.COLUMN_NAME  AS column_name,
       c.DATA_TYPE    AS data_type
FROM INFORMATION_SCHEMA.COLUMNS c
JOIN INFORMATION_SCHEMA.TABLES t
  ON t.TABLE_SCHEMA = c.TABLE_SCHEMA AND t.TABLE_NAME = c.TABLE_NAME
WHERE t.TABLE_TYPE IN ('BASE TABLE', 'VIEW')
ORDER BY c.TABLE_SCHEMA, c.TABLE_NAME, c.ORDINAL_POSITION
"""

# (kind keywords, subject keywords) — a table must hit both groups to qualify.
DATASET_KEYWORDS: dict[str, tuple[list[str], list[str]]] = {
    "fg_movements": (["movement", "movmnt", "transaction", "trans", "history"], ["finish", "product", "fg", "garment"]),
    "rm_movements": (["movement", "movmnt", "transaction", "trans", "history"], ["raw", "material", "rm", "fabric"]),
    "wip_balance": (["balance", "bal", "soh", "onhand", "on_hand", "stock"], ["wip", "progress", "work"]),
    "fg_balance": (["balance", "bal", "soh", "onhand", "on_hand", "stock"], ["finish", "product", "fg", "garment"]),
    "rm_balance": (["balance", "bal", "soh", "onhand", "on_hand", "stock"], ["raw", "material", "rm", "fabric"]),
}

# Ordered candidate substrings per canonical column; first hit wins.
COLUMN_PATTERNS: dict[str, list[str]] = {
    "item_code": ["itemcode", "item_code", "stockcode", "stock_code", "itemno", "item_no", "sku", "partno", "code"],
    "item_description": ["itemdesc", "description", "descr", "itemname", "item_name", "desc"],
    "warehouse": ["warehouse", "whse", "store", "location", "site", "branch"],
    "movement_date": ["movementdate", "movement_date", "transdate", "trans_date", "txndate", "docdate", "date"],
    "movement_type": ["movementtype", "movement_type", "transtype", "trans_type", "txntype", "doctype", "type"],
    "quantity": ["quantity", "qty"],
    "reference": ["reference", "docref", "docno", "doc_no", "document", "refno", "ref"],
    "balance_date": ["balancedate", "balance_date", "asatdate", "as_at", "snapshotdate", "perioddate", "date"],
}

DATE_TYPES = {"date", "datetime", "datetime2", "smalldatetime", "datetimeoffset"}
NUMERIC_TYPES = {"int", "bigint", "smallint", "decimal", "numeric", "float", "real", "money"}


def _score_table(table_name: str, dataset: str) -> int:
    name = table_name.lower()
    kind_kw, subject_kw = DATASET_KEYWORDS[dataset]
    kind_hits = sum(kw in name for kw in kind_kw)
    subject_hits = sum(kw in name for kw in subject_kw)
    if kind_hits == 0 or subject_hits == 0:
        return 0
    return kind_hits + subject_hits


def _guess_column(canonical: str, columns: pd.DataFrame) -> str | None:
    """Pick the best physical column for a canonical name, honoring dtype."""
    for pattern in COLUMN_PATTERNS[canonical]:
        for _, col in columns.iterrows():
            name, dtype = col["column_name"].lower(), col["data_type"].lower()
            if pattern not in name:
                continue
            if canonical in ("movement_date", "balance_date") and dtype not in DATE_TYPES:
                continue
            if canonical == "quantity" and dtype not in NUMERIC_TYPES:
                continue
            return col["column_name"]
    return None


def suggest_datasets(schema: pd.DataFrame) -> dict[str, dict]:
    """Map each logical dataset to its best-scoring table and column guesses.

    Returns {dataset: {table, score, columns: {canonical: physical|None}}};
    datasets with no qualifying table map to {}.
    """
    out: dict[str, dict] = {}
    tables = schema.groupby(["table_schema", "table_name"], sort=False)
    for dataset, spec_kind in [(d, "movement" if d.endswith("movements") else "balance") for d in DATASET_KEYWORDS]:
        required = MOVEMENT_COLUMNS if spec_kind == "movement" else BALANCE_COLUMNS
        best: dict = {}
        for (sch, tbl), cols in tables:
            score = _score_table(tbl, dataset)
            if score <= best.get("score", 0):
                continue
            guesses = {canon: _guess_column(canon, cols) for canon in sorted(required)}
            best = {"table": f"{sch}.{tbl}", "score": score, "columns": guesses}
        out[dataset] = best
    return out


def render_yaml(suggestions: dict[str, dict]) -> str:
    """Render suggestions as a tables.yml `datasets:` block, flagging gaps."""
    lines = ["datasets:"]
    for dataset, best in suggestions.items():
        kind = "movement" if dataset.endswith("movements") else "balance"
        if not best:
            lines += [f"  # {dataset}: NO CANDIDATE TABLE FOUND — map manually", ""]
            continue
        lines += [
            f"  {dataset}:",
            f"    kind: {kind}",
            f"    table: {best['table']}",
            "    columns:",
        ]
        for canon, physical in best["columns"].items():
            if physical:
                lines.append(f"      {canon}: {physical}")
            else:
                lines.append(f"      {canon}: FIXME  # no matching column found")
        lines.append("")
    return "\n".join(lines)
