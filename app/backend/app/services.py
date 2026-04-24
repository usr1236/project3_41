from __future__ import annotations

from datetime import datetime, timezone
import json
import random
from typing import Protocol

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


AlertDecision = tuple[bool, AlertSeverity | None, str, str]


class AlertRuleHandler:
    """Chain of Responsibility for alert rule evaluation."""

    def __init__(self):
        self._next: AlertRuleHandler | None = None

    def set_next(self, nxt: AlertRuleHandler) -> AlertRuleHandler:
        self._next = nxt
        return nxt

    def handle(self, reading: VitalReading, risk_score: float) -> AlertDecision:
        decision = self._evaluate(reading, risk_score)
        if decision is not None:
            return decision
        if self._next is not None:
            return self._next.handle(reading, risk_score)
        return False, None, "NORMAL", "No alert"

    def _evaluate(self, reading: VitalReading, risk_score: float) -> AlertDecision | None:
        raise NotImplementedError


class CriticalVitalsRule(AlertRuleHandler):
    def _evaluate(self, reading: VitalReading, risk_score: float) -> AlertDecision | None:
        del risk_score
        if reading.spo2 < 88 or reading.heart_rate > 130:
            return True, AlertSeverity.CRITICAL, "CRIT_VITALS", "Critical vital signs detected"
        return None


class HighRiskScoreRule(AlertRuleHandler):
    def _evaluate(self, reading: VitalReading, risk_score: float) -> AlertDecision | None:
        del reading
        if risk_score >= 20:
            return True, AlertSeverity.HIGH, "HIGH_RISK_SCORE", "High short-term risk predicted"
        return None


class AbnormalTrendRule(AlertRuleHandler):
    def _evaluate(self, reading: VitalReading, risk_score: float) -> AlertDecision | None:
        del risk_score
        if reading.spo2 < 92 or reading.temperature > 38.5:
            return True, AlertSeverity.MEDIUM, "ABNORMAL_TREND", "Abnormal trend detected"
        return None


class NotificationCommand(Protocol):
    """Command interface for notification dispatch actions."""

    def execute(self) -> tuple[bool, str]:
        ...


class InAppNotificationCommand:
    def __init__(self, db: Session, alert: Alert, recipient: str):
        self.db = db
        self.alert = alert
        self.recipient = recipient

    def execute(self) -> tuple[bool, str]:
        # Simulate occasional channel failures for failed-event handling demonstration.
        # Set recipient to include "fail" for deterministic failure.
        fails = "fail" in self.recipient or random.random() < 0.1
        if fails:
            return False, "Notification provider timeout"

        self.db.add(
            Notification(
                alert_id=self.alert.id,
                channel="in_app",
                recipient=self.recipient,
                status="SENT",
                details=f"Alert {self.alert.id} ({self.alert.severity}): {self.alert.message}",
            )
        )
        return True, "sent"


class NotificationCommandFactory:
    """Factory method for constructing concrete notification commands."""

    @staticmethod
    def create(channel: str, db: Session, alert: Alert, recipient: str) -> NotificationCommand:
        if channel == "in_app":
            return InAppNotificationCommand(db, alert, recipient)
        raise ValueError(f"Unsupported notification channel: {channel}")


def _build_alert_rule_chain() -> AlertRuleHandler:
    root = CriticalVitalsRule()
    root.set_next(HighRiskScoreRule()).set_next(AbnormalTrendRule())
    return root


_ALERT_RULE_CHAIN = _build_alert_rule_chain()


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
    return _ALERT_RULE_CHAIN.handle(reading, risk_score)


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
    command = NotificationCommandFactory.create("in_app", db, alert, recipient)
    return command.execute()


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
