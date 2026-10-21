# Agent recovery: stage 1

Status: recovery tooling only. No standby, external monitoring, backup schedule, or automatic failover is provisioned by this change.

## Audit

The team agent stores chat/review jobs and outbox delivery receipts in CHAT_DB (normally /data/chat.sqlite3). Legacy intake uses INTAKE_DB. The p-OPS modules also use SQLite for capture, approvals, notifications, and Linear actions. Inventory their configured paths before recovery. PartnerOS has a separate Postgres database and needs its own recovery plan.

Notion rows alone are not sufficient recovery state: a Slack post can succeed before its Notion receipt is written. Starting from an empty database or replaying an uncertain delivery can duplicate actions. A second independent worker must not be started against the same business records.

## Back up and restore

The Docker image includes recovery.py. For each configured database:

    python recovery.py backup /data/chat.sqlite3 /backup/chat.sqlite3
    python recovery.py restore /backup/chat.sqlite3 /restored/chat.sqlite3

Use a new destination for every copy. The tool uses SQLite's backup API (including committed WAL data), verifies integrity, and publishes a private file without overwriting an existing file. Backups contain private business data. Encrypt them and store them outside Railway with restricted access and retention controls. This tool does not perform encryption or upload.

For a coherent multi-file recovery point, stop all writers first. Record the exact deployed commit/image, database paths, enabled workflows, channel restrictions, and backup time. Store secrets separately in an approved secret manager, never in the repository or backup manifest.

## Manual recovery drill

1. Keep the Claude scheduled task paused and use only designated fictional records.
2. Stop the primary receiver and all workers. If the primary cannot be fenced off, do not promote a second worker.
3. Back up every state database and copy the verified snapshots to the recovery host.
4. Restore to new paths. Match the application's schema and deployed release to the backup.
5. Start one receiver/worker instance with the same test restrictions. Switch Slack event AND interaction endpoints only after signature validation and receipt persistence are tested.
6. Confirm one fictional submission posts once, a repeat event does not repost, and pickup/decision receipts survive a restart.
7. Reconcile any uncertain Slack deliveries manually before retrying. Do not clear receipts to force a retry.
8. For failback, stop the recovery host, transfer its newest state, and then start the primary. Never resume from the old primary snapshot.

## Next implementation gates

- Shared Postgres job ownership with leases/fencing and unique source IDs across every writing workflow; migrate existing delivery receipts before enabling another host.
- A durable Slack receiver independent of Railway, with acknowledgment only after persistence and a permanent company-owned URL.
- External monitoring: process health, worker heartbeat, last successful poll, oldest queued job, uncertain deliveries, and scheduled-task completion. A responding /healthz is not evidence of progress.
- Build immutable container releases outside Railway; deploy a processing-disabled standby on another provider.
- Off-provider encrypted backups with scheduled restore drills. Shared Postgres itself needs a tested recovery plan.

Do not enable multiple replicas as a substitute for these gates. Initial recovery targets must be measured in a drill before being represented as guarantees.

## Safe standby launcher

For services launched with `python service.py`, set `AGENT_RUNTIME_MODE=standby`. This starts only a health server: /healthz reports processing_enabled=false, and POST requests return 503 rather than falsely acknowledging Slack events. No review, chat, or outbox workers start. The default remains active for compatibility. Invalid mode values stop startup.

This switch does not govern alternative entrypoints such as p_ops_service.py or PartnerOS. Those must remain stopped until equivalent safeguards are implemented. Standby mode is not a distributed lock: only promote to active after fencing the former primary and restoring current state.
