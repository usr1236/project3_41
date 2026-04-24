# VitalTrack Project 3 Task 4 Prototype

This folder contains a full working prototype for **two end-to-end workflows** deployed as **microservices**:

1. **E2E-1 Critical Alert Pipeline**  
   `Gateway -> Ingestion Service -> Risk/Rule Evaluation -> Alert Creation -> RabbitMQ Publish -> Notification Service Consumer -> Realtime Dashboard Event -> Audit Log`
2. **E2E-2 Clinical Acknowledgment Pipeline**  
   `Doctor Acknowledges Alert -> Alert Status Update -> Realtime Dashboard Event -> Audit Log`

## Stack

- FastAPI ingestion service
- FastAPI notification service
- FastAPI prediction service
- FastAPI escalation mediator service
- React frontend (separate `frontend/` project)
- NGINX API gateway
- TimescaleDB (PostgreSQL + `timescaledb` extension)
- RabbitMQ message broker (`vitaltrack.vitals.received`, `vitaltrack.notifications.events`)
- RabbitMQ DLX/DLQ for consumer failures (`vitaltrack.dlx`, per-queue `.dlq`)
- WebSocket push updates
- JWT auth with RBAC
- Failed-event handling with retry endpoint

## Run the Full App

From project root:

```bash
docker compose up --build -d
docker compose ps
```

Open:

