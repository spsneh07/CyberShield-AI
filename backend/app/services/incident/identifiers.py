"""Deterministic extraction of digital identifiers and amounts from user text."""
import re
from dataclasses import dataclass, field

URL_RE = re.compile(
    r"\b(?:https?://|www\.)[^\s<>\"')\]]+|\b(?:[a-z0-9-]+\.)+(?:com|in|net|org|co|xyz|info|top|online|site|ly|me|io|app|link|club|shop)(?:/[^\s<>\"')\]]*)?",
    re.IGNORECASE,
)
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b")
# UPI VPA: handle@psp with no dot in the PSP part (distinguishes it from email).
UPI_RE = re.compile(r"\b[A-Za-z0-9._-]{2,256}@[A-Za-z]{2,64}\b(?!\.[A-Za-z])")
PHONE_RE = re.compile(r"(?<![\d\w])(?:\+91[\s-]?|0)?[6-9]\d{4}[\s-]?\d{5}(?!\d)|(?<![\d\w])\+\d{1,3}[\s-]?\d{6,12}(?!\d)")
ACCOUNT_RE = re.compile(
    r"\b(?:a/?c|account|acct|txn|transaction|utr|ref(?:erence)?|order)\s*(?:no\.?|number|id|#)?\s*[:\-]?\s*([A-Za-z0-9]{6,22})\b",
    re.IGNORECASE,
)
AMOUNT_RE = re.compile(
    r"(?:₹|rs\.?|inr|rupees?|रुपये|रुपए|रु\.?|rupaye?|rupaiye?|paisa|paise)\s*(\d+(?:,\d+)*(?:\.\d+)?)\s*(lakh|lakhs|crore|k|hazar|hazaar|लाख|करोड़|हजार|हज़ार)?|"
    r"(\d+(?:,\d+)*(?:\.\d+)?)\s*(lakh|lakhs|crore|k|hazar|hazaar|लाख|करोड़|हजार|हज़ार)?\s*(?:₹|rs\.?|inr|rupees?|रुपये|रुपए|रु\.?|rupaye?|rupaiye?|paisa|paise)\b|"
    r"\$\s*(\d+(?:,\d+)*(?:\.\d+)?)",
    re.IGNORECASE,
)
_MULT = {
    "lakh": 1e5, "lakhs": 1e5, "crore": 1e7, "k": 1e3, "लाख": 1e5, "करोड़": 1e7,
    "hazar": 1e3, "hazaar": 1e3, "हजार": 1e3, "हज़ार": 1e3,
}


@dataclass
class Identifiers:
    phone_numbers: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    urls: list[str] = field(default_factory=list)
    upi_ids: list[str] = field(default_factory=list)
    account_identifiers: list[str] = field(default_factory=list)
    amount: float | None = None
    currency: str | None = None


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out = []
    for i in items:
        key = normalize(i)
        if key not in seen:
            seen.add(key)
            out.append(i)
    return out


def normalize(value: str) -> str:
    return re.sub(r"[\s\-]", "", value.strip().lower().rstrip(".,;"))


def extract_identifiers(text: str) -> Identifiers:
    emails = _dedupe(EMAIL_RE.findall(text))
    email_spans = " ".join(emails)
    upis = _dedupe([u for u in UPI_RE.findall(text) if u not in email_spans])
    urls = _dedupe([u.rstrip(".,;") for u in URL_RE.findall(text) if "@" not in u and u not in email_spans])
    phones = _dedupe([p.strip() for p in PHONE_RE.findall(text)])
    accounts = _dedupe([m for m in ACCOUNT_RE.findall(text) if any(ch.isdigit() for ch in m)])
    ids = Identifiers(phones, emails, urls, upis, accounts)

    m = AMOUNT_RE.search(text)
    if m:
        if m.group(5):
            ids.amount, ids.currency = float(m.group(5).replace(",", "")), "USD"
        else:
            num = m.group(1) or m.group(3)
            mult = _MULT.get((m.group(2) or m.group(4) or "").lower(), 1)
            ids.amount, ids.currency = float(num.replace(",", "")) * mult, "INR"
    return ids


def grounded(values: list[str], text: str) -> list[str]:
    """Keep only identifiers that literally occur in the source text (normalised)."""
    haystack = normalize(text)
    return [v for v in values if v and normalize(v) in haystack]
