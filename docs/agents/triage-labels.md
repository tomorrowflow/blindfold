# Triage Labels

The skills speak in terms of five canonical triage roles. This file maps those roles to the actual label strings used in this repo's issue tracker.

| Label in mattpocock/skills | Label in our tracker | Meaning                                  |
| -------------------------- | -------------------- | ---------------------------------------- |
| `needs-triage`             | `needs-triage`       | Maintainer needs to evaluate this issue  |
| `needs-info`               | `needs-info`         | Waiting on reporter for more information |
| `ready-for-agent`          | `ready-for-agent`    | Fully specified, ready for an AFK agent  |
| `ready-for-human`          | `ready-for-human`    | Requires human implementation            |
| `wontfix`                  | `wontfix`            | Will not be actioned                     |

When a skill mentions a role (e.g. "apply the AFK-ready triage label"), use the corresponding label string from this table.

Edit the right-hand column to match whatever vocabulary you actually use.

## Sandcastle trust boundary (finding SC-3)

Autonomous pickup by the Sandcastle harness is gated on trust, not just readiness. The trust
boundary is **a trusted maintainer applying the `Sandcastle` label** — the act of labeling
*is* the authorization. This is enforced host-side in `.sandcastle/main.mts`, not left to a
prompt instruction:

- **Trusted maintainers** are listed in `TRUSTED_MAINTAINERS` in `.sandcastle/main.mts`
  (currently `tomorrowflow`). Add a maintainer there to grant them the authority to authorize
  autonomous work.
- **Label applier, not label presence.** The harness checks the issue's label *events* and
  only works an issue whose `Sandcastle` label was applied by a trusted maintainer. Anyone
  with triage rights can add the label, so presence alone never authorizes pickup.
- **Body vs. comments.** The issue body is trusted (the maintainer endorsed it by labeling).
  Comments are trusted **only when authored by a trusted maintainer**; every other comment is
  stripped before it reaches the planner or implementer prompt (a prompt-injection guard) and
  logged to the run log so a human can see what was quarantined.

`ready-for-human` remains the authoritative per-issue HITL signal: even a correctly
`Sandcastle`-labeled issue is excluded from autonomous work when it needs a human decision.
