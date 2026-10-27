# Partner intake expansion

Implementation flags default off. Existing production stays unchanged until a reviewed deployment.

- Set REVIEW_MODE=thread: HubSpot reviews reply to their source submission in C0BQKEQLSKH.
- FORWARD_INTAKE_ENABLED=true: accepts Colby's app mention, eyes reaction, or edit of his message in C0C7HG8KYFL. Worker re-fetches the message using reactions.get. Both Colby's mention and Colby's reaction must be present on the same message. One canonical source-message review key handles either trigger order and repeated reactions. Attachments cannot authorize intake. A failed API approval check records an error without blocking other jobs; fix access and reapply the reaction.
- SPECIALIST_ROUTING_ENABLED=true: qualified supply leads -> C0CAL8C08RK mentioning U0ACSTWBYHG. Eligible events -> C0CAKA6PW8K mentioning U0AB1TSBK5X and U0APRAUGJ6P. DQ, needs-research, and risk holds do not route. Supply Route tiers do not route. Persist one send attempt per normalized contact email (or company/source fallback) and route. Uncertain sends are held, never automatically retried. This is not comprehensive corporate entity resolution across different people/domains.
- GMAIL_INTAKE_ENABLED=true: polls Gmail history approximately every 60 seconds while the review worker is available. Requires GMAIL_OAUTH_CREDENTIAL and exact account colby.henderson@runpod.io. Label display name Partner Inbound. Initial activation saves the current history cursor and skips existing mail; later label additions (including labels added to older mail) and new labeled messages enqueue once per Gmail message ID. Multiple inbound messages in one email thread are separate reviews. Label removals and self/sent messages do not trigger reviews. No email sending, drafts, labels, attachment downloads, or Gmail research searches. Missing/expired history preserves cursor and requires operator reconciliation, never an automatic historical backfill.
- Email cards go to C0C7R970C5T, with a source email link. Email cards omit existing Slack-specific action buttons; automatic Notion capture is outside this change.

## Slack configuration before activation

Use app A0C76FQTM62 (int-partner-agent). Verify reactions:read, channels:history, groups:history, app_mentions:read, chat:write and existing scopes. Subscribe to app_mention, reaction_added, message.channels, message.groups. Reinstall the app if scopes change. The agent must be a member of all source and destination channels. An edited mention is detected only when Slack message_changed events are delivered.

## Google authorization

1. In the company Google Cloud project, enable Gmail API.
2. Configure Google Auth Platform branding as Runpod Partner Agent, audience Internal, with your company support/developer email. If Internal is unavailable, ask IT to use a project in the company organization.
3. Add only https://www.googleapis.com/auth/gmail.readonly to Data Access.
4. Create OAuth client of type Desktop app named Runpod Partner Agent Setup. Download its JSON outside the repository.
5. In a local virtual environment install google-auth-oauthlib, then run scripts/authorize_gmail_intake.py with the downloaded file path. Sign in as colby.henderson@runpod.io and approve read-only access. Script verifies the AWS account and Gmail identity, and stores a refresh credential directly in team-partner-agent/pilot/gmail-oauth without printing it.
6. Grant the ECS execution role GetSecretValue on this one secret and inject its ARN as GMAIL_OAUTH_CREDENTIAL. Do not place the value in source control or chat.

Google guidance: https://developers.google.com/workspace/gmail/api/quickstart/python
History semantics: https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.history/list

## Coordination and deployment

Before enabling email intake/specialist routing, pause the old Claude intake task or explicitly remove overlapping sources and direct handoffs there. Do not assume the scheduled task has been changed by this code. Preserve the Google Form/application lifecycle work if it remains with Claude.

Keep the production fresh-start timestamp and OUTBOX_TEST_IDS unchanged. New specialist handoffs use their own durable queue, not the legacy Notion outbox. Reuse the receiver-first deployment command from task definition :6; do not replace it with an ordinary canary worker start. Complete traffic transfer, confirm old workers have stopped, reconcile receipts/ownership, then release workers. Do not replay completed failed reviews as part of activation.

