# Architecture Decision Records

These ADRs are the **canonical record** of Blindfold's architectural decisions. They
were migrated from the decision log in [`../DESIGN.md`](../DESIGN.md); that table now
serves as a quick index, and `DESIGN.md` remains the narrative architecture overview.

Skills (`improve-codebase-architecture`, `grill-with-docs`, `tdd`, `diagnose`) read
this directory to respect — or explicitly reopen — prior decisions. If your work
contradicts an ADR, surface it rather than silently overriding it.

Vocabulary follows [`../../CONTEXT.md`](../../CONTEXT.md).

## Index

| ADR | Title | Status |
|-----|-------|--------|
| [0001](0001-litellm-gateway-own-restore-layer.md) | LiteLLM gateway with our own restore layer | Accepted |
| [0002](0002-blindfold-every-hop.md) | Blindfold every hop of every request | Accepted |
| [0003](0003-inline-layered-detection-candidate-span.md) | Inline layered detection (L1/L2/L3) with candidate-span adjudication | Accepted |
| [0004](0004-relational-entity-linking.md) | Relational entity-linking as the differentiator | Accepted |
| [0005](0005-surrogate-generation.md) | Surrogate generation: plausible names + reserved-namespace PII, coherent, date-shifted, stable | Accepted |
| [0006](0006-restore-mechanics.md) | Restore mechanics: closed-world + verify pass + sliding-window + tool-call reassembly | Accepted |
| [0007](0007-store-global-registry-workspace-tags.md) | Store scope: global registry + workspace tags | Accepted |
| [0008](0008-store-security-openbao-transit.md) | Store security: OpenBao Transit + ciphertext columns + blind index | Accepted |
| [0009](0009-fail-closed-failure-policy.md) | Failure policy: fail-closed default + per-workspace degrade opt-in | Accepted |
| [0010](0010-learning-loop-async-review.md) | Learning loop: auto-blindfold provisional + async review inbox | Accepted |
| [0011](0011-management-app-spa-json-api.md) | Management app: SPA over a FastAPI JSON API | Accepted |
| [0012](0012-voice-diary-concept-reuse.md) | voice-diary: concept reuse + data seed, not coupling | Accepted |
| [0013](0013-seed-only-sensitive-referents.md) | Seed only sensitive referents — the term/org_unit sensitivity boundary | Accepted |
| [0014](0014-unify-referents-sensitivity-flag.md) | Unify referents under a single entity with a sensitivity flag | Accepted |
| [0015](0015-reidentify-is-a-decrypt-operation.md) | Re-identification is a decrypt operation, gated by decrypt rights + workspace tags | Accepted |
| [0016](0016-merge-collapses-same-kind-entities.md) | Merge collapses two same-kind canonical entities into one | Accepted |
| [0017](0017-management-graph-renders-in-surrogate-space.md) | Management graph renders in surrogate-space; reveal is the gated re-identify exception | Accepted |
| [0018](0018-entity-list-real-name-search-audit-on-miss.md) | Entity-list real-name search emits audit on every attempt; surrogate-space viewing is decrypt-free | Accepted |
| [0019](0019-proxy-config-auth-contract.md) | Proxy config & auth contract — env-var split (v1, Anthropic path) | Accepted |

New ADRs: copy [`0000-template.md`](0000-template.md), take the next number.
