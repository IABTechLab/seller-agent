"""Patch for CrewAI Memory — replaces built-in memory with AgentCore Memory.

Addresses the bug where CrewAI's `memory=True` injects a `search_memory` tool
that doesn't work with Bedrock (schema not serialized, requires OpenAI embedder).

CrewAI 1.10.1 uses a `Memory` class with a `StorageBackend` that defaults to
LanceDB + OpenAI embeddings. This patch replaces the `StorageBackend` with one
that uses AgentCore's `MemoryClient` from `bedrock_agentcore.memory`.

This patch:
1. Replaces the default StorageBackend with AgentCoreStorageBackend
2. The new backend uses `memory_client.create_event()` for save and
   `memory_client.get_last_k_turns()` for search
3. Only activates when BEDROCK_AGENTCORE_MEMORY_ID env var is set

Usage:
    from patches.crewai_agentcore_memory import apply_patches
    apply_patches()  # Call once at startup

Compatible with: crewai==1.10.1
Requires: bedrock-agentcore SDK, BEDROCK_AGENTCORE_MEMORY_ID env var
"""

import logging
import os
import uuid
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

_patched = False

# Module-level memory client singleton
_memory_client = None
_memory_id = None
_actor_id = None
_session_id = None


def apply_patches(session_id: str | None = None, actor_id: str | None = None):
    """Apply AgentCore memory patches. Only activates if BEDROCK_AGENTCORE_MEMORY_ID is set.

    Args:
        session_id: Session ID from the invocation payload. Falls back to a generated UUID.
        actor_id: Actor ID (agent name). Falls back to 'buyer-agent'.
    """
    global _patched, _memory_client, _memory_id, _actor_id, _session_id

    if _patched:
        # Update session/actor if provided (new invocation with different session)
        if session_id and session_id != _session_id:
            _session_id = session_id[:100]  # AgentCore enforces 100-char max
            logger.info("Updated AgentCore memory session_id: %s", _session_id)
        if actor_id and actor_id != _actor_id:
            _actor_id = actor_id
        return

    memory_id = os.environ.get("BEDROCK_AGENTCORE_MEMORY_ID", "")
    if not memory_id:
        logger.info("BEDROCK_AGENTCORE_MEMORY_ID not set — skipping memory patch")
        return

    _memory_id = memory_id
    _actor_id = actor_id or os.environ.get("ACTOR_ID", "buyer-agent")
    _session_id = (session_id or str(uuid.uuid4()))[:100]

    try:
        from bedrock_agentcore.memory import MemoryClient

        region = os.environ.get("AWS_REGION", "us-west-2")
        _memory_client = MemoryClient(region_name=region)
        logger.info(
            "AgentCore MemoryClient initialized (memory_id=%s, region=%s)",
            _memory_id, region,
        )
    except ImportError:
        logger.warning("bedrock_agentcore.memory not available — skipping memory patch")
        return
    except Exception as exc:
        logger.error("Failed to create AgentCore MemoryClient: %s", exc)
        return

    _patch_storage_backend()
    _patched = True
    logger.info(
        "CrewAI AgentCore memory patch applied (memory_id=%s, actor=%s, session=%s)",
        _memory_id, _actor_id, _session_id,
    )


