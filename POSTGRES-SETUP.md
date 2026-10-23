# PostgreSQL foundation: pilot only

The private RDS instance exists. Team Partner Agent now supports explicit PostgreSQL selection for the receiver, review history, chat, and outbox. The application image includes the database driver and trusted RDS certificate. SQLite remains the default, p-OPS is unchanged, and health-only standby does not initialize storage. The PostgreSQL path is tested locally but has not been activated in AWS.

## Credentials and bootstrap

`infra/database-credentials.yaml` generates runtime and migration passwords directly in Secrets Manager. The bootstrap task receives secret references through ECS, never literal secrets in task definitions or GitHub. The runtime login has SELECT, INSERT and UPDATE, sequence access and schema USAGE; it cannot delete records or create/drop tables. The migration login owns the schema. Neither login has role creation, database creation, replication or superuser privileges.

Build `infra/Dockerfile.database` for the one-time task. It contains no agent worker code. Supply ADMIN_CREDENTIAL, MIGRATION_CREDENTIAL and RUNTIME_CREDENTIAL through ECS secret injection, and DB_HOST as a normal environment variable. Run it in the agent VPC and security group. The script requires full certificate and hostname verification. It does not reset existing account passwords. Errors print only exception classes to avoid leaking SQL or credentials.

Set the credentials stack's BootstrapAccess parameter to false immediately after the task exits, including on failure. This removes the temporary secret-read policy. Deregister the completed task definition. Do not grant the normal agent execution role access to administrator or migration secrets.

## Tests

Run `python3 scripts/test_postgres_local.py` with Docker and OpenSSL available. It creates a temporary TLS-enabled PostgreSQL instance, bootstraps twice, runs real database concurrency/permission tests, and cleans up its container and network. Test fixtures contain only generated namespaces and synthetic records. Never point these tests at RDS or imported business data.

`postgres_store.py` commits a unique send claim before a caller may post; an uncertain sending row remains blocked across reconnects. Decisions use conditional first-writer updates, and queue/event IDs are deduplicated per workspace:bot namespace. Namespace predicates isolate records logically; they are not row-level security against a malicious holder of the runtime credential.

## Activation remains blocked

1. Verify the configured receiver, review ledger, chat and outbox against the migrated AWS schema; selection must remain consistent across instances.
2. Validate deployment shutdown and the operator recovery procedure for durable worker crash holds.
3. Import and compare existing SQLite jobs, receipts, review history and ambiguous deliveries; do not infer missing receipts from Notion alone.
4. Test crash after Slack success, delayed writebacks, receiver database failure, restart, failover and rollback with fake external clients before controlled live records.
5. Attach only runtime credentials to the approved application release. Keep migration and administrator secrets separate.
6. Explicitly authorize production cutover after fencing former workers. An empty schema is not recovered processing history.

The pilot is single-zone with seven-day backups. It is not a completed disaster recovery solution; restore and cross-failure-domain recovery remain to be tested.

### Outbox adapter validation

The outbox accepts an explicitly injected PostgreSQL store for integration tests.
Its polling, delivery fence, receipt recovery, pickup queue, and first-writer
invitation decisions use that store without opening a SQLite database. Production
entry points still select SQLite; this is not an activation switch.

The disposable PostgreSQL suite now includes outbox restart after a failed Notion
writeback, ambiguous Slack timeout across restart, and event namespace isolation.
External Slack and Notion calls in these tests are simulated. No live messages are
sent. Receiver and review-history migration, worker coordination, and historical
state reconciliation remain activation prerequisites.

### Receiver and review history validation

`PostgresStore` can now be injected into the existing event receiver. An enqueue
or commit error becomes `StorageUnavailable`, which events and interactions return
as HTTP 503 without exposing database error details. A repeated event after an
uncertain commit is deduplicated. `next_job` preserves queue ordering but is only
a selection operation, not a distributed worker lock.

`PostgresLedger` implements review history, delivery receipts, metadata, and
feedback. Card lookup requires the channel as well as the timestamp. Feedback
updates serialize on the review row and retire old votes without DELETE access.
The schema additions have been tested locally, not applied to the AWS pilot yet.

Validation: 309 existing unit tests and 16 disposable TLS PostgreSQL tests pass.
Live entry points still use SQLite. Before activation, finish shared worker
coordination and backend selection, reconcile historical state, migrate the AWS
schema, and run a restricted end-to-end test. Do not enable live processing based
on these adapter tests alone.

### Exclusive worker sessions and crash recovery

`postgres_worker.worker_session(store, role)` yields a storage adapter bound to a
single owning PostgreSQL connection. Roles are `queue` and `outbox`; ownership is
scoped to the workspace/bot namespace. A session advisory lock prevents concurrent
owners. A durable ownership record blocks automatic takeover after connection
loss or a process crash, even after PostgreSQL releases the advisory lock.

Use the yielded adapter for every queue, review-ledger, and outbox operation in
that worker. Database failures terminate that adapter with `WorkerStopped`, which
ordinary per-job `except Exception` handlers cannot suppress. The adapter never
reconnects and cannot be reused after its context ends. Normal context completion
releases the durable hold; exceptions, signals that interrupt execution, and
uncertain release commits retain it conservatively.

