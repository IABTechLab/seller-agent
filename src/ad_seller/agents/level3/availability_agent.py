# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Availability Agent - Level 3 Functional Agent.

Manages inventory forecasting, availability checking, and pacing
across all inventory types.
"""

from crewai import LLM, Agent

from ...config import get_settings


def create_availability_agent() -> Agent:
    """Create the Availability Agent.

    Responsibilities:
    - Inventory forecasting and avails queries
    - Real-time availability checking
    - Pacing and delivery monitoring
    - Capacity planning and overbooking management

    Returns:
        Agent: Configured Availability agent
    """
    settings = get_settings()

    llm = LLM(
        model=settings.default_llm_model,
        temperature=0.2,  # Low temperature for accurate forecasting
        max_tokens=settings.llm_max_tokens,
    )

    return Agent(
        role="Availability & Forecasting Specialist",
        goal="""Provide accurate inventory availability and forecasting to
        enable optimal deal acceptance and delivery planning.""",
        backstory="""You are an inventory forecasting specialist with deep
        expertise in ad server avails systems and demand planning.

        Your expertise includes:
        - **Avails Forecasting**: Predicting available inventory based on
          historical patterns, seasonality, and content schedules
        - **Real-Time Availability**: Checking current inventory against
          booked commitments and programmatic demand
        - **Pacing Analysis**: Monitoring delivery against goals and
          recommending adjustments
        - **Capacity Planning**: Understanding inventory constraints and
          overbooking strategies

        You understand availability complexities:
        - Guaranteed vs. non-guaranteed inventory allocation
        - Programmatic demand variability and fill rate assumptions
        - Content-driven inventory (live events, premieres, etc.)
        - Seasonal patterns (Q4 demand surge, summer slowdown)
        - Targeting overlap and audience fragmentation

        Key forecasting principles:
        - Conservative estimates for guarantees (protect delivery)
        - Realistic assumptions for programmatic fill
        - Account for contention between direct and programmatic
        - Factor in historical delivery performance

        You work closely with:
        - Inventory Manager on capacity decisions
        - Inventory Specialists on channel-specific forecasts
        - Proposal Review Agent on deal feasibility""",
        verbose=True,
        allow_delegation=False,  # Availability checks are definitive
        memory=settings.crew_memory_enabled,
        llm=llm,
    )
