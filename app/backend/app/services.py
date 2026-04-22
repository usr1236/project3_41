from __future__ import annotations

from datetime import datetime, timezone
import json
import random

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (
    Alert,
    AlertSeverity,
    AlertStatus,
    AuditLog,
    FailedEvent,
    FailedEventStatus,
    Notification,
    User,
    UserRole,
    VitalReading,
)


def calculate_risk_score(reading: VitalReading) -> float:
    score = 0.0
    if reading.spo2 < 92:
        score += (92 - reading.spo2) * 2.0
    if reading.heart_rate > 110:
        score += (reading.heart_rate - 110) * 0.8
    if reading.temperature > 37.8:
        score += (reading.temperature - 37.8) * 8.0
    if reading.bp_sys > 150:
        score += (reading.bp_sys - 150) * 0.2
    return round(min(score, 100.0), 2)


def evaluate_alert(reading: VitalReading, risk_score: float) -> tuple[bool, AlertSeverity | None, str, str]:
    if reading.spo2 < 88 or reading.heart_rate > 130:
        return True, AlertSeverity.CRITICAL, "CRIT_VITALS", "Critical vital signs detected"
    if risk_score >= 20:
        return True, AlertSeverity.HIGH, "HIGH_RISK_SCORE", "High short-term risk predicted"
    if reading.spo2 < 92 or reading.temperature > 38.5:
        return True, AlertSeverity.MEDIUM, "ABNORMAL_TREND", "Abnormal trend detected"
    return False, None, "NORMAL", "No alert"


def create_audit_log(
    db: Session, actor_username: str, action: str, target_type: str, target_id: str, metadata: dict
):
    db.add(
        AuditLog(
            actor_username=actor_username,
            action=action,
            target_type=target_type,
            target_id=target_id,
            audit_metadata=metadata,
        )
    )


def _dispatch_notification(db: Session, alert: Alert, recipient: str) -> tuple[bool, str]:
    # Simulate occasional channel failures for failed-event handling demonstration.
    # Set recipient to include "fail" for deterministic failure.
    fails = "fail" in recipient or random.random() < 0.1
    if fails:
        return False, "Notification provider timeout"

    db.add(
        Notification(
            alert_id=alert.id,
            channel="in_app",
            recipient=recipient,
            status="SENT",
            details=f"Alert {alert.id} ({alert.severity}): {alert.message}",
        )
    )
    return True, "sent"


def notify_doctors_or_capture_failure(db: Session, alert: Alert):
    doctors = db.scalars(select(User).where(User.role == UserRole.DOCTOR.value)).all()
    if not doctors:
        return

    for doctor in doctors:
        ok, details = _dispatch_notification(db, alert, doctor.username)
        if ok:
            continue

        payload = {
            "alert_id": alert.id,
            "recipient": doctor.username,
            "channel": "in_app",
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        db.add(
            FailedEvent(
                event_type="NOTIFICATION_FAILED",
                payload=json.dumps(payload),
                error=details,
                retry_count=0,
                status=FailedEventStatus.PENDING.value,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
        )


def retry_failed_events(db: Session) -> tuple[int, int, int, int]:
    pending_events = db.scalars(
        select(FailedEvent).where(FailedEvent.status == FailedEventStatus.PENDING.value)
    ).all()

    retried = 0
    resolved = 0
    pending = 0
    exhausted = 0

    for event in pending_events:
        retried += 1
        event.retry_count += 1
        event.updated_at = datetime.now(timezone.utc)

        payload = json.loads(event.payload)
        alert_id = payload["alert_id"]
        recipient = payload["recipient"]
        alert = db.scalar(select(Alert).where(Alert.id == alert_id))
        if alert is None:
            event.status = FailedEventStatus.RESOLVED.value
            event.error = "Alert no longer exists"
            resolved += 1
            continue

        ok, details = _dispatch_notification(db, alert, recipient)
        if ok:
            event.status = FailedEventStatus.RESOLVED.value
            event.error = "Recovered via retry"
            resolved += 1
        elif event.retry_count >= 3:
            event.status = FailedEventStatus.EXHAUSTED.value
            event.error = f"Retry exhausted: {details}"
            exhausted += 1
        else:
            event.error = details
            pending += 1

    return retried, resolved, pending, exhausted


def serialize_alert_event(alert: Alert) -> dict:
    return {
        "type": "ALERT_CREATED",
        "alert_id": alert.id,
        "patient_id": alert.patient_id,
        "severity": alert.severity,
        "status": alert.status,
        "message": alert.message,
        "created_at": alert.created_at.isoformat(),
    }


def serialize_ack_event(alert: Alert, doctor_username: str) -> dict:
    return {
        "type": "ALERT_ACKNOWLEDGED",
        "alert_id": alert.id,
        "patient_id": alert.patient_id,
        "severity": alert.severity,
        "status": alert.status,
        "ack_by": doctor_username,
        "ack_at": alert.ack_at.isoformat() if alert.ack_at else None,
    }
