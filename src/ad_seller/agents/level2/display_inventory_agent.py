# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Display Inventory Agent - Level 2 Specialist.

Manages display advertising inventory including banners, rich media,
and premium homepage takeovers.
"""

from crewai import LLM, Agent

from ...config import get_settings


def create_display_inventory_agent() -> Agent:
    """Create the Display Inventory specialist agent.

    Specializes in:
    - Standard IAB display units (728x90, 300x250, 160x600, etc.)
    - Rich media formats (expandables, interstitials, floating)
    - Premium positions (homepage takeovers, roadblocks)
    - Programmatic display (RTB, PMP, PG)

    Returns:
        Agent: Configured Display Inventory agent
    """
    settings = get_settings()

    llm = LLM(
        model=settings.default_llm_model,
        temperature=0.5,
        max_tokens=settings.llm_max_tokens,
    )

    return Agent(
        role="Display Inventory Specialist",
        goal="""Optimize display advertising inventory for maximum yield while
        maintaining high viewability and brand-safe environments.""",
        backstory="""You are a display advertising specialist with deep expertise
        in managing web-based banner and rich media inventory for premium publishers.

        Your expertise includes:
        - **Standard Display**: IAB standard units across desktop and mobile web
        - **Rich Media**: Expandable units, interstitials, video-in-banner
        - **Premium Positions**: Homepage takeovers, roadblock executions, masthead
        - **Viewability Optimization**: Above-the-fold placement strategies
        - **Brand Safety**: Content adjacency controls and category exclusions

        You understand display-specific KPIs:
        - Viewability rates (target 70%+ for premium)
        - Click-through rates by format and position
        - Effective CPMs considering fill rate
        - Frequency capping strategies

        You work with the Pricing Agent to set appropriate floor prices by
        position and with the Availability Agent to forecast display inventory.
        You can delegate to Level 3 agents for detailed pricing and availability.""",
        verbose=True,
        allow_delegation=True,
        memory=settings.crew_memory_enabled,
        llm=llm,
    )
