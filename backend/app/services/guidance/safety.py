"""Deterministic output guards applied after generation (the prompt alone is not enough)."""
import re

# Advice that would destroy evidence. Negated forms ("do not delete", "never wipe") are allowed.
_DESTROY = re.compile(
    r"\b("
    r"delete|deleting|deleted|"
    r"erase|erasing|erased|"
    r"wipe|wiping|wiped|"
    r"factory[- ]reset|"
    r"format|formatting|formatted|"
    r"(?:clear|clearing|remove|removing|discard|discarding|purge|purging|trash|trashing)"
    r"(?:\s+\w+){0,4}\s+"
    r"(?:chats?|history|messages?|conversations?|call\s*logs?|screenshots?|evidence|records?|data|sms|files?|transactions?|logs?)"
    r")\b",
    re.I,
)
_NEGATED = re.compile(r"\b(do not|don't|dont|never|avoid|without|instead of|not|prevent)\b[^.;!?\n]{0,50}$", re.I)

# Hindi "delete/remove/erase" verbs; the negation ("मत", "न करें", "नहीं", "mat", "nahi") may come before or after the verb.
_DESTROY_HI = re.compile(
    r"(डिलीट|मिटा|फ़ॉर्मेट|फॉर्मेट|फैक्ट्री\s*रीसेट|"
    r"(?:चैट|मैसेज|संदेश|सबूत|स्क्रीनशॉट|कॉल\s*लॉग|कॉल\s*रिकॉर्ड|रिकॉर्ड|डेटा|फ़ाइल)[^।!?\n]{0,30}(?:हटा|साफ़|साफ|मिटा|नष्ट)|"
    r"(?:हटा|साफ़|साफ|मिटा|नष्ट)[^।!?\n]{0,30}(?:चैट|मैसेज|संदेश|सबूत|स्क्रीनशॉट|कॉल\s*लॉग|कॉल\s*रिकॉर्ड|रिकॉर्ड|डेटा|फ़ाइल)|"
    r"(?:chat|message|saboot|evidence|screenshot|call\s*log)[^.;!?।\n]{0,30}(?:mita|saaf|hata|nasht)|"
    r"(?:mita|saaf|hata|nasht)[^.;!?।\n]{0,30}(?:chat|message|saboot|evidence|screenshot|call\s*log))",
    re.I,
)
_NEGATED_HI = re.compile(r"(मत|नहीं|न\s*कर|कभी\s*न|बिना|बजाय|\b(?:mat|nahi|nahin|kabhi\s*nahi|bina)\b)", re.I)


def destroys_evidence(text: str) -> bool:
    for clause in re.split(r"[।.;!?\n]", text):
        clause_str = clause.strip()
        if not clause_str:
            continue
        if _DESTROY_HI.search(clause_str) and not _NEGATED_HI.search(clause_str):
            return True
        for m in _DESTROY.finditer(clause_str):
            preceding = clause_str[: m.start()]
            if not _NEGATED.search(preceding) and not _NEGATED_HI.search(clause_str):
                return True
    return False


def drop_evidence_destruction(items: list[str]) -> list[str]:
    return [i for i in items if not destroys_evidence(i)]


def scrub_reply(text: str) -> str:
    """Remove sentences/lines in free-text replies that advise destroying evidence."""
    kept = []
    for line in text.split("\n"):
        sentences = re.split(r"(?<=[.!?।])\s+", line)
        kept.append(" ".join(s for s in sentences if not destroys_evidence(s)))
    return "\n".join(kept)
