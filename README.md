# Event Ledger

A small distributed system that ingests financial transaction events and keeps
account balances correct in the face of **duplicate** and **out-of-order**
delivery, while degrading gracefully when a downstream service is unavailable.

It is composed of two independently-runnable microservices that communicate over
synchronous REST and each own a private in-memory SQLite database.

```
                          ┌────────────────────────┐
  Client ───────────────▶ │  Event Gateway (8000)  │   public-facing
                          │  - validation          │
                          │  - idempotency         │
                          │  - local event store   │
                          └──────────┬─────────────┘
                                     │ REST + traceparent header
                                     ▼
                          ┌────────────────────────┐
                          │  Account Service (8001) │   internal
                          │  - applies transactions │
                          │  - computes balances    │
                          └────────────────────────┘
```

## Services

### Event Gateway (`gateway/`) — public

Entry point for all clients. Validates input, enforces idempotency, stores an
authoritative copy of every accepted event in its own DB, and forwards the
transaction to the Account Service through a **resilient HTTP client**.

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/events` | Submit a transaction event |
| `GET`  | `/events/{id}` | Retrieve a single event |
| `GET`  | `/events?account={accountId}` | List an account's events, ordered by `eventTimestamp` |
| `GET`  | `/accounts/{accountId}/balance` | Balance (proxied to Account Service) |
| `GET`  | `/accounts/{accountId}` | Account detail (proxied to Account Service) |
| `GET`  | `/audit` | Read-only audit trail (`?account=&limit=`) |
| `GET`  | `/health` | Health + DB + circuit-breaker state |
| `GET`  | `/metrics` | Prometheus metrics |

### Account Service (`account_service/`) — internal

Owns account state. Applies transactions idempotently and computes balances.
Only ever called by the Gateway.

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/accounts/{accountId}/transactions` | Apply a transaction |
| `GET`  | `/accounts/{accountId}/balance` | Current balance |
| `GET`  | `/accounts/{accountId}` | Account detail + recent transactions |
| `GET`  | `/audit` | Read-only audit trail (`?account=&limit=`) |
| `GET`  | `/health` | Health + DB connectivity |
| `GET`  | `/metrics` | Prometheus metrics |

### Shared (`common/`)

Cross-cutting concerns only (no business logic, no shared state): W3C
`traceparent` propagation, JSON logging, a tiny metrics registry, the ASGI
observability middleware, an append-only **audit trail**, and a centralized
**error handler**. It is copied into each service image so the services remain
independently deployable.

## Documentation

- **[`docs/DESIGN.md`](docs/DESIGN.md)** — design document with architecture,
  sequence, state-machine, and ER diagrams (Mermaid).
- **[`docs/AI_SDLC.md`](docs/AI_SDLC.md)** — how this was built with an
  AI-assisted, agent-driven SDLC workflow (Design / Development / QA agents).
- **[`reports/COVERAGE_SUMMARY.md`](reports/COVERAGE_SUMMARY.md)** — test &
  coverage reports (92% combined).

## How it meets the requirements

| Requirement | How |
|---|---|
| **Idempotency** | `eventId` is the primary key in *both* services. A replay returns the stored record with `200` (`duplicate: true`) and never double-counts. The Gateway applies the transaction downstream *before* persisting locally, and the Account Service is itself idempotent, so retries are always safe. |
| **Out-of-order tolerance** | Events are stored as they arrive; listings are `ORDER BY event_timestamp`. Balance = Σ CREDIT − Σ DEBIT, which is order-independent by construction. |
| **Exact balances** | Amounts are stored as canonical decimal *strings* and summed with `Decimal` — no binary float drift (e.g. `0.10 × 3 == 0.30`). |
| **Validation** | Pydantic models reject missing fields, non-positive amounts, unknown types, bad timestamps, and unknown fields → `400` with structured detail. |
| **Service separation** | Two processes, two databases, no shared in-process state. |
| **Distributed tracing** | A trace ID is minted at the Gateway, propagated downstream via the `traceparent` header, and logged by both services (verified across containers). |
| **Structured logging** | Single-line JSON with timestamp, level, service, trace/span IDs. |
| **Health checks** | `GET /health` on both, reporting DB connectivity (and circuit state on the Gateway). |
| **Custom metric** | Request counter, error counter, and latency **histogram**, exposed at `/metrics` (Prometheus format) and summarised in `/health`. |
| **Auditing** | Append-only, tamper-evident audit trail in both services (DB + structured logs), trace-correlated, queryable via `GET /audit`. |
| **Error handling** | Global exception handler returns a structured `500` echoing the `trace_id`; validation → structured `400`. |
| **Resiliency** | Timeout + bounded retry-with-backoff/jitter + circuit breaker (see below). |
| **Graceful degradation** | Account down → `POST /events` and balance queries return `503`; local reads (`GET /events...`) keep working. |

## Resiliency: why all three, layered

The Gateway's `AccountClient` (`gateway/account_client.py`) layers three patterns
on every downstream call, because they solve different failure modes:

1. **Timeout** — bounds each attempt so a slow Account Service can never make a
   Gateway request hang indefinitely.
2. **Retry with exponential backoff + jitter** — absorbs *transient* blips
   (a dropped connection, a brief 5xx) without retrying forever; jitter avoids a
   synchronized retry stampede.
3. **Circuit breaker** — handles *sustained* failure. After
   `CB_FAILURE_THRESHOLD` consecutive failures it trips **OPEN** and the Gateway
   fails fast with a clear `503` instead of making every client wait through
   timeouts+retries. After `CB_RECOVERY_TIMEOUT` it allows a **HALF_OPEN** trial
   call; a success closes it, a failure re-opens it.

