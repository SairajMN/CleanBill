"""Offline check: the human-approval gate blocks sending until the flip."""
import pytest

import clearbill.config as config
from clearbill import pipeline


def test_send_blocked_until_gate_flips():
    pending = {"id": "t1", "state": config.PENDING_APPROVAL, "approval": {"approved": False}}
    with pytest.raises(PermissionError):
        pipeline._gate(pending)

    flipped_but_wrong_state = {"id": "t3", "state": config.AWAITING_REPLY,
                               "approval": {"approved": True}}
    with pytest.raises(PermissionError):
        pipeline._gate(flipped_but_wrong_state)

    approved = {"id": "t2", "state": config.APPROVED,
                "approval": {"approved": True, "approver": "sahil"}}
    pipeline._gate(approved)  # must not raise


def test_money_rounding():
    from clearbill.schemas import LineItem
    item = LineItem(description="x", amount_billed=123.456)
    assert item.amount_billed == 123.46
