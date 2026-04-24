from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
import logging
import os
import time
from typing import Protocol
from urllib import request as urllib_request

from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from openai import OpenAI
import pika
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from .auth import get_password_hash, get_user_from_token, require_roles
from .db import engine, get_db
from .messaging import RabbitMQBridge, parse_event_envelope
from .models import (
    Alert,
    AlertSeverity,
    AlertStatus,
    CaregiverAssignment,
    CaregiverLinkRequest,
    CaregiverRequestStatus,
    FailedEvent,
    FailedEventStatus,
    Notification,
    OutboxEvent,
    OutboxStatus,
    Patient,
    PredictionRecord,
    User,
    UserRole,
    VitalReading,
)
from .schemas import (
    AckResponse,
    AdminCreateUserRequest,
    ChatbotMessageRequest,
    ChatbotMessageResponse,
    CaregiverAssignmentRequest,
    CaregiverDashboardResponse,
    CaregiverRequestReviewRequest,
    DashboardResponse,
    FailedRetryResponse,
    PatientStatsResponse,
    PatientPortalResponse,
    RegisterResponse,
    SystemMetricsResponse,
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
    global rabbit_bridge, main_loop, openai_client
    main_loop = asyncio.get_running_loop()
    if OPENAI_CHATBOT_ENABLED:
        api_key = os.getenv("OPENAI_API_KEY")
        if api_key:
            openai_client = OpenAI(api_key=api_key)
        else:
            logger.warning("OpenAI chatbot is enabled by default but OPENAI_API_KEY is missing; using local fallback.")
    if GEMINI_CHATBOT_ENABLED and not os.getenv("GEMINI_API_KEY"):
        logger.warning("Gemini chatbot strategy is enabled but GEMINI_API_KEY is missing; strategy will be skipped.")
    rabbit_url = os.getenv("RABBITMQ_URL", "")
    rabbit_queue = os.getenv("RABBITMQ_NOTIFICATION_QUEUE", os.getenv("RABBITMQ_ALERT_QUEUE", "vitaltrack.alerts.created"))
    if rabbit_url:
        rabbit_bridge = RabbitMQBridge(rabbit_url, rabbit_queue, handle_alert_created_event)
        rabbit_bridge.start_consumer()
    yield
    if rabbit_bridge is not None:
        rabbit_bridge.stop_consumer()


app = FastAPI(title="VitalTrack Notification Service", lifespan=lifespan)
logger = logging.getLogger("vitaltrack.notification")
OPENAI_CHATBOT_ENABLED = os.getenv("OPENAI_CHATBOT_ENABLED", "true").lower() not in {"0", "false", "no"}
OPENAI_CHATBOT_MODEL = os.getenv("OPENAI_CHATBOT_MODEL", "gpt-4.1-mini")
GEMINI_CHATBOT_ENABLED = os.getenv("GEMINI_CHATBOT_ENABLED", "true").lower() not in {"0", "false", "no"}
GEMINI_CHATBOT_MODEL = os.getenv("GEMINI_CHATBOT_MODEL", "gemini-2.0-flash")
openai_client: OpenAI | None = None


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


def _chatbot_triage(message: str) -> tuple[str, str]:
    text = message.lower()
    high_keywords = [
        "chest pain",
        "shortness of breath",
        "can't breathe",
        "unconscious",
        "severe bleeding",
        "stroke",
        "heart attack",
    ]
    medium_keywords = ["dizzy", "fever", "vomit", "faint", "palpitations", "headache"]
    if any(k in text for k in high_keywords):
        return (
            "CRITICAL",
            "This may indicate an emergency. Contact emergency services immediately and notify your doctor now.",
        )
    if any(k in text for k in medium_keywords):
        return (
            "MEDIUM",
            "Your symptoms may need prompt clinician review. Please contact your care team soon and monitor vitals.",
        )
    return (
        "LOW",
        "Based on your message, this appears non-urgent. Continue routine monitoring and reach out if symptoms worsen.",
    )


def _build_vitals_hint(latest_vital: VitalReading | None) -> str:
    vitals_hint = (
        "No recent vitals available."
        if latest_vital is None
        else (
            f"Latest vitals: HR={latest_vital.heart_rate}, SpO2={latest_vital.spo2}, "
            f"BP={latest_vital.bp_sys}/{latest_vital.bp_dia}, RR={latest_vital.respiratory_rate}, Temp={latest_vital.temperature}."
        )
    )
    return vitals_hint


def _build_triage_system_prompt() -> str:
    return (
        "You are a non-diagnostic health triage assistant for remote monitoring. "
        "Return ONLY JSON with keys risk_level and reply. "
        "risk_level must be one of LOW, MEDIUM, CRITICAL. "
        "reply should be concise, safe, and advisory; never definitive diagnosis."
    )


class TriageResponseAdapter(Protocol):
    def adapt(self, raw_text: str) -> tuple[str, str]:
        ...


class JsonTriageResponseAdapter:
    def adapt(self, raw_text: str) -> tuple[str, str]:
        parsed = json.loads(raw_text.strip())
        risk_level = str(parsed.get("risk_level", "")).upper()
        reply = str(parsed.get("reply", "")).strip()
        if risk_level not in {"LOW", "MEDIUM", "CRITICAL"} or not reply:
            raise ValueError("Invalid triage JSON payload")
        return risk_level, reply


class ChatbotTriageStrategy(Protocol):
    name: str

    def triage(self, message: str, latest_vital: VitalReading | None) -> tuple[str, str]:
        ...


class OpenAITriageStrategy:
    name = "openai"

    def __init__(self, adapter: TriageResponseAdapter):
        self.adapter = adapter

    def triage(self, message: str, latest_vital: VitalReading | None) -> tuple[str, str]:
        if not OPENAI_CHATBOT_ENABLED or openai_client is None:
            raise RuntimeError("OpenAI triage unavailable")
        system_prompt = _build_triage_system_prompt()
        user_prompt = f"User message: {message}\n{_build_vitals_hint(latest_vital)}"
        response = openai_client.responses.create(
            model=OPENAI_CHATBOT_MODEL,
            input=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.1,
            max_output_tokens=220,
        )
        content = (response.output_text or "").strip()
        return self.adapter.adapt(content)


class GeminiTriageStrategy:
    name = "gemini"

    def __init__(self, adapter: TriageResponseAdapter):
        self.adapter = adapter

    def triage(self, message: str, latest_vital: VitalReading | None) -> tuple[str, str]:
        if not GEMINI_CHATBOT_ENABLED:
            raise RuntimeError("Gemini triage disabled")
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("Gemini triage unavailable")
        system_prompt = _build_triage_system_prompt()
        user_prompt = f"User message: {message}\n{_build_vitals_hint(latest_vital)}"
        endpoint = (
            f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_CHATBOT_MODEL}:generateContent"
            f"?key={api_key}"
        )
        body = {
            "contents": [{"parts": [{"text": f"{system_prompt}\n\n{user_prompt}"}]}],
            "generationConfig": {"temperature": 0.1, "maxOutputTokens": 220},
        }
        req = urllib_request.Request(
            endpoint,
            method="POST",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib_request.urlopen(req, timeout=20) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        text_parts = payload["candidates"][0]["content"]["parts"]
        raw_text = " ".join(str(part.get("text", "")) for part in text_parts).strip()
        if not raw_text:
            raise ValueError("Gemini returned empty response")
        return self.adapter.adapt(raw_text)


class LocalRulesTriageStrategy:
    name = "local_rules"

    def triage(self, message: str, latest_vital: VitalReading | None) -> tuple[str, str]:
        del latest_vital
        return _chatbot_triage(message)


class ChatbotTriageFactory:
    @staticmethod
    def create_response_adapter() -> TriageResponseAdapter:
        return JsonTriageResponseAdapter()

    @staticmethod
    def create_strategies() -> list[ChatbotTriageStrategy]:
        adapter = ChatbotTriageFactory.create_response_adapter()
        return [
            OpenAITriageStrategy(adapter),
            GeminiTriageStrategy(adapter),
            LocalRulesTriageStrategy(),
        ]


def handle_alert_created_event(payload: dict):
    event_type, event_data = parse_event_envelope(payload)
    if event_type == "RISK_PREDICTED":
        handle_risk_predicted_event(event_data)
        return
    if event_type is not None and event_type != "ALERT_CREATED":
        logger.warning("Ignoring unsupported event_type on notifications queue: %s", event_type)
        return
    alert_id = event_data.get("alert_id")
    if not alert_id:
        logger.warning("Dropping alert event without alert_id")
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


def handle_risk_predicted_event(payload: dict):
    patient_id = payload.get("patient_id")
    severity = str(payload.get("combined_severity", "")).upper()
    if patient_id is None or severity not in {"HIGH", "CRITICAL"}:
        return
    ws_event_payload: dict | None = None
    with Session(engine) as db:
        # Basic deduplication for repeated prediction events in short windows.
        recent_open = db.scalar(
            select(Alert)
            .where(
                Alert.patient_id == int(patient_id),
                Alert.rule_code == "RISK_PREDICTED",
                Alert.status == AlertStatus.OPEN.value,
            )
            .order_by(Alert.created_at.desc())
            .limit(1)
        )
        if recent_open is not None:
            return
        alert = Alert(
            patient_id=int(patient_id),
            severity=severity,
            rule_code="RISK_PREDICTED",
            message=(
                f"Prediction severity={severity}, "
                f"EWS={payload.get('ews_score')}, baseline_z={payload.get('baseline_max_z')}"
            ),
            status=AlertStatus.OPEN.value,
        )
        db.add(alert)
        db.flush()
        notify_doctors_or_capture_failure(db, alert)
        ws_event_payload = serialize_alert_event(alert)
        create_audit_log(
            db,
            actor_username="prediction-consumer",
            action="PREDICTION_ALERT_CREATED",
            target_type="ALERT",
            target_id=str(alert.id),
            metadata={
                "patient_id": int(patient_id),
                "combined_severity": severity,
                "strategy_versions": payload.get("strategy_versions"),
            },
        )
        db.commit()
    if main_loop is not None and ws_event_payload is not None:
        fut = asyncio.run_coroutine_threadsafe(manager.broadcast(ws_event_payload), main_loop)
        fut.add_done_callback(lambda done_fut: done_fut.exception())


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
            "respiratory_rate": r.respiratory_rate,
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


@app.get("/v1/patients/{patient_id}/predictions")
def patient_predictions(
    patient_id: int,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.DOCTOR, UserRole.PATIENT, UserRole.CAREGIVER)),
):
    if not _can_access_patient(db, current_user, patient_id):
        raise HTTPException(status_code=403, detail="No access to requested patient")
    safe_limit = max(1, min(limit, 300))
    rows = db.scalars(
        select(PredictionRecord)
        .where(PredictionRecord.patient_id == patient_id)
        .order_by(PredictionRecord.predicted_at.desc())
        .limit(safe_limit)
    ).all()
    return [
        {
            "id": p.id,
            "patient_id": p.patient_id,
            "reading_id": p.reading_id,
            "ews_score": p.ews_score,
            "ews_severity": p.ews_severity,
            "baseline_max_z": p.baseline_max_z,
            "baseline_severity": p.baseline_severity,
            "combined_severity": p.combined_severity,
            "strategy_versions": p.strategy_versions,
            "factors": p.factors,
            "predicted_at": p.predicted_at.isoformat(),
        }
        for p in rows
    ]