def _patch_storage_backend():
    """Monkey-patch CrewAI's default StorageBackend with AgentCore-backed implementation.

    CrewAI 1.10.1 memory architecture:
        Memory(llm, storage, embedder, ...) → self._storage (LanceDB default)
        Memory.remember(content) → self._storage.save(records)
        Memory.recall(query) → self._storage.search(query_embedding)

    Strategy: Patch Memory.__init__ to inject our AgentCoreStorageBackend as the
    `storage` parameter and a no-op embedder, bypassing LanceDB and OpenAI entirely.
    """
    try:
        from crewai.memory.unified_memory import Memory
    except ImportError:
        logger.warning("CrewAI memory modules not available — skipping patch")
        return

    original_memory_init = Memory.__init__

    def _patched_memory_init(self, *args, **kwargs):
        """Patched Memory.__init__ — injects AgentCore storage backend."""
        # Force our backend as the storage parameter
        kwargs["storage"] = AgentCoreStorageBackend()
        # Use a no-op embedder to avoid OpenAI dependency
        if "embedder" not in kwargs or kwargs.get("embedder") is None:
            kwargs["embedder"] = _NoOpEmbedder()
        # Use Bedrock LLM for memory analysis if available
        if "llm" not in kwargs or kwargs.get("llm") == "gpt-4o-mini":
            bedrock_model = os.environ.get(
                "MEMORY_LLM_MODEL",
                os.environ.get("DEFAULT_LLM_MODEL", "bedrock/us.amazon.nova-lite-v1:0"),
            )
            kwargs["llm"] = bedrock_model
        try:
            original_memory_init(self, *args, **kwargs)
            # Set read_only to prevent RememberTool injection — Nova Lite can't handle
            # the RememberSchema correctly. Memory is stored via AgentCore's
            # ShortTermMemoryHook instead (passive, no LLM tool call needed).
            self._read_only = True
            logger.debug("Memory initialized with AgentCore storage backend (read_only=True)")
        except Exception as exc:
            logger.warning("Memory.__init__ failed even with patched storage: %s", exc)
            # Last resort: set minimum attrs so CrewAI doesn't crash
            import threading
            from concurrent.futures import ThreadPoolExecutor
            if not hasattr(self, '_save_pool'):
                self._save_pool = ThreadPoolExecutor(max_workers=1)
            if not hasattr(self, '_pending_lock'):
                self._pending_lock = threading.Lock()
            if not hasattr(self, '_pending_saves'):
                self._pending_saves = []
            if not hasattr(self, '_storage'):
                self._storage = AgentCoreStorageBackend()
            if not hasattr(self, '_read_only'):
                self._read_only = False

    Memory.__init__ = _patched_memory_init
    logger.info("Patched Memory.__init__ to use AgentCore storage backend")


class _NoOpEmbedder:
    """No-op embedder that returns zero vectors.

    AgentCore handles embeddings server-side, so we don't need a local
    embedder. This satisfies CrewAI's embedder interface without requiring
    an OpenAI API key.
    """

    def embed(self, texts: "list[str]") -> "list[list[float]]":
        """Return zero vectors for any input texts."""
        return [[0.0] * 384 for _ in texts]

    def __call__(self, texts: "list[str]") -> "list[list[float]]":
        """Support callable interface."""
        return self.embed(texts)


