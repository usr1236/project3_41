from __future__ import annotations

import pytest

from tests.helpers import http_json, wait_until


@pytest.mark.integration
def test_e2e_critical_alert_pipeline(base_url: str, token_provider):
    """
    E2E-1:
    ingest critical vitals -> alert creation -> visible on doctor dashboard
    -> notification persisted -> audit evidence present.
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
            "heart_rate": 147,
            "spo2": 84.0,
            "bp_sys": 172,
            "bp_dia": 102,
            "respiratory_rate": 31,
            "temperature": 39.6,
            "source": "pytest-e2e-1",
        },
    )
    assert ingest["alert_created"] is True

    def _latest_alert():
        dashboard = http_json("GET", f"{base_url}/v1/doctor/dashboard", token=doctor_token)
        for alert in dashboard.get("active_alerts", []):
            if int(alert["patient_id"]) == 1 and alert["status"] == "OPEN":
                return alert
        return None

    alert = wait_until(_latest_alert, timeout_s=30.0, interval_s=1.0)
    assert alert is not None
    alert_id = int(alert["id"])

    def _notification_row():
        rows = http_json("GET", f"{base_url}/v1/notifications?limit=200", token=admin_token)
        for row in rows:
            if int(row.get("alert_id", -1)) == alert_id:
                return row
        return None

    notification = wait_until(_notification_row, timeout_s=30.0, interval_s=1.0)
    assert notification is not None
    assert notification["status"] == "SENT"

    audit_rows = http_json("GET", f"{base_url}/v1/audit", token=admin_token)
    has_alert_related_audit = any(
        row.get("target_id") == str(alert_id)
        and row.get("action")
        in {
            "ALERT_CREATED",
            "ALERT_EVENT_ENQUEUED",
            "ALERT_NOTIFICATIONS_PROCESSED",
            "ESCALATION_CASE_CREATED",
        }
        for row in audit_rows
    )
    assert has_alert_related_audit is True
