# Nightly baseline snapshot (Windows Task Scheduler)

Runs `stockwatch snapshot-all` on your own machine every night, writing
`rm_balance_YYYYMMDD.csv`, `fg_balance_YYYYMMDD.csv` and
`wip_balance_YYYYMMDD.csv` — the names `report` and `reconcile-chain`
auto-discover. Use this when you don't have Docker/VM access; it reuses the
stockwatch install you already have.

Two files:

| File | What it does |
|---|---|
| `snapshot.ps1` | Runs the snapshot once, with logging. Called by the task (and handy to run by hand). |
| `register-task.ps1` | Creates the scheduled task that runs `snapshot.ps1` nightly. |

## Setup

Open **PowerShell**, `cd` into your stockwatch project folder, then:

```powershell
# 1. test one snapshot by hand (writes to .\baselines)
.\deploy\windows\snapshot.ps1

# 2. if that produced three CSVs, schedule it for 02:00 daily
.\deploy\windows\register-task.ps1
```

That's it. Check the task exists: **Task Scheduler → Task Scheduler Library →
"StockWatch Nightly Snapshot"**, or from PowerShell:

```powershell
Start-ScheduledTask -TaskName 'StockWatch Nightly Snapshot'   # run it now to test
```

## Options

- **Write to a shared drive** so you can reconcile from any machine:
  ```powershell
  .\deploy\windows\snapshot.ps1 -OutDir '\\fileserver\stockwatch\baselines'
  .\deploy\windows\register-task.ps1 -OutDir '\\fileserver\stockwatch\baselines' -RunWhenLoggedOff
  ```
  `-RunWhenLoggedOff` prompts once for your Windows password (stored by Task
  Scheduler) so the task runs while you're signed out **and** can reach the
  network share at night.

- **Different time:** `-Time 23:36` (or any `HH:mm`).

- **Machine off overnight?** The task is set to *start when available*, so if the
  PC was off at 02:00 it runs the next time you turn it on — a fine quiescent
  baseline before the day's transactions begin.

## Where things go

- Baselines → `.\baselines\` (or your `-OutDir`).
- Logs → `<OutDir>\_logs\snapshot-YYYYMMDD.log`.

## Using the baselines

Once a few nights have accumulated, the reconciliation commands find them
automatically — no manual snapshot step:

```powershell
stockwatch report --from 2026-07-13
stockwatch reconcile-chain fg --baseline-dir .\baselines
```
