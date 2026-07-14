# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Drift guard for the auto-generated reference inventories (EP-9.1).

Regenerates the MCP tool / REST endpoint / EventType inventories in memory
from the *code* and asserts they byte-match the committed docs under
``docs/reference/``. Any code change that adds or removes a tool, endpoint,
or event type breaks these tests until the docs are regenerated with:

    python scripts/generate_inventories.py
"""

import importlib.util
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GENERATOR = _REPO_ROOT / "scripts" / "generate_inventories.py"


def _load_generator():
    spec = importlib.util.spec_from_file_location("generate_inventories", _GENERATOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_gen = _load_generator()


@pytest.mark.parametrize("filename", sorted(_gen.INVENTORIES))
def test_inventory_matches_committed_doc(filename):
    """Committed inventory must byte-match freshly generated output."""
    expected = _gen.INVENTORIES[filename]()
    committed_path = _gen.DOCS_DIR / filename
    assert committed_path.exists(), (
        f"{committed_path} is missing — run: python scripts/generate_inventories.py"
    )
    committed = committed_path.read_text()
    assert committed == expected, (
        f"docs/reference/{filename} is out of date with the code.\n"
        "Regenerate it with: python scripts/generate_inventories.py"
    )


def test_inventories_are_nonempty():
    """Guard against the generator silently emitting empty inventories."""
    rendered = _gen.render_all()
    assert set(rendered) == {"mcp-tools.md", "endpoints.md", "event-types.md"}
    for name, content in rendered.items():
        assert "**Total:" in content, f"{name} missing a total count"
