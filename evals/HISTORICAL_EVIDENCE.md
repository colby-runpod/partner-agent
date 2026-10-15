# p-OPS historical triage evidence

Reconciled on October 15, 2026. This file records the calibration status of the 18 historical scenarios in `p_ops_triage_cases.jsonl`. It is an evaluation inventory, not permission to change a task or send a message.

## Result

- 15 verified against accessible Notion, Slack, Gmail, or transcript-backed records.
- 1 partially verified: the Partner FDE/SE scope exists, but the bounded search did not locate a canonical Working Task for the original request.
- 2 blocked: the GCP and AWS Marketplace setup channels exist in the `runpod-team` Slack workspace, but the connected Slack app cannot read their message history.
- 2 material corrections were made to the earlier scenario summaries:
  - ArcFabric is a financial credit-eligibility and issuance decision, not a scheduled partnership-review call.
  - ArborCloud is a technical design-partner validation, not a credit request.

## Case inventory

| Case | Status | Primary evidence |
|---|---|---|
| ArcFabric credit eligibility | Verified | [Working Task](https://app.notion.com/p/3c3ff732fc3481ba8ec9f2971882432b), Gmail thread `19f8094d4cef165b` |
| ArborCloud technical validation | Verified | [Working Task](https://app.notion.com/p/3c6ff732fc3481c59f67cbc59eda5ee7), [meeting](https://app.notion.com/p/3c6ff732fc34813eb076ca0b7387fa1a) |
| AWS badge paths | Verified | [Working Task](https://app.notion.com/p/d792b4cef6804e6c992155665add52f7), [Justin 1:1](https://app.notion.com/p/3c2ff732fc348171b7dddf4adc87bc80) |
| Cloud GTM operating model | Verified | [Working Task](https://app.notion.com/p/aa0e17c17733476b9a7ea0003de86463) |
| Suger Marketing Ops support | Verified | [Working Task](https://app.notion.com/p/3ceff732fc3481099b65ca83b0c225f9), [Slack message](https://runpod.enterprise.slack.com/archives/C0BU75X2YEA/p1788295945340899) |
| SI operating model | Verified | [Working Task](https://app.notion.com/p/3aeff732fc3480deaaa1e7867b156b91), [executive brief](https://app.notion.com/p/3cfff732fc34810286d6d02658bbc9f5) |
| SoftServe introduction | Verified | [Working Task](https://app.notion.com/p/f4ba99ddef5d42d99d9ec92638415199) |
| Redis activity and POC | Verified | [POC task](https://app.notion.com/p/3ceff732fc3481cebb68f6f69520f1b0), [partner record](https://app.notion.com/p/d2f04608977b47f5a31aa485d0dcce40) |
| HubSpot attribution phase one | Verified | [Working Task](https://app.notion.com/p/3dcff732fc3481eea728f0f681f0cfcd) |
| PartnerStack checkpoint | Verified | [PRM task](https://app.notion.com/p/3dcff732fc3481f196c1e0dc461abb99), [meeting](https://app.notion.com/p/3f3ff732fc34818ba2c5f531492c9312) |
| Q4 waypoints | Verified | [Working Task](https://app.notion.com/p/3ceff732fc348122b51aec78414856ca) |
| Partner FDE/SE case | Partial | [Scope](https://app.notion.com/p/3f0ff732fc34806997a7c9d050f86080) |
| GCP Marketplace response | Blocked | [Objective](https://app.notion.com/p/397ff732fc34814183c7c13c5e0d95e0), channel `C0B9HPA76KE` is inaccessible to the connected app |
| AWS Marketplace response | Blocked | [Objective](https://app.notion.com/p/397ff732fc348119bed2d82174d6b6ca), channel `C0B9HKZ0PDJ` is inaccessible to the connected app |
| Zeb and Brennen review | Verified | [Working Task](https://app.notion.com/p/3ceff732fc3481b981e1c0250ba405e4), [meeting](https://app.notion.com/p/3ceff732fc3481cd9f4bea9390887f47) |
| Tanya feedback | Verified | [Working Task](https://app.notion.com/p/3d0ff732fc348161bd40dc75a710cde7), [interview](https://app.notion.com/p/3b8ff732fc348122bd9ee8e24ee9f237), Gmail notification `1a064c3ba93b3b9f` |
| Normalize partner tier | Verified | [Working Task](https://app.notion.com/p/3c2ff732fc3481a48954d9dbcad878fb) |
| Connect revenue systems | Verified | [Working Task](https://app.notion.com/p/3c2ff732fc34819281b3f6d806688879) |

## Release-gate rule

Do not use historical aggregate accuracy as a release gate until the partial case has a canonical-task disposition, both Slack channel histories are readable or intentionally excluded, and the live-model run passes without policy violations. Regardless of score, the pilot remains non-executable.
