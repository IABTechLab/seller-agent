# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Structural guards for the EP-3.1 decomposition (bead ar-6u86).

Asserts that:
1. interfaces/api/main.py stays a slim app-assembly module (< 400 lines).
2. No API router imports another interface adapter (chat, cli,
   mcp_server, agentcore) — cross-interface traffic must go through the
   service layer. (The api-package-internal deps/schemas/main modules are
   allowed.)
"""

import re
from pathlib import Path

import ad_seller.interfaces.api.main as api_main
from ad_seller.interfaces.api import routers as routers_pkg

API_DIR = Path(api_main.__file__).parent
ROUTERS_DIR = Path(routers_pkg.__file__).parent

# Matches any import statement line (including indented function-level ones).
_IMPORT_LINE = re.compile(r"^\s*(?:from|import)\s+.+$", re.MULTILINE)

# Interface adapter module names routers must NOT reach into, whether via
# absolute (`ad_seller.interfaces.chat`) or relative (`from ...chat.main`)
# imports. `interfaces.api` itself (deps/schemas/main compat) is fine —
# that's the same interface.
_FORBIDDEN_ADAPTERS = re.compile(r"\b(chat|cli|mcp_server|agentcore)\b")


def test_main_py_is_slim_app_assembly():
    main_path = API_DIR / "main.py"
    line_count = len(main_path.read_text().splitlines())
    assert line_count < 400, (
        f"interfaces/api/main.py is {line_count} lines; it must stay a slim "
        "app-assembly module (< 400 lines). Put endpoint logic in routers/ "
        "and business logic in ad_seller/services/."
    )


def test_routers_do_not_import_other_interfaces():
    router_files = sorted(ROUTERS_DIR.glob("*.py"))
    assert router_files, "no router modules found"

    offenders = []
    for path in router_files:
        source = path.read_text()
        for match in _IMPORT_LINE.finditer(source):
            line = match.group(0)
            if _FORBIDDEN_ADAPTERS.search(line):
                offenders.append(f"{path.name}: {line.strip()}")

    assert not offenders, (
        "Routers must not import other interface adapters directly "
        "(go through ad_seller/services/):\n" + "\n".join(offenders)
    )


def test_every_router_is_mounted():
    """Each router module's routes are all present on the app."""
    from fastapi.routing import APIRoute

    app_routes = {
        (r.path, m)
        for r in api_main.app.routes
        if isinstance(r, APIRoute)
        for m in (r.methods or [])
    }
    for router in routers_pkg.ALL_ROUTERS:
        for route in router.routes:
            if isinstance(route, APIRoute):
                for m in route.methods or []:
                    assert (route.path, m) in app_routes, (
                        f"route {m} {route.path} not mounted on app"
                    )
