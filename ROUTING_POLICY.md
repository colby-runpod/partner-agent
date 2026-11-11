# Request-based routing - Guide 2026-11-11.2

Approved by Colby November 11. Authority: https://app.notion.com/p/40fff732fc34810eac53f085a6d47576 section 5.1.

Implementation: routing_policy.py supplies the review prompt and validated intent-to-route map. Routes depend on the requested transaction, not applicant organization. The model identifies intent from source evidence; deterministic code maps it to existing destinations. This is not proof that model classifications are always correct.

Structured review metadata: intent, subtype, commercial_role, rationale, secondary_consultation, event_date, decision_deadline, policy_version. Metadata is retained in the review ledger, displayed on review cards/handoffs and logged in a linked Intake Activity Log Note. Pipeline qualification and existing Forward To overrides remain authoritative. No new Notion select options or schema migration.

Low-score specialist leads still queue automatically. Unverified identity, pre-development supply, personal email, missing event details and ordinary commercial solicitation do not themselves make a lead spam. Existing synthetic-test exclusions, duplicate receipts, human overrides and documented legal holds remain. No old leads are replayed by this release.

VC company identity no longer overrides a Partnerships route: a VC rebilling compute stays a reseller, while portfolio-program requests go to Zoe. Startup credits go to Cameron. Hackathon sponsorship goes to Events, academic research/teaching to Zoe, purchases to Sales. Standalone co-marketing/media goes to Marketing, creator sponsorship/affiliate to Affiliate. Product owner remains TBD. Secondary consultation never creates a second handoff.

## Pipeline adoption

The scheduled inbound-partner-requests task must read Guide 2026-11-11.2 section 5.1 and its Guide change ledger record before classification. Record request subtype, commercial role, routing rationale, secondary consultation and known event date/deadline in the existing Partnership Breakdown / Quality Notes; preserve canonical qualification. Queue specialist handoffs irrespective of score, using existing owners and one primary route. Apply 7.4 only to wrong-door vendors without an applicable specialist owner. Preserve source/HubSpot links, receipts and existing holds. No automatic applicant emails. Do not reroute historical records solely because the guide changed. Tick Pipeline Updated only after the task/skill is verified to match.

## Verification and release

Tests cover transaction/organization conflicts, low-score handoffs across every specialist intent, secondary context without a second post, saved metadata, source-safe rendering, invalid dates and legacy-card fallback. Existing queue/receipt tests remain required. Tests do not prove live deployment or pipeline adoption.

Guide .1's Working Task Capture scenario was read and is recognized as a separate automation, not new authority for this intake process. The agent accepts guide .2 after release; future unreviewed policy changes still require review.
