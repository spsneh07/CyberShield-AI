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
    "loss_conflict": "Could you clarify: was the earlier debit statement a mistake, or was there a suspicious transaction that was subsequently checked and found not to have occurred?",
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

QUESTIONS_HI: dict[str, str] = {
    "loss_conflict": "क्या आप स्पष्ट कर सकते हैं: क्या पहले पैसे कटने की बात गलती से लिखी गई थी, या कोई संदिग्ध लेन-देन था जिसे बाद में जांचने पर पाया गया कि वह नहीं हुआ?",
    "money_moved": ("कृपया अपने बैंक या यूपीआई ऐप और एसएमएस अलर्ट तुरंत देखें: क्या उसके बाद आपके खाते से कोई पैसा कटा है? "
                    "(ओटीपी या पिन यहाँ न बताएं।)"),
    "details_entered": ("लिंक खोलने के बाद, क्या आपने कोई पासवर्ड, कार्ड विवरण या ओटीपी डाला, या कोई ऐप इंस्टॉल किया? "
                        "(केवल हाँ या ना — विवरण साझा न करें।)"),
    "engaged": "क्या आपने उन्हें कोई जवाब दिया, लिंक पर क्लिक किया, भुगतान किया या कोई ओटीपी, पिन या कार्ड विवरण साझा किया?",
    "date_time": "यह घटना कब हुई (तारीख और लगभग समय)?",
    "platform": "आपसे कहाँ संपर्क किया गया था — व्हाट्सएप, एसएमएस, फोन कॉल, ईमेल, वेबसाइट या कोई ऐप?",
    "financial_loss": "क्या आपके खाते से कोई पैसा कटा, या आपने कोई ओटीपी, पिन या कार्ड विवरण साझा किया?",
    "amount": "कितना पैसा कटा, और किस मुद्रा (करेंसी) में?",
    "transaction_id": "क्या आपके पास भुगतान का ट्रांजैक्शन आईडी / यूटीआर (UTR) नंबर है?",
    "suspect_contact": "क्या आपके पास संदिग्ध का फोन नंबर, प्रोफाइल, यूपीआई आईडी, ईमेल या लिंक है?",
    "evidence": "क्या आपने स्क्रीनशॉट या मूल संदेश सहेज कर रखे हैं?",
}

# Regex boundaries compatible with ASCII, Devanagari letters, vowel signs and matras.
# Punctuation like danda (\u0964) and full stop are excluded so word-boundaries match at sentence ends.
_WB = r"(?<![a-zA-Z0-9\u0901-\u0963\u0966-\u096f\u0971-\u097f])"
_WE = r"(?![a-zA-Z0-9\u0901-\u0963\u0966-\u096f\u0971-\u097f])"

# Deterministic signals read from the user's own words in English, Hindi (Devanagari) and Hinglish (Romanised).
_NEG = re.compile(
    rf"{_WB}(?:not|no|never|didn'?t|did\s+not|haven'?t|hasn'?t|wasn'?t|nothing|"
    rf"nahi|nahin|mat|kabhi\s*nahi|kabhi\s*nahin|koi\s*nahi|koi\s*nahin|kuch\s*nahi|kuch\s*nahin|"
    rf"नहीं|नही|ना|न|कभी\s*न|कोई\s*नहीं|कुछ\s*नहीं){_WE}",
    re.I,
)
_UNSURE = re.compile(
    rf"{_WB}(?:if|whether|not\s+sure|don'?t\s+know|unsure|might|may\s+have|"
    rf"pata\s*nahi|pata\s*nahin|nahi\s*pata|nahin\s*pata|pakka\s*nahi|pakka\s*nahin|shayad|"
    rf"ho\s*sakta|ya\s*nahi|ya\s*nahin|confirm\s*nahi|confirm\s*nahin|doubt\s*hai|maloom\s*nahi|"
    rf"पता\s*नहीं|नहीं\s*पता|निश्चय\s*नहीं|पक्का\s*नहीं|शायद|हो\s*सकता|संभवतः|संदेह|या\s*नहीं){_WE}",
    re.I,
)
_LOSS = re.compile(
    rf"{_WB}(?:lost|lose|loss|debited|deducted|stolen|withdrawn|taken\s+from|transferred(?:\s+(?:out|from))?|"
    rf"left\s+(?:my\s+)?account|money\s+left|siphoned|"
    rf"kat\s*gaye|kat\s*gaya|kata|kate|kati|paise\s*gaye|paise\s*chale\s*gaye|paisa\s*gaya|paisa\s*chala\s*gaya|"
    rf"nikal\s*gaye|nikala|nikale|nikli|nuksan|loss\s*hua|debit\s*ho\s*gaya|debit\s*ho\s*gaye|debit\s*huye|debit\s*hue|"
    rf"transfer\s*ho\s*gaya|transfer\s*ho\s*gaye|transfer\s*hue|transfer\s*huye|chori\s*ho\s*gaye|chori\s*ho\s*gaya|chala\s*gaya|chale\s*gaye|uda\s*liye|"
    rf"गंवा|गंवाया|गंवाए|गंवाई|खोया|खोए|खोई|कट|कटा|कटे|कटी|कटने|निकला|निकले|निकली|चोरी|नुकसान|डेबिट|हस्तांतरित|ट्रांसफर|चले\s*गए|चला\s*गया|उड़\s*गए|उड़ा\s*लिया){_WE}",
    re.I,
)

