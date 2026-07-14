# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""FastAPI routers for the REST API (EP-3.1 decomposition of main.py).

Route paths are written in full (no router prefixes) so BOTH url
conventions keep working exactly as before — unprefixed legacy routes
and /api/v1 routes. Endpoint registration order within each router file
matches the original order in main.py; consolidating the conventions and
fixing route shadowing is a later bead (EP-8.4).
"""

from . import (  # noqa: F401
    admin,
    approvals,
    change_requests,
    deals,
    media_kit,
    negotiation,
    orders,
    products,
    quotes,
    registry,
    sessions,
)

# Mount order mirrors the first-registration order of each group's
# endpoints in the pre-split main.py. There are no overlapping path
# templates across routers, so this only affects OpenAPI listing order —
# but we preserve it anyway to keep behavior byte-identical.
ALL_ROUTERS = [
    admin.router,
    products.router,
    negotiation.router,
    deals.router,
    approvals.router,
    sessions.router,
    media_kit.router,
    registry.router,
    quotes.router,
    orders.router,
    change_requests.router,
]
