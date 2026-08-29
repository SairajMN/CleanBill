from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel


def _round2(v: float) -> float:
    return round(v, 2)


Money = Annotated[float, AfterValidator(_round2)]


class IntakeResult(BaseModel):
    category: Literal["bill", "eob", "provider_reply", "noise"]


class LineItem(BaseModel):
    service_date: str = ""
    description: str
    code: str = ""
    amount_billed: Money
    insurance_paid: Money = 0.0
    patient_responsibility: Money = 0.0


class ExtractedBill(BaseModel):
    provider_name: str
    patient_name: str
    bill_date: str = ""
    contact_email: str = ""
    line_items: list[LineItem]
    total_billed: Money
    total_patient_responsibility: Money


class Discrepancy(BaseModel):
    item_index: int
    issue_type: Literal["duplicate_charge", "pricing_mismatch", "undocumented_charge"]
    description: str
    amount_disputed: Money


class Reconciliation(BaseModel):
    flagged_discrepancies: list[Discrepancy]
    pricing_confidence: float


class DisputeLetter(BaseModel):
    subject: str
    body: str
