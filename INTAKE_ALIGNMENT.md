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


## Latest brief review, November 5

Compared with https://docs.google.com/document/d/16GnE_w248fLbEQZTn31qtAXh9hIpny-yHCxMWppxauQ and the live Operating Model v2026-11-05.2. No new routing owner assignments were added. The additional emphasis is continuous Guide reconciliation.

- Recaps refresh the Guide and include its version, durable drift count for the reporting window, fallback/read state, and overdue or missing review date.
- Before a new specialist delivery, resolve every owner as an active Slack user and confirm channel membership with pagination. Failure prevents the send and creates a DRIFT reconciliation entry. Runtime Slack access needs users:read and the appropriate conversations.members scopes for the destination channels; verify these before activation.
- Owner-only version changes can refresh in a running process when the policy fingerprint is unchanged. Changed policy text or new scenario pages hold intake/handoffs for review. New channels still require an allowlist release. A process restart with a newer Guide version requires a reviewed baseline; the current cache is process-local, not durable across restarts. This is a remaining gap against fully dynamic Guide operation.
- The Guide directory is checked for scenario pages; none were present during this review. Newly added scenarios are read and held for applicability review rather than automatically interpreted. General prose rules are not executable configuration.
- Original Slack source reactions now recognize both tpa: and slack: intake references.

Documentation conflict: the brief's channel allowlist omits the HubSpot intake channel while requiring reviews in that channel's threads, and omits the previously authorized signal-summary channel. Existing explicitly requested thread replies and signal summaries are preserved; the Guide should document these exceptions. No new channel access or posting is authorized by this change.

Current acceptance: local tests only. No deployment, live posts or queue replay. Existing cutover requirements remain.


## Guide v2026-11-05.4 reconciliation

The Guide changed during the November 5 rollout. Version .3 added the Weekly Partnerships Update scenario; .4 allows the Friday input reminder to post through Colby's scheduled task. The AWS agent does not send that reminder or capture its ordinary replies. Weekly feedback remains bound to a delivered agent outbox review parent; adding Zoe to approved reviewers does not permit arbitrary Friday-thread capture or final publication.

The runtime now recognizes and reads the reviewed weekly scenario, including when its page is nested in the Guide library callout. Unknown scenarios still require review. Current reviewed baseline is .4. New baseline and directory layout must pass runtime preflight before another rollout. No scheduled task or personal-account post is created by this release.


## Guide v2026-11-05.5 reconciliation

Rule 8 assigns replies for channel-routed leads to the receiving team. Automatic review cards no longer generate email drafts for specialist routes when alignment is enabled. Explicit Colby draft requests still delegate to the pipeline and state that exception. No automatic affiliate pointer emails are requested. Sales and Partnerships behavior is unchanged. The external pipeline must apply the same rule; the AWS agent cannot cancel or rewrite its independently created Gmail drafts.
