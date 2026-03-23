# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Supply chain models and sellers.json parser (IAB Tech Lab spec)."""

import json
from pathlib import Path
from typing import Optional

from pydantic import BaseModel


class SellersJsonSeller(BaseModel):
    """A seller entry from sellers.json (IAB spec)."""

    seller_id: str
    name: str
    domain: str
    seller_type: str  # PUBLISHER, INTERMEDIARY, BOTH
    is_confidential: int = 0
    is_passthrough: int = 0
    comment: Optional[str] = None


class SellersJsonFile(BaseModel):
    """Parsed sellers.json file (IAB spec)."""

    contact_email: Optional[str] = None
    contact_address: Optional[str] = None
    version: str = "1.0"
    identifiers: list[dict] = []
    sellers: list[SellersJsonSeller] = []


class SchainNode(BaseModel):
    """OpenRTB SupplyChain node (schain.nodes[])."""

    asi: str  # Account System Identifier (canonical domain)
    sid: str  # Seller ID within the exchange
    hp: int = 1  # Whether payment flows through this node (1=yes)
    rid: Optional[str] = None  # Request ID
    name: Optional[str] = None
    domain: Optional[str] = None


class Schain(BaseModel):
    """OpenRTB SupplyChain object."""

    ver: str = "1.0"
    complete: int = 1  # 1 = all nodes listed, 0 = partial
    nodes: list[SchainNode] = []


def load_sellers_json(path: Optional[str] = None) -> Optional[SellersJsonFile]:
    """Load and parse a sellers.json file.

    Args:
        path: File path to sellers.json. If None, returns None.

    Returns:
        Parsed SellersJsonFile or None if path not configured/found.
    """
    if not path:
        return None

    filepath = Path(path)
    if not filepath.exists():
        return None

    with open(filepath) as f:
        data = json.load(f)

    return SellersJsonFile(**data)


def build_schain_from_sellers_json(
    sellers_json: SellersJsonFile,
    seller_id: str,
) -> Schain:
    """Build an OpenRTB schain object from sellers.json data.

    The schain represents the supply path from this seller.
    Direct publishers have a single node; intermediaries add hops.

    Args:
        sellers_json: Parsed sellers.json data.
        seller_id: The primary seller's ID in the file.

    Returns:
        OpenRTB-compatible Schain object.
    """
    nodes = []
    for seller in sellers_json.sellers:
        nodes.append(SchainNode(
            asi=seller.domain,
            sid=seller.seller_id,
            hp=1,
            name=seller.name,
            domain=seller.domain,
        ))

    # complete=1 if the primary seller is a PUBLISHER (full chain known)
    primary = next((s for s in sellers_json.sellers if s.seller_id == seller_id), None)
    complete = 1 if (primary and primary.seller_type == "PUBLISHER") else 0

    return Schain(ver="1.0", complete=complete, nodes=nodes)
