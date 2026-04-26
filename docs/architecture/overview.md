# Architecture Overview

The Ad Seller Agent is a layered system built on FastAPI with CrewAI agent flows for intelligent decision-making.

## Access Paths

The seller agent exposes three protocols for different client types:

```mermaid
graph LR
    subgraph "Buyer Agent"
        MCP_C[MCP Client]
        A2A_C[A2A Client]
    end

    subgraph "Human / Dashboard"
        REST_C[REST Client]
    end

    subgraph "Seller Agent"
        MCP_S["/mcp/mcp (Streamable HTTP)<br/>MCP Server"]
        A2A_S["/a2a/seller/jsonrpc<br/>A2A Server"]
        REST_S["REST API<br/>58 endpoints"]
        NLP[NL Processing]
        TOOLS[Seller Tools]
        FLOWS[CrewAI Flows]
        INFRA[Storage & Engines]
    end

    MCP_C -->|Structured tool calls| MCP_S
    A2A_C -->|Natural language JSON-RPC| A2A_S
    REST_C -->|HTTP verbs| REST_S

    MCP_S --> TOOLS
    A2A_S --> NLP --> TOOLS
    REST_S --> FLOWS

    TOOLS --> FLOWS
    FLOWS --> INFRA
```

| Path | Flow | Best For |
|------|------|----------|
| **MCP** | Buyer Agent &rarr; MCP &rarr; Seller Tools &rarr; CrewAI Flows &rarr; Storage/Engines | Automated workflows, deterministic tool calls |
| **A2A** | Buyer Agent &rarr; A2A &rarr; NL Processing &rarr; Seller Tools &rarr; CrewAI Flows | Discovery, negotiation, conversational queries |
| **REST** | Human/Dashboard &rarr; REST API &rarr; CrewAI Flows &rarr; Storage/Engines | Operator dashboards, non-agent clients |

See [MCP Protocol](../api/mcp.md), [A2A Protocol](../api/a2a.md), and [API Overview](../api/overview.md) for details on each.

## System Architecture

```mermaid
graph TB
    subgraph External
        BA[Buyer Agent]
        AAMP[AAMP Registry]
        DSP[DSP / Ad Server]
    end

    subgraph "Seller Agent"
        subgraph "Protocol Layer"
            MCP[MCP Server<br/>/mcp/mcp Streamable HTTP]
            A2A[A2A Server<br/>/a2a/seller/jsonrpc]
            API[REST API<br/>58 endpoints, 19 tags]
        end

        AUTH[Auth & API Keys]
        REG[Agent Registry]

        subgraph "Business Logic"
            PE[Pricing Engine]
            NE[Negotiation Engine]
            YO[Yield Optimizer]
            MK[Media Kit Service]
        end

        subgraph "CrewAI Flows"
            PSF[Product Setup Flow]
            PHF[Proposal Handling Flow]
            DGF[Deal Generation Flow]
            DIF[Discovery Inquiry Flow]
        end

        subgraph "Infrastructure"
            EB[Event Bus]
            SM[Order State Machine]
            CR[Change Request Manager]
            AG[Approval Gate]
            ST[(Storage Backend)]
        end
    end

    BA -->|MCP tool calls| MCP
    BA -->|A2A JSON-RPC| A2A
    BA -->|REST HTTP| API
    MCP --> AUTH
    A2A --> AUTH
    API --> AUTH
    API --> REG
    REG -->|A2A| AAMP

    MCP --> PE
    MCP --> NE
    A2A --> PE
    A2A --> NE
    API --> PE
    API --> NE
    API --> MK
    NE --> PE
    NE --> YO

    MCP --> PSF
    MCP --> PHF
    A2A --> DIF
    API --> PSF
    API --> PHF
    API --> DGF
    API --> DIF
    PHF --> PE
    PHF --> AG

    API --> SM
    API --> CR
    SM --> ST
    CR --> ST
    EB --> ST
    AG --> ST
    PE --> ST
    MK --> ST

    DGF -->|Deal ID| DSP
```

## Components

### API Layer

**FastAPI application** with 58 endpoints across 19 OpenAPI tags. Handles HTTP routing, request validation, authentication, and response serialization. See [API Overview](../api/overview.md).

### Authentication and Agent Registry

- **API Key Service** --- Creates, validates, and revokes API keys. Keys carry buyer identity (seat, agency, advertiser).
- **Agent Registry** --- Tracks buyer agents with trust levels (unknown, registered, approved, preferred, blocked). Integrates with AAMP (IAB Agent & API Management Protocol) for cross-registry verification. See [Authentication](../api/authentication.md).

### Business Logic Engines

- **PricingRulesEngine** --- Calculates tiered pricing with buyer-context-aware discounts (tier, volume, deal type). Deterministic, no LLM calls.
- **NegotiationEngine** --- Manages multi-round price negotiation with strategy-based responses. Strategies are mapped from buyer access tier. See [Negotiation](../integration/negotiation.md).
- **YieldOptimizer** --- Provides floor price guidance and concession calculations to the negotiation engine.
- **MediaKitService** --- Manages the three-layer package catalog (ad-server sync, curated packages, dynamic assembly).

### CrewAI Flows

- **ProductSetupFlow** --- Initializes the product catalog from configuration.
- **ProposalHandlingFlow** --- Evaluates buyer proposals using AI agents and routes to acceptance, rejection, or counter-offer.
- **DealGenerationFlow** --- Converts accepted proposals into deals with OpenRTB parameters.
- **DiscoveryInquiryFlow** --- Handles natural-language inventory queries from buyers.

### Infrastructure

- **Event Bus** --- Emits and stores events for all system activity. 21 event types across 7 categories. See [Event Bus](../event-bus/overview.md).
- **Order State Machine** --- Formal state machine with 12 states and 20 transitions. Full audit trail. See [Order Lifecycle](../state-machines/order-lifecycle.md).
- **Change Request Manager** --- Handles post-deal modifications with severity classification and approval routing. See [Change Requests](../api/change-requests.md).
- **Approval Gate** --- Human-in-the-loop approval workflow for proposals and high-value decisions.
- **Storage Backend** --- Pluggable storage with key-prefix convention. SQLite and Redis backends. See [Storage](storage.md).

## Ecosystem

The seller agent is one side of the IAB Tech Lab Agent Ecosystem. See the [Buyer Agent architecture](https://iabtechlab.github.io/buyer-agent/architecture/overview/) for the other side.

```mermaid
graph LR
    subgraph "Buyer Side"
        BA[Buyer Agent]
    end

    subgraph "Seller Side"
        SA[Seller Agent]
    end

    subgraph "Shared Infrastructure"
        AAMP[AAMP Registry]
    end

    BA -->|"1. Discover (GET /.well-known/agent.json)"| SA
    BA -->|"2. Get API Key (POST /auth/api-keys)"| SA
    BA -->|"3. MCP: Structured tool calls (/mcp/mcp Streamable HTTP)"| SA
    BA -->|"4. A2A: Natural language (/a2a/seller/jsonrpc)"| SA
    BA -->|"5. REST: Browse, quote, book, negotiate"| SA

    BA -.->|Register| AAMP
    SA -.->|Verify Agents| AAMP
```
