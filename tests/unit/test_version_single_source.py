# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Regression test: the version string lives in exactly one place in `src/`.

`ad_seller.__version__` is the single source of truth, and `pyproject.toml`
carries the packaging copy. Anything else that repeats the literal drifts the
moment a release bumps it, and the failure is invisible in CI: the package
installs as the new version while running endpoints keep reporting the old
one, so bug reports name a version nobody is running.

This is not hypothetical. `GET /` (`routers/admin.py`) and
`GET /.well-known/agent.json` (`routers/registry.py`) both hardcoded
``"2.4.2"`` and were caught while scoping a release bump, having already
survived the v2.4.0 -> v2.4.1 -> v2.4.2 bumps unnoticed. The agent card is
the worse of the two, since registries and buyer agents read it to decide
what this seller supports.
"""

from pathlib import Path

import ad_seller

SRC = Path(ad_seller.__file__).parent


def test_version_literal_appears_only_in_dunder_init():
    """No module under `src/ad_seller/` may repeat the current version string.

    Scans for the exact current value rather than a semver pattern, so
    unrelated version numbers (OpenDirect spec dialects, protocol versions,
    dependency pins) cannot trip it.
    """
    version = ad_seller.__version__
    offenders = []

    for path in sorted(SRC.rglob("*.py")):
        if path.name == "__init__.py" and path.parent == SRC:
            continue  # the one legitimate home
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):  # pragma: no cover - unreadable file
            continue
        if version in text:
            for lineno, line in enumerate(text.splitlines(), start=1):
                if version in line:
                    offenders.append(f"{path.relative_to(SRC)}:{lineno}: {line.strip()}")

    assert not offenders, (
        f"The version literal {version!r} must only appear in ad_seller/__init__.py, "
        "so that a release bump cannot leave a served surface reporting a stale "
        "version. Import `__version__` instead. Offenders:\n  " + "\n  ".join(offenders)
    )


def test_served_surfaces_report_the_package_version():
    """The two endpoints that publish a version must read it from the package.

    A lighter-weight check than standing up the app: both call sites are
    expected to reference the imported name, not a literal.
    """
    for module_path in (
        SRC / "interfaces" / "api" / "routers" / "admin.py",
        SRC / "interfaces" / "api" / "routers" / "registry.py",
    ):
        text = module_path.read_text(encoding="utf-8")
        assert "from .... import __version__" in text, (
            f"{module_path.relative_to(SRC)} should import __version__ from the package"
        )
        assert "__version__" in text.split("router = APIRouter()", 1)[1], (
            f"{module_path.relative_to(SRC)} imports __version__ but never uses it below "
            "the router definition, which suggests the literal came back"
        )
