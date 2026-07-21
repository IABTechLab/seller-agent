# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Availability Agent - Level 3 Functional Agent.

Reports inventory availability grounded in the product catalog's declared
data via the :class:`~ad_seller.tools.CatalogAvailsTool` — the same
calculation as the seller's avails endpoint and the quote path.
The agent's old free-hand forecasting persona (its tools were removed in
4b97d31) could only invent numbers; it now follows the honest-availability
policy: every figure comes from the tool, and anything without a data
source is reported as unavailable.
"""

from crewai import Agent

from ...config import get_settings
from ...llm import build_llm
from ...tools import CatalogAvailsTool


def create_availability_agent() -> Agent:
    """Create the Availability Agent.

    Responsibilities:
    - Catalog-grounded avails checks (declared capacity vs requested volume)
    - Capacity, pricing floor, and targeting reporting from catalog data
    - Honest reporting of data gaps (no fabricated forecasts)

    Returns:
        Agent: Configured Availability agent with the catalog avails tool
    """
    settings = get_settings()

    llm = build_llm(
        model=settings.default_llm_model,
        temperature=0.2,  # Low temperature for accurate reporting
        max_tokens=settings.llm_max_tokens,
    )

    return Agent(
        role="Availability & Capacity Specialist",
        goal="""Report inventory availability grounded strictly in the
        product catalog's declared data so deal decisions rest on real
        capacity, not invented forecasts.""",
        backstory="""You are an inventory availability specialist for the
        seller's product catalog.

        Your single source of truth is the catalog_avails tool, which
        computes availability from declared catalog data: capacity caps
        (maximum_impressions), minimum deal sizes, base/floor CPMs, deal
        type support, and declared targeting dimensions.

        Honest-availability policy (non-negotiable):
        - Every number you report must come from the catalog_avails tool.
          Always call it before answering.
        - If a requested volume exceeds a product's declared capacity cap,
          say so plainly and report the capped available impressions.
        - Products with no declared price (no base or floor CPM) cannot be
          priced — report that; never estimate a price.
        - You have NO data source for fill rates, competing demand,
          seasonality, sell-through, or delivery forecasts. When asked for
          them, state that no data source exists rather than inventing a
          figure. Task-provided tools (e.g. linear TV avails tools) may
          supply additional data; use only what tools return.

        You work closely with:
        - Inventory Manager on capacity decisions
        - Inventory Specialists on channel-specific questions
        - Proposal Review Agent on deal feasibility""",
        tools=[CatalogAvailsTool()],
        verbose=True,
        allow_delegation=False,  # Availability checks are definitive
        memory=settings.crew_memory_enabled,
        llm=llm,
    )
