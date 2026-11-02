"""Targeted regression tests for Batch 2: Guidance and Evidence Safety improvements."""
from datetime import date
import pytest

from app.schemas.classification import ClassificationResult
from app.schemas.guidance import Citation, EvidenceChecklistItem
from app.schemas.incident import IncidentData
from app.services.ai.base import LLMProvider
from app.services.awareness.generator import AwarenessGenerator
from app.services.complaint.validator import validate_narrative
from app.services.guidance.advisor import Advisor, known_text, scrub_unsupported
from app.services.guidance.safety import destroys_evidence, drop_evidence_destruction
from app.services.incident.extractor import IncidentExtractor, _stated, _LOSS
from app.services.rag.retriever import freshness
from tests.conftest import DEMO, MockLLM


# ==============================================================================
# Task 1: Contact-number and helpline scrubbing in advisor.py
# ==============================================================================

def test_recognized_emergency_and_helpline_numbers_are_preserved():
    # 112 (National Emergency), 1930 (National Cybercrime), 100 (Police), 108 (Ambulance)
    assert "112" in scrub_unsupported("Call 112 immediately for emergency help.", [])
    assert "1930" in scrub_unsupported("Dial 1930 to report cyber fraud immediately.", [])
    assert "100" in scrub_unsupported("Contact local police at 100.", [])
    assert "108" in scrub_unsupported("Call 108 for medical emergency.", [])
    assert "1098" in scrub_unsupported("Childline helpline is 1098.", [])


def test_unsupported_contact_numbers_and_shortcodes_are_scrubbed():
    # 3-digit and 4-digit unverified shortcodes must not bypass regex
    res_114 = scrub_unsupported("Call 114 for quick assistance.", [])
    assert "114" not in res_114
    assert "[official helpline — verify]" in res_114

    res_999 = scrub_unsupported("Dial 999 for instant loan support.", [])
    assert "999" not in res_999
    assert "[official helpline — verify]" in res_999

    res_8888 = scrub_unsupported("Contact support at 8888.", [])
    assert "8888" not in res_8888
    assert "[official helpline — verify]" in res_8888

    # 10-digit phone number not in sources or known text
    res_phone = scrub_unsupported("Call suspect back on 9876543210.", [])
    assert "9876543210" not in res_phone
    assert "[official helpline — verify]" in res_phone


def test_ordinary_numbers_years_and_amounts_are_preserved():
    # Calendar years
    assert "2026" in scrub_unsupported("This scam was reported in 2026.", [])
    assert "2024" in scrub_unsupported("Guidance updated in 2024.", [])
    assert "2000" in scrub_unsupported("Offences fall under the IT Act 2000.", [])

    # Amounts
    assert "500" in scrub_unsupported("The user transferred Rs 500 via UPI.", [])
    assert "1000" in scrub_unsupported("A fee of ₹1000 was requested.", [])
    assert "500" in scrub_unsupported("User lost 500 rupees.", [])

    # Legal sections and durations
    assert "420" in scrub_unsupported("Punishable under Section 420 of IPC.", [])
    assert "66" in scrub_unsupported("Covered under Section 66D of the IT Act.", [])
    assert "180" in scrub_unsupported("Submit claims within 180 days.", [])
    assert "24" in scrub_unsupported("Notify bank within 24 hours.", [])


def test_user_provided_identifiers_in_description_are_preserved():
    # Identifier mentioned in description but not in structured fields
    inc = IncidentData(description="The scammer asked me to send money to reference 9876543210 and link test-scam.xyz.")
    known = known_text(inc)
    res = scrub_unsupported("You mentioned suspect number 9876543210.", [], known=known)
    assert "9876543210" in res


# ==============================================================================
# Batch 2.1 Task 1: Contact Trust & Grounding Regressions
# ==============================================================================

