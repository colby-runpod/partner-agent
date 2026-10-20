# Team Partner Agent outbox rollout

## Reviewed environment (2026-10-20)

Repository: `colby-runpod/partner-agent`, production main at `97e58bd` during inspection.
Railway project: inspiring-creativity, production.
Team Partner Agent service: `9b0e12de-e9b2-463a-ac52-d26d3cfdbedb`, online at `partner-agent-production-7064.up.railway.app`.
p-OPS is a separate online service (`3efed9af-7674-46ba-8875-9e640afd53f2`), not the outbox sender.
PartnerOS/Biz Dev is the separate research/review web+worker app. The native Notion Partner Agent entry describes an older pilot, not this Slack bot.
Railway displayed a temporary build pause during inspection. Existing deployment online does not prove this change is deployed.

## Release and configuration

1. Merge this reviewed change into main, keeping existing root Dockerfile and `python service.py` deployment. Do not point the Python bot at `/partneros`.
2. Keep `OUTBOX_ENABLED=false` (or absent) until preflight. Keep `SCREENING_V2_ENABLED=false` (or absent) to preserve existing digest review decisions. The latter is a separate optional advisory upgrade; the scheduled intake task must receive INBOUND_SCREENING_POLICY.md independently.
3. Use the existing bot token for user `U0C7372ET45`, configured `SLACK_TEAM_ID`, signing secret and `NOTION_TOKEN`. Never put secrets in GitHub, chat or Notion inventory fields. `auth.test` checks that the sender is the intended bot and workspace before any posting.
4. Retain `REVIEW_ENABLED=true` and `INTERACTIVE_ENABLED=true`. The service starts a separate supervised outbox worker so queue delivery cannot block digest reviews. Use one replica and the same persistent `CHAT_DB` volume as the current review service. Its outbox delivery/event tables and lock must survive deployments.
5. Grant the installed app `users:read` if missing (needed to ignore other bots); retain chat:write, reactions:read and the existing channel history scopes. Reinstall if scopes changed. Retain message.channels/message.groups/reaction_added subscriptions and the signed `/slack/events`, `/slack/interactions` endpoints. Add the bot to all three channels. Confirm Dean is in the supply channel, and Carmela, Adriana and Tanya are in events.
6. The existing Notion integration must read/write Partner Agent Outbox (`b9f3d371-6c3b-47f5-880a-47342ae69456`) and read/update the intake source (`ffecfb43-625c-42c6-a6af-1f8951641bba`). The outbox code restricts intake updates to the three invitation fields, only after Colby's click. Sharing through the user's Notion connector does not prove the Railway token has access.
7. In the deployed service run `python outbox.py check`. This is read-only; it verifies bot identity and source readability and prints only queue count. It does not verify channel membership or write permissions.
8. Set `OUTBOX_ENABLED=true` on Team Partner Agent only and deploy once the build pause clears. **This begins draining all due Queued rows**, including the ten real invitation items. Current inventory confirmed 13 Queued rows. Cancel or defer anything that should not post before activation.

No additional Railway service or database is required. Do not enable this worker on p-OPS or PartnerOS. `NOTION_WRITE` continues to govern legacy review buttons; OUTBOX_ENABLED is the explicit, separate authority for the narrow outbox workflow.

## Acceptance before pipeline cutover

- Parent OUT-1 posts once as plain text in #ask-partners. Ten related invitation rows post as replies with Approve/Decline/Call first buttons. Supply/events TEST rows go to their own feeds. Every row has Message TS, Permalink, Posted At and Posted. A second poll posts nothing.
- Invalid channel becomes Failed with `channel not allowed`. Future Not Before stays Queued. Cancelled stays untouched.
- Colby's check reaction on a supply TEST row confirms pickup; bot reactions do nothing. A non-Colby human reply confirms pickup. Hand-off pickup changes only the outbox.
- Non-Colby button click gets the ephemeral refusal. Colby Approve/Decline updates exactly Invitation Decision, Invitation Approved By and Invitation Approved Date on related intake rows, then marks the Slack item decided without buttons. Call first changes no intake fields. Use a designated test intake row for this acceptance check; do not approve a real prospect just to test.
- Check actual Slack rendering of bold, https links and backticked email. Body is posted verbatim, never model-rewritten. Fix malformed producer text in the outbox; do not silently alter it in the bot.
- Only after live acceptance 1-3, switch the scheduled Claude pipeline from user-identity Slack posting/DMs to the outbox. Update its scoring instructions separately. No external email sends are added.

Local fake-API tests verify behavior, not live access, Slack visual rendering or successful deployment. The initial seeded rows remain queued until activation.

## Failure and recovery

Polling is every 180 seconds, oldest first. Definitive Slack API rejection retries once, then Failed with its error. An HTTP/network timeout may mean Slack accepted a post: the durable `sending` fence holds it for reconciliation instead of reposting. This is intentionally stricter than blind retry. A saved Slack timestamp resumes Notion writeback; it never reposts. Buttons/pickup use a durable event queue and retry writebacks. First accepted decision wins.

For an ambiguous result: disable OUTBOX_ENABLED; inspect the channel for the exact post and the SQLite outbox_delivery record. If it exists, reconcile its receipt into that existing ledger row and outbox row; do not clone/requeue it. If no post exists and non-delivery has been established, an operator can explicitly repair the fenced row before retrying. No automatic reset is provided. Never delete the ledger or restore an older volume to retry delivery. After losing the ledger, posted rows are deliberately not adopted for editing.

Rollback: set OUTBOX_ENABLED=false. Existing digest reviews continue; queued rows remain queued. Leave the delivery ledger intact. Keep the pipeline on its existing delivery route until bot acceptance passes; avoid running both senders for the same item.

## Sources used for API details

- https://docs.slack.dev/reference/methods/chat.postMessage/
- https://developers.notion.com/reference/filter-data-source-entries
