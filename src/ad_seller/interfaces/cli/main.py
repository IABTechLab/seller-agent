# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""CLI interface for seller operations.

Provides commands for:
- Setting up products
- Viewing catalog
- Processing proposals
- Generating deals
- Checking status
"""

import asyncio
from typing import Optional

# Load .env file before any other imports that might need env vars
from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True))

import typer  # noqa: E402
from rich.console import Console  # noqa: E402
from rich.panel import Panel  # noqa: E402
from rich.table import Table  # noqa: E402

app = typer.Typer(
    name="ad-seller",
    help="Ad Seller System CLI - Manage publisher inventory and deals",
)
console = Console()


@app.command()
def init(
    organization_name: str = typer.Option(
        "My Publisher",
        "--name",
        "-n",
        help="Seller organization name",
    ),
):
    """Initialize the seller system and set up default products."""
    from ...flows import ProductSetupFlow

    console.print(Panel("Initializing Ad Seller System...", title="Setup"))

    flow = ProductSetupFlow()
    flow.kickoff()

    console.print(f"[green]✓[/green] Organization '{organization_name}' initialized")
    console.print(f"[green]✓[/green] Created {len(flow.state.products)} default products")

    # Show products
    table = Table(title="Product Catalog")
    table.add_column("Product ID")
    table.add_column("Name")
    table.add_column("Type")
    table.add_column("Base CPM")

    for product in flow.state.products.values():
        table.add_row(
            product.product_id,
            product.name,
            product.inventory_type,
            f"${product.base_cpm:.2f}",
        )

    console.print(table)


@app.command()
def catalog():
    """View the product catalog."""
    from ...flows import ProductSetupFlow

    flow = ProductSetupFlow()
    flow.kickoff()

    table = Table(title="Product Catalog")
    table.add_column("ID", style="cyan")
    table.add_column("Name")
    table.add_column("Type", style="yellow")
    table.add_column("Base CPM", style="green")
    table.add_column("Floor CPM", style="red")
    table.add_column("Deal Types")

    for product in flow.state.products.values():
        deal_types = ", ".join(dt.value[:2].upper() for dt in product.supported_deal_types)
        table.add_row(
            product.product_id,
            product.name,
            product.inventory_type,
            f"${product.base_cpm:.2f}",
            f"${product.floor_cpm:.2f}",
            deal_types,
        )

    console.print(table)


@app.command()
def price(
    product_id: str = typer.Argument(..., help="Product ID to get pricing for"),
    tier: str = typer.Option(
        "public", "--tier", "-t", help="Buyer tier: public, agency, advertiser"
    ),
    volume: int = typer.Option(0, "--volume", "-v", help="Impression volume for discounts"),
):
    """Get pricing for a product based on buyer tier."""
    from ...engines.pricing_rules_engine import PricingRulesEngine
    from ...flows import ProductSetupFlow
    from ...models.buyer_identity import AccessTier, BuyerContext, BuyerIdentity
    from ...models.pricing_tiers import TieredPricingConfig

    # Get products
    flow = ProductSetupFlow()
    flow.kickoff()

    product = flow.state.products.get(product_id)
    if not product:
        console.print(f"[red]Product not found: {product_id}[/red]")
        raise typer.Exit(1)

    # Create buyer context
    tier_map = {
        "public": AccessTier.PUBLIC,
        "seat": AccessTier.SEAT,
        "agency": AccessTier.AGENCY,
        "advertiser": AccessTier.ADVERTISER,
    }
    access_tier = tier_map.get(tier.lower(), AccessTier.PUBLIC)

    identity = BuyerIdentity()
    if access_tier == AccessTier.AGENCY:
        identity.agency_id = "test-agency"
    elif access_tier == AccessTier.ADVERTISER:
        identity.agency_id = "test-agency"
        identity.advertiser_id = "test-advertiser"

    context = BuyerContext(
        identity=identity,
        is_authenticated=access_tier != AccessTier.PUBLIC,
    )

    # Calculate price
    config = TieredPricingConfig(seller_organization_id="default")
    engine = PricingRulesEngine(config)

    decision = engine.calculate_price(
        product_id=product_id,
        base_price=product.base_cpm,
        buyer_context=context,
        volume=volume,
    )

    console.print(Panel(f"Pricing for [cyan]{product.name}[/cyan]", title="Pricing"))
    console.print(f"Buyer Tier: [yellow]{decision.buyer_tier}[/yellow]")
    console.print(f"Base Price: ${decision.base_price:.2f} CPM")
    console.print(f"Tier Discount: {decision.tier_discount * 100:.0f}%")
    console.print(f"Volume Discount: {decision.volume_discount * 100:.1f}%")
    console.print(f"[green]Final Price: ${decision.final_price:.2f} CPM[/green]")
    console.print(f"\nRationale: {decision.rationale}")


@app.command()
def deal(
    buyer_request: str = typer.Argument(..., help="Natural language deal request"),
    agency: Optional[str] = typer.Option(None, "--agency", "-a", help="Agency ID"),
    advertiser: Optional[str] = typer.Option(None, "--advertiser", help="Advertiser ID"),
):
    """Process a deal request."""
    from ...flows import DealRequestFlow
    from ...models.buyer_identity import BuyerContext, BuyerIdentity

    # Create buyer context
    identity = BuyerIdentity(
        agency_id=agency,
        advertiser_id=advertiser,
    )
    context = BuyerContext(
        identity=identity,
        is_authenticated=agency is not None,
    )

    console.print(Panel(f"Processing: [cyan]{buyer_request}[/cyan]", title="Deal Request"))

    flow = DealRequestFlow()
    result = flow.process_request(
        request_text=buyer_request,
        buyer_context=context,
    )

    if result["deal"]:
        console.print(result["response"])
    else:
        console.print(f"[yellow]{result['response']}[/yellow]")


@app.command()
def connect(
    url: str = typer.Option(
        "https://agentic-direct-server-hwgrypmndq-uk.a.run.app",
        "--url",
        "-u",
        help="OpenDirect server URL",
    ),
):
    """Test connection to OpenDirect server."""
    from ...clients import Protocol, UnifiedClient

    console.print(f"Connecting to [cyan]{url}[/cyan]...")

    async def test_connection():
        try:
            async with UnifiedClient(base_url=url, protocol=Protocol.OPENDIRECT_21) as client:
                result = await client.list_organizations()
                if result.success:
                    console.print("[green]✓ Connection successful![/green]")
                    orgs = result.data or []
                    console.print(f"Found {len(orgs)} organizations")
                else:
                    console.print(f"[red]✗ Connection failed: {result.error}[/red]")
        except Exception as e:
            console.print(f"[red]✗ Connection error: {e}[/red]")

    asyncio.run(test_connection())


@app.command()
def chat():
    """Start interactive chat mode for buyer interactions."""
    from ...flows import DealRequestFlow
    from ...models.buyer_identity import BuyerContext, BuyerIdentity

    console.print(
        Panel(
            "Ad Seller Chat Interface\n"
            "Ask about inventory, pricing, or request deals.\n"
            "Type 'quit' to exit.",
            title="Welcome",
        )
    )

    # Create anonymous buyer context (can be upgraded)
    identity = BuyerIdentity()
    context = BuyerContext(identity=identity, is_authenticated=False)

    while True:
        try:
            user_input = console.input("\n[bold blue]You:[/bold blue] ")

            if user_input.lower() in ["quit", "exit", "q"]:
                console.print("[yellow]Goodbye![/yellow]")
                break

            if not user_input.strip():
                continue

            # Determine query type and route
            input_lower = user_input.lower()

            if any(word in input_lower for word in ["deal", "create", "book", "buy"]):
                flow = DealRequestFlow()
                result = flow.process_request(
                    request_text=user_input,
                    buyer_context=context,
                )
                console.print(f"\n[bold green]Seller:[/bold green] {result['response']}")
            else:
                # Discovery query
                console.print(
                    "\n[bold green]Seller:[/bold green] Let me help you with that inquiry..."
                )
                console.print("Available inventory types: Display, Video, CTV, Mobile App, Native")
                console.print(
                    "Ask about pricing, availability, or say 'create deal' to start a transaction."
                )

        except KeyboardInterrupt:
            console.print("\n[yellow]Goodbye![/yellow]")
            break


if __name__ == "__main__":
    app()
