"""Explanation + response guidance + evidence checklist, grounded in retrieved sources."""
import logging
import re

from app.schemas.classification import ClassificationResult
from app.schemas.guidance import Citation, EvidenceChecklistItem, Explanation, Guidance
from app.schemas.incident import IncidentData
from app.services.ai.base import LLMProvider, StructuredOutputError
from app.services.ai.prompts import explanation as explanation_prompt
from app.services.ai.prompts import guidance as guidance_prompt
from app.services.ai.structured import generate_structured
from app.services.classification.taxonomy import CATEGORY_BY_ID
from app.services.guidance.safety import drop_evidence_destruction
from app.services.incident.extractor import remote_installed, sensitive_shared
from app.services.language import current_language

log = logging.getLogger(__name__)

_URL = re.compile(r"https?://[^\s)\]]+|\bwww\.[^\s)\]]+|(?<![@\w.-])(?:[a-z0-9-]+\.)+(?:com|net|org|in|co|info|xyz|app|io)\b", re.I)
_NUMBER = re.compile(r"(?<![\d,.])(?:\+?\d[\d\s-]{1,}\d)(?!\d|[,.]\d)")

# Recognized official emergency and national helpline shortcodes in India,
# backed by authoritative statutory notifications (MHA, MoHFW, MWCD, TRAI, DoCA, etc.)
RECOGNIZED_HELPLINES = {
    "100",   # Police (MHA / State Police)
    "101",   # Fire Services (MHA Fire Directorate)
    "102",   # Ambulance (MoHFW)
    "108",   # Emergency Medical & Disaster Response (MoHFW / EMRI)
    "112",   # National Emergency Response Support System (ERSS - MHA)
    "139",   # Railway Customer Care & Security (Ministry of Railways)
    "181",   # Women Helpline (Ministry of Women and Child Development)
    "1091",  # Women in Distress Helpline (MHA / Police)
    "1098",  # Childline (Ministry of Women and Child Development)
    "14567", # Elderline - Senior Citizens National Helpline (MoSJE)
    "1909",  # Telecom DND / Commercial Communications Helpline (TRAI / DoT)
    "1915",  # National Consumer Helpline (Department of Consumer Affairs)
    "1930",  # National Cyber Crime Helpline (Citizen Financial Cyber Fraud - I4C / MHA)
}

BASELINE_EVIDENCE = [
    EvidenceChecklistItem(item="Screenshots of all messages/chats showing date, time and sender", why="Shows how the incident happened", priority="high"),
    EvidenceChecklistItem(item="The original messages, emails and call logs (do not delete them)", why="Originals carry metadata investigators may need", priority="high"),
    EvidenceChecklistItem(item="A written timeline of events while it is fresh", why="Helps you give a consistent, complete complaint", priority="medium"),
]

BASELINE_EVIDENCE_HI = [
    EvidenceChecklistItem(item="दिनांक, समय और प्रेषक दिखाने वाले सभी संदेशों/चैट के स्क्रीनशॉट", why="दिखाता है कि घटना कैसे हुई", priority="high"),
    EvidenceChecklistItem(item="मूल संदेश, ईमेल और कॉल लॉग (उन्हें डिलीट न करें)", why="मूल संदेशों में तकनीकी मेटाडेटा होता है जिसकी जाँचकर्ताओं को आवश्यकता हो सकती है", priority="high"),
    EvidenceChecklistItem(item="घटनाक्रम की लिखित समय-सीमा (टाइमलाइन) जब तक यह ताज़ा है", why="सुसंगत और पूरी शिकायत दर्ज कराने में मदद करता है", priority="medium"),
]

URGENT_BANK_ACTION = ("Contact your bank or payment app now, using only the number printed on your card, passbook or "
                      "the bank's official app, and ask them to block further transactions.")
URGENT_BANK_ACTION_HI = ("अपने बैंक या पेमेंट ऐप से तुरंत संपर्क करें, केवल अपने कार्ड, पासबुक या "
                         "बैंक के आधिकारिक ऐप पर दिए गए नंबर का उपयोग करें, और आगे के लेन-देन रोकने के लिए कहें।")

NO_SOURCE_REPORTING = ("Report through the official national cybercrime reporting channel or your local police station. "
                       "No trusted source with contact details was found for this situation, so none are given here.")
NO_SOURCE_REPORTING_HI = ("राष्ट्रीय साइबर अपराध रिपोर्टिंग चैनल या अपने स्थानीय पुलिस स्टेशन के माध्यम से रिपोर्ट करें। "
                          "इस स्थिति के लिए संपर्क विवरण वाला कोई विश्वसनीय स्रोत नहीं मिला, इसलिए यहाँ कोई विवरण नहीं दिया गया है।")


def _source_text(sources: list[Citation]) -> str:
    return " ".join(f"{s.url} {s.excerpt}" for s in sources).lower()


