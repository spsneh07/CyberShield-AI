import re
import pytest
from app.schemas.conversation import MessageIn
from app.services.conversation.orchestrator import Orchestrator


def test_1_confirmed_loss_to_explicit_denial(client, db, pipeline):
    """1. Confirmed loss → explicit denial."""
    orchestrator = Orchestrator(db, pipeline)
    conv = client.post("/api/v1/conversations", json={}).json()
    cid = conv["id"]

    t1 = orchestrator.handle(cid, MessageIn(content="Someone debited ₹5,000 from my bank account."))
    assert t1.incident.financial_loss is True
    assert t1.incident.amount == 5000.0
    assert any("bank" in a.lower() for a in t1.guidance.immediate_actions)

    t2 = orchestrator.handle(cid, MessageIn(content="Actually, no money was lost. My account is safe."))
    assert t2.incident.financial_loss in (False, None)
    assert t2.incident.amount is None
    assert "noted your correction" in t2.reply.lower() or "no money was lost" in t2.reply.lower()
    assert any("mistake" in q.lower() and "suspicious transaction" in q.lower() for q in t2.follow_up_questions)
    # Must not contain debit reversal instructions
    for action in t2.guidance.immediate_actions:
        assert not re.search(r"\b(dispute the transaction|reverse.*transaction|recover losses)\b", action, re.I)


def test_2_confirmed_loss_to_uncertain_correction(client, db, pipeline):
    """2. Confirmed loss → uncertain correction."""
    orchestrator = Orchestrator(db, pipeline)
    conv = client.post("/api/v1/conversations", json={}).json()
    cid = conv["id"]

    t1 = orchestrator.handle(cid, MessageIn(content="₹5,000 was debited from my account."))
    assert t1.incident.financial_loss is True
    assert t1.incident.amount == 5000.0

    t2 = orchestrator.handle(cid, MessageIn(content="Actually, I am not sure if any money was debited."))
    assert t2.incident.financial_loss is None
    assert t2.incident.amount is None
    assert "loss_conflict" in t2.incident.missing_information
    assert any("mistake" in q.lower() or "suspicious transaction" in q.lower() for q in t2.follow_up_questions)


def test_3_explicit_denial_to_later_confirmed_loss(client, db, pipeline):
    """3. Explicit denial → later confirmed loss."""
    orchestrator = Orchestrator(db, pipeline)
    conv = client.post("/api/v1/conversations", json={}).json()
    cid = conv["id"]

    t1 = orchestrator.handle(cid, MessageIn(content="Someone sent a phishing link, but no money was lost."))
    assert t1.incident.financial_loss is False
    assert t1.incident.amount is None

    t2 = orchestrator.handle(cid, MessageIn(content="Wait, I checked my net banking and ₹5,000 was debited!"))
    assert t2.incident.financial_loss is True
    assert t2.incident.amount == 5000.0
    assert any("bank" in a.lower() for a in t2.guidance.immediate_actions)


def test_4_amount_correction(client, db, pipeline):
    """4. Amount correction (₹5,000 → ₹500)."""
    orchestrator = Orchestrator(db, pipeline)
    conv = client.post("/api/v1/conversations", json={}).json()
    cid = conv["id"]

    t1 = orchestrator.handle(cid, MessageIn(content="Someone debited ₹5,000 from my bank account."))
    assert t1.incident.amount == 5000.0

    t2 = orchestrator.handle(cid, MessageIn(content="Actually it was only ₹500, not ₹5,000."))
    assert t2.incident.amount == 500.0
    assert t2.incident.financial_loss is True

    comp = orchestrator.handle(cid, MessageIn(content="Please draft a complaint.", action="generate_complaint"))
    assert "Amount lost: INR 500.00" in comp.complaint.body
    assert "5,000" not in comp.complaint.body


def test_5_complaint_generation_after_loss_correction(client, db, pipeline):
    """5. Complaint generation after a financial-loss correction."""
    orchestrator = Orchestrator(db, pipeline)
    conv = client.post("/api/v1/conversations", json={}).json()
    cid = conv["id"]

    orchestrator.handle(cid, MessageIn(content="Someone debited ₹5,000 from my bank account."))
    orchestrator.handle(cid, MessageIn(content="Actually, no money was lost. My account is safe."))

    comp = orchestrator.handle(cid, MessageIn(content="Please draft a formal complaint for my incident.", action="generate_complaint"))
    assert ("Amount lost: [AMOUNT LOST, IF ANY]" in comp.complaint.body or "Amount lost: No financial loss reported." in comp.complaint.body)
    assert "INR 5,000.00" not in comp.complaint.body
    assert "5,000" not in comp.complaint.body
    for req in comp.complaint.body.splitlines():
        if "Assistance requested" in req:
            break
    assert "freeze the transferred funds" not in comp.complaint.body


