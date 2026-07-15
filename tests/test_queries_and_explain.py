import pandas as pd
import pytest
from datetime import date

from stockwatch.config import Config, DatasetConfig, load_config
from stockwatch.explain import explain_anomalies, explain_reconciliation, explain_summary
from stockwatch.queries import build_select


@pytest.fixture
def ds():
    return DatasetConfig(
        name="fg_movements",
        kind="movement",
        table="dbo.FinishedProductMovements",
        columns={
            "item_code": "ItemCode",
            "item_description": "ItemDescription",
            "warehouse": "Warehouse",
            "movement_date": "MovementDate",
            "movement_type": "MovementType",
            "quantity": "Quantity",
            "reference": "DocumentRef",
        },
    )


def test_build_select_brackets_and_binds(ds):
    sql, params = build_select(ds, date_from=date(2026, 6, 1), date_to=date(2026, 7, 1), item_code="FG-001")
    assert "FROM [dbo].[FinishedProductMovements]" in sql
    assert "[ItemCode] AS [item_code]" in sql
    assert "[MovementDate] >= :date_from" in sql
    assert "[MovementDate] < :date_to_excl" in sql
    assert params == {"date_from": date(2026, 6, 1), "date_to_excl": date(2026, 7, 1), "item_code": "FG-001"}


def test_build_select_no_filters(ds):
    sql, params = build_select(ds)
    assert "WHERE" not in sql
    assert params == {}


def test_build_select_exclude_where():
    from stockwatch.config import DatasetConfig

    ds = DatasetConfig(
        name="rm_balance",
        kind="balance",
        table="Reporting.vFabricListing",
        columns={
            "item_code": "Stock No",
            "item_description": "Stock Desc",
            "warehouse": "Location",
            "balance_date": None,
            "quantity": "SOH Units",
        },
        exclude_where=[{"column": "ItemStatus", "equals": "Missing"}],
    )
    sql, params = build_select(ds)
    assert "([ItemStatus] IS NULL OR [ItemStatus] <> :exclude_0)" in sql
    assert params == {"exclude_0": "Missing"}


def test_config_rejects_bad_exclude_where(tmp_path):
    bad = tmp_path / "tables.yml"
    bad.write_text(
        """
datasets:
  rm_balance:
    kind: balance
    table: Reporting.vFabricListing
    columns:
      item_code: Stock No
      item_description: Stock Desc
      warehouse: Location
      balance_date: null
      quantity: SOH Units
    exclude_where:
      - column: "Item; DROP--"
        equals: Missing
movement_types: {}
"""
    )
    with pytest.raises(ValueError, match="Unsafe SQL identifier"):
        load_config(bad)


def test_load_config_rejects_unsafe_identifier(tmp_path):
    bad = tmp_path / "tables.yml"
    bad.write_text(
        """
datasets:
  fg_movements:
    kind: movement
    table: "dbo.Movements; DROP TABLE x--"
    columns:
      item_code: ItemCode
      item_description: D
      warehouse: W
      movement_date: MD
      movement_type: MT
      quantity: Q
      reference: R
"""
    )
    with pytest.raises(ValueError, match="Unsafe SQL identifier"):
        load_config(bad)


def test_load_config_reads_bundled_default():
    cfg = load_config("config/tables.yml")
    assert set(cfg.datasets) == {"fg_movements", "rm_movements", "wip_balance", "fg_balance", "rm_balance"}
    assert cfg.datasets["wip_balance"].kind == "balance"


def test_explanations_produce_readable_lines():
    summary = pd.DataFrame(
        {
            "item_code": ["FG-001"],
            "warehouse": ["WH1"],
            "period": [pd.Timestamp("2026-06-01")],
            "receipt": [100.0],
            "issue": [-30.0],
            "adjustment": [0.0],
            "other": [0.0],
            "net": [70.0],
        }
    )
    lines = explain_summary(summary)
    assert any("net stock change of +70" in line for line in lines)

    rec = pd.DataFrame(
        {
            "item_code": ["FG-001"],
            "warehouse": ["WH1"],
            "item_description": ["Navy Conti Suit"],
            "opening_qty": [50.0],
            "net_movement": [70.0],
            "closing_qty": [130.0],
            "expected_closing": [120.0],
            "variance": [10.0],
            "within_tolerance": [False],
        }
    )
    lines = explain_reconciliation(rec)
    assert any("10.0 units more" in line for line in lines)

    anomalies = pd.DataFrame(
        {
            "kind": ["negative_balance"],
            "item_code": ["RM-001"],
            "warehouse": ["WH1"],
            "detail": ["balance -4.0 on 2026-06-30"],
            "value": [-4.0],
        }
    )
    lines = explain_anomalies(anomalies)
    assert any("NEGATIVE balance" in line for line in lines)


def test_build_select_composite_and_null_mappings():
    from stockwatch.config import DatasetConfig

    ds = DatasetConfig(
        name="fg_balance",
        kind="balance",
        table="Reporting.vProductStockItems",
        columns={
            "item_code": ["Style #", "Size"],
            "item_description": "Product Desc",
            "warehouse": "Warehouse",
            "balance_date": None,
            "quantity": "Units",
        },
    )
    sql, params = build_select(ds, date_from=date(2026, 6, 1), item_code="STY1|XL")
    assert "CONCAT_WS('|', [Style #], [Size]) AS [item_code]" in sql
    assert "NULL AS [balance_date]" in sql
    assert "date_from" not in params  # no date column -> date filters skipped
    assert "CONCAT_WS('|', [Style #], [Size]) = :item_code" in sql
    assert params == {"item_code": "STY1|XL"}
    assert not ds.has_date


