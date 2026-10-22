# GitHub to AWS standby

This workflow deploys the existing ECS Express service in health-only standby. It does not migrate databases, install business credentials, switch Slack endpoints, or enable processing.

## Setup

1. Deploy `infra/github-standby-role.yaml` in account 626963115342, region us-east-2, using CloudFormation with CAPABILITY_NAMED_IAM. No GitHub OIDC provider existed when inspected on October 21, 2026. Reuse an existing provider if another team creates one before deployment; do not create a duplicate.
2. Set repository Actions variable `AWS_STANDBY_DEPLOY_ROLE_ARN` to the stack's DeployRoleArn output. This is an identifier, not a secret. No AWS access keys belong in GitHub.
3. Review and merge this branch. Check Railway's GitHub auto-deployment setting FIRST: merging main can replace the old Railway container and lose unexported local state. Hold the merge until auto-deployment is disabled or that state is safely exported.
4. Run **Deploy AWS standby** from main in GitHub Actions. The workflow is manual-only; opening a PR or pushing does not invoke it.

## Behavior and limits

Tests and a credential-free local standby smoke test precede AWS authentication. Images use unique commit/run/attempt tags; deployments reference the ECR digest. The deployment explicitly supplies the standby environment, no business secrets, and the service.py command. It checks the deployed task configuration, image digest and public health endpoint. It serializes runs and never cancels an in-flight deployment.

The role trusts only this repository's main branch and can push to one ECR repository and update one ECS service. It cannot fetch Secrets Manager values or create databases. Repository administrators must protect main and workflow edits: the IAM role can update the service, so standby is a reviewed workflow constraint, not an IAM-enforced runtime restriction.

A green deployment proves only that the standby works. Active-mode cutover requires separate review, durable state integration, current receipts, fresh restricted credentials and end-to-end tests. The single-zone database proposal remains separate and unprovisioned.

If deployment fails, inspect the Actions and ECS events. Do not assume a successful update request means rollout completion. Re-running generates a new immutable tag. Do not restore old posting receipts to force retries.