_SAFE_ACCOUNT = re.compile(
    rf"{_WB}(?:account\s+(?:is\s+)?safe|account\s+secure|no\s+money\s+(?:was\s+)?lost|"
    rf"khata\s+surakshit|account\s+safe|खाता\s+सुरक्षित|सुरक्षित\s+है){_WE}",
    re.I,
)

_SHARED_VERBS = (
    r"shared?|giving|gave|give|told|tell|sent|send|entered?|typed?|provided?|disclosed?|read\s*out|"
    r"diya|diye|dee|bheja|bheje|bhej|bataya|bataye|bata|daala|daale|daal|enter\s*kiya|type\s*kiya|share\s*kiya|"
    r"दिया|दिए|दी|भेजा|भेजे|साझा|शेयर|बताया|बताए|डाला|डाले|दर्ज|दे\s*दिया|बता\s*दिया|भेज\s*दिया"
)
_SECRETS = (
    r"otp|one[- ]time\s*password|pin|cvv|passwords?|card\s*(?:details|numbers?)|net\s*banking|"
    r"ओटीपी|पिन|पासवर्ड|सीवीवी|कार्ड"
)
_SHARED = re.compile(
    rf"(?:{_WB}(?:{_SHARED_VERBS}){_WE}[^.;!?।\n]{{0,35}}{_WB}(?:{_SECRETS}){_WE}|{_WB}(?:{_SECRETS}){_WE}[^.;!?।\n]{{0,35}}{_WB}(?:{_SHARED_VERBS}){_WE})",
    re.I,
)

_CLICK_VERBS = r"clicked?|opened?|tapped?|visited?|क्लिक|खोला|खोले|टैप|khol|kholi|khole|click|open"
_CLICK_TARGETS = r"link|urls?|websites?|sites?|pages?|लिंक|वेबसाइट|साइट|यूआरएल|पेज"
_CLICKED = re.compile(
    rf"(?:{_WB}(?:{_CLICK_VERBS}){_WE}[^.;!?।\n]{{0,35}}{_WB}(?:{_CLICK_TARGETS}){_WE}|{_WB}(?:{_CLICK_TARGETS}){_WE}[^.;!?।\n]{{0,35}}{_WB}(?:{_CLICK_VERBS}){_WE})",
    re.I,
)

_REMOTE_TOOLS = r"anydesk|teamviewer|rustdesk|quick\s*support|airdroid|screenshare|screen\s*share|remote\s*access|एनीडेस्क|टीमव्यूअर|स्क्रीन\s*शेयर"
_REMOTE_VERBS = r"install|installed|download|downloaded|karwaya|karaya|bola|access|daala|daalo|use|download\s*kiya|install\s*kiya|इंस्टॉल|डाउनलोड"
_REMOTE_APP = re.compile(
    rf"(?:{_WB}(?:{_REMOTE_TOOLS}){_WE}[^.;!?।\n]{{0,35}}{_WB}(?:{_REMOTE_VERBS}){_WE}|{_WB}(?:{_REMOTE_VERBS}){_WE}[^.;!?।\n]{{0,35}}{_WB}(?:{_REMOTE_TOOLS}){_WE}|{_WB}(?:{_REMOTE_TOOLS}){_WE})",
    re.I,
)

