# Publisher Setup Guide

Step-by-step guide for publishers deploying the IAB Tech Lab Seller Agent.

## Recommended: Interactive Setup Wizard

The fastest way to set up your seller agent is the **interactive setup wizard** via Claude, ChatGPT, or any MCP-compatible assistant. It walks you through every configuration step conversationally.

1. **Developer** runs [Developer Setup](developer-setup.md) in Claude Code — deploys server, connects ad server + SSPs, generates credentials
2. **Publisher ops** adds the seller agent to [Claude](claude-desktop-setup.md) (desktop or web), [ChatGPT, or Codex](chatgpt-setup.md) — the wizard auto-launches and guides through business setup

> See [Claude Setup Guide](claude-desktop-setup.md) or [ChatGPT & AI IDEs Guide](chatgpt-setup.md) for the full walkthrough.

## Manual Setup Checklist

If you prefer to configure manually (or need reference for what the wizard does):

1. [Configuration & Environment](configuration.md) -- Set env vars, connect your ad server
2. [Inventory Sync](inventory-sync.md) -- Connect GAM/FreeWheel, sync your inventory
3. [Media Kit](media-kit.md) -- Set up your inventory catalog with packages, tiers, and featured items
4. [Pricing & Access Tiers](pricing-rules.md) -- Configure buyer pricing tiers, discounts, negotiation limits
5. [Approval & Human-in-the-Loop](approval-rules.md) -- Set up approval gates for deals
6. [Buyer & Agent Management](agent-management.md) -- Manage API keys, agent trust, buyer access

## Prerequisites

Before starting, ensure you have:

- **Python 3.11+** installed
- An **LLM provider API key** (`LLM_API_KEY`) for the LLM-powered specialist agents
- (Optional) **Google Ad Manager** credentials or **FreeWheel** MCP URL for live inventory sync
- (Optional) **SSP API keys** for PubMatic, Index Exchange, or Magnite
- (Optional) A public URL for agent discovery if participating in the IAB AAMP ecosystem

## Architecture Overview

The seller agent runs as a FastAPI service that exposes:

- **REST API** for buyer agents and operators
- **MCP** and **A2A** protocol interfaces for agent-to-agent communication
- **OpenDirect 2.1** compliance for deal lifecycle management
- **Human-in-the-loop** approval gates for operator oversight

```mermaid
graph LR
    BuyerAgent[Buyer Agent] -->|REST / A2A| SellerAPI[Seller API]
    Operator[Publisher Operator] -->|REST| SellerAPI
    SellerAPI --> Crew[CrewAI Agents]
    SellerAPI --> GAM[Google Ad Manager]
    SellerAPI --> Storage[(SQLite / Redis)]
    SellerAPI --> Registry[AAMP Registry]
```
