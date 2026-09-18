# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Scheduled periodic inventory sync from ad server.

Runs as an asyncio background task tied to the FastAPI app lifecycle.
Configurable via environment variables:
  INVENTORY_SYNC_ENABLED=true
  INVENTORY_SYNC_INTERVAL_MINUTES=60
  INVENTORY_SYNC_INCLUDE_ARCHIVED=false
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

_sync_task: Optional[asyncio.Task] = None
_last_sync: Optional[str] = None
_sync_count: int = 0


async def _run_sync(include_archived: bool = False) -> dict:
    """Execute a single inventory sync cycle.

    Delegates to :class:`ProductSetupFlow.sync_from_ad_server` (AI-9,
    AI-10). This used to call the ad server client directly and discard
    the result -- items were fetched and counted but never turned into
    products or packages, so a "successful" sync persisted nothing.
    The flow already does this correctly (idempotent upsert of SYNCED
    packages + pruning of stale ones) and is the same path
    ``POST /packages/sync`` uses in production, so kicking it off here
    reuses that proven path instead of re-deriving a thinner, lossy one.

    ``include_archived`` is accepted for backward compatibility with
    callers/config, but the flow does not currently filter by archived
    status -- neither does ``POST /packages/sync``, which has the same
    pre-existing limitation. Not fixed here; tracked separately.

    Returns:
        Summary dict with sync results.
    """
    global _last_sync, _sync_count

    from ..config import get_settings as _get_settings
    from ..flows.product_setup_flow import ProductSetupFlow

    settings = _get_settings()
    logger.info("Starting scheduled inventory sync (ad_server=%s)...", settings.ad_server_type)

    try:
        flow = ProductSetupFlow()
        await flow.kickoff_async()

        _last_sync = datetime.now(timezone.utc).isoformat()
        _sync_count += 1
        logger.info(
            "Scheduled inventory sync completed (count=%d, products=%d, packages=%d)",
            _sync_count,
            len(flow.state.products),
            len(flow.state.synced_segments),
        )
        return {
            "status": "success",
            "items_synced": len(flow.state.products),
            "packages_synced": len(flow.state.synced_segments),
            "synced_at": _last_sync,
            "warnings": flow.state.warnings,
        }
    except Exception as e:
        logger.error("Scheduled inventory sync failed: %s", e)
        return {"status": "error", "error": str(e)}


async def _sync_loop(interval_minutes: int, include_archived: bool) -> None:
    """Background loop that runs sync at the configured interval."""
    interval_seconds = interval_minutes * 60
    logger.info(
        "Inventory sync scheduler started (interval=%d min, archived=%s)",
        interval_minutes,
        include_archived,
    )

    while True:
        try:
            await _run_sync(include_archived)
        except asyncio.CancelledError:
            logger.info("Inventory sync scheduler stopped")
            raise
        except Exception as e:
            logger.error("Unexpected error in sync loop: %s", e)

        await asyncio.sleep(interval_seconds)


def start_sync_scheduler() -> Optional[asyncio.Task]:
    """Start the periodic inventory sync background task.

    Reads configuration from settings. Returns the task if started,
    None if sync is disabled.
    """
    global _sync_task

    from ..config import get_settings

    settings = get_settings()

    if not settings.inventory_sync_enabled:
        logger.info("Inventory sync scheduler disabled (INVENTORY_SYNC_ENABLED=false)")
        return None

    if _sync_task and not _sync_task.done():
        logger.warning("Inventory sync scheduler already running")
        return _sync_task

    _sync_task = asyncio.create_task(
        _sync_loop(
            interval_minutes=settings.inventory_sync_interval_minutes,
            include_archived=settings.inventory_sync_include_archived,
        ),
        name="inventory-sync-scheduler",
    )
    return _sync_task


def stop_sync_scheduler() -> None:
    """Stop the periodic inventory sync background task."""
    global _sync_task

    if _sync_task and not _sync_task.done():
        _sync_task.cancel()
        logger.info("Inventory sync scheduler cancel requested")
    _sync_task = None


def get_sync_status() -> dict:
    """Get the current status of the sync scheduler."""
    return {
        "enabled": _sync_task is not None and not _sync_task.done(),
        "last_sync": _last_sync,
        "sync_count": _sync_count,
        "task_running": _sync_task is not None and not _sync_task.done() if _sync_task else False,
    }
