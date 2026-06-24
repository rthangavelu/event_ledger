# AGENTS.md — Working agreement for AI agents in this repo

This repository is built and maintained with an **agent-driven SDLC**. The full,
explicit agent configuration lives in [`agents/`](agents/) — start with
[`agents/agents.yaml`](agents/agents.yaml) and [`agents/README.md`](agents/README.md).

## Roles (see `agents/` for full playbooks)

- **Orchestrator** — plans, sequences, verifies hand-offs. Owns no code.
- **Design Agent** (read-only) — produces `docs/DESIGN.md` + Mermaid diagrams.
- **Development Agent** — implements the design; the only agent that edits source.
- **QA Agent** — writes unit + functional tests, coverage, and UI verification.
- **Review Agent** (read-only) — Bugbot + security review before release.

## Project conventions

- **Language/stack:** Python 3.10+, FastAPI, SQLite (one DB per service), httpx.
- **Layout:** `gateway/` and `account_service/` are independent services; shared
  cross-cutting code (tracing, logging, metrics, audit, errors) lives in
  `common/` and is copied into each image — never share business logic or state.
- **Money:** represent amounts as decimal **strings**; compute with `Decimal`.
- **Idempotency:** `eventId` is the primary key in both services.
- **Observability:** every request has a `traceparent`; logs are JSON with
  `trace_id`; metrics at `/metrics`; significant actions are audited.

## Definition of done (acceptance gates)

1. `ReadLints` clean on edited files.
2. `pytest` green (unit + functional); combined coverage ≥ 90%
   (`make coverage`).
3. New behavior has tests; failure modes are tested, not just happy paths.
4. Docs/diagrams updated when behavior or contracts change.
5. Incremental, conventional commits — **never** squash the whole change.

## Guardrails

- Never commit secrets; never force-push or rewrite shared history without
  explicit human approval.
- Prefer editing existing files/patterns over creating new ones.
- Stop and ask the human on ambiguous scope or irreversible/destructive actions.
