# Deploying immediate intake

## Current state

Implemented, locally tested, and not deployed. No live Slack or Notion calls have been made. Tests exercise signed HTTP events, durable storage, message filtering, extraction, page payloads, duplicate lookup, uncertain-write recovery, and threaded replies using fake clients. The container packaging has not been executed here.

The first service is an intake processor, not a general conversational assistant. It does not require an AI API key. It captures requests with `Contact:` or `Company:` fields, including the existing form digest format. Manual messages should include those labels plus the actual request. It asks for details when it cannot identify the intake. Arbitrary research commands, remote attachment downloads, automatic forwarded-link retrieval, and qualification scoring are not implemented. Source text and links in the event are preserved; source instructions are never executed.

## Hosting shape

Use an approved Linux host with Docker Compose, an HTTPS reverse proxy, and persistent storage. `compose.yaml` runs a web receiver and one worker sharing the same local SQLite volume. Do not place these processes on separate machines with independent disks, use a network SQLite filesystem, or scale worker replicas. A managed multi-host deployment needs a shared database/queue adaptation first.

GitHub stores code; it is not the always-running event listener. No host is selected yet.

## Setup

1. Create a Slack app from `slack-manifest.json`, replacing its placeholder request URL with the chosen HTTPS host. Its scopes allow reading channel messages and posting replies. Install it in the Runpod workspace and invite it to the two configured channels. Installation and access must be completed by an authorized administrator.
2. Store the Slack signing secret, bot token, workspace ID, and this app's ID in the host secret store (or a host-only `.env`). Never paste or commit tokens.
3. Identify the form integration's real `app_id` and `bot_id` from a trusted Slack message/event. Set both FORM_APP_ID and FORM_BOT_ID. Form messages remain disabled until both match; a message title alone cannot authorize a bot.
4. Give a Notion integration read and insert access to Partner Intake & Qualification and set NOTION_TOKEN. The code uses Notion API version 2025-09-03. Existing connector access in Codex is separate from this deployed application's credentials.
5. Start the web receiver with INTAKE_ENABLED=false. Configure HTTPS forwarding to localhost port 8080. `/slack/events` validates the Slack signature and timestamp, including URL verification. `/healthz` reports receiver state only; it does not prove that the worker or downstream services work.
6. Review the destination and test plan, set INTAKE_ENABLED=true, and start both processes. With Compose: `docker compose up --build -d`. Web and worker both need the same secrets and volume. The worker polls the durable local queue every half-second; external API and workload latency determine completion time.
7. Post one clearly labeled test request in the manual capture channel. Verify exactly one new Notion entry and its thread reply, then replay the event to verify no duplicate. Test a real form notification and confirm the trusted bot filter. Clean up test records deliberately after inspection.

## Message behavior

- No mention is required in the manual capture channel. Human messages in either top-level posts or threads are eligible; the pilot treats each message as a separate candidate intake, not conversational memory.
- Form notifications require matching channel, app ID, and bot ID. Bot posts elsewhere, edits, deletion events, and unrelated channels are ignored.
- The receiver durably queues an accepted event before acknowledging Slack. That acknowledgement is transport-level, not a visible “working” message. The worker starts promptly and posts the result or clarification in-thread.
- Notion entries start New/New/Not ready. Original content is preserved in the body. No emails, qualification decisions, commercial commitments, or automatic partner conversions occur.
- Submitted is deliberately left unset until the original form date can be verified. The Slack notification timestamp is explicitly labeled in the body.
- The same Slack message is deduplicated locally and by its permalink in Intake Ref. Forwarding the same request as a different message may produce a different reference; cross-source duplicate detection is not yet implemented.

## Failures and operations

Monitor both processes and the `intake_jobs` table. States `failed`, `review`, and reply states `review` or `sending` require operator inspection. The table contains internal request text; restrict access and define retention before broad rollout. Do not expose the database or include it in Git.

If Notion creation times out, the next pass searches for the existing Intake Ref. If no entry is confirmed, it stops for reconciliation rather than blindly retrying. A crash while writing follows the same path. This avoids automatic duplicate creation at the cost of manual recovery for ambiguous failures.

If a Slack send fails or crashes in-flight, delivery may have occurred. The worker will not blindly resend. An operator must check the source thread. Ordinary pre-write failures are also retained for review; automated backoff and a recovery UI are future improvements. Do not delete queue records to retry writes.

To pause processing, stop the worker and disable intake on the receiver. Back up the persistent volume. A single-worker lock prevents concurrent local workers from racing Notion creation.

## Sources

- [Slack request verification](https://docs.slack.dev/authentication/verifying-requests-from-slack/)
- [Slack Events API](https://docs.slack.dev/apis/events-api/)
- [Notion data source queries](https://developers.notion.com/reference/query-a-data-source)
