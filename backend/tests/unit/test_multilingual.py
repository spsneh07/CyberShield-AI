"""Dedicated multilingual reliability test suite for English, Hindi (Devanagari), and Hinglish (Romanised).

Covers Tasks 1 to 5 of Batch 3:
- Task 1: Hindi incident understanding (loss, OTP, links, remote apps, uncertainty)
- Task 2: Hinglish support (realistic phrases, distinguishing facts from uncertainty)
- Task 3: Mixed-language conversations & cross-language contradictions
- Task 4: Multilingual complaint validation & fact preservation
- Task 5: Multilingual safety (evidence preservation & urgent banking guidance)
"""
import pytest

from app.schemas.classification import ClassificationResult
from app.schemas.guidance import Citation
from app.schemas.incident import FactSource, IncidentData
from app.services.ai.providers.mock import MockLLM
from app.services.classification.classifier import IncidentClassifier
from app.services.complaint.validator import validate_narrative
from app.services.guidance.advisor import Advisor
from app.services.guidance.safety import destroys_evidence, scrub_reply
from app.services.incident.extractor import (
    QUESTIONS,
    QUESTIONS_HI,
    clicked_link,
    compute_missing,
    follow_up_questions,
    loss_conflict,
    loss_mentioned,
    remote_installed,
    sensitive_shared,
)
from app.services.incident.identifiers import extract_identifiers
from app.services.language import current_language, set_language


# ==============================================================================
# Task 1 & 2: Hindi & Hinglish Extraction & Heuristics
# ==============================================================================


class TestHindiUnderstanding:
    """Verifies understanding of Hindi (Devanagari) incident statements."""

    def test_hindi_loss_confirmed(self):
        text = "मेरे बैंक खाते से 5000 रुपये कट गए हैं।"
        assert loss_mentioned(text)
        from app.services.incident.extractor import _stated, _LOSS
        asserted, denied = _stated(_LOSS, text)
        assert asserted is True
        assert denied is False
        data = IncidentData(description=text)
        ids = extract_identifiers(text)
        assert ids.amount == 5000.0

    def test_hindi_loss_explicitly_denied(self):
        text = "मैंने समय पर कॉल काट दिया और कोई पैसा नहीं कटा।"
        from app.services.incident.extractor import _stated, _LOSS
        asserted, denied = _stated(_LOSS, text)
        assert asserted is False
        assert denied is True

    def test_hindi_otp_shared_sov_structure(self):
        text = "कॉल करने वाले ने मुझसे ओटीपी पूछा और मैंने ओटीपी बता दिया।"
        assert sensitive_shared(text) is True

    def test_hindi_otp_not_shared(self):
        text = "उन्होंने मुझसे ओटीपी माँगा लेकिन मैंने ओटीपी नहीं दिया।"
        assert sensitive_shared(text) is False

    def test_hindi_link_clicked(self):
        text = "मैंने एसएमएस में आए लिंक पर क्लिक किया था।"
        assert clicked_link(text) is True

    def test_hindi_remote_app_installed(self):
        text = "उन्होंने मुझे एनीडेस्क ऐप इंस्टॉल करने को कहा और स्क्रीन शेयर करवाई।"
        assert remote_installed(text) is True

    def test_hindi_uncertain_loss(self):
        text = "मुझे पता नहीं कि बैंक से पैसा कटा है या नहीं।"
        from app.services.incident.extractor import _stated, _LOSS
        asserted, denied = _stated(_LOSS, text)
        assert asserted is False
        assert denied is False


class TestHinglishUnderstanding:
    """Verifies understanding of realistic Hinglish (Romanised Hindi) statements."""

    def test_hinglish_loss_confirmed(self):
        text = "Mere account se 5000 rupees debit ho gaye."
        assert loss_mentioned(text)
        from app.services.incident.extractor import _stated, _LOSS
        asserted, denied = _stated(_LOSS, text)
        assert asserted is True
        assert denied is False
        ids = extract_identifiers(text)
        assert ids.amount == 5000.0

    def test_hinglish_otp_not_shared(self):
        text = "Maine OTP share nahi kiya tha."
        assert sensitive_shared(text) is False

    def test_hinglish_otp_shared(self):
        text = "Maine galti se OTP bata diya call par."
        assert sensitive_shared(text) is True

    def test_hinglish_remote_access(self):
        text = "AnyDesk install karwaya aur screen share karne bola."
        assert remote_installed(text) is True

    def test_hinglish_uncertain_loss(self):
        text = "Mujhe pata nahi paisa kata ya nahi."
        from app.services.incident.extractor import _stated, _LOSS
        asserted, denied = _stated(_LOSS, text)
        assert asserted is False
        assert denied is False

    def test_hinglish_bank_call_vishing(self):
        text = "Bank wale bolke call kiya tha aur password mang rahe the."
        classifier = IncidentClassifier(MockLLM())
        res = classifier.classify(IncidentData(description=text))
        assert res.category in ("vishing", "banking_fraud", "otp_scam")


# ==============================================================================
# Task 3: Mixed-Language & Contradictions
# ==============================================================================


