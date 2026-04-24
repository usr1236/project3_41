from __future__ import annotations

import pytest

from tests.helpers import http_json, wait_until


@pytest.mark.integration
def test_e2e_clinical_acknowledgment_pipeline(base_url: str, token_provider):
    """
    E2E-2:
    doctor acknowledges an OPEN alert -> status updates -> audit recorded.
    """
    sim_token = token_provider("simulator", "sim123")
    doctor_token = token_provider("doctor1", "doctor123")
    admin_token = token_provider("admin", "admin123")

    ingest = http_json(
        "POST",
        f"{base_url}/v1/vitals",
        token=sim_token,
        body={
            "patient_id": 1,
            "heart_rate": 146,
            "spo2": 84.0,
            "bp_sys": 171,
            "bp_dia": 101,
            "respiratory_rate": 30,
            "temperature": 39.5,
            "source": "pytest-e2e-2",
        },
    )
    assert ingest["alert_created"] is True

    def _find_open_alert():
        dashboard = http_json("GET", f"{base_url}/v1/doctor/dashboard", token=doctor_token)
        for alert in dashboard.get("active_alerts", []):
            if int(alert["patient_id"]) == 1 and alert["status"] == "OPEN":
                return int(alert["id"])
        return None

    alert_id = wait_until(_find_open_alert, timeout_s=30.0, interval_s=1.0)
    assert alert_id is not None

    ack = http_json(
        "POST",
        f"{base_url}/v1/alerts/{alert_id}/ack",
        token=doctor_token,
        body={},
    )
    assert int(ack["alert_id"]) == alert_id
    assert ack["status"] == "ACKNOWLEDGED"
    assert ack["ack_by"] == "doctor1"

    def _alert_removed_from_open_list():
        dashboard = http_json("GET", f"{base_url}/v1/doctor/dashboard", token=doctor_token)
        still_open = any(int(a["id"]) == alert_id for a in dashboard.get("active_alerts", []))
        return not still_open

    removed = wait_until(_alert_removed_from_open_list, timeout_s=20.0, interval_s=1.0)
    assert removed is True

    audit_rows = http_json("GET", f"{base_url}/v1/audit", token=admin_token)
    has_ack_audit = any(
        row.get("action") == "ALERT_ACKNOWLEDGED" and row.get("target_id") == str(alert_id)
        for row in audit_rows
    )
    assert has_ack_audit is True
