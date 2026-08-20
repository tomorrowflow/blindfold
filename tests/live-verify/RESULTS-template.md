# #74 live-verify run N — results

Copy this file to `RESULTS-run-N.md` (not committed — a scratch write-up, same as the
captures directory itself) and fill it in after a run. Structured around ADR-0023's two
numeric bars (`docs/adr/0023-l3-suppression-token-granularity.md`, "Verification, with the
acceptance bar made numeric") so each run's verdict is comparable to the last, rather than
resting on prose.

## Run identity

- Date:
- Build SHA (`preflight.py`'s matched HEAD):
- Detection configuration (ADR-0049): GLiNER cascade (`BLINDFOLD_L3_PROVIDER=gliner` +
  `BLINDFOLD_L3_INNER_PROVIDER=omlx`) or bare L3 LLM (neither set)?
- Prompt used: `74-prompt.md` (full agentic session) or `57-smoke-prompt.md` (smoke)?
- `<OUT-PATH>` (confirm it satisfies the README's layout rule: an otherwise-empty
  directory, with neither the kit nor any `captures/` directory as a sibling):

## Bar 1 — #59 inbound code-token precision ≥ 80%

Run `precision_report.py` against this run's review inbox (an exported
`{"items": [...]}` JSON fixture, or `--base-url` against the still-running proxy) and
paste its output below:

```
$ uv run python tests/live-verify/precision_report.py --inbox-file <fixture>.json
(paste output here)
```

- **Precision figure:** ___% (___ genuine of ___ mints)
- **Pass/fail against the 80% bar:**
- Per-mint provenance notes (adjudicator/entity_type patterns; suppression-trace
  entries once issue #350 ships): 

## Bar 2 — zero terminal blocks

A terminal block is one the session could not recover from without a human `reject`
(ADR-0023's own definition) — this is a run-time observation, not something either script
computes from a capture after the fact.

- **Terminal block count:**
- **Pass/fail against the zero bar:**
- For each block (terminal or recovered), note: what triggered it, whether the session
  self-recovered, and if not, what the human `reject` unblocked.

## Clause-A sweep — no real entity egressed

Run `clause_a_sweep.py` against this run's captures directory and paste its output below.
A `SweepSelfCheckFailed` here means the sweep itself is broken — fix the sweep before
trusting the result, not before trusting the run.

```
$ uv run python tests/live-verify/clause_a_sweep.py --captures-dir <OUT-PATH>/captures
(paste output here)
```

- **Clean, or LEAK?**
- If LEAK: which capture(s) and leaf path(s) (per the sweep's own scrubbed-reference
  output — cross-reference the review inbox / brief to identify the value; do not paste
  the real value itself into this file):

## Known non-bugs observed

Check off any of `README.md`'s "Known non-bugs" section that this run reproduced
(latency, multi-word fragmentation, token flood) — these are not verdict-relevant, just
context for reading the numbers above.

- [ ] Latency (#58)
- [ ] Multi-word fragmentation (#60)
- [ ] Token flood (#59)

## Verdict

- Bar 1 (precision ≥ 80%): PASS / FAIL
- Bar 2 (zero terminal blocks): PASS / FAIL
- Clause-A sweep: CLEAN / LEAK
- **Overall:** 

## Notes for the next run

What this run's residual doesn't answer, and what the next run (or a follow-up issue)
should measure instead of re-deriving the same diagnosis.
