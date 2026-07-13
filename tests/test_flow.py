import pandas as pd
import pytest

from stockwatch.flow import PROD, build_flow, render_html
from tests.test_analysis import cfg  # fixture


def _mov(rows):
    df = pd.DataFrame(
        rows,
        columns=["dataset", "item_code", "warehouse", "movement_date",
                 "movement_type", "quantity", "value", "reference"],
    )
    df["movement_date"] = pd.to_datetime(df["movement_date"])
    df["item_description"] = ""
    return df


@pytest.fixture
def flow_cfg(cfg):
    cfg.movement_types = {
        "receipts": ["RECEIPT", "RECEIVED", "RETURN", "RETURNED"],
        "issues": ["DESPATCH", "INVOICED", "SALE STOCK", "RTV"],
        "adjustments": ["STOCK ADJ IN", "STOCK ADJ OUT"],
    }
    cfg.issues_stored_positive = False
    return cfg


def test_flow_routes_and_nets(flow_cfg):
    mov = _mov([
        ("rm_movements", "F1", "Fabric", "2026-07-01", "RECEIPT", 100, 1000.0, "G1"),
        ("rm_movements", "F1", "Fabric", "2026-07-02", "DESPATCH", -80, -800.0, "J1"),
        ("rm_movements", "F1", "Fabric", "2026-07-03", "RETURN", 10, 100.0, "J1"),   # nets despatch to 700
        ("fg_movements", "S|M", "FG", "2026-07-04", "RECEIVED", 50, 900.0, "PPO-1"),
        ("fg_movements", "S|M", "FG", "2026-07-05", "INVOICED", -40, -720.0, "N1"),
        ("fg_movements", "S|M", "FG", "2026-07-06", "STOCK ADJ OUT", -2, -36.0, "A1"),
        ("fg_movements", "S|M", "FG", "2026-07-06", "TRANSFERRED IN", 40, 720.0, ""),  # skipped
        ("fg_movements", "S|M", "FG", "2026-07-06", "TRANSFERRED OUT", -40, -720.0, ""),
    ])
    out = build_flow(mov, flow_cfg, measure="value")
    edges = {(l["source"], l["target"]): l["weight"] for l in out["links"]}
    assert edges[("Vendors", "RM · Fabric")] == 1000.0
    assert edges[("RM · Fabric", PROD)] == 700.0            # 800 - 100 return
    assert edges[(PROD, "FG · FG")] == 900.0
    assert edges[("FG · FG", "Customers")] == 720.0
    assert edges[("FG · FG", "Adj out (FG)")] == 36.0
    assert not any("TRANSFER" in s or "TRANSFER" in t for s, t in edges)
    # components table still counts the skipped transfers
    types = {c["movement_type"] for c in out["components"]}
    assert "TRANSFERRED IN" in types


def test_flow_falls_back_to_units_without_value(flow_cfg):
    mov = _mov([("rm_movements", "F1", "Fabric", "2026-07-01", "RECEIPT", 100, 0.0, "G1")])
    out = build_flow(mov, flow_cfg, measure="value")
    assert out["measure"] == "units"
    assert out["links"][0]["weight"] == 100


def test_render_html_is_self_contained(flow_cfg):
    mov = _mov([
        ("rm_movements", "F1", "Fabric", "2026-07-01", "RECEIPT", 100, 1000.0, "G1"),
        ("fg_movements", "S|M", "FG", "2026-07-04", "RECEIVED", 50, 900.0, "PPO-1"),
    ])
    html = render_html(build_flow(mov, flow_cfg, "value"), "T", "S")
    assert "<script src" not in html and "http" not in html.split("</style>")[1][:2000]
    assert "Vendors" in html and "prefers-color-scheme" in html
