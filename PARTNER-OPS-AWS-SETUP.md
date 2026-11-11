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

## Capture-only pilot

After the disabled deployment, the operator may add `--enable-capture-test` to enable only Colby's mentions and configured handshake captures in private `#ask-partners`. This mode persists the capture and adds its processing reaction. It does not enable shadow triage, review replies, the Working Task briefing, Notion writes, Linear writes, or general message sending.

The capture-test allowlist is intentionally fixed to channel `C0C7HG8KYFL` and user `U0BEHPEJA6L`. A broader channel or user rollout requires a separate reviewed change.

## Read-only shadow triage pilot

Add `--enable-shadow-triage-test` together with `--enable-capture-test` only after the dedicated `partner-ops/notion-token` and `partner-ops/anthropic-api-key` secrets pass read-only preflight. Shadow triage rereads relevant Working Tasks and produces a guarded recommendation in the internal ledger. It does not post the result to Slack or enable Notion, Linear, message, briefing, or approval writes.

The initial model is fixed to `claude-sonnet-4-6`. Changing the model, expanding Notion access, or enabling a Slack review response requires a separate reviewed stage.

## Guarded Slack review pilot

After one representative shadow evaluation has been inspected, add
`--enable-slack-review-test` together with the capture and shadow-triage flags.
This stage posts the generated review card only in the originating
`#ask-partners` thread, tags the requester, and asks one decision question at a
time. Only Colby's allowlisted Slack user may answer with the buttons or the
supported short replies.

```text
python scripts/deploy_partner_ops_aws.py \
  --subnets subnet-a,subnet-b,subnet-c \
  --deploy-disabled \
  --enable-capture-test \
  --enable-shadow-triage-test \
  --enable-slack-review-test
```

Despite the historical `--deploy-disabled` acknowledgement name, this stage
allows only the narrowly generated internal review response. Notion creation
and updates, Linear writes, the Working Task briefing, daily previews, general
message sending, email, and execution remain disabled. Recorded Slack answers
do not authorize those capabilities.
