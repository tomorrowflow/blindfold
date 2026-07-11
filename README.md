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

**Stack:** Python / FastAPI, hand-rolled all the way down (ADR-0020) — today's request
path keeps the entity graph in-process (Postgres persistence is targeted, ADR-0008) and
novel-entity adjudication ships as a fail-closed stub pending a real local-LLM client.
A self-hosted key-custody service (OpenBao Transit) backs the mapping's encryption. The
architecture narrative, the request flow in full, and the decision log live in
[`docs/DESIGN.md`](docs/DESIGN.md). Runtime is Python-only — no Node at install or run
time. The one exception is `frontend/`, the management SPA's Vite+React source
(ADR-0026): built once and vendored as a static bundle served at `/ui/` (see
[Management app build](#management-app-build) below). Any other JS/TypeScript tooling
you see (e.g. under [`.sandcastle/`](.sandcastle/)) is dev-harness-only, not part
of the shipped build.

---

## Quickstart

```bash
uv sync
uv run blindfold serve
```

This starts the proxy at `http://127.0.0.1:8000` — **loopback-only by default**
(SEC-11); pass `--host`/`--port` to bind elsewhere, an explicit opt-in. It runs against
the vendored in-process entity-graph seed, so there's nothing else to stand up to try
it end-to-end (Postgres-backed persistence, ADR-0008, is a separate slice — today's
server keeps its state in-process for the request path).

Point your client at it (see [Usability](#usability) below), and you're blindfolding.

**Optional: OpenBao Transit** (production key custody for the mapping, ADR-0008 —
needed today only for the re-identify/decrypt path):

```bash
docker compose -f infra/docker-compose.dev.yml up -d
./infra/bootstrap-openbao.sh
export BLINDFOLD_OPENBAO_ADDR=http://localhost:8200
export BLINDFOLD_OPENBAO_TOKEN=$(bao token create -policy=blindfold-proxy -field=token)
uv run blindfold serve
```

Never hand the running proxy the OpenBao **root** token (the `dev-root-token` the
bootstrap script uses to set up keys/policies) — `blindfold serve` refuses to start
against a root Transit token (SEC-2) unless you explicitly set `BLINDFOLD_DEV_MODE=1`,
since root bypasses the `blindfold-proxy`/`-human`/`-admin` policy separation the store's
RBAC depends on. Mint a scoped token as shown above instead.

---

## Management app build

The management app shell (ADR-0026) is a Vite+React app, source in [`frontend/`](frontend/),
**committed as a built static bundle** at `src/blindfold/ui_dist/` and served by
`blindfold serve` at `/ui/`. Node is a **dev dependency only** — a clean venv running an
installed wheel needs no Node at all, because the bundle is already vendored in the
repo/package like the fonts and icons it embeds.

**Dev loop** (editing the shell itself):

```bash
uv run blindfold serve       # the API, on 127.0.0.1:8000
cd frontend && npm install && npm run dev   # the SPA, proxying /v1/* to the API above
```

**Regenerating the vendored bundle** (after changing anything under `frontend/src/`):

```bash
cd frontend
npm install
npm run build                # writes straight into ../src/blindfold/ui_dist/
```

Commit the resulting `src/blindfold/ui_dist/` changes alongside your `frontend/` source
change — packaging (`uv build` / CI) does not rebuild the frontend itself, it just picks
up whatever is already committed there.

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
- **Warm start.** The app bootstraps its entity graph, relationships, and (when
  OpenBao Transit is configured) the re-identify store from a vendored seed at
  startup, so a fresh install shows a non-empty, protected workspace from request
  #1 instead of an empty one learned one leak at a time. Importing your own
  curated data is future work.

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

> **Status:** design agreed and recorded; a working proxy, RBAC-gated re-identify,
> merge, and the management SPAs ship today. See [ADR-0020](docs/adr/0020-hand-rolled-local-interceptor-drop-litellm.md)
> for the hand-rolled interceptor decision and [ADR-0005](docs/adr/0005-surrogate-generation.md)
> for what the surrogate engine does and doesn't do yet.

---

## License

[Apache License 2.0](LICENSE) — permissive, with an explicit patent grant suited to
security infrastructure used inside a company. See [`NOTICE`](NOTICE) for third-party
attributions.

Blindfold's dependencies impose no copyleft on this code: **OpenBao** (MPL-2.0) is used
as a separate network service (the Transit engine, not bundled), fully compatible with
Apache-2.0.
