from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import threading
import time
from typing import Protocol

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from .auth import authenticate_user, create_access_token, get_password_hash, require_roles
from .db import Base, engine, get_db
from .messaging import RabbitMQBridge, build_event_envelope
from .models import (
    Alert,
    AlertStatus,
    CaregiverAssignment,
    CaregiverLinkRequest,
    CaregiverRequestStatus,
    OutboxEvent,
    OutboxStatus,
    Patient,
    User,
    UserRole,
    VitalReading,
)
from .schemas import RegisterRequest, RegisterResponse, VitalIngestRequest, VitalIngestResponse
from .services import create_audit_log
from .vital_observer import VitalObservationContext, build_default_vital_subject

STATIC_DIR = Path(__file__).resolve().parent / "static"
logger = logging.getLogger("vitaltrack.ingestion")

notification_bridge: RabbitMQBridge | None = None
prediction_bridge: RabbitMQBridge | None = None
outbox_stop_event = threading.Event()
outbox_thread: threading.Thread | None = None
vital_subject = build_default_vital_subject()


class VitalSourceAdapter(Protocol):
    """Adapter interface for source-specific payload normalization."""

    def adapt(self, payload: VitalIngestRequest) -> dict:
        ...


class GenericVitalSourceAdapter:
    def adapt(self, payload: VitalIngestRequest) -> dict:
        return {
            "patient_id": payload.patient_id,
            "heart_rate": payload.heart_rate,
            "spo2": payload.spo2,
            "bp_sys": payload.bp_sys,
            "bp_dia": payload.bp_dia,
            "respiratory_rate": payload.respiratory_rate,
            "temperature": payload.temperature,
            "source": payload.source.strip().lower(),
        }


class SimulatorVitalSourceAdapter(GenericVitalSourceAdapter):
    def adapt(self, payload: VitalIngestRequest) -> dict:
        normalized = super().adapt(payload)
        normalized["source"] = "simulator"
        return normalized


class VitalSourceAdapterFactory:
    @staticmethod
    def create(source: str) -> VitalSourceAdapter:
        if source.strip().lower() == "simulator":
            return SimulatorVitalSourceAdapter()
        return GenericVitalSourceAdapter()


def _publish_pending_outbox():
    notification_queue = os.getenv("RABBITMQ_NOTIFICATION_QUEUE", os.getenv("RABBITMQ_ALERT_QUEUE", "vitaltrack.alerts.created"))
    prediction_queue = os.getenv("RABBITMQ_VITAL_QUEUE", "vitaltrack.vitals.received")
    escalation_queue = os.getenv("RABBITMQ_ESCALATION_QUEUE", "vitaltrack.escalation.events")
    while not outbox_stop_event.is_set():
        with Session(engine) as db:
            rows = db.scalars(
                select(OutboxEvent)
                .where(OutboxEvent.status.in_([OutboxStatus.PENDING.value, OutboxStatus.FAILED.value]))
                .order_by(OutboxEvent.created_at.asc())
                .limit(100)
            ).all()
            if not rows:
                time.sleep(0.5)
                continue

            for event in rows:
                event.updated_at = datetime.now(timezone.utc)
                if notification_bridge is None or prediction_bridge is None:
                    event.status = OutboxStatus.FAILED.value
                    event.retry_count += 1
                    event.last_error = "RabbitMQ bridge unavailable"
                    continue
                if event.event_type == "VITAL_RECEIVED":
                    published = prediction_bridge.publish_event(event.payload, queue_name=prediction_queue)
                elif event.event_type == "ALERT_CREATED_NOTIFICATION":
                    published = notification_bridge.publish_event(event.payload, queue_name=notification_queue)
                elif event.event_type == "ALERT_CREATED_ESCALATION":
                    published = notification_bridge.publish_event(event.payload, queue_name=escalation_queue)
                else:
                    published = notification_bridge.publish_event(event.payload, queue_name=notification_queue)
                if published:
                    event.status = OutboxStatus.PUBLISHED.value
                    event.last_error = None
                    event.published_at = datetime.now(timezone.utc)
                else:
                    event.status = OutboxStatus.FAILED.value
                    event.retry_count += 1
                    event.last_error = "RabbitMQ publish failed"
            db.commit()
        time.sleep(0.2)


