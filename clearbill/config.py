import os

# ADK resolves bare model names through google-genai, which reads these env vars.
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "agent-506812")
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", "global")
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "true")

PROJECT = os.environ["GOOGLE_CLOUD_PROJECT"]
LOCATION = os.environ["GOOGLE_CLOUD_LOCATION"]

MODEL_FLASH = "gemini-3.5-flash"
# Pro is reserved for the one output a human billing office actually reads; no GA
# 3.x Pro exists on Vertex yet, so we ride the preview — swap the string when it ships.
MODEL_PRO = "gemini-3.1-pro-preview"

RECONCILIATION_CONFIDENCE_THRESHOLD = 0.7
FOLLOWUP_DAYS = 14

INTAKE = "intake"
AWAITING_DOCS = "awaiting_docs"
RECONCILED = "reconciled"
PENDING_APPROVAL = "pending_approval"
APPROVED = "approved"
AWAITING_REPLY = "awaiting_reply"
CLOSED = "closed"
NOISE = "noise"
DEAD_LETTER = "dead_letter"

GMAIL_QUERY = (
    'is:unread (invoice OR statement OR "explanation of benefits" '
    'OR "patient responsibility" OR billing)'
)

# Fallback send target when the bill shows no billing-office email.
DEMO_RECIPIENT = os.environ.get("DEMO_RECIPIENT", "")