## Test posts already sent (synthetic, no action needed)

- Supply: https://runpod-team.slack.com/archives/C0CAL8C08RK/p1793041671168909
- Events: https://runpod-team.slack.com/archives/C0CAKA6PW8K/p1793041671257459

These establish identity, mentions and destination access, not end-to-end automatic routing. Test a fresh HubSpot form; forward a synthetic request and apply both controls in either order; test one newly labeled email after baseline; verify one card and one eligible handoff despite event retries.

## Agreed activation scope (October 26)

Claude owns Partner Inbound Gmail capture and Gmail-origin specialist handoffs; the user confirms its task has been updated. Keep GMAIL_INTAKE_ENABLED=false in AWS and do not add Gmail credentials. AWS owns Slack signup reviews and approved forwards. Cross-system duplicate detection is not guaranteed by the AWS queue; Claude must check existing records and handoffs.

Research/University handoffs remain disabled. The intended destination is C0CCBS9NU2V, with Zoe McKinley as owner after her Slack identity is verified on or after November 2. Research requests must not fall through to the events channel. Existing research qualification cards can still be generated.

Specialist routing skips reviews marked already_in_play as well as blocked reviews.


## Review-card routing actions

New review cards include **Route to supply** and **Route to events** when specialist routing is enabled and the saved review has no spam, blocker or already-in-play flag. Only Colby can trigger these actions. A manual route may send a low-score lead; automatic tier thresholds remain unchanged.

The buttons use the standard specialist template, mention the destination owners, and cc Colby. They share the automatic handoff deduplication key (contact + destination), so clicking again or routing an already-forwarded lead does not post another copy. The card shows the delivered message link or an unconfirmed status requiring reconciliation. Uncertain sends are never automatically retried.

Older cards without saved routing evidence require a fresh review. Log in Notion and Park remain available; Draft in Gmail appears only when the review includes a suitable draft and email address. That action requests a draft through the Claude/Notion pipeline; it does not send email.


## Daily Slack review summary

When SUMMARY_ENABLED=true, the same AWS agent posts to SUMMARY_CHANNEL_ID (C0C7R970C5T, team-partner-biz-dev) at or after 5 p.m. Mountain on weekdays. It runs when the review queue is idle. Coverage is explicitly Slack signup reviews and approved forwards; Claude/Gmail leads are excluded until a separate integration is implemented.

The first report covers reviews since that day's Mountain midnight. Later reports start at the last confirmed reporting boundary, including late arrivals and weekends. Re-reviews of the same source message count once. Synthetic tests and email reviews are excluded. The summary includes scores, dispositions, next steps, source links and available specialist delivery receipts; it never infers delivery from a recommended route. Detailed listings are bounded at 25 reviewed items and 15 issues, with total counts retained. No automatic Friday weekly report is sent.

A single PostgreSQL review_meta value, daily_summary_state, records the frozen message and reporting window before posting. Only a returned Slack timestamp advances the boundary. A crash or ambiguous send leaves pending state and blocks subsequent reports until an operator checks Slack and reconciles the saved receipt. Do not clear pending state or retry without checking whether the message was delivered.

### VC/incubator handoffs

VC/PE/Incubator submissions classified under Partnerships now use the same gated specialist-delivery flow as events. Eligible Act now, Qualify and Route tiers go to `C0CCQ9311D2` (#feed-partner-startup-leads), mentioning Cameron McClendon (`U0B9ZRF3JUX`) and ending with Colby's cc. Other partnership types and individual Startup Program credit requests do not automatically enter this route.

New review cards also offer a Colby-only **Route to VC/incubator** action. Explicit routing allows eligible lower-score leads, while spam, blockers, already-in-play records and parked cards remain held. Automatic and manual routes share the same contact/destination receipt, preventing duplicate sends. Uncertain sends require reconciliation. The template keeps Region, Lead score, Assessment, request/qualification, a concise suggested review and source/CRM links. Daily summaries include available VC handoff receipts for Cameron. No historical leads are replayed by deployment.