@app.get("/v1/notifications")
def list_notifications(
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.DOCTOR, UserRole.PATIENT, UserRole.CAREGIVER)),
):
    safe_limit = max(1, min(limit, 300))
    base_query = select(Notification).order_by(Notification.created_at.desc()).limit(safe_limit)

    if current_user.role == UserRole.DOCTOR.value:
        rows = db.scalars(base_query.where(Notification.recipient == current_user.username)).all()
    elif current_user.role == UserRole.ADMIN.value:
        rows = db.scalars(base_query).all()
    else:
        # For patient/caregiver, map through accessible patient alerts.
        if current_user.role == UserRole.PATIENT.value:
            patient = db.scalar(select(Patient).where(Patient.user_id == current_user.id))
            patient_ids = [patient.id] if patient else []
        else:
            assignments = db.scalars(
                select(CaregiverAssignment).where(CaregiverAssignment.caregiver_user_id == current_user.id)
            ).all()
            patient_ids = [a.patient_id for a in assignments]

        if not patient_ids:
            return []
        alert_ids = db.scalars(select(Alert.id).where(Alert.patient_id.in_(patient_ids))).all()
        if not alert_ids:
            return []
        rows = db.scalars(base_query.where(Notification.alert_id.in_(alert_ids))).all()

    return [
        {
            "id": n.id,
            "alert_id": n.alert_id,
            "channel": n.channel,
            "recipient": n.recipient,
            "status": n.status,
            "details": n.details,
            "created_at": n.created_at.isoformat(),
        }
        for n in rows
    ]


