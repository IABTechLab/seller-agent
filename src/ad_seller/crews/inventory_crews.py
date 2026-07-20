# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Inventory Crews - Channel-specific crews for inventory operations.

These crews combine inventory specialists with functional agents
for channel-specific operations.
"""

from crewai import Crew, Process, Task

from ..agents.level2 import (
    create_ctv_inventory_agent,
    create_display_inventory_agent,
    create_linear_tv_inventory_agent,
    create_mobile_app_inventory_agent,
    create_native_inventory_agent,
    create_video_inventory_agent,
)
from ..agents.level3 import (
    create_audience_validator_agent,
    create_availability_agent,
    create_pricing_agent,
    create_proposal_review_agent,
    create_upsell_agent,
)
from ..config import get_settings
from ..models.flow_state import ProposalReviewOutput
from ..tools.audience import (
    AudienceCapabilityTool,
    AudienceValidationTool,
    CoverageCalculatorTool,
)
from ..tools.linear import (
    AddressableTargetingTool,
    DMAAvailsTool,
    LinearAvailsTool,
    LinearPricingTool,
    MakegoodPoolTool,
    ScatterPricingTool,
    UpfrontDealCalculator,
)


def create_display_crew() -> Crew:
    """Create a crew for display inventory operations.

    Returns:
        Crew: Display inventory crew with pricing and availability agents
    """
    settings = get_settings()

    display_agent = create_display_inventory_agent()
    pricing_agent = create_pricing_agent()
    availability_agent = create_availability_agent()

    pricing_task = Task(
        description="""Provide display inventory pricing guidance:
- Standard IAB unit pricing (728x90, 300x250, 160x600, etc.)
- Rich media premiums
- Homepage takeover and premium position rates
- Viewability-based pricing tiers""",
        expected_output="Display pricing recommendations with rationale",
        agent=pricing_agent,
    )

    availability_task = Task(
        description="""Assess display inventory availability:
- Current fill rates by position
- Forecasted availability for upcoming periods
- Premium position capacity
- Programmatic vs direct allocation""",
        expected_output="Display availability forecast with capacity notes",
        agent=availability_agent,
    )

    strategy_task = Task(
        description="""Based on pricing and availability input, develop
display inventory strategy recommendations:
- Optimal pricing for current market conditions
- Inventory allocation recommendations
- Product packaging suggestions""",
        expected_output="Display inventory strategy recommendations",
        agent=display_agent,
        context=[pricing_task, availability_task],
    )

    return Crew(
        agents=[display_agent, pricing_agent, availability_agent],
        tasks=[pricing_task, availability_task, strategy_task],
        process=Process.sequential,
        verbose=settings.crew_verbose,
        memory=settings.crew_memory_enabled,
    )


def create_video_crew() -> Crew:
    """Create a crew for video inventory operations.

    Returns:
        Crew: Video inventory crew with pricing and availability agents
    """
    settings = get_settings()

    video_agent = create_video_inventory_agent()
    pricing_agent = create_pricing_agent()
    availability_agent = create_availability_agent()

    pricing_task = Task(
        description="""Provide video inventory pricing guidance:
- Pre-roll, mid-roll, post-roll rates
- Outstream video pricing
- Sound-on vs sound-off premiums
- Completion-based pricing options (CPCV)""",
        expected_output="Video pricing recommendations with completion rate assumptions",
        agent=pricing_agent,
    )

    availability_task = Task(
        description="""Assess video inventory availability:
- In-stream video capacity by content type
- Outstream fill rates and projections
- Sound-on inventory availability
- Content quality tier breakdown""",
        expected_output="Video availability forecast with quality tier breakdown",
        agent=availability_agent,
    )

    strategy_task = Task(
        description="""Based on pricing and availability input, develop
