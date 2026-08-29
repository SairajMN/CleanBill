import json
import time
from datetime import timedelta

from google.genai import errors as genai_errors
from pydantic import ValidationError

import clearbill.config as config
import clearbill.store as store
from clearbill.agents import (
    AgentError,
    dispute_draft_agent,
    extractor_agent,
    followup_agent,
    intake_agent,
    reconciliation_agent,
    run_agent,
)


def process_source(sender, subject, received_date, body, attachments=()):
    case_id, created = store.create_case(sender, subject, received_date, body)
    if created:
        advance(case_id, attachments)
    return case_id, created


def analyze_upload(bill_body="", bill_attachments=(), eob_body="", eob_attachments=()):
    """Web-upload path: skips intake (the user's intent is explicit), extracts whatever
    docs were submitted into their slots, then reconciles against both and drafts."""
    combined = " ".join(x for x in (bill_body, eob_body) if x).strip() or "(uploaded files only)"
    case_id, created = store.create_case("web-user", "web upload", str(store.now()), combined)
    if not created:
        return case_id
    if bill_body.strip() or bill_attachments:
        _extract_doc(case_id, "bill", bill_body, bill_attachments)
    if eob_body.strip() or eob_attachments:
        _extract_doc(case_id, "eob", eob_body, eob_attachments)
    _reconcile(case_id, store.load(case_id))
    return case_id


def _extract_doc(case_id, kind, body, attachments):
    prompt = f"Extract the billing data from the uploaded {kind.upper()} document(s).\n\n{body or '(uploaded files only)'}"
    extracted, usage = _run_validated(extractor_agent, case_id, prompt, attachments)
    store.record_usage(case_id, "extractor_agent", config.MODEL_FLASH, usage)
    store.transition(case_id, "extractor_agent", config.RECONCILED, {f"docs.{kind}": extracted.model_dump()})


def advance(case_id, attachments=()):
    """Runs the next stage for a case based on its persisted state. Safe to re-run after a crash."""
    case = store.load(case_id)
    if case is None:
        return
    state = case["state"]
    if state == config.INTAKE:
        _intake(case_id, case, attachments)
    elif state == config.RECONCILED:
        # crash during reconciliation lands here with no discrepancies persisted
        if case["discrepancies"]:
            _draft(case_id, case)
        else:
            _reconcile(case_id, case)
    elif state == config.APPROVED:
        _send(case_id)
    # awaiting_docs: resumes when the counterpart document arrives
    # pending_approval / awaiting_reply: advance only on a human flip or the followup cron


def resume():
    for case in store.open_cases():
        advance(case["id"])


def approve_and_send(case_id, approver):
    case = store.load(case_id)
    if case is None:
        raise ValueError(f"no case with id {case_id!r}")
    if case["state"] != config.PENDING_APPROVAL:
        raise ValueError(f"case {case_id} is in state {case['state']!r}, not pending_approval")
    store.approve(case_id, approver)
    _send(case_id)


def _gate(case):
    # Load-bearing: nothing may send without the human flip recorded in Firestore.
    if case["state"] != config.APPROVED or not case["approval"]["approved"]:
        raise PermissionError(f"case {case['id']} not approved; send blocked")


def _intake(case_id, case, attachments):
    src = case["source"]
    result, usage = _run_validated(
        intake_agent, case_id,
        f"Classify this message.\nFrom: {src['sender']}\nSubject: {src['subject']}\nBody:\n{src['body']}",
        attachments,
    )
    store.record_usage(case_id, "intake_agent", config.MODEL_FLASH, usage)
    category = result.category
    if category == "noise":
        store.transition(case_id, "intake_agent", config.NOISE)
    elif category == "provider_reply":
        matched = store.match_reply(src["sender"])
        note = f"provider reply; closed case {matched['id']}" if matched else "provider reply, no open case"
        store.transition(case_id, "intake_agent", config.CLOSED, {"note": note})
        if matched:
            store.transition(matched["id"], "intake_agent", config.CLOSED, {"note": "provider replied"})
    else:
        _extract(case_id, case, category, attachments)


def _extract(case_id, case, category, attachments):
    src = case["source"]
    prompt = (f"Extract the billing data from this {category.upper()} document.\n"
              f"From: {src['sender']}\nSubject: {src['subject']}\nBody:\n{src['body']}")
    extracted, usage = _run_validated(extractor_agent, case_id, prompt, attachments)
    store.record_usage(case_id, "extractor_agent", config.MODEL_FLASH, usage)

    if category == "eob":
        twin = store.find_awaiting_eob_case(extracted.provider_name, extracted.patient_name)
        if twin:
            store.db().collection("cases").document(twin["id"]).update(
                {"docs.eob": extracted.model_dump(), "updated_at": store.now()}
            )
            store.transition(case_id, "extractor_agent", config.CLOSED,
                             {"note": f"EOB merged into case {twin['id']}"})
            return
        store.transition(case_id, "extractor_agent", config.AWAITING_DOCS)
        return

    store.transition(case_id, "extractor_agent", config.RECONCILED,
                     {"docs.bill": extracted.model_dump()})
    _reconcile(case_id, store.load(case_id))


