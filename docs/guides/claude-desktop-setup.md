# Claude Desktop Setup Guide

Connect your seller agent to Claude Desktop for conversational management of your media kit, pricing, deals, and buyer relationships.

## Prerequisites

Your developer should have already:
- Deployed the seller agent server
- Connected your ad server (GAM or FreeWheel)
- Connected SSPs (PubMatic, Index Exchange, etc.)
- Generated a `claude_desktop_config.json` file for you

If not, see the [Developer Setup Guide](developer-setup.md) first.

## Step 1: Add the Seller Agent to Claude Desktop

Your developer will give you a `claude_desktop_config.json` file. It looks like this:

```json
{
  "mcpServers": {
    "seller-agent": {
      "url": "https://your-publisher.example.com/mcp/sse",
      "headers": {
        "Authorization": "Bearer sk-operator-XXXXX"
      }
    }
  }
}
```

Copy this file to your Claude Desktop config directory:

- **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

Then restart Claude Desktop.

## Step 2: First-Run Setup Wizard

When you first connect, the seller agent detects that business setup isn't complete and offers to walk you through it:

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

## ChatGPT / OpenAI

The same MCP endpoint works with ChatGPT. See [ChatGPT Setup Guide](chatgpt-setup.md).
