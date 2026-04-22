from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import logging
import os
import time

from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
import pika
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from .auth import get_password_hash, get_user_from_token, require_roles
from .db import engine, get_db
from .messaging import RabbitMQBridge
from .models import (
    Alert,
    AlertStatus,
    CaregiverAssignment,
    CaregiverLinkRequest,
    CaregiverRequestStatus,
    Patient,
    User,
    UserRole,
    VitalReading,
)
from .schemas import (
    AckResponse,
    AdminCreateUserRequest,
    CaregiverAssignmentRequest,
    CaregiverDashboardResponse,
    CaregiverRequestReviewRequest,
    DashboardResponse,
    FailedRetryResponse,
    PatientStatsResponse,
    PatientPortalResponse,
    RegisterResponse,
    VitalsSeriesResponse,
)
from .services import (
    create_audit_log,
    notify_doctors_or_capture_failure,
    retry_failed_events,
    serialize_ack_event,
    serialize_alert_event,
)

class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, event: dict):
        dead: list[WebSocket] = []
        for connection in self.active_connections:
            try:
                await connection.send_json(event)
            except RuntimeError:
                dead.append(connection)
        for ws in dead:
            self.disconnect(ws)


manager = ConnectionManager()
rabbit_bridge: RabbitMQBridge | None = None
main_loop: asyncio.AbstractEventLoop | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global rabbit_bridge, main_loop
    main_loop = asyncio.get_running_loop()
    rabbit_url = os.getenv("RABBITMQ_URL", "")
    rabbit_queue = os.getenv("RABBITMQ_ALERT_QUEUE", "vitaltrack.alerts.created")
    if rabbit_url:
        rabbit_bridge = RabbitMQBridge(rabbit_url, rabbit_queue, handle_alert_created_event)
        rabbit_bridge.start_consumer()
    yield
    if rabbit_bridge is not None:
        rabbit_bridge.stop_consumer()


app = FastAPI(title="VitalTrack Notification Service", lifespan=lifespan)
logger = logging.getLogger("vitaltrack.notification")


def _can_access_patient(db: Session, user: User, patient_id: int) -> bool:
    if user.role in {UserRole.ADMIN.value, UserRole.DOCTOR.value}:
        return True
    if user.role == UserRole.PATIENT.value:
        own_patient = db.scalar(select(Patient).where(Patient.user_id == user.id))
        return own_patient is not None and own_patient.id == patient_id
    if user.role == UserRole.CAREGIVER.value:
        assignment = db.scalar(
            select(CaregiverAssignment).where(
                CaregiverAssignment.caregiver_user_id == user.id,
                CaregiverAssignment.patient_id == patient_id,
            )
        )
        return assignment is not None
    return False


def handle_alert_created_event(payload: dict):
    alert_id = payload.get("alert_id")
    if not alert_id:
        return
    ws_event_payload: dict | None = None
    with Session(engine) as db:
        alert = None
        # Handle commit-vs-consume race with bounded retry.
        for _ in range(5):
            alert = db.scalar(select(Alert).where(Alert.id == int(alert_id)))
            if alert is not None:
                break
            time.sleep(0.1)
        if alert is None:
            logger.warning("Dropping stale alert event: alert_id=%s not found after retries", alert_id)
            return
        notify_doctors_or_capture_failure(db, alert)
        # Build event payload before commit/session teardown to avoid detached-instance access.
        ws_event_payload = serialize_alert_event(alert)
        create_audit_log(
            db,
            actor_username="rabbitmq-consumer",
            action="ALERT_NOTIFICATIONS_PROCESSED",
            target_type="ALERT",
            target_id=str(alert.id),
            metadata={"severity": alert.severity},
        )
        db.commit()
    if main_loop is not None and ws_event_payload is not None:
        fut = asyncio.run_coroutine_threadsafe(manager.broadcast(ws_event_payload), main_loop)

        def _log_future_exception(done_fut):  # noqa: ANN001
            try:
                done_fut.result()
            except Exception as exc:  # pragma: no cover - best-effort logging
                logger.warning("WebSocket broadcast failed after alert processing: %s", exc)

        fut.add_done_callback(_log_future_exception)