def test_scammer_phone_in_description_scrubbed_from_affirmative_advice():
    """A scammer's phone number in incident description must never be trusted as a helpline in advice."""
    desc = "I received a call from 9876543210 claiming to be FedEx. They asked for money."
    inc = IncidentData(description=desc, phone_numbers=["9876543210"])
    known = known_text(inc)

    # Affirmative advice recommending to contact/call the scammer number must be scrubbed
    advice = "Call 9876543210 to clarify your parcel delivery status."
    scrubbed = scrub_unsupported(advice, [], known=known)
    assert "9876543210" not in scrubbed
    assert "[official helpline — verify]" in scrubbed

    # But evidence checklist and negated warnings must preserve the scammer's number
    evidence = "Suspect phone number(s): 9876543210 and call log"
    assert "9876543210" in scrub_unsupported(evidence, [], known=known)

    negation = "Do not call 9876543210 or reply to their SMS."
    assert "9876543210" in scrub_unsupported(negation, [], known=known)


def test_user_provided_bank_number_scrubbed_to_safe_banking_instruction():
    """User-provided bank number must be replaced with safe bank card/passbook instructions."""
    desc = "I received a call from someone claiming to be my bank. They told me to call 9876543210 to reverse the transaction."
    inc = IncidentData(description=desc)
    known = known_text(inc)

    # Model advice repeating the user's unverified number as an action to contact
    advice = "Call 9876543210 to reverse the transaction immediately."
    scrubbed = scrub_unsupported(advice, [], known=known)
    assert "9876543210" not in scrubbed
    assert "the official number on your bank card, passbook or mobile app [official helpline — verify]" in scrubbed

    # Direct contact phrasing
    advice2 = "Contact your bank at 9876543210."
    scrubbed2 = scrub_unsupported(advice2, [], known=known)
    assert "9876543210" not in scrubbed2
    assert "the official number on your bank card, passbook or mobile app [official helpline — verify]" in scrubbed2


def test_contact_number_supported_by_retrieved_official_source_is_preserved():
    """Contact numbers explicitly supported by retrieved official citations are allowed."""
    from app.schemas.guidance import Citation

    src = Citation(
        id="ncrp-1",
        title="NCRP Reporting",
        organization="I4C / MHA",
        url="https://cybercrime.gov.in",
        excerpt="Victims of cyber financial fraud should call 1930 immediately or dial 1800-11-4000.",
        category="general",
        document_type="official_text",
        score=0.9,
    )
    # 1930 and 1800-11-4000 are in the source citation
    assert "1930" in scrub_unsupported("Call 1930 immediately to report the transaction.", [src], known="")
    assert "1800-11-4000" in scrub_unsupported("Dial toll-free 1800-11-4000 for support.", [src], known="")


def test_number_in_incident_not_intended_for_guidance_is_preserved_in_context():
    """Amounts, years, and transaction references in user report are preserved as evidence/facts."""
    inc = IncidentData(
        description="I sent ₹5000 with transaction reference 987654321000 to the scammer in 2026.",
        amount=5000.0,
    )
    known = known_text(inc)

    # Preserved in factual explanation / summary / evidence
    assert "987654321000" in scrub_unsupported("Transaction reference 987654321000", [], known=known)
    assert "5000" in scrub_unsupported("You reported a loss of ₹5000.", [], known=known)
    assert "2026" in scrub_unsupported("The transaction occurred in 2026.", [], known=known)

    # But if model mistakenly advises calling the transaction ID, it must be scrubbed
    bad_advice = "Call 987654321000 to dispute the transaction."
    scrubbed = scrub_unsupported(bad_advice, [], known=known)
    assert "987654321000" not in scrubbed
    assert "the official number on your bank card, passbook or mobile app [official helpline — verify]" in scrubbed


def test_unsupported_number_generated_by_model_is_scrubbed():
    """Unsupported phone numbers invented by the model must be scrubbed to safe verification instructions."""
    # Generic context
    generic = scrub_unsupported("Call customer support helpline 1800-425-9999 immediately.", [], known="")
    assert "1800-425-9999" not in generic
    assert "[official helpline — verify]" in generic

    # Banking context
    banking = scrub_unsupported("Call 1800-999-8888 to unfreeze your bank account.", [], known="")
    assert "1800-999-8888" not in banking
    assert "the official number on your bank card, passbook or mobile app [official helpline — verify]" in banking


