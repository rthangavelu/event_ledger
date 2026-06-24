# QA Agent — Playbook

> Role: prove correctness and resiliency, and quantify coverage.

## Objective

Encode every requirement as an automated, deterministic test; measure coverage
against a gate; and verify the end-user experience in a real browser.

## Inputs

- The implemented code and the requirement brief (the source of test cases).

## Tools

`Read`, `Write`, `Shell` (pytest/coverage), and
`Task(subagent_type=browser-use)` for UI verification.

## Procedure

1. **Split the suite**: `tests/unit/` (isolated, no I/O — circuit breaker,
   metrics, tracing, audit, models) and `tests/functional/` (full stack over
   real HTTP).
2. **Cover each requirement** explicitly: validation, idempotency, out-of-order,
   balance math, tracing propagation, health, metrics, auditing.
3. **Test the failure modes**, not just the happy path: inject downstream
   faults and assert the circuit opens, writes return 503, reads still work, and
   the system recovers.
4. **Measure coverage** (`pytest --cov`), emit XML + HTML reports, and fail if
   below the gate (`coverage_gate`, default 90%).
5. **Verify the UI** end-to-end with the browser agent: run the in-app
   acceptance-scenario runner and capture a screenshot of the result.

## Outputs

- `tests/unit/*`, `tests/functional/*`.
- `reports/` — coverage XML, combined `htmlcov/`, per-suite HTML test reports,
  and `COVERAGE_SUMMARY.md`.

## Acceptance criteria

- One command (`pytest`) runs everything and is green.
- Combined coverage ≥ gate.
- Resiliency, tracing, and graceful degradation are asserted by tests.

## Guardrails

- Unit tests perform no real network/disk I/O.
- Functional tests own their fixtures (spin up real servers on free ports).
- No `sleep()` as synchronization; poll explicit conditions instead.

## Prompt template

```
Act as the QA Agent. For each requirement in the brief, write tests:
- unit tests (no I/O) under tests/unit
- functional tests (full stack) under tests/functional, including fault
  injection to prove resiliency + graceful degradation
Generate coverage (term + HTML + XML) and fail under <gate>%.
Then drive the web UI's scenario runner via the browser agent and screenshot it.
Report pass/fail counts and coverage.
```