@app.post("/v1/chatbot/message", response_model=ChatbotMessageResponse)
async def chatbot_message(
    payload: ChatbotMessageRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.DOCTOR, UserRole.PATIENT, UserRole.CAREGIVER)),
):
    risk_level = "LOW"
    escalated = False
    alert_id: int | None = None
    reply = ""
    strategy_used = "none"

    if payload.patient_id is not None and not _can_access_patient(db, current_user, payload.patient_id):
        raise HTTPException(status_code=403, detail="No access to requested patient")

    latest_vital = None
    if payload.patient_id is not None:
        latest_vital = db.scalar(
            select(VitalReading)
            .where(VitalReading.patient_id == payload.patient_id)
            .order_by(VitalReading.ts.desc())
            .limit(1)
        )
    for strategy in ChatbotTriageFactory.create_strategies():
        try:
            risk_level, reply = strategy.triage(payload.message, latest_vital)
            strategy_used = strategy.name
            break
        except Exception as exc:
            logger.warning("%s triage failed, trying next strategy: %s", strategy.name, exc)

    if latest_vital is not None and (latest_vital.spo2 < 90 or latest_vital.heart_rate > 130):
        risk_level = "CRITICAL"
        reply = "Latest vitals are concerning (low oxygen or very high heart rate). Please contact emergency services and your doctor immediately."

    create_audit_log(
        db,
        actor_username=current_user.username,
        action="CHATBOT_MESSAGE",
        target_type="PATIENT" if payload.patient_id is not None else "CHATBOT",
        target_id=str(payload.patient_id) if payload.patient_id is not None else "N/A",
        metadata={"risk_level": risk_level, "advisory_only": True, "triage_strategy": strategy_used},
    )
    db.commit()

    return ChatbotMessageResponse(
        reply=reply,
        risk_level=risk_level,
        strategy_used=strategy_used,
        escalated=escalated,
        alert_id=alert_id,
    )


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
            "respiratory_rate": v.respiratory_rate,
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
def list_patients(
    db: Session = Depends(get_db), _: User = Depends(require_roles(UserRole.ADMIN, UserRole.DOCTOR, UserRole.SIMULATOR))
):
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


