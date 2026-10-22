# Controlled AWS validation plan

Status on October 22, 2026: the existing agent service has one running task,
its health endpoint reports standby with processing disabled, and the database
pilot stack is CREATE_COMPLETE. These checks do not prove the new PostgreSQL
application path has been deployed or that historical data has been recovered.

## Recovery gate

No agent SQLite backup was found in Desktop, Downloads, or the current project.
The package-manager cache database is unrelated and must not be imported.
Railway processing history remains unresolved. Do not set POSTGRES_STATE_READY.

Recover a consistent private snapshot of each agent database, including its WAL
through the SQLite backup API rather than copying only the main database file.
Record the source service, capture time, and whether all former writers were
stopped. Keep original containers and evidence intact until recovery is complete.

Run `reconcile_state.py SOURCE --profile combined --output NEW_PRIVATE_REPORT`
against the recovered Team Partner Agent database. Use the intake profile for a
separate intake-only database. Missing tables or columns require investigation;
choosing a smaller profile is not a way to bypass missing history. The tool never
writes to a source or enables processing. Its report contains counts, hashes,
and issue categories, not message bodies or record IDs.

Use `--compare-to SECOND_SNAPSHOT` to compare recovered copies. Equal counts
alone are insufficient; content fingerprints must also match. This command
compares SQLite snapshots only. PostgreSQL import and source-to-destination
verification are still required and are not implemented by this checker.

## AWS validation sequence

1. Keep the existing service in standby and retain its known-good image reference.
2. Review and merge the application changes after validation. Apply the additional
   schema with the migration role through an isolated task. Give the application
   execution role access only to the runtime secret, never administrator secrets.
3. Perform a read-only runtime connection check in the agent VPC: verify TLS,
   restricted role, expected schema, and access. This requires no Slack/Notion
   credentials and must not launch the agent worker.
4. Import recovered state with an independently reviewed migration and compare
   source/destination counts and canonical content, including receipts, pending
   jobs, feedback, review metadata, and uncertain operations. Do not reset any
   sending or writing state to pending merely to make processing resume.
5. Run an isolated synthetic-data test using simulated external clients. Never
   point the disposable local integration suite at RDS; its bootstrap fixture is
   specifically for a throwaway database.
6. After reconciliation and former-worker shutdown are verified, prepare a
   restricted live outbox test with explicitly named fixture page IDs. Keep the
   original real queued rows excluded. Start with one test post, verify its
   receipt and Notion writeback, and confirm a second poll does not repost it.
7. Verify ownership handoff and crash recovery under supervision before normal
   traffic. Crash holds require review; automatic failover is not implemented.

A successful infrastructure deployment is not permission to release the real
queue. No AWS settings or schema were changed during this preparation.
