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

## Migration completed and remaining Slack scope gate

The additive migration task completed successfully on October 23 using only
migration and runtime credentials. All seven tables passed runtime access checks;
DELETE remains denied and TLS verification passed. No business rows were changed.
Temporary secret access was removed and the task definition deregistered.

The subsequent preflight reports no missing tables. Slack `conversations.info`
returns `missing_scope` and lists `channels:read,groups:read,mpim:read,im:read` as
its accepted conversation-type scopes. These are alternatives depending on the
conversation type, not a requirement to grant all four. For the test channel use
`channels:read` if public or `groups:read` if private; DM scopes are unnecessary.
Reauthorize the app after adding the appropriate scope, and update the AWS token
secret if Slack issues a different bot token. OUT-22 remains Cancelled; no posting
or queue release occurred.

## Updated token check, October 23

After the user reauthorized Slack, the missing-scope response stopped. The
read-only channel lookup was corrected to send its channel parameter using GET;
the previous JSON POST produced `invalid_arguments` once scope validation passed.
The corrected lookup now returns `channel_not_found` for C0C7R970C5T with the
expected bot identity. This does not distinguish private-channel membership from
an incorrect/inaccessible channel. Confirm the channel and add the bot before the
live fixture test. Notion and database checks pass, and all seven tables exist.
OUT-22 remains Cancelled; no message was sent. Temporary task access was removed.

## Isolated live test passed, October 23

After channel membership was granted, all read-only preflight checks passed.
The first isolated attempt stopped before writing because the fixture-creation
connector had removed the two literal Slack formatting asterisks. Read-only
inspection confirmed Cancelled status, no timestamp and no database receipt.
The runner accepts only that exact synthetic text variant and writes the intended
literal mrkdwn with the Notion API when queuing the fixture.

The subsequent task exited 0: one chat.postMessage call, PostgreSQL receipt
persisted, Notion Status=Posted with Message TS and permalink, and the Slack
message read back with matching bot identity and text. A new worker session
polled again and processed a stale queued copy without another post call.

Receipt: 1792774931.154469
Message: https://runpod-team.slack.com/archives/C0C7R970C5T/p1792774931154469

The runner uses a separate `pilot-OUT22-20261023` database namespace, restricts
all Notion reads/writes to OUT-22, and uses GET for Slack read methods. It was
packaged with the current storage/outbox modules into a temporary zip loaded by
the existing database-bootstrap image. This verifies the isolated application
path, not deployment of the production image. No historical rows were released,
and POSTGRES_STATE_READY was not set. The one-off definition was deregistered
and temporary secret access removed. Production rollout and historical recovery
remain separate outstanding work. The 24 outbox unit tests also passed.
