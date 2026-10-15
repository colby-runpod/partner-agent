# Auto-review of partnership digests

When HubSpot posts a *PARTNERSHIP MQL DIGEST* in #team-rev-sales-partnerships,
Team Partner Agent qualifies it and posts a review card. It uses the same rules
as the Notion intake pipeline: four checks, a score, the do-not-pursue gate,
routing and one suggested next step.

The review itself is advisory. The bot reads the digest, runs a short web
search on the person and the company, and posts the card. It never writes to
HubSpot, never sends email and never contacts anyone. It writes to Notion only
when someone clicks a button on the card (see *Buttons*). The scheduled Notion
pipeline does everything else.

Chat mentions keep working. When `REVIEW_ENABLED=true`, `review.py` runs both
features on the same queue and worker.

## How the call is made

Claude researches the submission and reports evidence: four checks (identity,
company, fit, potential) and a set of yes/no signals, such as a specific
motion, a named customer, quantified volume, spam or a blocker. It doesn't
pick the label. `decide()` in `review.py` applies the intake rules to those
facts, so the same facts always get the same score and decision:

- **DQ:** spam. **1:** a legitimate sender at the wrong door. **2:** an
  unverified person, company or fit, or supply below the bar.
- **5:** all four checks verified. **4:** identity, company and fit verified.
  **3:** anything else.
- 4 and 5 are **pursue**. A 3 is **pursue** only with a named customer, real
  volume or a warm intro, **triage** with a specific motion or a notable
  counterparty, and **do not pursue** otherwise.
- A blocker (for example sanctions) or "already in play" turns pursue into
  **triage**. Self-serve routes become **point to self-serve**. A blank or
  unusable ask is always **triage**, never a DQ.

To change a rule, change one line in `decide()` and its test.

## Re-reviews

If a submission already has a review (for example, you ask for a manual
review in its thread), the new card says on its second line whether the call
matches the earlier one. If it changed, it gives the old score and decision
and which checks moved, so you can pick the call you agree with or reply with
a correction.

## Drafts on the card

Each card can carry copy-ready text in the same voice the Notion pipeline
uses:

- **Probe reply:** one question, for triage and pursue cases.
- **Invitation:** for 4s and 5s on the Partnerships route. Send it from
  partners@runpod.io once you approve.
- **Pointer reply:** the startup program, or the referral and affiliate
  program.
- **Draft for Dean:** supply leads, in the 3c format. You send it.
- **Decline:** not drafted. The card only notes the date a soft decline
  becomes eligible, 7 days after submission.

The first line of every card is a one-line summary (decision, score, route,
headline), so the thread preview tells you the call.

## Daily summary and alerts

