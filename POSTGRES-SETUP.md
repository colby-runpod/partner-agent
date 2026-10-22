# PostgreSQL foundation: pilot only

The private RDS instance exists. This change adds separate migration and runtime accounts, schema initialization and an independently tested storage foundation. It does **not** switch the receiver, review ledger, chat, outbox or p-OPS workers to PostgreSQL. The normal agent Dockerfile does not include this module or its driver; the health-only standby remains unchanged.

## Credentials and bootstrap

`infra/database-credentials.yaml` generates runtime and migration passwords directly in Secrets Manager. The bootstrap task receives secret references through ECS, never literal secrets in task definitions or GitHub. The runtime login has SELECT, INSERT and UPDATE, sequence access and schema USAGE; it cannot delete records or create/drop tables. The migration login owns the schema. Neither login has role creation, database creation, replication or superuser privileges.

Build `infra/Dockerfile.database` for the one-time task. It contains no agent worker code. Supply ADMIN_CREDENTIAL, MIGRATION_CREDENTIAL and RUNTIME_CREDENTIAL through ECS secret injection, and DB_HOST as a normal environment variable. Run it in the agent VPC and security group. The script requires full certificate and hostname verification. It does not reset existing account passwords. Errors print only exception classes to avoid leaking SQL or credentials.

Set the credentials stack's BootstrapAccess parameter to false immediately after the task exits, including on failure. This removes the temporary secret-read policy. Deregister the completed task definition. Do not grant the normal agent execution role access to administrator or migration secrets.

## Tests

Run `python3 scripts/test_postgres_local.py` with Docker and OpenSSL available. It creates a temporary TLS-enabled PostgreSQL instance, bootstraps twice, runs real database concurrency/permission tests, and cleans up its container and network. Test fixtures contain only generated namespaces and synthetic records. Never point these tests at RDS or imported business data.

`postgres_store.py` commits a unique send claim before a caller may post; an uncertain sending row remains blocked across reconnects. Decisions use conditional first-writer updates, and queue/event IDs are deduplicated per workspace:bot namespace. Namespace predicates isolate records logically; they are not row-level security against a malicious holder of the runtime credential.

## Activation remains blocked

1. Port receiver, review ledger, chat and outbox to the shared store together; do not silently fall back to SQLite.
2. Add database-backed worker ownership and fail-closed behavior on loss of ownership before external actions.
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