def _host(url: str) -> str:
    return re.sub(r"^https?://(www\.)?|^www\.", "", url.lower().rstrip(".,")).split("/")[0].split("?")[0]


def known_text(incident: IncidentData) -> str:
    """Details the user gave us; repeating them back is not an invention."""
    parts = [incident.description or ""]
    parts += incident.phone_numbers + incident.emails + incident.urls + incident.upi_ids + incident.account_identifiers
    parts += [incident.date_time or ""] + ([str(int(incident.amount))] if incident.amount is not None else [])
    return " ".join(parts)


def scrub_unsupported(text: str, sources: list[Citation], known: str = "") -> str:
    """Replace URLs and phone/helpline numbers that appear neither in retrieved sources nor in `known`.

    Distinguishes official verified contacts from unverified user-provided contacts:
    - Official sources and recognized statutory helplines are trusted contacts.
    - User-provided numbers are preserved as evidence, references, or in warnings/blocking,
      but NEVER trusted as verified helplines in affirmative contact advice.
    - Unsupported contact numbers generated by the model are scrubbed or replaced with safe guidance.
    """
    source_text = _source_text(sources)
    source_hosts = {_host(m.group(0)) for m in _URL.finditer(source_text)} | {_host(s.url) for s in sources if s.url}
    source_nums = {re.sub(r"\D", "", m.group(0)) for m in _NUMBER.finditer(source_text)} | set(re.findall(r"\d+", source_text))

    user_text = known.lower()
    user_hosts = {_host(m.group(0)) for m in _URL.finditer(user_text)}
    user_nums = {re.sub(r"\D", "", m.group(0)) for m in _NUMBER.finditer(user_text)} | set(re.findall(r"\d+", user_text))

    allowed_hosts = source_hosts | user_hosts

    def url_ok(m: re.Match[str]) -> str:
        return m.group(0) if _host(m.group(0)) in allowed_hosts else "[official website — verify]"

    def num_ok(m: re.Match[str]) -> str:
        raw = m.group(0)
        digits = re.sub(r"\D", "", raw)
        if len(digits) < 3 or len(digits) > 13:  # 3-6: helpline short codes; 7-13: phone / toll-free numbers
            return raw

        # Recognized statutory emergency and national helplines supported by official government bodies
        if digits in RECOGNIZED_HELPLINES:
            return raw

        # Ordinary 4-digit calendar years (e.g. 2026, 2024, 2000) that are not helplines
        if len(digits) == 4 and digits.isdigit():
            val = int(digits)
            if 1900 <= val <= 2099:
                return raw

        # Context surrounding match to preserve amounts, durations, sections, steps
        start, end = m.start(), m.end()
        preceding = text[max(0, start - 50):start].lower()
        following = text[end:min(len(text), end + 50)].lower()

        # Currency / amounts: e.g. Rs 500, ₹1000, 500 rupees, $250
        if re.search(r"(?:₹|rs\.?|inr|\$|usd|eur)\s*$", preceding) or re.search(r"^\s*(?:rupees?|rs\.?|inr|cents?|bucks?)\b", following):
            return raw

        # Legal sections / acts: e.g. Section 420, Act 2000, Section 66D
        if re.search(r"\b(?:section|sec\.?|u/s|under section|article|act)\s*$", preceding):
            return raw

        # Durations / time: e.g. within 180 days, 365 days, 24 hours
        if re.search(r"^\s*(?:days?|hours?|hrs?|minutes?|mins?|seconds?|secs?|weeks?|months?|years?)\b", following):
            return raw

        # Counters / steps / codes: e.g. step 101, page 200
        if re.search(r"\b(?:step|option|item|rule|clause|point|page|code)\s*$", preceding):
            return raw

        # Source-backed numbers from authoritative retrieved citations
        if digits in source_nums or (len(digits) > 10 and digits[-10:] in source_nums):
            return raw

        is_negated = bool(re.search(r"\b(?:do not|don'?t|never|avoid|beware of|block|report)\b", preceding))
        is_evidence = bool(re.search(
            r"\b(?:suspect|scammer|fraudster|caller|attacker|impersonator|phisher|sender|received|from|mentioned|reference|ref\.?|utr|txn|transaction|id|account|evidence|log|checklist|screenshot|sms|whatsapp|message|chat|told|asked|instructed|directed|claimed)\b",
            preceding
        ) or re.search(r"\b(?:and call log|call log|sms|chat)\b", following))

        # Check if the number is being recommended as an affirmative contact instruction to call/reach
        is_contact_call = bool(
            re.search(r"\b(?:call|dial|contact|phone|reach|reach out to|helpline|support|customer care)\b", preceding)
            or re.search(r"^\s*(?:helpline|number|toll[\s-]?free)\b", following)
        ) and not is_negated and not is_evidence

        # User-provided number: allow it as long as it is NOT recommended as a contact number to call.
        # User numbers can be evidence, references, amounts, or in warnings, but NEVER trusted as helplines to call.
        is_user_num = (digits in user_nums) or (len(digits) > 10 and digits[-10:] in user_nums)
        if is_user_num and not is_contact_call:
            return raw

        # In affirmative contact advice or for unsupported model inventions, replace with safe guidance
        is_banking = bool(
            re.search(r"\b(?:bank|card|account|passbook|atm|transaction|txn|transfer|upi|payment|financial|money|reverse)\b", text, re.I)
            or re.search(r"\b(?:bank|card|account|passbook|atm|transaction|txn|transfer|upi|payment|financial|money|reverse)\b", known, re.I)
        )
        if is_banking:
            return "the official number on your bank card, passbook or mobile app [official helpline — verify]"
        return "[official helpline — verify]"

    return _NUMBER.sub(num_ok, _URL.sub(url_ok, text))


