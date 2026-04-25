#!/usr/bin/env bash
# =============================================================================
# Quick test runner for the seller agent
# =============================================================================
# Activates .venv, sets PYTHONPATH, and runs pytest.
#
# Usage:
#   ./test.sh                                    # run all unit tests
#   ./test.sh tests/unit/test_routing_mode.py    # run specific test
#   ./test.sh tests/unit/ -v                     # verbose
#   ./test.sh tests/integration/ -k "agentcore"  # filter
#   ./test.sh --all                              # run everything
# =============================================================================

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Always activate the .venv
if [[ -f "${REPO_ROOT}/.venv/bin/activate" ]]; then
  source "${REPO_ROOT}/.venv/bin/activate"
else
  echo "ERROR: .venv not found at ${REPO_ROOT}/.venv"
  echo "Create it with: python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'"
  exit 1
fi

export PYTHONPATH="${REPO_ROOT}/src:${PYTHONPATH:-}"
export AWS_PROFILE="${AWS_PROFILE:-genai}"
export AWS_REGION="${AWS_REGION:-us-west-2}"

# Ensure test deps are installed
pip install -q hypothesis pytest pytest-asyncio 2>/dev/null || true

# Default: run unit tests
if [[ $# -eq 0 ]]; then
  echo "Running: pytest tests/unit/ -v"
  exec pytest tests/unit/ -v
elif [[ "$1" == "--all" ]]; then
  shift
  echo "Running: pytest tests/ -v $*"
  exec pytest tests/ -v "$@"
else
  echo "Running: pytest $*"
  exec pytest "$@"
fi