class AgentCoreStorageBackend:
    """StorageBackend implementation using AgentCore Memory APIs.

    Implements the subset of StorageBackend methods that CrewAI's Memory
    class actually calls:
    - save(records) → create_event
    - search(query_embedding, ...) → get_last_k_turns (embedding-free)
    - reset() → no-op (AgentCore manages TTL)
    - delete() → no-op
    - count() → 0

    AgentCore handles embeddings server-side, so we don't need a local
    embedder. The search method ignores the query_embedding parameter
    and uses get_last_k_turns for recency-based recall instead.
    """

    def __init__(self):
        import threading
        self.write_lock = threading.Lock()
        self.read_lock = threading.Lock()

    def save(self, records: list) -> None:
        """Store memory records to AgentCore via create_event."""
        if not _memory_client or not _memory_id:
            return

        for record in records:
            try:
                # Extract content from MemoryRecord
                content = getattr(record, "content", str(record))
                if not content or len(str(content).strip()) < 3:
                    continue

                # Skip tool-related content
                content_str = str(content)
                if any(m in content_str for m in ["toolUse", "toolResult", "tooluse_"]):
                    continue

                # Truncate to 9000 chars (same as Strands pattern)
                content_str = content_str[:9000]

                _memory_client.create_event(
                    memory_id=_memory_id,
                    actor_id=_actor_id,
                    session_id=_session_id,
                    messages=[(content_str, "ASSISTANT")],
                )
                logger.debug("Stored memory record to AgentCore (%d chars)", len(content_str))
            except Exception as exc:
                logger.warning("Failed to store memory record to AgentCore: %s", exc)

    async def asave(self, records: list) -> None:
        """Async version — delegates to sync save."""
        self.save(records)

    def search(
        self,
        query_embedding: "list[float]",
        scope_prefix: str | None = None,
        categories: "list[str] | None" = None,
        metadata_filter: "dict[str, Any] | None" = None,
        limit: int = 10,
        min_score: float = 0.0,
    ) -> "list[tuple[Any, float]]":
        """Retrieve recent memory from AgentCore via get_last_k_turns.

        AgentCore handles embeddings server-side, so we ignore query_embedding
        and use recency-based retrieval instead. Returns results as
        (MemoryRecord-like, score) tuples to match the StorageBackend interface.
        """
        if not _memory_client or not _memory_id:
            return []

        try:
            recent_turns = _memory_client.get_last_k_turns(
                memory_id=_memory_id,
                actor_id=_actor_id,
                session_id=_session_id,
                k=min(limit, 5),
                branch_name="main",
                max_results=limit,
            )

            if not recent_turns:
                return []

            results = []
            for turn in recent_turns:
                for msg in turn:
                    content = msg.get("content", {})
                    text = content.get("text", str(content)) if isinstance(content, dict) else str(content)

                    # Skip tool messages
                    if any(m in text for m in ["toolUse", "toolResult", "tooluse_"]):
                        continue

                    if text and len(text.strip()) >= 3:
                        # Return as (record-like object, score) tuple
                        record = _SimpleRecord(content=text, scope=scope_prefix or "/")
                        results.append((record, 0.9))  # High relevance score for recent context

            logger.debug("Retrieved %d memory records from AgentCore", len(results))
            return results[:limit]

        except Exception as exc:
            logger.warning("Failed to retrieve memory from AgentCore: %s", exc)
            return []

    async def asearch(self, query_embedding, **kwargs) -> "list[tuple[Any, float]]":
        """Async version — delegates to sync search."""
        return self.search(query_embedding, **kwargs)

    def delete(self, **kwargs) -> int:
        """No-op — AgentCore manages memory lifecycle via TTL."""
        return 0

    async def adelete(self, **kwargs) -> int:
        """Async no-op."""
        return 0

    def reset(self, scope_prefix: str | None = None) -> None:
        """No-op — AgentCore manages memory lifecycle."""
        pass

    def count(self, scope_prefix: str | None = None) -> int:
        """Return 0 — we don't track local count."""
        return 0

    def get_record(self, record_id: str):
        """Not supported — return None."""
        return None

    def list_records(self, **kwargs) -> list:
        """Not supported — return empty list."""
        return []

    def list_scopes(self, parent: str = "/") -> list:
        """Return single scope."""
        return ["/agentcore/"]

    def list_categories(self, **kwargs) -> dict:
        """Return empty categories."""
        return {}

    def get_scope_info(self, scope: str):
        """Not supported."""
        return None

    def update(self, record) -> None:
        """Not supported — AgentCore is append-only."""
        pass


class _SimpleRecord:
    """Minimal record object matching what CrewAI's Memory.recall expects."""

    def __init__(self, content: str, scope: str = "/"):
        self.id = str(uuid.uuid4())
        self.content = content
        self.scope = scope
        self.categories = []
        self.metadata = {}
        self.importance = 0.5
        self.embedding = []
        # Use naive datetime (no timezone) to match CrewAI's internal comparisons
        self.created_at = datetime.now()
        self.updated_at = self.created_at
        self.source = "agentcore"
        self.private = False
        self.agent_role = None
