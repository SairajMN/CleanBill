# CleanBill

The AI billing advocate that watches, catches, disputes, and follows up — so you don't get overcharged.

Google ADK agents on Vertex AI (Gemini 3.5 Flash; dispute drafting on gemini-3.1-pro-preview),
case state in Cloud Firestore, served by Cloud Run.

## Layout

- `clearbill/config.py` — models, thresholds, state names
- `clearbill/schemas.py` — Pydantic schemas enforced as structured output on every LLM call
- `clearbill/agents.py` — the five LLM agents + the ADK runner
- `clearbill/pipeline.py` — state machine; sends happen only after the human-approval gate
- `clearbill/store.py` — Firestore case store (hash-keyed, idempotent), transition logs, token accounting
- `clearbill/gmail.py` — inbox poll + send
- `main.py` — HTTP endpoints

## Endpoints

- `POST /ingest` — poll Gmail unread, create/resume cases
- `POST /approve/{case_id}` `{"approver": "..."}` — the human gate flip; sends the letter
- `POST /followups/run` — escalate cases past their follow-up window
- `POST /resume` — re-drive every open case (crash recovery)
- `GET /case/{case_id}` — full case state

## Running locally

```
pip install -r requirements.txt
gcloud auth application-default login \
  --scopes=https://www.googleapis.com/auth/cloud-platform,https://www.googleapis.com/auth/gmail.modify,https://www.googleapis.com/auth/gmail.send
export DEMO_RECIPIENT=you@example.com   # fallback recipient when the bill shows no billing email
uvicorn main:app --port 8080
```

Gmail-touching endpoints (`/ingest`, `/approve`) run under your user OAuth via ADC.
On Cloud Run the service handles `/case`, `/resume`, `/followups/run`; personal-Gmail
ingest stays local until the inbox is a Workspace account.

## Deployed

https://clearbill-512401546414.us-central1.run.app (min-instances=0)

## Tests

```
python3 -m pytest tests/test_gate.py tests/test_idempotency.py   # offline + Firestore
python3 -m pytest tests/test_live_pipeline.py                    # live Vertex: extraction, planted duplicate
python3 -m pytest tests/test_e2e_resume.py                       # kill/restart, gate, escalation (sends captured)
```
