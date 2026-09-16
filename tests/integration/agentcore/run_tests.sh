#!/usr/bin/env bash
# =============================================================================
# Functional test runner for Seller AgentCore runtime
# =============================================================================
# Called by deploy.sh --test or run standalone.
#
# Usage:
#   bash tests/functional/run_tests.sh --profile genai
#   bash tests/functional/run_tests.sh --profile genai -k "create_deal"
#   bash tests/functional/run_tests.sh --profile genai -k "chat"
#   bash tests/functional/run_tests.sh --profile genai --runtime-arn arn:aws:...
#
# Options:
#   --profile PROFILE   AWS CLI profile
#   --runtime-arn ARN   Runtime ARN override (auto-detected from yaml)
#   --agent-name NAME   Agent name in .bedrock_agentcore.yaml
#   -k EXPR             pytest -k expression to select tests
#   -v                  Verbose output
#   --help              Show this help
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
# HTTP chat/crew suite — needs --agent-name to target the http runtime.
TEST_FILE="${SCRIPT_DIR}/test_runtime.py"
# MCP + A2A live post-deploy suites — self-resolve their runtime ARN from
# .bedrock_agentcore.yaml (or SELLER_{MCP,A2A}_RUNTIME_ARN) and skip cleanly
# when a runtime/auth stack is absent, so they need no --agent-name.
MCP_TEST_FILE="${SCRIPT_DIR}/test_mcp_runtime.py"
A2A_TEST_FILE="${SCRIPT_DIR}/test_a2a_runtime.py"

# Parse args — pass through to pytest
PYTEST_ARGS=()
PROFILE=""
RUNTIME_ARN=""
AGENT_NAME=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile)    PROFILE="$2"; shift 2 ;;
    --runtime-arn) RUNTIME_ARN="$2"; shift 2 ;;
    --agent-name) AGENT_NAME="$2"; shift 2 ;;
    --help|-h)
      echo "Usage: $(basename "$0") [--profile PROFILE] [--runtime-arn ARN] [-k EXPR] [-v]"
      echo ""
      echo "Test groups (use -k to select):"
      echo "  chat              Chat mode tests"
      echo "  list_products     List products tool"
      echo "  get_pricing       Pricing tool"
      echo "  get_rate_card     Rate card tool"
      echo "  discover          Inventory discovery tool"
      echo "  product_details   Product details tool"
      echo "  create_deal       Deal creation tool"
      echo "  complex           Multi-step campaign scenario"
      exit 0
      ;;
    *)            PYTEST_ARGS+=("$1"); shift ;;
  esac
done

# Resolve Python — prefer .venv if available
if [[ -f "${REPO_ROOT}/.venv/bin/python" ]]; then
  PYTHON="${REPO_ROOT}/.venv/bin/python"
else
  PYTHON="python3"
fi

# Common pytest args shared by all suites (profile / arn / passthrough).
COMMON_ARGS=()
if [[ -n "${PROFILE}" ]]; then
  COMMON_ARGS+=(--profile "${PROFILE}")
fi
if [[ -n "${RUNTIME_ARN}" ]]; then
  COMMON_ARGS+=(--runtime-arn "${RUNTIME_ARN}")
fi
# Add default verbose if not specified
if [[ ! " ${PYTEST_ARGS[*]:-} " =~ " -v " ]] && [[ ! " ${PYTEST_ARGS[*]:-} " =~ " --verbose " ]]; then
  COMMON_ARGS+=(-v)
fi
COMMON_ARGS+=("${PYTEST_ARGS[@]+"${PYTEST_ARGS[@]}"}")

echo "============================================="
echo "  Seller Runtime — Post-Deploy Integration"
echo "============================================="

# Don't let a failing suite abort the others — aggregate the exit codes so a
# single non-zero is surfaced at the end (all suites always run).
set +e
OVERALL_RC=0

# 1) HTTP chat/crew suite — REQUIRES --agent-name to hit the http runtime.
HTTP_ARGS=("${COMMON_ARGS[@]}")
if [[ -n "${AGENT_NAME}" ]]; then
  HTTP_ARGS+=(--agent-name "${AGENT_NAME}")
fi
echo ">>> [1/3] HTTP chat/crew: ${TEST_FILE}"
"${PYTHON}" -m pytest "${TEST_FILE}" "${HTTP_ARGS[@]}"
OVERALL_RC=$(( OVERALL_RC | $? ))

# 2) MCP live suite — self-resolves its runtime ARN; no --agent-name needed.
echo ">>> [2/3] MCP live: ${MCP_TEST_FILE}"
"${PYTHON}" -m pytest "${MCP_TEST_FILE}" "${COMMON_ARGS[@]}"
OVERALL_RC=$(( OVERALL_RC | $? ))

# 3) A2A live smoke — self-resolves its runtime ARN; no --agent-name needed.
echo ">>> [3/3] A2A live smoke: ${A2A_TEST_FILE}"
"${PYTHON}" -m pytest "${A2A_TEST_FILE}" "${COMMON_ARGS[@]}"
OVERALL_RC=$(( OVERALL_RC | $? ))

echo "============================================="
echo "  Post-deploy integration exit code: ${OVERALL_RC}"
echo "============================================="
exit "${OVERALL_RC}"
