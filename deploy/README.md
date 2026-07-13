# Deploying the nightly baseline snapshot

iSync's balance views are current-state only, so reconciliation needs a saved
opening balance. Automate that with a nightly `stockwatch snapshot-all` and the
CSVs are always waiting — `report` and `reconcile-chain` discover them by name.

Pick the setup that matches your access:

| Folder | Use when | How it runs |
|---|---|---|
| [`windows/`](windows/) | You have a Windows machine with stockwatch installed (no Docker/VM access). | Windows Task Scheduler runs `snapshot.ps1` nightly. |
| [`n8n/`](n8n/) | You have Docker + n8n on a VM. | A stockwatch sidecar container, triggered nightly by n8n. |

Both write the same files to the same convention, so you can start with Windows
and move to the n8n sidecar later without changing anything downstream.
