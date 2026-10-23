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
from app.services.incident.extractor import sensitive_shared

log = logging.getLogger(__name__)

_URL = re.compile(r"https?://[^\s)\]]+|\bwww\.[^\s)\]]+|(?<![@\w.-])(?:[a-z0-9-]+\.)+(?:com|net|org|in|co|info|xyz|app|io)\b", re.I)
_NUMBER = re.compile(r"(?<![\d,.])(?:\+?\d[\d\s-]{2,}\d)(?!\d|[,.]\d)")

BASELINE_EVIDENCE = [
    EvidenceChecklistItem(item="Screenshots of all messages/chats showing date, time and sender", why="Shows how the incident happened", priority="high"),
    EvidenceChecklistItem(item="The original messages, emails and call logs (do not delete them)", why="Originals carry metadata investigators may need", priority="high"),
    EvidenceChecklistItem(item="A written timeline of events while it is fresh", why="Helps you give a consistent, complete complaint", priority="medium"),
]

URGENT_BANK_ACTION = ("Contact your bank or payment app now, using only the number printed on your card, passbook or "
                      "the bank's official app, and ask them to block further transactions.")
NO_SOURCE_REPORTING = ("Report through the official national cybercrime reporting channel or your local police station. "
                       "No trusted source with contact details was found for this situation, so none are given here.")


def _source_text(sources: list[Citation]) -> str:
    return " ".join(f"{s.url} {s.excerpt}" for s in sources).lower()


def _host(url: str) -> str:
    return re.sub(r"^https?://(www\.)?|^www\.", "", url.lower().rstrip(".,")).split("/")[0].split("?")[0]


def known_text(incident: IncidentData) -> str:
    """Details the user gave us; repeating them back is not an invention."""
    parts = incident.phone_numbers + incident.emails + incident.urls + incident.upi_ids + incident.account_identifiers
    parts += [incident.date_time or ""] + ([str(int(incident.amount))] if incident.amount is not None else [])
    return " ".join(parts)


def scrub_unsupported(text: str, sources: list[Citation], known: str = "") -> str:
    """Replace URLs and phone/helpline-style numbers that appear neither in retrieved sources nor in `known`."""
    allowed = f"{_source_text(sources)} {known.lower()}"
    hosts = {_host(m.group(0)) for m in _URL.finditer(allowed)} | {_host(s.url) for s in sources if s.url}
    nums = {re.sub(r"\D", "", m.group(0)) for m in _NUMBER.finditer(allowed)} | set(re.findall(r"\d+", allowed))

    def url_ok(m: re.Match[str]) -> str:
        return m.group(0) if _host(m.group(0)) in hosts else "[official website — verify]"

    def num_ok(m: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", m.group(0))
        if len(digits) < 3 or len(digits) > 13:  # 3-6: helpline short codes; 7-13: phone / toll-free numbers
            return m.group(0)
        if digits in nums or (len(digits) > 10 and digits[-10:] in nums):  # same number with a +91 / 0 prefix
            return m.group(0)
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
        if not sources:
            g.reporting_guidance = [NO_SOURCE_REPORTING]
        # Urgent, low-risk step that must never be missing once money or credentials may be at risk.
        at_risk = incident.financial_loss or sensitive_shared(incident.description)
        if at_risk and not any(re.search(r"\b(bank|payment app)\b", a, re.I) for a in g.immediate_actions):
            g.immediate_actions.insert(0, URGENT_BANK_ACTION)
        g.source_ids = [i for i in g.source_ids if i in {s.id for s in sources}]
        g.evidence_status = "insufficient" if not sources else ("supported" if g.source_ids else "limited")
        g.verification_notes = verification_notes(g, sources, scrubbed)
        g.evidence_checklist = self.evidence_checklist(incident, g.evidence_checklist)
        return g

    @staticmethod
    def evidence_checklist(incident: IncidentData, generated: list[EvidenceChecklistItem]) -> list[EvidenceChecklistItem]:
        """Merge model items with a deterministic baseline and identifier-driven items."""
        items = list(generated)
        extra = list(BASELINE_EVIDENCE)
        if incident.financial_loss or incident.upi_ids or incident.account_identifiers:
            extra.append(EvidenceChecklistItem(item="Bank/UPI statement and transaction ID (UTR) for each payment", why="Needed to trace and hold funds", priority="high"))
        if incident.urls:
            extra.append(EvidenceChecklistItem(item=f"The suspicious link(s): {', '.join(incident.urls[:3])} (copy, don't open)", why="Allows the site to be investigated", priority="high"))
        if incident.phone_numbers:
            extra.append(EvidenceChecklistItem(item=f"Suspect phone number(s): {', '.join(incident.phone_numbers[:3])} and call log", why="Identifies the suspect", priority="high"))
        if incident.upi_ids:
            extra.append(EvidenceChecklistItem(item=f"Suspect UPI ID(s): {', '.join(incident.upi_ids[:3])}", why="Identifies the receiving account", priority="high"))
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
