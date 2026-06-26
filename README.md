# Blindfold

**A self-hosted proxy that pseudonymizes your prompts to hosted LLMs — and restores the real values in the response.**

Blindfold sits in the request path of the LLM tools you control (Claude Code, OpenAI-SDK
clients, IDE extensions, scripts). It **blindfolds** every outbound message — replacing
real **entities** (people, organizations, contact PII, IP terms/codenames) with stable,
plausible **surrogates** — and **restores** the real values when the response comes back.
You work with clear names; the provider only ever sees coherent fakes; the model's
reasoning stays intact.

---

## The value

You send prompts to hosted LLM providers that routinely contain real colleagues' and
clients' names, company names, emails/phones/IBANs, and confidential codenames. Under
**GDPR** and **IP-protection** obligations you can't hand that data to a third-party
provider — but you still want to work naturally with clear names and keep the model's
reasoning intact.

Blindfold gives you all three at once:

- **Privacy by default.** No real entity leaves your machine. Every hop of every request
  is rewritten before egress, and a post-restore **verify pass** confirms nothing leaked.
- **No loss of fidelity.** Surrogates are plausible and relationship-consistent, so the
  model reasons exactly as it would on the real data — then you read the answer in real
  names.
- **Built to be shared.** The real↔surrogate **mapping** is encrypted and access-controlled,
  so a whole team can get consistent anonymization without anyone seeing real values they
  aren't entitled to.

What sets it apart from existing redaction tools is **relational entity-linking**: the same
company maps to a shared fake, aliases resolve to one surrogate, two different "Anna"s stay
apart, and a durable, queryable, team-shareable mapping store backs it all. Most tools
redact tokens in isolation; Blindfold preserves the *world* the tokens describe.

> This is **reversible pseudonymization, not anonymization**. The data leaving your
> machine is only safe while (a) surrogates carry no identifying signal and (b) the
> mapping — the crown-jewel re-identification key — never leaks. Both are core design
> commitments below.

---

## Design approach

```
your app  ─▶  Blindfold proxy  (Anthropic /v1/messages  or  OpenAI /v1/chat/completions)
                  │
                  ├─ blindfold every hop (system prompt, user turns, tool-result messages)
                  │     real → surrogate via the entity graph
                  │
                  ▼
              provider  (sees only a coherent surrogate world)
                  │
                  ├─ restore the response: surrogate → real  (streaming- and tool-call-safe)
                  ├─ verify pass: no real value leaked, no surrogate left unresolved
                  ▼
              your app  (sees fully restored real values)
```

A few principles shape the whole system:

- **Blindfold every hop, not just the first prompt.** System prompts, user turns, and
  tool-result messages (file contents a coding agent reads back) are all rewritten.
  Over-redaction is a *quality* bug; an un-blindfolded real entity is a *privacy* bug —
  the system leans toward the safe failure.

- **A coherent surrogate world, not scattered redactions.** Surrogates are locale-aware
  and plausible; a person's fake email domain matches their employer's fake domain; dates
  are shifted by a stable per-entity offset that preserves intervals. The fakes hold
  together as a believable world, so the model's reasoning stays intact and the output
  doesn't look obviously synthetic.

- **Stable and reversible.** A given entity always maps to the same surrogate, everywhere
  and over time, so past exchanges keep restoring correctly. **Restore is closed-world**
  (only the surrogates actually injected for this exchange) and followed by a **verify
  pass**, so a coincidental lookalike is never over-restored and nothing slips through.

- **Reversibility lives in an encrypted, access-controlled store.** The real-value side of
  the mapping is never stored in plaintext. Encryption keys live in a dedicated
  key-custody service — never in the app — every re-identification (decrypt) is audited,
  and access is role-controlled. That's what makes the mapping both secure *and*
  shareable across a company.

- **Fail-closed.** When the full detection pipeline can't run, Blindfold blocks by default;
  known entities are still protected deterministically. An explicit, logged, per-workspace
  opt-in can degrade to deterministic-only so you keep working during an outage.

- **Learns as you use it.** Novel entities are auto-blindfolded immediately (non-blocking,
  so agents never stall) and surfaced for review. Confirming one grows the dictionary so
  it's caught deterministically next time; rejecting one teaches the system to leave it
  alone. Detection gets more deterministic, and less dependent on heavier inference, over
  time.

**Stack:** Python / FastAPI, with Postgres for the entity graph, a local LLM for novel-entity
adjudication, and a self-hosted key-custody service for encryption. The architecture
narrative, the request flow in full, and the decision log live in
[`docs/DESIGN.md`](docs/DESIGN.md).

---

## Usability

**Point your tool at the proxy — a ~2-line change, no app rewrite:**

```bash
# Claude Code
export ANTHROPIC_BASE_URL=http://localhost:8000
export ANTHROPIC_AUTH_TOKEN=…

# Any OpenAI-SDK client
export OPENAI_BASE_URL=http://localhost:8000/v1
```

From there it's transparent — you keep prompting and reading in real names. The system
works in the background:

- **Review inbox.** Novel candidates land in a queue you triage at your own pace. The
  traffic is never blocked waiting on you; protection happens immediately with a
  provisional surrogate.
- **Management app.** A web UI to review and confirm candidates, merge entities that are
  the same person, edit relationships and the org graph, edit a surrogate, and read the
  audit log of every re-identification.
- **Warm start.** Seed the entity graph from your existing curated data so your known
  people and orgs are protected from request #1, rather than learned one leak at a time.

**Works with:** any tool whose endpoint you can point at a URL — CLIs, IDE extensions,
scripts. **Doesn't work with:** apps whose endpoint can't be redirected (claude.ai web,
ChatGPT desktop/mobile) — those are out of scope by design.

---

## Documentation

| Doc | Contents |
|-----|----------|
| [`docs/PRD.md`](docs/PRD.md) | Product requirements: problem, solution, user stories, scope. |
| [`docs/DESIGN.md`](docs/DESIGN.md) | Architecture narrative, request flow, landscape analysis, decision log, risks. |
| [`CONTEXT.md`](CONTEXT.md) | The project's **ubiquitous language** — glossary, key invariants, non-goals. |
| [`docs/adr/`](docs/adr/) | The canonical [Architecture Decision Records](docs/adr/README.md). |

A few terms you'll see throughout (full glossary in [`CONTEXT.md`](CONTEXT.md)):
**entity** (a real referent to protect) · **surrogate** (its stable fake stand-in) ·
**mapping** (the real↔surrogate record, encrypted) · **entity graph** (the curated
dictionary) · **hop** (one message within a request) · **coherent surrogate world**
(relationship-consistent fakes) · **closed-world restore** · **verify pass**.

> **Status:** design agreed and recorded; implementation in progress. This README
> describes the intended system.

---

## License

[Apache License 2.0](LICENSE) — permissive, with an explicit patent grant suited to
security infrastructure used inside a company. See [`NOTICE`](NOTICE) for third-party
attributions.

Blindfold's dependencies impose no copyleft on this code: **OpenBao** (MPL-2.0) is used
as a separate network service (the Transit engine, not bundled), and **LiteLLM** is MIT.
Both are fully compatible with Apache-2.0.
