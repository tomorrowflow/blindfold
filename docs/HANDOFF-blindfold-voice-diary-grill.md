# Handoff: grill session — combining Blindfold with voice-diary

**Date:** 2026-07-07
**Repo:** `/Users/florianwolf/Documents/GitHub/blindfold` (work from repo root, not `.sandcastle/`)
**Sibling repo:** `/Users/florianwolf/Documents/GitHub/voice-diary`
**Next session's job:** run `/grill-with-docs` to stress-test the integration plan below against CONTEXT.md and the ADRs, resolve each decision branch, and update docs/issues as decisions crystallise.

## What happened in the previous session

A full review of the Blindfold repo, an exploration of the voice-diary repo, and web research on alternative implementations (anonymization proxies + voice-journaling apps). Conclusions, in brief:

- **Blindfold's implementation approach is validated.** Code matches docs (closed-world `ExchangeSession`, `StreamingRestorer` tail buffer, leak/resolution gates, scrubbed reasons in `src/blindfold/engine.py`; seams in `app.py`). All v1 issues #39–#50 closed; L3 wired local-only fail-closed (ADR-0022); 276 tests. ADR-0020 (drop LiteLLM) is externally vindicated — LiteLLM's own tracker shows PII unmasking silently failing on Anthropic-native paths and tool calls (BerriAI/litellm issues #22821, #6247).
- **⚠️ L3 has a known blocking defect (issue #68), found by a live verify against real Ollama on 2026-07-07** (the stubbed suite can't catch it): `select_candidate_spans` doesn't filter known *surrogates*, so L3 re-adjudicates the surrogates L2 just injected → double-mint → unresolved restore → **any seeded entity 503s** once a real L3 is wired. Privacy invariant held throughout (all failures fail-closed; egress capture proved surrogates-only). Also on #58: `OllamaAdjudicator` uses httpx's 5s default timeout, cold model load ~6.4s → spurious fail-closed on first request. **The diary integration is blocked on #68** — real German prose traffic hits seeded entities constantly.
- **The differentiator claim holds.** No alternative ships cross-session surrogate consistency with a crypto-governed, audited mapping. Kong AI Sanitizer keeps its map per-request (`ngx.ctx`), LLM Guard's Vault is an unencrypted in-memory list, Presidio punts mapping governance to the integrator, Skyflow is commercial/closed.
- **The Blindfold × voice-diary combination is genuinely novel.** No shipped product anonymizes personal journal/voice content with consistent surrogates before a cloud LLM. Closest: Rosebud's undisclosed "anonymized before processing" claim; Day One's admitted E2EE gap during AI processing. Academic prior art: PAPILLON, Hide-and-Seek (see references).
- **voice-diary is far more mature than its top-level docs claim** (README/CLAUDE.md say "implementation about to begin"; reality is ~99 Swift files, milestones M1–M10 substantially built — its `docs/REVIEW-2026-07-04.md` flags this as DOC-1). It is fully local today: Parakeet/Whisper STT, Ollama analysis, Piper/Voxtral TTS — **zero external LLM/cloud-AI egress**. Its top security finding: the "Tailscale-only" boundary is enforced by nothing (`docker-compose.yml` binds `0.0.0.0:8000`; ~77 legacy `main.py` routes unauthenticated — its SEC-1/SEC-2).

## The proposal to grill

**Anchor ADR:** `docs/adr/0012-voice-diary-concept-reuse.md` — concepts + data seed, never code coupling; the JSON API (ADR-0011) is the convergence point. The proposal stays inside this frame.

1. **voice-diary becomes Blindfold's first real client.** Route exactly one diary step — the once-per-session `document_processor` narrative analysis (`server/webapp/` ingest pipeline, currently Ollama) — through Blindfold's OpenAI-compatible endpoint to a frontier cloud model. Two-line base-URL change on the diary side; everything else stays local. Gives Blindfold sustained real German prose traffic containing exactly the entities it was seeded with.
2. **Adopt concepts from alternatives research** (details + sources in References):
   - Inflection/paraphrase-robust restore (German genitive: provider echoes "Müllers" for surrogate "Müller" → unresolved). Options: closed-world fuzzy matching (Textwash-style) vs. a small local seek-model (Hide-and-Seek paper). Addresses DESIGN.md Top Risk #1.
   - INTACT-style simulated inference attack as the implementation of issue #61 (L3 defense-in-depth): after blindfolding, ask local Ollama to guess originals from context; success = leaking signal.
   - OpenAI Privacy Filter (Apache-2.0, 1.5B/50M-active, CPU-viable, 98% recall, `secret` category) as an additional local detector behind the existing seam. Relevant to #58 (latency) and #59 (code-token traffic).
   - PUPA benchmark (from PAPILLON) as a real-world corpus for the leak audit.
   - GLiNER-style runtime-defined entity types (longer-term; relates to the Term concept).
3. **Export Blindfold's boundary discipline to voice-diary** — port the *concept* of #44 (loopback bind + refuse-at-startup guard) to the diary's unenforced Tailscale-only assumption. Cross-repo; likely a voice-diary issue, not a Blindfold one.
4. **Import voice-diary's latency patterns into L3 (#58)** — deterministic slot selection + LLM wording + template fallback; prefetch-to-mask-latency (`prefetchAllOpeners` pattern in `ios/Sources/Dialog/`).

## Decision branches for the grill (each needs resolving)

1. **Threat-model change for the diary.** The diary currently has *zero* cloud egress; routing `document_processor` through Blindfold introduces the first one. Is blindfolded-egress acceptable for intimate journal content (reflections on direct reports, customer health, family)? Pseudonymization ≠ anonymization (DESIGN.md is explicit); journal *content* (events, feelings) egresses even with perfect entity blindfolding. Is entity-level protection the right bar here, or does the diary need a content-level opt-in per session/segment?
2. **Which endpoint contract?** Diary server speaks OpenAI-style to Ollama. Blindfold's OpenAI endpoint config/auth contract is parked as v2 issue #37 — does this integration pull #37 forward into scope? Also: #45 rejected `stream:true` on OpenAI; does the diary need streaming?
3. **Does diary traffic un-park the coherent surrogate world (ADR-0005, deferred; prototype deleted in #39)?** Longitudinal narratives are where incoherent fakes (person's email domain ≠ employer's fake domain) visibly confuse a model tracking recurring people. Or does v1's flat-pool minting suffice for a first slice?
4. **Inflection-robust restore vs. closed-world.** Fuzzy restore risks exactly Top Risk #2 (sub-token over-restoration, restoring a coincidental lookalike). Where is the line — restrict to morphological suffixes on injected surrogates only? Is a seek-model overkill? Does "closed-world restore" in CONTEXT.md need a sharper definition to permit this?
5. **Where does an additional ML detector slot in the L1/L2/L3 vocabulary?** OpenAI Privacy Filter is neither deterministic (L1/L2) nor candidate-span adjudication (L3). New layer term for CONTEXT.md, or an L3 implementation detail behind the seam? Guard against terminology drift.
6. **Scope of the diary work.** Which parts are Blindfold issues, which are voice-diary issues, and is any coupling risk creeping back in (ADR-0012 says concepts/data only)? Note the diary repo has its own agent workflow and review backlog (`docs/REVIEW-2026-07-04.md` there).
7. **Candidate CONTEXT.md terms still unadded** (carried from the 2026-07-04 grill): Identity/Principal, Role, Retired surrogate, Novel entity — pull in any that this slice touches.
8. **Sequencing vs. #68.** The diary integration depends on a working L3 path for seeded entities. Does #68 (plus the #58 cold-timeout) become a hard prerequisite slice before any diary traffic, and does its fix (`is_known_surrogate` guard in the L3 candidate loop, mirroring the L1 guard at `engine.py:218`) warrant a CONTEXT.md/ADR touch (candidate spans must exclude already-injected surrogates as an invariant, not an implementation detail)?

## Key references (don't re-derive)

**Blindfold repo:** `CONTEXT.md` (ubiquitous language — use its terms exactly), `docs/DESIGN.md`, `docs/adr/0012` (voice-diary frame), `0005` (coherent world deferred), `0020` (hand-rolled interceptor), `0022` (L3 local-only), `docs/findings-2026-07-04.md` (finding IDs ARCH-n/SEC-n/UX-n). Open issues: **#68 (L3 re-blindfolds L2 surrogates — blocking)**, #58/#59/#60/#61 (L3 follow-ups; #58 carries the cold-timeout comment), #37 (OpenAI contract, v2), #25 (coherent-world ripple, v2), #62 (closed-client spike), #1 (umbrella design issue). Issue tracker: `gh` CLI on `tomorrowflow/blindfold`; triage labels per `docs/agents/triage-labels.md`; AFK pickup needs the `Sandcastle` label.

**voice-diary repo:** `SPEC.md` (§3 architecture, §10 ingest, §11 prompts), `docs/REVIEW-2026-07-04.md` (ground truth on maturity + SEC-1/SEC-2), `server/webapp/routers/sessions.py` (ingest), `ios/Sources/Dialog/WalkthroughCoordinator.swift` (behavioral core, 4.8k-LOC god module).

**Research sources:** Hide-and-Seek arXiv:2309.03057 · PAPILLON + PUPA arXiv:2410.17127 · INTACT arXiv:2412.12928 · pseudonymization framework arXiv:2502.15233 · OpenAI Privacy Filter (openai.com/index/introducing-openai-privacy-filter, HF `openai/privacy-filter`) · GLiNER-PII (HF knowledgator) · LLM Guard Anonymize/Deanonymize docs · Kong AI Sanitizer docs · Skyflow LLM privacy vault · LiteLLM issues #22821/#6247 · Day One AI-features privacy disclosure.

## Suggested skills

- **`/grill-with-docs`** — the main event: grill the decision branches above against CONTEXT.md/ADRs, updating docs inline as decisions land.
- **`/to-issues`** — after the grill, slice resolved decisions into tracker issues (remember `Sandcastle` + `ready-for-agent` labels for AFK pickup).
- **`/triage`** — if new issues need routing through the label state machine.

## Constraints and preferences to honor

- Project CLAUDE.md: do **not** use the AskUserQuestion tool.
- User prefers mapping complete functionality before carving MVP slices, and versions over milestones.
- Use CONTEXT.md vocabulary exactly (blindfold/restore/re-identify, Term, surrogate, egress, …) — inventing synonyms is a bug.
- ADR-0012's no-coupling rule is settled; don't re-litigate it, extend it.
