# Coding Standards — Blindfold

Blindfold is a **privacy-critical, fail-closed** reversible LLM-anonymization proxy.
It **blindfolds** outbound prompts (real **entities** → **surrogates**) and **restores**
real values in the response. These standards are the contract every change is held to.

## Definition of done

- **Done = the leak-audit property holds, not "the suite is green."** An un-blindfolded
  real **entity** reaching the provider is a *privacy bug*, not a test failure.
  Over-redaction is only a *quality* bug.
- Never weaken, skip, or delete a leak-audit assertion to make a test pass. If a clause
  can't be satisfied, that is a **stop-and-report**, not a workaround — a weakened clause
  is a privacy regression.

## Ubiquitous language (mandatory)

`CONTEXT.md` is the project's ubiquitous language. Use these terms — never synonyms — in
test names, interfaces, commits, and comments:

> blindfold · restore · entity · surrogate · mapping · variation · entity graph · hop ·
> candidate span · workspace · closed-world restore · verify pass · fail-closed · Transit ·
> blind index.

Do **not** use "anonymize / mask / redact / de-anonymize / unmask" as the primary verb —
we pseudonymize **reversibly**.

## Stack & style

- **Python 3.12+ / FastAPI**, managed with **uv**. Run tests with `uv run pytest`.
- **Deep modules**: small public interface, substantial implementation behind it.
  Keep each module focused on one responsibility.
- Type-annotate public functions; avoid bare `Any` and unchecked casts on the
  blindfold/restore/mapping path.

## Testing

- Assert **observable behavior at a seam through the public interface** — never internal
  call counts, private methods, or imagined call shapes.
- Stub external services (**upstream provider, Ollama/L3, OpenBao Transit**) at their
  **network boundary only**; assert on what crossed the boundary, not on mock call counts.
- Every request-path change carries the relevant leak-audit clauses (see
  `.claude/skills/leak-audit/SKILL.md`): zero real-entity egress across every **hop**
  (prose, streamed, tool-call JSON), full **restore**, **closed-world** restore, clean
  **verify pass**, **fail-closed** honored. State which clauses are N/A and why.

## Architecture

- Respect every ADR in `docs/adr/` that touches the area you change. If a change needs a
  decision reversed, that's a human/ADR matter — surface it, don't quietly diverge.
- The real-value side of the **mapping** is never stored in plaintext (Transit ciphertext +
  blind index for equality lookups).
