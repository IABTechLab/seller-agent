# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Configuration settings for the Ad Seller System."""

from functools import lru_cache
from typing import Optional

from dotenv import find_dotenv
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Find .env file by searching up from current working directory
_ENV_FILE = find_dotenv(usecwd=True)


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=_ENV_FILE if _ENV_FILE else None,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # API Keys
    anthropic_api_key: str
    openai_api_key: Optional[str] = None
    google_api_key: Optional[str] = None

    # OpenDirect Configuration
    opendirect_base_url: str = "http://localhost:3000"
    opendirect_api_key: Optional[str] = None
    opendirect_token: Optional[str] = None

    # Protocol Selection
    default_protocol: str = "opendirect21"  # opendirect21, a2a

    # LLM Configuration
    # Supported providers: anthropic (default), openai, gemini, bedrock
    # Set DEFAULT_LLM_MODEL to switch provider, e.g.:
    #   anthropic/claude-sonnet-4-5-20250929  (requires ANTHROPIC_API_KEY)
    #   openai/gpt-4o                          (requires OPENAI_API_KEY)
    #   gemini/gemini-2.5-flash                (requires GOOGLE_API_KEY)
    #   bedrock/us.anthropic.claude-sonnet-4-5-20250929-v1:0 (requires AWS creds)
    default_llm_model: str = "anthropic/claude-sonnet-4-5-20250929"
    manager_llm_model: str = "anthropic/claude-opus-4-20250514"
    llm_temperature: float = 0.3
    llm_max_tokens: int = 4096

    # Alternative: any OpenAI-wire-compatible endpoint (NVIDIA NIM, Ollama,
    # HuggingFace TGI, vLLM, ...). Set OPENAI_COMPATIBLE_LLM_API_BASE_URL
    # alongside DEFAULT_LLM_MODEL/MANAGER_LLM_MODEL (using the raw model id
    # the endpoint expects) to route through that endpoint instead of a named
    # provider above. OPENAI_COMPATIBLE_LLM_API_KEY is optional — omit it for
    # endpoints like a local Ollama server that don't require one.
    openai_compatible_llm_api_key: Optional[str] = None
    openai_compatible_llm_api_base_url: Optional[str] = None

    # Database / Storage Configuration
    database_url: str = "sqlite:///./ad_seller.db"
    redis_url: Optional[str] = None
    storage_type: str = "sqlite"  # sqlite, redis, hybrid
    postgres_pool_min: int = 2
    postgres_pool_max: int = 10

    # CrewAI Configuration
    crew_memory_enabled: bool = True
    crew_verbose: bool = True
    crew_max_iterations: int = 15

    # Proposal-flow time budget. POST /proposals runs the
    # proposal-review crew in-request; a real LLM crew was measured at
    # ~10m46s while wire buyers time out at ~30s (NegotiationClient default;
    # made configurable), so every negotiation died at round 0.
    # The crew gets this many seconds; past the budget the flow falls back
    # to the existing deterministic rule-based evaluation (the same path
    # used when the LLM fails) so the request answers within wire timeouts.
    # <= 0 disables the bound (the previous unbounded behavior, e.g. for
    # non-wire deployments where the crew's depth matters more than latency).
    # Env: PROPOSAL_FLOW_TIME_BUDGET (documented) or the field name.
    proposal_flow_time_budget_seconds: float = Field(
        default=20.0,
        validation_alias=AliasChoices(
            "proposal_flow_time_budget",
            "proposal_flow_time_budget_seconds",
        ),
    )

    # Seller Identity
    seller_organization_id: Optional[str] = None
    seller_organization_name: str = "Default Publisher"

    # Supply Chain Transparency
    sellers_json_path: Optional[str] = None  # Path to sellers.json file (IAB spec)

    # Inventory Sync Scheduling
    inventory_sync_enabled: bool = False  # Enable periodic inventory sync
    inventory_sync_interval_minutes: int = 60  # Sync interval in minutes
    inventory_sync_include_archived: bool = False  # Include archived ad units

    # Ad Server Configuration
    ad_server_type: str = "google_ad_manager"  # google_ad_manager, freewheel, csv, s3
    csv_data_dir: str = "./data/csv/samples/ctv_streaming"  # Path to CSV data directory

    # S3 Ad Server Configuration (AD_SERVER_TYPE=s3)
    s3_data_bucket: str = ""  # S3 bucket for inventory data (e.g. a4a-data-omixaj)
    s3_data_prefix: str = "seller-data/"  # S3 key prefix for CSV files
    s3_data_region: Optional[str] = None  # Region (defaults to AWS_REGION or us-west-2)

    # Google Ad Manager (GAM) Configuration
    gam_enabled: bool = False  # Feature flag to enable GAM integration
    gam_network_code: Optional[str] = None  # GAM network ID
    gam_json_key_path: Optional[str] = None  # Path to service account JSON key
    gam_application_name: str = "AdSellerSystem"  # Application name for GAM API
    gam_api_version: str = "v202505"  # SOAP API version
    gam_default_trafficker_id: Optional[str] = None  # Default trafficker user ID

    # FreeWheel Configuration (alternative ad server)
    freewheel_enabled: bool = False  # Feature flag to enable FreeWheel integration
    freewheel_api_url: Optional[str] = None  # Legacy — use MCP URLs below
    freewheel_api_key: Optional[str] = None  # Legacy — use MCP auth below
    freewheel_network_id: Optional[str] = None  # Publisher network/account ID in FreeWheel
    # Inventory access mode: controls what the agent can see
    #   "full"       — agent calls list_inventory() and sees all available inventory
    #   "deals_only" — agent only sees pre-configured deals the publisher set up
    #                   for agentic selling in FreeWheel (template deals / packages)
    freewheel_inventory_mode: str = "deals_only"  # full, deals_only
    # Streaming Hub MCP — publisher-side (inventory, deals, audiences)
    # Auth: OAuth 2.1 PKCE via /mcp/oauth.
    freewheel_sh_mcp_url: Optional[str] = None  # e.g. https://shmcp.freewheel.com
    freewheel_sh_oauth_client_id: Optional[str] = None
    freewheel_sh_oauth_client_name: str = "Ad Seller Agent"
    freewheel_sh_oauth_redirect_uri: str = "http://127.0.0.1:8765/callback"
    freewheel_sh_oauth_scope: str = "api"
    freewheel_sh_oauth_token_path: str = "~/.config/ad-seller/freewheel-sh-oauth.json"
    # Buyer Cloud MCP — demand-side (campaign execution, creatives, reporting)
    # Auth: OAuth 2.1 PKCE via /mcp/oauth.
    freewheel_bc_mcp_url: Optional[str] = None  # e.g. https://bcmcp.freewheel.com
    freewheel_bc_oauth_client_id: Optional[str] = None
    freewheel_bc_oauth_client_name: str = "Ad Seller Agent"
    freewheel_bc_oauth_redirect_uri: str = "http://127.0.0.1:8766/callback"
    freewheel_bc_oauth_scope: str = "api"
    freewheel_bc_oauth_token_path: str = "~/.config/ad-seller/freewheel-bc-oauth.json"

    # SSP Connectors (publishers can configure multiple SSPs)
    # Comma-separated list of SSP names to enable
    ssp_connectors: str = ""  # e.g. "pubmatic,magnite"
    # Routing rules: inventory_type:ssp_name pairs, comma-separated
    ssp_routing_rules: str = ""  # e.g. "ctv:pubmatic,display:magnite"
    # PubMatic SSP
    pubmatic_mcp_url: Optional[str] = None  # e.g. https://mcp.pubmatic.com/sses
    pubmatic_api_key: Optional[str] = None
    # Magnite SSP (REST API)
    magnite_api_url: Optional[str] = None
    magnite_api_key: Optional[str] = None
    # Index Exchange SSP (REST API)
    index_exchange_api_url: Optional[str] = None
    index_exchange_api_key: Optional[str] = None

    # Deal Sync Connectors (external deal-sync services, peer of SSP connectors)
    # Comma-separated list of provider names to enable
    deal_sync_connectors: str = ""  # e.g. "deals_api_mcp"  (env: DEAL_SYNC_CONNECTORS)
    # IAB Deals MCP (deals-api-mcp server — HTTP Streamable transport)
    deals_api_mcp_url: Optional[str] = None  # e.g. http://localhost:3100/mcp
    deals_api_mcp_key: Optional[str] = None  # IAB_DEALS_API_KEY on the MCP server
    deals_api_mcp_seller_origin: str = "publisher.example.com"  # origin field for deals_create

    # Pricing Configuration
    default_currency: str = "USD"
    min_deal_value: float = 1000.0
    default_price_floor_cpm: float = 5.0

    # Yield Optimization
    yield_optimization_enabled: bool = True
    programmatic_floor_multiplier: float = 1.2
    preferred_deal_discount_max: float = 0.15

    # Event Bus / Human-in-the-Loop Configuration
    event_bus_enabled: bool = True
    # Durable fallback for audit-class events (see events/audit_fallback.py).
    # When the event bus fails for an audit-class event, the event is appended
    # to this JSONL file (fsynced per write) instead of being dropped.
    audit_fallback_path: str = "data/audit_fallback.jsonl"
    approval_gate_enabled: bool = False  # Default off, opt-in
    approval_timeout_hours: int = 24
    approval_required_flows: str = (
        ""  # Comma-separated gate names: "proposal_decision,deal_registration"
    )
    # Mandatory (threshold-driven) approval gate. When > 0, any proposal whose
    # gross deal value (CPM x impressions / 1000) is >= this amount ALWAYS
    # requires human approval before it can finalize — regardless of the
    # opt-in ``approval_gate_enabled`` toggle. A high-value deal can never
    # auto-finalize. 0.0 disables the threshold gate (opt-in behavior only).
    approval_required_above_value: float = 0.0

    # Session Configuration
    session_ttl_seconds: int = 604800  # 7 days
    session_max_messages: int = 200

    # Agent Registry
    # Primary registry is IAB Tech Lab AAMP. Additional registries can be
    # configured via agent_registry_extra_urls (comma-separated). Each gets
    # a unique registry_id derived from its URL for multi-source tracking.
    agent_registry_enabled: bool = True
    agent_registry_url: str = "https://tools.iabtechlab.com/agent-registry"
    agent_registry_extra_urls: str = ""  # Comma-separated additional registry URLs
    # Real IAB AAMP agent registry (EP-5.1). When aamp_registry_url is set
    # (env AAMP_REGISTRY_URL), registry verification/lookup/search go
    # through the shared contract library's RegistryClient against the real
    # /api/agents API; when empty (the default), the legacy stub clients
    # above are used. The swap is config, not code.
    # AAMP_REGISTRY_AUTH_TOKEN carries the bearer JWT — never log it.
    aamp_registry_url: str = ""
    aamp_registry_auth_token: str = ""
    auto_approve_registered_agents: bool = True
    require_approval_for_unregistered: bool = True
    seller_agent_url: str = "http://localhost:8000"
    seller_agent_name: str = "Ad Seller Agent"

    # API Key Authentication
    api_key_auth_enabled: bool = True
    api_key_default_expiry_days: Optional[int] = None  # None = never expires


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
