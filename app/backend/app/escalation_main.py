from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import os
import threading
import time

from fastapi import FastAPI
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .db import engine
from .messaging import RabbitMQBridge, parse_event_envelope
from .models import Alert, AlertSeverity, AlertStatus, CaregiverAssignment, EscalationCase, Notification, User, UserRole
from .services import create_audit_log


RECIPIENT_CHAIN = ["PRIMARY_DOCTOR", "ON_CALL_DOCTOR", "CAREGIVER_OR_FAMILY", "ADMIN"]

rabbit_bridge: RabbitMQBridge | None = None
worker_stop_event = threading.Event()
worker_thread: threading.Thread | None = None


def _escalation_interval_seconds() -> int:
    try:
        return max(30, int(os.getenv("ESCALATION_INTERVAL_SECONDS", "120")))
    except ValueError:
        return 120


def _handle_alert_created(payload: dict):
    event_type, event_data = parse_event_envelope(payload)
    if event_type is not None and event_type != "ALERT_CREATED":
        return
    alert_id = event_data.get("alert_id")
    if alert_id is None:
        return
    with Session(engine) as db:
        alert = db.scalar(select(Alert).where(Alert.id == int(alert_id)))
        if alert is None or alert.severity != AlertSeverity.CRITICAL.value:
            return
        existing = db.scalar(select(EscalationCase).where(EscalationCase.alert_id == alert.id))
        if existing is not None:
            return
        now = datetime.now(timezone.utc)
        case = EscalationCase(
            alert_id=alert.id,
            status="ACTIVE",
            step_index=0,
            next_due_at=now + timedelta(seconds=_escalation_interval_seconds()),
            created_at=now,
            updated_at=now,
        )
        db.add(case)
        create_audit_log(
            db,
            actor_username="escalation-mediator",
            action="ESCALATION_CASE_STARTED",
            target_type="ALERT",
            target_id=str(alert.id),
            metadata={"severity": alert.severity, "interval_seconds": _escalation_interval_seconds()},
        )
        db.commit()


def _run_escalation_worker():
    while not worker_stop_event.is_set():
        with Session(engine) as db:
            now = datetime.now(timezone.utc)
            cases = db.scalars(
                select(EscalationCase)
                .where(EscalationCase.status == "ACTIVE", EscalationCase.next_due_at <= now)
                .order_by(EscalationCase.next_due_at.asc())
                .limit(50)
            ).all()
            for case in cases:
                alert = db.scalar(select(Alert).where(Alert.id == case.alert_id))
                if alert is None:
                    case.status = "CANCELLED"
                    case.updated_at = now
                    continue
                if alert.status in {AlertStatus.ACKNOWLEDGED.value, AlertStatus.RESOLVED.value}:
                    case.status = "COMPLETED"
                    case.updated_at = now
                    create_audit_log(
                        db,
                        actor_username="escalation-mediator",
                        action="ESCALATION_COMPLETED_ON_ACK",
                        target_type="ALERT",
                        target_id=str(alert.id),
                        metadata={"ack_status": alert.status},
                    )
                    continue
                if case.step_index >= len(RECIPIENT_CHAIN):
                    case.status = "EXHAUSTED"
                    case.updated_at = now
                    create_audit_log(
                        db,
                        actor_username="escalation-mediator",
                        action="ESCALATION_EXHAUSTED",
                        target_type="ALERT",
                        target_id=str(alert.id),
                        metadata={"steps": len(RECIPIENT_CHAIN)},
                    )
                    continue

                recipient_role = RECIPIENT_CHAIN[case.step_index]
                recipient_value = _resolve_recipient(db, alert.patient_id, recipient_role)
                db.add(
                    Notification(
                        alert_id=alert.id,
                        channel="IN_APP_ESCALATION",
                        recipient=recipient_value,
                        status="SENT",
                        details=f"Escalation step {case.step_index + 1} for critical alert {alert.id}",
                    )
                )
                case.last_recipient_role = recipient_role
                case.step_index += 1
                case.next_due_at = now + timedelta(seconds=_escalation_interval_seconds())
                case.updated_at = now
                create_audit_log(
                    db,
                    actor_username="escalation-mediator",
                    action="ESCALATION_STEP_DISPATCHED",
                    target_type="ALERT",
                    target_id=str(alert.id),
                    metadata={"step_index": case.step_index, "recipient_role": recipient_role, "recipient": recipient_value},
                )
            db.commit()
        time.sleep(2)


def _resolve_recipient(db: Session, patient_id: int, recipient_role: str) -> str:
    doctors = db.scalars(select(User).where(User.role == UserRole.DOCTOR.value).order_by(User.id.asc())).all()
    admins = db.scalars(select(User).where(User.role == UserRole.ADMIN.value).order_by(User.id.asc())).all()
    caregivers = db.scalars(
        select(User)
        .join(CaregiverAssignment, CaregiverAssignment.caregiver_user_id == User.id)
        .where(CaregiverAssignment.patient_id == patient_id, User.role == UserRole.CAREGIVER.value)
    ).all()

    if recipient_role == "PRIMARY_DOCTOR":
        return doctors[0].username if doctors else recipient_role
    if recipient_role == "ON_CALL_DOCTOR":
        if len(doctors) >= 2:
            return doctors[1].username
        return doctors[0].username if doctors else recipient_role
    if recipient_role == "CAREGIVER_OR_FAMILY":
        if caregivers:
            return ",".join(sorted({c.username for c in caregivers}))
        return recipient_role
    if recipient_role == "ADMIN":
        return admins[0].username if admins else recipient_role
    return recipient_role


def start_escalation_worker():
    global worker_thread
    if worker_thread and worker_thread.is_alive():
        return
    worker_stop_event.clear()
    worker_thread = threading.Thread(target=_run_escalation_worker, daemon=True)
    worker_thread.start()


def stop_escalation_worker():
    worker_stop_event.set()
    if worker_thread:
        worker_thread.join(timeout=3)


def ensure_escalation_tables():
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS escalation_cases (
                    id SERIAL PRIMARY KEY,
                    alert_id INTEGER NOT NULL UNIQUE REFERENCES alerts(id),
                    status VARCHAR(30) NOT NULL DEFAULT 'ACTIVE',
                    step_index INTEGER NOT NULL DEFAULT 0,
                    next_due_at TIMESTAMP NOT NULL,
                    last_recipient_role VARCHAR(40) NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS idx_escalation_cases_status_due
                ON escalation_cases (status, next_due_at);
                """
            )
        )


@asynccontextmanager
async def lifespan(_: FastAPI):
    global rabbit_bridge
    ensure_escalation_tables()
    rabbit_url = os.getenv("RABBITMQ_URL", "")
    escalation_queue = os.getenv("RABBITMQ_ESCALATION_QUEUE", "vitaltrack.escalation.events")
    if rabbit_url:
        rabbit_bridge = RabbitMQBridge(rabbit_url, escalation_queue, _handle_alert_created)
        rabbit_bridge.start_consumer()
    start_escalation_worker()
    yield
    stop_escalation_worker()
    if rabbit_bridge is not None:
        rabbit_bridge.stop_consumer()


app = FastAPI(title="VitalTrack Escalation Mediator Service", lifespan=lifespan)


@app.get("/v1/escalation/health")
def escalation_health():
    return {"status": "ok", "service": "escalation-mediator"}