def _probe_queue_health() -> dict:
    rabbit_url = os.getenv("RABBITMQ_URL", "")
    queue_name = os.getenv("RABBITMQ_NOTIFICATION_QUEUE", os.getenv("RABBITMQ_ALERT_QUEUE", "vitaltrack.alerts.created"))
    dlq_name = f"{queue_name}.dlq"
    if not rabbit_url:
        return {"connected": False, "configured": False, "queue": queue_name, "dlq": dlq_name}

    try:
        conn = pika.BlockingConnection(pika.URLParameters(rabbit_url))
        ch = conn.channel()
        # Passive declarations avoid mutating queue arguments and precondition conflicts.
        result = ch.queue_declare(queue=queue_name, passive=True)
        dlq_result = ch.queue_declare(queue=dlq_name, passive=True)
        conn.close()
        return {
            "connected": True,
            "configured": True,
            "queue": queue_name,
            "messages": result.method.message_count,
            "consumers": result.method.consumer_count,
            "dlq": dlq_name,
            "dlq_messages": dlq_result.method.message_count,
        }
    except Exception as exc:
        return {
            "connected": False,
            "configured": True,
            "queue": queue_name,
            "error": str(exc),
        }


@app.get("/v1/queue/health")
def queue_health(_: User = Depends(require_roles(UserRole.ADMIN, UserRole.DOCTOR))):
    return _probe_queue_health()


