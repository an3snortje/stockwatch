from datetime import datetime
from pathlib import Path

import pandas as pd

from stockwatch.config import Config, DatasetConfig, load_config
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


def test_build_dashboard_carries_coverage():
    cfg = _cfg()
    cov = {"from": "2026-07-15", "to": "2026-07-16"}
    data = build_dashboard(pd.DataFrame(), pd.DataFrame(), cfg,
                           {"rm": [], "fg": [], "wip": []}, coverage=cov)
    assert data["coverage"] == cov
    # the reconcile guard must be wired into the page
    html = render_html(data, "T", "S")
    assert "inCoverage" in html and "exclUpper" in html


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


def _fake_fetch_factory():
    def fake_fetch(cfg, dataset, **filters):
        if dataset.endswith("_movements"):
            cat = "Fabric" if dataset.startswith("rm") else "Overall"
            return pd.DataFrame({
                "item_code": ["X|1", "X|1"],
                "warehouse": ["W", "W"],
                "item_description": ["d", "d"],
                "movement_date": pd.to_datetime(["2026-07-01", "2026-07-02"]),
                "movement_type": ["RECEIPT" if dataset.startswith("rm") else "RECEIVED",
                                  "DESPATCH" if dataset.startswith("rm") else "INVOICED"],
                "quantity": [100.0, -30.0],
                "value": [5000.0, -1500.0],
                "category": [cat, cat],
            })
        # balance dataset
        return pd.DataFrame({
            "item_code": ["X|1", "Y|2"],
            "warehouse": ["W", "W"],
            "item_description": ["d", "e"],
            "balance_date": pd.to_datetime(["2026-07-10", "2026-07-10"]),
            "quantity": [70.0, 20.0],
            "value": [3500.0, 900.0],
        })
    return fake_fetch


def test_cli_report_and_dashboard_run(tmp_path, monkeypatch):
    """report and dashboard execute end-to-end (no DB) and produce output —
    guards against a command body referencing an undefined name."""
    from stockwatch import cli

    monkeypatch.setattr(cli, "_fetch", _fake_fetch_factory())
    bdir = tmp_path / "baselines"
    bdir.mkdir()
    for ds in ("rm_balance", "fg_balance", "wip_balance"):
        (bdir / f"{ds}_20260701.csv").write_text(
            "item_code,warehouse,quantity,value\nX|1,W,60,3000\n", encoding="utf-8")

    out_dir = tmp_path / "out"
    cli.report(date_from=datetime(2026, 7, 1), date_to=datetime(2026, 7, 5),
               baseline_dir=bdir, out_dir=out_dir, config=Path("config/tables.yml"))
    assert (out_dir / "balances.csv").is_file()
    assert (out_dir / "rm_by_cost_type.csv").is_file()

    html_out = tmp_path / "dash.html"
    cli.dashboard(date_from=datetime(2026, 7, 1), date_to=datetime(2026, 7, 5),
                  baseline_dir=bdir, out=html_out, config=Path("config/tables.yml"))
    assert html_out.is_file()
    assert html_out.read_text(encoding="utf-8").startswith("<!doctype html>")
