# Partner Ops AWS setup

This is a separate AWS service for the `partner-ops` Slack app
(`A0CA2C43D4L`). It does not reuse or modify the live `int-partner-agent`
service. The first deployment is health-only: capture, briefing, review posts,
interactive approvals, Notion writes, Linear writes and message sending remain
off.

## Architecture

- One ECS Express task running `python p_ops_service.py`.
- One immutable `partner-ops-agent` ECR repository.
- One encrypted EFS access point mounted at `/data` for the SQLite runtime.
- Minimum and maximum task count fixed at one. Do not scale this SQLite release.
- Dedicated execution role, log group and Secrets Manager entries.
- Dedicated `partner-ops` Slack identity. The generated Slack secret values are
  disabled-deployment placeholders and must be replaced before Slack routing.

## Foundation

Deploy `infra/partner-ops-foundation.yaml` in account `626963115342`, region
`us-east-2`, with `CAPABILITY_NAMED_IAM`. Use the default VPC and its three
public subnets:

```text
VpcId=vpc-...
SubnetIds=subnet-a,subnet-b,subnet-c
```

The stack creates the repository, EFS storage, mount targets, security groups,
log group, execution role and four startup secrets. EFS, access credentials and
review credentials are retained if the stack is removed.

## Disabled deployment

Run from a clean commit on `main`:

```text
python scripts/deploy_partner_ops_aws.py \
  --subnets subnet-a,subnet-b,subnet-c \
  --deploy-disabled
```

The script builds and pushes a unique Linux/amd64 image, registers a task
definition with EFS mounted at `/data`, creates or updates only the
`partner-ops-agent` Express service and waits for guarded health. Success
requires `/healthz` to report:

- `slack_app_id=A0CA2C43D4L`
- `capture_enabled=false`
- `working_task_brief_enabled=false`

It does not set Slack URLs or enable any processing gate.

## Slack credential cutover

Before configuring Slack Events or Interactivity:

1. Replace `partner-ops/slack-bot-token` with the bot token from the
   `partner-ops` Slack app.
2. Replace `partner-ops/slack-signing-secret` with that app's signing secret.
3. Force a new ECS deployment and verify `/healthz` still reports the Partner
   Ops app ID.
4. Add the app to `#ask-partners` and verify read access before posting.
5. Set the Slack Event URL to `https://<partner-ops-endpoint>/slack/events` and
   the Interactivity URL to
   `https://<partner-ops-endpoint>/slack/interactions`.
6. Enable one capability at a time. The Working Task briefing remains off until
   the Notion token, feedback user IDs, integrity snapshot and first natural
   delivery test are ready.

## Important limits

- Do not point the `int-partner-agent` Slack app at this service.
- Do not copy the Team Partner Agent token or signing secret.
- Do not run more than one task while the p-OPS ledger is SQLite.
- A healthy deployment is not proof that Slack or the briefing is live.
- Mark the Notion Ops record live only after deployed revision, health, Slack
  access and the first natural receipt are verified.
