# Partner Intake Operating Model implementation

Built on branch `codex/intake-operating-model`. Not deployed or live-verified.
Reviewed specification: [Operating Model](https://app.notion.com/p/40fff732fc34810eac53f085a6d47576), version 2026-11-05.2.

## Contract

- AWS captures Slack submissions/approved forwards before review, using the exact submission reference. It does not merge unrelated requests just because the email matches.
- Advisory review does not overwrite pipeline qualification. Existing saved routes win unless Colby explicitly redirects.
- The review worker produces Notion outbox rows. Only the outbox worker sends specialist handoffs. Pipeline/AWS copies share intake/destination receipt reconciliation.
- Before a specialist send, require the intake relation, matching Forward To, current owner mentions, no explicit park/closure, and complete duplicate reconciliation. Adopt an existing delivery rather than reposting.
- Startup Program: Cameron. VC/Incubator and research: Zoe. Marketing: Adriana/Carmela. Affiliate/creator: Colby cc until an owner is configured.
- Low score, incomplete verification and pre-development supply do not block routing. Spam, synthetic sources, empty payloads, explicit parks and cited sanctions/export-control findings do.
- Append audit entries with stable event identity and Guide Version. Confirmed Slack receipts update intake status and source reactions. Pickup requires a configured owner or Colby; conversational replies do not imply acceptance.
- Email preparation requests belong to the pipeline. Requests require the partners alias/signature and current Suger signup URL; no automatic sending.
- Daily recap reads shared intake/outbox records, including Gmail, and separates decisions, drafts awaiting send, handoffs/pickup, older work, closures and delivery failures.
- Weekly feedback, general Q&A and signal updates retain their own scopes and existing channel permissions.

## Configuration and rollout

`INTAKE_ALIGNMENT_ENABLED=true` activates shared capture, transport, audit, pipeline draft delegation and recap. Requires `NOTION_WRITE=true` and `OUTBOX_ENABLED=true`.

Do not flip this setting on the currently running old image. Deploy tested code first with worker holds using the existing guarded ECS release procedure. No backlog is sent by merely building this branch.

Before activation:

1. Verify the runtime integration can read/write intake, outbox and Activity Log, and read the Guide. The connector's access is not proof of runtime access.
2. Verify the actual bot's channel memberships and owner identities. Preserve source-channel replies, weekly and signal channels.
3. Audit queued rows and current `OUTBOX_TEST_IDS`/`OUTBOX_CREATED_AFTER`. A test-ID restriction is deliberately not bypassed by code. Remove it only during the reviewed cutover.
4. The pipeline cancels obsolete/duplicate rows and refreshes still-valid handoffs using current owners. The agent holds and reports rows older than 72 hours; it does not silently refresh/replay them.
5. Enable alignment only after old writers stop. Verify one natural lead per source: one record, one delivery, receipt, audit, marker and pickup.
6. Update deployment evidence in both Notion documentation entries. A merge/test is not proof of deployment.

## Guide changes

Every 15 minutes the process attempts to read and validate the guide and route table. Known destinations can refresh owner IDs. It records drift and uses its last reviewed snapshot when the page is unavailable or its version/rules need a code review. A new channel or policy version is not executed as arbitrary prose. Review/version changes must be reconciled into code. The separate Friday Claude guide-review task is not created or changed by this release.

## Failure semantics

- A Slack send timeout is never blindly retried. The durable send fence remains for reconciliation.
- Slack success followed by Notion failure retries the writeback, not the post.
- A missing route, wrong owner mention or incomplete history scan fails the row visibly.
- A Notion creation timeout is held for reconciliation; do not create again without checking the exact source/event identity.
- A draft request is Pending, not Draft created or Email sent. Colby remains the sender.
- Historical intake records and queued content are not rewritten by this implementation.

## Acceptance evidence

Run `python -m unittest discover -q`. New contract tests cover delivery adoption, duplicate queue rows, stale rows, parks, wrong routes, source markers, owner-only pickup, distinct submissions, legal/test exclusions, all-source recap and failure-before-review.

Outstanding live acceptance: runtime permissions, actual backlog reconciliation/cutover, deployment, channel/owner membership and natural end-to-end events. Do not mark the Operating Model checklist live until those are verified.
