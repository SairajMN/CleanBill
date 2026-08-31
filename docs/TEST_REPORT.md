# ClearBill — Test Report

All runs against live **Vertex AI** (Gemini 3.5 Flash / 3.7 Flash), live **Cloud Firestore**,
auto-dispute emails delivered to `resell.it.here@gmail.com` (the bill's declared
"Billing questions" address). Run date: 2026-08-31.

## Environment A — real Gmail inbox, local poller (full end-to-end)

5 synthetic bill+EOB email pairs sent to the watched inbox; the poller picked them up
without any manual step. Each pair plants a specific billing error.

| # | Provider / Patient | Planted error | Final state | Flagged | Disputed | Dispute sent |
|---|---|---|---|---|---|---|
| 1 | Aurora General Hospital / Priya Sharma | duplicate MRI charge ($850 ×2) | `awaiting_reply` | duplicate_charge + 3 pricing mismatches | $1,750.00 | ✅ 11:36 AM |
| 2 | Summit Orthopedic Clinic / Rahul Verma | bill demands more than EOB patient-owes | `awaiting_reply` | 2 pricing_mismatch | $2,016.00 | ✅ 11:36 AM |
| 3 | Cedar Valley Diagnostics / Meera Iyer | Vitamin D assay on bill, absent from EOB | `awaiting_reply` | undocumented_charge + 2 pricing mismatches | $462.00 | ✅ 11:34 AM |
| 4 | Lakeside Family Practice / Arjun Patel | duplicate ECG ($95 ×2) | `awaiting_reply` | duplicate_charge + 2 pricing mismatches | $315.00 | ✅ 11:34 AM |
| 5 | Redwood Pediatrics / Ananya Rao | bill total ≠ EOB patient-owes | `awaiting_reply` | 2 pricing_mismatch | $161.60 | ✅ 11:34 AM |

Note on pair 5: intended as a "clean" pair, but the fixture's bill demanded the full
billed amount while the EOB said the patient owes far less — the agent correctly flagged
this as a pricing mismatch. Correct behavior; fixture flaw.

EOB-first ordering was also exercised live: one EOB was processed while its bill was
still extracting; the reverse-merge path parked the EOB, attached it when the bill
arrived, and the case completed normally.

## Environment B — Cloud Run (https://clearbill-512401546414.us-central1.run.app)

Same 5 pairs submitted through `POST /api/analyze`. The pipeline (extraction,
reconciliation, drafting on Vertex) ran on Cloud Run; the human-approval gate armed
there, and the send executed under the operator's OAuth (Cloud Run's service account
has no personal mailbox by design — see README "Deploying").

| # | Case ID | Final state | Disputed | Dispute sent |
|---|---|---|---|---|
| 1 | `7946e8e8681e49f8` | `awaiting_reply` | $1,750.00 | ✅ 11:47 AM |
| 2 | `a32e0e2c23df7ec7` | `awaiting_reply` | $2,016.00 | ✅ 11:48 AM |
| 3 | `9158af2493e198e9` | `awaiting_reply` | $462.00 | ✅ 11:48 AM |
| 4 | `94d515feee57f522` | `awaiting_reply` | $315.00 | ✅ 11:48 AM |
| 5 | `5daaf2f0f45ddbf8` | `awaiting_reply` | $161.60 | ✅ 11:48 AM |

Cloud and local runs produced the same flags and amounts for identical inputs.

## Resilience checks

- **Crash resume**: process killed after case creation and mid-reconciliation; fresh
  processes resumed from persisted Firestore state to completion (`test_e2e_resume.py`).
- **Rate-limit recovery**: a Vertex 429 mid-batch left cases parked; the poller's
  per-cycle `resume()` re-drove them automatically once quota recovered.
- **Idempotency**: duplicate delivery of the same email reuses the case
  (`test_idempotency.py`); a double-sent bill produced one case, not two.
- **Human gate**: sending is blocked unless the approval flip is recorded in Firestore,
  human or auto-agent alike (`test_gate.py`, `test_auto_send.py`).
- **EOB gating**: a bill alone never drafts or sends — it waits for its matching EOB
  (patient + provider match) before any dispute is written.

## Cost accounting

Every LLM call logs prompt/completion/total tokens per agent into the case's `cost`
array in Firestore (see `store.record_usage`), giving a real per-agent cost breakdown —
extraction and reconciliation run ~$0.001/case on Flash; drafting is the only
higher-tier call.
