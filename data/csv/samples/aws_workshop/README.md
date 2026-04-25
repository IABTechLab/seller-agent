# AWS Workshop — Synthetic Publisher Inventory

Synthetic data for the IAB-AWS AAMP workshop demo. Models a fictional multi-platform publisher with diverse inventory across 5 channels.

## Publisher Properties

| Property | Channel | Content |
|----------|---------|---------|
| Apex Streaming | CTV | Premium series, live sports (basketball, hockey) |
| GNN | Linear + Digital | News programming, podcasts |
| Crestline Entertainment | CTV + Linear | Reality TV, comedy, entertainment |
| SportsPulse | Linear + Digital | Live sports broadcasts, sports video |
| Horizon Discovery | Display | Homepage takeovers, rich media |

## Files

| File | Description |
|------|-------------|
| `inventory.csv` | 15 products across CTV, linear, digital video, display, audio |
| `audiences.csv` | 6 audience segments (sports, cord-cutters, news, entertainment, high-income, auto) |
| `rate_card.json` | Base CPM rates by channel + 4-tier discount structure |
| `media_kits.json` | 4 curated media kit packages |

## Inventory Types & Base CPMs

| Channel | Base CPM | Products |
|---------|----------|----------|
| CTV/Streaming | $45 | Apex series, live basketball, live hockey, Crestline reality |
| Linear TV | $25 | GNN primetime, SportsPulse live, Crestline entertainment |
| Digital Video | $18 | GNN pre-roll, SportsPulse mid-roll, GNN outstream |
| Display | $12 | Horizon takeover, GNN rich media, SportsPulse display |
| Audio | $8 | GNN podcast sponsorship, Apex companion podcasts |

## Pricing Tiers

| Tier | Discount | Example (CTV $45 base) |
|------|----------|----------------------|
| Public | 0% | $45.00 |
| Registered Buyer | 5% | $42.75 |
| Preferred Agency | 12% | $39.60 |
| Strategic Advertiser | 15% | $38.25 |

## Media Kit Packages

| Package | Products | CPM Range |
|---------|----------|-----------|
| Apex Premium Sports Bundle | Basketball + Hockey CTV + SportsPulse linear | $42-55 |
| GNN News Reach Package | GNN linear + digital video + display | $18-28 |
| Entertainment Upfront Package | Apex series + Crestline CTV + linear | $35-48 |
| Cross-Platform Reach | 10 products across all channels | $15-45 |

## Key IDs

- Inventory IDs: `inv-ctv-*`, `inv-lin-*`, `inv-dig-*`, `inv-dsp-*`, `inv-aud-*`
- Package IDs: `PKG-APEX-SPORTS`, `PKG-GNN-NEWS`, `PKG-ENT-UPFRONT`, `PKG-CROSS-PLATFORM`
- Audience IDs: `aud-sports-enthusiasts`, `aud-cord-cutters`, `aud-news-engaged`, `aud-entertainment-seekers`, `aud-high-income-pros`, `aud-auto-intenders`

## Usage

Set `CSV_DATA_DIR=./data/csv/samples/aws_workshop` to load this data set. The AgentCore Dockerfile uses this by default.