@app.get("/v1/health")
def service_health():
    return {"status": "ok", "service": "notification"}


@app.get("/v1/system/metrics", response_model=SystemMetricsResponse)
def system_metrics(
    db: Session = Depends(get_db), _: User = Depends(require_roles(UserRole.ADMIN, UserRole.SIMULATOR))
):
    now = datetime.now(timezone.utc)
    one_minute_ago = now.timestamp() - 60
    fifteen_minutes_ago = now.timestamp() - (15 * 60)
    one_hour_ago = now.timestamp() - (60 * 60)

    ingestion_last_minute = db.scalar(
        select(func.count(VitalReading.id)).where(func.extract("epoch", VitalReading.ts) >= one_minute_ago)
    ) or 0
    active_patients_15m = db.scalar(
        select(func.count(func.distinct(VitalReading.patient_id))).where(
            func.extract("epoch", VitalReading.ts) >= fifteen_minutes_ago
        )
    ) or 0

    open_alerts = db.scalar(select(func.count(Alert.id)).where(Alert.status == AlertStatus.OPEN.value)) or 0
    total_alerts = db.scalar(select(func.count(Alert.id))) or 0

    outbox_pending = db.scalar(
        select(func.count(OutboxEvent.id)).where(OutboxEvent.status == OutboxStatus.PENDING.value)
    ) or 0
    outbox_failed = db.scalar(
        select(func.count(OutboxEvent.id)).where(OutboxEvent.status == OutboxStatus.FAILED.value)
    ) or 0
    outbox_published = db.scalar(
        select(func.count(OutboxEvent.id)).where(OutboxEvent.status == OutboxStatus.PUBLISHED.value)
    ) or 0

    failed_pending = db.scalar(
        select(func.count(FailedEvent.id)).where(FailedEvent.status == FailedEventStatus.PENDING.value)
    ) or 0
    failed_exhausted = db.scalar(
        select(func.count(FailedEvent.id)).where(FailedEvent.status == FailedEventStatus.EXHAUSTED.value)
    ) or 0

    latency_row = db.execute(
        text(
            """
            SELECT
              percentile_cont(0.5) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (published_at - created_at))) AS median_s,
              percentile_cont(0.95) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (published_at - created_at))) AS p95_s
            FROM outbox_events
            WHERE published_at IS NOT NULL
              AND EXTRACT(EPOCH FROM created_at) >= :cutoff
            """
        ),
        {"cutoff": one_hour_ago},
    ).mappings().first()

    queue_status = _probe_queue_health()

    return SystemMetricsResponse(
        generated_at=now,
        ingestion={
            "readings_last_minute": int(ingestion_last_minute),
            "readings_per_second_last_minute": round(float(ingestion_last_minute) / 60.0, 3),
            "active_patients_last_15m": int(active_patients_15m),
        },
        alerts={"open": int(open_alerts), "total": int(total_alerts)},
        outbox={
            "pending": int(outbox_pending),
            "failed": int(outbox_failed),
            "published": int(outbox_published),
            "publish_latency_median_s_last_hour": round(float(latency_row["median_s"]), 3)
            if latency_row and latency_row["median_s"] is not None
            else None,
            "publish_latency_p95_s_last_hour": round(float(latency_row["p95_s"]), 3)
            if latency_row and latency_row["p95_s"] is not None
            else None,
        },
        failed_events={"pending": int(failed_pending), "exhausted": int(failed_exhausted)},
        queue=queue_status,
    )


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


