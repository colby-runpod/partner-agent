# Lead-thread clarification

Colby can mention the agent inside an already reviewed HubSpot submission thread to ask questions, such as “Is this a sales lead?”, “Why did you score it 2?”, or “What evidence supports that?”. Each follow-up needs a mention. Explicit routing, review and correction handlers retain priority.

Answers use the verified original HubSpot submission, saved assessment, and up to 20 prior Colby/agent thread messages. The conversation read is bounded to five pages and fails explicitly when incomplete. Other users' messages and future messages are excluded. Sources are linked separately using application-controlled URLs.

This release does not perform new external research, update qualification, write to Notion, send email or route leads from a Q&A response. It distinguishes assertions and missing evidence from verified findings. The original question and reply remain in Slack; the durable queue retains the question and answer. A later explicit routing request uses the existing routing workflow and Notion activity logging.

Delivery is recorded before posting and uncertain replies are held rather than resent automatically. Missing saved reviews, unavailable context and invalid model output produce an explicit failure reply instead of silence. No new credentials or Slack subscriptions are needed.

Deployment depends on the HubSpot source-links change in the parent branch. Do not call this capability live until the merged image is deployed. Live acceptance: ask the original Aidios Frontier question, then a follow-up referencing that answer; verify one in-thread response per mention, working source links and no lead mutations.
