# Live-verify prompts

Manual prompts for the one acceptance criterion the automated suite cannot cover: a real client →
proxy → real L3 provider → restore round trip. Everything in `tests/` proper uses a recording-stub
adjudicator and a stub upstream, so no real provider is ever exercised there.

These are **not** collected by pytest. They are pasted into a live client by a human.

| File | Shape | Exercises |
|---|---|---|
| `74-prompt.md` + `74-engagement-brief.md` | Full agentic session over novel entities | L3 mint, non-ASCII spans, restore of a verbatim contact table, coding-agent token flood |
| `57-smoke-prompt.md` | Single modest message, seeded + novel mix | L1/L2 vs L3 attribution, review-inbox learning loop |

All entity values are synthetic. `.example` domains and `555` phone ranges are reserved for
documentation, and the IBAN is a test value.

## Running

1. Start the proxy with a **local, non-`:cloud`** L3 model configured (an empty model ⇒ L3
   unconfigured ⇒ fail-closed per ADR-0009):

   ```
   uv sync && BLINDFOLD_L3_MODEL=<local-tag> uv run blindfold serve
   ```

   `BLINDFOLD_OLLAMA_MODEL` is a legacy alias for the same setting (`serve.py`); prefer the
   current name.

   **Record which detection configuration the run measures** — the two answer different
   questions and produce very different recall (ADR-0049):

   - `BLINDFOLD_L3_PROVIDER=gliner` + `BLINDFOLD_L3_INNER_PROVIDER=omlx` — the GLiNER cascade.
   - neither set — the bare L3 LLM.

2. **Run the preflight before pasting anything.** It refuses to proceed if the running
   proxy is not the repo's current `HEAD` — a stale binary's findings are unattributable
   to `main` (issue #291):

   ```
   uv run python tests/live-verify/preflight.py
   ```

   A mismatch (or a build too old to report an identity at all) names the expected SHA,
   the actual SHA, and the binary's path, then exits non-zero — **rebuild the proxy and
   restart it** before running live-verify. A match prints the matching SHA and exits 0.
   `--base-url`/`--repo-root` override the defaults (`http://localhost:25463`, this
   checkout) if you're pointing at a different proxy or comparing against a different
   worktree.

3. Point a client at the **proxy's** port — `ANTHROPIC_BASE_URL=http://localhost:25463`
   (`DEFAULT_PORT` in `config.py`; overridable with `BLINDFOLD_PORT`),
   `ANTHROPIC_AUTH_TOKEN=<real key>`. Do not use `:8000` — that is the local L3 provider's own
   port, so agent traffic would go straight to the adjudicator. Without a key, set
   `BLINDFOLD_UPSTREAM_BASE_URL` to a small local echo server that logs the received body and
   returns an Anthropic-shaped message; that proves blindfold + mint + restore but not the real
   provider.

4. Copy `74-engagement-brief.md` somewhere the client can read it, then paste `74-prompt.md` with
   `<BRIEF-PATH>` / `<OUT-PATH>` substituted.

## What to check afterwards

- `GET /v1/status` — no `leak_detected` / `blocked-leak` events in the blocks window.
- The captured upstream payload contains **only** surrogates — no real name, email, phone, codename
  or IBAN from the brief.
- The written one-pager shows the **real** values (restore closed the loop for the client).
- `GET /v1/management/review-inbox` (or `/ui/review-inbox`) — the novel candidates appear as
  provisional items, i.e. the ADR-0010 learning loop fired.
- Startup refuses a `:cloud` model tag, with no override (the local-only invariant).
- If you ran a Diagnostic session (ADR-0047) alongside this run, its Exchange captures'
  header records carry the same `build_sha`/`build_source` the preflight checked, so an
  archived capture stays attributable to this run's build after the fact.

See `.claude/skills/leak-audit` for the property being asserted, and
`docs/adr/0022-wire-l3-adjudicator-local-ollama.md` for the contract.

## Known non-bugs

Expect these; they are tracked separately and are not failures of the run:

- **Latency** — L3 calls a local model per novel candidate span; a cold model load is slow (#58).
- **Multi-word fragmentation** — a novel two-token name can mint two surrogates rather than one.
  A quality bug, not a privacy bug: both tokens are blindfolded (#60).
- **Token flood** — a full agentic session pushes tool/code tokens (`Bash`, `Read`, …) through L3 and
  can disturb tool-calls until they reach the allowlist (#59). `74-prompt.md` deliberately provokes
  this; use `57-smoke-prompt.md` when you want the flood out of the picture.

## History

The `74-*` pair is the prompt that was actually run on 2026-07-24/25 against issue #74. It surfaced
#206 (L1-PII self-collision, since fixed) and #207 (non-ASCII span drift → mis-slice and reanchor
503). `57-smoke-prompt.md` is reconstructed from the #57 handoff's fixture spec — that session
recorded which entities to mix, not a verbatim prompt.
