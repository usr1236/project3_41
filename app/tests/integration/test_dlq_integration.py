from __future__ import annotations

import os
import time

import pika
import pytest

from tests.helpers import http_json, wait_until


@pytest.mark.integration
def test_notification_queue_failed_message_moves_to_dlq(base_url: str, token_provider):
    """
    Verifies RabbitMQ DLQ behavior:
    publish malformed payload to notifications queue and confirm DLQ depth increases.
    """
    doctor_token = token_provider("doctor1", "doctor123")
    amqp_url = os.getenv("VITALTRACK_AMQP_URL", "amqp://guest:guest@localhost:5672/%2F")
    queue_name = os.getenv("VITALTRACK_NOTIFICATION_QUEUE", "vitaltrack.notifications.events")

    before = http_json("GET", f"{base_url}/v1/queue/health", token=doctor_token)
    before_dlq = int(before.get("dlq_messages", 0))

    conn = pika.BlockingConnection(pika.URLParameters(amqp_url))
    ch = conn.channel()
    ch.basic_publish(
        exchange="",
        routing_key=queue_name,
        body=b"not-json-dlq-test",
        properties=pika.BasicProperties(delivery_mode=2),
    )
    conn.close()

    def _dlq_increased():
        current = http_json("GET", f"{base_url}/v1/queue/health", token=doctor_token)
        dlq_now = int(current.get("dlq_messages", 0))
        if dlq_now >= before_dlq + 1:
            return current
        return None

    result = wait_until(_dlq_increased, timeout_s=20.0, interval_s=1.0)
    assert result is not None, "DLQ message count did not increase after consumer failure"
