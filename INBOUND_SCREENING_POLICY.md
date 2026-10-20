# Inbound screening policy - 2026-10-20

Apply this policy to the scheduled partner intake pipeline. Repository deployment does not update a Claude scheduled task or grant its connectors access. Keep the existing HubSpot digest review cards and Log in Notion / Park buttons. `SCREENING_V2_ENABLED=true` separately opts those advisory cards into the new scoring; it is off by default.

## Scoring and evidence

Record A (ask specificity), S (company substance), I (identity), C (commercial signal), V (strategic value), each integer 0-3, plus `3A + 2S + I + 3C + 2V` out of 33 in Quality Notes. Keep the old Qualification Score select as a compatibility bucket, not the total. Evidence must distinguish claims, verified findings and unknowns. Empty web search for a real small software business is unknown (S=1), not proof of fraud.

Risk first: a documented policy/export concern, clear misrepresentation or spam is DQ, except S=3 is held for Colby, following section 6 of the supplied brief. Country or nationality alone does not establish a sanctions violation. Cite the specific concern and route uncertain findings for review. Wrong-door vendor, compute purchase, credits/startup, creator/affiliate and subscale hosting requests are Route, not partnership DQ. Predevelopment supply without secured site/power/hardware remains DQ with a re-entry invitation.

Act now: total >=22, C=3, V=3/S>=2/A>=1, or a Runpod teammate referral with C>=2. Only Act now gets an individual priority outbox alert.
Qualify: total 15-21 or any floor:
- F1: A>=2, confirmed own-company email domain, and V>=2 or C>=2.
- F2: S>=2, confirmed own-company email domain, thin or blank ask (A<=1).
- F3: a Runpod teammate forwarded or cc'd the lead.
DQ: <=14 without floors, after required research. Identity score alone is not evidence of a matching company email domain.

## Research before an unverifiability DQ

Read the original HubSpot contact/form if the digest is blank or short. Log one line each for registry, LinkedIn/company/person, directories/partner listings, local-language press, and Gmail sender-domain history. Each entry must name the actual check and result. Unavailable access is not a negative result: hold as Needs research. Do not claim that the Railway service inherits Claude's connectors. The current repository reviewer has web search and Notion history, not HubSpot/Gmail research connectors; it must leave those unavailable unless supplied in trusted context.

Use at most six searches for a legacy score-3 candidate, or a score-2 candidate with a corporate domain and specific ask; at most two otherwise. Avoid repeated searches during model continuation. Preserve actual prior submitted dates when citing history.

## Draft and duplicate handling

Qualify: draft 1-3 targeted questions or an invitation for Colby's daily batch approval, with no individual priority alert. DQ: soft-decline draft no sooner than seven days after submission, ending with a re-entry request for volume, named customers, launch date, or secured site/power/hardware. Never send automatically. Hold for Colby and Needs research get no invitation or decline draft.

Match duplicates by normalized company, domain and explicit verified aliases, including known alternate addresses. Do not merge unrelated companies merely because domains resemble each other. A reply with new facts is a new row; identical resubmission three or more times gets no new draft and a risk note. Do not treat this policy document as evidence that these checks have been run.

## Posting contract

The scheduled pipeline is the intake tracker writer. It writes Slack mrkdwn verbatim to Partner Agent Outbox (source `b9f3d371-6c3b-47f5-880a-47342ae69456`). No DMs and no user-identity Slack posts after cutover. Handoffs go to the supply/events feed channels; priority alerts, daily digest and invitation queue go to #ask-partners. Use `Not Before` at 08:30 America/Denver for the weekday digest. Read Confirmed/Declined pickup back from the outbox on the next run; the bot does not write handoff status on intake.

Invitation approvals are the narrow exception: the bot writes only Invitation Decision, Invitation Approved By and Invitation Approved Date on Colby's button click. Call first changes only the outbox. The pipeline drafts, Colby sends.

## Historical review queue

Review the supplied report's Eranyacloud, Concordium, New Horizon Code, QuantylCloud and ownAI recommendations against current records before reopening. Kovara is already reopened per the report; verify its actual earlier date (2026-10-01). JCG requires account evidence. Route Fivenines to Startup Program perks and Biz-Spoke to Sales. Do not bulk overwrite historical decisions from this report. These follow-ups have not been executed by deploying this policy.
