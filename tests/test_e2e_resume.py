"""End-to-end: crash resume, EOB pairing, the approval gate, and followup escalation,
against live Firestore + Vertex. Sends are captured, never really emailed. AUTO_SEND
is off here so the manual gate stays the subject under test."""
import os

os.environ["AUTO_SEND"] = "0"

import subprocess
import sys
import uuid
from pathlib import Path

import pytest

import clearbill.config as config
from clearbill import pipeline as pl
from clearbill import store

BILL_BODY = (Path(__file__).parent / "sample_docs" / "bill.txt").read_text()
EOB_BODY = (Path(__file__).parent / "sample_docs" / "eob.txt").read_text()


def _run(code):
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=420)
    assert out.returncode == 0, f"subprocess failed:\n{out.stdout}\n{out.stderr}"
    return out.stdout.strip()


def _cid(stdout):
    for line in stdout.splitlines():
        if line.startswith("CID:"):
            return line[4:].strip()
    raise AssertionError(f"no CID marker in subprocess output:\n{stdout}")


def _new_source(provider):
    marker = uuid.uuid4().hex[:6]
    bill = BILL_BODY.replace("RIVERSIDE MEDICAL GROUP", provider).replace("Riverside Medical Group", provider)
    eob = EOB_BODY.replace("Riverside Medical Group", provider)
    return (f"billing-{marker}@provider.example", "Your bill", "2026-08-27", bill), \
           (f"claims-{marker}@insurer.example", "EOB", "2026-08-29", eob)


def test_crash_resume_gate_and_followup():
    # 1. bill email arrives, process dies right after create; a fresh process resumes it.
    #    A lone bill now waits for its EOB before any dispute can draft.
    (bsender, bsubject, bdate, bbody), (esender, esubject, edate, ebody) = _new_source(
        f"Riverside Medical Group {uuid.uuid4().hex[:6]}")
    case_id = _cid(_run(f"""
import os
from clearbill import store
cid, created = store.create_case({bsender!r}, {bsubject!r}, {bdate!r}, {bbody!r})
print("CID:" + cid, flush=True)
os._exit(0)
"""))
    _run(f"""
from clearbill import pipeline
pipeline.advance({case_id!r})
""")
    case = store.load(case_id)
    assert case["state"] == config.AWAITING_DOCS
    assert case["docs"].get("bill")

    # 2. the matching EOB arrives -> merges -> reconciles -> drafts -> pending_approval
    _run(f"""
from clearbill import pipeline
pipeline.process_source({esender!r}, {esubject!r}, {edate!r}, {ebody!r})
""")
    case = store.load(case_id)
    assert case["state"] == config.PENDING_APPROVAL
    assert case["docs"].get("eob")
    assert case["discrepancies"], "reconciliation flagged nothing on the planted duplicate"
    assert case["letter"]["body"]

    # 3. gate: sending a pending-approval case is blocked
    with pytest.raises(PermissionError):
        pl._send(case_id)

    # 4. flip the gate -> send path runs (send captured, not emailed)
    sent = []
    from clearbill import gmail
    gmail.send = lambda to, s, h, t: sent.append((to, s, h))
    pl.approve_and_send(case_id, "sahil")
    case = store.load(case_id)
    assert case["state"] == config.AWAITING_REPLY
    assert case["approval"]["approved"] and case["approval"]["approver"] == "sahil"
    assert sent and "riverside" in sent[0][2].lower()

    # 5. followup: past-due case with no reply produces an escalation draft behind the same gate
    store.db().collection("cases").document(case_id).update({"followup_due": store.now()})
    escalated = pl.run_followups()
    assert case_id in escalated
    case = store.load(case_id)
    assert case["state"] == config.PENDING_APPROVAL
    assert case["is_escalation"]
    pl.approve_and_send(case_id, "sahil")
    assert store.load(case_id)["state"] == config.AWAITING_REPLY
