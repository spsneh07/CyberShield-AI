"""Incident extraction: LLM structured output, grounded and merged with regex identifiers."""
import logging
import re

from app.schemas.incident import FactSource, IncidentData
from app.services.ai.base import LLMProvider, StructuredOutputError
from app.services.ai.prompts import extraction as prompt
from app.services.ai.structured import generate_structured
from app.services.incident.identifiers import extract_identifiers, grounded

log = logging.getLogger(__name__)

ID_FIELDS = ("phone_numbers", "emails", "urls", "upi_ids", "account_identifiers")

# Importance-ordered facts and the question we ask when they are unknown. The first four change what the
# user should do next, so they come before the details a complaint needs. None asks for the secret itself.
QUESTIONS: dict[str, str] = {
    "loss_conflict": "You mentioned both losing money and not losing money. Could you confirm: has any money left your account?",
    "money_moved": ("Please check your bank or UPI app and SMS alerts now: has any money left your account since then? "
                    "(Don't tell me the OTP or PIN itself.)"),
    "details_entered": ("After opening the link, did you type any password, card details or OTP, or install an app? "
                        "(Just yes or no — please don't share the details themselves.)"),
    "engaged": "Have you replied, clicked a link, made a payment or shared any OTP, PIN or card details with them yet?",
    "date_time": "When did this happen (date and approximate time)?",
    "platform": "Where were you contacted — WhatsApp, SMS, phone call, email, a website or an app?",
    "financial_loss": "Did any money leave your account, or did you share an OTP, PIN or card details?",
    "amount": "How much money was lost, and in what currency?",
    "transaction_id": "Do you have the transaction ID / UTR number of the payment?",
    "suspect_contact": "Do you have the phone number, profile, UPI ID, email or link the suspect used?",
    "evidence": "Have you saved screenshots or the original messages?",
}

# Deterministic signals read from the user's own words (English only; shortcut: misses paraphrases and
# other languages — the LLM extraction still runs, these only decide which question to ask first).
_NEG = re.compile(r"\b(not|no|never|didn'?t|did not|haven'?t|hasn'?t|wasn'?t|nothing|without)\b[^.;!?]{0,30}$", re.I)
_UNSURE = re.compile(r"\b(if|whether|not sure|don'?t know|unsure|might|may have)\b[^.;!?]{0,40}$", re.I)
_LOSS = re.compile(r"\b(lost|lose|loss|debited|deducted|stolen|withdrawn|taken from|transferred (out|from))\b", re.I)
_SHARED = re.compile(r"\b(shared|gave|told|sent|entered|typed|provided|disclosed|read out)\b[^.;!?]{0,30}"
                     r"\b(otp|one[- ]time password|pin|cvv|password|card (details|number)|net ?banking)\b", re.I)
_CLICKED = re.compile(r"\b(clicked|opened|tapped|visited)\b[^.;!?]{0,30}\b(link|url|website|site|page)\b", re.I)
_ENTERED_ANY = re.compile(r"\b(entered|typed|filled|submitted|installed|downloaded|logged in|shared|gave|paid)\b", re.I)


def _stated(pattern: re.Pattern[str], text: str) -> tuple[bool, bool]:
    """(asserted, denied) for a pattern; uncertain mentions ("not sure if ... debited") count as neither."""
    asserted = denied = False
    for m in pattern.finditer(text):
        before = text[: m.start()]
        if _UNSURE.search(before):
            continue
        if _NEG.search(before):
            denied = True
        else:
            asserted = True
    return asserted, denied


def loss_mentioned(text: str) -> bool:
    return _LOSS.search(text) is not None


def sensitive_shared(text: str) -> bool:
    return _stated(_SHARED, text)[0]


def clicked_link(text: str) -> bool:
    return _stated(_CLICKED, text)[0]


def loss_conflict(message: str, previous: IncidentData | None = None) -> bool:
    """The message both claims and denies a loss, or contradicts what was recorded before."""
    asserted, denied = _stated(_LOSS, message)
    before = previous.financial_loss if previous else None
    return (asserted and denied) or (denied and before is True) or (asserted and before is False)


DECISIVE_LOSS_QUESTIONS = {"loss_conflict", "money_moved", "engaged"}


