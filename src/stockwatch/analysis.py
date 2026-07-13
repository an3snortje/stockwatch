"""Pure-pandas analysis of inventory movements and balances.

All functions take canonical-column DataFrames (see queries.normalize) and
return DataFrames, so they are unit-testable without a database.
"""

from __future__ import annotations

import pandas as pd

from .config import Config

KEY = ["item_code", "warehouse"]


def classify_movements(movements: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    """Add `direction` (receipt/issue/adjustment/other) and `signed_qty`."""
    df = movements.copy()
    type_to_dir: dict[str, str] = {}
    for direction, types in cfg.movement_types.items():
        # config uses plural bucket names: receipts / issues / adjustments
        for t in types:
            type_to_dir[t.upper()] = direction.rstrip("s")
    df["direction"] = df["movement_type"].map(type_to_dir).fillna("other")

    df["signed_qty"] = df["quantity"]
    if cfg.issues_stored_positive:
        is_issue = df["direction"] == "issue"
        df.loc[is_issue, "signed_qty"] = -df.loc[is_issue, "quantity"].abs()
    return df


def apply_exclusions(movements: pd.DataFrame, rules: list[dict]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split movements into (kept, excluded) using reconcile_exclusions rules.

    Each rule's conditions are ANDed; a row matching any rule is excluded.
    Conditions: movement_type (exact, case-insensitive), warehouse (exact),
    reference_prefix (startswith).
    """
    if not rules or movements.empty:
        return movements, movements.iloc[0:0]
    excluded = pd.Series(False, index=movements.index)
    for rule in rules:
        match = pd.Series(True, index=movements.index)
        if "movement_type" in rule:
            match &= movements["movement_type"] == str(rule["movement_type"]).upper()
        if "warehouse" in rule:
            match &= movements["warehouse"] == rule["warehouse"]
        if "reference_prefix" in rule:
            match &= movements["reference"].str.startswith(str(rule["reference_prefix"]))
        excluded |= match
    return movements[~excluded], movements[excluded]


def movement_summary(movements: pd.DataFrame, cfg: Config, freq: str = "MS") -> pd.DataFrame:
    """Receipts / issues / adjustments / net per item, warehouse and period."""
    df = classify_movements(movements, cfg)
    df["period"] = df["movement_date"].dt.to_period(
        {"MS": "M", "W": "W", "D": "D"}.get(freq, "M")
    ).dt.start_time

    pivot = (
        df.pivot_table(
            index=KEY + ["period"],
            columns="direction",
            values="signed_qty",
            aggfunc="sum",
            fill_value=0.0,
        )
        .reset_index()
    )
    pivot.columns.name = None
    for col in ("receipt", "issue", "adjustment", "other"):
        if col not in pivot.columns:
            pivot[col] = 0.0
    pivot["net"] = pivot[["receipt", "issue", "adjustment", "other"]].sum(axis=1)
    return pivot.sort_values(KEY + ["period"]).reset_index(drop=True)


def category_summary(movements: pd.DataFrame, cfg: Config, by: str = "category") -> pd.DataFrame:
    """Receipts / issues / adjustments / net per grouping column (e.g. cost type,
    product), with net Rand value when the movements carry one."""
    if by not in movements.columns:
        raise KeyError(f"movements have no {by!r} column — map it in tables.yml")
    df = classify_movements(movements, cfg)
    df[by] = df[by].astype(str).str.strip().replace("", "(unspecified)")

    pivot = df.pivot_table(
        index=by, columns="direction", values="signed_qty", aggfunc="sum", fill_value=0.0
    )
    pivot.columns.name = None
    for col in ("receipt", "issue", "adjustment", "other"):
        if col not in pivot.columns:
            pivot[col] = 0.0
    pivot["net"] = pivot[["receipt", "issue", "adjustment", "other"]].sum(axis=1)
    pivot["movements"] = df.groupby(by).size()
    if "value" in df.columns:
        pivot["net_value"] = df.groupby(by)["value"].sum()
    cols = ["movements", "receipt", "issue", "adjustment", "other", "net"]
    if "net_value" in pivot.columns:
        cols.append("net_value")
    return (
        pivot[cols]
        .reset_index()
        .sort_values("net", key=lambda s: s.abs(), ascending=False)
        .reset_index(drop=True)
    )


def reconcile(
    opening: pd.DataFrame,
    movements: pd.DataFrame,
    closing: pd.DataFrame,
    cfg: Config,
) -> pd.DataFrame:
    """opening + net movement vs closing, per item/warehouse.

    `opening`/`closing` are balance snapshots (canonical balance columns);
    `movements` covers the interval between the two snapshot dates.
    """
    mov = classify_movements(movements, cfg)
    net = mov.groupby(KEY)["signed_qty"].sum().rename("net_movement")
    open_qty = opening.groupby(KEY)["quantity"].sum().rename("opening_qty")
    close_qty = closing.groupby(KEY)["quantity"].sum().rename("closing_qty")

    desc = (
        pd.concat([opening, closing, movements])
        .dropna(subset=["item_description"])
        .drop_duplicates("item_code")
        .set_index("item_code")["item_description"]
        if "item_description" in closing.columns
        else pd.Series(dtype=str)
    )

    rec = (
        pd.concat([open_qty, net, close_qty], axis=1)
        .fillna(0.0)
        .reset_index()
    )
    rec["expected_closing"] = rec["opening_qty"] + rec["net_movement"]
    rec["variance"] = rec["closing_qty"] - rec["expected_closing"]
    rec["within_tolerance"] = rec["variance"].abs() <= cfg.variance_tolerance
    rec["item_description"] = rec["item_code"].map(desc).fillna("")
    return rec.sort_values("variance", key=lambda s: s.abs(), ascending=False).reset_index(
        drop=True
    )


def classify_chain(period_variances: pd.DataFrame, tolerance: float = 0.5) -> pd.DataFrame:
    """Classify each item's variance across a chain of consecutive periods.

    Input: long DataFrame with columns [item_code, warehouse, period,
    variance, item_description]. A `period` is one reconciled interval
    (e.g. "0630->0707"). Output: one row per item/warehouse with the
    per-period variances plus:
      - gross_variance: sum of |variance| across periods (total churn)
      - net_variance:   signed sum across periods (what survives to the end)
      - classification:
          timing     -> churned but nets to ~0 (reverses across boundaries)
          persistent -> net survives and is most of the churn (real, one-directional)
          mixed      -> net survives but with reversals too (partly real, partly timing)
    """
    df = period_variances.copy()
    grp = df.groupby(KEY)
    out = grp.agg(
        item_description=("item_description", "first"),
        gross_variance=("variance", lambda s: s.abs().sum()),
        net_variance=("variance", "sum"),
        periods_flagged=("variance", lambda s: (s.abs() > tolerance).sum()),
    ).reset_index()

    def _classify(row: pd.Series) -> str:
        if row["gross_variance"] <= tolerance:
            return "clean"
        if abs(row["net_variance"]) <= tolerance:
            return "timing"
        # net survives; if it accounts for most of the churn it's one-directional
        return "persistent" if abs(row["net_variance"]) >= row["gross_variance"] - tolerance else "mixed"

    out["classification"] = out.apply(_classify, axis=1)

    # widen per-period variances into columns for the report
    wide = period_variances.pivot_table(
        index=KEY, columns="period", values="variance", aggfunc="sum", fill_value=0.0
    )
    wide.columns = [str(c) for c in wide.columns]
    wide.columns.name = None
    out = out.merge(wide.reset_index(), on=KEY, how="left")
    order = {"persistent": 0, "mixed": 1, "timing": 2, "clean": 3}
    return out.sort_values(
        ["classification", "net_variance"],
        key=lambda s: s.map(order) if s.name == "classification" else s.abs(),
        ascending=[True, False],
    ).reset_index(drop=True)


def detect_anomalies(
    movements: pd.DataFrame,
    balances: pd.DataFrame | None,
    cfg: Config,
    as_of: pd.Timestamp | None = None,
) -> pd.DataFrame:
    """Return one row per finding: negative balances, outlier movements, dormant items."""
    findings: list[dict] = []
    mov = classify_movements(movements, cfg)
    as_of = as_of or mov["movement_date"].max()

    if balances is not None and not balances.empty:
        latest = balances.sort_values("balance_date").groupby(KEY).tail(1)
        for _, row in latest[latest["quantity"] < 0].iterrows():
            findings.append(
                {
                    "kind": "negative_balance",
                    "item_code": row["item_code"],
                    "warehouse": row["warehouse"],
                    "detail": f"balance {row['quantity']:.1f} on {row['balance_date']:%Y-%m-%d}",
                    "value": row["quantity"],
                }
            )

    # Robust (median/MAD) z-score: a plain mean/std lets one huge movement
    # inflate the std enough to hide itself.
    def _robust_stats(s: pd.Series) -> pd.Series:
        med = s.median()
        dev = (s - med).abs()
        mad = dev.median()
        scale = 1.4826 * mad if mad > 0 else 1.2533 * dev.mean()
        return pd.Series({"median": med, "scale": scale, "count": len(s)})

    stats = mov.groupby(KEY)["quantity"].apply(_robust_stats).unstack()
    mov = mov.join(stats, on=KEY)
    eligible = mov[(mov["count"] >= 5) & (mov["scale"] > 0)]
    z = (eligible["quantity"] - eligible["median"]).abs() / eligible["scale"]
    for _, row in eligible[z > cfg.outlier_zscore].iterrows():
        findings.append(
            {
                "kind": "outlier_movement",
                "item_code": row["item_code"],
                "warehouse": row["warehouse"],
                "detail": (
                    f"{row['movement_type']} of {row['quantity']:.1f} on "
                    f"{row['movement_date']:%Y-%m-%d} (ref {row['reference']}), "
                    f"typical {row['median']:.1f} (robust spread {row['scale']:.1f})"
                ),
                "value": row["quantity"],
            }
        )

    last_move = mov.groupby(KEY)["movement_date"].max()
    dormant = last_move[(as_of - last_move).dt.days > cfg.dormant_days]
    for (item, wh), last in dormant.items():
        findings.append(
            {
                "kind": "dormant_item",
                "item_code": item,
                "warehouse": wh,
                "detail": f"no movement since {last:%Y-%m-%d} "
                f"({(as_of - last).days} days as of {as_of:%Y-%m-%d})",
                "value": float((as_of - last).days),
            }
        )

    return pd.DataFrame(findings, columns=["kind", "item_code", "warehouse", "detail", "value"])