def test_normalize_stamps_current_state_balance():
    import pandas as pd
    from stockwatch.config import DatasetConfig
    from stockwatch.queries import normalize

    ds = DatasetConfig(
        name="rm_balance",
        kind="balance",
        table="Reporting.vFabricListing",
        columns={
            "item_code": "Stock No",
            "item_description": "Stock Desc",
            "warehouse": None,
            "balance_date": None,
            "quantity": "SOH Units",
        },
    )
    raw = pd.DataFrame(
        {
            "item_code": ["RM1"],
            "item_description": [None],
            "warehouse": [None],
            "balance_date": [None],
            "quantity": ["12.5"],
        }
    )
    out = normalize(raw, ds)
    assert out["balance_date"].notna().all()
    assert out["warehouse"].iloc[0] == "-"
    assert out["quantity"].iloc[0] == 12.5


def test_config_rejects_null_for_required_column(tmp_path):
    bad = tmp_path / "tables.yml"
    bad.write_text(
        """
datasets:
  rm_balance:
    kind: balance
    table: dbo.Balances
    columns:
      item_code: null
      item_description: D
      warehouse: W
      balance_date: BD
      quantity: Q
"""
    )
    with pytest.raises(ValueError, match="cannot be null"):
        load_config(bad)


def test_build_select_product_mapping():
    from stockwatch.config import DatasetConfig

    ds = DatasetConfig(
        name="wip_balance",
        kind="balance",
        table="Reporting.vWorkInProgress",
        columns={
            "item_code": "DONumber",
            "item_description": "JobDescription",
            "warehouse": "Customer",
            "balance_date": None,
            "quantity": {"product": ["Value", "Ratio", 0.01]},
        },
    )
    sql, _ = build_select(ds)
    assert "([Value] * [Ratio] * 0.01) AS [quantity]" in sql


def test_config_rejects_bad_product_mapping(tmp_path):
    bad = tmp_path / "tables.yml"
    bad.write_text(
        """
datasets:
  wip_balance:
    kind: balance
    table: dbo.WIP
    columns:
      item_code: Job
      item_description: D
      warehouse: W
      balance_date: null
      quantity: {product: [OnlyOne]}
"""
    )
    with pytest.raises(ValueError, match="at least two factors"):
        load_config(bad)


def test_find_baseline_picks_newest_on_or_before(tmp_path):
    import pandas as pd
    from stockwatch.cli import _find_baseline

    for name in ("rm_balance_20260630.csv", "rm_balance_20260707.csv",
                 "rm_balance_20260801.csv", "fg_balance_20260707.csv", "rm_balance_junk.csv"):
        (tmp_path / name).write_text("item_code,warehouse,quantity\n")
    pick = _find_baseline(tmp_path, "rm_balance", pd.Timestamp("2026-07-13"))
    assert pick.name == "rm_balance_20260707.csv"
    assert _find_baseline(tmp_path, "wip_balance", pd.Timestamp("2026-07-13")) is None


def test_load_snapshot_csv_reads_cp1252(tmp_path):
    """Baselines written as Windows-1252 (en-dash 0x96 in a description) still load."""
    import pandas as pd
    from pathlib import Path
    from stockwatch.cli import _load_snapshot_csv

    path = tmp_path / "rm_balance_20260707.csv"
    path.write_text(
        "item_code,warehouse,item_description,quantity\n"
        "AC-1,W1,Navy – trouser,10\n",   # en-dash, not ASCII
        encoding="cp1252",
    )
    df = _load_snapshot_csv(Path(path))
    assert df["quantity"].sum() == 10
    assert "–" in df["item_description"].iloc[0]


def test_snapshot_all_writes_one_csv_per_store(tmp_path, monkeypatch):
    """snapshot-all writes baselines/<dataset>_YYYYMMDD.csv for all three stores,
    with the naming convention report/reconcile-chain auto-discover."""
    import pandas as pd
    from pathlib import Path
    from stockwatch import cli

    def fake_fetch(cfg, dataset, **filters):
        return pd.DataFrame(
            {
                "item_code": [f"{dataset}-A", f"{dataset}-B"],
                "warehouse": ["W1", "W1"],
                "item_description": ["a", "b"],
                "balance_date": pd.to_datetime(["2026-07-13", "2026-07-13"]),
                "quantity": [10.0, 5.0],
                "value": [1000.0, 250.0],
            }
        )

    monkeypatch.setattr(cli, "_fetch", fake_fetch)
    cli.snapshot_all(out_dir=tmp_path, as_of=None, config=Path("config/tables.yml"))

    stamp = pd.Timestamp.now().strftime("%Y%m%d")
    for ds in ("rm_balance", "fg_balance", "wip_balance"):
        path = tmp_path / f"{ds}_{stamp}.csv"
        assert path.is_file(), f"missing {path}"
        df = pd.read_csv(path)
        assert set(["item_code", "warehouse", "quantity", "value"]).issubset(df.columns)
        assert df["quantity"].sum() == 15.0
        assert df["value"].sum() == 1250.0    # Rand value carried through
