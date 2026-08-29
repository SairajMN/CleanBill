"""Live checks against Vertex: intake classification, extraction schema, reconciliation
catching the duplicate ECG planted in tests/sample_docs/bill.txt. Requires ADC."""
import json
from pathlib import Path

import pytest

from clearbill.agents import (
    extractor_agent,
    intake_agent,
    reconciliation_agent,
    run_agent,
)

SAMPLES = Path(__file__).parent / "sample_docs"


@pytest.fixture(scope="module")
def bill():
    return (SAMPLES / "bill.txt").read_text()


@pytest.fixture(scope="module")
def extracted_bill(bill):
    parsed, _ = run_agent(
        extractor_agent, "live-test",
        f"Extract the billing data from this BILL document.\n\n{bill}")
    return parsed


def test_intake_classifies_bill_and_noise():
    parsed, _ = run_agent(
        intake_agent, "live-test",
        f"Classify this message.\n{(SAMPLES / 'bill.txt').read_text()}")
    assert parsed.category == "bill"
    parsed, _ = run_agent(
        intake_agent, "live-test",
        f"Classify this message.\n{(SAMPLES / 'noise.txt').read_text()}")
    assert parsed.category == "noise"


def test_extraction_hits_schema(extracted_bill):
    assert extracted_bill.provider_name
    assert extracted_bill.patient_name
    assert extracted_bill.total_billed == 412.00
    assert len(extracted_bill.line_items) == 5


def test_reconciliation_flags_planted_duplicate(extracted_bill):
    eob_text = (SAMPLES / "eob.txt").read_text()
    eob, _ = run_agent(
        extractor_agent, "live-test",
        f"Extract the billing data from this EOB document.\n\n{eob_text}")
    result, _ = run_agent(
        reconciliation_agent, "live-test",
        "Audit these documents:\n"
        + json.dumps({"bill": extracted_bill.model_dump(), "eob": eob.model_dump()}))
    dups = [d for d in result.flagged_discrepancies if d.issue_type == "duplicate_charge"]
    assert dups, "reconciliation missed the planted duplicate ECG charge"
    assert dups[0].amount_disputed == 95.00
    assert result.pricing_confidence >= 0.7
