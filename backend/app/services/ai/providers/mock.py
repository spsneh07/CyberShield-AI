"""Offline mock LLM.

Used by tests and for running the app without an API key. It is NOT a language
model: it applies transparent keyword/regex heuristics to the same structured
<input> payload a real model receives and returns schema-shaped JSON. Responses
are labelled provider="mock" end-to-end. Tests can queue raw responses per task
(including malformed ones) via `queue`.
"""
import json
import re
from collections import defaultdict, deque
from typing import Any

from app.services.ai.base import LLMProvider
from app.services.classification.taxonomy import CATEGORIES, CATEGORY_BY_ID, label
from app.services.incident.identifiers import extract_identifiers
from app.services.language import current_language

_INPUT_RE = re.compile(r"<input>\s*(.*?)\s*</input>", re.DOTALL)
_SOURCE_RE = re.compile(r'<source id="([^"]*)" title="([^"]*)" organization="([^"]*)" url="([^"]*)">\s*(.*?)\s*</source>', re.DOTALL)

PLATFORMS = ["WhatsApp", "Telegram", "Instagram", "Facebook", "Twitter", "X", "LinkedIn", "Gmail", "email", "SMS",
             "phone call", "Google Pay", "PhonePe", "Paytm", "OLX", "Amazon", "Flipkart", "YouTube", "Snapchat", "website"]
EVIDENCE_WORDS = {"screenshot": "Screenshots", "sms": "SMS/message", "message": "Message", "chat": "Chat history",
                  "call log": "Call log", "recording": "Call recording", "statement": "Bank statement",
                  "receipt": "Payment receipt", "email": "Email", "transaction id": "Transaction ID", "utr": "UTR number"}
ACTION_PATTERNS = {r"block(ed)? (my )?(card|account)": "Blocked card/account", r"called (my )?bank|contacted (my )?bank": "Contacted bank",
                   r"changed (my )?password": "Changed password", r"reported|filed a complaint|complained": "Reported the incident",
                   r"blocked (the )?(number|contact|profile)": "Blocked the sender"}
LOSS_RE = re.compile(
    r"\b(lost|debited|deducted|transferred|paid|sent (him|her|them)?\s*(money|rs|₹)|withdrawn|stolen money|"
    r"kat gaye|kat gaya|kata|kate|paise gaye|paise chale gaye|paisa gaya|nikal gaye|nikala|nikale|nuksan|loss hua|"
    r"debit ho gaya|debit ho gaye|transfer ho gaya|transfer ho gaye|chori ho gaye|चले गए|चला गया|"
    r"गंवा|गंवाया|खोया|कट|कटा|कटे|कटी|निकला|निकले|चोरी|नुकसान|डेबिट|हस्तांतरित|ट्रांसफर)\b",
    re.I,
)
NO_LOSS_RE = re.compile(
    r"(?:\b(didn'?t|did not|no|not)\b[^.]{0,30}\b(lose|lost|pay|paid|money|transfer|share|shared)\b|"
    r"\b(koi\s*(?:nuksan|loss|paisa|paise)?\s*nahi|paise\s*nahi\s*kate|paisa\s*nahi\s*kata|debit\s*nahi|transfer\s*nahi)\b|"
    r"(?:कोई\s*(?:नुकसान|पैसा|पैसे)?\s*नहीं|पैसे\s*नहीं\s*कटे|पैसा\s*नहीं\s*कटा|डेबिट\s*नहीं|ट्रांसफर\s*नहीं))",
    re.I,
)
DATE_RE = re.compile(r"\b(today|yesterday(?: (?:morning|evening|night|afternoon))?|last (?:night|week|month)|\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{1,2}(?:st|nd|rd|th)? (?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*(?: \d{4})?|(?:on )?(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)|\d{1,2}(?::\d{2})? ?(?:am|pm))\b", re.I)

RED_FLAGS = [
    (r"otp|one.time password|verification code", "Asked you to share an OTP — banks never ask for OTPs"),
    (r"blocked|suspend|deactivat|within \d+ hours|urgent|immediately", "Creates urgency or threatens account blocking"),
    (r"click|link|http|www\.|\.com|\.in", "Asks you to click a link"),
    (r"claim(ing|ed)? to be|pretend|from (my|the|your) bank|bank official|customer care", "Impersonates a trusted organisation"),
    (r"fee|deposit|pay first|registration", "Asks for upfront payment"),
    (r"guarantee|double|high return|profit", "Promises unrealistic returns"),
    (r"anydesk|teamviewer|apk|install", "Asks you to install an app or give remote access"),
    (r"whatsapp|telegram|personal number", "Uses an unofficial channel instead of official ones"),
]


