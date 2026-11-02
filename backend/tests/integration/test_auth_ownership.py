"""Comprehensive automated tests for authentication, token validation, and multi-tenant isolation."""
import json
import uuid
import pytest

from app.models.entities import Conversation, Incident, Message
from tests.conftest import DEMO


def test_missing_token_returns_401(unauthenticated_client):
    c = unauthenticated_client
    # GET protected conversation
    r = c.get("/api/v1/conversations/12345678-1234-1234-1234-123456789abc")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"

    # POST message
    r = c.post("/api/v1/conversations/12345678-1234-1234-1234-123456789abc/messages", json={"content": "hello"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"

    # Streaming message
    r = c.post("/api/v1/conversations/12345678-1234-1234-1234-123456789abc/messages/stream", json={"content": "hello"})
    assert r.status_code == 401

    # DELETE conversation
    r = c.delete("/api/v1/conversations/12345678-1234-1234-1234-123456789abc")
    assert r.status_code == 401

    # POST incidents/analyze
    r = c.post("/api/v1/incidents/analyze", json={"description": DEMO})
    assert r.status_code == 401

    # GET incident
    r = c.get("/api/v1/incidents/12345678-1234-1234-1234-123456789abc")
    assert r.status_code == 401

    # POST incidents sub-resources without token -> 401
    r = c.post("/api/v1/incidents/12345678-1234-1234-1234-123456789abc/guidance")
    assert r.status_code == 401
    r = c.post("/api/v1/incidents/12345678-1234-1234-1234-123456789abc/complaint")
    assert r.status_code == 401
    r = c.post("/api/v1/incidents/12345678-1234-1234-1234-123456789abc/awareness")
    assert r.status_code == 401

    # POST feedback
    r = c.post("/api/v1/feedback", json={"message_id": "m1", "rating": 1})
    assert r.status_code == 401


def test_invalid_or_malformed_token_returns_401(unauthenticated_client):
    c = unauthenticated_client
    bad_headers = [
        {"Authorization": "Bearer invalid_token_value_that_does_not_exist"},
        {"Authorization": "Bearer "},
        {"Authorization": "Basic dXNlcjpwYXNz"},
        {"X-Session-Token": "cs_nonexistent_token_1234567890"},
    ]
    for h in bad_headers:
        r = c.get("/api/v1/conversations/12345678-1234-1234-1234-123456789abc", headers=h)
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "unauthorized"


def test_first_time_conversation_creation_provisions_session_token(unauthenticated_client):
    r = unauthenticated_client.post("/api/v1/conversations", json={"title": "First Conv"})
    assert r.status_code == 201
    data = r.json()
    assert data["id"]
    assert data["session_token"] is not None
    assert data["session_token"].startswith("cs_")

    # Second creation with that token reuses identity and does not re-expose raw token
    r2 = unauthenticated_client.post(
        "/api/v1/conversations",
        json={"title": "Second Conv"},
        headers={"Authorization": f"Bearer {data['session_token']}"},
    )
    assert r2.status_code == 201
    data2 = r2.json()
    assert data2["id"]
    assert data2["session_token"] is None


def test_cross_user_access_blocked_with_403(create_user_client):
    user_a = create_user_client("User A")
    user_b = create_user_client("User B")

    # User A creates a conversation and incident
    res = user_a.post("/api/v1/conversations", json={"title": "User A Private Incident"}).json()
    cid = res["id"]
    msg_res = user_a.post(f"/api/v1/conversations/{cid}/messages", json={"content": DEMO}).json()
    iid = msg_res["incident_id"]

    # 1. User B cannot GET User A's conversation
    r = user_b.get(f"/api/v1/conversations/{cid}")
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "forbidden"

    # 2. User B cannot POST messages to User A's conversation
    r = user_b.post(f"/api/v1/conversations/{cid}/messages", json={"content": "Malicious intrusion attempt"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "forbidden"

    # 3. User B cannot stream messages from User A's conversation
    r = user_b.post(f"/api/v1/conversations/{cid}/messages/stream", json={"content": "Stream intrusion"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "forbidden"

    # 4. User B cannot DELETE User A's conversation
    r = user_b.delete(f"/api/v1/conversations/{cid}")
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "forbidden"

    # 4b. Confirm User A can still retrieve their conversation intact after unauthorized delete attempt
    r_check = user_a.get(f"/api/v1/conversations/{cid}")
    assert r_check.status_code == 200
    assert r_check.json()["id"] == cid

    # 5. User B cannot GET User A's incident
    r = user_b.get(f"/api/v1/incidents/{iid}")
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "forbidden"

    # 6. User B cannot generate guidance for User A's incident
    r = user_b.post(f"/api/v1/incidents/{iid}/guidance")
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "forbidden"

    # 7. User B cannot generate a complaint for User A's incident
    r = user_b.post(f"/api/v1/incidents/{iid}/complaint")
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "forbidden"

    # 8. User B cannot generate awareness for User A's incident
    r = user_b.post(f"/api/v1/incidents/{iid}/awareness")
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "forbidden"


def test_feedback_cross_user_isolation(create_user_client, db):
    user_a = create_user_client("User A")
    user_b = create_user_client("User B")

    # User A creates a conversation and posts a message
    cid = user_a.post("/api/v1/conversations", json={}).json()["id"]
    user_a.post(f"/api/v1/conversations/{cid}/messages", json={"content": DEMO})

    # Retrieve User A's message id
    conv_data = user_a.get(f"/api/v1/conversations/{cid}").json()
    msg_id = conv_data["messages"][0]["id"]

    # User B attempts to post feedback on User A's message -> 403
    r = user_b.post("/api/v1/feedback", json={"message_id": msg_id, "rating": 1, "comment": "Spoofed feedback"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "forbidden"

    # User A posts feedback on their own message -> 201
    r_ok = user_a.post("/api/v1/feedback", json={"message_id": msg_id, "rating": 1, "comment": "Legitimate feedback"})
    assert r_ok.status_code == 201


def test_standalone_analyze_assigns_user_and_enforces_ownership(create_user_client):
    user_a = create_user_client("User A")
    user_b = create_user_client("User B")

    # User A runs standalone analysis
    res = user_a.post("/api/v1/incidents/analyze", json={"description": DEMO})
    assert res.status_code == 201
    iid = res.json()["id"]

    # User A can access it
    assert user_a.get(f"/api/v1/incidents/{iid}").status_code == 200

    # User B cannot access it
    assert user_b.get(f"/api/v1/incidents/{iid}").status_code == 403


def test_legacy_records_with_null_user_id_are_quarantined(create_user_client, db):
    user = create_user_client("Active User")

    # Seed a legacy conversation created before auth (user_id = None)
    legacy_conv_id = str(uuid.uuid4())
    legacy_conv = Conversation(
        id=legacy_conv_id,
        title="Legacy Unclaimed Conversation",
        user_id=None,
    )
    db.add(legacy_conv)

    # Seed a legacy incident created before auth (user_id = None)
    legacy_inc_id = str(uuid.uuid4())
    legacy_inc = Incident(
        id=legacy_inc_id,
        description="Legacy incident description",
        user_id=None,
    )
    db.add(legacy_inc)
    db.commit()

    # 1. Authenticated user cannot GET legacy conversation
    r = user.get(f"/api/v1/conversations/{legacy_conv_id}")
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "forbidden"

    # 2. Authenticated user cannot POST messages to legacy conversation
    r = user.post(f"/api/v1/conversations/{legacy_conv_id}/messages", json={"content": "Malicious attempt"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "forbidden"

    # 3. Authenticated user cannot DELETE legacy conversation
    r = user.delete(f"/api/v1/conversations/{legacy_conv_id}")
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "forbidden"

    # 4. Authenticated user cannot GET legacy incident
    r = user.get(f"/api/v1/incidents/{legacy_inc_id}")
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "forbidden"

    # 5. Authenticated user cannot call guidance/complaint/awareness on legacy incident
    assert user.post(f"/api/v1/incidents/{legacy_inc_id}/guidance").status_code == 403
    assert user.post(f"/api/v1/incidents/{legacy_inc_id}/complaint").status_code == 403
    assert user.post(f"/api/v1/incidents/{legacy_inc_id}/awareness").status_code == 403

    # 6. Verify database records remain intact and user_id is still NULL
    db.expire_all()
    conv_db = db.query(Conversation).filter_by(id=legacy_conv_id).first()
    assert conv_db is not None
    assert conv_db.user_id is None
    assert conv_db.title == "Legacy Unclaimed Conversation"

    inc_db = db.query(Incident).filter_by(id=legacy_inc_id).first()
    assert inc_db is not None
    assert inc_db.user_id is None


def test_unauthorized_requests_do_not_modify_or_delete_records(create_user_client, unauthenticated_client, db):
    user_a = create_user_client("User A")
    user_b = create_user_client("User B")

    # User A creates a conversation and posts 1 message
    c_res = user_a.post("/api/v1/conversations", json={"title": "Immutable User A Conv"}).json()
    cid = c_res["id"]
    msg_res = user_a.post(f"/api/v1/conversations/{cid}/messages", json={"content": DEMO}).json()
    iid = msg_res["incident_id"]

    # Verify initial state: messages exist
    initial_conv = user_a.get(f"/api/v1/conversations/{cid}").json()
    initial_msg_count = len(initial_conv["messages"])
    assert initial_msg_count >= 1

    # Attack 1: User B attempts DELETE -> 403
    r = user_b.delete(f"/api/v1/conversations/{cid}")
    assert r.status_code == 403

    # Attack 2: Unauthenticated client attempts DELETE -> 401
    r = unauthenticated_client.delete(f"/api/v1/conversations/{cid}")
    assert r.status_code == 401

    # Attack 3: User B attempts POST message -> 403
    r = user_b.post(f"/api/v1/conversations/{cid}/messages", json={"content": "Malicious payload from B"})
    assert r.status_code == 403

    # Attack 4: Unauthenticated client attempts POST message -> 401
    r = unauthenticated_client.post(f"/api/v1/conversations/{cid}/messages", json={"content": "Unauthenticated payload"})
    assert r.status_code == 401

    # Verify User A's conversation is completely unmodified
    surviving_conv = user_a.get(f"/api/v1/conversations/{cid}").json()
    assert surviving_conv["id"] == cid
    assert surviving_conv["title"] == "Immutable User A Conv"
    assert len(surviving_conv["messages"]) == initial_msg_count
    for m in surviving_conv["messages"]:
        assert "Malicious payload from B" not in m["content"]
        assert "Unauthenticated payload" not in m["content"]

    # Direct database verification
    db.expire_all()
    conv_db = db.query(Conversation).filter_by(id=cid).first()
    assert conv_db is not None
    assert conv_db.user_id == user_a.user.id
    assert len(conv_db.messages) == initial_msg_count

    inc_db = db.query(Incident).filter_by(id=iid).first()
    assert inc_db is not None
    assert inc_db.user_id == user_a.user.id


def test_cross_user_incident_subresources_blocked_and_unmodified(create_user_client, db):
    # 1. Create an incident owned by User A
    user_a = create_user_client("User A")
    res = user_a.post("/api/v1/incidents/analyze", json={"description": DEMO})
    assert res.status_code == 201
    iid = res.json()["id"]

    # Record snapshot of incident and related state before User B's attempts
    db.expire_all()
    inc_before = db.query(Incident).filter_by(id=iid).first()
    assert inc_before is not None
    assert inc_before.user_id == user_a.user.id
    before_classifications_count = len(inc_before.classifications)
    before_updated_at = inc_before.updated_at
    before_description = inc_before.description
    before_data = dict(inc_before.data)

    # 2. Authenticate as User B using a different valid session token
    user_b = create_user_client("User B")
    assert user_b.user.id != user_a.user.id
    assert user_b.token != user_a.token

    # 3 & 4. Attempt to access User A's incident via sub-resource endpoints; assert all return 403 Forbidden
    r_guidance = user_b.post(f"/api/v1/incidents/{iid}/guidance")
    assert r_guidance.status_code == 403
    assert r_guidance.json()["error"]["code"] == "forbidden"

    r_complaint = user_b.post(f"/api/v1/incidents/{iid}/complaint")
    assert r_complaint.status_code == 403
    assert r_complaint.json()["error"]["code"] == "forbidden"

    r_awareness = user_b.post(f"/api/v1/incidents/{iid}/awareness")
    assert r_awareness.status_code == 403
    assert r_awareness.json()["error"]["code"] == "forbidden"

    # 5. Verify that the incident and its associated records remain unchanged after these attempts
    db.expire_all()
    inc_after = db.query(Incident).filter_by(id=iid).first()
    assert inc_after is not None
    assert inc_after.user_id == user_a.user.id
    assert inc_after.description == before_description
    assert len(inc_after.classifications) == before_classifications_count
    assert inc_after.data == before_data
    assert inc_after.updated_at == before_updated_at

    # Verify User A can still retrieve their own incident intact
    r_a_get = user_a.get(f"/api/v1/incidents/{iid}")
    assert r_a_get.status_code == 200
    assert r_a_get.json()["id"] == iid



