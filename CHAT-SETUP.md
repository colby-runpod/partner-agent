# Slack thread chat on Railway

Mention Team Partner Agent in a thread to ask questions or summarize the submission. Tag it again for follow-ups. It reads the parent and thread through the requesting message, including text blocks/attachments, then replies to that thread and mentions the requester. It does not fetch websites, download files, write to Notion, or proactively qualify every submission. Thread text is sent to OpenAI with store:false; configure your API project appropriately. The pilot defaults to Colby's Slack user and the submissions channel only.

## Setup

1. In Team Partner Agent's Bot Token Scopes, add app_mentions:read alongside channels:history, groups:history, chat:write. Add app_mention under Event Subscriptions > Subscribe to bot events, save, and reinstall if prompted. Keep Socket Mode off. Invite this app to the submissions channel.
2. Keep the new app's SLACK_BOT_TOKEN, SLACK_SIGNING_SECRET, SLACK_TEAM_ID, and SLACK_APP_ID in Railway. Rotate any exposed old token. Do not put secrets in Git or chat.
3. In the existing Railway service, attach a persistent volume mounted at /data. Use exactly one service replica. Do not add a second worker service: service.py starts both processes sharing this local disk. Clear any custom Start Command so Docker's CMD runs, or explicitly set python service.py. Keep networking port 8080. Configure health check /healthz and a restart-on-failure policy.
4. Add OPENAI_API_KEY with a funded API project's key and OPENAI_MODEL with a Responses-compatible model available to that project (for example gpt-6-astra). Set CHAT_DB=/data/chat.sqlite3, CHAT_CHANNEL_IDS=C0BQKEQLSKH, CHAT_USER_IDS=U0BEHPEJA6L. Keep INTAKE_ENABLED=false and set CHAT_ENABLED=true when ready. Deploy the updated code and variables together.
5. Check /healthz reports chat_enabled:true and intake_enabled:false. This confirms the receiver configuration, not external API access. The supervisor exits if either process fails, allowing Railway to restart both.
6. Post a NEW mention in the submission thread. Old disabled mentions are not automatically replayed. Verify one reply in that thread, then tag again with a follow-up. If thread access fails, the bot explains that instead of generating a summary from incomplete context.

## Limits and operations

Up to 500 thread messages / 60,000 context characters; oversized or incomplete threads return an explicit failure. Processing is serial. API calls have a 25-second timeout. Model/API failures yield a thread error message; tag again after correcting configuration. No automatic notification for ordinary channel posts.

Queue entries retain request text and generated replies on the persistent volume. Restrict access and establish retention. Slack retries are deduplicated by event ID and message identity. An uncertain Slack send is marked review and is never automatically resent; inspect the source thread and chat.sqlite3 before retrying. A worker crash during generation may repeat the model call, but a saved answer is reused for reply delivery. Do not run multiple replicas or independently mount this SQLite file on different hosts.

Automated tests mock Slack and OpenAI; live token/model access and actual thread delivery still require the deployment test above.
