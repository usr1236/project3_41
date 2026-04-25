# VitalTrack — Security Viewpoint: Access Matrix

This matrix specifies which roles may invoke each architecturally significant endpoint or action. It implements ASR-02 (Security and Privacy), NFR-03, NFR-04, FR-09, and FR-10. Server-side enforcement is via `require_roles(...)` and `_can_access_patient(...)` in the Notification and Ingestion services.

## Legend

| Symbol | Meaning |
|--------|---------|
| ✓ | Full access |
| ✓ (own) | Access only to the user's own patient record |
| ✓ (assigned) | Access only to patients with an APPROVED `CaregiverAssignment` |
| ✓ (scope-checked) | Subject to `_can_access_patient(user, patient_id)` |
| ⚠ (prototype only) | Allowed in prototype for evaluator convenience; restricted in production |
| ✗ | Forbidden |

## Endpoint × Role matrix

| Endpoint / Action | ADMIN | DOCTOR | PATIENT | CAREGIVER | SIMULATOR |
|---|:-:|:-:|:-:|:-:|:-:|
| `POST /auth/token` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `POST /auth/register` (PATIENT) | ✓ | ✗ | ✓ | ✗ | ✗ |
| `POST /auth/register` (CAREGIVER) | ✓ | ✗ | ✗ | ✓ | ✗ |
| `POST /auth/register` (DOCTOR) | ✓ | ⚠ (prototype only) | ✗ | ✗ | ✗ |
| `POST /v1/vitals` | ✗ | ✓ | ✗ | ✗ | ✓ |
| `GET /v1/patients/{id}/vitals` | ✓ | ✓ (prototype-wide access; production scope-restricted) | ✓ (own) | ✓ (assigned) | ✗ |
| `GET /v1/patients/{id}/stats` | ✓ | ✓ (prototype-wide access; production scope-restricted) | ✓ (own) | ✓ (assigned) | ✗ |
| `GET /v1/patients/{id}/predictions` | ✓ | ✓ (prototype-wide access; production scope-restricted) | ✓ (own) | ✓ (assigned) | ✗ |
| `GET /v1/doctor/dashboard` | ✓ | ✓ | ✗ | ✗ | ✗ |
| `POST /v1/alerts/{id}/ack` | ✓ | ✓ | ✗ | ✗ | ✗ |
| `GET /v1/patient/portal` | ✗ | ✗ | ✓ (own) | ✗ | ✗ |
| `GET /v1/caregiver/dashboard` | ✗ | ✗ | ✗ | ✓ (assigned) | ✗ |
| `POST /v1/chatbot/message` | ✓ | ✓ | ✓ (own) | ✓ (scope-checked, advisory) | ✗ |
| `GET /v1/notifications` | ✓ | ✓ | ✓ (own) | ✓ (assigned) | ✗ |
| `GET /v1/admin/caregiver-requests` (list pending) | ✓ | ✗ | ✗ | ✗ | ✗ |
| `POST /v1/admin/caregiver-requests/{id}/approve` | ✓ | ✗ | ✗ | ✗ | ✗ |
| `POST /v1/admin/caregiver-requests/{id}/reject` | ✓ | ✗ | ✗ | ✗ | ✗ |
| `POST /v1/admin/caregiver-assignments` | ✓ | ✗ | ✗ | ✗ | ✗ |
| `POST /v1/admin/users` (create user) | ✓ | ✗ | ✗ | ✗ | ✗ |
| `GET /v1/admin/patients` | ✓ | ✓ | ✗ | ✗ | ✓ |
| `GET /v1/audit` | ✓ | ✗ | ✗ | ✗ | ✗ |
| `GET /v1/system/metrics` | ✓ | ✗ | ✗ | ✗ | ✓ |
| `GET /v1/queue/health` | ✓ | ✓ | ✗ | ✗ | ✗ |
| `POST /v1/failed-events/retry` | ✓ | ✓ | ✗ | ✗ | ✗ |
| `GET /v1/prediction/health` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `GET /v1/escalation/health` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `WebSocket /ws/doctor` | ✓ | ✓ | ✗ | ✗ | ✗ |

Caregiver link requests are self-submitted through `POST /auth/register` with `role=CAREGIVER` and `patient_id`; there is no standalone `POST /v1/caregiver-requests` endpoint in the current implementation.

## Access-scope rules (server-side, in addition to role checks)

The `_can_access_patient(user, patient_id)` helper returns `True` only when at least one of the following holds:

1. `user.role == ADMIN`
2. `user.role == DOCTOR` (assumed authorized in prototype; production restricts to assigned doctors)
3. `user.role == PATIENT` and `user.patient.id == patient_id`
4. `user.role == CAREGIVER` and an APPROVED `CaregiverAssignment` exists linking `user.id` to `patient_id`

Cells marked **scope-checked** in the matrix above pass through this helper before any patient data is returned.

## Audit coverage (FR-10, NFR-04)

Implemented audited actions include (not exhaustive of all possible reads):

- Vital and outbox events (e.g., `VITAL_INGESTED`, `VITAL_EVENT_ENQUEUED`, `ALERT_EVENT_ENQUEUED`)
- Alert lifecycle transitions (e.g., `ALERT_CREATED`, `ALERT_ACKNOWLEDGED`)
- Alert/notification processing (e.g., `ALERT_NOTIFICATIONS_PROCESSED`, `PREDICTION_ALERT_CREATED`)
- Admin operations (e.g., `ADMIN_USER_CREATED`, `CAREGIVER_ASSIGNED`, `CAREGIVER_REQUEST_APPROVED`, `CAREGIVER_REQUEST_REJECTED`)
- All escalation steps (ESCALATION_CASE_STARTED, ESCALATION_STEP_DISPATCHED, ESCALATION_COMPLETED_ON_ACK, ESCALATION_EXHAUSTED)
- All chatbot interactions (CHATBOT_MESSAGE, with `advisory_only: true` and `strategy_used` in metadata)

Current implementation does **not** universally audit every successful endpoint invocation, login attempts, or all denied patient-data reads.

## Prototype-only deviations

| Deviation | Production target |
|---|---|
| Doctor self-registration via `/auth/register` | Doctor accounts created only by ADMIN through verified credentialing workflow (see ADR-009 note) |
| TLS termination not at gateway | TLS 1.2+ at the gateway with valid certificates |
| Token signing key in environment variable | Hardware-backed key management or dedicated secret store |
| Indefinite data retention | Configurable retention policies aligned with HIPAA / organizational policy (see NFR-04) |

## Mapping to QAS

- **QAS-03 (Unauthorized Access Attempt)** is enforced by every "✗" cell in the matrix and verified by `test_access_scope_unit.py`.
- **QAS-04 (Patient Data Scope Violation)** is enforced by the **scope-checked** cells via `_can_access_patient()`.
