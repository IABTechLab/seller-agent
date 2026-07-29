# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""API Key models for buyer agent authentication.

Lightweight API key authentication for buyer agents.
Each key maps to a BuyerIdentity (seat, agency, advertiser)
and is stored as a SHA-256 hash in the KV store. The plaintext
key is returned exactly once at creation time and never stored.

Key format: ask_live_{token} (ad-seller-key)
Storage: api_key:{sha256_hex} → ApiKeyRecord JSON
"""

import hashlib
import secrets
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from .buyer_identity import BuyerIdentity

API_KEY_PREFIX = "ask_live_"
API_KEY_STORAGE_PREFIX = "api_key:"
API_KEY_INDEX_PREFIX = "api_key_index:"


class ApiKeyRole(str, Enum):
    """Role attached to an API key.

    - BUYER: buyer-agent credential; grants tiered data access
      (seat/agency/advertiser pricing) but no control-plane rights.
    - OPERATOR: publisher operator credential; required for admin
      endpoints (key management, rate card, registry trust, packages,
      inventory sync, deal push/distribute).
    """

    BUYER = "buyer"
    OPERATOR = "operator"


def generate_api_key() -> str:
    """Generate a new API key with prefix.

    Returns the full key (shown once to the operator).
    256 bits of entropy via secrets.token_urlsafe(32).
    """
    token = secrets.token_urlsafe(32)
    return f"{API_KEY_PREFIX}{token}"


def hash_api_key(full_key: str) -> str:
    """SHA-256 hash of the full API key for storage lookup."""
    return hashlib.sha256(full_key.encode()).hexdigest()


class ApiKeyRecord(BaseModel):
    """Stored record for an issued API key.

    Stored at key ``api_key:{sha256_hash}`` in the KV store.
    The plaintext key is never stored — only the hash.
    """

    key_id: str  # Short human-readable ID, e.g. "key-a1b2c3d4"
    key_hash: str  # SHA-256 hex digest of the full key
    key_prefix_hint: str  # First 12 chars for identification, e.g. "ask_live_Ab..."

    # The identity this key authenticates
    identity: BuyerIdentity

    # Role: pre-existing records deserialize as "buyer" (safe default —
    # never silently promotes an old key to operator).
    role: ApiKeyRole = ApiKeyRole.BUYER

    # Metadata
    label: str = ""  # Human-readable label, e.g. "Acme Agency production key"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: Optional[datetime] = None  # None = never expires
    revoked: bool = False
    revoked_at: Optional[datetime] = None

    # Usage tracking
    last_used_at: Optional[datetime] = None
    use_count: int = 0

    @property
    def is_expired(self) -> bool:
        """Whether the key has expired."""
        if self.expires_at is None:
            return False
        return datetime.utcnow() > self.expires_at

    @property
    def is_active(self) -> bool:
        """Whether the key is valid for authentication."""
        return not self.revoked and not self.is_expired


class ApiKeyCreateRequest(BaseModel):
    """Request to create a new API key (operator-facing)."""

    # Identity fields for the buyer this key authenticates
    seat_id: Optional[str] = None
    seat_name: Optional[str] = None
    dsp_platform: Optional[str] = None
    agency_id: Optional[str] = None
    agency_name: Optional[str] = None
    agency_holding_company: Optional[str] = None
    advertiser_id: Optional[str] = None
    advertiser_name: Optional[str] = None

    # Key metadata
    role: ApiKeyRole = ApiKeyRole.BUYER
    label: str = ""
    expires_in_days: Optional[int] = None  # None = never expires


class ApiKeyCreateResponse(BaseModel):
    """Response after creating an API key.

    The ``api_key`` field contains the full key and is shown
    ONLY in this response. It cannot be retrieved again.
    """

    key_id: str
    api_key: str  # Full key, shown once
    identity: BuyerIdentity
    role: ApiKeyRole = ApiKeyRole.BUYER
    label: str
    expires_at: Optional[datetime] = None
    warning: str = "Store this key securely. It will not be shown again."


class ApiKeyInfo(BaseModel):
    """Public info about an API key (no secret material)."""

    key_id: str
    key_prefix_hint: str
    identity: BuyerIdentity
    role: ApiKeyRole = ApiKeyRole.BUYER
    label: str
    created_at: datetime
    expires_at: Optional[datetime] = None
    revoked: bool = False
    is_active: bool = True
    last_used_at: Optional[datetime] = None
    use_count: int = 0