def verification_notes(guidance: Guidance, sources: list[Citation], scrubbed: bool) -> list[str]:
    """Plain statements about how far the guidance can be trusted; computed, never generated."""
    notes: list[str] = []
    if not sources:
        notes.append("No trusted source was found for this situation. The steps above are general safety advice only; "
                      "no helpline numbers or links are given.")
        return notes
    if not guidance.source_ids:
        notes.append("The AI did not tie these steps to a specific trusted source. Treat them as general advice.")
    used = [s for s in sources if s.id in guidance.source_ids] or sources
    stale = [s for s in used if s.freshness == "stale"]
    unverified = [s for s in used if s.freshness == "unverified"]
    if stale:
        notes.append("Some sources were last verified a while ago (" + ", ".join(f"{s.id}: {s.last_verified}" for s in stale)
                     + "). Helplines and procedures change; confirm them on the official website.")
    if unverified:
        notes.append("Sources " + ", ".join(s.id for s in unverified) + " have no recorded verification date. Confirm any "
                     "helpline number, website or procedure on the organisation's official website before relying on it.")
    if scrubbed:
        notes.append("Some contact details suggested by the AI were removed because they did not appear in a trusted source.")
    return notes


class Advisor:
    def __init__(self, llm: LLMProvider) -> None:
        self.llm = llm

    def explain(self, incident: IncidentData, cls: ClassificationResult, sources: list[Citation]) -> Explanation:
        try:
            exp = generate_structured(
                self.llm, task=explanation_prompt.TASK, system=explanation_prompt.SYSTEM,
                user=explanation_prompt.build(incident, cls, sources), schema=Explanation,
            )
        except StructuredOutputError:
            cat = CATEGORY_BY_ID.get(cls.category, CATEGORY_BY_ID["unknown"])
            return Explanation(summary=incident.description[:300], why_this_type=cls.reasoning,
                               simple_explanation=f"{cat.label}: {cat.definition}")
        valid_ids = {s.id for s in sources}
        exp.source_ids = [i for i in exp.source_ids if i in valid_ids]
        known = known_text(incident)
        # The summary often echoes the user's text, which can carry a scammer's "official" number.
        for field in ("summary", "why_this_type", "simple_explanation"):
            setattr(exp, field, scrub_unsupported(getattr(exp, field), sources, known))
        exp.warning_signs = [scrub_unsupported(w, sources, known) for w in exp.warning_signs]
        return exp

    def guide(self, incident: IncidentData, cls: ClassificationResult, sources: list[Citation]) -> Guidance:
        try:
            g = generate_structured(
                self.llm, task=guidance_prompt.TASK, system=guidance_prompt.SYSTEM,
                user=guidance_prompt.build(incident, cls, sources), schema=Guidance,
            )
        except StructuredOutputError:
            log.warning("guidance generation failed; using minimal safe guidance")
            g = Guidance(
                immediate_actions=["If money or banking details are involved, contact your bank immediately through its official channels."],
                reporting_guidance=["Report the incident through the official national cybercrime reporting channel or your local police station."],
            )
        known, scrubbed = known_text(incident), False
        for field in ("immediate_actions", "security_steps", "reporting_guidance", "do_not"):
            raw = getattr(g, field)
            items = [scrub_unsupported(x, sources, known) for x in raw]
            scrubbed = scrubbed or items != raw
            setattr(g, field, items if field == "do_not" else drop_evidence_destruction(items))
        is_hi = current_language() == "hi"
        if not sources:
            g.reporting_guidance = [NO_SOURCE_REPORTING_HI if is_hi else NO_SOURCE_REPORTING]
        # Urgent, low-risk step that must never be missing once money or credentials may be at risk.
        at_risk = incident.financial_loss or sensitive_shared(incident.description) or remote_installed(incident.description)
        if at_risk:
            if is_hi:
                if not any(re.search(r"(बैंक|पेमेंट ऐप|भुगतान ऐप)", a) for a in g.immediate_actions):
                    g.immediate_actions.insert(0, URGENT_BANK_ACTION_HI)
            else:
                if not any(re.search(r"\b(bank|payment app)\b", a, re.I) for a in g.immediate_actions):
                    g.immediate_actions.insert(0, URGENT_BANK_ACTION)
        if remote_installed(incident.description) and not any(re.search(r"\b(uninstall|remove|hata|अनइंस्टॉल|हटा|delete)\b", a, re.I) for a in g.immediate_actions):
            remote_action = (
                "संदिग्ध रिमोट-एक्सेस ऐप (जैसे AnyDesk, TeamViewer) को तुरंत अनइंस्टॉल करें और किसी सुरक्षित डिवाइस से अपने पासवर्ड बदलें।"
                if is_hi
                else "Uninstall the suspicious remote-access app (e.g. AnyDesk, TeamViewer) immediately and change your passwords from a secure device."
            )
            g.immediate_actions.append(remote_action)
        g.source_ids = [i for i in g.source_ids if i in {s.id for s in sources}]
        g.evidence_status = "insufficient" if not sources else ("supported" if g.source_ids else "limited")
        g.verification_notes = verification_notes(g, sources, scrubbed)
        g.evidence_checklist = self.evidence_checklist(incident, g.evidence_checklist, sources, known)
        return g

    @staticmethod
    def evidence_checklist(
        incident: IncidentData,
        generated: list[EvidenceChecklistItem],
        sources: list[Citation] | None = None,
        known: str = "",
    ) -> list[EvidenceChecklistItem]:
        """Merge model items with a deterministic baseline and identifier-driven items, scrubbing unsupported details."""
        srcs = sources or []
        from app.services.guidance.safety import destroys_evidence

        items: list[EvidenceChecklistItem] = []
        for it in generated:
            if destroys_evidence(it.item) or destroys_evidence(it.why):
                continue
            scrubbed_item = scrub_unsupported(it.item, srcs, known)
            scrubbed_why = scrub_unsupported(it.why, srcs, known)
            items.append(it.model_copy(update={"item": scrubbed_item, "why": scrubbed_why}))

        is_hi = current_language() == "hi"
        extra = list(BASELINE_EVIDENCE_HI if is_hi else BASELINE_EVIDENCE)
        if incident.financial_loss or incident.upi_ids or incident.account_identifiers:
            extra.append(
                EvidenceChecklistItem(
                    item="बैंक/यूपीआई विवरण और प्रत्येक भुगतान के लिए ट्रांजैक्शन आईडी (UTR)" if is_hi else "Bank/UPI statement and transaction ID (UTR) for each payment",
                    why="धनराशि को ट्रैक करने और रोकने के लिए आवश्यक" if is_hi else "Needed to trace and hold funds",
                    priority="high",
                )
            )
        if incident.urls:
            extra.append(
                EvidenceChecklistItem(
                    item=f"संदिग्ध लिंक: {', '.join(incident.urls[:3])} (कॉपी करें, खोलें नहीं)" if is_hi else f"The suspicious link(s): {', '.join(incident.urls[:3])} (copy, don't open)",
                    why="साइट की जाँच और उसे बंद कराने में मदद करता है" if is_hi else "Allows the site to be investigated",
                    priority="high",
                )
            )
        if incident.phone_numbers:
            extra.append(
                EvidenceChecklistItem(
                    item=f"संदिग्ध फोन नंबर: {', '.join(incident.phone_numbers[:3])} और कॉल लॉग" if is_hi else f"Suspect phone number(s): {', '.join(incident.phone_numbers[:3])} and call log",
                    why="संदिग्ध की पहचान करता है" if is_hi else "Identifies the suspect",
                    priority="high",
                )
            )
        if incident.upi_ids:
            extra.append(
                EvidenceChecklistItem(
                    item=f"संदिग्ध यूपीआई आईडी: {', '.join(incident.upi_ids[:3])}" if is_hi else f"Suspect UPI ID(s): {', '.join(incident.upi_ids[:3])}",
                    why="प्राप्तकर्ता खाते की पहचान करता है" if is_hi else "Identifies the receiving account",
                    priority="high",
                )
            )
        existing = " ".join(i.item.lower() for i in items)
        for e in extra:
            key = e.item.lower().split()[0:3]
            if " ".join(key) not in existing:
                items.append(e)
        have = " ".join(incident.evidence_available).lower()
        for it in items:
            words = [w for w in re.findall(r"[a-z]{5,}", it.item.lower())]
            it.already_available = it.already_available or any(w in have for w in words)
        order = {"high": 0, "medium": 1, "low": 2}
        return sorted(items, key=lambda i: order[i.priority])[:10]
