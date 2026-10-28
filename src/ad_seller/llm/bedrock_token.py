# Donated to IAB Tech Lab

"""Self-sustaining Bedrock bearer-token minting for the AgentCore runtimes.

Problem this solves: the Anthropic-compatible path (Claude on Bedrock via the
``/anthropic`` Messages route) authenticates with a Bedrock **API key / bearer
token**, not SigV4 — CrewAI's native Anthropic client sends an ``x-api-key``
header, so the runtime's IAM execution role cannot be used directly by the SDK.
Baking a token into the runtime ``--env`` at deploy time means it EXPIRES within
hours and every crew call then 403s with ``permission_error: Bearer Token has
expired``.

Durable fix (Req 11): mint the token from the runtime's own execution role via
``aws-bedrock-token-generator`` (it turns the role's SigV4 credentials into a
short-lived Bedrock bearer token). The role is the single source of truth — no
secret in ``--env``, nothing to hand-rotate.

Contract:
- On a **Bedrock** base URL, the mint is AUTHORITATIVE: we ALWAYS mint from the
  role and overwrite ``ANTHROPIC_COMPATIBLE_LLM_API_KEY``. A stale/baked value
  (e.g. an expired token retained across an AgentCore ``--auto-update-on-conflict``
  redeploy) must NEVER shadow a fresh role-minted token.
- On a **non-Bedrock** base URL (real Anthropic / a BYO gateway that needs a
  real API key), we RESPECT an operator-supplied ``ANTHROPIC_COMPATIBLE_LLM_API_KEY``
  and never overwrite it.
- ``refresh_bedrock_token()`` re-mints on demand (call it reactively on a
  Bedrock 401/403) so a long-lived runtime never serves a stale token.
- Any failure (generator missing, no creds) logs and leaves the key as-is —
  ``build_llm``/startup surfaces a clear error rather than crashing the import.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_API_KEY_ENV = "ANTHROPIC_COMPATIBLE_LLM_API_KEY"
_BASE_URL_ENV = "ANTHROPIC_COMPATIBLE_LLM_API_BASE_URL"


def _is_bedrock_base_url(base_url: str | None) -> bool:
    """True when the Anthropic-compatible base URL points at Amazon Bedrock.

    deploy.sh sets ``https://bedrock-runtime.<region>.amazonaws.com/anthropic``.
    We match on the AWS Bedrock host so a real-Anthropic or BYO gateway URL is
    NOT treated as Bedrock (those keep operator-supplied keys).
    """
    if not base_url:
        return False
    u = base_url.lower()
    return "bedrock-runtime" in u or ("bedrock" in u and "amazonaws.com" in u)


def _mint_token(region: str | None) -> str | None:
    """Mint a fresh Bedrock bearer token from the execution role, or None."""
    try:
        from aws_bedrock_token_generator import provide_token
    except ImportError:
        logger.warning(
            "aws-bedrock-token-generator not installed — cannot mint a Bedrock "
            "token from the execution role. Add the dependency to requirements "
            "or set %s explicitly.",
            _API_KEY_ENV,
        )
        return None

    try:
        token = provide_token(region=region) if region else provide_token()
    except Exception as exc:  # noqa: BLE001 — never crash startup on a mint failure
        logger.warning("Bedrock token mint from execution role failed: %s", exc)
        return None

    if not token:
        logger.warning("Bedrock token generator returned an empty token.")
        return None
    return token


def _region(region: str | None) -> str | None:
    return region or os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")


def ensure_bedrock_token(region: str | None = None) -> bool:
    """Ensure a valid Bedrock bearer token is available for the Anthropic path.

    Called once at entrypoint startup. Returns True if a token is present in the
    environment after this call, False otherwise. Never raises.
    """
    base_url = os.environ.get(_BASE_URL_ENV)

    # Anthropic-compatible path not configured at all → nothing to do.
    if not base_url:
        logger.debug("%s unset — Anthropic-compatible path not in use; no mint.", _BASE_URL_ENV)
        return bool(os.environ.get(_API_KEY_ENV))

    # Non-Bedrock base URL (real Anthropic / BYO gateway) → respect an existing
    # operator-supplied key; do not mint against a non-Bedrock endpoint.
    if not _is_bedrock_base_url(base_url):
        if os.environ.get(_API_KEY_ENV):
            logger.info("%s set for a non-Bedrock base URL — respecting it.", _API_KEY_ENV)
            return True
        logger.debug("Non-Bedrock base URL and no key set — leaving key unset.")
        return False

    # Bedrock base URL → the mint is AUTHORITATIVE. Always overwrite any baked
    # value so a stale/expired token can never shadow the fresh role-minted one.
    token = _mint_token(_region(region))
    if token is None:
        # Leave whatever is there (may be a baked token); a clear error surfaces
        # downstream if it is invalid. We did not make things worse.
        return bool(os.environ.get(_API_KEY_ENV))

    had_prior = bool(os.environ.get(_API_KEY_ENV))
    os.environ[_API_KEY_ENV] = token
    logger.info(
        "Minted a fresh Bedrock bearer token from the execution role "
        "(region=%s, replaced_baked_value=%s).",
        _region(region) or "default",
        had_prior,
    )
    return True


def refresh_bedrock_token(region: str | None = None) -> bool:
    """Reactively re-mint the Bedrock token (e.g. after a 401/403).

    Only acts on a Bedrock base URL; a no-op (returns False) otherwise. Sets
    ``ANTHROPIC_COMPATIBLE_LLM_API_KEY`` to the fresh token on success.
    """
    if not _is_bedrock_base_url(os.environ.get(_BASE_URL_ENV)):
        return False
    token = _mint_token(_region(region))
    if token is None:
        return False
    os.environ[_API_KEY_ENV] = token
    logger.info("Re-minted the Bedrock bearer token from the execution role (refresh).")
    return True