# ==============================================================================
# Task 2: Evidence destruction prevention in safety.py
# ==============================================================================

@pytest.mark.parametrize("phrase", [
    "Clear your WhatsApp messages",
    "Clear all chat history",
    "Please clear the chat history with the scammer",
    "Clear your messages immediately",
    "Remove the call logs from your phone",
    "Discard all evidence and reset",
    "Purge chat messages right now",
    "Delete all messages received from the suspect",
    "Factory reset your phone",
    "Wipe your device completely",
])
def test_evidence_destruction_english_phrases_detected(phrase):
    assert destroys_evidence(phrase) is True
    assert drop_evidence_destruction([phrase]) == []


@pytest.mark.parametrize("safe_phrase", [
    "Do not clear your WhatsApp messages or chat history.",
    "Never delete original SMS or transaction logs.",
    "Avoid deleting any screenshots or evidence.",
    "Uninstall the suspicious remote-access application.",
    "Remove the malicious app from your phone.",
    "Clear your browser cache and cookies.",
])
def test_legitimate_safety_guidance_preserved(safe_phrase):
    assert destroys_evidence(safe_phrase) is False
    assert drop_evidence_destruction([safe_phrase]) == [safe_phrase]


@pytest.mark.parametrize("hindi_phrase", [
    "व्हाट्सएप संदेशों को साफ़ करें",
    "सभी चैट इतिहास हटा दें",
    "कॉल लॉग डिलीट करें",
    "संदेशों को तुरंत मिटा दें",
    "सबूत नष्ट कर दें",
])
def test_evidence_destruction_hindi_phrases_detected(hindi_phrase):
    assert destroys_evidence(hindi_phrase) is True
    assert drop_evidence_destruction([hindi_phrase]) == []


@pytest.mark.parametrize("hindi_safe_phrase", [
    "व्हाट्सएप संदेशों को साफ़ मत करें।",
    "चैट और संदेशों को कभी न हटाएं।",
    "संदेशों को डिलीट नहीं करें, यह सबूत हैं।",
    "कॉल रिकॉर्ड मिटाएं नहीं।",
])
def test_legitimate_hindi_guidance_preserved(hindi_safe_phrase):
    assert destroys_evidence(hindi_safe_phrase) is False
    assert drop_evidence_destruction([hindi_safe_phrase]) == [hindi_safe_phrase]


# ==============================================================================
# Task 3: Complaint narrative validation in validator.py
# ==============================================================================

def test_third_person_unsupported_actions_are_removed():
    inc = IncidentData(description=DEMO, actions_taken=[])
    narrative = (
        f"{DEMO} The complainant immediately visited the police station and blocked the card. "
        "The victim has already filed a complaint with the cyber cell."
    )
    validated, notes = validate_narrative(narrative, inc)
    assert "visited the police station" not in validated
    assert "blocked the card" not in validated
    assert "filed a complaint" not in validated
    assert len(notes) >= 2


def test_third_person_unsupported_disclosure_claims_are_removed():
    # User never stated in DEMO that they shared OTP
    inc = IncidentData(description="I received a suspicious message asking for OTP, but I did not reply.", actions_taken=[])
    narrative = "I received a message. The complainant shared the OTP with the fraudster."
    validated, notes = validate_narrative(narrative, inc)
    assert "shared the OTP" not in validated
    assert any("Removed claim about what the complainant did" in n for n in notes)


def test_supported_actions_are_preserved_in_complaint():
    inc = IncidentData(description=DEMO, actions_taken=["Reported to bank and visited police station"])
    narrative = f"{DEMO} The complainant reported to bank and visited police station."
    validated, notes = validate_narrative(narrative, inc)
    assert "visited police station" in validated


