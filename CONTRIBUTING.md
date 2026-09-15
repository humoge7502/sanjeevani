# Contributing to SANJEEVANI

Thanks for considering a contribution. This is a single-maintainer
prototype, so the process is lightweight — but the *standards* are not,
because the whole point of this repository is that its claims are enforced
by checks rather than by trust.

## The one rule that matters most

**Honesty boundaries are load-bearing.** This project's README, docs and API
payloads go out of their way to label what is real, deterministic, simulated
or mocked (`docs/mock-boundaries.md`). A change that blurs those labels —
making a mock behave like a real integration, or an unverified claim sound
measured — is rejected even if the code is good. If your change affects
what is real vs. mocked, update that document in the same PR.

## Getting started

```bash
python -m pip install -r requirements-dev.txt   # runtime + dev tooling
npm --prefix frontend install                    # frontend deps
(cd frontend && npx playwright install chromium) # only for browser tests

python scripts/health.py                         # pre-flight check
```

## The gate

Every change must pass the same gate CI runs — one definition, two
environments:

```bash
bash scripts/verify.sh
```

That runs, in order: health check, compile check, **ruff**, **mypy**, the
159-test backend suite, frontend `tsc`, 41 frontend unit tests, the 48-case
Chromium browser suite (needs the Playwright install above), and the
production build. If a step is skipped because a tool is missing, the script
fails rather than silently passing — install your dev tooling.

## How to work

1. **Baseline first.** Run `bash scripts/verify.sh` before you change
   anything, so a failure is provably yours.
2. **Small, coherent commits.** One behaviour per commit; the commit message
   says *why*, not just *what*.
3. **Tests travel with the change.** If you add behaviour, add the test that
   would fail without it. Governance changes belong in `tests/redteam/` —
   the adversarial suite that asserts attacks FAIL.
4. **Config is the API for numbers.** Thresholds go in `config/*.yaml`, never
   hardcoded in modules, so every number the UI shows is traceable (see
   `backend/config.py`).
5. **Measure claims.** If a change touches the hot path, re-run the commands
   in `docs/performance-budget.md` and note the numbers in the PR.
6. **Architecture changes need an ADR.** Copy an existing file in
   `docs/decisions/`, include the downsides section, and link it.

## What good looks like here

- The governance invariant stays intact: execution is reachable only from
  `APPROVED`, and only the approval state machine can make a plan `APPROVED`.
  `tests/redteam/test_architectural_guard.py` fails the build if that ever
  drifts.
- Error paths are user-legible: governance refusals are structured 4xx/409s,
  never bare 500s.
- No new dependency without a reason written down. The frontend runs the
  entire command center on React alone (ADR 0002) — that decision has kept
  the bundle at ~73 KB gzip.

## Reporting issues

Open a GitHub issue with what you did, what you expected, and what happened.
Security matters go to `SECURITY.md`'s private disclosure path, not a public
issue.
