import io
import re
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
    return case


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
    return case