_ENTERED_ANY = re.compile(
    rf"{_WB}(?:entered|typed|filled|submitted|installed|downloaded|logged\s*in|shared|gave|paid|"
    rf"enter\s*kiya|type\s*kiya|fill\s*kiya|submit\s*kiya|install\s*kiya|install\s*karwaya|download\s*kiya|download\s*karaya|"
    rf"login\s*kiya|share\s*kiya|diya|daala|daal\s*diya|dala|bhara|bhar\s*diya|pay\s*kiya|"
    rf"दर्ज|डाला|भरा|इंस्टॉल|डाउनलोड|लॉगिन|शेयर|साझा|दिया|भुगतान|पे){_WE}",
    re.I,
)


def _stated(pattern: re.Pattern[str], text: str) -> tuple[bool, bool]:
    """(asserted, denied) for a pattern; uncertain mentions ("not sure if ... debited") count as neither."""
    asserted = denied = False
    _CLAUSE_SPLIT = re.compile(r"[,.;!?।\n]|\b(?:but|however|lekin|parantu|magar|परन्तु|लेकिन|मगर)\b", re.I)
    for m in pattern.finditer(text):
        matched_text = m.group(0)
        # Extract the clause containing the match without crossing sentence/clause boundaries
        before_clause = _CLAUSE_SPLIT.split(text[: m.start()])[-1]
        after_clause = _CLAUSE_SPLIT.split(text[m.end():])[0]
        # Uncertainty checked within the clause before, after, or inside the match
        if _UNSURE.search(before_clause) or _UNSURE.search(after_clause) or _UNSURE.search(matched_text):
            continue
        # Negation checked within the clause before, after, or inside the match
        if _NEG.search(before_clause) or _NEG.search(after_clause) or _NEG.search(matched_text):
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


def remote_installed(text: str) -> bool:
    return _stated(_REMOTE_APP, text)[0]


def detect_loss_correction(message: str, previous: IncidentData | None) -> tuple[bool, str | None]:
    """Detect if the message corrects or updates previously stated financial loss.

    Returns (is_correction, correction_type):
    - ("denial", "no_loss"): previously loss claimed, now explicitly denied ("no money lost", "account safe")
    - ("uncertain", "unsure"): previously loss claimed, now uncertain ("not sure if debited")
    - ("amount", "new_amount"): previously amount stated, now corrected to a new amount
    - ("confirmed", "loss_confirmed"): previously denied/uncertain, now confirmed debited
    """
    if previous is None:
        return False, None

    had_loss = previous.financial_loss is True or previous.amount is not None
    asserted_loss, denied_loss = _stated(_LOSS, message)
    if not denied_loss and _SAFE_ACCOUNT.search(message):
        denied_loss = True

    new_ids = extract_identifiers(message)
    is_unsure = (loss_mentioned(message) or _LOSS.search(message) is not None) and bool(_UNSURE.search(message))

    # Confirmed loss -> explicit denial
    if had_loss and denied_loss and not asserted_loss:
        return True, "no_loss"

    # Confirmed loss -> uncertain correction
    if had_loss and is_unsure and not asserted_loss:
        return True, "unsure"

    # Amount correction
    if had_loss and new_ids.amount is not None and previous.amount is not None and new_ids.amount != previous.amount:
        return True, "new_amount"

    # Explicit denial -> later confirmed loss
    if previous.financial_loss is False and asserted_loss and not denied_loss:
        return True, "loss_confirmed"

    return False, None


def loss_conflict(message: str, previous: IncidentData | None = None) -> bool:
    """The message both claims and denies a loss, or contradicts what was recorded before."""
    asserted, denied = _stated(_LOSS, message)
    if not denied and _SAFE_ACCOUNT.search(message):
        denied = True
    before = previous.financial_loss if previous else None
    had_loss = before is True or (previous is not None and previous.amount is not None)
    return (asserted and denied) or (denied and had_loss) or (asserted and before is False)


DECISIVE_LOSS_QUESTIONS = {"loss_conflict", "money_moved", "engaged"}


def compute_missing(data: IncidentData, conflict: bool = False) -> list[str]:
    missing: list[str] = []
    text = data.description
    shared, clicked = sensitive_shared(text), clicked_link(text)
    remote = remote_installed(text)
    if conflict:
        missing.append("loss_conflict")
    elif data.financial_loss is None and (shared or remote):
        missing.append("money_moved")
    if clicked and not (shared or remote) and not _ENTERED_ANY.search(text):
        missing.append("details_entered")
    if data.financial_loss is None and not (shared or clicked or remote or _ENTERED_ANY.search(text) or missing):
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


