"""Turn analysis DataFrames into plain-English explanations."""

from __future__ import annotations

import pandas as pd


def _item(row: pd.Series) -> str:
    desc = str(row.get("item_description", "") or "").strip()
    label = f"{row['item_code']} ({desc})" if desc else str(row["item_code"])
    return f"{label} in {row['warehouse']}"


def explain_summary(summary: pd.DataFrame, top_n: int = 10) -> list[str]:
    if summary.empty:
        return ["No movements found in the selected period."]
    lines: list[str] = []
    totals = summary[["receipt", "issue", "adjustment", "net"]].sum()
    lines.append(
        f"Across {summary['item_code'].nunique()} items and "
        f"{summary['warehouse'].nunique()} warehouse(s): received "
        f"{totals['receipt']:,.0f}, issued {abs(totals['issue']):,.0f}, "
        f"adjusted {totals['adjustment']:+,.0f}, for a net stock change of "
        f"{totals['net']:+,.0f} units."
    )
    movers = (
        summary.groupby(["item_code", "warehouse"], as_index=False)
        .agg(net=("net", "sum"), item_description=("item_code", "first"))
        .sort_values("net", key=lambda s: s.abs(), ascending=False)
        .head(top_n)
    )
    for _, row in movers.iterrows():
        verb = "built up" if row["net"] > 0 else "drew down"
        lines.append(f"{_item(row)} {verb} {abs(row['net']):,.0f} units net.")
    return lines


def explain_reconciliation(rec: pd.DataFrame, top_n: int = 10) -> list[str]:
    if rec.empty:
        return ["Nothing to reconcile for the selected period."]
    bad = rec[~rec["within_tolerance"]]
    lines = [
        f"Reconciled {len(rec)} item/warehouse combinations: "
        f"{len(rec) - len(bad)} balance, {len(bad)} show unexplained variance."
    ]
    for _, row in bad.head(top_n).iterrows():
        direction = "more" if row["variance"] > 0 else "less"
        lines.append(
            f"{_item(row)}: closing balance is {row['closing_qty']:,.1f} but opening "
            f"{row['opening_qty']:,.1f} plus recorded movements {row['net_movement']:+,.1f} "
            f"predicts {row['expected_closing']:,.1f} — {abs(row['variance']):,.1f} units "
            f"{direction} than the movements explain. Check for unposted transactions, "
            f"stocktake adjustments captured outside the movement tables, or timing cut-off."
        )
    return lines


def explain_anomalies(anomalies: pd.DataFrame, top_n: int = 20) -> list[str]:
    if anomalies.empty:
        return ["No anomalies detected."]
    templates = {
        "negative_balance": "{item} has a NEGATIVE balance: {detail}. Stock was issued "
        "that the system never received — check GRN backlogs and issue timing.",
        "outlier_movement": "{item} had an unusually large movement: {detail}. Verify the "
        "document capture (possible unit-of-measure or keying error).",
        "dormant_item": "{item} is dormant: {detail}. Candidate for stock count, "
        "write-off review or reallocation.",
    }
    lines: list[str] = []
    for _, row in anomalies.head(top_n).iterrows():
        template = templates.get(row["kind"], "{item}: {detail}")
        lines.append(template.format(item=_item(row), detail=row["detail"]))
    remaining = len(anomalies) - top_n
    if remaining > 0:
        lines.append(f"...and {remaining} more finding(s); export with --csv for the full list.")
    return lines