def start_outbox_worker():
    global outbox_thread
    if outbox_thread and outbox_thread.is_alive():
        return
    outbox_stop_event.clear()
    outbox_thread = threading.Thread(target=_publish_pending_outbox, daemon=True)
    outbox_thread.start()
    logger.info("Outbox publisher started")


def stop_outbox_worker():
    outbox_stop_event.set()
    if outbox_thread:
        outbox_thread.join(timeout=3)
    logger.info("Outbox publisher stopped")


@asynccontextmanager
async def lifespan(_: FastAPI):
    global notification_bridge, prediction_bridge
    Base.metadata.create_all(bind=engine)
    migrate_audit_metadata_to_jsonb()
    migrate_vital_columns()
    seed_basics()
    init_timescale()
    rabbit_url = os.getenv("RABBITMQ_URL", "")
    rabbit_queue = os.getenv("RABBITMQ_NOTIFICATION_QUEUE", os.getenv("RABBITMQ_ALERT_QUEUE", "vitaltrack.alerts.created"))
    if rabbit_url:
        notification_bridge = RabbitMQBridge(rabbit_url, rabbit_queue, lambda _: None)
        prediction_bridge = RabbitMQBridge(
            rabbit_url, os.getenv("RABBITMQ_VITAL_QUEUE", "vitaltrack.vitals.received"), lambda _: None
        )
    start_outbox_worker()
    yield
    stop_outbox_worker()


