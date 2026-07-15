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

## Troubleshooting — "it never fired"

Run this to see the task's state, last result, and latest log in one shot:

```powershell
$t = 'StockWatch Nightly Snapshot'
Get-ScheduledTaskInfo -TaskName $t |
    Select-Object LastRunTime, LastTaskResult, NextRunTime, NumberOfMissedRuns
Get-ChildItem .\baselines\_logs\snapshot-*.log |
    Sort-Object LastWriteTime | Select-Object -Last 1 |
    ForEach-Object { Get-Content $_.FullName -Tail 30 }
```

| What you see | Meaning / fix |
|---|---|
| `Get-ScheduledTask` errors / nothing | Task not registered — run `register-task.ps1`. |
| `LastTaskResult` = `267011` (`0x41303`) | Task has never run yet — normal until the first 02:00, or it only runs while logged on. Re-register with `-RunWhenLoggedOff` to run signed out. |
| `NumberOfMissedRuns` > 0 on a **laptop** | Was on battery. Re-register — the script now sets `-AllowStartIfOnBatteries -DontStopIfGoingOnBatteries`. |
| `LastTaskResult` = `0` but no CSVs | It ran fine but wrote elsewhere — check `-OutDir`. |
| `LastTaskResult` ≠ `0` with a log | It ran and the tool failed — the log names the cause (DB creds, venv path, or share unreachable). |

After changing power/logon options, **re-run `register-task.ps1`** (it replaces the
task with `-Force`) so the new settings take effect.

## Using the baselines

Once a few nights have accumulated, the reconciliation commands find them
automatically — no manual snapshot step:

```powershell
stockwatch report --from 2026-07-13
stockwatch reconcile-chain fg --baseline-dir .\baselines
```
