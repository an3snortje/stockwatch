import pandas as pd
import pytest

from stockwatch.analysis import detect_anomalies, movement_summary, reconcile
from stockwatch.config import Config, DatasetConfig


@pytest.fixture
def cfg():
    return Config(
        datasets={},
        movement_types={
            "receipts": ["GRN", "PRODUCTION_IN"],
            "issues": ["ISSUE", "SALES_DISPATCH"],
            "adjustments": ["STOCK_ADJ"],
        },
        issues_stored_positive=True,
        dormant_days=90,
        outlier_zscore=3.0,
        variance_tolerance=0.5,
    )


def _mov(rows):
    df = pd.DataFrame(
        rows,
        columns=["item_code", "warehouse", "movement_date", "movement_type", "quantity", "reference"],
    )
    df["movement_date"] = pd.to_datetime(df["movement_date"])
    df["item_description"] = ""
    return df


def _bal(rows):
    df = pd.DataFrame(rows, columns=["item_code", "warehouse", "balance_date", "quantity"])
    df["balance_date"] = pd.to_datetime(df["balance_date"])
    df["item_description"] = ""
    return df


def test_movement_summary_nets_receipts_and_issues(cfg):
    mov = _mov(
        [
            ("FG-001", "WH1", "2026-06-01", "GRN", 100, "GRN001"),
            ("FG-001", "WH1", "2026-06-10", "ISSUE", 30, "ISS001"),
            ("FG-001", "WH1", "2026-06-15", "STOCK_ADJ", -5, "ADJ001"),
        ]
    )
    out = movement_summary(mov, cfg)
    assert len(out) == 1
    row = out.iloc[0]
    assert row["receipt"] == 100
    assert row["issue"] == -30  # stored positive, negated
    assert row["adjustment"] == -5
    assert row["net"] == 65


def test_reconcile_flags_variance(cfg):
    opening = _bal([("FG-001", "WH1", "2026-06-01", 50)])
    closing = _bal([("FG-001", "WH1", "2026-07-01", 130)])
    mov = _mov([("FG-001", "WH1", "2026-06-05", "GRN", 70, "GRN002")])
    out = reconcile(opening, mov, closing, cfg)
    row = out.iloc[0]
    assert row["expected_closing"] == 120
    assert row["variance"] == 10
    assert not row["within_tolerance"]


def test_reconcile_within_tolerance(cfg):
    opening = _bal([("RM-001", "WH1", "2026-06-01", 10)])
    closing = _bal([("RM-001", "WH1", "2026-07-01", 10.3)])
    mov = _mov([("RM-001", "WH1", "2026-06-05", "GRN", 0, "X")])
    out = reconcile(opening, mov, closing, cfg)
    assert out.iloc[0]["within_tolerance"]


def test_detect_negative_balance_and_dormant(cfg):
    mov = _mov([("FG-001", "WH1", "2026-01-02", "GRN", 10, "G")])
    bal = _bal([("FG-001", "WH1", "2026-06-30", -4)])
    out = detect_anomalies(mov, bal, cfg, as_of=pd.Timestamp("2026-06-30"))
    kinds = set(out["kind"])
    assert "negative_balance" in kinds
    assert "dormant_item" in kinds


def test_detect_outlier_movement(cfg):
    rows = [("RM-001", "WH1", f"2026-06-{d:02d}", "ISSUE", 10, f"I{d}") for d in range(1, 10)]
    rows.append(("RM-001", "WH1", "2026-06-15", "ISSUE", 500, "BIG"))
    out = detect_anomalies(_mov(rows), None, cfg, as_of=pd.Timestamp("2026-06-30"))
    outliers = out[out["kind"] == "outlier_movement"]
    assert len(outliers) == 1
    assert "500" in outliers.iloc[0]["detail"]


def test_apply_exclusions_rules():
    from stockwatch.analysis import apply_exclusions

    mov = _mov(
        [
            ("ESCT434A|34", "Finished Goods", "2026-07-07", "RECEIVED", 320, "PPO-083220"),
            ("ESCT434A|34", "Finished Goods", "2026-07-07", "RECEIVED", 100, "GRN-000123"),
            ("HEM-P750|102", "HAMISA JHB", "2026-07-07", "STOCK ADJ IN - STOCK ADJUST- IN", 925, "D50746"),
            ("HEM-P750|102", "Prod GRV", "2026-07-09", "RECEIVED", 275, "PPO-082930"),
        ]
    )
    rules = [
        {"movement_type": "RECEIVED", "warehouse": "Finished Goods", "reference_prefix": "PPO-"},
        {"movement_type": "STOCK ADJ IN - STOCK ADJUST- IN", "warehouse": "HAMISA JHB"},
    ]
    kept, excluded = apply_exclusions(mov, rules)
    assert len(excluded) == 2
    assert set(excluded["quantity"]) == {320, 925}
    # GRN receipt at Finished Goods and PPO receipt at Prod GRV are kept
    assert len(kept) == 2
    assert set(kept["quantity"]) == {100, 275}


def test_apply_exclusions_no_rules_is_passthrough():
    from stockwatch.analysis import apply_exclusions

    mov = _mov([("A", "W", "2026-07-01", "RECEIVED", 5, "X")])
    kept, excluded = apply_exclusions(mov, [])
    assert len(kept) == 1 and len(excluded) == 0