- Gateway + frontend UI: [http://localhost:8000](http://localhost:8000)
- RabbitMQ management: [http://localhost:15672](http://localhost:15672) (`guest/guest`)

Stop / restart:

```bash
docker compose down
docker compose up -d
```

Rebuild a single changed service:

```bash
docker compose up --build -d notification
```

## Login Accounts (Seeded)

- `admin / admin123`
- `doctor1 / doctor123`
- `patient1 / patient123`
- `caregiver1 / care123`
- `simulator / sim123`

## Use the Full App (Role Flows)

### 1) Simulator

- Login as `simulator`.
- Go to Simulator dashboard.
- Send manual vitals or enable auto mode:
  - one patient id
  - selected patient ids
  - all patients
- Use abnormal values to trigger alerts and prediction events.

### 2) Doctor

- Login as `doctor1`.
- Open Doctor dashboard.
- Observe:
  - incoming websocket events
  - active alerts
  - patient vitals and predictions
- Acknowledge alerts from the dashboard/API.

### 3) Patient

- Login as `patient1`.
- Open Patient portal:
  - own vitals history
  - own alerts and status
  - chatbot (advisory only; no alert escalation from chatbot messages)

### 4) Caregiver / Relative

- Login as caregiver.
- Caregiver can register and request patient linkage.
- After admin approval, caregiver can view assigned patient data and alerts.

### 5) Admin

- Login as `admin`.
- Manage users and caregiver approvals.
- Review notifications panel and audit trail.
- View runtime system metrics (NFR-oriented observability).

## Quick E2E Demo

1. Login as `doctor1` in one browser tab.
2. Login as `simulator` in another tab.
3. In simulator, send critical vitals for patient `1`.
4. In doctor dashboard, verify new alert + websocket event.
5. Wait escalation interval and verify escalation notification/audit entry.
6. Acknowledge alert and verify status/event update.
7. Login as `patient1`, send chatbot message, verify response is advisory-only (`escalated=false`).

## Verify Functions via API

Get token example:

```bash
curl -s -X POST http://localhost:8000/auth/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=admin&password=admin123"
```

Health checks:

```bash
curl -s http://localhost:8000/v1/health
curl -s http://localhost:8000/v1/queue/health
curl -s http://localhost:8000/v1/prediction/health
curl -s http://localhost:8000/v1/escalation/health
```

Queue health (includes DLQ depth):

```bash
curl -s http://localhost:8000/v1/queue/health \
  -H "Authorization: Bearer <ADMIN_OR_DOCTOR_TOKEN>"
```

Ingest critical vitals:

```bash
curl -s -X POST http://localhost:8000/v1/vitals \
  -H "Authorization: Bearer <SIM_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{
    "patient_id":1,
    "heart_rate":147,
    "spo2":84,
    "bp_sys":172,
    "bp_dia":102,
    "respiratory_rate":31,
    "temperature":39.6,
    "source":"manual-test"
  }'
```

Verify notifications and audit:

```bash
curl -s "http://localhost:8000/v1/notifications?limit=50" \
  -H "Authorization: Bearer <ADMIN_TOKEN>"

curl -s "http://localhost:8000/v1/audit" \
  -H "Authorization: Bearer <ADMIN_TOKEN>"
```

Chatbot advisory-only check:

```bash
curl -s -X POST http://localhost:8000/v1/chatbot/message \
  -H "Authorization: Bearer <PATIENT_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"message":"I have chest pain and breathlessness","patient_id":1}'
```

Expected chatbot behavior: `risk_level` may be high/critical, but `escalated=false` and `alert_id=null`.

## See Metrics

- UI: Admin and Simulator dashboards
- API:

```bash
curl -s http://localhost:8000/v1/system/metrics \
  -H "Authorization: Bearer <ADMIN_TOKEN>"
```

Includes ingestion rate, outbox health, queue depth/consumers, and outbox publish latency percentiles.
Queue health also reports DLQ message count for failed consumer payloads.

## Automated Tests (NFR-07)

The repository now includes a root `tests/` directory with:

- Unit tests for critical functions:
  - risk scoring and alert evaluation (`tests/unit/test_services_unit.py`)
  - access-scope enforcement (`tests/unit/test_access_scope_unit.py`)
  - prediction strategy severity logic (`tests/unit/test_prediction_unit.py`)
- Integration tests for implemented E2E flows:
  - critical alert pipeline (`tests/integration/test_e2e_critical_alert_pipeline.py`)
  - clinical acknowledgment pipeline (`tests/integration/test_e2e_acknowledgment_pipeline.py`)

Install test dependency:

```bash
pip install -r backend/requirements.txt
```

Run unit tests:

```bash
python3 -m pytest tests/unit -q
```

Run integration tests (requires running stack on `http://localhost:8000`):

```bash
python3 -m pytest tests/integration -q
```

Override runtime base URL if needed:

```bash
VITALTRACK_BASE_URL=http://localhost:8000 python3 -m pytest tests/integration -q
```

## API Endpoints

- `POST /auth/token` - get JWT token (Ingestion Service via gateway)
- `POST /auth/register` - self-register patient/caregiver account (Ingestion Service via gateway)
- `POST /v1/vitals` - ingest vital reading (Ingestion Service via gateway)
- `GET /v1/doctor/dashboard` - view open alerts (Notification Service via gateway)
- `GET /v1/patients/{patient_id}/predictions` - prediction history for patient (Notification Service via gateway)
- `POST /v1/chatbot/message` - direct in-app health chatbot triage endpoint (Notification Service via gateway)
- `GET /v1/patient/portal` - patient portal view (Notification Service via gateway)
- `GET /v1/caregiver/dashboard` - caregiver assigned-patient view (Notification Service via gateway)
- `POST /v1/alerts/{alert_id}/ack` - acknowledge alert (Notification Service via gateway)
- `POST /v1/failed-events/retry` - retry pending failed events (Notification Service via gateway)
- `POST /v1/admin/users` - admin account creation (Notification Service via gateway)
- `POST /v1/admin/caregiver-assignments` - assign caregiver to patient (Notification Service via gateway)
- `GET /v1/admin/patients` - list patient records (Notification Service via gateway)
- `GET /v1/system/metrics` - NFR/runtime metrics snapshot (Admin/Simulator)
- `GET /v1/audit` - view audit trail (Notification Service via gateway)
- `GET /v1/prediction/health` - prediction service health endpoint
- `GET /v1/escalation/health` - escalation mediator health endpoint
- `GET /` - React frontend (via gateway)
- `WS /ws/doctor` - realtime event stream (Notification Service via gateway)
- `GET /docs` + `GET /openapi.json` - ingestion service docs (via gateway)
- `GET /notification/docs` + `GET /notification/openapi.json` - notification service docs (via gateway)
- `GET /prediction/docs` + `GET /prediction/openapi.json` - prediction service docs (via gateway)
- `GET /escalation/docs` + `GET /escalation/openapi.json` - escalation service docs (via gateway)

## Troubleshooting

- View all logs:

```bash
docker compose logs -f
```

- Service-specific logs:

```bash
docker compose logs -f ingestion
docker compose logs -f notification
docker compose logs -f prediction
docker compose logs -f escalation
docker compose logs -f gateway
```

- If endpoints fail after rebuild:

```bash
docker compose restart gateway
```

- If OpenAI key is not set, chatbot automatically falls back to local triage (still advisory-only).

## Notes for Report (Task 4)

- Prototype deployment style: **microservices with gateway + broker**
- Frontend deployment style: **separate containerized React app**
- Database: **shared TimescaleDB-backed PostgreSQL (prototype compromise)**
- Non-trivial implemented E2E count: **2**
- Failed-event handling is implemented via `failed_events` table and retry workflow.
- Transactional outbox is implemented for alert event delivery. The ingestion service writes `alerts` and `outbox_events` in one transaction, and a background outbox publisher retries broker publish until events transition to `PUBLISHED`.
- Broker messages now use a versioned event envelope (`schema_version`, `event_type`, `occurred_at`, `data`) for producer/consumer compatibility and independent evolution.
- Ingestion emits `VITAL_RECEIVED` events for every accepted reading and `ALERT_CREATED` when alert rules trigger.
- Prediction service consumes `VITAL_RECEIVED`, runs two strategies (NEWS2-inspired EWS and personalized rolling z-score baseline), and publishes `RISK_PREDICTED`.
- Prediction outputs are persisted to `prediction_records` for traceability and historical review.
- Notification service consumes both `ALERT_CREATED` and `RISK_PREDICTED`; high prediction severity creates prediction-based alerts.
- Escalation mediator consumes `ALERT_CREATED` for critical alerts and executes timed escalation steps until acknowledgment or exhaustion.
- Chatbot uses OpenAI by default (`OPENAI_CHATBOT_ENABLED=true`). If `OPENAI_API_KEY` is missing or call fails, it safely falls back to local rule-based triage.
- Gateway currently provides routing and WebSocket proxying for the prototype. Centralized gateway token validation and rate limiting are deferred to production hardening.
- Admin and Simulator dashboards expose observable NFR-oriented metrics (ingestion rate, active patients, outbox health, queue depth/consumers, and outbox publish latency percentiles).
- Auto mode supports one, some, or all patient IDs simultaneously for workload and scalability demonstrations.

## Additional ADRs (Latest)

- **ADR-009: Admin-Mediated Caregiver-to-Patient Assignment**
  - Caregiver linkage requests remain `PENDING` until admin approval/rejection.
  - Approved requests create scoped caregiver-patient assignments.
- **ADR-010: Versioned Event Envelope for Broker Messages**
  - Broker events should carry schema-version and event-type metadata to support independent producer/consumer evolution.
- **ADR-011: Runtime NFR Metrics Visibility for Operations Roles**
  - Admin and Simulator roles can query a consolidated `/v1/system/metrics` endpoint to inspect reliability/performance indicators.
- **ADR-012: Multi-Patient Auto-Mode Streaming Control**
  - Auto mode supports concurrent generation for one, selected, or all patient IDs to validate multi-patient runtime behavior.
