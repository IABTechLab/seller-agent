# IAB Agentic Audiences (DRAFT, 2026-01)

This directory vendors the wire-format-relevant subset of the IAB Tech Lab
Agentic Audiences specification, formerly known as User Context Protocol (UCP).

| Field | Value |
|-------|-------|
| Version | draft-2026-01 |
| Spec status | DRAFT (last upstream update 2026-01-28) |
| Source repo | https://github.com/IABTechLab/agentic-audiences |
| Fetched at | 2026-04-25T19:27:34Z |
| Spec license | CC-BY-4.0 (https://creativecommons.org/licenses/by/4.0/) |
| Reference impl license | Apache-2.0 (see `spec/LICENSE-APACHE`) |
| Attribution | "IAB Agentic Audiences" by IAB Tech Lab; spec is licensed under CC BY 4.0; reference implementations are licensed under Apache 2.0. |

## What is vendored

We vendor only the wire-format-relevant subset:

```
spec/
  UPSTREAM_README.md                       (upstream README — context only)
  LICENSE                                  (upstream license notice)
  LICENSE-APACHE                           (Apache-2.0 for reference impl)
  roadmap.md                               (upstream specs/roadmap.md — currently empty placeholder)
  docs/
    systems-and-models.md                  (background on agentic systems and models)
  v1.0/
    embedding-exchange.md                  (wire format for embedding exchange)
    embedding-taxonomy.md                  (taxonomy of embedding signals)
    schema/
      agent_interface.schema.json          (currently empty upstream)
      embedding_format.schema.json         (JSON Schema for embedding payload)
    examples/
      buyer_agent_request.json             (currently empty upstream)
      embedding_update.json                (reference example)
      seller_agent_response.json           (currently empty upstream)
```

Empty files are reproduced verbatim from upstream — they are placeholders the
spec authors haven't filled in yet. Sizes are pinned in `../taxonomies.lock.json`
so any future fill-in will be detected.

## Why this subset

Per proposal §5.6, Agentic Audiences is the **dynamic carrier** for embedding
references; Standard / Contextual taxonomies are static IDs. The seller side
needs:

1. The wire-format spec (`embedding-exchange.md`, `embedding_format.schema.json`)
   to validate inbound `AudienceRef(type=agentic, ...)` payloads.
2. The taxonomy of signal types (`embedding-taxonomy.md`) to mirror in seller
   `AgenticCapabilities.supported_signal_types`.
3. The reference example (`embedding_update.json`) to keep test fixtures aligned.

We deliberately do NOT vendor the upstream `src/`, `prebid-module/`, or
`community/` directories — those are reference implementations, not wire format.

## Versioning

Spec version pinned at `draft-2026-01`. The exact `sha256` of each vendored file
is recorded in `../taxonomies.lock.json`. Refresh through
`scripts/update-taxonomies.py` (proposal §5.4); treat as a manual reviewed update
because the spec is still DRAFT and field shapes may change.

## How this seller copy relates to the buyer copy

The buyer ships the same vendored subset at
`ad_buyer_system/data/taxonomies/agentic-audiences-draft-2026-01/`. Both sides
emit the relevant `taxonomy_lock_hashes` in capability discovery (proposal §5.7)
so any drift between buyer and seller copies is detected at runtime.