This deliberately favors duplicate prevention over automatic recovery. A database
lease expiring would not prove that an old Slack/Notion request has stopped.
Before an operator clears a crash hold, stop and verify termination of the former
worker, inspect pending external-write and send receipts, and reconcile uncertain
operations. Never clear ownership solely because its timestamp is old. A complete
operator recovery command and deployment integration remain outstanding.

Twenty disposable PostgreSQL tests now pass, including mutual exclusion, normal
handoff, namespace independence, process failure, and loss of the owning database
connection. This mechanism is not yet selected by production entry points. AWS
schema and service settings have not changed.

### Startup configuration (implemented, not activated)

The current startup wiring supersedes earlier adapter-only milestone notes above.
`STORAGE_BACKEND` accepts `sqlite` (default) or `postgres`; unknown values fail.
PostgreSQL requires all of:

- `POSTGRES_STATE_READY=true`: an explicit assertion that historical state has
  been reconciled. Do not set this on the current empty pilot yet.
- `DB_HOST`: the private database endpoint.
- `SLACK_TEAM_ID`: the intended workspace; the namespace combines this with the
  fixed Team Partner Agent bot identity.
- `RUNTIME_CREDENTIAL`: restricted `agent_runtime` credentials injected through
  ECS Secrets Manager references, never GitHub variables or literal task values.
- `DB_SSL_ROOT_CERT`: optional override of `/app/rds-ca.pem`; the file must exist.
  TLS certificate and hostname verification are mandatory.

Active startup checks database/schema access before launching children. Receiver
connections use the shared store; queue and outbox workers acquire separate
exclusive sessions. Reviews and outbox operations use their owning session.
Receipt lookups also use PostgreSQL, and lookup failures return a retry response.
An outbox-event handoff failure leaves the accepted queue job pending for retry.
`AGENT_RUNTIME_MODE=standby` still bypasses all storage and processing startup.

Validation now includes 314 unit tests and 21 disposable PostgreSQL tests. The
startup-factory test exercises receiver enqueue, exclusive worker selection,
review history, outbox posting with fake external clients, and receipt lookup
through the configured backend. No live AWS schema, credentials, or service
settings were changed. Historical-state reconciliation and controlled AWS
acceptance are still required before setting the readiness assertion.

### Historical-state recovery gate

See `AWS-PILOT-TEST.md` for the current recovery gate and staged AWS validation.
`reconcile_state.py` inventories recovered SQLite snapshots read-only and compares
canonical row fingerprints as well as counts. Reports always leave cutover
readiness false; matching snapshots do not establish that old writers stopped or
that PostgreSQL contains the same history. No usable local agent backup was found
during the October 22 check. PostgreSQL import is not implemented by this tool.

### Forward-only outbox rollout preparation

`OUTBOX_CREATED_AFTER` optionally takes an ISO 8601 date-time with a timezone.
Only rows whose Notion `created_time` is strictly later may acquire a new send
fence. Rows at/before the cutoff, or without a valid creation timestamp, are
skipped without writes. The check runs again immediately before claiming a send.
Already-owned delivery receipts can still finish writeback. `OUTBOX_TEST_IDS`
remains an additional restriction, not an override of the cutoff.

Choose and record the exact UTC cutoff at activation; do not use a moving value
such as today's date on each deploy. Preserve it across restarts. Editing or
re-queuing an old row does not make it eligible. Copying an old row into a new
page does, so legacy requests must not be recreated automatically by the intake
pipeline. The cutoff does not itself recover history or enable PostgreSQL.

Remaining activation work:
1. Implement an explicit fresh-start mode with a persistent recorded cutoff and
   receive-time/event-time restrictions for inbound Slack events. Do not assert
   POSTGRES_STATE_READY simply because an outbox cutoff exists.
2. Confirm old writers are stopped, and reference the rotated Slack signing
   secret and model key for the signed receiver and review processing. Only the
   Slack bot, Notion and database runtime secrets were exercised by OUT-22.
3. Merge reviewed application changes and deploy the immutable image in standby.
4. Verify Slack event/interactivity URLs, restricted production credentials,
   single-worker ownership and a new post-cutoff acceptance fixture before
   enabling normal traffic. Preserve OUT-1 through OUT-21 as historical work.

### Explicit fresh-start mode (implemented; not activated)

Instead of the historical recovery assertion, startup can use:

- `POSTGRES_FRESH_START_AT`: fixed timezone-aware timestamp selected at cutover.
- `OUTBOX_CREATED_AFTER`: exactly the same value.
- `LEGACY_WRITERS_STOPPED=true`: only after old writers are verified stopped.
- Leave `POSTGRES_STATE_READY` unset; mixing the two modes fails.

The first start requires an empty production namespace and records the normalized
boundary in `review_meta`. Concurrent startup is serialized. Subsequent startup
refuses a changed or removed boundary, including a switch to the reconciled
assertion. Pilot OUT-22 remains in its own separate namespace. No schema changes
are needed. Ordinary SQL access controls are unchanged; this is an application
startup safeguard, not protection against privileged database edits.

Signed Slack events are acknowledged without enqueuing if their message/event,
thread, nested-message or reaction-target timestamp is old or invalid. Signed
button clicks require both a new message and new action timestamps. New replies
to pre-cutoff threads are intentionally excluded. Signature validation still
runs first; Slack URL verification still works. Existing history is not recovered.

Validation: 335 unit tests and 23 disposable TLS PostgreSQL tests, including
concurrent marker creation, immutable boundary and nonempty-namespace refusal.
No production cutoff or readiness assertion has been set.
