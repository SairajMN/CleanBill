import base64
from email.message import EmailMessage

from googleapiclient.discovery import build

import clearbill.config as config

# Auth is the operator's OAuth user via ADC with gmail scopes (see README).
# ponytail: Cloud Run can't touch a personal Gmail without Workspace delegation,
# so /ingest and /approve runs locally for the demo; upgrade when the inbox is a Workspace account.


def _svc():
    return build("gmail", "v1", cache_discovery=False)


def _exec(request):
    return request.execute(num_retries=1)


def fetch_unread(max_results=10):
    svc = _svc()
    ids = _exec(svc.users().messages().list(userId="me", q=config.GMAIL_QUERY,
                                            maxResults=max_results)).get("messages", [])
    return [_parse(svc, _exec(svc.users().messages().get(userId="me", id=m["id"], format="full")))
            for m in ids]


def _parse(svc, msg):
    headers = {h["name"].lower(): h["value"] for h in msg["payload"]["headers"]}
    text, attachments = _walk(svc, msg["id"], msg["payload"])
    return {
        "id": msg["id"],
        "sender": headers.get("from", ""),
        "subject": headers.get("subject", ""),
        "received_date": headers.get("date", ""),
        "body": text,
        "attachments": attachments,
    }


def _walk(svc, msg_id, part):
    text, attachments = "", []
    body = part.get("body", {})
    if part.get("mimeType") == "text/plain" and body.get("data"):
        text = base64.urlsafe_b64decode(body["data"]).decode("utf-8", "replace")
    for sub in part.get("parts", []):
        if sub.get("filename") and sub.get("body", {}).get("attachmentId"):
            att = _exec(svc.users().messages().attachments().get(
                userId="me", messageId=msg_id, id=sub["body"]["attachmentId"]))
            attachments.append((base64.urlsafe_b64decode(att["data"]), sub["mimeType"]))
        else:
            t, a = _walk(svc, msg_id, sub)
            text += t
            attachments += a
    return text, attachments


def mark_read(message_id):
    _exec(_svc().users().messages().modify(
        userId="me", id=message_id, body={"removeLabelIds": ["UNREAD"]}))


def send(to, subject, body):
    msg = EmailMessage()
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    _exec(_svc().users().messages().send(userId="me", body={"raw": raw}))
