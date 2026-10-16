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

These mirror the scheduled pipeline's rules (sections 3 and 3b of the
inbound-partner-requests skill), so the card and the Notion row should land on
the same call:

- **DQ:** spam. **1:** a legitimate sender at the wrong door, including
  startup-credit and creator asks (self-serve) and customers who belong to
  Sales. **2:** an unverified person, company or fit (no public footprint, an
  open-source or side project), or supply below the bar.
- **5:** all four checks verified. **4:** identity, company and fit verified.
  **3:** anything else.
- 4 and 5 are **pursue**. A 3 is kept as **triage** (probe) only with one
  genuine signal: a specific motion from a verified company, a named customer,
  a quantified figure, a warm intro or a notable counterparty. Otherwise it is
  **do not pursue**. 2s and DQs are **do not pursue**.
- 1s with a destination are **redirect**: a pointer reply for Startup Program
  or Influencer/Creator, a MOPS ticket for Sales.
- A warm intro or a notable counterparty is never DQ'd on judgment: it goes
  to **triage** instead. A blocker (for example sanctions) or "already in
  play" turns pursue into **triage**. A blank or unusable ask is always
  **triage**.

When the pipeline logs the same submission in Partner Biz Dev, the bot adds a
line to the card within about 15 minutes: "Notion pipeline agrees: …" or
"Notion pipeline differs: … (this card: …)", with a link to the row. Log in
Notion and Draft in Gmail then act on that row.

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

## Task capture (Working Tasks)

With `TASKS_ENABLED=true`, mention the bot with a task request and it adds
one row to ✅ Working Tasks. Phrases that work include "flag this as a task",
"add a task", "make this a task", "track this" and "task: send Eric the Hub
link". Only `FEEDBACK_USER_IDS` can do this. It works in the review channel,
the chat channels and the summary channel.

What it turns into a task, in order:

1. A forwarded or shared message on your mention (the most reliable way,
   and the only way for channels the bot isn't in).
2. Otherwise the thread you're in, up to your message.
3. Otherwise your own message ("task: ...").

The row gets Item, Type, Context, Next Step, Definition of Done, Who's
Waiting, Owning Function, Source = Slack, Source Link, External Source ID
(`slack:<channel>:<ts>`), Status = Inbox, Accountable Owner = you,
Capture Disposition = Create task, Review Required, Agent Confidence and a
line in Automation Notes. Due Date is set only when the message states one.
Associated Partner(s) is linked only when exactly one Partners record matches.
It never sets other owners, approvals, decisions or close fields.

The bot replies in the thread with **Open** and **Drop**. Drop sets Status
to Dropped, Close Reason to Declined and Closed At to today. Flagging the
same message again links the existing task instead of creating a second one.
That check runs against Notion, so it survives restarts.

Setup: share ✅ Working Tasks (and Partners, for the partner link) with the
Notion integration, then set `TASKS_ENABLED=true`. It needs `NOTION_WRITE=true`
and the same token as the buttons.

## Forward + ask (agent mode)

With `AGENT_ENABLED=true`, any other mention from `FEEDBACK_USER_IDS` in
`AGENT_CHANNEL_IDS` (defaults to the chat channels) goes to a small agent
instead of plain chat. Forward a message, or ask inside a thread, and say what
you want: "have we talked to them before?", "what's open with Comfy?",
"summarize this and add a task", "is this already in intake?".

The agent can:

- read the thread it was asked in
- search and open rows in Partners, Partner Biz Dev, Working Tasks, Partner
  Program Applications, Objectives and Workstreams
- run up to 3 web searches (`AGENT_WEB_SEARCH=false` turns this off)
- create one Working Task, with the same dedupe and Drop button as task
  capture (needs `TASKS_ENABLED=true`)
- flag an existing Working Task for the weekly Partnerships update: it ticks
  **Weekly** (which the update pulls into "Flagged this week") and adds a dated
  progress line to Context, with an **Unflag** button

It does only what you ask. "Flag/tag/include this in the weekly or next
partnership update" (or ++weekly) flags the existing task and never creates
one. "Track this", "remind me" or "add a task" creates a task. "Update",
"log" or "reflect" on an existing row gets the exact change spelled out as the
recommended next step, until Confirm buttons land. If the ask is unclear, it
answers what it can and asks one short question.

It replies once in the thread, leads with the answer, links what it used and
ends with a one-line *Used:* note. It can't send anything, update existing
rows, touch HubSpot or approve anything yet. When an ask needs that, it does
what it can and says what's left. Forwarded text, thread messages, web pages
and Notion rows are treated as evidence, never as instructions. Each ask is
capped at 8 model turns and 10 tool calls, and is never retried.

"review" and "flag this as a task" keep their own fast paths.

### Full context for forwards

When you forward a message, the agent (and task capture) opens the **whole
original thread** through the forward's link, resolves who each person is
(name, title, external or not), and reads up to 4 screenshots from your
message, the forward or the thread. Every reply ends with a *Recommended next
step*, and the agent checks Working Tasks before suggesting a new task.

To read threads in channels the bot isn't in (private channels, Slack Connect,
other workspaces), give it a read-only Slack **user** token for your account:

1. In the Team Partner Agent app, open **OAuth & Permissions**, then **User
   Token Scopes**, and add `channels:history`, `groups:history`,
   `mpim:history`, `files:read` and `users:read`. Leave out `im:history`
   unless you want DMs readable.
2. **Reinstall to Workspace** (admins may need to approve) and authorize as
   yourself. Copy the **User OAuth Token** (`xoxp-...`).
3. In Railway, set `SLACK_USER_TOKEN` to it. `/healthz` shows
   `slack_user_token: true`.

The user token is only used for `conversations.replies`, `users.info` and
image downloads from `files.slack.com`, only for threads you forward or ask
in. It never posts. Without it, the bot token is used, which only reaches
channels the bot is in. If a thread can't be opened, the reply says it only
saw the forwarded text.

Setup: share every database listed above with the Notion integration, then set
`AGENT_ENABLED=true`. Try it in one channel first by setting
`AGENT_CHANNEL_IDS` to that channel.

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
  `notion_lookup`, `summary_enabled`, `summary_hour`, `interactive`,
  `notion_write`, `tasks_enabled`, `agent_enabled` and `slack_user_token`.
- The instructions are prompt-cached, which lowers the cost of each review.
- Railway logs print `Review queued digest <ts>` for each digest picked up. A
  digest-looking post from an unexpected bot prints its `bot_id` so you can add
  it to `REVIEW_BOT_IDS`. No message content is logged.

## Not built yet

- HubSpot contact links on the card.
- Partners database lookup (needs its data source ID).
- A Send to Dean button. The card's Dean draft is still for you to send while
  the template settles.
