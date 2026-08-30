import hashlib
import json
from datetime import datetime, timedelta, timezone

from google.api_core.exceptions import AlreadyExists
from google.cloud import firestore

import clearbill.config as config

_db = None


def db():
    global _db
    if _db is None:
        _db = firestore.Client(project=config.PROJECT)
    return _db


def now():
    return datetime.now(timezone.utc)


def _log(case_id, agent, from_state, to_state, note=""):
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "case_id": case_id,
        "agent": agent,
        "from_state": from_state,
        "to_state": to_state,
    }
    if note:
        entry["note"] = note
    print(json.dumps(entry), flush=True)


def create_case(sender, subject, received_date, body):
    """Returns (case_id, created). Duplicate source messages reuse the existing case."""
    case_id = hashlib.sha256(f"{sender}|{subject}|{received_date}|{body}".encode()).hexdigest()[:16]
    doc = {
        "state": config.INTAKE,
        "source": {"sender": sender, "subject": subject, "received_date": received_date, "body": body},
        "docs": {},
        "discrepancies": [],
        "pricing_confidence": None,
        "letter": None,
        "is_escalation": False,
        "approval": {"approved": False, "approver": None, "approved_at": None},
        "sent_at": None,
        "followup_due": None,
        "cost": [],
        "note": "",
        "created_at": now(),
        "updated_at": now(),
    }
    try:
        db().collection("cases").document(case_id).create(doc)
        return case_id, True
    except AlreadyExists:
        return case_id, False


def load(case_id):
    snap = db().collection("cases").document(case_id).get()
    if not snap.exists:
        return None
    case = snap.to_dict()
    case["id"] = case_id
    return case


def transition(case_id, agent, to_state, patch=None):
    # ponytail: read-then-update without a transaction; single-operator deployment
    ref = db().collection("cases").document(case_id)
    from_state = ref.get().to_dict()["state"]
    data = {"state": to_state, "updated_at": now()}
    if patch:
        data.update(patch)
    ref.update(data)
    _log(case_id, agent, from_state, to_state, patch.get("note", "") if patch else "")


def record_usage(case_id, agent, model, usage):
    db().collection("cases").document(case_id).update(
        {
            "cost": firestore.ArrayUnion(
                [
                    {
                        "agent": agent,
                        "model": model,
                        "prompt_tokens": usage["prompt"],
                        "completion_tokens": usage["completion"],
                        "total_tokens": usage["prompt"] + usage["completion"],
                        "ts": now(),
                    }
                ]
            )
        }
    )


def dead_letter(case_id, agent, error):
    transition(case_id, agent, config.DEAD_LETTER, {"note": error[:2000]})


def approve(case_id, approver):
    db().collection("cases").document(case_id).update(
        {
            "approval.approved": True,
            "approval.approver": approver,
            "approval.approved_at": now(),
            "state": config.APPROVED,
            "updated_at": now(),
        }
    )
    _log(case_id, "human", config.PENDING_APPROVAL, config.APPROVED, f"approver={approver}")


def open_cases():
    states = [config.INTAKE, config.AWAITING_DOCS, config.RECONCILED,
              config.PENDING_APPROVAL, config.APPROVED, config.AWAITING_REPLY]
    return [_with_id(d) for d in db().collection("cases").where("state", "in", states).stream()]


def due_followups():
    cases = [_with_id(d) for d in db().collection("cases").where("state", "==", config.AWAITING_REPLY).stream()]
    # ponytail: filter followup_due in Python to dodge a composite index
    return [c for c in cases if c.get("followup_due") and c["followup_due"] <= now()]


def find_awaiting_eob_case(provider_name, patient_name):
    for case in open_cases():
        bill = case.get("docs", {}).get("bill")
        if case["state"] == config.AWAITING_DOCS and bill:
            # case-insensitive: EOBs and bills rarely agree on capitalization
            if (bill["provider_name"].lower() == provider_name.lower()
                    and bill["patient_name"].lower() == patient_name.lower()):
                return case
    return None


def match_reply(sender):
    # ponytail: exact-sender match; upgrade when one case can have multiple billing senders
    for case in open_cases():
        if case["state"] == config.AWAITING_REPLY and case["source"]["sender"] == sender:
            return case
    return None


def _with_id(snapshot):
    case = snapshot.to_dict()
    case["id"] = snapshot.id
    return case
