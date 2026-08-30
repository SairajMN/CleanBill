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
# hackathon mandate: every model must be Gemini 3.5+. No 3.5+ Pro exists on Vertex yet,
# so the drafting "Pro tier" is the newest Flash; restore a true Pro when Google ships 3.5-pro.
MODEL_PRO = "gemini-3.7-flash"

RECONCILIATION_CONFIDENCE_THRESHOLD = 0.7
FOLLOWUP_DAYS = 14

# When ON, a bill with a matched EOB and above-threshold discrepancies auto-sends
# its dispute (approver="auto-agent"). Off keeps the manual Send button. Either way
# send only happens after the approval flip is recorded in Firestore.
AUTO_SEND = os.environ.get("AUTO_SEND", "1") == "1"
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "120"))

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

# Send target for the demo: letters are delivered here (operator mailbox) regardless
# of the bill contact_email, so a click is always verifiable.
DEMO_RECIPIENT = os.environ.get("DEMO_RECIPIENT", "resell.it.here@gmail.com")
