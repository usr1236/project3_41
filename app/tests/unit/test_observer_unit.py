from __future__ import annotations

from app.models import AlertSeverity, VitalReading
from app.vital_observer import VitalObservationContext, build_default_vital_subject


def _reading(**overrides) -> VitalReading:
    base = {
        "patient_id": 1,
        "heart_rate": 80,
        "spo2": 98.0,
        "bp_sys": 120,
        "bp_dia": 80,
        "respiratory_rate": 16,
        "temperature": 36.8,
        "source": "test",
    }
    base.update(overrides)
    return VitalReading(**base)


def test_vital_observer_subject_computes_risk_and_alert_decision():
    subject = build_default_vital_subject()
    context = VitalObservationContext(
        reading=_reading(heart_rate=145, spo2=84.0, bp_sys=170, temperature=39.2),
    )
    subject.notify(context)

    assert context.risk_score > 0
    assert context.should_alert is True
    assert context.severity == AlertSeverity.CRITICAL
    assert context.rule_code == "CRIT_VITALS"
