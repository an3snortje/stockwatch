import pandas as pd

from stockwatch.introspect import render_yaml, suggest_datasets


def _schema(tables: dict[str, list[tuple[str, str]]]) -> pd.DataFrame:
    rows = [
        ("dbo", tbl, col, dtype)
        for tbl, cols in tables.items()
        for col, dtype in cols
    ]
    return pd.DataFrame(rows, columns=["table_schema", "table_name", "column_name", "data_type"])


MOVEMENT_COLS = [
    ("ItemCode", "varchar"),
    ("ItemDescription", "varchar"),
    ("Warehouse", "varchar"),
    ("TransDate", "datetime"),
    ("TransType", "varchar"),
    ("Qty", "decimal"),
    ("DocRef", "varchar"),
]
BALANCE_COLS = [
    ("StockCode", "varchar"),
    ("Description", "varchar"),
    ("Store", "varchar"),
    ("AsAtDate", "date"),
    ("QtyOnHand", "decimal"),
]


def test_suggests_matching_tables_and_columns():
    schema = _schema(
        {
            "FinishedProductTrans": MOVEMENT_COLS,
            "RawMaterialTransHistory": MOVEMENT_COLS,
            "WIPStockBalance": BALANCE_COLS,
            "FinishedGoodsSOH": BALANCE_COLS,
            "RawMaterialSOH": BALANCE_COLS,
            "Customers": [("CustomerCode", "varchar")],
        }
    )
    out = suggest_datasets(schema)
    assert out["fg_movements"]["table"] == "dbo.FinishedProductTrans"
    assert out["rm_movements"]["table"] == "dbo.RawMaterialTransHistory"
    assert out["wip_balance"]["table"] == "dbo.WIPStockBalance"
    assert out["fg_balance"]["table"] == "dbo.FinishedGoodsSOH"
    assert out["rm_balance"]["table"] == "dbo.RawMaterialSOH"

    fg = out["fg_movements"]["columns"]
    assert fg["item_code"] == "ItemCode"
    assert fg["movement_date"] == "TransDate"
    assert fg["quantity"] == "Qty"
    assert fg["reference"] == "DocRef"

    bal = out["fg_balance"]["columns"]
    assert bal["item_code"] == "StockCode"
    assert bal["balance_date"] == "AsAtDate"
    assert bal["quantity"] == "QtyOnHand"


def test_column_guess_respects_dtype():
    # "DateCaptured" is varchar → must not be picked as movement_date
    cols = [c for c in MOVEMENT_COLS if c[0] != "TransDate"] + [("DateCaptured", "varchar")]
    schema = _schema({"FGMovements": cols})
    out = suggest_datasets(schema)
    assert out["fg_movements"]["columns"]["movement_date"] is None


def test_no_candidate_and_yaml_rendering():
    schema = _schema({"Customers": [("CustomerCode", "varchar")]})
    out = suggest_datasets(schema)
    assert out["fg_movements"] == {}
    text = render_yaml(out)
    assert "NO CANDIDATE TABLE FOUND" in text


def test_yaml_marks_unmapped_columns():
    cols = [c for c in MOVEMENT_COLS if c[0] != "DocRef"]
    schema = _schema({"FGMovementHistory": cols})
    text = render_yaml(suggest_datasets(schema))
    assert "table: dbo.FGMovementHistory" in text
    assert "reference: FIXME" in text