app = FastAPI(title="VitalTrack Ingestion Service", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def root():
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.post("/auth/token")
def token(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = authenticate_user(db, form_data.username, form_data.password)
    if not user:
        raise HTTPException(status_code=401, detail="Incorrect username or password")
    return {"access_token": create_access_token({"sub": user.username, "role": user.role}), "token_type": "bearer"}


@app.post("/auth/register", response_model=RegisterResponse)
def register(payload: RegisterRequest, db: Session = Depends(get_db)):
    try:
        role = UserRole(payload.role.upper())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid role") from exc

    if role not in {UserRole.PATIENT, UserRole.CAREGIVER, UserRole.DOCTOR}:
        raise HTTPException(status_code=400, detail="Self-registration supports PATIENT, DOCTOR, or CAREGIVER")

    existing = db.scalar(select(User).where(User.username == payload.username))
    if existing:
        raise HTTPException(status_code=409, detail="Username already exists")

    patient_id = None
    caregiver_request_id = None
    caregiver_request_status = None
    user = User(
        username=payload.username,
        password_hash=get_password_hash(payload.password),
        role=role.value,
    )
    db.add(user)
    db.flush()

    if role == UserRole.PATIENT:
        if not payload.full_name:
            raise HTTPException(status_code=400, detail="full_name is required for PATIENT role")
        patient = Patient(user_id=user.id, full_name=payload.full_name)
        db.add(patient)
        db.flush()
        patient_id = patient.id
    elif role == UserRole.CAREGIVER:
        if payload.patient_id is None:
            raise HTTPException(status_code=400, detail="patient_id is required for CAREGIVER role")
        patient = db.scalar(select(Patient).where(Patient.id == payload.patient_id))
        if patient is None:
            raise HTTPException(status_code=404, detail="Requested patient not found")
        request = CaregiverLinkRequest(
            caregiver_user_id=user.id,
            patient_id=patient.id,
            status=CaregiverRequestStatus.PENDING.value,
            notes="Requested during signup",
        )
        db.add(request)
        db.flush()
        caregiver_request_id = request.id
        caregiver_request_status = request.status

    db.commit()
    return RegisterResponse(
        user_id=user.id,
        username=user.username,
        role=user.role,
        patient_id=patient_id,
        caregiver_request_id=caregiver_request_id,
        caregiver_request_status=caregiver_request_status,
    )


@app.post("/v1/vitals", response_model=VitalIngestResponse)
def ingest_vitals(
    payload: VitalIngestRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles(UserRole.SIMULATOR, UserRole.DOCTOR, UserRole.ADMIN)),
):
    adapter = VitalSourceAdapterFactory.create(payload.source)
    normalized = adapter.adapt(payload)

    patient = db.scalar(select(Patient).where(Patient.id == payload.patient_id))
    if patient is None:
        raise HTTPException(status_code=404, detail="Patient not found")

    reading = VitalReading(
        patient_id=normalized["patient_id"],
        ts=datetime.now(timezone.utc),
        heart_rate=normalized["heart_rate"],
        spo2=normalized["spo2"],
        bp_sys=normalized["bp_sys"],
        bp_dia=normalized["bp_dia"],
        respiratory_rate=normalized["respiratory_rate"],
        temperature=normalized["temperature"],
        source=normalized["source"],
    )
    db.add(reading)
    db.flush()

    observation = VitalObservationContext(reading=reading)
    vital_subject.notify(observation)
    risk_score = observation.risk_score
    should_alert = observation.should_alert
    severity = observation.severity
    rule_code = observation.rule_code
    message = observation.message
    alert_created = False
    alert: Alert | None = None

    if should_alert and severity is not None:
        alert = Alert(
            patient_id=payload.patient_id,
            severity=severity.value,
            rule_code=rule_code,
            message=message,
            status=AlertStatus.OPEN.value,
        )
        db.add(alert)
        db.flush()
        alert_created = True
        alert_id = alert.id
        create_audit_log(
            db,
            actor_username=user.username,
            action="ALERT_CREATED",
            target_type="ALERT",
            target_id=str(alert.id),
            metadata={"severity": alert.severity, "rule_code": alert.rule_code},
        )

    create_audit_log(
        db,
        actor_username=user.username,
        action="VITAL_INGESTED",
        target_type="PATIENT",
        target_id=str(payload.patient_id),
        metadata={"reading_id": reading.id, "risk_score": risk_score},
    )
    # Transactional outbox: enqueue events in same commit as domain writes.
    db.add(
        OutboxEvent(
            event_type="VITAL_RECEIVED",
            payload=build_event_envelope(
                "VITAL_RECEIVED",
                {
                    "reading_id": reading.id,
                    "patient_id": normalized["patient_id"],
                    "heart_rate": normalized["heart_rate"],
                    "spo2": normalized["spo2"],
                    "bp_sys": normalized["bp_sys"],
                    "bp_dia": normalized["bp_dia"],
                    "respiratory_rate": normalized["respiratory_rate"],
                    "temperature": normalized["temperature"],
                    "source": normalized["source"],
                    "risk_score": risk_score,
                },
            ),
            status=OutboxStatus.PENDING.value,
        )
    )
    create_audit_log(
        db,
        actor_username=user.username,
        action="VITAL_EVENT_ENQUEUED",
        target_type="OUTBOX",
        target_id=str(reading.id),
        metadata={"event_type": "VITAL_RECEIVED"},
    )
    if alert_created and alert is not None:
        db.add(
            OutboxEvent(
                event_type="ALERT_CREATED_NOTIFICATION",
                payload=build_event_envelope("ALERT_CREATED", {"alert_id": alert_id}),
                status=OutboxStatus.PENDING.value,
            )
        )
        db.add(
            OutboxEvent(
                event_type="ALERT_CREATED_ESCALATION",
                payload=build_event_envelope("ALERT_CREATED", {"alert_id": alert_id}),
                status=OutboxStatus.PENDING.value,
            )
        )
        create_audit_log(
            db,
            actor_username=user.username,
            action="ALERT_EVENT_ENQUEUED",
            target_type="OUTBOX",
            target_id=str(alert_id),
            metadata={"event_type": "ALERT_CREATED", "targets": ["NOTIFICATION", "ESCALATION"]},
        )
    db.commit()

    return VitalIngestResponse(
        reading_id=reading.id,
        alert_created=alert_created,
        risk_score=risk_score,
        severity=severity.value if severity else None,
    )


def init_timescale():
    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS timescaledb;"))
            conn.execute(
                text(
                    """
                    SELECT create_hypertable('vital_readings', 'ts', if_not_exists => TRUE);
                    """
                )
            )
            conn.execute(
                text(
                    """
                    CREATE INDEX IF NOT EXISTS idx_vitals_patient_ts
                    ON vital_readings (patient_id, ts DESC);
                    """
                )
            )
    except SQLAlchemyError:
        pass


def migrate_audit_metadata_to_jsonb():
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                DO $$
                BEGIN
                    IF EXISTS (
                        SELECT 1
                        FROM information_schema.columns
                        WHERE table_name = 'audit_logs'
                          AND column_name = 'audit_metadata'
                          AND udt_name <> 'jsonb'
                    ) THEN
                        ALTER TABLE audit_logs
                        ALTER COLUMN audit_metadata
                        TYPE jsonb
                        USING audit_metadata::jsonb;
                    END IF;
                END $$;
                """
            )
        )


def migrate_vital_columns():
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                ALTER TABLE vital_readings
                ADD COLUMN IF NOT EXISTS respiratory_rate INTEGER NOT NULL DEFAULT 16;
                """
            )
        )


