"""Patch for CrewAI AnthropicCompletion — Bedrock Anthropic Messages compat.

When Claude runs on Amazon Bedrock's Anthropic-compatible (Messages) endpoint
(https://bedrock-runtime.<region>.amazonaws.com/anthropic), CrewAI's native
Anthropic provider builds tool definitions that include a ``strict`` field.

CrewAI (>=1.15) unconditionally sets ``"strict": True`` on every tool's
function schema in ``crewai.utilities.agent_utils`` (build_tools), and
``AnthropicCompletion._convert_tools_for_interference`` copies it onto the
Anthropic tool object. Anthropic's own API accepts ``strict``; Amazon Bedrock's
Anthropic-compatible endpoint does NOT, and rejects the request with:

    400 invalid_request_error: tools.0.custom.strict: Extra inputs are not permitted

This patch wraps ``_convert_tools_for_interference`` to remove the ``strict``
key from the produced tool objects, but ONLY when the completion is pointed at
a Bedrock base URL (so behavior against real Anthropic is unchanged). It is the
Messages-endpoint analog of the retired Converse ``crewai_bedrock_fix`` and is
applied only when ``ANTHROPIC_COMPATIBLE_LLM_API_BASE_URL`` is set.

Usage:
    from patches.crewai_bedrock_anthropic_fix import apply_patches
    apply_patches()  # call once at startup, before building crews
"""

import logging

logger = logging.getLogger(__name__)

_patched = False


def _is_bedrock_base_url(url: object) -> bool:
    return isinstance(url, str) and ("bedrock-runtime" in url or "bedrock-mantle" in url)


def apply_patches() -> None:
    """Strip the unsupported ``strict`` tool field on the Bedrock Messages endpoint."""
    global _patched
    if _patched:
        return
    try:
        from crewai.llms.providers.anthropic.completion import AnthropicCompletion
    except ImportError:
        logger.warning("AnthropicCompletion not available — skipping Bedrock Anthropic patch")
        return

    original = AnthropicCompletion._convert_tools_for_interference

    def _patched_convert(self, tools):
        result = original(self, tools)
        if _is_bedrock_base_url(getattr(self, "base_url", None)) and isinstance(result, list):
            for tool in result:
                if isinstance(tool, dict):
                    tool.pop("strict", None)
        return result

    AnthropicCompletion._convert_tools_for_interference = _patched_convert
    _patched = True
    logger.info(
        "Patched AnthropicCompletion._convert_tools_for_interference (Bedrock strict-strip)"
    )
