# StockWatch

Analyzes and **explains in plain English** inventory movements and balances read directly
from the iSync MS SQL database:

| Logical dataset | iSync source (configurable) |
|---|---|
| `fg_movements` | Finished product movements |
| `rm_movements` | Raw material movements |
| `wip_balance`  | Work-in-progress balances |
| `fg_balance`   | Finished goods balances |
| `rm_balance`   | Raw material balances |

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env          # fill in SQL credentials (read-only account)

# Auto-suggest the dataset mapping from the live database, then review & merge
stockwatch discover --out suggested-tables.yml
# edit config/tables.yml      # finalize the five dataset mappings

# Monthly receipts / issues / adjustments / net per item & warehouse
stockwatch summary all --from 2026-06-01 --to 2026-07-01

# Does opening balance + movements explain the closing balance?
stockwatch reconcile fg --from 2026-06-01 --to 2026-07-01 --csv output/rec.csv

# Negative balances, outlier movements, dormant stock — with explanations
stockwatch anomalies all --from 2026-04-01 --to 2026-07-01

# Balance snapshot (works for WIP too)
stockwatch snapshot wip_balance --as-of 2026-06-30
```

All commands accept `--item`, `--warehouse`, `--csv` and `--config`.

## Configuration

**`config/tables.yml`** is the only file you edit for a new iSync schema:

- `datasets.*.table` / `columns` — physical table and column names. The app only
  ever uses the canonical (left-hand) names, so no code changes are required.
- `movement_types` — how iSync movement-type codes classify into
  receipts / issues / adjustments.
- `issues_stored_positive` — set `true` if iSync stores issue quantities as
  positive numbers (they are negated for net calculations).
- `analysis` — dormancy window, outlier z-score, reconciliation tolerance.

**`.env`** — SQL Server connection (see `.env.example`). Use a read-only login;
the app never writes to iSync.

## What the analysis does

- **summary** — receipts, issues, adjustments and net per item/warehouse/period,
  plus a narrative of the biggest builders and drawdowns.
- **reconcile** — `opening + net movement = closing?` per item/warehouse.
  Variances beyond tolerance are explained (unposted transactions, stocktake
  adjustments outside the movement tables, cut-off timing).
- **anomalies** — negative balances (issued stock never received), movement
  outliers (>3σ vs the item's history — often UoM or keying errors), and
  dormant items (no movement in 90 days).
- **snapshot** — latest balance per item/warehouse as of a date, including WIP.

## Architecture

```
config/tables.yml     dataset → physical table/column mapping (validated)
src/stockwatch/
  config.py           YAML loader + SQL-identifier validation
  db.py               SQLAlchemy/pyodbc engine from .env (read-only intent)
  queries.py          parameterized SELECT builder + dtype normalization
  analysis.py         pure-pandas: summary, reconciliation, anomaly detection
  explain.py          findings → plain-English narrative
  cli.py              typer CLI (stockwatch)
tests/                unit tests — no database required
```

`analysis.py` and `explain.py` are pure functions over DataFrames, so the same
logic can be reused from n8n (via a Python node/script), a Power BI dataflow
export, or a future API without touching the DB layer.

## Tests

```bash
pytest
```
