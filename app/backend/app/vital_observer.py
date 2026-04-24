from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .models import AlertSeverity, VitalReading
from .services import calculate_risk_score, evaluate_alert


@dataclass
class VitalObservationContext:
    """Shared context passed through in-process vital observers."""

    reading: VitalReading
    risk_score: float = 0.0
    should_alert: bool = False
    severity: AlertSeverity | None = None
    rule_code: str = "NORMAL"
    message: str = "No alert"


class VitalObserver(Protocol):
    def update(self, context: VitalObservationContext) -> None:
        ...


class VitalSubject:
    """GoF Observer Subject for in-process vital processing stages."""

    def __init__(self):
        self._observers: list[VitalObserver] = []

    def attach(self, observer: VitalObserver) -> None:
        self._observers.append(observer)

    def notify(self, context: VitalObservationContext) -> None:
        for observer in self._observers:
            observer.update(context)


class RiskScoreObserver:
    def update(self, context: VitalObservationContext) -> None:
        context.risk_score = calculate_risk_score(context.reading)


class AlertEvaluationObserver:
    def update(self, context: VitalObservationContext) -> None:
        (
            context.should_alert,
            context.severity,
            context.rule_code,
            context.message,
        ) = evaluate_alert(context.reading, context.risk_score)


def build_default_vital_subject() -> VitalSubject:
    subject = VitalSubject()
    subject.attach(RiskScoreObserver())
    subject.attach(AlertEvaluationObserver())
    return subject