def _reconcile(case_id, case):
    result, usage = _run_validated(
        reconciliation_agent, case_id, f"Audit these documents:\n{json.dumps(case['docs'], default=str)}"
    )
    store.record_usage(case_id, "reconciliation_agent", config.MODEL_FLASH, usage)
    patch = {
        "discrepancies": [d.model_dump() for d in result.flagged_discrepancies],
        "pricing_confidence": result.pricing_confidence,
    }
    if result.flagged_discrepancies and result.pricing_confidence >= config.RECONCILIATION_CONFIDENCE_THRESHOLD:
        store.transition(case_id, "reconciliation_agent", config.RECONCILED, patch)
        _draft(case_id, store.load(case_id))
    elif result.flagged_discrepancies:
        patch["note"] = f"confidence {result.pricing_confidence} below threshold"
        store.transition(case_id, "reconciliation_agent", config.CLOSED, patch)
    else:
        patch["note"] = "no discrepancies found"
        store.transition(case_id, "reconciliation_agent", config.CLOSED, patch)


def _draft(case_id, case):
    bill = case["docs"]["bill"]
    prompt = (
        f"Patient: {bill['patient_name']}\nProvider: {bill['provider_name']}\n"
        f"Bill date: {bill['bill_date']}\n"
        f"Flagged discrepancies: {json.dumps(case['discrepancies'])}\n"
        "Draft the dispute letter."
    )
    letter, usage = _run_validated(dispute_draft_agent, case_id, prompt)
    store.record_usage(case_id, "dispute_draft_agent", config.MODEL_PRO, usage)
    store.transition(case_id, "dispute_draft_agent", config.PENDING_APPROVAL,
                     {"letter": letter.model_dump(), "recipient": bill["provider_name"]})


def _send(case_id):
    case = store.load(case_id)
    _gate(case)
    # gmail auth is operator-local (OAuth user scopes); keep it out of the import path
    # so pipeline logic stays testable without Google credentials.
    from clearbill import gmail
    to = case["docs"]["bill"].get("contact_email") or config.DEMO_RECIPIENT
    if not to:
        raise ValueError("no recipient: bill has no contact_email and DEMO_RECIPIENT is unset")
    gmail.send(to, case["letter"]["subject"], case["letter"]["body"])
    store.transition(case_id, "action_agent", config.AWAITING_REPLY, {
        "sent_at": store.now(),
        "followup_due": store.now() + timedelta(days=config.FOLLOWUP_DAYS),
    })


def run_followups():
    escalated = []
    for case in store.due_followups():
        try:
            if case.get("is_escalation"):
                store.transition(case["id"], "followup_agent", config.CLOSED,
                                 {"note": "escalation window elapsed; manual action needed"})
            else:
                _escalate(case["id"], case)
                escalated.append(case["id"])
        except Exception as e:
            store.dead_letter(case["id"], "followup_agent", str(e))
    return escalated


def _escalate(case_id, case):
    bill = case["docs"]["bill"]
    total = sum(d["amount_disputed"] for d in case["discrepancies"])
    prompt = (
        f"Patient: {bill['patient_name']}\nProvider: {bill['provider_name']}\n"
        f"Original dispute letter (sent {case['sent_at']}) subject: \"{case['letter']['subject']}\"\n"
        f"Total disputed: ${total:.2f}\nWrite the follow-up letter."
    )
    letter, usage = _run_validated(followup_agent, case_id, prompt)
    store.record_usage(case_id, "followup_agent", config.MODEL_FLASH, usage)
    store.transition(case_id, "followup_agent", config.PENDING_APPROVAL,
                     {"letter": letter.model_dump(), "is_escalation": True})


def _run_validated(agent, case_id, prompt, attachments=()):
    """One retry on transient Vertex 5xx; one retry on schema failure with the error appended; else dead-letter."""
    last_error = None
    for attempt in range(2):
        try:
            return run_agent(agent, case_id, prompt, attachments)
        except genai_errors.ServerError:
            if attempt:
                store.dead_letter(case_id, agent.name, "Vertex 5xx twice")
                raise
            time.sleep(5)  # ADK exposes no per-call retry knob; one manual backoff
        except (ValidationError, AgentError) as e:
            last_error = e
            prompt = f"{prompt}\n\nYour previous reply was invalid: {e}\nReturn only JSON matching the schema."
    store.dead_letter(case_id, agent.name, str(last_error))
    raise last_error
