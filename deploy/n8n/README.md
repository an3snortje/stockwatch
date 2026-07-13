# Nightly baseline snapshot (n8n)

Captures `rm_balance`, `fg_balance` and `wip_balance` every night to an SMB
share, named `<dataset>_YYYYMMDD.csv` — the exact convention `stockwatch report`
and `stockwatch reconcile-chain` auto-discover. This is what turns
reconciliation from a manual "grab a snapshot first" chore into a hands-off,
point-to-point check across quiescent nightly baselines.

## What runs

An n8n **Execute Command** node runs, inside the n8n container, at 02:00 daily:

```sh
stockwatch snapshot-all --out-dir /baselines --config /opt/stockwatch-src/config/tables.yml
```

`/baselines` is a CIFS mount of your share, so the CSVs land on the network and
you can run `report`/`reconcile-chain` from any machine against the same files.
Each run also appends to `/baselines/_logs/snapshot-YYYYMMDD.log`.

## One-time setup

1. **Credentials** — from this folder:
   ```sh
   cp .env.example .env
   ```
   Fill in `MSSQL_PASSWORD` (the read-only `SyncDBNOverallReportingUser`) and the
   `SMB_*` values. `.env` is git-ignored — keep it that way.

2. **Build & start** (the build context is the repo root so the package is baked in):
   ```sh
   docker compose build
   docker compose up -d
   ```
   The first build compiles the ODBC driver + pyodbc, so it takes a few minutes.

3. **Import the workflow** — in the n8n UI (`http://<host>:5678`):
   *Workflows → Import from File →* `stockwatch-nightly-snapshot.json`, then
   toggle it **Active**.

4. **Smoke test** — open the workflow and click *Execute Workflow* once. Confirm
   three files appear on the share:
   ```
   rm_balance_YYYYMMDD.csv   fg_balance_YYYYMMDD.csv   wip_balance_YYYYMMDD.csv
   ```

## Alerting on failure

The workflow's `Exit code 0?` branch calls **Stop And Error** on any non-zero
exit, so the execution is marked failed. To get notified, create a separate n8n
workflow with an **Error Trigger** node wired to your channel (Outlook, Slack,
etc.) — it will fire for this and any other failed workflow.

## Tuning the mappings without a rebuild

`config/tables.yml` is baked into the image. To edit mappings on the fly, mount
your own copy over it — add to the `n8n` service in `docker-compose.yml`:

```yaml
    volumes:
      - ./tables.yml:/opt/stockwatch-src/config/tables.yml:ro
```

## Changing the schedule

Edit the `Every day 02:00` node's cron (`0 2 * * *`). Weekdays only → `0 2 * * 1-5`.
