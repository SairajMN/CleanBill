"""Offline agent-logic check for the auto-dispute flow: a bill alone waits for its EOB
and never sends; once the matching EOB lands it reconciles, drafts, and auto-sends to
the bill's contact_email. LLM calls are stubbed; only Firestore is live."""
import uuid

import pytest

import clearbill.config as config
from clearbill import gmail, pipeline, store
from clearbill.schemas import (
    DisputeLetter,
    ExtractedBill,
    IntakeResult,
    Reconciliation,
)


@pytest.fixture(autouse=True)
def _auto_on(monkeypatch):
    monkeypatch.setattr(config, "AUTO_SEND", True)


@pytest.fixture(autouse=True)
def _stub_agents(monkeypatch):
    calls = {"intake": [], "extract": []}

    def fake_run(agent, case_id, prompt, attachments=()):
        usage = {"prompt": 10, "completion": 5}
        import re as _re
        m = _re.search(r"Harbor General [0-9a-f]{6}", prompt.upper()) or _re.search(r"Harbor General [0-9a-f]{6}", prompt)
        provider = m.group(0) if m else "Harbor General"
        if agent.name == "intake_agent":
            calls["intake"].append(prompt.upper())
            return IntakeResult(category=("eob" if "BENEFITS" in prompt.upper() else "bill")), usage
        if agent.name == "extractor_agent":
            calls["extract"].append(prompt.upper())
            email = f"billing-{provider.split()[-1]}@harbor.example" if m else "billing@harbor.example"
            doc = ExtractedBill(provider_name=provider, patient_name="Jane Doe",
                                bill_date="2026-08-01", contact_email=(email if "BENEFITS" not in prompt.upper() else ""),
                                line_items=[], total_billed=0.0, total_patient_responsibility=0.0)
            return doc, usage
        if agent.name == "reconciliation_agent":
            return Reconciliation(
                flagged_discrepancies=[{
                    "item_index": 0, "issue_type": "duplicate_charge",
                    "description": "dup", "amount_disputed": 50.0,
                }],
                pricing_confidence=0.95,
            ), usage
        if agent.name == "dispute_draft_agent":
            return DisputeLetter(subject="Dispute", body="Dear Billing..."), usage
        raise AssertionError(f"unexpected agent {agent.name}")

    monkeypatch.setattr(pipeline, "_run_validated", fake_run)
    return calls


@pytest.fixture
def sender():
    marker = uuid.uuid4().hex[:8]
    return f"auto-{marker}@provider.example"


@pytest.fixture
def billing_account():
    # unique provider per run so prior leftover cases never match this run's EOB
    marker = uuid.uuid4().hex[:6]
    return (f"Harbor General {marker}", f"billing-{marker}@harbor.example")


def _process(sender, label, body):
    return pipeline.process_source(sender, label, "2026-08-01", body)[0]


def test_bill_alone_never_sends(sender, billing_account, monkeypatch):
    provider, billing_email = billing_account
    sent = []
    monkeypatch.setattr(gmail, "send", lambda *a, **k: sent.append(a))
    case_id = _process(sender, "Hospital bill", f"STATEMENT {provider}")
    case = store.load(case_id)
    assert case["state"] == config.AWAITING_DOCS
    assert case["docs"].get("bill")
    assert sent == [], "a bill with no EOB must not auto-send"


def test_eob_merges_then_auto_sends(sender, billing_account, monkeypatch):
    provider, billing_email = billing_account
    sent = []
    monkeypatch.setattr(gmail, "send", lambda *a, **k: sent.append(a))
    bill_id = _process(sender, "Hospital bill", f"STATEMENT {provider}")
    assert store.load(bill_id)["state"] == config.AWAITING_DOCS

    eob_id = _process("claims@insurer.example", "EOB",
                      f"EXPLANATION OF BENEFITS {provider} Jane Doe")
    store.db().collection("cases").document(eob_id).delete()

    case = store.load(bill_id)
    assert case["state"] == config.AWAITING_REPLY
    assert case["approval"]["approved"] is True
    assert case["approval"]["approver"] == "auto-agent"
    assert case["docs"].get("eob")
    assert sent, "matching EOB should have triggered an auto-send"
    assert sent[0][0] == billing_email, "auto-send goes to the bill's billing address"