"""Conftest for AgentCore runtime integration tests.

Registers custom pytest CLI options for AWS profile and runtime ARN, and
provides a STALE-PROOF runtime-ARN resolver.

Why live resolution: an ARN recorded at deploy time (in ``.bedrock_agentcore.yaml``
or hardcoded in a test) goes stale the moment a runtime is deleted + recreated —
which the immutable-VPC / immutable-header-allowlist constraints force. A test
reading the snapshot then invokes a dead ARN and either 404s or, worse, skips
silently. ``resolve_live_runtime_arn`` reads the AWS control plane instead:
it matches ``agentRuntimeName`` and returns the CURRENT ARN, so it can never be
stale. The yaml is kept only as an offline fallback (no creds / no boto3).
"""

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def pytest_addoption(parser):
    """Add AgentCore-specific CLI options."""
    parser.addoption("--profile", action="store", default=None, help="AWS CLI profile")
    parser.addoption("--runtime-arn", action="store", default=None, help="Runtime ARN override")
    parser.addoption(
        "--agent-name", action="store", default=None, help="Agent name in .bedrock_agentcore.yaml"
    )


def _arn_from_yaml(agent_name: str) -> str:
    """Offline fallback: read the ARN snapshot from .bedrock_agentcore.yaml."""
    yaml_path = Path(__file__).resolve().parents[3] / ".bedrock_agentcore.yaml"
    if not yaml_path.exists():
        return ""
    try:
        import yaml
    except ImportError:
        return ""
    with open(yaml_path) as f:
        cfg = yaml.safe_load(f) or {}
    agent = cfg.get("agents", {}).get(agent_name, {})
    return agent.get("bedrock_agentcore", {}).get("agent_arn", "") or ""


def _arn_from_control_plane(agent_name: str, region: str) -> tuple[str, str]:
    """Resolve the CURRENT ARN + status for ``agent_name`` via ListAgentRuntimes.

    Returns ``(arn, status)``; ``("", "")`` when boto3/creds are unavailable or
    no runtime matches the name. Name-based (not position-based) so it never
    grabs the wrong runtime, and it returns the live status so callers can fail
    loudly on a not-READY runtime instead of invoking it and skipping on a
    confusing downstream error.
    """
    try:
        import boto3
        from botocore.exceptions import BotoCoreError, ClientError
    except ImportError:
        return "", ""
    try:
        client = boto3.client("bedrock-agentcore-control", region_name=region)
        paginator = None
        runtimes = []
        # ListAgentRuntimes is not always paginated in older botocore; handle both.
        try:
            paginator = client.get_paginator("list_agent_runtimes")
            for page in paginator.paginate():
                runtimes.extend(page.get("agentRuntimes", []))
        except Exception:
            runtimes = client.list_agent_runtimes().get("agentRuntimes", [])
        for rt in runtimes:
            if rt.get("agentRuntimeName") == agent_name:
                return rt.get("agentRuntimeArn", ""), rt.get("status", "")
        return "", ""
    except (BotoCoreError, ClientError, Exception) as exc:  # noqa: BLE001
        logger.info("control-plane ARN resolution failed for %s: %s", agent_name, exc)
        return "", ""


def resolve_live_runtime_arn(agent_name: str, env_var: str) -> str:
    """Stale-proof ARN resolution for ``agent_name``.

    Order:
      1. ``env_var`` override (operator-supplied, e.g. SELLER_MCP_RUNTIME_ARN).
      2. Live ``ListAgentRuntimes`` match by name — the primary path; can never
         be stale. If the matched runtime is not READY, raise so the test fails
         loudly with the reason rather than invoking a half-ready ARN.
      3. ``.bedrock_agentcore.yaml`` snapshot (offline fallback).
      4. ``""`` — caller skips (legitimate pre-deploy case).
    """
    override = os.environ.get(env_var, "")
    if override:
        return override

    region = os.environ.get("AWS_REGION", "us-west-2")
    arn, status = _arn_from_control_plane(agent_name, region)
    if arn:
        if status and status != "READY":
            raise RuntimeError(
                f"Runtime '{agent_name}' resolved live but status={status} (not READY). "
                f"Redeploy or wait for it to become READY before running live tests."
            )
        logger.info("resolved %s live -> %s (%s)", agent_name, arn[-14:], status)
        return arn

    snap = _arn_from_yaml(agent_name)
    if snap:
        logger.info("resolved %s from yaml snapshot (offline fallback)", agent_name)
    return snap
