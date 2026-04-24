from __future__ import annotations

from app.models import AlertSeverity, VitalReading
from app.services import calculate_risk_score, evaluate_alert


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


def test_calculate_risk_score_normal_is_zero():
    reading = _reading()
    assert calculate_risk_score(reading) == 0.0


def test_calculate_risk_score_caps_at_100():
    reading = _reading(heart_rate=230, spo2=60.0, temperature=42.0, bp_sys=260)
    assert calculate_risk_score(reading) == 100.0


def test_evaluate_alert_critical_vitals_take_priority():
    reading = _reading(heart_rate=135, spo2=90.0)
    should_alert, severity, rule_code, message = evaluate_alert(reading, risk_score=10.0)

    assert should_alert is True
    assert severity == AlertSeverity.CRITICAL
    assert rule_code == "CRIT_VITALS"
    assert "Critical vital signs" in message


def test_evaluate_alert_high_for_risk_score_threshold():
    reading = _reading(heart_rate=115, spo2=94.0)
    should_alert, severity, rule_code, _ = evaluate_alert(reading, risk_score=25.0)

    assert should_alert is True
    assert severity == AlertSeverity.HIGH
    assert rule_code == "HIGH_RISK_SCORE"
