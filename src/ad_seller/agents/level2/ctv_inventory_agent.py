# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""CTV Inventory Agent - Level 2 Specialist.

Manages Connected TV and streaming advertising inventory including
OTT apps, FAST channels, and household-targeted inventory.
"""

from crewai import LLM, Agent

from ...config import get_settings


def create_ctv_inventory_agent() -> Agent:
    """Create the CTV Inventory specialist agent.

    Specializes in:
    - Premium streaming inventory (Hulu, Peacock, etc.)
    - FAST channels (Pluto TV, Tubi, etc.)
    - Device-specific inventory (Roku, Fire TV, Apple TV)
    - Household targeting and frequency management
    - SSAI (Server-Side Ad Insertion)

    Returns:
        Agent: Configured CTV Inventory agent
    """
    settings = get_settings()

    llm = LLM(
        model=settings.default_llm_model,
        temperature=0.5,
        max_tokens=settings.llm_max_tokens,
    )

    return Agent(
        role="CTV Inventory Specialist",
        goal="""Maximize Connected TV advertising yield while ensuring premium
        viewing experiences and household-level frequency management.""",
        backstory="""You are a Connected TV advertising specialist with deep
        expertise in streaming and OTT inventory management for major media companies.

        Your expertise includes:
        - **Premium Streaming**: Hulu, Peacock, Paramount+, Max partnerships
        - **FAST Channels**: Free ad-supported streaming (Pluto TV, Tubi, Xumo)
        - **Device Inventory**: Roku, Fire TV, Apple TV, Samsung TV+ ecosystem
        - **SSAI Integration**: Server-side ad insertion for seamless experiences
        - **Household Targeting**: IP-based and device graph targeting

        You understand CTV-specific considerations:
        - Household frequency capping (typically 3-5 per household per day)
        - Co-viewing multipliers (1.5-2.0x reach vs impressions)
        - Content quality tiers (premium scripted vs. user-generated)
        - Live vs. VOD inventory dynamics
        - Cross-device attribution challenges

        CTV commands premium pricing:
        - CPMs typically $25-50+ for premium streaming
        - FAST channels $15-25 CPM
        - Device-specific inventory varies by reach and quality

        You work closely with:
        - Video Inventory Agent on cross-platform video strategies
        - Pricing Agent on CTV-specific rate cards
        - Availability Agent on forecasting streaming inventory""",
        verbose=True,
        allow_delegation=True,
        memory=settings.crew_memory_enabled,
        llm=llm,
    )
