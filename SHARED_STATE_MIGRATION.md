# Shared-state migration design

Status: storage initialization extracted; Postgres is NOT enabled. This branch depends on the recovery foundation branch.

## What must move together

- outbox_delivery: immutable send snapshot, sending fence, Slack receipt, pending Notion synchronization, invitation decision and actor.
- outbox_events: persisted pickup and button events with unique event keys.
- Receiver ownership lookup (owns) must query the same ledger.
- The receiver currently persists incoming work in the chat/review Store before forwarding outbox events. That queue must also become durable outside the host before claiming event-loss protection.
- The review worker and p-OPS stores require separate migration. An outbox-only Postgres backend must not be advertised as whole-agent failover.

## Required transaction rules

Use a unique row ID and an atomic INSERT ... ON CONFLICT DO NOTHING to acquire the send fence. Commit BEFORE calling Slack. A sending row without a receipt is uncertain and never automatically retried, including after a worker lease expires.

Use conditional updates for first-authorized invitation decisions. Never overwrite a prior decision after a concurrent read. Writeback reconciliation may repeat idempotent Notion updates, but must not create a new Slack post.

Worker ownership needs session/lease fencing. Losing database access must stop new external actions, not fall back to local SQLite. A lease alone cannot make an already in-flight Slack request transactional; preserve uncertainty records and manually reconcile them.

Namespace storage by Slack workspace and bot identity. Keep unique event IDs, source-channel message IDs and outbox IDs within that namespace. Require encrypted database connections and separate runtime versus migration credentials.

## Cutover sequence

1. Pause pipeline and fence all receivers/workers; record and back up every current state file.
2. Import outbox records and events into an empty namespaced Postgres schema in one transaction. Preserve snapshots, receipts, decisions and ambiguous sending states verbatim.
3. Compare counts and content hashes, then verify sample Slack receipts. Abort on any conflicting existing row; never merge by overwriting.
4. Configure one worker and receiver to use the shared backend. Do not run a mixed local/shared deployment.
5. Run the controlled test set and restart/failover tests before promoting a standby.
6. Rollback requires fencing Postgres writers and exporting the newest state, not restoring an older SQLite file.

## Acceptance gates before enabling Postgres

- Real Postgres integration tests, not SQL mocks: concurrent claims, simultaneous decisions, transactional rollback and reconnect after disconnect.
- Crash after fence, after Slack success, and before Notion writeback: no automatic duplicate posts.
- Database unavailable: no external send, no local fallback, no false acknowledgment of unpersisted Slack input.
- Migration counts/hashes match and ambiguous records remain blocked.
- Process-level failover drill with the old worker fenced, including replay of the same event.

## Infrastructure needed

A private test Postgres instance and an approved production database location outside Railway's failure boundary. No production URL or secret should be pasted into chat. Until this is provisioned and the gates pass, keep one active worker and use the manual recovery runbook.
