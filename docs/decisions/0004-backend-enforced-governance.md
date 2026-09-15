# 0004 — Approval is enforced by the backend state machine, and nowhere else

**Status:** Accepted
**Date:** 2026-09-15

## Context

The product's central claim is that a human must authorise consequential action.
Any enforcement of that claim lives somewhere, and the choice of *where* is the
whole security model.

The tempting option is the interface. A disabled Execute button is one line, it
looks like enforcement, and it demos well. It is also not enforcement — it is a
suggestion to a client that the user controls. Anyone with curl, a browser console
or a modified bundle is past it.

## Decision

**The approval state machine in `backend/governance/approval.py` is the single
authority for execution. The frontend has no privileged path and holds no state
the backend trusts.**

Mechanically:

1. **One guard.** `can_execute(gov)` returns `True` only when the plan is in
   `APPROVED`. There is no other expression anywhere that authorises execution.
2. **Explicit states.** `RECEIVED → POLICY_CHECKING → COMPLIANCE_CHECKING →
   APPROVAL_REQUIRED → APPROVED | COMPLIANCE_BLOCKED | REJECTED → EXECUTING →
   EXECUTED | FAILED → AUDITED → COMPLETED`, with `COMPLIANCE_BLOCKED` and
   `REJECTED` terminal.
3. **Transitions are data, not code paths.** An `ALLOWED` table is the only way to
   change state, so "can `EXECUTING` be reached from anywhere?" is a question you
   answer by reading a table, not by tracing every caller.
4. **`decide()` is the only way out of `APPROVAL_REQUIRED`.** It takes a decision,
   an actor and a rationale. **It has no `approved` parameter** — approval is not a
   value a caller can pass in.
5. **The API cannot be tricked into it.** `DecisionRequest` uses Pydantic's
   `extra="forbid"`, so an attempt to send `approved: true` is a validation error
   rather than a silently ignored field.
6. **Approval requires an exact role match.** A more senior role does not satisfy a
   narrower requirement — the system refuses more often than a seniority guess
   would, which is the safe direction.
7. **Everything is recorded, including refusals.** Denied approvals, rejected
   signals and blocked executions all write ledger entries, so a refusal is as
   auditable as a success.

The frontend is a client of exactly the same endpoints a curl is. There is no
"internal" route.

## Alternatives considered

**Enforce in the UI, trust the client.** Rejected — it is the failure mode this
decision exists to prevent. A disabled button is a courtesy to the user, not a
security boundary.

**Enforce in both places.** Rejected as *redundant*, not as wrong. Duplicating the
rule means two things can drift, and the drift is invisible. The UI reflects the
state machine's answer (`governance.can_execute`) rather than re-deriving it, so
there is one authority and the UI displays it.

**A permission matrix / role hierarchy.** Rejected as premature. With a handful of
roles and one consequential action, an exact-match check is easier to reason about
and easier to test than a hierarchy, and the failure mode is over-refusal. A
hierarchy becomes right when there are many actions with different requirements —
that is a real future need, not a present one.

**Signed approval tokens carried by the client.** Rejected. It adds cryptographic
machinery to a system that already resolves identity server-side, and it would put
a credential in the frontend — the wrong direction for this threat model.

## Consequences

**Good.** The governance claim is testable as a property rather than argued. The
red-team suite (`tests/redteam/test_governance_bypass.py`, 26 tests) attacks this
boundary directly:

- execute before approval → refused (`test_a01`)
- execute after rejection → refused (`test_a02`)
- insufficient role → refused (`test_a03`); unknown actor → refused (`test_a04`)
- double approval → refused (`test_a05`)
- double execution → replayed, no new SAP calls (`test_a06`, `test_a06b`)
- compliance-blocked → cannot approve or execute (`test_a07`)
- `EXECUTING` unreachable from any unapproved state (`test_a08b`, scanning the
  full transition table)
- forged `approved`/`required_role` in a request → rejected (`test_a09`)
- acting on a non-active or absent plan → refused (`test_a16`, `test_a16b`)

Most importantly, `test_a08c_approved_state_is_only_entered_from_the_decide_path`
**scans the backend source** and fails if any module other than `orchestrator.py`
transitions a plan into `APPROVED`. That test guards against future contributors,
not just today's code — it is the difference between a property that holds and a
property that is enforced.

**Bad, and admitted.** The largest real gap in this build lives inside this
decision's scope: **there is no authentication.** `resolve_actor()` maps a
supplied `actor_id` to a role, and anyone who can reach the API can claim any
identity. The mitigation today is that the API binds to loopback. This is finding
S1 in the [threat model](../security-threat-model.md), stated rather than left to
be discovered.

**Also bad.** `resolve_actor()` is a single function, which is a genuine strength
for the production fix (an OIDC swap in one place) and a single point of failure
if it ever grows logic.

**Also bad.** The approval registry is process-local, so a restart discards
approval state. Approval does not survive a reset
(`test_a17_approval_does_not_survive_a_reset`). Failing in that direction is
deliberate — a restart must never *grant* authority — but it does mean the demo
cannot resume mid-flow across a server restart.

## Enforcement

```bash
python -m pytest tests/redteam -v          # 26 adversarial tests
python -m pytest -m contract               # contract integrity
python scripts/health.py                   # asserts the governance invariant
```

## Revisiting this

Add authentication by replacing `resolve_actor()` with an OIDC-backed lookup. This
decision's *structure* does not change — the check moves from "trust the supplied
id" to "derive the id from a verified token" — and no other file should need to
change. If a change to this decision requires touching the transition table or
`decide()`, that is a signal the boundary is being weakened and it should be
justified explicitly.