def test_6_guidance_after_correction_no_unsupported_loss_claims(client, db, pipeline):
    """6. Guidance after a correction must not repeat unsupported loss claims."""
    orchestrator = Orchestrator(db, pipeline)
    conv = client.post("/api/v1/conversations", json={}).json()
    cid = conv["id"]

    orchestrator.handle(cid, MessageIn(content="₹5,000 was debited from my account."))
    t2 = orchestrator.handle(cid, MessageIn(content="Actually, no money was lost. My account is safe."))

    for a in t2.guidance.immediate_actions:
        assert not re.search(r"\b(dispute the transaction|reverse.*funds|reverse.*debit|recover losses)\b", a, re.I)
    assert len(t2.guidance.immediate_actions) > 0


def test_7_hindi_and_hinglish_contradiction_handling(client, db, pipeline):
    """7. Hindi and Hinglish contradiction handling."""
    orchestrator = Orchestrator(db, pipeline)

    # Hindi
    conv_hi = client.post("/api/v1/conversations", json={}).json()
    cid_hi = conv_hi["id"]
    t1_hi = orchestrator.handle(cid_hi, MessageIn(content="मेरे खाते से 5,000 रुपये कट गए।", language="hi"))
    assert t1_hi.incident.financial_loss is True

    t2_hi = orchestrator.handle(cid_hi, MessageIn(content="दरअसल कोई पैसा नहीं कटा। मेरा खाता सुरक्षित है।", language="hi"))
    assert t2_hi.incident.financial_loss in (False, None)
    assert t2_hi.incident.amount is None
    assert "सुधार नोट कर लिया है" in t2_hi.reply
    assert any("गलती" in q or "लेन-देन" in q for q in t2_hi.follow_up_questions)

    # Hinglish
    conv_hing = client.post("/api/v1/conversations", json={}).json()
    cid_hing = conv_hing["id"]
    t1_hing = orchestrator.handle(cid_hing, MessageIn(content="Mere bank account se 5000 debit ho gaya."))
    assert t1_hing.incident.financial_loss is True

    t2_hing = orchestrator.handle(cid_hing, MessageIn(content="Actually koi paisa nahi kata. Mera account safe hai."))
    assert t2_hing.incident.financial_loss in (False, None)
    assert t2_hing.incident.amount is None
    assert "noted your correction" in t2_hing.reply.lower() or "safe" in t2_hing.reply.lower()


def test_8_preserve_urgent_banking_action_when_loss_remains_confirmed(client, db, pipeline):
    """8. Preserve urgent banking action when loss remains confirmed."""
    orchestrator = Orchestrator(db, pipeline)
    conv = client.post("/api/v1/conversations", json={}).json()
    cid = conv["id"]

    t1 = orchestrator.handle(cid, MessageIn(content="Someone debited ₹5,000 from my account via UPI."))
    assert t1.incident.financial_loss is True
    assert any("bank" in a.lower() for a in t1.guidance.immediate_actions)

    t2 = orchestrator.handle(cid, MessageIn(content="It happened yesterday on Google Pay."))
    assert t2.incident.financial_loss is True
    assert t2.incident.amount == 5000.0
    assert t2.incident.platform == "Google Pay"
    assert any("bank" in a.lower() for a in t2.guidance.immediate_actions)


def test_9_without_otp_and_contrastive_clauses_not_treated_as_loss_denial(client, db, pipeline):
    """9. Confirm 'without OTP / without permission' and contrastive clauses are not treated as loss denial."""
    orchestrator = Orchestrator(db, pipeline)
    
    # English: without OTP
    conv1 = client.post("/api/v1/conversations", json={}).json()
    t1 = orchestrator.handle(conv1["id"], MessageIn(content="Money was debited without OTP."))
    assert t1.incident.financial_loss is True

    # English: without permission + contrastive clause
    conv2 = client.post("/api/v1/conversations", json={}).json()
    t2 = orchestrator.handle(conv2["id"], MessageIn(content="Money was stolen from my account without my permission, but I haven't checked passbook yet."))
    assert t2.incident.financial_loss is True

    # Hinglish: bina OTP
    conv3 = client.post("/api/v1/conversations", json={}).json()
    t3 = orchestrator.handle(conv3["id"], MessageIn(content="bina OTP ke 2000 rupaye kat gaye."))
    assert t3.incident.financial_loss is True
    assert t3.incident.amount == 2000.0

