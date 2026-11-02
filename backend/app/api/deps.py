from fastapi import Depends, Header, Request
from sqlalchemy.orm import Session

from app.core.auth import generate_session_token, hash_token
from app.core.config import Settings, get_settings
from app.core.db import get_db
from app.core.errors import ForbiddenError, NotFoundError, UnauthorizedError
from app.models.entities import Conversation, Incident, User
from app.repositories.conversations import ConversationRepository
from app.repositories.users import UserRepository
from app.services.ai.base import EmbeddingProvider, LLMProvider
from app.services.ai.factory import get_embeddings, get_llm
from app.services.pipeline import Pipeline


def get_pipeline(
    db: Session = Depends(get_db),
    llm: LLMProvider = Depends(get_llm),
    embeddings: EmbeddingProvider = Depends(get_embeddings),
    settings: Settings = Depends(get_settings),
) -> Pipeline:
    return Pipeline(db, llm, embeddings, settings)


def _extract_token(authorization: str | None = None, x_session_token: str | None = None) -> str | None:
    if authorization:
        parts = authorization.strip().split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1].strip()
        if len(parts) == 1:
            return parts[0].strip()
    if x_session_token:
        return x_session_token.strip()
    return None


def get_current_user(
    db: Session = Depends(get_db),
    authorization: str | None = Header(None, alias="Authorization"),
    x_session_token: str | None = Header(None, alias="X-Session-Token"),
) -> User:
    token = _extract_token(authorization, x_session_token)
    if not token:
        raise UnauthorizedError("Authentication required")
    th = hash_token(token)
    user = UserRepository(db).get_by_token_hash(th)
    if user is None:
        raise UnauthorizedError("Invalid authentication token")
    return user


def get_optional_user(
    db: Session = Depends(get_db),
    authorization: str | None = Header(None, alias="Authorization"),
    x_session_token: str | None = Header(None, alias="X-Session-Token"),
) -> tuple[User, str | None]:
    """Resolves existing user from bearer token or provisions a new user + session token."""
    token = _extract_token(authorization, x_session_token)
    user_repo = UserRepository(db)
    if token:
        th = hash_token(token)
        user = user_repo.get_by_token_hash(th)
        if user is None:
            raise UnauthorizedError("Invalid authentication token")
        return user, None

    raw_token = generate_session_token()
    th = hash_token(raw_token)
    user = user_repo.create_with_token(token_hash=th)
    return user, raw_token


def get_authorized_conversation(
    conversation_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Conversation:
    conv = ConversationRepository(db).get(conversation_id)
    if conv is None:
        raise NotFoundError("Conversation")
    if conv.user_id != current_user.id:
        raise ForbiddenError("You do not have permission to access this conversation")
    return conv


def get_authorized_incident(
    incident_id: str,
    p: Pipeline = Depends(get_pipeline),
    current_user: User = Depends(get_current_user),
) -> Incident:
    incident = p.incidents.get(incident_id)
    if incident is None:
        raise NotFoundError("Incident")
    is_owner = incident.user_id == current_user.id
    if not is_owner and incident.conversation and incident.conversation.user_id == current_user.id:
        is_owner = True
    if not is_owner:
        raise ForbiddenError("You do not have permission to access this incident")
    return incident
