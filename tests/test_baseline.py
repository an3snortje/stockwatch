import pandas as pd
import pytest

from stockwatch.baseline import baseline_from_sheet
from stockwatch.config import DatasetConfig

AS_OF = pd.Timestamp("2026-07-07")


def test_composite_key_and_blank_rows():
    ds = DatasetConfig(
        name="fg_balance",
        kind="balance",
        table="x.y",
        columns={
            "item_code": ["Style #", "Size"],
            "item_description": "Product Desc",
            "warehouse": "Warehouse",
            "balance_date": None,
            "quantity": "Units",
        },
    )
    sheet = pd.DataFrame(
        {
            "Style #": [None, "BOB02D", "BOB02D", "BOB02D"],
            "Size": [None, 58.0, 58.0, 54.0],  # Excel floatified sizes
            "Product Desc": [None, "Choc Conti", "Choc Conti", "Choc Conti"],
            "Warehouse": [None, "Finished Goods", "Finished Goods", "Finished Goods"],
            "Units": [None, 4, 6, 39],
        }
    )
    out = baseline_from_sheet(sheet, ds, AS_OF)
    assert len(out) == 2  # blank row dropped, size-58 rows aggregated
    r58 = out[out["item_code"] == "BOB02D|58"].iloc[0]
    assert r58["quantity"] == 10
    assert (out["balance_date"] == AS_OF).all()


def test_product_quantity():
    ds = DatasetConfig(
        name="wip_balance",
        kind="balance",
        table="x.y",
        columns={
            "item_code": "DONumber",
            "item_description": "JobDescription",
            "warehouse": "Customer",
            "balance_date": None,
            "quantity": {"product": ["Value", "Ratio", 0.01]},
        },
    )
    sheet = pd.DataFrame(
        {
            "DONumber": ["DO-1", "DO-1"],
            "JobDescription": ["Army jacket", "Army jacket"],
            "Customer": ["Armscor", "Armscor"],
            "Value": [29795.52, 600.0],
            "Ratio": [34.97, 34.97],
        }
    )
    out = baseline_from_sheet(sheet, ds, AS_OF)
    assert len(out) == 1
    assert out.iloc[0]["quantity"] == pytest.approx((29795.52 + 600.0) * 0.3497)


def test_missing_column_is_reported():
    ds = DatasetConfig(
        name="rm_balance",
        kind="balance",
        table="x.y",
        columns={
            "item_code": "Stock No",
            "item_description": "Stock Desc",
            "warehouse": "Location",
            "balance_date": None,
            "quantity": "SOH Units",
        },
    )
    sheet = pd.DataFrame({"Stock No": ["A"], "Stock Desc": ["x"], "Location": ["L"]})
    with pytest.raises(KeyError, match="SOH Units"):
        baseline_from_sheet(sheet, ds, AS_OF)
