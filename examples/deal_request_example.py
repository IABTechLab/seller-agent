# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Deal Request workflow example.

Demonstrates how the seller agent creates deals for buyers:
1. Receives a natural language request from a buyer
2. Validates, prices, and creates a Deal ID
3. Provides activation instructions for the DSP

Compatible with traditional DSPs (TTD, Amazon DSP, DV360) and
agentic buyer workflows (Deal Jockey sub-agent).
"""

from ad_seller.flows import DealRequestFlow
from ad_seller.models.buyer_identity import BuyerContext, BuyerIdentity


def main():
    """Run deal request workflow example."""
    print("=" * 60)
    print("Deal Request Workflow Example")
    print("=" * 60)

    # Scenario 1: Anonymous inquiry
    print("\n1. Anonymous inquiry (public tier)...")
    flow1 = DealRequestFlow()
    result1 = flow1.process_request(
        request_text="What CTV inventory do you have available?",
        buyer_context=None,
    )
    print(f"   Response type: {result1['request_type']}")
    print(f"   Status: {result1['status']}")

    # Scenario 2: Agency-authenticated deal request
    print("\n2. Agency deal request...")
    agency_identity = BuyerIdentity(
        agency_id="agency-wpp-123",
        agency_name="GroupM",
        agency_holding_company="WPP",
    )
    agency_context = BuyerContext(
        identity=agency_identity,
        is_authenticated=True,
    )

    flow2 = DealRequestFlow()
    result2 = flow2.process_request(
        request_text="I want to create a CTV deal for 5 million impressions",
        buyer_context=agency_context,
    )

    print(result2["response"])

    if result2.get("deal"):
        deal = result2["deal"]
        print(f"\n   Deal created successfully!")
        print(f"   Deal ID: {deal['deal_id']}")
        print(f"   Price: ${deal['price']:.2f} CPM")

    # Scenario 3: Advertiser-level pricing
    print("\n3. Advertiser-level deal request (best pricing)...")
    advertiser_identity = BuyerIdentity(
        agency_id="agency-wpp-123",
        agency_name="GroupM",
        advertiser_id="adv-unilever-001",
        advertiser_name="Unilever",
    )
    advertiser_context = BuyerContext(
        identity=advertiser_identity,
        is_authenticated=True,
    )

    flow3 = DealRequestFlow()
    result3 = flow3.process_request(
        request_text="Create a preferred deal for video inventory",
        buyer_context=advertiser_context,
    )

    print(result3["response"])

    # Scenario 4: Same advertiser through different agency
    # (demonstrates cross-agency pricing consistency)
    print("\n4. Same advertiser via different agency...")
    different_agency_identity = BuyerIdentity(
        agency_id="agency-omnicom-456",
        agency_name="OMD",
        advertiser_id="adv-unilever-001",  # Same advertiser
        advertiser_name="Unilever",
    )
    different_agency_context = BuyerContext(
        identity=different_agency_identity,
        is_authenticated=True,
    )

    flow4 = DealRequestFlow()
    result4 = flow4.process_request(
        request_text="What's the pricing for video?",
        buyer_context=different_agency_context,
    )

    print(result4["response"])
    print("\n   Note: Same advertiser gets consistent pricing regardless of agency!")

    print("\n" + "=" * 60)
    print("Deal request example completed!")


if __name__ == "__main__":
    main()