def _payload(user: str) -> dict[str, Any]:
    m = _INPUT_RE.search(user)
    return json.loads(m.group(1)) if m else {}


def _sources(user: str) -> list[dict[str, str]]:
    return [dict(zip(("id", "title", "organization", "url", "text"), g)) for g in _SOURCE_RE.findall(user)]


def _has(keyword: str, text: str) -> bool:
    """Word-boundary prefix match ('impersonat' matches 'impersonating'; 'rang' does not match 'strange')."""
    return re.search(rf"(?<![a-z0-9]){re.escape(keyword)}", text) is not None


def _scores(text: str) -> list[tuple[str, int]]:
    t = text.lower()
    scores = [(c.id, sum(1 for k in c.keywords if _has(k, t))) for c in CATEGORIES if c.id != "unknown"]
    # Combination rules mirroring common definitions.
    boost = {"phishing": 2 if ("link" in t or "http" in t or "www." in t) and ("bank" in t or "otp" in t or "kyc" in t) else 0}
    if "whatsapp" in t or "sms" in t or "message" in t:
        boost["phishing"] = boost.get("phishing", 0) + (1 if "link" in t else 0)
    if "sms" in t and "link" in t:
        boost["smishing"] = 2
    scores = [(cid, s + boost.get(cid, 0)) for cid, s in scores]
    return sorted(scores, key=lambda x: x[1], reverse=True)


def _flags(text: str) -> list[str]:
    return [msg for pat, msg in RED_FLAGS if re.search(pat, text, re.I)]


