"""Deterministic post-generation validation of the complaint narrative.

The narrative is compared sentence by sentence with the validated incident record and the user's own
words (`incident.description` holds the user's messages verbatim). Claims the record does not support are
removed or replaced by placeholders. The prompt asks the model not to make such claims; this module
enforces it.
"""
import re

from app.schemas.incident import IncidentData
from app.services.incident.identifiers import AMOUNT_RE, extract_identifiers

_WB = r"(?<![a-zA-Z0-9\u0901-\u0963\u0966-\u096f\u0971-\u097f])"
_WE = r"(?![a-zA-Z0-9\u0901-\u0963\u0966-\u096f\u0971-\u097f])"
_NEG = re.compile(rf"{_WB}(?:not|no|never|none|nothing|nahi|nahin|mat|bina|नहीं|नही|ना|न|बिना){_WE}|n't\b", re.I)

# Statements about what the complainant did / did not do after the incident (first-person or third-person).
_SUBJECT = r"(?:the complainant|complainant|the victim|victim|the user|user|i|we|he|she|they)"
_ACTION_VERBS = (
    r"(?:reported|report|informing|informed|inform|contacted|contact|called|call|"
    r"approached|approach|filed|file|lodged|lodge|complained|complain|notified|notify|"
    r"visited|visit|blocked|block|froze|frozen|freeze|changed|change|taken|take|done|do)"
)
_ACTION_CLAIM = re.compile(
    rf"\b(no (further |other |additional )?(action|steps?|complaint)s?"
    rf"|(not|n't|never) (yet )?{_ACTION_VERBS}"
    rf"|(have|has|had) (already |also |immediately |promptly |then )?{_ACTION_VERBS}"
    rf"|{_SUBJECT} (already |also |immediately |promptly |then |subsequently |later |thereafter )?(?:has |have |had )?{_ACTION_VERBS})\b",
    re.I,
)
# Statements about money lost / not lost.
_LOSS_NEG = re.compile(
    r"\b(no (financial |monetary )?loss|no money|(not|n't|never) (lost|lose|been debited|been deducted|suffered)|lost no)\b", re.I)
_LOSS_POS = re.compile(r"\b(lost|debited|deducted|siphoned|withdrawn|transferred|paid)\b", re.I)
# Statements about what the complainant shared / entered / clicked.
_DISCLOSURE = re.compile(r"\b(share|shared|sharing|enter|entered|click|clicked|provide|provided|disclose|disclosed|gave|give|install|installed)\b", re.I)
_DISCLOSURE_EQUIVALENTS = {
    "share": re.compile(r"(?:shar|prov|gave|give|disc|साझा|शेयर|दिया|दिए|दी|दे\s*दिया|बताया|बताए|बता\s*दिया|डाला|डाले|दर्ज|diya|diye|de\s*diya|bheja|bataya|daal)", re.I),
    "click": re.compile(r"(?:clic|open|क्लिक|खोला|खोले|khol)", re.I),
    "install": re.compile(r"(?:inst|down|anydesk|teamviewer|quick\s*support|rustdesk|इंस्टॉल|डाउनलोड|karwaya)", re.I),
    "enter": re.compile(r"(?:ente|type|fill|दर्ज|डाला|भरा|daal|bhara)", re.I),
}
_DATE = re.compile(
    r"\b(\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}|\d{1,2}(st|nd|rd|th)? (of )?(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?( \d{4})?"
    r"|(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]* \d{1,2}(st|nd|rd|th)?(,? \d{4})?"
    r"|(monday|tuesday|wednesday|thursday|friday|saturday|sunday))\b", re.I)
_TIME = re.compile(r"\b\d{1,2}([:.]\d{2})? ?(am|pm|a\.m\.|p\.m\.|hrs|hours)\b", re.I)


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]


def _user_mentions(user_text: str, verb_match: str, negated: bool) -> bool:
    """True if the user said something with matching verb semantics and polarity in English, Hindi, or Hinglish."""
    v = verb_match.lower()
    cat = "share" if any(k in v for k in ("share", "provid", "give", "gave", "disclos")) else (
        "click" if "click" in v else (
            "install" if "install" in v else (
                "enter" if "enter" in v else None
            )
        )
    )
    pat = _DISCLOSURE_EQUIVALENTS.get(cat) if cat else re.compile(re.escape(v[:4]), re.I)

    for sent in re.split(r"(?<=[.!?।\n])\s*", user_text):
        if pat.search(sent):
            has_neg = bool(_NEG.search(sent))
            if has_neg == negated:
                return True
    return False


def validate_narrative(narrative: str, incident: IncidentData) -> tuple[str, list[str]]:
    """Return (validated narrative, notes describing what was removed/replaced)."""
    user_text = incident.description or ""
    known_when = f"{incident.date_time or ''} {user_text}".lower()
    actions = " ".join(incident.actions_taken).lower()
    notes: list[str] = []
    kept: list[str] = []

    for sent in _sentences(narrative):
        negated = bool(_NEG.search(sent))

        if _ACTION_CLAIM.search(sent):
            words = {w for w in re.findall(r"[a-z]{5,}", sent.lower())}
            supported = not negated and actions and any(w in actions for w in words)
            if not supported:
                notes.append(f"Removed unsupported claim about actions taken: {sent!r}")
                continue

        if _LOSS_NEG.search(sent) and incident.financial_loss is not False:
            notes.append(f"Removed unsupported claim that no money was lost: {sent!r}")
            continue
        if incident.financial_loss is False and _LOSS_POS.search(sent) and not negated:
            notes.append(f"Removed claim of financial loss because user denied financial loss: {sent!r}")
            continue
        if (_LOSS_POS.search(sent) and not negated and incident.financial_loss is not True
                and incident.amount is None and not re.search(r"\[[A-Z]", sent)):
            notes.append(f"Removed unsupported claim of financial loss: {sent!r}")
            continue

        m = _DISCLOSURE.search(sent)
        if m and re.search(r"\b(i|we|me|my|the complainant|complainant|the victim|victim|the user|user|he|she|they)\b", sent, re.I) and not _user_mentions(user_text, m.group(0), negated):
            notes.append(f"Removed claim about what the complainant did that the user did not state: {sent!r}")
            continue

        def amount_ok(a: re.Match[str]) -> str:
            value = extract_identifiers(a.group(0)).amount
            if value is None or (incident.amount is not None and abs(value - incident.amount) < 0.5):
                return a.group(0)
            notes.append(f"Replaced unsupported amount {a.group(0)!r}")
            return "[AMOUNT]"

        sent = AMOUNT_RE.sub(amount_ok, sent)

        def date_ok(d: re.Match[str]) -> str:
            if d.group(0).lower() in known_when:
                return d.group(0)
            notes.append(f"Replaced unsupported date {d.group(0)!r}")
            return "[DATE]"

        def time_ok(t: re.Match[str]) -> str:
            if t.group(0).lower().replace(" ", "") in known_when.replace(" ", ""):
                return t.group(0)
            notes.append(f"Replaced unsupported time {t.group(0)!r}")
            return "[TIME]"

        sent = _TIME.sub(time_ok, _DATE.sub(date_ok, sent))
        kept.append(sent)

    text = " ".join(kept).strip() or "[DESCRIBE THE INCIDENT IN DETAIL]"
    return text, notes
