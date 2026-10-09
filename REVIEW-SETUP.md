# Auto-review of partnership digests

When HubSpot posts a *PARTNERSHIP MQL DIGEST* in #team-rev-sales-partnerships,
Team Partner Agent qualifies it and posts a review card. It uses the same rules
as the Notion intake pipeline: four checks, a score, the do-not-pursue gate,
routing and one suggested next step.

Phase 1 is advisory only. The bot reads the digest, runs a short web search on
the person and the company, and posts the card. It never writes to Notion or
HubSpot, never drafts or sends email, and never contacts anyone. The scheduled
Notion pipeline is still the only thing that writes those records.

Chat mentions keep working. When `REVIEW_ENABLED=true`, `review.py` runs both
features on the same queue and worker.

## Modes

- `REVIEW_MODE=shadow` (default) posts each card to #team-partnerships-development
  (`REVIEW_SHADOW_CHANNEL_ID`), with a link back to the submission. Nobody in
  the submissions channel sees it.
- `REVIEW_MODE=thread` replies under the digest itself.

## Setup

1. **Slack app.** In Team Partner Agent → Event Subscriptions → Subscribe to bot
   events, add `message.channels`. Add `message.groups` too if
   #team-rev-sales-partnerships is private. Save, and reinstall if Slack asks.
   The bot needs `channels:history` (already granted) and has to be in both
   channels (it already is). Slack will now send the bot every message in
   those channels. Anything that isn't a top-level HubSpot digest is dropped
   before it's queued.
2. **Railway (production, team-partner-agent).** Add `REVIEW_ENABLED=true` and
   `REVIEW_MODE=shadow`. Leave the other variables as they are. The defaults are
   in `.env.example`.
3. **Merge to `main`.** Production deploys from `main`.
4. **Shadow week.** Compare the cards in #team-partnerships-development with
   what the Notion pipeline decides for the same leads.
5. **Go live.** Set `REVIEW_MODE=thread`.

To turn it off, set `REVIEW_ENABLED=false`. Chat goes back to running on
`chat.py` on its own.

## Web search

Reviews use Anthropic's server-side web search tool, so it has to be enabled
for the API organisation behind `ANTHROPIC_API_KEY`. Each review runs up to 5
searches. If search isn't available, set `REVIEW_WEB_SEARCH=false`. Reviews
will still run, but identity and company checks rely on the digest alone, so
expect more "unverified".

## Behaviour and limits

- Only top-level messages from HubSpot's bot (`REVIEW_BOT_IDS`, default
  `B083VARARJ7`) that contain the digest header are reviewed. Each message is
  reviewed once; Slack retries are deduplicated.
- The model returns structured JSON, and the bot builds the card itself.
  Unknown routes and types fall back to safe values, mentions are stripped,
  and only `https://` source links are kept.
- If the model fails, the card is a short notice that names nothing sensitive.
  If a Slack post is uncertain, it is marked `review` in `chat.sqlite3` and is
  never resent automatically.
- Work is serial. A review takes roughly 30-90 seconds, and chat mentions wait
  behind it.
- `/healthz` reports `chat_enabled: true` whenever the receiver is on, even if
  only reviews are enabled.

## Phase 2 (not built)

- Approve / Park buttons on the card, which need Slack interactivity.
- The bot creates the Notion row, and the scheduled pipeline stops creating
  rows from Slack.
- HubSpot contact links on the card.
