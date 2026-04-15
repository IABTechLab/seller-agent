# Claude Setup Guide (Desktop & Web)

Connect your seller agent to Claude Desktop or Claude on the web for conversational management of your media kit, pricing, deals, and buyer relationships.

## Prerequisites

Your developer should have already:
- Deployed the seller agent server
- Connected your ad server (GAM or FreeWheel)
- Connected SSPs (PubMatic, Index Exchange, etc.)
- Generated an operator API key for you

If not, see the [Developer Setup Guide](developer-setup.md) first.

## Step 1: Add the Seller Agent to Claude Desktop

There are two ways to connect, depending on whether the seller agent is running locally or on a remote server.

### Option A: Remote Server (Recommended for Production)

Works on both **Claude Desktop** and **Claude on the web** (claude.ai):

1. Open Claude Desktop or go to [claude.ai](https://claude.ai)
2. Go to **Settings > Integrations**
3. Click **"+ Add Custom Integration"**
4. Enter your seller agent's MCP URL: `https://your-publisher.example.com/mcp`
5. If prompted for authentication, enter your operator API key
6. Click **Save**

> Available on Pro, Max, Team, and Enterprise plans. Free users get one custom integration. This is the same setup for both Claude Desktop and Claude web — the integration syncs across both.

### Option B: Local Development Server

For seller agents running on `localhost`:

1. Open Claude Desktop
2. Go to **Settings > Developer > Edit Config**
3. This opens `claude_desktop_config.json`. Add:

```json
{
  "mcpServers": {
    "seller-agent": {
      "command": "uvicorn",
      "args": ["ad_seller.interfaces.api.main:app", "--port", "8000"],
      "env": {
        "ANTHROPIC_API_KEY": "your-key"
      }
    }
  }
}
```

4. Save and restart Claude Desktop

> **Note**: The JSON config method is for **local stdio servers only**. Remote servers must use the Settings > Integrations UI.

## Step 2: First-Run Setup Wizard

Type `/setup` to begin the guided configuration wizard.

> **Claude**: "Welcome! Your seller agent is deployed and connected to your ad server. Let's set up the business configuration. I'll walk you through 8 steps — you can skip any step to accept defaults."

### Step 1: Publisher Identity

> **You**: "We're ESPN Digital"
>
> **Claude**: "Got it. I've set your publisher name to 'ESPN Digital'. What's your primary domain?"
>
> **You**: "espn.com"

### Step 2: Agent Behavior

> **Claude**: "Your seller agent has a 3-level AI hierarchy. The top-level Inventory Manager optimizes for your business goals. What should it prioritize?"
>
> **You**: "Maximize yield but maintain a $15 floor on CTV"

### Step 3: Media Kit

> **Claude**: "I found 24 inventory packages synced from your ad server. Here are the top ones: [list]. Want to customize these or create new packages?"
>
> **You**: "Feature the CTV Sports package and the Premium Video bundle"

### Step 4: Pricing Rules

> **Claude**: "Your current rate card: CTV $35, Video $25, Display $12. Want to adjust?"
>
> **You**: "Raise CTV to $40 and set a 15% discount for advertiser-tier buyers"

### Step 5: Approval Gates

> **Claude**: "Want to approve deals before they go live, or auto-approve?"
>
> **You**: "Auto-approve deals under $50 CPM from preferred buyers. Require approval for everything else."

### Step 6: Buyer Agents

> **Claude**: "Who should be allowed to buy from you?"
>
> **You**: "Register GroupM's buyer agent and set them as preferred"

### Step 7: Curators

> **Claude**: "Agent Range is set up to curate deals. Want to add any other curation partners?"
>
> **You**: "No, just Agent Range for now"

### Step 8: Review & Launch

> **Claude**: "Here's your complete setup: [summary]. Everything looks good. Your seller agent is live!"

## Available Slash Commands

Once the seller agent is connected, Claude Desktop recognizes the following slash commands:

| Command | What it does |
|---------|-------------|
| `/setup` | Run the guided setup wizard (first-time or reconfigure) |
| `/status` | Check configuration and system health |
| `/inventory` | See your products and media kit packages |
| `/deals` | Full report on all deal activity |
| `/queue` | Inbound items waiting for your action |
| `/new-deal` | Create a new deal step by step |
| `/configure` | Manage event bus flows, approval gates, guard conditions |
| `/buyers` | See which buyer agents are accessing your inventory |
| `/help` | List all available capabilities |

---

## Day-to-Day Operations

After setup, use Claude Desktop to manage your seller agent:

### Deals
- "Create a PMP deal for GroupM at $28 CPM for CTV"
- "How is deal DEMO-ABC123 performing?"
- "Push that deal to PubMatic"
- "Deprecate deal DEMO-OLD because fill rate is too low"
- "Show me the lineage for deal DEMO-XYZ"

### Media Kit
- "Show me my media kit"
- "Create a new Premium Sports package"
- "What do buyers see when they browse my inventory?"

### Pricing
- "What's my current rate card?"
- "Update CTV floor to $45 CPM"
- "What price does an agency-tier buyer get for video?"

### Approvals
- "Show pending approvals"
- "Approve the GroupM proposal"
- "Reject the deal from unregistered-agent-123"

### Buyer Management
- "Who's connected to my seller agent?"
- "Set Havas as an approved buyer"
- "Block unknown-agent-456"

### Troubleshooting
- "Troubleshoot deal XYZ on PubMatic"
- "Why is my CTV fill rate low?"
- "Show me my SSP routing rules"

## Also Works With

The same MCP endpoint works with other AI platforms:

- **[ChatGPT](chatgpt-setup.md)** — via Developer Mode Apps & Connectors
- **[OpenAI Codex](chatgpt-setup.md#openai-codex)** — via `config.toml`
- **[Cursor](chatgpt-setup.md#cursor)** — via `.cursor/mcp.json`
- **[Windsurf](chatgpt-setup.md#windsurf)** — via MCP Marketplace