@app.get("/v1/doctor/dashboard", response_model=DashboardResponse)
def doctor_dashboard(
    db: Session = Depends(get_db), _: User = Depends(require_roles(UserRole.DOCTOR, UserRole.ADMIN))
):
    rows = db.execute(
        select(Alert, func.count(Alert.id).over().label("total_open"))
        .where(Alert.status == AlertStatus.OPEN.value)
        .order_by(Alert.created_at.desc())
        .limit(100)
    ).all()
    alerts = [row[0] for row in rows]
    total = int(rows[0][1]) if rows else 0
    return DashboardResponse(active_alerts=alerts, total_open_alerts=total)


@app.get("/v1/patients/{patient_id}/vitals", response_model=VitalsSeriesResponse)
def patient_vitals_series(
    patient_id: int,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.DOCTOR, UserRole.PATIENT, UserRole.CAREGIVER)),
):
    if not _can_access_patient(db, current_user, patient_id):
        raise HTTPException(status_code=403, detail="No access to requested patient")
    safe_limit = max(1, min(limit, 500))
    rows = db.scalars(
        select(VitalReading)
        .where(VitalReading.patient_id == patient_id)
        .order_by(VitalReading.ts.desc())
        .limit(safe_limit)
    ).all()
    points = [
        {
            "ts": r.ts.isoformat(),
            "heart_rate": r.heart_rate,
            "spo2": r.spo2,
            "bp_sys": r.bp_sys,
            "bp_dia": r.bp_dia,
            "temperature": r.temperature,
            "source": r.source,
        }
        for r in reversed(rows)
    ]
    return VitalsSeriesResponse(patient_id=patient_id, points=points)


