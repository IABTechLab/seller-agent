#!/usr/bin/env bash
# Wire up the repo's committed git hooks (.githooks/).
# Run once after cloning: ./scripts/setup-hooks.sh
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"
chmod +x .githooks/*
git config core.hooksPath .githooks

echo "core.hooksPath -> .githooks"
echo "Active guardrails:"
echo "  commit-msg : rejects internal tracker IDs (ar-*), Claude-Session URLs,"
echo "               and non-public bead trailers"
echo "  pre-commit : gitleaks staged scan (if installed), blocks .beads/ files"
echo "               and secret-like \${VAR:-default} fallbacks"
echo "Details: CONTRIBUTING.md ('Repository hygiene policy')."
