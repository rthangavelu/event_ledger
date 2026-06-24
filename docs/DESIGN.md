# Event Ledger — Design Document

> Authored as part of the **Design Agent** step of the AI-assisted SDLC
> workflow (see [`AI_SDLC.md`](AI_SDLC.md)). Diagrams are Mermaid so they render
> directly on GitHub/GitLab and stay version-controlled alongside the code.

## 1. Problem & goals

Ingest financial transaction events from multiple, loosely-synchronized upstream
systems and maintain correct account balances despite:

- **Duplicate delivery** — the same `eventId` may arrive multiple times.
- **Out-of-order delivery** — earlier-timestamped events may arrive later.

…while remaining **observable** (tracing, structured logs, metrics, audit) and
**resilient** (graceful behavior when a dependency is down).

### Non-functional requirements

| Concern | Target / approach |
|---|---|
| Correctness | Idempotent writes; order-independent balances; exact decimal math |
| Availability | Read endpoints stay up during downstream outage; fast-fail writes |
| Observability | Trace propagation, JSON logs, Prometheus metrics, audit trail |
| Resiliency | Timeout + retry/backoff + circuit breaker on downstream calls |
| Operability | Health checks, Docker Compose, config via env vars |
| Testability | Unit + functional suites, coverage reporting |

## 2. System context

```mermaid
flowchart LR
    client([Client / Upstream systems])
    subgraph Gateway["Event Gateway (public · :8000)"]
        GW_API[REST API]
        GW_DB[(SQLite: events + audit)]
        GW_CB{{Resilient AccountClient<br/>timeout · retry · circuit breaker}}
    end
    subgraph Account["Account Service (internal · :8001)"]
        AC_API[REST API]
        AC_DB[(SQLite: transactions + audit)]
    end

    client -->|HTTPS REST| GW_API
    GW_API --> GW_DB
    GW_API --> GW_CB
    GW_CB -->|REST + traceparent| AC_API
    AC_API --> AC_DB
```

Key boundary rule: **each service owns a private database**. The Gateway never
reads the Account Service's tables and vice-versa; they integrate only through
the REST contract in §6.

## 3. Component / module view

```mermaid
flowchart TB
    subgraph common["common/ (shared, no business logic)"]
        tracing[tracing.py<br/>W3C traceparent]
        logging[logging_config.py<br/>JSON logs]
        metrics[metrics.py<br/>counters + histogram]
        middleware[middleware.py<br/>ASGI observability]
        audit[audit.py<br/>append-only trail]
        errors[errors.py<br/>global handler]
    end

    subgraph gw["gateway/"]
        gw_main[main.py<br/>endpoints + validation + idempotency]
        gw_client[account_client.py<br/>resilient HTTP]
        gw_cb[circuit_breaker.py]
        gw_db[db.py]
        gw_models[models.py]
    end

    subgraph ac["account_service/"]
        ac_main[main.py<br/>apply tx + balances]
        ac_db[db.py]
        ac_models[models.py]
    end

    gw --> common
    ac --> common
    gw_main --> gw_client --> gw_cb
```

## 4. Request flow — `POST /events` (happy path)

```mermaid
sequenceDiagram
    autonumber
    participant C as Client
    participant G as Gateway
    participant CB as CircuitBreaker
    participant A as Account Service
    participant GA as Gateway Audit/DB
    participant AA as Account Audit/DB

    C->>G: POST /events (eventId, …)
    Note over G: Middleware starts/continues trace,<br/>generates traceparent
    G->>G: Validate payload (Pydantic)
    G->>GA: lookup eventId (idempotency)
    alt already seen
        G-->>C: 200 OK (duplicate=true)
    else new event
        G->>CB: allow_request()?
        CB-->>G: yes (CLOSED)
        G->>A: POST /accounts/{id}/transactions<br/>(traceparent header)
        A->>AA: insert transaction (idempotent) + audit APPLIED
        A-->>G: 201 Created
        G->>CB: record_success()
        G->>GA: insert event + audit ACCEPTED
        G-->>C: 201 Created (duplicate=false, x-trace-id)
    end
```

**Why apply-then-persist?** The Gateway applies the transaction downstream
*before* writing its own event row. Both sides are idempotent on `eventId`, so a
retry or crash between the two steps cannot double-count, and resubmission
self-heals.