@app.get("/v1/patients/{patient_id}/stats", response_model=PatientStatsResponse)
def patient_stats(
    patient_id: int,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.DOCTOR, UserRole.PATIENT, UserRole.CAREGIVER)),
):
    if not _can_access_patient(db, current_user, patient_id):
        raise HTTPException(status_code=403, detail="No access to requested patient")
    safe_limit = max(1, min(limit, 500))
    rows = db.scalars(
        select(VitalReading)
        .where(VitalReading.patient_id == patient_id)
        .order_by(VitalReading.ts.desc())
        .limit(safe_limit)
    ).all()
    if not rows:
        return PatientStatsResponse(patient_id=patient_id, window_minutes=0, summary={})
    hrs = [r.heart_rate for r in rows]
    spo2 = [r.spo2 for r in rows]
    temps = [r.temperature for r in rows]
    window_minutes = int(max((rows[0].ts - rows[-1].ts).total_seconds(), 0) // 60)
    summary = {
        "samples": len(rows),
        "heart_rate_avg": round(sum(hrs) / len(hrs), 2),
        "heart_rate_max": max(hrs),
        "spo2_min": min(spo2),
        "temperature_max": max(temps),
        "latest_ts": rows[0].ts.isoformat(),
    }
    return PatientStatsResponse(patient_id=patient_id, window_minutes=window_minutes, summary=summary)


@app.get("/v1/patient/portal", response_model=PatientPortalResponse)
def patient_portal(
    db: Session = Depends(get_db), current_user: User = Depends(require_roles(UserRole.PATIENT))
):
    patient = db.scalar(select(Patient).where(Patient.user_id == current_user.id))
    if patient is None:
        raise HTTPException(status_code=404, detail="Patient profile not found")

    vitals = db.scalars(
        select(VitalReading).where(VitalReading.patient_id == patient.id).order_by(VitalReading.ts.desc()).limit(20)
    ).all()
    alerts = db.scalars(
        select(Alert).where(Alert.patient_id == patient.id).order_by(Alert.created_at.desc()).limit(20)
    ).all()

    recent_vitals = [
        {
            "id": v.id,
            "ts": v.ts.isoformat(),
            "heart_rate": v.heart_rate,
            "spo2": v.spo2,
            "bp_sys": v.bp_sys,
            "bp_dia": v.bp_dia,
            "temperature": v.temperature,
            "source": v.source,
        }
        for v in vitals
    ]
    recent_alerts = [
        {
            "id": a.id,
            "severity": a.severity,
            "message": a.message,
            "status": a.status,
            "created_at": a.created_at.isoformat(),
            "ack_at": a.ack_at.isoformat() if a.ack_at else None,
        }
        for a in alerts
    ]
    return PatientPortalResponse(
        patient_id=patient.id,
        full_name=patient.full_name,
        recent_vitals=recent_vitals,
        recent_alerts=recent_alerts,
    )


@app.get("/v1/caregiver/dashboard", response_model=CaregiverDashboardResponse)
def caregiver_dashboard(
    db: Session = Depends(get_db), current_user: User = Depends(require_roles(UserRole.CAREGIVER))
):
    assignments = db.scalars(
        select(CaregiverAssignment).where(CaregiverAssignment.caregiver_user_id == current_user.id)
    ).all()
    patient_rows: list[dict] = []
    for assignment in assignments:
        patient = db.scalar(select(Patient).where(Patient.id == assignment.patient_id))
        if patient is None:
            continue
        latest_alert = db.scalar(
            select(Alert).where(Alert.patient_id == patient.id).order_by(Alert.created_at.desc()).limit(1)
        )
        latest_vital = db.scalar(
            select(VitalReading).where(VitalReading.patient_id == patient.id).order_by(VitalReading.ts.desc()).limit(1)
        )
        patient_rows.append(
            {
                "patient_id": patient.id,
                "full_name": patient.full_name,
                "latest_alert_severity": latest_alert.severity if latest_alert else None,
                "latest_alert_status": latest_alert.status if latest_alert else None,
                "latest_vital_ts": latest_vital.ts.isoformat() if latest_vital else None,
            }
        )
    return CaregiverDashboardResponse(caregiver_username=current_user.username, assigned_patients=patient_rows)


@app.post("/v1/alerts/{alert_id}/ack", response_model=AckResponse)
async def acknowledge_alert(
    alert_id: int,
    db: Session = Depends(get_db),
    doctor: User = Depends(require_roles(UserRole.DOCTOR, UserRole.ADMIN)),
):
    alert = db.scalar(select(Alert).where(Alert.id == alert_id))
    if alert is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    if alert.status != AlertStatus.OPEN.value:
        raise HTTPException(status_code=400, detail="Alert already handled")

    alert.status = AlertStatus.ACKNOWLEDGED.value
    alert.ack_by = doctor.id
    alert.ack_at = datetime.now(timezone.utc)

    create_audit_log(
        db,
        actor_username=doctor.username,
        action="ALERT_ACKNOWLEDGED",
        target_type="ALERT",
        target_id=str(alert.id),
        metadata={"new_status": alert.status},
    )
    db.commit()
    await manager.broadcast(serialize_ack_event(alert, doctor.username))
    return AckResponse(alert_id=alert.id, status=alert.status, ack_by=doctor.username)


@app.post("/v1/failed-events/retry", response_model=FailedRetryResponse)
def retry_failed(
    db: Session = Depends(get_db), _: User = Depends(require_roles(UserRole.ADMIN, UserRole.DOCTOR))
):
    retried, resolved, pending, exhausted = retry_failed_events(db)
    db.commit()
    return FailedRetryResponse(retried=retried, resolved=resolved, pending=pending, exhausted=exhausted)


@app.post("/v1/admin/users", response_model=RegisterResponse)
def admin_create_user(
    payload: AdminCreateUserRequest,
    db: Session = Depends(get_db),
    admin_user: User = Depends(require_roles(UserRole.ADMIN)),
):
    try:
        role = UserRole(payload.role.upper())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid role") from exc
    existing = db.scalar(select(User).where(User.username == payload.username))
    if existing:
        raise HTTPException(status_code=409, detail="Username already exists")

    user = User(username=payload.username, password_hash=get_password_hash(payload.password), role=role.value)
    db.add(user)
    db.flush()

    patient_id = None
    if role == UserRole.PATIENT:
        if not payload.full_name:
            raise HTTPException(status_code=400, detail="full_name is required for PATIENT role")
        patient = Patient(user_id=user.id, full_name=payload.full_name)
        db.add(patient)
        db.flush()
        patient_id = patient.id
    db.commit()

    create_audit_log(
        db,
        actor_username=admin_user.username,
        action="ADMIN_USER_CREATED",
        target_type="USER",
        target_id=str(user.id),
        metadata={"role": user.role, "username": user.username},
    )
    db.commit()
    return RegisterResponse(user_id=user.id, username=user.username, role=user.role, patient_id=patient_id)


@app.post("/v1/admin/caregiver-assignments")
def assign_caregiver(
    payload: CaregiverAssignmentRequest,
    db: Session = Depends(get_db),
    admin_user: User = Depends(require_roles(UserRole.ADMIN)),
):
    caregiver = db.scalar(select(User).where(User.username == payload.caregiver_username))
    if caregiver is None or caregiver.role != UserRole.CAREGIVER.value:
        raise HTTPException(status_code=404, detail="Caregiver user not found")
    patient = db.scalar(select(Patient).where(Patient.id == payload.patient_id))
    if patient is None:
        raise HTTPException(status_code=404, detail="Patient not found")

    existing = db.scalar(
        select(CaregiverAssignment).where(
            CaregiverAssignment.caregiver_user_id == caregiver.id,
            CaregiverAssignment.patient_id == patient.id,
        )
    )
    if existing:
        return {"assigned": False, "detail": "Assignment already exists"}

    assignment = CaregiverAssignment(caregiver_user_id=caregiver.id, patient_id=patient.id)
    db.add(assignment)
    create_audit_log(
        db,
        actor_username=admin_user.username,
        action="CAREGIVER_ASSIGNED",
        target_type="PATIENT",
        target_id=str(patient.id),
        metadata={"caregiver_username": caregiver.username},
    )
    db.commit()
    return {"assigned": True, "caregiver_username": caregiver.username, "patient_id": patient.id}


@app.get("/v1/admin/caregiver-requests")
def list_caregiver_requests(
    db: Session = Depends(get_db), _: User = Depends(require_roles(UserRole.ADMIN))
):
    rows = db.scalars(
        select(CaregiverLinkRequest).order_by(CaregiverLinkRequest.created_at.desc()).limit(200)
    ).all()
    output = []
    for r in rows:
        caregiver = db.scalar(select(User).where(User.id == r.caregiver_user_id))
        patient = db.scalar(select(Patient).where(Patient.id == r.patient_id))
        output.append(
            {
                "id": r.id,
                "caregiver_user_id": r.caregiver_user_id,
                "caregiver_username": caregiver.username if caregiver else None,
                "patient_id": r.patient_id,
                "patient_name": patient.full_name if patient else None,
                "status": r.status,
                "notes": r.notes,
                "created_at": r.created_at.isoformat(),
                "reviewed_at": r.reviewed_at.isoformat() if r.reviewed_at else None,
            }
        )
    return output


@app.post("/v1/admin/caregiver-requests/{request_id}/approve")
def approve_caregiver_request(
    request_id: int,
    payload: CaregiverRequestReviewRequest,
    db: Session = Depends(get_db),
    admin_user: User = Depends(require_roles(UserRole.ADMIN)),
):
    request = db.scalar(select(CaregiverLinkRequest).where(CaregiverLinkRequest.id == request_id))
    if request is None:
        raise HTTPException(status_code=404, detail="Request not found")
    if request.status != CaregiverRequestStatus.PENDING.value:
        raise HTTPException(status_code=400, detail="Request already reviewed")
    existing = db.scalar(
        select(CaregiverAssignment).where(
            CaregiverAssignment.caregiver_user_id == request.caregiver_user_id,
            CaregiverAssignment.patient_id == request.patient_id,
        )
    )
    if existing is None:
        db.add(CaregiverAssignment(caregiver_user_id=request.caregiver_user_id, patient_id=request.patient_id))
    request.status = CaregiverRequestStatus.APPROVED.value
    request.reviewed_at = datetime.now(timezone.utc)
    request.notes = payload.notes or request.notes
    create_audit_log(
        db,
        actor_username=admin_user.username,
        action="CAREGIVER_REQUEST_APPROVED",
        target_type="CAREGIVER_REQUEST",
        target_id=str(request.id),
        metadata={"caregiver_user_id": request.caregiver_user_id, "patient_id": request.patient_id},
    )
    db.commit()
    return {"approved": True, "request_id": request.id}


@app.post("/v1/admin/caregiver-requests/{request_id}/reject")
def reject_caregiver_request(
    request_id: int,
    payload: CaregiverRequestReviewRequest,
    db: Session = Depends(get_db),
    admin_user: User = Depends(require_roles(UserRole.ADMIN)),
):
    request = db.scalar(select(CaregiverLinkRequest).where(CaregiverLinkRequest.id == request_id))
    if request is None:
        raise HTTPException(status_code=404, detail="Request not found")
    if request.status != CaregiverRequestStatus.PENDING.value:
        raise HTTPException(status_code=400, detail="Request already reviewed")
    request.status = CaregiverRequestStatus.REJECTED.value
    request.reviewed_at = datetime.now(timezone.utc)
    request.notes = payload.notes or request.notes
    create_audit_log(
        db,
        actor_username=admin_user.username,
        action="CAREGIVER_REQUEST_REJECTED",
        target_type="CAREGIVER_REQUEST",
        target_id=str(request.id),
        metadata={"caregiver_user_id": request.caregiver_user_id, "patient_id": request.patient_id},
    )
    db.commit()
    return {"rejected": True, "request_id": request.id}


@app.get("/v1/admin/patients")
def list_patients(db: Session = Depends(get_db), _: User = Depends(require_roles(UserRole.ADMIN, UserRole.DOCTOR))):
    rows = db.scalars(select(Patient).order_by(Patient.id.asc()).limit(500)).all()
    return [{"id": p.id, "full_name": p.full_name, "user_id": p.user_id} for p in rows]


@app.get("/v1/audit")
def list_audit(db: Session = Depends(get_db), _: User = Depends(require_roles(UserRole.ADMIN))):
    rows = db.execute(
        text(
            """
            SELECT id, actor_username, action, target_type, target_id, audit_metadata, created_at
            FROM audit_logs
            ORDER BY created_at DESC
            LIMIT 200
            """
        )
    ).mappings()
    return [dict(r) for r in rows]


@app.get("/v1/queue/health")
def queue_health(_: User = Depends(require_roles(UserRole.ADMIN, UserRole.DOCTOR))):
    rabbit_url = os.getenv("RABBITMQ_URL", "")
    queue_name = os.getenv("RABBITMQ_ALERT_QUEUE", "vitaltrack.alerts.created")
    if not rabbit_url:
        return {"connected": False, "configured": False, "queue": queue_name}

    try:
        conn = pika.BlockingConnection(pika.URLParameters(rabbit_url))
        ch = conn.channel()
        # Ensure queue exists; avoids false negatives on fresh startup.
        result = ch.queue_declare(queue=queue_name, durable=True)
        conn.close()
        return {
            "connected": True,
            "configured": True,
            "queue": queue_name,
            "messages": result.method.message_count,
            "consumers": result.method.consumer_count,
        }
    except Exception as exc:
        return {
            "connected": False,
            "configured": True,
            "queue": queue_name,
            "error": str(exc),
        }


@app.websocket("/ws/doctor")
async def doctor_ws(websocket: WebSocket):
    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=1008, reason="Missing token")
        return
    try:
        with Session(engine) as db:
            user = get_user_from_token(token, db)
            if user.role not in {UserRole.DOCTOR.value, UserRole.ADMIN.value}:
                await websocket.close(code=1008, reason="Insufficient role")
                return
    except Exception:
        await websocket.close(code=1008, reason="Invalid token")
        return
    await manager.connect(websocket)
    try:
        while True:
            _ = await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception:
        manager.disconnect(websocket)


