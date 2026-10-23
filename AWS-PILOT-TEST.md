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

## Fresh test fixture prepared October 23

A new synthetic outbox fixture was created and left Cancelled:
https://app.notion.com/p/402ff732fc3481d885e3f6c01f1b2fdd

Its only permitted posting channel is C0C7R970C5T (the review/test channel), its
kind is `other`, and it has no mentions, related intake, or invitation buttons.
For the isolated test, the exact restriction must be:

```
OUTBOX_TEST_IDS=402ff732-fc34-81d8-85e3-f6c01f1b2fdd
```

Do not remove this restriction or add OUT-1 through OUT-21. OUT-12 and OUT-13
already have matching Slack posts despite their missing Notion receipts; the
other historical rows remain outside the test. Do not infer recovered ledger
ownership from those search results.

Keep this fixture Cancelled until the isolated task is configured and its identity,
private database access, updated schema, and restrictions are verified. An isolated
synthetic test must not assert that production historical state has been recovered.
The main service stays in standby. Change only this fixture to Queued when the
restricted test is ready; run two polls and verify exactly one Slack receipt.

AWS inspection on October 23 was blocked by an expired CLI session. No AWS
resource, secret, runtime setting, or schema was changed during fixture preparation.

## AWS read-only preflight result, October 23

A one-off ECS task ran `scripts/check_aws_pilot.py` using secret references only.
Slack identity matched the expected bot, Notion could read the cancelled OUT-22
fixture, and the restricted database login connected over verified TLS. The
schema is still missing `feedback`, `review_meta`, `reviews`, and
`worker_ownership`; apply the additive migration before attempting the worker.
Test-channel membership was unverified, which does not establish whether the bot
is absent or the token lacks permission for channel inspection. Resolve before
posting. The task exited with code 2 (preflight incomplete). Its task definition
was deregistered and temporary access to all three secrets was removed. No Slack
post, Notion write, or database write occurred.