video inventory strategy recommendations:
- Optimal video pricing by format and quality
- Inventory allocation between programmatic and direct
- Video product packaging suggestions""",
        expected_output="Video inventory strategy recommendations",
        agent=video_agent,
        context=[pricing_task, availability_task],
    )

    return Crew(
        agents=[video_agent, pricing_agent, availability_agent],
        tasks=[pricing_task, availability_task, strategy_task],
        process=Process.sequential,
        verbose=settings.crew_verbose,
        memory=settings.crew_memory_enabled,
    )


def create_ctv_crew() -> Crew:
    """Create a crew for CTV inventory operations.

    Returns:
        Crew: CTV inventory crew with pricing and availability agents
    """
    settings = get_settings()

    ctv_agent = create_ctv_inventory_agent()
    pricing_agent = create_pricing_agent()
    availability_agent = create_availability_agent()

    pricing_task = Task(
        description="""Provide CTV inventory pricing guidance:
- Premium streaming rates (Hulu, Peacock tier)
- FAST channel pricing
- Device-specific inventory pricing
- Household targeting premiums
- Live vs VOD pricing differences""",
        expected_output="CTV pricing recommendations by tier and device",
        agent=pricing_agent,
    )

    availability_task = Task(
        description="""Assess CTV inventory availability:
- Streaming inventory capacity by platform
- FAST channel availability
- Live event inventory
- Household reach projections
- Co-viewing assumptions""",
        expected_output="CTV availability forecast with household reach estimates",
        agent=availability_agent,
    )

    strategy_task = Task(
        description="""Based on pricing and availability input, develop
CTV inventory strategy recommendations:
- Premium streaming vs FAST allocation
- Household frequency recommendations
- CTV product packaging for maximum yield""",
        expected_output="CTV inventory strategy recommendations",
        agent=ctv_agent,
        context=[pricing_task, availability_task],
    )

    return Crew(
        agents=[ctv_agent, pricing_agent, availability_agent],
        tasks=[pricing_task, availability_task, strategy_task],
        process=Process.sequential,
        verbose=settings.crew_verbose,
        memory=settings.crew_memory_enabled,
    )


def create_mobile_app_crew() -> Crew:
    """Create a crew for mobile app inventory operations.

    Returns:
        Crew: Mobile app inventory crew with pricing and availability agents
    """
    settings = get_settings()

    mobile_agent = create_mobile_app_inventory_agent()
    pricing_agent = create_pricing_agent()
    availability_agent = create_availability_agent()

    pricing_task = Task(
        description="""Provide mobile app inventory pricing guidance:
- Rewarded video rates by app genre
- Interstitial pricing
- Banner pricing within apps
- iOS vs Android pricing differences
- In-app bidding floor recommendations""",
        expected_output="Mobile app pricing recommendations by format and platform",
        agent=pricing_agent,
    )

    availability_task = Task(
        description="""Assess mobile app inventory availability:
- Rewarded video capacity by app
- Interstitial availability and frequency limits
- iOS vs Android breakdown
- Post-ATT targeting availability
- Mediation waterfall performance""",
        expected_output="Mobile app availability forecast with platform breakdown",
        agent=availability_agent,
    )

    strategy_task = Task(
        description="""Based on pricing and availability input, develop
mobile app inventory strategy recommendations:
- Format mix optimization
- Platform-specific strategies
- Mediation and bidding recommendations
- Privacy-compliant targeting strategies""",
        expected_output="Mobile app inventory strategy recommendations",
        agent=mobile_agent,
        context=[pricing_task, availability_task],
    )

    return Crew(
        agents=[mobile_agent, pricing_agent, availability_agent],
        tasks=[pricing_task, availability_task, strategy_task],
        process=Process.sequential,
        verbose=settings.crew_verbose,
        memory=settings.crew_memory_enabled,
    )


def create_native_crew() -> Crew:
    """Create a crew for native inventory operations.

    Returns:
        Crew: Native inventory crew with pricing and availability agents
    """
    settings = get_settings()

    native_agent = create_native_inventory_agent()
    pricing_agent = create_pricing_agent()
    availability_agent = create_availability_agent()

    pricing_task = Task(
        description="""Provide native inventory pricing guidance:
- In-feed native CPM rates
- Content recommendation widget pricing
- Sponsored content flat-fee rates
- Native video premiums
- Performance-based pricing options (CPC)""",
        expected_output="Native pricing recommendations by format",
        agent=pricing_agent,
    )

    availability_task = Task(
        description="""Assess native inventory availability:
- In-feed native positions and capacity
- Content recommendation widget impressions
- Sponsored content slots available
- Native video inventory
- Editorial integration opportunities""",
        expected_output="Native availability forecast with position breakdown",
        agent=availability_agent,
    )

    strategy_task = Task(
        description="""Based on pricing and availability input, develop
native inventory strategy recommendations:
- Editorial integration guidelines
- Native format mix optimization
- Sponsored content opportunities
- Performance vs brand native positioning""",
        expected_output="Native inventory strategy recommendations",
        agent=native_agent,
        context=[pricing_task, availability_task],
    )

    return Crew(
        agents=[native_agent, pricing_agent, availability_agent],
        tasks=[pricing_task, availability_task, strategy_task],
        process=Process.sequential,
        verbose=settings.crew_verbose,
        memory=settings.crew_memory_enabled,
    )


def create_proposal_review_crew(proposal_data: dict) -> Crew:
    """Create a crew for reviewing a specific proposal.

    Args:
        proposal_data: The proposal to review

    Returns:
        Crew: Proposal review crew with all functional agents including audience validation
    """
    settings = get_settings()

    # Create audience tools for the audience validator
    audience_tools = [
        AudienceValidationTool(),
        AudienceCapabilityTool(),
        CoverageCalculatorTool(),
    ]

    # Create agents
    proposal_review_agent = create_proposal_review_agent()
    pricing_agent = create_pricing_agent()
    availability_agent = create_availability_agent()
    upsell_agent = create_upsell_agent()
    audience_validator_agent = create_audience_validator_agent(tools=audience_tools)

    pricing_check_task = Task(
        description=f"""Evaluate the pricing in this proposal:

{proposal_data}

Check:
- Is requested price above our floor?
- Does price match buyer's tier (public/agency/advertiser)?
- What is our recommended price for this buyer?
- Are there volume discounts applicable?""",
        expected_output="Pricing evaluation with recommended price and rationale",
        agent=pricing_agent,
    )

    availability_check_task = Task(
        description=f"""Check availability for this proposal:

{proposal_data}

Verify:
- Can we deliver requested impressions?
- Is inventory available for the flight dates?
- Are there any targeting constraints that limit availability?
- What is our confidence level in delivery?""",
        expected_output="Availability assessment with delivery confidence",
        agent=availability_agent,
    )

    # New audience validation task
    audience_validation_task = Task(
        description=f"""Validate the audience targeting in this proposal using UCP:

{proposal_data}

Check:
- Can we fulfill the requested audience targeting?
- What is the estimated coverage percentage?
- Are there any gaps in audience capabilities?
- What is the UCP similarity score for this audience?
- Suggest alternatives if we cannot fully fulfill requirements.

Use the audience validation tools to compute UCP-based similarity scores.""",
        expected_output="""Audience validation with:
- Validation status (valid/partial_match/no_match)
- Coverage percentage
- UCP similarity score (0-1)
- Gaps and alternatives if applicable
- Targeting compatibility assessment""",
        agent=audience_validator_agent,
    )

    upsell_task = Task(
        description=f"""Identify upsell opportunities for this proposal:

{proposal_data}

Consider:
- Can we expand to additional inventory types?
- Are there volume upgrade opportunities?
- What alternative products might interest this buyer?
- If we reject, what alternatives can we propose?
- Consider audience-based upsells (broader targeting for more reach).""",
        expected_output="Upsell and cross-sell recommendations",
        agent=upsell_agent,
    )

    final_review_task = Task(
        description=f"""Based on pricing, availability, audience validation, and upsell input,
provide a final recommendation for this proposal:

{proposal_data}