class TestMixedLanguageAndContradictions:
    """Tests cross-language contradiction detection and follow-up generation."""

    def test_cross_language_contradiction_english_to_hindi(self):
        prev = IncidentData(description="I lost Rs 5000 from my account", financial_loss=True)
        message = "Nahi, koi paisa nahi kata, mere account ke paise safe hain."
        assert loss_conflict(message, prev) is True

    def test_cross_language_contradiction_hindi_to_english(self):
        prev = IncidentData(description="Koi paisa nahi kata", financial_loss=False)
        message = "Actually Rs 3000 was debited after that call."
        assert loss_conflict(message, prev) is True

    def test_within_turn_contradiction_hinglish(self):
        message = "Mera 5000 rupaye kat gaya. Par actually koi paisa nahi kata."
        assert loss_conflict(message, None) is True

    def test_follow_up_questions_language_selection(self):
        missing = ["engaged", "date_time"]
        en_questions = follow_up_questions(missing, limit=2, language="en")
        assert en_questions[0] == QUESTIONS["engaged"]

        hi_questions = follow_up_questions(missing, limit=2, language="hi")
        assert hi_questions[0] == QUESTIONS_HI["engaged"]

    def test_decisive_missing_questions_logic(self):
        # Case A: Remote app installed, loss unknown -> decisive is money_moved
        data = IncidentData(description="AnyDesk install karwaya unhone call pe.", financial_loss=None)
        missing = compute_missing(data)
        assert missing[0] == "money_moved"

        # Case B: Link clicked, nothing entered -> decisive is details_entered
        data = IncidentData(description="I clicked the link from the SMS.", financial_loss=None)
        missing = compute_missing(data)
        assert missing[0] == "details_entered"

        # Case C: Call received, nothing done -> decisive is engaged
        data = IncidentData(description="Bank wale bolke call kiya tha.", financial_loss=None)
        missing = compute_missing(data)
        assert missing[0] == "engaged"


# ==============================================================================
# Task 4: Multilingual Complaint Validation
# ==============================================================================


class TestMultilingualComplaintValidation:
    """Ensures user facts in Hindi/Hinglish are respected in complaint validation."""

    def test_hindi_otp_disclosure_matches_english_draft(self):
        user_text = "मैंने जालसाज को कॉल पर ओटीपी दे दिया था।"
        incident = IncidentData(description=user_text)
        complaint_body = "The suspect contacted me and I shared the OTP over the phone."
        scrubbed, notes = validate_narrative(complaint_body, incident)
        # Should not flag or scrub "I shared the OTP" since user stated it in Hindi
        assert "shared the OTP" in scrubbed

    def test_hinglish_otp_disclosure_matches_english_draft(self):
        user_text = "Maine call par OTP share kiya tha."
        incident = IncidentData(description=user_text)
        complaint_body = "The caller asked for the code and I provided the OTP."
        scrubbed, notes = validate_narrative(complaint_body, incident)
        assert "provided the OTP" in scrubbed

    def test_unsupported_action_in_complaint_flagged(self):
        user_text = "Mujhe fake bank call aaya tha."
        incident = IncidentData(description=user_text)
        complaint_body = "I immediately contacted my bank branch to report it."
        scrubbed, notes = validate_narrative(complaint_body, incident)
        assert len(notes) > 0
        assert any("action" in n.lower() or "unsupported" in n.lower() for n in notes)


# ==============================================================================
# Task 5: Multilingual Safety & Guidance
# ==============================================================================


class TestMultilingualSafety:
    """Verifies safety rules hold across English, Hindi, and Hinglish."""

    def test_evidence_destruction_hinglish(self):
        assert destroys_evidence("Apne saare WhatsApp messages mita do.")
        assert destroys_evidence("Chat delete kar do turant.")
        assert destroys_evidence("Call log saaf kar dein.")
        assert destroys_evidence("Saboot mita do.")

    def test_evidence_destruction_hinglish_negated_is_safe(self):
        assert not destroys_evidence("WhatsApp chat delete mat karna.")
        assert not destroys_evidence("Message delete nahi karna.")
        assert not destroys_evidence("Screenshots delete mat karo.")
        assert not destroys_evidence("Bina chat delete kiye saboot rakhein.")

    def test_evidence_destruction_hindi(self):
        assert destroys_evidence("सारे मैसेज मिटा दें।")
        assert destroys_evidence("स्क्रीनशॉट हटा दो।")
        assert not destroys_evidence("मैसेज मत मिटाएं।")
        assert not destroys_evidence("चैट डिलीट न करें।")

    def test_scrub_reply_multilingual(self):
        raw = "तुरंत बैंक को सूचित करें। चैट डिलीट कर दें। साक्ष्य सुरक्षित रखें।"
        scrubbed = scrub_reply(raw)
        assert "चैट डिलीट" not in scrubbed
        assert "तुरंत बैंक" in scrubbed

    def test_urgent_bank_guidance_for_remote_compromise(self):
        llm = MockLLM()
        advisor = Advisor(llm)
        data = IncidentData(description="AnyDesk install karwaya tha unhone.")
        cls = ClassificationResult(category="malware", confidence=0.8, reasoning="Remote access tool installed.")
        src = [
            Citation(
                id="doc-1",
                title="RBI Safety",
                organization="RBI",
                url="https://rbi.org.in/safety",
                category="malware",
                document_type="official_text",
                score=0.9,
                excerpt="Contact your bank immediately to block net banking.",
            )
        ]
        guidance = advisor.guide(data, cls, src)
        assert any("bank" in act.lower() or "uninstall" in act.lower() for act in guidance.immediate_actions)

    def test_hindi_response_localises_urgent_actions(self):
        set_language("hi")
        try:
            llm = MockLLM()
            advisor = Advisor(llm)
            data = IncidentData(description="मेरे खाते से 10000 रुपये कट गए।", financial_loss=True)
            cls = ClassificationResult(category="banking_fraud", confidence=0.9, reasoning="Banking loss.")
            guidance = advisor.guide(data, cls, [])
            # Should have Hindi urgent bank action
            assert any("बैंक" in act for act in guidance.immediate_actions)
            # Baseline evidence checklist items should be localized in Hindi
            assert any("स्क्रीनशॉट" in item.item for item in guidance.evidence_checklist)
        finally:
            set_language("en")