On weekdays at `SUMMARY_HOUR` (default 17) US Mountain time, the bot posts to
`SUMMARY_CHANNEL_ID` (default #team-partner-biz-dev). The summary has a
count by decision, one linked line per submission and a *Needs attention*
section. If a review fails, or a card may not have posted, the bot alerts that
channel right away. Set `ANTHROPIC_KEY_EXPIRES=YYYY-MM-DD` to get a key
reminder in the summary during the last 14 days. The summary posts at most
once a day and is never retried. Records are kept in `chat.sqlite3` on the
`/data` volume.

## Feedback

React :+1: or :-1: on a card, or reply in its thread with
`@Team Partner Agent should be triage`. The calls you can name are pursue,
triage, self-serve and do not pursue (`actually` and `correction:` also work).
Votes are one per person per card, and the latest one counts. Removing a
reaction removes the vote. A correction gets a one-line acknowledgment in the
thread. Only `FEEDBACK_USER_IDS` can give feedback; if it's unset, that falls
back to `CHAT_USER_IDS`. The bot recognizes its own cards through
`REVIEW_BOT_USER_ID` (default `U0C7372ET45`).

On Fridays, after the daily summary, a *Weekly review check* shows the
thumbs up and down, how often the bot and you agreed, each correction, and
the most common miss if it happened twice or more. A repeated miss is the cue
to change a rule in `decide()`.

Slack setup: add the `reactions:read` scope and subscribe to the
`reaction_added` and `reaction_removed` bot events, then reinstall the app.
Cards posted before this release don't record their message ID, so reactions
only count on new cards. Corrections in a thread work for any card from PR A
on.

## Buttons

With `INTERACTIVE_ENABLED=true`, each successful card gets buttons. Only
`FEEDBACK_USER_IDS` can use them; clicks from anyone else are ignored.

- **Log in Notion** creates the intake row in Partner Biz Dev, with Intake Ref
  `tpa:<channel>:<digest ts>` and the review's checks, score, decision, route
  and summary filled in. If a row already exists for that ref, or for the same
  email submitted within the last 3 days, it links that row instead of
  creating a second one.
- **Draft in Gmail** logs the row if needed, then writes the card's probe or
  pointer reply into the row's Next Step Notes as an instruction. The
  scheduled pipeline creates the Gmail draft on its next run, and you send it
  as usual. Any notes already on the row are kept under the request. If the
  row already has a Gmail Draft ID, it stops and tells you instead of asking
  for a second draft. The bot never touches Gmail.
- **Park** sets the card aside. The daily summary shows it as parked.

After a click, the card updates in place to show what happened and who did
it, and buttons that no longer apply are removed. If something fails, only
you see the message (an ephemeral reply), and nothing changes.

Setup:

1. In the Slack app, open **Interactivity & Shortcuts**, turn it on, and set
   the Request URL to `https://<railway-domain>/slack/interactions`. Save.
2. In Railway, set `INTERACTIVE_ENABLED=true`. Park works right away.
3. For the Notion buttons: an internal Notion integration with **read, insert
   and update** content capabilities, shared with Partner Biz Dev. Set
   `NOTION_TOKEN` and `NOTION_WRITE=true`. Without `NOTION_WRITE`, only Park
   shows.

## Modes

- `REVIEW_MODE=shadow` (default) posts each card to #team-partnerships-development
  (`REVIEW_SHADOW_CHANNEL_ID`), with a link back to the submission. Nobody in
  the submissions channel sees it.
- `REVIEW_MODE=thread` replies under the digest itself.

## Manual review

Mention the bot with "review" inside a digest's thread, for example
`@Team Partner Agent review`, and it runs the full review on that thread's
HubSpot digest. Short polite forms work too, such as "can you review",
"please re-review this" or "could you run the review again?". The card is posted in the same thread and tags you. It
answers only users in `CHAT_USER_IDS`, in `REVIEW_CHANNEL_IDS` or
`CHAT_CHANNEL_IDS`. If the thread doesn't start with a HubSpot digest, it says
so. Other mentions still go to chat as before.

## Notion history (optional)

Set `NOTION_TOKEN` to an internal Notion integration with read access, and
share these with it (database → ⋯ → Connections):

- Partner Intake & Qualification (`REVIEW_NOTION_INTAKE_ID`)
- Partner Program Applications (`REVIEW_NOTION_APPS_ID`)

Each review then looks up earlier rows that match the email, the email domain
(skipped for free-mail addresses) or the company name, plus any application
made with the same email. Claude sees the matches, and the card shows a
*History* section with links. The bot only reads Notion, never writes to it.
If the lookup fails, the card says so and the review still runs.

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
- `/healthz` shows `review_enabled`, `review_mode`, `web_search`,
  `notion_lookup`, `summary_enabled`, `summary_hour`, `interactive` and
  `notion_write`.
- The instructions are prompt-cached, which lowers the cost of each review.
- Railway logs print `Review queued digest <ts>` for each digest picked up. A
  digest-looking post from an unexpected bot prints its `bot_id` so you can add
  it to `REVIEW_BOT_IDS`. No message content is logged.

## Not built yet

- HubSpot contact links on the card.
- Partners database lookup (needs its data source ID).
- A Send to Dean button. The card's Dean draft is still for you to send while
  the template settles.
