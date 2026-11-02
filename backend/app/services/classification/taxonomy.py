"""Fixed cybercrime taxonomy shared by prompts, validation, the mock provider and the UI."""
import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Category:
    id: str
    label: str
    definition: str
    keywords: tuple[str, ...] = field(default_factory=tuple)


CATEGORIES: tuple[Category, ...] = (
    Category("phishing", "Phishing", "Deceptive message or email with a link/attachment that impersonates a trusted entity to steal credentials, OTPs or card data.",
             ("link", "click", "login", "verify", "kyc", "blocked", "suspended", "update your", "password", "email", "fake bank", "link bheja", "लिंक भेजा", "लिंक पर क्लिक")),
    Category("smishing", "Smishing (SMS phishing)", "Phishing delivered through SMS text messages.",
             ("sms", "text message", "electricity bill", "disconnect", "parcel", "delivery failed")),
    Category("vishing", "Vishing (voice phishing)", "Fraudster phones the victim, impersonating a bank, police, courier or support agent.",
             ("called me", "phone call", "caller", "customer care", "rang", "voice call", "digital arrest", "call kiya", "call aaya", "bolke call", "phone aaya", "कॉल किया", "कॉल आया", "फोन आया")),
    Category("upi_fraud", "UPI / payment fraud", "Fraud using UPI collect requests, QR codes or payment apps to take money.",
             ("upi", "gpay", "google pay", "phonepe", "paytm", "qr code", "collect request", "@ybl", "@okaxis", "@paytm", "scan", "upi pin", "qr scan", "paise bhejo", "क्यूआर")),
    Category("banking_fraud", "Banking fraud", "Unauthorised bank/card transactions, card cloning, net-banking misuse.",
             ("debited", "unauthorised transaction", "unauthorized transaction", "credit card", "debit card", "net banking", "atm", "account debited", "bank account", "debit", "paise kat", "paise nikal", "account se paise", "पैसे कट", "खाते से पैसे")),
    Category("otp_scam", "OTP scam", "Victim is manipulated into sharing a one-time password that authorises a transaction or account change.",
             ("otp", "one time password", "one-time password", "verification code", "ओटीपी")),
    Category("shopping_fraud", "Online shopping fraud", "Fake sellers or listings that take payment and never deliver.",
             ("ordered", "seller", "delivery", "product", "olx", "marketplace", "refund", "instagram shop", "never received")),
    Category("investment_scam", "Investment scam", "Promises of high/guaranteed returns, fake trading apps or task-based investment groups.",
             ("investment", "returns", "trading", "stock tips", "double your money", "profit", "telegram group", "task", "टास्क", "निवेश")),
    Category("job_scam", "Job / employment scam", "Fake job offers requiring fees, deposits or personal documents; part-time 'task' jobs.",
             ("job", "offer letter", "registration fee", "work from home", "part-time", "part time", "recruiter", "hiring", "salary", "interview", "job offer", "नौकरी")),
    Category("identity_theft", "Identity theft", "Misuse of someone's personal identity documents or details (e.g. loans or SIMs in their name).",
             ("aadhaar", "pan card", "loan in my name", "identity", "documents misused", "sim in my name")),
    Category("account_takeover", "Account takeover", "Attacker gains control of an online account (email, social, banking) and locks the owner out.",
             ("hacked", "can't log in", "cannot log in", "password changed", "account compromised", "locked out", "someone logged")),
    Category("social_media_impersonation", "Social-media impersonation", "Fake or cloned profile impersonating a person or brand, often to ask contacts for money.",
             ("fake profile", "fake account", "impersonat", "cloned", "pretending to be", "my name and photo", "asking my friends")),
    Category("fake_website", "Fake website", "Spoofed or look-alike website imitating a legitimate brand, bank or government portal.",
             ("website", "fake site", "look-alike", "lookalike", "domain", "portal")),
    Category("malware", "Malware", "Malicious app or file installed on a device (e.g. APK, remote-access tools, spyware).",
             ("apk", "installed an app", "anydesk", "teamviewer", "remote access", "virus", "spyware", "screen sharing", "screen share", "install karwaya", "download karwaya", "स्क्रीन शेयर")),
    Category("ransomware", "Ransomware", "Files encrypted and a ransom demanded for decryption.",
             ("encrypted", "ransom", "files locked", "decrypt", "bitcoin to unlock")),
    Category("sextortion", "Sextortion", "Threats to share intimate images/videos unless the victim pays or complies.",
             ("nude", "intimate", "video call", "morphed", "leak my video", "obscene", "blackmail")),
    Category("cyberbullying", "Cyberbullying / harassment", "Online harassment, threats, stalking, abusive messages or doxxing.",
             ("harass", "bully", "abusive", "threatening messages", "stalking", "trolling", "threat")),
    Category("data_theft", "Data theft / breach", "Unauthorised access to or leak of personal or organisational data.",
             ("data breach", "leaked", "data leak", "database", "stolen data")),
    Category("romance_scam", "Romance scam", "Fraudster builds an online romantic relationship to extract money.",
             ("dating", "relationship", "boyfriend", "girlfriend", "matrimonial", "met online", "gift customs")),
    Category("crypto_scam", "Cryptocurrency scam", "Fraud involving crypto wallets, fake exchanges or crypto investment schemes.",
             ("crypto", "bitcoin", "usdt", "wallet", "exchange", "ethereum", "binance")),
    Category("unknown", "Unknown / Needs More Information", "Not enough information to determine the type, or it does not fit the taxonomy.", ()),
)

CATEGORY_BY_ID = {c.id: c for c in CATEGORIES}
CATEGORY_IDS = frozenset(CATEGORY_BY_ID)

_ALIASES = {
    "upi": "upi_fraud", "payment_fraud": "upi_fraud", "upi_payment_fraud": "upi_fraud",
    "bank_fraud": "banking_fraud", "online_shopping_fraud": "shopping_fraud",
    "job": "job_scam", "employment_scam": "job_scam", "impersonation": "social_media_impersonation",
    "social_media": "social_media_impersonation", "harassment": "cyberbullying",
    "cyberbullying_harassment": "cyberbullying", "cryptocurrency_scam": "crypto_scam",
    "other": "unknown", "other_unknown": "unknown", "needs_more_information": "unknown",
    "unknown_needs_more_information": "unknown", "sms_phishing": "smishing", "voice_phishing": "vishing",
}


def normalize_category(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")
    if slug in CATEGORY_BY_ID:
        return slug
    if slug in _ALIASES:
        return _ALIASES[slug]
    for c in CATEGORIES:
        if re.sub(r"[^a-z0-9]+", "_", c.label.lower()).strip("_") == slug:
            return c.id
    return slug


def label(category_id: str | None) -> str:
    if not category_id:
        return "Unknown"
    return CATEGORY_BY_ID.get(category_id, CATEGORY_BY_ID["unknown"]).label


def taxonomy_for_prompt() -> str:
    return "\n".join(f"- {c.id}: {c.label} — {c.definition}" for c in CATEGORIES)
