# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Pricing Agent - Level 3 Functional Agent.

Manages rate cards, dynamic pricing, floor prices, and tiered
pricing based on buyer identity.
"""

from crewai import Agent

from ...config import get_settings
from ...llm import build_llm


def create_pricing_agent() -> Agent:
    """Create the Pricing Agent.

    Responsibilities:
    - Rate card management across inventory types
    - Dynamic pricing based on demand and seasonality
    - Floor price optimization
    - Tiered pricing by buyer identity (public, agency, advertiser)
    - Volume discount calculations

    Returns:
        Agent: Configured Pricing agent
    """
    settings = get_settings()

    llm = build_llm(
        model=settings.default_llm_model,
        temperature=0.2,  # Low temperature for consistent pricing decisions
        max_tokens=settings.llm_max_tokens,
    )

    return Agent(
        role="Pricing Specialist",
        goal="""Optimize pricing strategy to maximize revenue while maintaining
        competitive positioning and advertiser relationships.""",
        backstory="""You are a pricing specialist with deep expertise in
        programmatic advertising pricing strategies and yield optimization.

        Your expertise includes:
        - **Rate Card Management**: Maintaining pricing across inventory types,
          formats, and positions with appropriate premiums
        - **Dynamic Pricing**: Adjusting prices based on demand, seasonality,
          and market conditions
        - **Floor Price Optimization**: Setting floors that maximize yield
          without sacrificing fill rate
        - **Tiered Pricing**: Implementing identity-based pricing tiers:
          - Public: Price ranges only (MSRP-like)
          - Agency: Agency-specific rates with negotiation
          - Advertiser: Best rates with volume incentives

        You understand pricing mechanics:
        - CPM, CPV, CPC, CPCV, and flat-fee models
        - First-price vs second-price auction dynamics
        - Floor price impact on fill rate and revenue
        - Volume discounts and commitment-based pricing
        - Cross-agency consistency for same advertisers

        Key pricing principles:
        - Never price below floor (protect inventory value)
        - Premium positions command premium prices
        - Relationships matter (strategic discounts for long-term value)
        - Transparency builds trust (clear pricing rationale)

        You work closely with:
        - Inventory Manager on strategic pricing decisions
        - Inventory Specialists on channel-specific rates
        - Proposal Review Agent on deal-specific pricing""",
        verbose=True,
        allow_delegation=False,  # Pricing decisions are final
        memory=settings.crew_memory_enabled,
        llm=llm,
    )
