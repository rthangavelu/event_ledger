# AI-Assisted SDLC Workflow

This project was built using an **agent-driven, AI-augmented engineering
workflow**. Rather than hand-writing every artifact, the work was decomposed
into specialized agent roles that mirror an SDLC, each with a clear objective,
inputs, and verifiable outputs. This document records how AI assistance was
applied across the lifecycle and where to find each deliverable.

## Roles and deliverables

### Design Agent

**Objective:** turn the assignment brief into an explicit, reviewable design.

| Deliverable | Location |
|---|---|
| Design document (goals, NFRs, decisions, trade-offs) | [`DESIGN.md`](DESIGN.md) |
| Architecture & component diagrams | [`DESIGN.md`](DESIGN.md) §2–3 (Mermaid) |
| Sequence diagrams (happy path + degradation) | [`DESIGN.md`](DESIGN.md) §4–5 |
| Circuit-breaker state machine | [`DESIGN.md`](DESIGN.md) §5 |
| Data model / ER diagram | [`DESIGN.md`](DESIGN.md) §7 |
| API contract between services | [`DESIGN.md`](DESIGN.md) §6 |

Diagrams are kept as **Mermaid-in-Markdown** so they are version-controlled,
diff-able, and render automatically on the Git host — no binary image drift.

### Development Agent

**Objective:** implement the design with production-minded cross-cutting concerns.

| Deliverable | Location |
|---|---|
| Two independent services + shared `common` library | `gateway/`, `account_service/`, `common/` |
| Idempotency, out-of-order tolerance, exact-decimal balances | `*/db.py`, `*/main.py` |
| Resiliency: timeout + retry/backoff/jitter + circuit breaker | `gateway/account_client.py`, `gateway/circuit_breaker.py` |
| Structured JSON logging + distributed tracing | `common/logging_config.py`, `common/tracing.py`, `common/middleware.py` |
| **Error handling** (global handler, trace-correlated 500s) | `common/errors.py` |
| **Auditing** (append-only, tamper-evident, trace-linked) | `common/audit.py`, `GET /audit` on both services |
| Metrics (counters + latency histogram, Prometheus) | `common/metrics.py`, `GET /metrics` |
| Containerization | `Dockerfile`s, `docker-compose.yml` |
| **Meaningful Git history** | see below |

### QA Agent

**Objective:** prove correctness and resiliency, and quantify coverage.

| Deliverable | Location |
|---|---|
| Unit tests (isolated, no I/O) — 33 tests | `tests/unit/` |
| Functional / integration tests — 30 tests | `tests/functional/` |
| Resiliency, tracing, degradation, audit tests | `tests/functional/test_resiliency.py`, `test_tracing.py`, `test_audit.py` |
| **Unit test coverage report** | `reports/coverage-unit.xml`, `reports/unit-tests.html` |
| **Functional test coverage report** | `reports/coverage-functional.xml`, `reports/functional-tests.html` |
| Combined coverage (92%) | `reports/coverage.xml`, `reports/htmlcov/index.html`, `reports/COVERAGE_SUMMARY.md` |

Run it all: `make reports PYTHON=.venv/bin/python` (or `make coverage`).

## Git history as a working-process artifact

The commit history is intentionally **incremental and meaningful** — each commit
maps to a coherent step in the workflow rather than one squashed blob:

1. Shared observability package + Account Service
2. Resilient Gateway + full test suite
3. Docker Compose
4. README
5. Audit trail + centralized error handling (Development Agent)
6. Unit/functional split + unit tests + coverage tooling (QA Agent)
7. Generated test/coverage reports (QA Agent)
8. Design document + AI-SDLC documentation (Design Agent)

## How AI assistance was applied (practices)

- **Spec → plan decomposition:** the brief was broken into a task list with
  explicit acceptance criteria per requirement, tracked to completion.
- **Iterative generate-verify loops:** every component was smoke-tested and
  covered by automated tests immediately after generation; failures (e.g. a
  non-JSON-serializable validation error, ASGI contextvar propagation) were
  caught and fixed in-loop.
- **Guardrails encoded as tests:** resiliency and tracing requirements are
  asserted by tests, so regressions surface automatically.
- **Reproducible automation:** a `Makefile` standardizes test, coverage, report,
  and run commands so the workflow is repeatable by a human or another agent.
- **Documentation generated alongside code**, kept in-repo and diff-able.

## Reproduce the full pipeline

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
make test                              # all 63 tests
make reports PYTHON=.venv/bin/python   # coverage + HTML reports into reports/
make docker-up                         # run both services
```