def seed_basics():
    with Session(engine) as db:
        seed_users = [
            ("admin", "admin123", UserRole.ADMIN.value),
            ("doctor1", "doctor123", UserRole.DOCTOR.value),
            ("doctor2", "doctor223", UserRole.DOCTOR.value),
            ("patient1", "patient123", UserRole.PATIENT.value),
            ("patient2", "patient223", UserRole.PATIENT.value),
            ("caregiver1", "care123", UserRole.CAREGIVER.value),
            ("caregiver2", "care223", UserRole.CAREGIVER.value),
            ("caregiver3", "care323", UserRole.CAREGIVER.value),
            ("caregiver4", "care423", UserRole.CAREGIVER.value),
            ("simulator", "sim123", UserRole.SIMULATOR.value),
        ]

        user_by_name: dict[str, User] = {}
        for username, password, role in seed_users:
            user = db.scalar(select(User).where(User.username == username))
            if user is None:
                user = User(username=username, password_hash=get_password_hash(password), role=role)
                db.add(user)
                db.flush()
            user_by_name[username] = user

        patient_specs = {
            "patient1": "John Patient",
            "patient2": "Jane Patient",
        }
        patient_by_username: dict[str, Patient] = {}
        for patient_username, full_name in patient_specs.items():
            patient_user = user_by_name[patient_username]
            patient = db.scalar(select(Patient).where(Patient.user_id == patient_user.id))
            if patient is None:
                patient = Patient(user_id=patient_user.id, full_name=full_name)
                db.add(patient)
                db.flush()
            patient_by_username[patient_username] = patient

        caregiver_assignments = {
            "patient1": ["caregiver1", "caregiver2"],
            "patient2": ["caregiver3", "caregiver4"],
        }
        for patient_username, caregiver_usernames in caregiver_assignments.items():
            patient = patient_by_username[patient_username]
            for caregiver_username in caregiver_usernames:
                caregiver_user = user_by_name[caregiver_username]
                assignment = db.scalar(
                    select(CaregiverAssignment).where(
                        CaregiverAssignment.caregiver_user_id == caregiver_user.id,
                        CaregiverAssignment.patient_id == patient.id,
                    )
                )
                if assignment is None:
                    db.add(CaregiverAssignment(caregiver_user_id=caregiver_user.id, patient_id=patient.id))
        db.commit()