Synthesize all inputs and provide:
- Final recommendation: Accept / Counter / Reject
- If Counter: Specific counter-terms (including audience adjustments if needed)
- If Reject: Clear explanation and alternatives
- Audience coverage impact on delivery confidence
- Upsell opportunities to pursue""",
        expected_output="""A structured proposal decision with:
- decision: accept, counter, or reject
- rationale: brief explanation
- counter_price_cpm and counter_terms if countering
- rejection_reason if rejecting
- audience_summary
- upsell_opportunities""",
        agent=proposal_review_agent,
        context=[
            pricing_check_task,
            availability_check_task,
            audience_validation_task,
            upsell_task,
        ],
        output_pydantic=ProposalReviewOutput,
    )

    return Crew(
        agents=[
            proposal_review_agent,
            pricing_agent,
            availability_agent,
            audience_validator_agent,
            upsell_agent,
        ],
        tasks=[
            pricing_check_task,
            availability_check_task,
            audience_validation_task,
            upsell_task,
            final_review_task,
        ],
        process=Process.sequential,
        verbose=settings.crew_verbose,
        memory=settings.crew_memory_enabled,
    )


def create_linear_tv_crew() -> Crew:
    """Create a crew for linear TV inventory operations.

    Covers broadcast, cable, MVPD local avails, addressable TV, and
    programmatic linear. Supports three seller perspectives: direct
    publishers, MVPD operators, and reseller/SSPs.

    Returns:
        Crew: Linear TV inventory crew with pricing and availability agents
    """
    settings = get_settings()

    linear_tv_agent = create_linear_tv_inventory_agent()
    pricing_agent = create_pricing_agent()
    availability_agent = create_availability_agent()

    # Linear TV pricing tools
    linear_pricing_tools = [
        LinearPricingTool(),
        ScatterPricingTool(),
        UpfrontDealCalculator(),
    ]

    # Linear TV avails tools
    linear_avails_tools = [
        LinearAvailsTool(),
        DMAAvailsTool(),
        MakegoodPoolTool(),
        AddressableTargetingTool(),
    ]

    pricing_task = Task(
        description="""Provide linear TV inventory pricing guidance:
- Daypart-specific rates (primetime, daytime, late night, early morning, news)
- Upfront vs scatter pricing and rate-of-change calculations
- Sports and event premium pricing
- Dual currency output: both CPP and CPM for every recommendation
- Holding company tier discounts and volume commitments
- Reseller margin calculation for aggregated supply
- Addressable TV CPM uplift estimates""",
        expected_output="Linear TV pricing with dual CPP/CPM and market type breakdown",
        agent=pricing_agent,
        tools=linear_pricing_tools,
    )

    availability_task = Task(
        description="""Assess linear TV inventory availability:
- Broadcast calendar avails by network and daypart
- Sell-through rates (primetime typically 85%+, daytime 40-60%)
- Local DMA avails for MVPD operators
- Makegood pool for active campaigns with underdelivery
- Addressable TV household coverage and targeting capacity
- Programmatic linear availability (FreeWheel biddable inventory)""",
        expected_output="Linear TV availability with sell-through and addressable coverage",
        agent=availability_agent,
        tools=linear_avails_tools,
    )

    strategy_task = Task(
        description="""Based on pricing and availability input, develop
linear TV inventory strategy recommendations:
- Optimal daypart allocation across broadcast and cable
- Network mix recommendations (broadcast vs cable vs MVPD)
- Upfront vs scatter market positioning
- Cross-platform companion opportunities (linear + CTV + digital)
- Deal structure recommendation (PG, PMP, or traditional IO)
- Makegood provisions and audience guarantee strategy
- Addressable TV overlay opportunities""",
        expected_output="Linear TV inventory strategy with deal structure recommendation",
        agent=linear_tv_agent,
        context=[pricing_task, availability_task],
    )

    return Crew(
        agents=[linear_tv_agent, pricing_agent, availability_agent],
        tasks=[pricing_task, availability_task, strategy_task],
        process=Process.sequential,
        verbose=settings.crew_verbose,
        memory=settings.crew_memory_enabled,
    )
