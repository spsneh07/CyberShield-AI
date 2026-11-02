"""Authentication token generation, hashing and constant-time verification."""
import hashlib
import hmac
import secrets


def generate_session_token() -> str:
    """Generate a high-entropy URL-safe bearer token.
    The 'cs_' prefix distinguishes CyberShield session tokens from other API keys.
    """
    return f"cs_{secrets.token_urlsafe(32)}"


def hash_token(token: str) -> str:
    """Deterministic SHA-256 hash for database storage and index lookup.
    Never store or log the raw token.
    """
    return hashlib.sha256(token.strip().encode("utf-8")).hexdigest()


def verify_token(raw_token: str, stored_hash: str) -> bool:
    """Constant-time token verification to prevent timing side-channel attacks."""
    computed_hash = hash_token(raw_token)
    return hmac.compare_digest(computed_hash, stored_hash)

