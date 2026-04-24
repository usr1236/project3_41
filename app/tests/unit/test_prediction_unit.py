from __future__ import annotations

from app.prediction_main import EarlyWarningStrategy, _combine_severity


def test_ews_strategy_returns_expected_shape_and_severity():
    strategy = EarlyWarningStrategy()
    result = strategy.run(
        None,  # db is intentionally unused by this strategy
        {
            "heart_rate": 145,
            "spo2": 84.0,
            "temperature": 39.4,
            "respiratory_rate": 30,
            "bp_sys": 175,
        },
    )

    assert result["version"] == "news2-inspired-v1"
    assert result["score"] >= 9
    assert result["severity"] == "CRITICAL"


def test_combine_severity_uses_highest_priority_value():
    combined = _combine_severity("LOW", "MEDIUM", "CRITICAL", "HIGH")
    assert combined == "CRITICAL"
