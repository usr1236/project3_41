# VitalTrack Project 3 Task 4 Prototype

This folder contains a full working prototype for **two end-to-end workflows** deployed as **microservices**:

1. **E2E-1 Critical Alert Pipeline**  
   `Gateway -> Ingestion Service -> Risk/Rule Evaluation -> Alert Creation -> RabbitMQ Publish -> Notification Service Consumer -> Realtime Dashboard Event -> Audit Log`
2. **E2E-2 Clinical Acknowledgment Pipeline**  
   `Doctor Acknowledges Alert -> Alert Status Update -> Realtime Dashboard Event -> Audit Log`

## Stack

- FastAPI ingestion service
- FastAPI notification service
- React frontend (separate `frontend/` project)
- NGINX API gateway
- TimescaleDB (PostgreSQL + `timescaledb` extension)
- RabbitMQ message broker (`AlertCreated` queue)
- WebSocket push updates
- JWT auth with RBAC
- Failed-event handling with retry endpoint

## Run

From this directory:

```bash
docker compose up --build
```

Open:

- Gateway + frontend UI: [http://localhost:8000](http://localhost:8000)
- RabbitMQ management: [http://localhost:15672](http://localhost:15672) (`guest/guest`)

## Seeded Users

- `admin / admin123`
- `doctor1 / doctor123`
- `patient1 / patient123`
- `caregiver1 / care123`
- `simulator / sim123`

## E2E Demo Steps

1. Open UI and login as `doctor1`.
2. Click **Get Simulator Token**.
3. Send abnormal vitals (defaults already abnormal).
4. Observe:
   - alert created in API output
   - websocket event appears
   - alert visible in dashboard API call
5. Acknowledge alert using alert id.
6. Observe:
   - status changes to `ACKNOWLEDGED`
   - websocket ack event appears
   - audit logs include both actions
7. Optionally click **Retry Failed Events** to process captured failed notification events.

## API Endpoints

- `POST /auth/token` - get JWT token (Ingestion Service via gateway)
- `POST /auth/register` - self-register patient/caregiver account (Ingestion Service via gateway)
- `POST /v1/vitals` - ingest vital reading (Ingestion Service via gateway)
- `GET /v1/doctor/dashboard` - view open alerts (Notification Service via gateway)
- `GET /v1/patient/portal` - patient portal view (Notification Service via gateway)
- `GET /v1/caregiver/dashboard` - caregiver assigned-patient view (Notification Service via gateway)
- `POST /v1/alerts/{alert_id}/ack` - acknowledge alert (Notification Service via gateway)
- `POST /v1/failed-events/retry` - retry pending failed events (Notification Service via gateway)
- `POST /v1/admin/users` - admin account creation (Notification Service via gateway)
- `POST /v1/admin/caregiver-assignments` - assign caregiver to patient (Notification Service via gateway)
- `GET /v1/admin/patients` - list patient records (Notification Service via gateway)
- `GET /v1/audit` - view audit trail (Notification Service via gateway)
- `GET /` - React frontend (via gateway)
- `WS /ws/doctor` - realtime event stream (Notification Service via gateway)
- `GET /docs` + `GET /openapi.json` - ingestion service docs (via gateway)
- `GET /notification/docs` + `GET /notification/openapi.json` - notification service docs (via gateway)

## Notes for Report (Task 4)

- Prototype deployment style: **microservices with gateway + broker**
- Frontend deployment style: **separate containerized React app**
- Database: **shared TimescaleDB-backed PostgreSQL (prototype compromise)**
- Non-trivial implemented E2E count: **2**
- Failed-event handling is implemented via `failed_events` table and retry workflow.
- Transactional outbox is implemented for alert event delivery. The ingestion service writes `alerts` and `outbox_events` in one transaction, and a background outbox publisher retries broker publish until events transition to `PUBLISHED`.
- Gateway currently provides routing and WebSocket proxying for the prototype. Centralized gateway token validation and rate limiting are deferred to production hardening.
