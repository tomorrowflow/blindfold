# #57 smoke prompt — single modest message

The lightweight counterpart to `74-prompt.md`. One message, no tool use, so nothing but the prompt's
own text reaches L3 — this is what you want when the #59 token flood would muddy the result, or when
you only need to confirm the round trip is alive.

Reconstructed from the #57 handoff (2026-07-07), which recorded the entity mix to use rather than a
verbatim prompt. The mix is the point: seeded entities must be caught deterministically by L1/L2,
and the single novel name must be caught by L3 — so a miss tells you *which* layer is at fault.

- **Seeded** (present in `src/blindfold/store/vendored_seed.json`): persons `Martin Bach`,
  `Andreas Ritter`, `Sophie Maier`; orgs/terms `Enervia`, `Voltwerk`, `Magic Square`.
- **Novel** (absent from the seed): `Priya Nadkarni`.

---

Here are the notes from this morning's handover meeting:

Martin Bach walked us through the Enervia migration status. Andreas Ritter raised that the Voltwerk
integration is still blocked on the Magic Square rollout, and Sophie Maier agreed to own the
follow-up. Priya Nadkarni joined for the second half and offered to review the data model.

Summarise this in three bullet points, naming who owns what.

---

## Expected

- The reply names all five people and all three orgs/terms **in plain form** — restore worked.
- The captured upstream payload contains **none** of them — only surrogates.
- `Priya Nadkarni` shows up in the review inbox as a provisional item; the seeded six do not, because
  L1/L2 already resolved them.
