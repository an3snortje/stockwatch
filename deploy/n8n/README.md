# Nightly baseline snapshot (stockwatch sidecar + your existing n8n)

Captures `rm_balance`, `fg_balance` and `wip_balance` every night to an SMB
share, named `<dataset>_YYYYMMDD.csv` — the exact convention `stockwatch report`
and `stockwatch reconcile-chain` auto-discover. This turns reconciliation from a
manual "grab a snapshot first" chore into a hands-off, point-to-point check
across quiescent nightly baselines.

## Design

Your existing n8n container is **not** modified or replaced. Instead a small
long-lived **stockwatch sidecar** container runs on the same VM (Debian slim +
Microsoft ODBC Driver 18 + the stockwatch CLI, with the baselines SMB share
CIFS-mounted at `/baselines`). Your n8n triggers a snapshot inside it with:

```sh
docker exec stockwatch stockwatch snapshot-all --out-dir /baselines \
  --config /opt/stockwatch-src/config/tables.yml
```

## One-time setup

1. **Credentials** — from this folder:
   ```sh
   cp .env.example .env
   ```
   Fill in `MSSQL_PASSWORD` (read-only `SyncDBNOverallReportingUser`) and the
   `SMB_*` values. `.env` is git-ignored — keep it that way.

2. **Build & start the sidecar** (build context is the repo root):
   ```sh
   docker compose build
   docker compose up -d
   ```
   The first build compiles pyodbc, so it takes a few minutes. `docker ps`
   should now show a running `stockwatch` container.

3. **Smoke test** — run one snapshot by hand:
   ```sh
   docker exec stockwatch stockwatch snapshot-all --out-dir /baselines \
     --config /opt/stockwatch-src/config/tables.yml
   ```
   Confirm three files appear on the share:
   ```
   rm_balance_YYYYMMDD.csv   fg_balance_YYYYMMDD.csv   wip_balance_YYYYMMDD.csv
   ```

4. **Let n8n reach Docker.** For n8n's *Execute Command* node to run
   `docker exec`, your n8n container needs the Docker socket and the `docker`
   CLI. Add a drop-in override next to your existing n8n compose file — e.g.
   `docker-compose.override.yml`:
   ```yaml
   services:
     n8n:                      # <-- match your existing n8n service name
       user: root              # needed to read /var/run/docker.sock
       volumes:
         - /var/run/docker.sock:/var/run/docker.sock
   ```
   Then install the client inside the n8n container (or bake it into your n8n
   image): `docker exec -u root <n8n> apk add --no-cache docker-cli`.
   Recreate n8n: `docker compose up -d`.

   > The Docker socket grants root-equivalent access to the host. On a
   > single-tenant VM that's a common trade-off; if you'd rather not, use the
   > **SSH alternative** below instead.

5. **Import the workflow** — in the n8n UI: *Workflows → Import from File →*
   `stockwatch-nightly-snapshot.json`. Run it once (*Execute Workflow*) to
   confirm the `docker exec` succeeds, then toggle it **Active**. It runs the
   snapshot at 02:00 daily and appends to `/baselines/_logs/snapshot-YYYYMMDD.log`.

## Alerting on failure

The workflow's `Exit code 0?` branch calls **Stop And Error** on any non-zero
exit, marking the execution failed. Wire a separate workflow with an **Error
Trigger** node to your channel (Outlook, Slack) to be notified.

## Alternative: n8n SSH node (no Docker socket in n8n)

If you don't want to give n8n the Docker socket, keep the sidecar and instead
use n8n's built-in **SSH** node pointed at the VM host, running:

```sh
docker exec stockwatch stockwatch snapshot-all --out-dir /baselines \
  --config /opt/stockwatch-src/config/tables.yml
```

Store the host SSH key as an n8n *SSH* credential. Replace the *Execute Command*
node in the imported workflow with an *SSH* node carrying that command; the rest
(schedule, exit-code check, alert) is unchanged.

## Alternative: sidecar schedules itself (no n8n dependency for the run)

To decouple entirely, let the sidecar run its own cron and use n8n only for
alerting. Override the container command in `docker-compose.yml`:

```yaml
    # replace `CMD ["sleep","infinity"]`
    command: >
      sh -c "apt-get update && apt-get install -y --no-install-recommends cron && rm -rf /var/lib/apt/lists/* &&
      echo '0 2 * * * root stockwatch snapshot-all --out-dir /baselines --config /opt/stockwatch-src/config/tables.yml >> /baselines/_logs/snapshot-$(date +\%Y\%m\%d).log 2>&1' > /etc/cron.d/stockwatch &&
      chmod 0644 /etc/cron.d/stockwatch && cron -f"
```

## Tuning the mappings without a rebuild

`config/tables.yml` is baked into the image. To edit mappings on the fly, mount
your own copy over it — add to the `stockwatch` service in `docker-compose.yml`:

```yaml
    volumes:
      - ./tables.yml:/opt/stockwatch-src/config/tables.yml:ro
```

## Changing the schedule

Edit the `Every day 02:00` node's cron (`0 2 * * *`). Weekdays only → `0 2 * * 1-5`.
