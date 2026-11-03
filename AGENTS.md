# Partner agent change and release documentation

For every partner-agent behavior, configuration, permission, channel, schedule, or deployment change, review and reconcile:
- Agent entry: https://app.notion.com/p/3ffff732fc34810da359de80219c4856
- Apps/Agents overview: https://app.notion.com/p/3ffff732fc34807294c5c0319319a80f

Before closing implementation work, record built/tested changes as pending with branch/PR evidence. After deployment and live verification, update the entry's Current Capabilities, How to Use, Known Limitations, Latest Progress, Next Step, Deployment Branch and Last Reviewed as applicable. Update the overview only for material capability/status changes. Preserve unrelated records and historical evidence; clearly identify the current authoritative status.

A merge is not proof of deployment. Record deployed commit/task revision, checks and unresolved gaps. Never mark a capability live from tests alone. For other partner agents, update their own verified entry and the overview; do not claim unaudited agents were reviewed.

If Notion access is unavailable, report the exact documentation gap and leave it as outstanding release work. Do not claim documentation is current. This is an event-driven release requirement, not a separately scheduled audit.