class MockLLM(LLMProvider):
    name = "mock"

    def __init__(self) -> None:
        self.queue: dict[str, deque[str]] = defaultdict(deque)
        self.calls: list[str] = []

    def complete(self, *, task: str, system: str, user: str, json_schema: dict[str, Any] | None = None) -> str:
        self.calls.append(task)
        if self.queue[task]:
            return self.queue[task].popleft()
        handler = getattr(self, f"_{task}")
        return json.dumps(handler(_payload(user), _sources(user)))

    # ---- tasks -------------------------------------------------------------------------------
    def _intent(self, p: dict, _: list) -> dict:
        m = p.get("message", "").lower()
        rules = [
            (r"complaint|draft|fir\b|shikayat|शिकायत", "generate_complaint"),
            (r"evidence|proof|what should i (keep|save)|saboot|सबूत", "evidence_checklist"),
            (r"tips|prevent|stay safe|protect myself|precaution|surakshit|suraksha|सुझाव|सुरक्षा", "safety_tips"),
            (r"is this (a )?(scam|fraud|genuine|real|legit)|is it (a )?(scam|safe)|suspicious|check this|kya ye (scam|fraud|sahi|fake)|kya yeh|क्या यह", "check_message"),
        ]
        for pat, intent in rules:
            if re.search(pat, m):
                return {"intent": intent, "confidence": 0.7}
        if re.search(r"^(hi|hello|hey|thanks|thank you|ok|okay|namaste|dhanyawad)\b", m) and len(m) < 40:
            return {"intent": "smalltalk", "confidence": 0.8}
        happened = re.search(
            r"\b(i|me|my|we|mere|maine|mujhe|hum|humein|mera|meri|मेरे|मैंने|मुझे|हम|मेरा)\b.*"
            r"\b(received|got|lost|clicked|paid|shared|hacked|called|sent|someone|debited|scammed|cheated|asked|"
            r"call|debit|kat|nikal|bheja|diya|bataya|install|download|chori|kaha|कट|गए|दिया|लिया|आया|कहा)\b",
            m,
        )
        if happened:
            return {"intent": "provide_details" if p.get("has_active_incident") else "report_incident", "confidence": 0.7}
        if re.search(r"^(what|how|why|can|should|where|who|kya|kaise|kyun|kahan)\b", m):
            return {"intent": "question", "confidence": 0.6}
        return {"intent": "provide_details" if p.get("has_active_incident") else "report_incident", "confidence": 0.4}

    def _extraction(self, p: dict, _: list) -> dict:
        msg: str = p.get("user_message", "")
        prev: dict = p.get("previous_incident") or {}
        ids = extract_identifiers(msg)
        low = msg.lower()
        platform = next((pl for pl in PLATFORMS if re.search(rf"\b{re.escape(pl.lower())}\b", low)), None)
        date = DATE_RE.search(msg)
        loss: bool | None = None
        if LOSS_RE.search(msg) or ids.amount:
            loss = True
        if NO_LOSS_RE.search(msg) and not ids.amount:
            loss = False
        evidence = [v for k, v in EVIDENCE_WORDS.items() if k in low and re.search(r"\b(have|saved|took|kept|got)\b", low)]
        actions = [v for pat, v in ACTION_PATTERNS.items() if re.search(pat, low)]
        sources: dict[str, str] = dict(prev.get("field_sources") or {})

        def merge(key: str, new: Any, src: str = "user_provided") -> Any:
            old = prev.get(key)
            if isinstance(old, list):
                combined = old + [x for x in (new or []) if x not in old]
                if new:
                    sources[key] = sources.get(key, src)
                return combined
            if new not in (None, "", []):
                sources[key] = src
                return new
            return old

        description = (prev.get("description", "") + " " + msg).strip() if prev else msg
        data = {
            "incident_type": prev.get("incident_type"),
            "subtype": prev.get("subtype"),
            "description": description,
            "date_time": merge("date_time", date.group(0) if date else None),
            "platform": merge("platform", platform, "inferred"),
            "financial_loss": merge("financial_loss", loss, "inferred"),
            "amount": merge("amount", ids.amount),
            "currency": merge("currency", ids.currency, "inferred"),
            "phone_numbers": merge("phone_numbers", ids.phone_numbers),
            "emails": merge("emails", ids.emails),
            "urls": merge("urls", ids.urls),
            "upi_ids": merge("upi_ids", ids.upi_ids),
            "account_identifiers": merge("account_identifiers", ids.account_identifiers),
            "evidence_available": merge("evidence_available", evidence),
            "actions_taken": merge("actions_taken", actions),
            "missing_information": [],
            "field_sources": sources,
        }
        known = sum(1 for k in ("date_time", "platform", "financial_loss") if data[k] is not None)
        data["confidence"] = round(min(0.9, 0.4 + 0.1 * known + (0.1 if len(description) > 120 else 0)), 2)
        return data

    def _classification(self, p: dict, _: list) -> dict:
        inc = p.get("incident") or {}
        text = " ".join([inc.get("description", ""), inc.get("platform") or "", " ".join(inc.get("urls") or []),
                         " ".join(inc.get("upi_ids") or [])])
        ranked = _scores(text)
        top, score = ranked[0]
        if score == 0 or len(inc.get("description", "")) < 25:
            return {"category": "unknown", "subtype": None, "confidence": 0.2,
                    "reasoning": "The description does not contain enough specific details to determine the type.",
                    "alternatives": [{"category": c, "confidence": 0.1} for c, s in ranked[:2] if s > 0]}
        conf = round(min(0.9, 0.4 + 0.1 * score), 2)
        hits = [k for k in CATEGORY_BY_ID[top].keywords if _has(k, text.lower())][:4]
        return {
            "category": top,
            "subtype": f"{label(top)} via {inc['platform']}" if inc.get("platform") else None,
            "confidence": conf,
            "reasoning": f"Heuristic match on indicators: {', '.join(hits) or 'context'}. (offline mock classifier)",
            "alternatives": [{"category": c, "confidence": round(min(conf - 0.05, 0.3 + 0.1 * s), 2)}
                             for c, s in ranked[1:4] if s > 0],
        }

    def _explanation(self, p: dict, src: list) -> dict:
        inc, cls = p.get("incident") or {}, p.get("classification") or {}
        cat = CATEGORY_BY_ID.get(cls.get("category", "unknown"), CATEGORY_BY_ID["unknown"])
        return {
            "summary": inc.get("description", "")[:300],
            "why_this_type": cls.get("reasoning", ""),
            "warning_signs": _flags(inc.get("description", "")),
            "simple_explanation": f"{cat.label}: {cat.definition}"
            + (f" {src[0]['text'][:300]}" if src else ""),
            "source_ids": [s["id"] for s in src[:2]],
        }

    def _guidance(self, p: dict, src: list) -> dict:
        inc = p.get("incident") or {}
        d = inc.get("description", "").lower()
        is_hi = current_language() == "hi"
        actions = []
        if is_hi:
            if inc.get("financial_loss"):
                actions.append("तुरंत अपने बैंक या भुगतान ऐप (UPI/वॉलेट) से संपर्क करें और खाते/कार्ड/UPI को ब्लॉक कराएं ताकि आगे नुकसान न हो।")
            if "otp" in d or "password" in d or "pin" in d:
                actions.append("जिन खातों या सेवाओं का पासवर्ड या ओटीपी साझा हुआ है, उन्हें तुरंत बदलें।")
            if inc.get("urls") or "link" in d:
                actions.append("संदिग्ध लिंक को दोबारा न खोलें और उस पर कोई विवरण न भरें।")
            actions.append("जालसाज से तुरंत संपर्क बंद करें और पैसे वापस दिलाने के नाम पर किसी को कोई भुगतान न करें।")
            security = [
                "ईमेल, बैंकिंग और सोशल मीडिया खातों पर टू-फैक्टर ऑथेंटिकेशन (2FA) चालू करें।",
                "हाल ही के लॉगिन और डिवाइस की समीक्षा करें और अज्ञात सत्रों से लॉग आउट करें।",
                "बैंक और यूपीआई ऐप के लेन-देन इतिहास की जांच करें।",
            ]
            do_not = [
                "चैट, संदेश या कॉल लॉग डिलीट न करें।",
                "कोई अन्य ओटीपी, पिन या पासवर्ड किसी से साझा न करें।",
                "पैसे वापस दिलाने का दावा करने वाले किसी भी व्यक्ति को शुल्क न दें।",
            ]
        else:
            if inc.get("financial_loss"):
                actions.append("Contact your bank immediately using the number on your card or the official app and ask them to block the card/account and dispute the transaction.")
            if "otp" in d or "password" in d or "pin" in d:
                actions.append("Change passwords/PINs for any account whose details or OTP may have been shared, starting with banking and email.")
            if inc.get("urls") or "link" in d:
                actions.append("Do not open the link again or enter any more details on that page.")
            actions.append("Stop all communication with the suspected fraudster and do not send money to 'recover' losses.")
            security = [
                "Enable two-factor authentication on email, banking and social accounts.",
                "Review recent logins/devices and sign out of unknown sessions.",
                "Check bank and UPI app transaction history for unfamiliar activity.",
            ]
            do_not = [
                "Do not delete the chats, messages or call logs.",
                "Do not share any more OTPs, PINs or passwords.",
                "Do not pay anyone who promises to recover your money for a fee.",
            ]
        reporting = [re.sub(r"\s+", " ", sent).strip(" -*")
                     for s in src for line in s["text"].split("\n")[1:]  # skip the chunk's heading line
                     for sent in re.split(r"(?<=[.!?])\s+", line)
                     if re.search(r"report|complain|helpline", sent, re.I)][:3]
        evidence = [{"item": "Screenshots of the messages/chats with visible date, time and sender", "why": "Shows how the fraud was carried out", "priority": "high"}]
        if inc.get("phone_numbers"):
            evidence.append({"item": "Call log / numbers used by the suspect", "why": "Identifies the suspect's contact", "priority": "high"})
        if inc.get("urls"):
            evidence.append({"item": "The exact suspicious URL (copy, do not open)", "why": "Allows the site to be investigated and taken down", "priority": "high"})
        if inc.get("financial_loss") or inc.get("upi_ids"):
            evidence.append({"item": "Bank statement / transaction receipt with transaction ID or UTR", "why": "Needed to trace and freeze funds", "priority": "high"})
        evidence.append({"item": "Any emails, SMS or app notifications related to the incident", "why": "Corroborates the timeline", "priority": "medium"})
        have = " ".join(inc.get("evidence_available") or []).lower()
        for e in evidence:
            e["already_available"] = any(w in e["item"].lower() for w in have.split() if len(w) > 3)
        return {
            "immediate_actions": actions,
            "security_steps": security,
            "reporting_guidance": reporting or (
                ["राष्ट्रीय साइबर अपराध रिपोर्टिंग पोर्टल (cybercrime.gov.in) या 1930 पर तुरंत शिकायत दर्ज करें।"]
                if is_hi
                else ["Report the incident through the official national cybercrime reporting channel or your local police station."]
            ),
            "evidence_checklist": evidence,
            "do_not": do_not,
            "source_ids": [s["id"] for s in src[:3]],
        }

    def _complaint(self, p: dict, _: list) -> dict:
        inc, cls = p.get("incident") or {}, p.get("classification") or {}
        when = inc.get("date_time") or "[DATE AND TIME]"
        via = inc.get("platform") or "[PLATFORM/MEDIUM]"
        loss = (f"I lost {inc.get('currency') or ''} {inc['amount']:,.2f}." if inc.get("amount")
                else "I lost [AMOUNT]." if inc.get("financial_loss") else "")
        narrative = (f"On {when}, I was contacted via {via}. {inc.get('description', '').strip()} {loss}").strip()
        req = ["Register my complaint and investigate the incident.", "Take action against the persons responsible."]
        if inc.get("financial_loss"):
            req.append("Help trace and freeze the transferred funds and assist in their recovery.")
        if cls.get("category") in ("social_media_impersonation", "fake_website"):
            req.append("Help get the fake profile/website taken down.")
        return {"narrative": narrative, "requested_assistance": req}

    def _awareness(self, p: dict, src: list) -> dict:
        cls = p.get("classification") or {}
        cat = CATEGORY_BY_ID.get(cls.get("category") or "unknown", CATEGORY_BY_ID["unknown"])
        topic = p.get("topic") or cat.label
        desc = (p.get("incident") or {}).get("description", "") + " " + (p.get("topic") or "")
        return {
            "headline": f"Spot it, stop it: protecting yourself from {topic.lower()}",
            "warning_signs": _flags(desc) or ["Unexpected messages creating urgency", "Requests for OTP, PIN or passwords"],
            "prevention_tips": ["Never share OTPs, UPI PINs or passwords — no genuine bank or agency asks for them.",
                                "Type official website addresses yourself instead of clicking links in messages.",
                                "Verify claims by calling the official number printed on your card or the official website."],
            "future_precautions": ["Turn on two-factor authentication for important accounts.",
                                   "Set transaction alerts and daily limits on bank/UPI apps.",
                                   "Talk to family members about this scam pattern."],
            "resources": [{"title": s["title"], "organization": s["organization"], "url": s["url"]} for s in src[:3] if s["url"]],
        }

    def _redflag_explain(self, p: dict, _: list) -> dict:
        # The mock cannot write better prose than the rule text, so it keeps the rule explanations.
        return {"explanations": []}

    def _conversation(self, p: dict, src: list) -> dict:
        is_hi = current_language() == "hi"
        intent = p.get("intent")
        if intent == "smalltalk":
            reply = "नमस्ते! मैं संदिग्ध संदेशों को समझने, साइबर घटना की रिपोर्ट करने, सबूत सहेजने या शिकायत तैयार करने में आपकी मदद कर सकता हूँ। क्या हुआ?" if is_hi else "Hello! I can help you understand a suspicious message, report a cyber incident, preserve evidence, or draft a complaint. What happened?"
            return {"reply": reply}
        if p.get("classification"):
            cls = p["classification"]
            header = f"यह घटना सबसे अधिक **{label(cls['category'])}** जैसी लगती है (सटीकता {cls['confidence']:.0%})।" if is_hi else f"This looks most like **{label(cls['category'])}** (confidence {cls['confidence']:.0%})."
            lines = [header]
            acts = (p.get("top_actions") or [])[:3]
            if acts:
                urgent_title = "सबसे ज़रूरी कदम:" if is_hi else "Most urgent steps:"
                lines.append(f"{urgent_title}\n" + "\n".join(f"{i+1}. {a}" for i, a in enumerate(acts)))
            qs = p.get("follow_up_questions") or []
            if qs:
                q_title = "अपनी रिपोर्ट पूरी करने के लिए, क्या आप बता सकते हैं:" if is_hi else "To complete your report, could you tell me:"
                lines.append(f"{q_title}\n" + "\n".join(f"- {q}" for q in qs))
            return {"reply": "\n\n".join(lines)}
        if src:
            body = " ".join(s["text"][:350] for s in src[:2])
            source_label = "स्रोत:" if is_hi else "Based on:"
            return {"reply": f"{body}\n\n_{source_label} {', '.join(s['title'] for s in src[:2])}_"}
        fallback = "मुझे अपने ज्ञानकोष में इस पर विश्वसनीय जानकारी नहीं मिली। क्या आप अधिक विस्तार से बता सकते हैं कि क्या हुआ था?" if is_hi else "I couldn't find trusted material on that in my knowledge base. Could you describe what happened in more detail?"
        return {"reply": fallback}