Retry alone would keep hammering a service that's genuinely down (and slow every
caller); the circuit breaker is what turns a dependency outage into a fast,
honest error. **4xx responses are treated as healthy** interactions — they are
not retried and do not trip the breaker.

All values are configurable via environment variables (see `docker-compose.yml`).

## Prerequisites

- **Docker + Docker Compose** (recommended path), *or*
- **Python 3.10+** for running locally / running the tests.

## Run with Docker Compose (recommended)

```bash
docker compose up --build
```

- **Web UI / test console: http://localhost:8000/** ← open this in a browser
- Gateway API:     http://localhost:8000
- Account Service: http://localhost:8001 (internal; exposed for convenience)

### Web UI

The Gateway serves a single-page **test console** at `/` that can exercise
**every requirement** from the browser — no `curl` needed:

- **Submit / query** events, balances, account detail, and the audit trail; live
  health + circuit-breaker + downstream indicators in the header.
- **Fault injection** — toggle the Account Service to *Healthy / Unavailable /
  Slow* (`POST /test/fault`, gated by `ENABLE_TEST_CONTROLS`). This drives the
  real retry + circuit-breaker code, so you can test resiliency and graceful
  degradation without stopping any container.
- **Acceptance scenario runner** — “Run all scenarios” executes automated checks
  for validation, idempotency, out-of-order, balance, tracing, health, metrics,
  and resiliency/degradation, showing pass/fail with details (11/11 green).

It calls the Gateway on the same origin, so there's no extra setup.

Quick smoke test:

```bash
# Submit an event
curl -X POST localhost:8000/events -H 'content-type: application/json' -d '{
  "eventId":"evt-001","accountId":"acct-123","type":"CREDIT","amount":150.00,
  "currency":"USD","eventTimestamp":"2026-05-15T14:02:11Z",
  "metadata":{"source":"mainframe-batch"}}'

curl localhost:8000/events/evt-001
curl "localhost:8000/events?account=acct-123"
curl localhost:8000/accounts/acct-123/balance
curl localhost:8000/health
```

See trace propagation in action:

```bash
docker compose logs | grep <trace_id_from_the_x-trace-id_response_header>
```

## Run locally without Docker

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

# Terminal 1 — Account Service
uvicorn account_service.main:app --port 8001

# Terminal 2 — Gateway (point it at the Account Service)
ACCOUNT_SERVICE_URL=http://localhost:8001 uvicorn gateway.main:app --port 8000
```

## Run the tests

```bash
pip install -r requirements-dev.txt
pytest                       # all 63 tests
pytest tests/unit            # 33 unit tests (fast, no I/O)
pytest tests/functional      # 30 functional/integration tests

# Coverage + HTML reports into reports/ (92% combined)
make coverage                # or: make reports
```

The suite (63 tests) is split into **unit** (`tests/unit/`) and **functional**
(`tests/functional/`) layers and covers:

- **Core**: validation, idempotency, out-of-order ordering, exact-decimal balances
- **Resiliency**: Account Service failure → `503`; circuit breaker opens & fails
  fast; breaker recovery; retry succeeds on a transient failure
- **Graceful degradation**: local reads keep working while the downstream is down
- **Tracing**: trace IDs flow Gateway → Account Service (asserted via captured logs)
- **Auditing**: audit entries recorded for accept/duplicate/outage, across services
- **Integration**: full `POST /events` → balance flow against a *real* Account
  Service running over HTTP on a background port

Coverage and per-suite test reports are generated under [`reports/`](reports/)
(`coverage.xml`, `htmlcov/index.html`, `unit-tests.html`, `functional-tests.html`).

## Configuration

| Variable | Default | Service | Purpose |
|---|---|---|---|
| `GATEWAY_DB_PATH` | `:memory:` | Gateway | SQLite path (`:memory:` or a file) |
| `ACCOUNT_DB_PATH` | `:memory:` | Account | SQLite path |
| `ACCOUNT_SERVICE_URL` | `http://localhost:8001` | Gateway | Downstream base URL |
| `ACCOUNT_TIMEOUT_SECONDS` | `2.0` | Gateway | Per-attempt timeout |
| `ACCOUNT_MAX_RETRIES` | `2` | Gateway | Retries (total attempts = retries + 1) |
| `ACCOUNT_BACKOFF_BASE` / `ACCOUNT_BACKOFF_MAX` | `0.1` / `2.0` | Gateway | Backoff window (seconds) |
| `CB_FAILURE_THRESHOLD` | `5` | Gateway | Failures before the breaker opens |
| `CB_RECOVERY_TIMEOUT` | `10.0` | Gateway | Cooldown before a HALF_OPEN trial |

## Design notes & trade-offs

- **Apply-then-persist ordering.** The Gateway calls the Account Service before
  writing its own record. Because both sides are idempotent on `eventId`, this
  avoids recording an event the ledger never applied; a crash between the two
  steps self-heals on resubmission.
- **Decimal money.** SQLite has no decimal type, so amounts are stored as strings
  and computed with `Decimal` for exactness.
- **Lightweight tracing instead of full OpenTelemetry.** The implementation is
  W3C Trace Context-compatible, so swapping in the OTel SDK + an exporter
  (Jaeger/Zipkin) later is a localized change in `common/`.

### Possible next steps (bonus, not implemented)

OpenTelemetry Collector + Jaeger for visualization; rate limiting on the Gateway;
an async local queue that buffers events while the Account Service is down and
drains on recovery.
