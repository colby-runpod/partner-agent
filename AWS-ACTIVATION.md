# AWS activation record

## Verified October 23, 2026

- User authorized stopping Railway despite unrecovered ephemeral history.
- Railway Team Partner Agent deployment was removed; status then showed no active deployments. Partner Ops is separately stopped/crashed and has not been migrated by this rollout.
- Slack token identity: app A0C76FQTM62, bot U0C7372ET45 (`int-partner-agent`), workspace T06F03V8NUQ.
- Required source, review, supply, event and ask-partners channel memberships verified.
- User confirmed review output stays in C0C7R970C5T, whose actual name is team-partner-biz-dev.
- Fresh-start boundary: 2026-10-23T19:38:28.519980+00:00. Do not change on redeployment.
- Runtime image: sha256:6f80e2abb667730436d54d7e5f2f87f25d7dcc6357d730ead5f6ebbcfa9ca650 (merged application d500f8e).
- Runtime references five Secrets Manager entries: database-runtime, slack-bot-token, slack-signing-secret, notion-token and anthropic-api-key under team-partner-agent/pilot. No administrator/migrator credentials are attached.
- Execution role ecsTaskExecutionRole has PartnerAgentRuntimeSecrets, scoped to those five secret ARNs.

## Restricted receiver rollout

Review input C0BQKEQLSKH; shadow output C0C7R970C5T. Chat, tasks, general-agent, intake and scheduled summaries disabled. Review and signed interactivity enabled. Claude model claude-sonnet-4-6 was returned by the authenticated models API. Outbox enabled with exact OUTBOX_TEST_IDS=402ff732-fc34-81d8-85e3-f6c01f1b2fdd; this already-posted pre-cutoff fixture cannot release historical queued work.

Activation request accepted; service deployment klJIzwRfMHdvTZe-wDl15 is being verified. Do not consider Slack callbacks verified from a format check or a self-signed request. Slack must successfully verify its actual Event Subscriptions URL and send a new event.

Slack app: https://api.slack.com/apps/A0C76FQTM62
Events URL: https://te-9776df4994d4439c9de9b95961285a76.ecs.us-east-2.on.aws/slack/events
Interactivity URL: https://te-9776df4994d4439c9de9b95961285a76.ecs.us-east-2.on.aws/slack/interactions

Do not run the Deploy AWS standby workflow as a normal active release: it intentionally removes runtime settings/secrets and disables processing. Future active deployments must preserve the cutoff and respect durable worker ownership. If a worker crashes, verify its termination and reconcile uncertain receipts before clearing any hold. Do not automatically retry an active deployment with a new cutoff.

`scripts/prepare_aws_activation.py` prepares a secret-reference-only container configuration from a stable standby revision. It never deploys or grants IAM access. It requires an explicit cutoff and output channel, uses an exclusive output-file create, and refuses unpinned images or missing secret versions.
