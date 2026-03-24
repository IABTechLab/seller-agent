# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Interactive setup wizard for the seller agent.

Two-phase model:
  Phase A (Developer / Claude Code): infra, credentials, deployment
  Phase B (Business / Claude Desktop): media kit, pricing, buyers, operations

The wizard tracks which steps are complete and guides the user through
any remaining configuration. Steps are skippable with sensible defaults.
"""

import logging
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class WizardPhase(str, Enum):
    """Which phase of setup."""

    DEVELOPER = "developer"  # Claude Code — infra/credentials
    BUSINESS = "business"  # Claude Desktop — business config


class WizardStepStatus(str, Enum):
    """Status of a wizard step."""

    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    SKIPPED = "skipped"


class WizardStep(BaseModel):
    """A single step in the setup wizard."""

    step_id: str
    phase: WizardPhase
    order: int
    title: str
    description: str
    status: WizardStepStatus = WizardStepStatus.NOT_STARTED
    is_required: bool = False  # If true, can't skip


# =============================================================================
# Developer steps (Claude Code)
# =============================================================================

DEVELOPER_STEPS = [
    WizardStep(
        step_id="d1_environment",
        phase=WizardPhase.DEVELOPER,
        order=1,
        title="Deploy & Environment",
        description="Set deployment target, ANTHROPIC_API_KEY, storage backend",
        is_required=True,
    ),
    WizardStep(
        step_id="d2_ad_server",
        phase=WizardPhase.DEVELOPER,
        order=2,
        title="Ad Server Connection",
        description="Connect GAM or FreeWheel (or skip for mock inventory)",
    ),
    WizardStep(
        step_id="d3_ssp",
        phase=WizardPhase.DEVELOPER,
        order=3,
        title="SSP Connections",
        description="Configure PubMatic, Index Exchange, Magnite (optional)",
    ),
    WizardStep(
        step_id="d4_credentials",
        phase=WizardPhase.DEVELOPER,
        order=4,
        title="Generate Operator Credentials",
        description="Create operator API key and Claude Desktop config file",
        is_required=True,
    ),
    WizardStep(
        step_id="d5_verify",
        phase=WizardPhase.DEVELOPER,
        order=5,
        title="Verify & Hand Off",
        description="Health check all connections, trigger initial sync, generate handoff file",
        is_required=True,
    ),
]


# =============================================================================
# Business steps (Claude Desktop)
# =============================================================================

BUSINESS_STEPS = [
    WizardStep(
        step_id="b1_identity",
        phase=WizardPhase.BUSINESS,
        order=1,
        title="Publisher Identity",
        description="Set publisher name, domain, inventory types",
        is_required=True,
    ),
    WizardStep(
        step_id="b2_agents",
        phase=WizardPhase.BUSINESS,
        order=2,
        title="Agent Behavior & Strategy",
        description="Configure optimization priority, negotiation style, auto-approve thresholds",
    ),
    WizardStep(
        step_id="b3_media_kit",
        phase=WizardPhase.BUSINESS,
        order=3,
        title="Media Kit Setup",
        description="Review synced inventory, create curated packages, set featured items",
    ),
    WizardStep(
        step_id="b4_pricing",
        phase=WizardPhase.BUSINESS,
        order=4,
        title="Pricing Rules & Tiers",
        description="Set rate card, floor prices, tier discounts, yield optimization",
    ),
    WizardStep(
        step_id="b5_approvals",
        phase=WizardPhase.BUSINESS,
        order=5,
        title="Approval Gates & Guard Conditions",
        description="Enable approval workflows, set required flows, timeout, auto-approve rules",
    ),
    WizardStep(
        step_id="b6_buyers",
        phase=WizardPhase.BUSINESS,
        order=6,
        title="Buyer Agent Registration",
        description="Register buyer agents, set trust levels, create API keys",
    ),
    WizardStep(
        step_id="b7_curators",
        phase=WizardPhase.BUSINESS,
        order=7,
        title="Curator Configuration",
        description="Agent Range is set up to curate deals. Add any other curation partners?",
    ),
    WizardStep(
        step_id="b8_review",
        phase=WizardPhase.BUSINESS,
        order=8,
        title="Review & Launch",
        description="Summary of all configuration, health check, go live",
        is_required=True,
    ),
]


# =============================================================================
# Wizard state
# =============================================================================


class SetupWizard:
    """Tracks wizard progress and determines next steps.

    Usage:
        wizard = SetupWizard()
        status = await wizard.get_status()
        next_step = wizard.get_next_step(WizardPhase.BUSINESS)
    """

    def __init__(self) -> None:
        self._dev_steps = {s.step_id: s.model_copy() for s in DEVELOPER_STEPS}
        self._biz_steps = {s.step_id: s.model_copy() for s in BUSINESS_STEPS}

    async def get_status(self) -> dict[str, Any]:
        """Get overall wizard status by checking actual system state."""
        settings = self._get_settings()

        # Auto-detect completed developer steps
        if settings.anthropic_api_key:
            self._dev_steps["d1_environment"].status = WizardStepStatus.COMPLETED
        if settings.gam_network_code or settings.freewheel_sh_mcp_url:
            self._dev_steps["d2_ad_server"].status = WizardStepStatus.COMPLETED
        if settings.ssp_connectors:
            self._dev_steps["d3_ssp"].status = WizardStepStatus.COMPLETED

        # Auto-detect completed business steps
        if settings.seller_organization_name != "Default Publisher":
            self._biz_steps["b1_identity"].status = WizardStepStatus.COMPLETED
        if settings.approval_gate_enabled or settings.approval_required_flows:
            self._biz_steps["b5_approvals"].status = WizardStepStatus.COMPLETED

        dev_complete = sum(1 for s in self._dev_steps.values() if s.status in (WizardStepStatus.COMPLETED, WizardStepStatus.SKIPPED))
        biz_complete = sum(1 for s in self._biz_steps.values() if s.status in (WizardStepStatus.COMPLETED, WizardStepStatus.SKIPPED))

        return {
            "developer_phase": {
                "completed": dev_complete,
                "total": len(self._dev_steps),
                "steps": [{"id": s.step_id, "title": s.title, "status": s.status.value} for s in self._dev_steps.values()],
            },
            "business_phase": {
                "completed": biz_complete,
                "total": len(self._biz_steps),
                "steps": [{"id": s.step_id, "title": s.title, "status": s.status.value} for s in self._biz_steps.values()],
            },
            "overall_complete": dev_complete + biz_complete == len(self._dev_steps) + len(self._biz_steps),
        }

    def get_next_step(self, phase: WizardPhase) -> Optional[WizardStep]:
        """Get the next incomplete step for a phase."""
        steps = self._dev_steps if phase == WizardPhase.DEVELOPER else self._biz_steps
        for step in sorted(steps.values(), key=lambda s: s.order):
            if step.status == WizardStepStatus.NOT_STARTED:
                return step
        return None

    def complete_step(self, step_id: str) -> None:
        """Mark a step as completed."""
        if step_id in self._dev_steps:
            self._dev_steps[step_id].status = WizardStepStatus.COMPLETED
        elif step_id in self._biz_steps:
            self._biz_steps[step_id].status = WizardStepStatus.COMPLETED

    def skip_step(self, step_id: str) -> bool:
        """Skip a step (if not required). Returns False if step is required."""
        steps = {**self._dev_steps, **self._biz_steps}
        if step_id in steps:
            if steps[step_id].is_required:
                return False
            steps[step_id].status = WizardStepStatus.SKIPPED
            return True
        return False

    def _get_settings(self) -> Any:
        from ..config import get_settings
        return get_settings()


def get_wizard() -> SetupWizard:
    """Get a SetupWizard instance."""
    return SetupWizard()
