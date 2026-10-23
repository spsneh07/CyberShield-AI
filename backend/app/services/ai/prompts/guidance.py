from app.schemas.classification import ClassificationResult
from app.schemas.guidance import Citation
from app.schemas.incident import IncidentData
from app.services.ai.prompts.common import SAFETY_RULES, input_block, sources_block

TASK = "guidance"

SYSTEM = f"""{SAFETY_RULES}

Task: give practical response guidance for THIS incident.
- immediate_actions: ordered, most urgent first (e.g. contact bank to block card if money/OTP was involved). Tailor to what already happened; skip actions listed in actions_taken.
- security_steps: account/device hardening steps.
- reporting_guidance: where/how to report — ONLY channels, numbers and URLs present in <sources>. If none are present, say to report to the official national cybercrime reporting channel or local police without inventing details.
- evidence_checklist: items to preserve, each with why and priority (high/medium/low); set already_available=true for items in evidence_available.
- do_not: things the victim should avoid (e.g. sharing more OTPs, deleting chats, paying "recovery agents").
- source_ids: ids of <sources> you relied on.
Uncertainty rules:
- If financial_loss is null, do NOT say or imply that money was lost or that a transaction happened. Tell the user how to check (bank/UPI app, SMS alerts, statement) and what to do if money did move.
- If an OTP, PIN or password may have been shared, treat it as urgent (contact the bank through official channels, change the password) without assuming money was taken.
- Give the safe protective steps first even when details are missing; do not wait for every fact.
- Never ask the user to share an OTP, PIN, password, CVV or full card number.
- Never promise that money will be recovered, and never state deadlines or time limits that are not in <sources>."""


def build(incident: IncidentData, classification: ClassificationResult, sources: list[Citation]) -> str:
    return (
        input_block({
            "incident": incident.model_dump(mode="json", exclude={"field_sources"}),
            "classification": classification.model_dump(),
        })
        + "\n"
        + sources_block(sources)
    )
