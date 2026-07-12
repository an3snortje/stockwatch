import pandas as pd
import pytest
from datetime import date

from isync_inventory.config import Config, DatasetConfig, load_config
from isync_inventory.explain import explain_anomalies, explain_reconciliation, explain_summary
from isync_inventory.queries import build_select


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
