import html


def _money(v):
    try:
        return f"${float(v):,.2f}"
    except (TypeError, ValueError):
        return "$0.00"


def _esc(x):
    return html.escape(str(x or ""))


_LABELS = {
    "duplicate_charge": "Duplicate charge",
    "pricing_mismatch": "Pricing mismatch",
    "undocumented_charge": "Undocumented charge",
}


def render_dispute_email(case):
    """Branded disposable HTML for the dispute letter; returns (subject, text, html)."""
    letter = case.get("letter") or {}
    bill = (case.get("docs") or {}).get("bill") or {}
    dis = case.get("discrepancies") or []
    total = sum(float(d.get("amount_disputed") or 0) for d in dis)

    subject = _esc(letter.get("subject") or "Medical Bill Dispute")

    text = (
        f"DISPUTE LETTER\n\n"
        f"Patient: {bill.get('patient_name', '')}\n"
        f"Provider: {bill.get('provider_name', '')}\n"
        f"Bill date: {bill.get('bill_date', '')}\n"
        f"Total disputed: {_money(total)}\n\n"
        f"{letter.get('body', '')}\n"
    )

    rows = "".join(
        f"<tr>"
        f"<td style='padding:10px 12px;border-bottom:1px solid #e1e8ef;font-size:13px;font-weight:600;color:#c0392b;'>{_esc(_LABELS.get(d.get('issue_type'), d.get('issue_type')))}</td>"
        f"<td style='padding:10px 12px;border-bottom:1px solid #e1e8ef;font-size:13px;color:#334;'>{_esc(d.get('description'))}</td>"
        f"<td style='padding:10px 12px;border-bottom:1px solid #e1e8ef;font-size:13px;text-align:right;font-weight:700;color:#c0392b;'>{_money(d.get('amount_disputed'))}</td>"
        f"</tr>"
        for d in dis
    ) or (
        "<tr><td colspan='3' style='padding:12px;font-size:13px;color:#1b7f4d;'>"
        "No discrepancies listed.</td></tr>"
    )

    html_body = f"""<!DOCTYPE html>
<html><body style="margin:0;padding:0;background:#f4f6f8;font-family:Arial,Helvetica,sans-serif;">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f4f6f8;padding:24px;">
<tr><td align="center">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:640px;background:#ffffff;border-radius:10px;overflow:hidden;border:1px solid #dce4ec;">
<tr><td style="background:#0d3b66;padding:26px 28px;color:#ffffff;">
  <div style="font-size:22px;font-weight:800;letter-spacing:.3px;">ClearBill</div>
  <div style="font-size:13px;opacity:.85;margin-top:4px;">Medical Bill Dispute Letter</div>
</td></tr>
<tr><td style="padding:30px 34px;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="font-size:13px;color:#334;margin-bottom:20px;">
    <tr><td style="padding:3px 0;color:#5a6b7e;width:110px;">Patient</td><td style="padding:3px 0;font-weight:600;">{_esc(bill.get('patient_name'))}</td></tr>
    <tr><td style="padding:3px 0;color:#5a6b7e;">Provider</td><td style="padding:3px 0;font-weight:600;">{_esc(bill.get('provider_name'))}</td></tr>
    <tr><td style="padding:3px 0;color:#5a6b7e;">Bill date</td><td style="padding:3px 0;font-weight:600;">{_esc(bill.get('bill_date'))}</td></tr>
  </table>
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#fff8f7;border:1px solid #f2d7d4;border-radius:8px;margin-bottom:24px;">
    <tr><td style="padding:16px 20px;">
      <div style="font-size:12px;color:#c0392b;text-transform:uppercase;letter-spacing:.5px;">Total amount disputed</div>
      <div style="font-size:30px;font-weight:800;color:#c0392b;margin-top:2px;">{_money(total)}</div>
      <div style="font-size:12px;color:#7a5b00;margin-top:4px;">{len(dis)} flagged item(s)</div>
    </td></tr>
  </table>
  <div style="font-size:15px;font-weight:700;color:#0d3b66;margin-bottom:8px;">Flagged discrepancies</div>
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="border:1px solid #e1e8ef;border-radius:8px;margin-bottom:26px;">
    <tr style="background:#f2f6fa;">
      <td style="padding:9px 12px;font-size:11px;text-transform:uppercase;color:#5a6b7e;letter-spacing:.4px;">Issue</td>
      <td style="padding:9px 12px;font-size:11px;text-transform:uppercase;color:#5a6b7e;letter-spacing:.4px;">Detail</td>
      <td style="padding:9px 12px;font-size:11px;text-transform:uppercase;color:#5a6b7e;letter-spacing:.4px;text-align:right;">Amount</td>
    </tr>
    {rows}
  </table>
  <div style="font-size:15px;font-weight:700;color:#0d3b66;margin-bottom:8px;">Dispute letter</div>
  <div style="background:#f8fafc;border:1px solid #e1e8ef;border-left:4px solid #0d3b66;border-radius:8px;padding:18px 20px;font-size:14px;line-height:1.6;color:#1a2332;white-space:pre-wrap;">{_esc(letter.get('body'))}</div>
</td></tr>
<tr><td style="padding:18px 34px;background:#f7fafd;border-top:1px solid #e1e8ef;font-size:12px;color:#5a6b7e;">
  Sent by ClearBill on behalf of the patient{(' &mdash; ' + _esc(bill.get('patient_name'))) if bill.get('patient_name') else ''}. A correction is expected within 30 days.
</td></tr>
</table>
</td></tr></table>
</body></html>"""

    return subject, text, html_body