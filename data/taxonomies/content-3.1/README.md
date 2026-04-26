# IAB Content Taxonomy 3.1

This directory vendors a verbatim copy of the IAB Tech Lab Content Taxonomy version 3.1.

| Field | Value |
|-------|-------|
| Version | 3.1 |
| Format | TSV |
| Source | https://raw.githubusercontent.com/InteractiveAdvertisingBureau/Taxonomies/main/Content%20Taxonomies/Content%20Taxonomy%203.1.tsv |
| Upstream repo | https://github.com/InteractiveAdvertisingBureau/Taxonomies |
| Fetched at | 2026-04-25T19:27:34Z |
| License | CC-BY-3.0 (https://creativecommons.org/licenses/by/3.0/) |
| Attribution | "Content Taxonomy 3.1" by IAB Tech Lab is licensed under CC BY 3.0. |

## Files

- `Content Taxonomy 3.1.tsv` — the upstream TSV, unchanged. ~1,500 hierarchical
  category IDs, cross-mapped to CTV Genre, Podcast Genre, and Ad Product taxonomies.

## Versioning notes

Content Taxonomy 3.x is **not backwards compatible** with 2.x — deletions exist.
IAB ships an "IAB Mapper" tool for migration. Briefs referencing 2.x IDs that
were deleted in 3.x must fail loudly or run through Mapper (see proposal §7).

The exact `sha256` of the vendored file is recorded in `../taxonomies.lock.json`.

## How to refresh

See ../audience-1.1/README.md.
