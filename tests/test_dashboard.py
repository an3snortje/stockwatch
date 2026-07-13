import pandas as pd

from stockwatch.config import Config, DatasetConfig
from stockwatch.dashboard import build_dashboard, render_html


def _cfg():
    return Config(
        datasets={
            "rm_movements": DatasetConfig("rm_movements", "movement", "t", {}),
            "fg_movements": DatasetConfig("fg_movements", "movement", "t", {}),
        },
        movement_types={"receipts": ["RECEIPT", "RECEIVED"], "issues": ["DESPATCH", "INVOICED"]},
        issues_stored_positive=False,
    )


def _mov(rows):
    return pd.DataFrame(rows)


def test_build_dashboard_aggregates_by_day_category():
    cfg = _cfg()
    rm = _mov([
        {"movement_date": pd.Timestamp("2026-07-01"), "movement_type": "RECEIPT",
         "quantity": 100.0, "value": 5000.0, "category": "Fabric", "warehouse": "W"},
        {"movement_date": pd.Timestamp("2026-07-01"), "movement_type": "DESPATCH",
         "quantity": -40.0, "value": -2000.0, "category": "Fabric", "warehouse": "W"},
        {"movement_date": pd.Timestamp("2026-07-02"), "movement_type": "RECEIPT",
         "quantity": 10.0, "value": 300.0, "category": "Trims", "warehouse": "W"},
    ])
    fg = _mov([
        {"movement_date": pd.Timestamp("2026-07-01"), "movement_type": "RECEIVED",
         "quantity": 20.0, "value": 900.0, "category": "Overall", "warehouse": "W"},
    ])
    baselines = {
        "rm": [{"d": "2026-07-01", "q": 1000.0, "v": 50000.0},
               {"d": "2026-07-02", "q": 1070.0, "v": 53300.0}],
        "fg": [{"d": "2026-07-01", "q": 500.0, "v": 20000.0}],
        "wip": [{"d": "2026-07-01", "q": 12345.0, "v": 12345.0}],
    }
    data = build_dashboard(rm, fg, cfg, baselines)

    assert set(data["movements"]) == {"rm", "fg"}
    # Fabric on 2026-07-01: receipt +100, issue -40
    fab = [r for r in data["movements"]["rm"] if r["c"] == "Fabric" and r["d"] == "2026-07-01"][0]
    assert fab["ru"] == 100.0 and fab["iu"] == -40.0
    assert "Fabric" in data["categories"]["rm"] and "Trims" in data["categories"]["rm"]
    assert data["baselines"]["wip"][0]["q"] == 12345.0


def test_build_dashboard_folds_beyond_top_n():
    cfg = _cfg()
    rows = [
        {"movement_date": pd.Timestamp("2026-07-01"), "movement_type": "RECEIPT",
         "quantity": float(i + 1), "value": float(i + 1), "category": f"cat{i}", "warehouse": "W"}
        for i in range(30)
    ]
    data = build_dashboard(_mov(rows), pd.DataFrame(), cfg, {"rm": [], "fg": [], "wip": []})
    cats = data["categories"]["rm"]
    assert "Other" in cats
    assert len(cats) <= 21  # TOP_N individual + Other


def test_render_html_is_self_contained():
    cfg = _cfg()
    data = build_dashboard(pd.DataFrame(), pd.DataFrame(), cfg, {"rm": [], "fg": [], "wip": []})
    html = render_html(data, "T", "S")
    assert html.startswith("<!doctype html>")
    # No external resource loads (the only http:// is the inert SVG namespace).
    assert "cdn" not in html.lower()
    assert "<script src" not in html and 'link rel="stylesheet"' not in html
    assert "https://" not in html and "//unpkg" not in html and "//cdnjs" not in html
