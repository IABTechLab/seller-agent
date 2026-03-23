# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Configuration settings for the Ad Seller System."""

from functools import lru_cache
from typing import Optional

from dotenv import find_dotenv
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

    # OpenDirect Configuration
    opendirect_base_url: str = "http://localhost:3000"
    opendirect_api_key: Optional[str] = None
    opendirect_token: Optional[str] = None

    # Protocol Selection
    default_protocol: str = "opendirect21"  # opendirect21, a2a

    # LLM Configuration
    default_llm_model: str = "anthropic/claude-sonnet-4-5-20250929"
    manager_llm_model: str = "anthropic/claude-opus-4-20250514"
    llm_temperature: float = 0.3
    llm_max_tokens: int = 4096

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
    ad_server_type: str = "google_ad_manager"  # google_ad_manager, freewheel

    # Google Ad Manager (GAM) Configuration
    gam_enabled: bool = False  # Feature flag to enable GAM integration
    gam_network_code: Optional[str] = None  # GAM network ID
    gam_json_key_path: Optional[str] = None  # Path to service account JSON key
    gam_application_name: str = "AdSellerSystem"  # Application name for GAM API
    gam_api_version: str = "v202411"  # SOAP API version
    gam_default_trafficker_id: Optional[str] = None  # Default trafficker user ID

    # FreeWheel Configuration (alternative ad server)
    freewheel_enabled: bool = False  # Feature flag to enable FreeWheel integration
    freewheel_api_url: Optional[str] = None  # Legacy — use MCP URLs below
    freewheel_api_key: Optional[str] = None  # Legacy — use MCP auth below
    # Streaming Hub MCP (publisher-side: inventory, deals, audiences)
    freewheel_sh_mcp_url: Optional[str] = None  # e.g. https://shmcp.freewheel.com
    freewheel_sh_username: Optional[str] = None
    freewheel_sh_password: Optional[str] = None
    # Buyer Cloud MCP (demand-side: campaign execution, creatives, reporting)
    freewheel_bc_mcp_url: Optional[str] = None  # e.g. https://bcmcp.freewheel.com
    freewheel_bc_client_id: Optional[str] = None  # OAuth 2.0
    freewheel_bc_client_secret: Optional[str] = None
    freewheel_bc_email: Optional[str] = None  # Session auth
    freewheel_bc_password: Optional[str] = None
    freewheel_bc_buzz_key: Optional[str] = None

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
    approval_gate_enabled: bool = False  # Default off, opt-in
    approval_timeout_hours: int = 24
    approval_required_flows: str = ""  # Comma-separated gate names: "proposal_decision,deal_registration"

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
