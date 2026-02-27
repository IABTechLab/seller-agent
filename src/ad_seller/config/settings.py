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
    storage_type: str = "sqlite"  # sqlite, redis

    # CrewAI Configuration
    crew_memory_enabled: bool = True
    crew_verbose: bool = True
    crew_max_iterations: int = 15

    # Seller Identity
    seller_organization_id: Optional[str] = None
    seller_organization_name: str = "Default Publisher"

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
    freewheel_api_url: Optional[str] = None
    freewheel_api_key: Optional[str] = None

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


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
