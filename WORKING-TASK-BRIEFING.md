# AWS Working Task briefing

This feature runs inside the existing active ECS service. It does not use
Railway and it does not require a separate EventBridge schedule. The queue
worker checks the Mountain-time schedule and uses the PostgreSQL review ledger
to ensure that only one brief or hold notice is attempted per weekday.

## Source contract

The `Working Task Daily Integrity Review` runs before the briefing and writes a
compact JSON object to `Briefing Snapshot` on its dated Agent Run Log row. The
row must use this key:

`working-task-daily-integrity-review:YYYY-MM-DD`

The JSON contract is version 1 and includes:

- `as_of`, `safe_for_briefing`, and `task_updates_complete`
- Slack, Gmail, and Calendar/meetings coverage
- up to three Today items
- material progress
- risks and decisions
- cadence
- data-quality exceptions

Every item with a WI ID must include the canonical Notion task URL. The AWS
worker rereads every referenced task before posting and holds the briefing if a
task changed after the snapshot.

## ECS settings

Add these environment values to the active ECS task definition:

```text
WORKING_TASK_BRIEF_ENABLED=true
WORKING_TASK_BRIEF_CHANNEL_ID=C0C7HG8KYFL
WORKING_TASK_BRIEF_HOUR=8
WORKING_TASK_BRIEF_MINUTE=15
WORKING_TASK_BRIEF_MAX_AGE_HOURS=3
WORKING_TASK_RUN_LOG_ID=8d12b4ca-c723-4372-827c-d607070179a2
WORKING_TASKS_DATA_SOURCE_ID=e77648f6-502d-450f-8b79-4dd2ced52bab
```

The existing `NOTION_TOKEN` and `SLACK_BOT_TOKEN` secrets are reused. Do not
copy either secret into plain environment variables.

## Delivery gates

The normal brief is posted only when all of these are true:

1. Slack identity, private-channel read access, and `chat:write` scope verify.
2. Today's integrity run is `Complete` or `Partial` and has exactly one run row.
3. The snapshot is no more than three hours old.
4. Required source coverage is explicitly recorded.
5. Relevant task updates are complete.
6. The snapshot is explicitly marked safe for briefing.
7. Every referenced task rereads successfully and still matches its WI ID.
8. No referenced task changed after the snapshot.

If any gate fails, the worker attempts one bounded hold notice and records the
reason. An uncertain Slack send is never retried automatically.

## Feedback

Replies in the posted brief's thread are accepted only from configured feedback
users and only when they match an approved command with a WI ID, Decision Key,
or source link. The AWS agent stores the structured command in its durable job
ledger as `captured_for_reconciliation`. It does not update Working Tasks or
acknowledge the reply. The separate Working Task capture automation remains the
only reconciliation writer.

## Rollout

1. Merge and deploy the image with the feature disabled.
2. Verify `/healthz` on the new ECS revision.
3. Add the environment values above and deploy canary.
4. Confirm the health response shows the channel and `08:15 Mountain` setting.
5. Run a dry test against a copied run row or with an injected test client. Do
   not post a production test message.
6. Let the next integrity review populate `Briefing Snapshot`.
7. Verify the first natural 8:15 post or hold notice and its saved receipt.
8. Keep the legacy local Slack briefing automation paused.

Do not mark this capability live until the deployed task revision, health check,
first post receipt, and Notion source contract have all been verified.
