# 0016 — The approval registry is tenant-scoped

**Status:** Accepted
**Date:** 2026-10-28

## Context

The token carried a tenant claim end to end — principal, ledger records, identity
roster — but the approval registry was process-global and keyed by plan id alone.
In a shared deployment, a caller from tenant B (holding the *right role* in their
own tenant) could read and act on tenant A's governance journey. This was N-1,
the last High-severity item in the post-completion analysis.

## Decision

**A governance journey belongs to the tenant that created it, and the registry
refuses everyone else.**

* `ApprovalRegistry.get_or_create(plan_id, tenant=…)` — a journey is created
  under the caller's tenant; a later caller from a different tenant is refused
  with `TENANT_MISMATCH` (HTTP 403) before any state is read or moved. A second
  tenant can never obtain a *parallel* journey for the same plan.
* The API layer always declares the caller's tenant explicitly: the verified
  token's claim when auth is on, the demo principal's when it is off.
* An **undeclared** tenant (scripts, tests, safety nets) resolves lazily to the
  identity roster's default tenant — never at import time, so a config reload
  takes effect.
* With authentication off, every caller resolves to the same demo tenant, so the
  offline demo, the browser suites and the container smoke test are byte-identical
  to before tenancy existed.

## Alternatives considered

**Key the registry by `(tenant, plan_id)` and hand each tenant its own journey.**
Rejected: two journeys for one plan is a forked governance history — exactly the
ambiguity the single-edge state machine exists to prevent.

**Enforce tenancy only in the API layer.** Rejected: the whole project's lesson
(ADR 0004) is that boundaries live in the backend state machine, not in handlers;
the registry is where the authority lives, so it is where the check lives.

**Do nothing until a real multi-tenant deployment exists.** Rejected: the claim
"tenancy is enforced" was already implied by the tenant claim existing on tokens
and ledger records; an implied-but-unenforced claim is worse than either saying
nothing or enforcing it.

## Consequences

**Good.** The cross-tenant attack is refused at the registry and asserted by a
red-team suite that drives the real ASGI app with a same-role, different-tenant
principal (`tests/redteam/test_tenant_isolation.py`); the owning tenant's full
gate path is unchanged; `governance` payloads now carry `tenant`, so the boundary
is observable.

**Bad, and admitted.** The registry is still process-local — tenancy scoping
does not unlock horizontal scaling (that is N-2, a shared store). An undeclared
tenant silently resolves to the demo default, which is the right behaviour for
scripts and the wrong one if the API ever forgets to declare — the API's three
state-changing endpoints are the enforcement points and are covered by the
red-team suite. Journey ownership does not survive a reset, by the same design
as the registry itself (authority is lost on restart, deliberately).

## Enforcement

- `tests/redteam/test_tenant_isolation.py` — 5 tests: same-role/different-tenant
  refusal on approve, governance-read and execute; the owning tenant completes
  the whole gate; the registry's parallel-journey refusal; reset semantics.
- `scripts/health.py` and every existing suite — the demo path with auth off
  must be byte-identical, which the full gate asserts on every run.

See also [ADR 0011](0011-authentication-is-opt-in.md) (the tenant claim's origin)
and the post-completion analysis, N-1.
