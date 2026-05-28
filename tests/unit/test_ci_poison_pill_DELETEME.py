# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Temporary poison-pill test to verify CI workflow surfaces pytest failures.

This file MUST be deleted before merging. It exists solely to confirm that
the Test job in .github/workflows/ci.yml now correctly reports FAILURE
when pytest exits non-zero. See bead ar-r82f.16.
"""


def test_ci_workflow_must_surface_this_failure():
    """Deliberate failure to verify CI fails the Test job on pytest failures."""
    assert False, "CI verification poison-pill (ar-r82f.16) — delete this file"
