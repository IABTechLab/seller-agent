# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Ad Seller System - IAB OpenDirect 2.1 compliant publisher/SSP agent system."""

from importlib.metadata import version

from ad_seller import _telemetry_shim  # noqa: F401  # MUST be first import

__version__ = version("ad_seller_system")
