# IAB Audience Taxonomy 1.1

This directory vendors a verbatim copy of the IAB Tech Lab Audience Taxonomy version 1.1.

| Field | Value |
|-------|-------|
| Version | 1.1 |
| Format | TSV |
| Source | https://raw.githubusercontent.com/InteractiveAdvertisingBureau/Taxonomies/main/Audience%20Taxonomies/Audience%20Taxonomy%201.1.tsv |
| Upstream repo | https://github.com/InteractiveAdvertisingBureau/Taxonomies |
| Fetched at | 2026-04-25T19:27:34Z |
| License | CC-BY-3.0 (https://creativecommons.org/licenses/by/3.0/) |
| Attribution | "Audience Taxonomy 1.1" by IAB Tech Lab is licensed under CC BY 3.0. |

## Files

- `Audience Taxonomy 1.1.tsv` — the upstream TSV, unchanged. Tier-1 splits into Demographic, Interest-based, and Purchase-intent.

## Versioning

IAB convention: major version = breaking change, minor version = additive.
The exact `sha256` of the vendored file is recorded in `../taxonomies.lock.json`.

## How to refresh

Vendor refreshes are gated through `scripts/update-taxonomies.py` (proposal §5.4)
which refetches the canonical URL, recomputes the hash, and updates the lock file.
Treat refreshes as manual reviewed updates, not silent upgrades.
