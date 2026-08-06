"""High-entropy integration credentials stored as one-way digests."""

import hashlib
import hmac
import secrets


def generate_integration_secret() -> str:
    return secrets.token_urlsafe(32)


def hash_integration_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def verify_integration_secret(secret: str, expected_hash: str) -> bool:
    return hmac.compare_digest(hash_integration_secret(secret), expected_hash)
