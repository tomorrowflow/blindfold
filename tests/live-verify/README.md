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

## Measurement apparatus (issue #351)

Run 11's clause-A sweep, review-inbox precision report and the offline projection script all
lived in a session scratchpad and were destroyed with it — every run since run 5 rebuilt this
apparatus by hand, so run-to-run comparability rested on notes rather than committed code. This
directory now commits the apparatus itself, each with its own unit coverage against fixture
captures so it is known-good **before** a run rather than debugged during one:

| File | Purpose |
|---|---|
| `preflight.py` | Refuses to start a run against a proxy build that isn't the repo's current `HEAD` (issue #291). |
| `clause_a_sweep.py` | Leak-audit clause A, offline: walks every string leaf of every outbound capture in a run's captures directory, word-boundary-matching against every seeded brief value and every review-inbox real value. Carries a positive control so a broken matcher fails loudly instead of reporting a clean 0-hit run. |
| `precision_report.py` | Reads a run's review inbox (a `GET /v1/management/review-inbox` JSON fixture, or that endpoint live), classifies each mint genuine vs false positive against the brief's seeded set, and prints the #59 precision figure plus per-mint provenance (issue #348's `adjudicator`/`entity_type`, issue #350's suppression trace when present). |
| `RESULTS-template.md` | The writeup skeleton for a run, keyed to ADR-0023's two numeric bars (precision ≥ 80%, zero terminal blocks) so each run's verdict is comparable to the last. |

None of these three are collected by pytest either (they need a real run's captures/inbox) —
see `tests/test_live_verify_clause_a_sweep.py` and `tests/test_live_verify_precision_report.py`
for their regression coverage, loaded by file path the same way
`test_live_verify_preflight.py` loads `preflight.py`.

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

   **Layout rule for `<OUT-PATH>` (issue #351):** it must be an otherwise-empty directory, and
   neither this kit (`tests/live-verify/`) nor any `captures/` directory may be a sibling of it.
   On run 11 the driving session listed its own output directory, read the pre-run analysis
   sitting right next to it, and that text persisted in `messages[]` for most of the run —
   causing at least one false-positive mint purely from reading the run's own scaffolding.
   Moving files mid-run does not undo this; only a session restart clears the transcript. Put
   `<OUT-PATH>` somewhere the session has no other reason to `ls`/`glob` into — a fresh scratch
   directory outside this repo checkout, not a subdirectory of it.

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

5. Run the clause-A sweep against the Diagnostic session's captures directory, and the
   precision report against an exported review-inbox JSON fixture (or live, with
   `--base-url`):

   ```
   uv run python tests/live-verify/clause_a_sweep.py --captures-dir <captures-dir>
   uv run python tests/live-verify/precision_report.py --inbox-file <inbox-fixture>.json
   ```

   Copy `RESULTS-template.md` to `RESULTS-run-N.md` and fill it in from both scripts'
   output plus the terminal-block count (a run-time observation neither script can compute
   after the fact).

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

Runs 5–11 re-ran the `74-*` pair against successive suppression/allowlist changes. Full detail
lives in `docs/adr/0023-l3-suppression-token-granularity.md` (the design ADR these runs measure
against) and `docs/adr/0051`/`0052` (the leak-gate/reserved-surrogate ADRs two of these runs fed);
this is the short index:

- **Run 5b** (issue #294) proved suppression is token-granularity-only *except* for a multi-word
  allowlist entry: a rejected multi-word phrase was silently undone on the very next hop because
  `select_candidate_spans` only ever checked the allowlist by single-token equality.
- **Run 6** (issue #297) surfaced a tool name minted as a real from a sub-agent/short-context
  request that carried no `tools` array at all.
- **Run 7** (issue #302) died: 14 blocked exchanges, 9 of them on one declared-tool-shaped value
  minted from prose in a `tools`-array-less request, unrecoverable without a human `reject` —
  because that request is exactly the shape the per-request declared-vocabulary set can't see.
- **Run 8** (issue #328, ADR-0052) ended in a terminal deadlock — 12 consecutive fail-closed
  exchanges — and died without producing a deliverable.
- **Run 9** (no dedicated ADR update of its own — its numbers survive only as run 10's own
  baseline, ADR-0023 "Update, issue #342") measured 32% precision (6 of 19), flattered by two
  mid-run deadlocks that cut the source-reading phase short; needed a manual `reject` to continue.
- **Run 10** (issue #342, build `5501ef3`) was the first run with no mid-run intervention, so the
  first clean read on the acceptance gate: 22% precision (6 genuine of 27 mints), dominated by
  ordinary capitalised English words minted from repo file content arriving as `Read` tool results.
  Four of its five blocked exchanges came from this class and self-recovered (503s interleaved
  with 200s) rather than deadlocking as run 9 had.
- **Run 11** (issue #345, 2026-08-19) measured this repo's case-inconsistency-suppression build:
  zero terminal blocks **passed** (three blocks occurred, all self-recovered); #59 precision
  **failed at 43%** (6 genuine of 14 mints) — up from run 10's 22%, still short of the 80% bar.
  Its own captures and review-inbox export were a session scratchpad, not committed code, and were
  destroyed with it — the reason this apparatus (issue #351) now lives in this directory instead.

**The two numeric bars** (ADR-0023 "Verification, with the acceptance bar made numeric") replace
prose ("the review inbox is not flooded") as the run-to-run verdict, since nine runs produced no
checkable verdict against the prose version: **#59 inbound code-token precision ≥ 80%**, and
**zero terminal blocks** (a block the session cannot recover from without a human `reject`).
`RESULTS-template.md` is keyed to exactly these two bars.