## 5. Resiliency & graceful degradation

### Circuit breaker state machine

```mermaid
stateDiagram-v2
    [*] --> CLOSED
    CLOSED --> OPEN: consecutive failures ≥ threshold
    OPEN --> HALF_OPEN: recovery_timeout elapsed
    HALF_OPEN --> CLOSED: trial success(es) ≥ success_threshold
    HALF_OPEN --> OPEN: any trial fails
    CLOSED --> CLOSED: success resets failure count
```

### Behavior when the Account Service is down

```mermaid
sequenceDiagram
    autonumber
    participant C as Client
    participant G as Gateway
    participant A as Account Service (DOWN)

    C->>G: POST /events
    G->>A: POST transaction (attempt 1)
    A--xG: connection refused / timeout
    G->>A: retry (backoff + jitter)
    A--xG: still failing
    G-->>C: 503 account_service_unavailable (not recorded)
    Note over G: After N failures the breaker OPENs →<br/>subsequent calls fail fast

    C->>G: GET /events/{id}
    G-->>C: 200 OK (served from Gateway DB)
    C->>G: GET /accounts/{id}/balance
    G-->>C: 503 (clearly degraded)
```

This satisfies the requirement that **local reads keep working** while
**writes and balance queries fail clearly** instead of hanging or 500-ing.

## 6. API contract (Gateway ⇄ Account Service)

`POST /accounts/{accountId}/transactions`

```json
// request (from Gateway)
{ "eventId": "evt-001", "type": "CREDIT", "amount": "150.00",
  "currency": "USD", "eventTimestamp": "2026-05-15T14:02:11+00:00" }
// response 201 (new) / 200 (duplicate)
{ "eventId": "evt-001", "accountId": "acct-123", "type": "CREDIT",
  "amount": "150", "currency": "USD", "eventTimestamp": "...",
  "createdAt": "...", "duplicate": false }
```

Headers: the Gateway injects `traceparent: 00-<trace>-<span>-01`; both services
echo `x-trace-id` on responses. Amounts cross the wire as **strings** to preserve
decimal precision.

## 7. Data model

```mermaid
erDiagram
    EVENTS {
        TEXT event_id PK
        TEXT account_id
        TEXT type
        TEXT amount
        TEXT currency
        TEXT event_timestamp
        TEXT metadata_json
        TEXT received_at
    }
    TRANSACTIONS {
        TEXT event_id PK
        TEXT account_id
        TEXT type
        TEXT amount
        TEXT currency
        TEXT event_timestamp
        TEXT created_at
    }
    AUDIT_LOG {
        INTEGER id PK
        TEXT timestamp
        TEXT service
        TEXT action
        TEXT outcome
        TEXT account_id
        TEXT event_id
        TEXT trace_id
        TEXT detail_json
    }
```

- `EVENTS` lives in the Gateway DB; `TRANSACTIONS` in the Account Service DB.
- `AUDIT_LOG` exists (independently) in **both** databases — append-only.
- `event_id` is the primary key everywhere → the idempotency guarantee is
  enforced at the storage layer, not just in application logic.

## 8. Key design decisions & trade-offs

| Decision | Rationale | Trade-off |
|---|---|---|
| Decimal-as-string money | Exact financial math (no float drift) | Slightly more (de)serialization |
| Apply-then-persist ordering | Avoids recording un-applied events | Tiny window needing idempotent downstream (provided) |
| Custom W3C tracer vs full OTel | Zero deps, satisfies requirements | No spans export UI (OTel is a drop-in upgrade) |
| In-memory SQLite + lock | Simple, embedded, per-service | Single-node; not for HA persistence |
| Layered resiliency (3 patterns) | Each covers a distinct failure mode | More config surface |
| Pure-ASGI middleware | Correct contextvar propagation | Slightly lower-level than `BaseHTTPMiddleware` |

## 9. Future work (not implemented)

- OpenTelemetry SDK + Collector + Jaeger/Zipkin for trace visualization.
- Async local queue: buffer events while the Account Service is down, drain on
  recovery (turns write outages into eventual consistency).
- Rate limiting on the Gateway; per-account currency support; persistent DB.
