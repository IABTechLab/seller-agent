# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Pricing type enum for seller inventory pricing signals.

Allows sellers to express whether a price is fixed, a floor for
negotiation, or unavailable (rate on request). Buyer agents must
respect these signals and never fabricate pricing when a seller
has indicated on_request.
"""

from enum import Enum


class PricingType(str, Enum):
    """How pricing should be interpreted for a product or package.

    - FIXED: Price is set by the seller, use as-is.
    - FLOOR: Minimum price; negotiation expected above this level.
    - ON_REQUEST: No price available; buyer must negotiate before
      any pricing exists. Pricing fields should be None.
    """

    FIXED = "fixed"
    FLOOR = "floor"
    ON_REQUEST = "on_request"
