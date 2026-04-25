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
TEST_FILE="${SCRIPT_DIR}/test_runtime.py"

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

# Build pytest command
CMD=("${PYTHON}" -m pytest "${TEST_FILE}")

if [[ -n "${PROFILE}" ]]; then
  CMD+=(--profile "${PROFILE}")
fi
if [[ -n "${RUNTIME_ARN}" ]]; then
  CMD+=(--runtime-arn "${RUNTIME_ARN}")
fi
if [[ -n "${AGENT_NAME}" ]]; then
  CMD+=(--agent-name "${AGENT_NAME}")
fi

# Add default verbose if not specified
if [[ ! " ${PYTEST_ARGS[*]:-} " =~ " -v " ]] && [[ ! " ${PYTEST_ARGS[*]:-} " =~ " --verbose " ]]; then
  CMD+=(-v)
fi

# Pass through remaining args
CMD+=("${PYTEST_ARGS[@]+"${PYTEST_ARGS[@]}"}")

echo "============================================="
echo "  Seller Runtime — Functional Tests"
echo "============================================="
echo "  Command: ${CMD[*]}"
echo "============================================="

exec "${CMD[@]}"
