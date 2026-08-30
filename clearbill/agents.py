import asyncio

from google.adk.agents import LlmAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

import clearbill.config as config
import clearbill.schemas as schemas


def _llm(name, schema, instruction, model=config.MODEL_FLASH, max_tokens=1000, temperature=0,
         thinking_level=None):
    # thinking tokens count against max_output_tokens — ceilings must leave room for them
    gen_kwargs = {"temperature": temperature, "max_output_tokens": max_tokens}
    if thinking_level:
        gen_kwargs["thinking_config"] = types.ThinkingConfig(thinking_level=thinking_level)
    return LlmAgent(
        name=name,
        model=model,
        instruction=instruction,
        output_schema=schema,
        output_key=name,
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
        generate_content_config=types.GenerateContentConfig(**gen_kwargs),
    )


INTAKE_INSTR = """You triage one email in a personal inbox for medical billing disputes.
Classify exactly one category:
- bill: a medical bill or invoice from a healthcare provider requesting payment
- eob: an insurance Explanation of Benefits or claim summary
- provider_reply: a reply from a provider, billing office, or insurer referencing a prior dispute
- noise: anything else (marketing, appointment reminders, newsletters, lab portals)
Judge only the content given."""

EXTRACT_INSTR = """Extract the billing data from the document below (a medical bill and/or an EOB).
Rules:
- One entry per line item, in document order. If the same service appears twice, list it twice.
- amount_billed: what the provider charged. insurance_paid / patient_responsibility from the EOB if present, else 0.
- total_billed and total_patient_responsibility must equal the sum over line items; if the document states totals, use those.
- code is the CPT/HCPCS code, empty if none shown.
- contact_email: copy the billing office email ONLY if it is explicitly printed in the document, character for character. Never guess, never construct one from a domain or name; if not printed, use the empty string. This address receives a legal dispute letter, so a wrong value causes a misdelivery."""

RECON_INSTR = """You are a medical billing auditor. You receive the extracted bill and EOB as JSON.
Flag these issue types only:
- duplicate_charge: the same service (same code, or same description and amount) billed more than once — flag each repeat after the first.
- pricing_mismatch: the EOB's allowed amount for a code contradicts the bill with no documented adjustment.
- undocumented_charge: a line item on the bill with no matching EOB entry.
amount_disputed = dollars the patient should not owe for that flag. pricing_confidence is your overall certainty in [0,1].
Flag nothing you cannot justify from the documents themselves."""

DRAFT_INSTR = """Write a formal dispute letter from the patient to the provider's billing office.
Requirements:
- Name the patient and provider; reference the bill date.
- For each discrepancy: name the line item description and code, state the disputed amount, and say exactly what is wrong.
- Ask for a corrected bill and refund of any overpayment within 30 days.
- Firm, factual, polite. No accusations of fraud. Under 400 words. Plain text, no markdown."""

FOLLOWUP_INSTR = """Write a short follow-up letter from the patient to the same billing office
about the earlier dispute letter described below. No response was received.
Restate the total disputed amount, request a written response within 10 business days,
and note the patient may escalate to the state insurance commissioner and the insurer's appeals office.
Under 200 words. Plain text."""

intake_agent = _llm("intake_agent", schemas.IntakeResult, INTAKE_INSTR,
                    max_tokens=1000, thinking_level="low")
extractor_agent = _llm("extractor_agent", schemas.ExtractedBill, EXTRACT_INSTR,
                       max_tokens=4000, thinking_level="low")
reconciliation_agent = _llm("reconciliation_agent", schemas.Reconciliation, RECON_INSTR,
                            max_tokens=2000, thinking_level="low")
# Only this agent uses Pro: the letter is the one artifact a human billing office reads.
dispute_draft_agent = _llm("dispute_draft_agent", schemas.DisputeLetter, DRAFT_INSTR,
                           model=config.MODEL_PRO, max_tokens=4000, temperature=0.4)
followup_agent = _llm("followup_agent", schemas.DisputeLetter, FOLLOWUP_INSTR,
                      max_tokens=3000, temperature=0.4)


class AgentError(Exception):
    pass


_runners = {}


def _runner(agent):
    if agent.name not in _runners:
        _runners[agent.name] = Runner(app_name="clearbill", agent=agent,
                                      session_service=InMemorySessionService())
    return _runners[agent.name]


def run_agent(agent, case_id, prompt, attachments=()):
    """Runs one ADK agent turn; returns (parsed output, token usage). Raises on empty or invalid output."""
    runner = _runner(agent)
    # sync entry points (FastAPI sync handlers, tests) have no running loop; create_session is async
    session = asyncio.run(runner.session_service.create_session(app_name="clearbill", user_id=case_id))
    parts = [types.Part(text=prompt)]
    parts += [types.Part.from_bytes(data=data, mime_type=mime) for data, mime in attachments]
    message = types.Content(role="user", parts=parts)
    text, usage = "", {"prompt": 0, "completion": 0}
    for event in runner.run(user_id=case_id, session_id=session.id, new_message=message):
        if event.content and event.content.parts:
            text = "".join(p.text or "" for p in event.content.parts) or text
        meta = event.usage_metadata
        if meta:
            usage["prompt"] += meta.prompt_token_count or 0
            usage["completion"] += meta.candidates_token_count or 0
    if not text:
        raise AgentError(f"{agent.name} returned no output")
    parsed = agent.output_schema.model_validate_json(text)
    return parsed, usage
