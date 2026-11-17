# Intake stabilization release

Status: built and tested; not deployed. Existing automatic routing, owner rules, human holds and receipt-based delivery fences remain unchanged.

## Reliability

- `/healthz` reports listener availability. `/readyz` separately checks durable queue/outbox progress, repeated failures, stalled jobs and overdue ready work.
- Classify ready, blocked, scheduled and excluded work separately. Classification never changes business status or authorizes replay.
- Persist the last validated intake guide configuration across restarts. Unknown policy changes still block; unrelated Working Task governance does not invalidate the intake policy.
- Independent one-minute Lambda probe and CloudWatch alarm: three unhealthy/missing checks notify a confirmed SNS recipient; recovery sends one state-change notification. The probe cannot read secrets, post Slack messages, alter leads or replay work.
- Before monitor deployment: package operations_probe.py in a ZIP, upload to a deployment bucket, supply that immutable CodeKey and a confirmed SNS AlarmTopicArn to infra/intake-monitor.yaml. The monitor is not operational until a deliberate failure and recovery both reach the recipient.

## Template contract 2026-11-17.1

For newly queued specialist handoffs, use this order in Body:

1. Company and primary route heading.
2. Company, contact, email, region.
3. Lead score and assessment immediately after region. Distinguish advisory priority from canonical qualification; never convert /33 into /5 by rounding. Where available, include A/S/I/C/V total /33 and tier with assessment owner. Unknown means unknown.
4. Partnership requested, ask from Runpod, what the applicant brings.
5. Concise assessment: verified facts, applicant claims and unknowns distinguished. Include event date and decision deadline when known.
6. One concrete suggested review action for the receiving owner. Missing information is a question for specialist validation, not a reason to prevent the handoff.
7. Original source, HubSpot contact when present in the source, and linked intake record. Use actual URLs, not guessed record links. Slack syntax is <https://host/path|Label>.
8. Template version and advisory/final qualification label. Owner mentions belong to Mention IDs from the guide; preserve agent-added Colby cc.

AWS source reviews and handoffs disclose that their legacy 1-5 value is a compatibility bucket, not a second qualification score. Canonical pipeline qualification remains A/S/I/C/V /33 plus tier. No scoring thresholds change in this release. Historical receipts and posted bodies are not regenerated automatically. Weekly and signal summary bodies retain their separate verbatim contracts.

## Claude scheduled task alignment instructions

Read the current Partner Intake Operating Model and latest Guide change ledger before each run. Continue the existing source coverage, ownership and qualification rules. For newly queued specialist handoffs, use Template contract 2026-11-17.1 above. Supply the actual requested transaction, Runpod ask and offered value explicitly; do not substitute organization type for the route. Keep canonical A/S/I/C/V score /33, tier and evidence. Identify pipeline qualification separately from an agent advisory assessment. Never infer a new score from the legacy 1-5 bucket.

Keep specialist handoffs automatic regardless of low score or missing verification, except the guide's spam, empty-payload and documented human/legal holds. The Team Partner Agent alone posts Slack handoffs. Before creating another outbox row, check the source/intake plus destination for an existing queued row or delivery receipt. Never requeue an uncertain send. Queued is not Posted, Posted is not Picked up, and Actioned is not Closed.

Reconcile existing held rows using their linked intake status and Forward To. The pipeline owns cancellation and corrected route decisions; do not bypass an agent hold by creating a new row. Report ready, held, scheduled, excluded and failed rows separately with the exact next action and owner. Do not replay historical invitation or weekly-update rows merely to reduce the backlog count. Record actual changes in the Activity Log with the guide version used. Mark Pipeline Updated in the Guide change ledger only after these instructions are saved and a real run is verified.

## Release acceptance still required

- Deploy reviewed code and verify old workers stopped before ownership resumes.
- Confirm independent alert recipient and prove failure/recovery notifications.
- Reconcile legacy holds with pipeline owner; no blind replay or cancellation.
- Verify new real lead review and handoff, exactly one Slack receipt, intake update, Activity Log and guide version.
- Confirm Claude task adoption; do not claim both producers aligned until verified.
- Update authoritative agent record/overview after deployment, separately from pending release evidence.
