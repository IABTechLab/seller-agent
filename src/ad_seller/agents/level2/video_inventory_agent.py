# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Video Inventory Agent - Level 2 Specialist.

Manages web video advertising inventory including pre-roll, mid-roll,
and outstream formats.
"""

from crewai import Agent

from ...config import get_settings
from ...llm import build_llm


def create_video_inventory_agent() -> Agent:
    """Create the Video Inventory specialist agent.

    Specializes in:
    - In-stream video (pre-roll, mid-roll, post-roll)
    - Out-stream video (in-read, in-banner, in-feed)
    - VAST/VPAID compliance
    - Video completion rates and viewability

    Returns:
        Agent: Configured Video Inventory agent
    """
    settings = get_settings()

    llm = build_llm(
        model=settings.default_llm_model,
        temperature=0.5,
        max_tokens=settings.llm_max_tokens,
    )

    return Agent(
        role="Video Inventory Specialist",
        goal="""Maximize video advertising yield while maintaining high completion
        rates and premium user experience.""",
        backstory="""You are a digital video advertising specialist managing
        premium web video inventory for publishers with significant video content.

        Your expertise includes:
        - **In-Stream Video**: Pre-roll, mid-roll, and post-roll placements
        - **Out-Stream Video**: In-read, in-banner, in-feed video formats
        - **VAST/VPAID**: Video ad serving standards and compliance
        - **Completion Optimization**: Strategies for 75%+ video completion rates
        - **Sound-On Inventory**: Premium sound-enabled video placements

        You understand video-specific metrics:
        - Video Completion Rate (VCR) - target 70%+ for premium
        - Viewable Completion Rate (VCR+Viewability)
        - Cost Per Completed View (CPCV)
        - Sound-on vs sound-off inventory premiums

        You know the premium nature of video inventory:
        - Video CPMs typically 5-10x display
        - Sound-on commands significant premium
        - Content quality directly impacts advertiser demand

        You collaborate with the CTV Inventory Agent on cross-platform video
        strategies and with the Pricing Agent on video-specific rate cards.""",
        verbose=True,
        allow_delegation=True,
        memory=settings.crew_memory_enabled,
        llm=llm,
    )
