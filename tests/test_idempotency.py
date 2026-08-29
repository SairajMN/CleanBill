"""Live Firestore check: re-processing the same source never creates a second case."""
import uuid

from clearbill import store


def test_duplicate_source_creates_one_case():
    marker = uuid.uuid4().hex
    args = (f"billing@{marker}.example", "Your bill", "2026-08-01", f"idempotency-test {marker}")
    case_id, created = store.create_case(*args)
    assert created
    case_id2, created2 = store.create_case(*args)
    assert case_id2 == case_id
    assert not created2
    store.db().collection("cases").document(case_id).delete()
