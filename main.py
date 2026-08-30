import io
import re
import threading
import time
import zipfile

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from googleapiclient.errors import HttpError
from pydantic import BaseModel

import clearbill.config as config  # sets Vertex env vars before ADK resolves models
from clearbill import gmail, pipeline, store

app = FastAPI(title="ClearBill")
app.mount("/static", StaticFiles(directory="static"), name="static")

_stop_polling = threading.Event()


@app.on_event("startup")
def _start_poller():
    # personal-Gmail auto-dispute agent: poll unread mail on a loop while the app runs.
    # Runs under the operator's OAuth; harmless no-op on Cloud Run (no mailbox).
    threading.Thread(target=_poll_loop, daemon=True).start()


@app.on_event("shutdown")
def _stop_poller():
    _stop_polling.set()


def _poll_loop():
    while not _stop_polling.is_set():
        try:
            _poll_once()
        except Exception:
            pass  # transient Gmail/no-net errors must not kill the loop
        _stop_polling.wait(config.POLL_INTERVAL)


def _poll_once():
    try:
        unread = gmail.fetch_unread()
    except HttpError:
        return  # Cloud Run / no mailbox: nothing to do
    for msg in unread:
        pipeline.process_source(
            msg["sender"], msg["subject"], msg["received_date"], msg["body"],
            attachments=tuple(msg["attachments"]),
        )
        gmail.mark_read(msg["id"])


class Approval(BaseModel):
    approver: str


@app.get("/")
def index():
    return FileResponse("static/index.html")


@app.post("/api/analyze")
def analyze(bill_files: list[UploadFile] = File(default=[]), bill_text: str = Form(""),
            eob_files: list[UploadFile] = File(default=[]), eob_text: str = Form("")):
    # sync endpoint: runs in a threadpool, which asyncio.run (inside run_agent) requires

    def _split(files_):
        body, attachments = "", []
        for f in files_:
            data = f.file.read()
            if f.filename and f.filename.lower().endswith(".docx"):
                body += f"\n\n{_docx_text(data)}"  # gemini reads pdf/images inline; docx we unzip ourselves
            else:
                attachments.append((data, f.content_type or "application/octet-stream"))
        return body, attachments

    bill_body, bill_att = _split(bill_files)
    eob_body, eob_att = _split(eob_files)
    bill_body += bill_text.strip()
    eob_body += eob_text.strip()
    if not (bill_body.strip() or bill_att or eob_body.strip() or eob_att):
        raise HTTPException(status_code=400, detail="attach a bill or EOB file, or paste the text")
    case_id = pipeline.analyze_upload(bill_body, tuple(bill_att), eob_body, tuple(eob_att))
    return {"case_id": case_id, "case": store.load(case_id)}


def _docx_text(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        xml = z.read("word/document.xml").decode("utf-8", "replace")
    paragraphs = re.sub(r"</w:p>", "\n", xml)
    return re.sub(r"<[^>]+>", "", paragraphs)


@app.get("/api/cases")
def cases():
    rows = []
    query = store.db().collection("cases").order_by(
        "created_at", direction="DESCENDING").limit(25)
    for snap in query.stream():
        c = snap.to_dict()
        rows.append({
            "id": snap.id,
            "state": c["state"],
            "created_at": c.get("created_at"),
            "discrepancies": len(c.get("discrepancies") or []),
            "has_letter": bool(c.get("letter")),
            "is_escalation": c.get("is_escalation", False),
        })
    return rows


@app.post("/ingest")
def ingest():
    try:
        unread = gmail.fetch_unread()
    except HttpError as e:
        # Cloud Run's service account has no personal mailbox; Gmail ingest is operator-local
        raise HTTPException(
            status_code=503,
            detail="Gmail ingest needs the operator's OAuth user (run locally per README); "
                   f"underlying error: {e.resp.status} {e.reason}",
        )
    out = []
    for msg in unread:
        case_id, created = pipeline.process_source(
            msg["sender"], msg["subject"], msg["received_date"], msg["body"],
            attachments=tuple(msg["attachments"]),
        )
        gmail.mark_read(msg["id"])
        out.append({"case_id": case_id, "created": created, "subject": msg["subject"]})
    return {"processed": out}


@app.post("/approve/{case_id}")
def approve(case_id: str, req: Approval):
    try:
        pipeline.approve_and_send(case_id, req.approver)
    except HttpError as e:
        # Cloud Run's service account has no mailbox; real sends run under local OAuth
        raise HTTPException(
            status_code=503,
            detail="Sending needs the operator's Gmail OAuth — run the app locally "
                   f"(clearbill/README.md). Underlying error: {e.resp.status} {e.reason}",
        )
    except (ValueError, PermissionError) as e:
        raise HTTPException(status_code=404 if "no case" in str(e) else 409, detail=str(e))
    return store.load(case_id)


@app.post("/followups/run")
def followups():
    return {"escalated": pipeline.run_followups()}


@app.post("/resume")
def resume():
    pipeline.resume()
    return {"ok": True}


@app.get("/case/{case_id}")
def get_case(case_id: str):
    case = store.load(case_id)
    if not case:
        raise HTTPException(status_code=404, detail="case not found")