def follow_up_questions(missing: list[str], limit: int = 3, language: str | None = None) -> list[str]:
    from app.services.language import current_language
    lang = language or current_language()
    q_map = QUESTIONS_HI if lang == "hi" else QUESTIONS
    skip = {"financial_loss"} if DECISIVE_LOSS_QUESTIONS & set(missing) else set()  # same question, sharper
    return [q_map[m] for m in missing if m in q_map and m not in skip][:limit]


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

        is_corr, corr_type = detect_loss_correction(message, previous)
        new_ids = extract_identifiers(message)
        asserted_loss, denied_loss = _stated(_LOSS, message)
        if not denied_loss and _SAFE_ACCOUNT.search(message):
            denied_loss = True
        only_unsure = loss_mentioned(message) and not (asserted_loss or denied_loss)

        # Ground identifiers against appropriate text:
        # If user corrected or denied loss, do not pick up stale amounts from previous description
        skip_prev_loss = is_corr and corr_type in ("no_loss", "unsure", "new_amount")
        self._ground(data, message if skip_prev_loss else user_text, previous)
        if previous:
            self._keep_previous_facts(data, previous, skip_loss=skip_prev_loss)

        conflict = loss_conflict(message, previous)

        if is_corr:
            if corr_type in ("no_loss", "unsure"):
                data.financial_loss = None
                data.amount = None
                data.currency = None
                data.field_sources["financial_loss"] = FactSource.unknown
                data.field_sources.pop("amount", None)
                conflict = True
            elif corr_type == "new_amount":
                data.amount = new_ids.amount
                data.currency = new_ids.currency or (previous.currency if previous else "INR")
                data.financial_loss = True
                data.field_sources["amount"] = FactSource.user_provided
                data.field_sources["financial_loss"] = FactSource.user_provided
                conflict = False
            elif corr_type == "loss_confirmed":
                data.financial_loss = True
                if new_ids.amount is not None:
                    data.amount = new_ids.amount
                    data.currency = new_ids.currency or "INR"
                    data.field_sources["amount"] = FactSource.user_provided
                data.field_sources["financial_loss"] = FactSource.user_provided
                conflict = False
        else:
            settled = previous is not None and previous.financial_loss is not None
            if data.financial_loss is not None and only_unsure and data.amount is None and not settled:
                conflict = False
                data.financial_loss = None
                data.field_sources["financial_loss"] = FactSource.unknown
            elif not conflict and not settled and not only_unsure:
                if denied_loss and not asserted_loss and data.amount is None:
                    data.financial_loss = False
                    data.field_sources["financial_loss"] = FactSource.user_provided
                elif asserted_loss and not denied_loss and data.financial_loss is None:
                    data.financial_loss = True
                    data.field_sources["financial_loss"] = FactSource.user_provided

            if conflict:
                data.financial_loss = None
                data.amount = None
                data.currency = None
                data.field_sources["financial_loss"] = FactSource.unknown
                data.field_sources.pop("amount", None)

        # Distinguish user correction in incident description while preserving conversation history
        if previous:
            if is_corr:
                data.description = f"{previous.description}\n[User correction]: {message}".strip()
            elif message not in data.description:
                data.description = f"{previous.description}\n{message}".strip()

        if is_corr:
            setattr(data, "_loss_correction", corr_type)

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
        if data.financial_loss is not False:
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
        else:
            data.amount, data.currency = None, None
            data.field_sources.pop("amount", None)
        if not data.description:
            data.description = text.strip()

    @staticmethod
    def _keep_previous_facts(data: IncidentData, previous: IncidentData, skip_loss: bool = False) -> None:
        for name in IncidentData.model_fields:
            if name in ("missing_information", "confidence", "description", "field_sources"):
                continue
            if skip_loss and name in ("amount", "currency", "financial_loss"):
                continue
            old, new = getattr(previous, name), getattr(data, name)
            if isinstance(old, list):
                setattr(data, name, old + [x for x in new if x not in old])
            elif new in (None, "") and old not in (None, ""):
                setattr(data, name, old)
        for k, v in previous.field_sources.items():
            if skip_loss and k in ("amount", "currency", "financial_loss"):
                continue
            data.field_sources.setdefault(k, v)