def compute_missing(data: IncidentData, conflict: bool = False) -> list[str]:
    missing: list[str] = []
    text = data.description
    shared, clicked = sensitive_shared(text), clicked_link(text)
    if conflict:
        missing.append("loss_conflict")
    elif data.financial_loss is None and shared:
        missing.append("money_moved")
    if clicked and not shared and not _ENTERED_ANY.search(text):
        missing.append("details_entered")
    if data.financial_loss is None and not (shared or clicked or _ENTERED_ANY.search(text) or missing):
        missing.append("engaged")
    if not data.date_time:
        missing.append("date_time")
    if not data.platform:
        missing.append("platform")
    if data.financial_loss is None:
        missing.append("financial_loss")
    if data.financial_loss and data.amount is None:
        missing.append("amount")
    if data.financial_loss and not data.account_identifiers:
        missing.append("transaction_id")
    if not any(getattr(data, f) for f in ("phone_numbers", "emails", "urls", "upi_ids")):
        missing.append("suspect_contact")
    if not data.evidence_available:
        missing.append("evidence")
    return missing


def follow_up_questions(missing: list[str], limit: int = 3) -> list[str]:
    skip = {"financial_loss"} if DECISIVE_LOSS_QUESTIONS & set(missing) else set()  # same question, sharper
    return [QUESTIONS[m] for m in missing if m in QUESTIONS and m not in skip][:limit]


class IncidentExtractor:
    def __init__(self, llm: LLMProvider) -> None:
        self.llm = llm

    def extract(self, message: str, previous: IncidentData | None = None) -> tuple[IncidentData, list[str]]:
        """Return (validated incident, warnings)."""
        warnings: list[str] = []
        user_text = ((previous.description + "\n") if previous else "") + message
        try:
            data = generate_structured(
                self.llm, task=prompt.TASK, system=prompt.SYSTEM,
                user=prompt.build(message, previous), schema=IncidentData,
            )
        except StructuredOutputError:
            log.warning("extraction failed; using deterministic fallback")
            warnings.append("AI extraction failed; identifiers were extracted with rule-based parsing only.")
            data = previous.model_copy(deep=True) if previous else IncidentData()
            data.description = user_text.strip()

        self._ground(data, user_text, previous)
        if previous:
            self._keep_previous_facts(data, previous)
        conflict = loss_conflict(message, previous)
        # "Not sure if money was debited" is neither a loss nor a denial: only an uncertain mention, no amount,
        # and nothing settled earlier in the conversation.
        only_unsure = loss_mentioned(message) and not any(_stated(_LOSS, message))
        settled = previous is not None and previous.financial_loss is not None
        if data.financial_loss is not None and only_unsure and data.amount is None and not settled:
            conflict = False
            data.financial_loss = None
            data.field_sources["financial_loss"] = FactSource.unknown
        if conflict:  # don't pick a side: the user is asked to confirm
            data.financial_loss = None
            data.field_sources["financial_loss"] = FactSource.unknown
        data.missing_information = compute_missing(data, conflict)
        return data, warnings

    @staticmethod
    def _ground(data: IncidentData, text: str, previous: IncidentData | None) -> None:
        """Drop identifiers the model invented; add ones the regexes found."""
        found = extract_identifiers(text)
        for f in ID_FIELDS:
            kept = grounded(getattr(data, f), text)
            for v in getattr(found, f):
                if v not in kept:
                    kept.append(v)
            setattr(data, f, kept)
            if kept:
                data.field_sources.setdefault(f, FactSource.user_provided)
        if data.amount is None and found.amount is not None:
            data.amount, data.currency = found.amount, found.currency
            data.field_sources["amount"] = FactSource.user_provided
        if data.amount is not None and found.amount is None and not (previous and previous.amount == data.amount):
            # An amount that never appears in the text is an invention.
            if str(int(data.amount)) not in text.replace(",", ""):
                data.amount, data.currency = None, None
        if data.amount is not None and data.financial_loss is None:
            data.financial_loss = True
            data.field_sources.setdefault("financial_loss", FactSource.inferred)
        if not data.description:
            data.description = text.strip()

    @staticmethod
    def _keep_previous_facts(data: IncidentData, previous: IncidentData) -> None:
        for name in IncidentData.model_fields:
            if name in ("missing_information", "confidence", "description", "field_sources"):
                continue
            old, new = getattr(previous, name), getattr(data, name)
            if isinstance(old, list):
                setattr(data, name, old + [x for x in new if x not in old])
            elif new in (None, "") and old not in (None, ""):
                setattr(data, name, old)
        for k, v in previous.field_sources.items():
            data.field_sources.setdefault(k, v)
        if len(previous.description) > len(data.description):
            data.description = previous.description
