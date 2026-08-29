"""End-to-end: crash resume, the approval gate, and followup escalation, against live
Firestore + Vertex. Sends are captured, never really emailed; real send happens via the
/approve endpoint with a DEMO_RECIPIENT set."""
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

import clearbill.config as config
from clearbill import pipeline as pl
from clearbill import store

BILL_BODY = (Path(__file__).parent / "sample_docs" / "bill.txt").read_text()


def _run(code):
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=300)
    assert out.returncode == 0, f"subprocess failed:\n{out.stdout}\n{out.stderr}"
    return out.stdout.strip()


def _cid(stdout):
    for line in stdout.splitlines():
        if line.startswith("CID:"):
            return line[4:].strip()
    raise AssertionError(f"no CID marker in subprocess output:\n{stdout}")


def _new_source():
    marker = uuid.uuid4().hex[:8]
    return (f"billing-{marker}@riversidemed.example", "Your Riverside Medical Group bill",
            "2026-08-27", BILL_BODY)


def test_crash_resume_gate_and_followup():
    # 1. crash right after case creation: a fresh process must run the whole pipeline
    sender, subject, date, body = _new_source()
    case_id = _cid(_run(f"""
import os
from clearbill import store
cid, created = store.create_case({sender!r}, {subject!r}, {date!r}, {body!r})
print("CID:" + cid, flush=True)
os._exit(0)
"""))
    _run(f"""
from clearbill import pipeline
pipeline.advance({case_id!r})
""")
    case = store.load(case_id)
    assert case["state"] == config.PENDING_APPROVAL
    assert case["discrepancies"], "reconciliation flagged nothing on the planted duplicate"
    assert case["letter"]["body"]

    # 2. crash mid-run (killed right after extraction): restart re-reconciles, not drafts
    sender2, subject2, date2, body2 = _new_source()
    case_id2 = _cid(_run(f"""
import os
from clearbill import pipeline, store
cid, _ = store.create_case({sender2!r}, {subject2!r}, {date2!r}, {body2!r})
print("CID:" + cid, flush=True)
pipeline._reconcile = lambda *a, **k: os._exit(0)
pipeline.advance(cid)
"""))
    case2 = store.load(case_id2)
    assert case2["state"] == config.RECONCILED
    _run(f"""
from clearbill import pipeline
pipeline.advance({case_id2!r})
""")
    case2 = store.load(case_id2)
    assert case2["state"] == config.PENDING_APPROVAL
    assert case2["discrepancies"]

    # 3. gate: sending a pending-approval case is blocked
    with pytest.raises(PermissionError):
        pl._send(case_id2)

    # 4. flip the gate -> full send path runs (send captured, not emailed)
    sent = []
    from clearbill import gmail
    gmail.send = lambda to, s, b: sent.append((to, s, b))
    pl.approve_and_send(case_id2, "sahil")
    case2 = store.load(case_id2)
    assert case2["state"] == config.AWAITING_REPLY
    assert case2["approval"]["approved"] and case2["approval"]["approver"] == "sahil"
    assert sent and "riverside" in sent[0][2].lower()

    # 5. followup: past-due case with no reply produces an escalation draft behind the same gate
    store.db().collection("cases").document(case_id2).update(
        {"followup_due": store.now()})
    escalated = pl.run_followups()
    assert case_id2 in escalated
    case2 = store.load(case_id2)
    assert case2["state"] == config.PENDING_APPROVAL
    assert case2["is_escalation"]
    pl.approve_and_send(case_id2, "sahil")
    assert store.load(case_id2)["state"] == config.AWAITING_REPLY
