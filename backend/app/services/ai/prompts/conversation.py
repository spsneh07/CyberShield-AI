from typing import Any

from app.schemas.guidance import Citation
from app.services.ai.prompts.common import SAFETY_RULES, input_block, sources_block

INTENT_TASK = "intent"

INTENT_SYSTEM = f"""{SAFETY_RULES}

Task: classify the user's latest message intent. One of:
- report_incident: describes something that happened to them (scam, fraud, hack, harassment).
- provide_details: adds/corrects details of an incident already being discussed (has_active_incident=true).
- check_message: pastes or describes a suspicious message/call/link and asks if it's a scam.
- question: general question about cybercrime, scams or reporting.
- generate_complaint: asks to write/draft a complaint.
- evidence_checklist: asks what evidence to keep.
- safety_tips: asks for prevention or safety tips.
- smalltalk: greetings, thanks, off-topic."""


def build_intent(message: str, has_active_incident: bool, recent: list[dict[str, str]]) -> str:
    return input_block({"message": message, "has_active_incident": has_active_incident, "recent_messages": recent})


REPLY_TASK = "conversation"

REPLY_SYSTEM = f"""{SAFETY_RULES}

Task: write the assistant's chat reply (markdown, concise, at most ~180 words).
- If analysis is provided, briefly summarise the likely type and the 2-3 most urgent actions; the UI shows full details separately, so don't repeat everything.
- If follow_up_questions are provided, end by asking them (they are the only missing details worth asking; never re-ask known facts).
- If it is a general question, answer it using <sources>; say when the sources don't cover something.
- Never say or imply money was lost unless the incident's financial_loss is true, and never promise that money will be recovered.
- Never ask for an OTP, PIN, password, CVV or full card number.
- For smalltalk, reply briefly and offer help with reporting or checking a suspicious message."""


def build_reply(message: str, intent: str, context: dict[str, Any], sources: list[Citation]) -> str:
    return input_block({"message": message, "intent": intent, **context}) + "\n" + sources_block(sources)
