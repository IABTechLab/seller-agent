# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""API Key management service.

Handles creation, lookup, revocation, and listing of API keys.
Uses the existing StorageBackend KV pattern.

Storage layout:
    api_key:{sha256_hash}     → ApiKeyRecord JSON (O(1) lookup)
    api_key_index:{key_id}    → sha256_hash (management by key_id)
    api_key_list              → [key_id, ...] (enumeration)
"""

import logging
import uuid
from datetime import datetime, timedelta
from typing import Optional

from ..models.api_key import (
    API_KEY_INDEX_PREFIX,
    API_KEY_STORAGE_PREFIX,
    ApiKeyCreateRequest,
    ApiKeyCreateResponse,
    ApiKeyInfo,
    ApiKeyRecord,
    generate_api_key,
    hash_api_key,
)
from ..models.buyer_identity import BuyerIdentity
from ..storage.base import StorageBackend

logger = logging.getLogger(__name__)


class ApiKeyService:
    """Manages API keys for buyer authentication."""

    def __init__(self, storage: StorageBackend):
        self._storage = storage

    async def create_key(self, request: ApiKeyCreateRequest) -> ApiKeyCreateResponse:
        """Issue a new API key for a buyer identity.

        Returns the full key exactly once. The key is hashed
        before storage and can never be retrieved again.
        """
        full_key = generate_api_key()
        key_hash = hash_api_key(full_key)
        key_id = f"key-{uuid.uuid4().hex[:8]}"

        identity = BuyerIdentity(
            seat_id=request.seat_id,
            seat_name=request.seat_name,
            dsp_platform=request.dsp_platform,
            agency_id=request.agency_id,
            agency_name=request.agency_name,
            agency_holding_company=request.agency_holding_company,
            advertiser_id=request.advertiser_id,
            advertiser_name=request.advertiser_name,
        )

        expires_at = None
        if request.expires_in_days is not None:
            expires_at = datetime.utcnow() + timedelta(days=request.expires_in_days)

        record = ApiKeyRecord(
            key_id=key_id,
            key_hash=key_hash,
            key_prefix_hint=full_key[:12] + "...",
            identity=identity,
            role=request.role,
            label=request.label,
            expires_at=expires_at,
        )

        # Store by hash for O(1) lookup during auth
        await self._storage.set(
            f"{API_KEY_STORAGE_PREFIX}{key_hash}",
            record.model_dump(mode="json"),
        )

        # Maintain key_id → key_hash index for management operations
        await self._storage.set(
            f"{API_KEY_INDEX_PREFIX}{key_id}",
            key_hash,
        )

        # Maintain list of all key IDs for enumeration
        all_keys = await self._storage.get("api_key_list") or []
        all_keys.append(key_id)
        await self._storage.set("api_key_list", all_keys)

        logger.info(
            "API key %s created for %s (role: %s, label: %s)",
            key_id,
            identity.identity_level.value,
            request.role.value,
            request.label,
        )

        return ApiKeyCreateResponse(
            key_id=key_id,
            api_key=full_key,
            identity=identity,
            role=request.role,
            label=request.label,
            expires_at=expires_at,
        )

    async def validate_key(self, full_key: str) -> Optional[ApiKeyRecord]:
        """Look up and validate an API key.

        Returns:
            ApiKeyRecord if valid, None if not found.

        Raises:
            ValueError: If key is found but revoked or expired.
        """
        key_hash = hash_api_key(full_key)
        data = await self._storage.get(f"{API_KEY_STORAGE_PREFIX}{key_hash}")

        if data is None:
            return None

        record = ApiKeyRecord(**data)

        if record.revoked:
            raise ValueError(f"API key {record.key_id} has been revoked")

        if record.is_expired:
            raise ValueError(f"API key {record.key_id} has expired")

        # Update usage stats
        record.last_used_at = datetime.utcnow()
        record.use_count += 1
        await self._storage.set(
            f"{API_KEY_STORAGE_PREFIX}{key_hash}",
            record.model_dump(mode="json"),
        )

        return record

    async def get_key_info(self, key_id: str) -> Optional[ApiKeyInfo]:
        """Get info about a key by its key_id (not the secret)."""
        key_hash = await self._storage.get(f"{API_KEY_INDEX_PREFIX}{key_id}")
        if not key_hash:
            return None

        data = await self._storage.get(f"{API_KEY_STORAGE_PREFIX}{key_hash}")
        if not data:
            return None

        record = ApiKeyRecord(**data)
        return ApiKeyInfo(
            key_id=record.key_id,
            key_prefix_hint=record.key_prefix_hint,
            identity=record.identity,
            role=record.role,
            label=record.label,
            created_at=record.created_at,
            expires_at=record.expires_at,
            revoked=record.revoked,
            is_active=record.is_active,
            last_used_at=record.last_used_at,
            use_count=record.use_count,
        )

    async def list_keys(self) -> list[ApiKeyInfo]:
        """List all API keys (metadata only, no secrets)."""
        all_key_ids = await self._storage.get("api_key_list") or []
        results = []
        for key_id in all_key_ids:
            info = await self.get_key_info(key_id)
            if info:
                results.append(info)
        return results

    async def revoke_key(self, key_id: str) -> bool:
        """Revoke an API key. Returns True if found and revoked."""
        key_hash = await self._storage.get(f"{API_KEY_INDEX_PREFIX}{key_id}")
        if not key_hash:
            return False

        data = await self._storage.get(f"{API_KEY_STORAGE_PREFIX}{key_hash}")
        if not data:
            return False

        record = ApiKeyRecord(**data)
        record.revoked = True
        record.revoked_at = datetime.utcnow()

        await self._storage.set(
            f"{API_KEY_STORAGE_PREFIX}{key_hash}",
            record.model_dump(mode="json"),
        )

        logger.info("API key %s revoked", key_id)
        return True