# ==============================================================================
# Task 4: Hindi ambiguity and loss detection in extractor.py
# ==============================================================================

def test_hindi_explicit_no_loss_recognized():
    llm = MockLLM()
    extractor = IncidentExtractor(llm)
    hindi_msg = "मुझे एक कॉल आया बैंक से लेकिन मैंने कोई पैसे नहीं गंवाए।"
    data, warnings = extractor.extract(hindi_msg)

    assert data.financial_loss is False
    assert "financial_loss" not in data.missing_information
    assert "money_moved" not in data.missing_information


def test_hindi_confirmed_loss_recognized():
    llm = MockLLM()
    extractor = IncidentExtractor(llm)
    hindi_loss_msg = "मेरे बैंक खाते से 5000 रुपये कट गए हैं।"
    data, warnings = extractor.extract(hindi_loss_msg)

    assert data.financial_loss is True


def test_hindi_uncertain_loss_preserves_clarification():
    llm = MockLLM()
    extractor = IncidentExtractor(llm)
    hindi_unsure_msg = "मुझे नहीं पता कि खाते से पैसे कटे हैं या नहीं।"
    data, warnings = extractor.extract(hindi_unsure_msg)

    assert data.financial_loss is None
    assert any(q in ("financial_loss", "money_moved") for q in data.missing_information)


# ==============================================================================
# Task 5: Source freshness validation in retriever.py
# ==============================================================================

def test_future_verification_dates_rejected_as_unverified():
    today = date(2026, 11, 2)
    # Future dates must be rejected as unverified
    assert freshness("2099-01-01", max_age_days=180, today=today) == "unverified"
    assert freshness("2027-01-01", max_age_days=180, today=today) == "unverified"
    assert freshness("2026-11-03", max_age_days=180, today=today) == "unverified"

    # None or empty string must be unverified
    assert freshness(None, max_age_days=180, today=today) == "unverified"
    assert freshness("", max_age_days=180, today=today) == "unverified"

    # Recent past date within 180 days is verified
    assert freshness("2026-10-01", max_age_days=180, today=today) == "verified"
    # Old past date exceeding 180 days is stale
    assert freshness("2025-01-01", max_age_days=180, today=today) == "stale"


# ==============================================================================
# Task 6: Scrubbing user-visible generated fields
# ==============================================================================

def test_evidence_checklist_items_scrubbed_and_filtered():
    inc = IncidentData(description=DEMO)
    generated_items = [
        EvidenceChecklistItem(item="Call scam helpline 9876543210 for verification", why="Helpline check", priority="high"),
        EvidenceChecklistItem(item="Clear your WhatsApp messages once screenshotted", why="Clean chat", priority="high"),
        EvidenceChecklistItem(item="Take screenshots of the chat", why="Preserves evidence", priority="high"),
    ]
    cleaned = Advisor.evidence_checklist(inc, generated_items, sources=[], known="")

    # Item with evidence destruction must be dropped
    assert not any("Clear your WhatsApp messages" in it.item for it in cleaned)

    # Item with unsupported helpline must have number scrubbed
    item_with_number = next((it for it in cleaned if "verification" in it.item), None)
    assert item_with_number is not None
    assert "9876543210" not in item_with_number.item
    assert "[official helpline — verify]" in item_with_number.item

    # Legitimate evidence item preserved
    assert any("Take screenshots of the chat" in it.item for it in cleaned)


def test_awareness_headline_scrubbed_and_guarded():
    generator = AwarenessGenerator(MockLLM())
    sources = [Citation(id="S1", title="NPCI Safety", organization="NPCI", url="https://npci.org.in",
                        category="upi_fraud", document_type="official", freshness="verified", score=0.9, excerpt="NPCI advice")]

    # Awareness generated with an unsupported helpline in headline
    awareness = generator.generate(incident=None, cls=ClassificationResult(category="upi_fraud", confidence=0.9, reasoning="test"),
                                   sources=sources)
    assert awareness.headline
    assert "9876543210" not in awareness.headline

