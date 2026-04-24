from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Callable

import pika

logger = logging.getLogger("vitaltrack.messaging")
DEFAULT_EVENT_SCHEMA_VERSION = "1.0"


def build_event_envelope(event_type: str, data: dict, schema_version: str = DEFAULT_EVENT_SCHEMA_VERSION) -> dict:
    return {
        "schema_version": schema_version,
        "event_type": event_type,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "data": data,
    }


def parse_event_envelope(message: dict) -> tuple[str | None, dict]:
    """
    Supports both versioned envelopes and legacy flat payloads.
    Returns (event_type, data_payload).
    """
    if isinstance(message, dict) and "data" in message and "event_type" in message:
        data = message.get("data")
        if isinstance(data, dict):
            return str(message.get("event_type")), data
        return str(message.get("event_type")), {}
    # Legacy payload fallback.
    if isinstance(message, dict):
        return None, message
    return None, {}


class RabbitMQBridge:
    def __init__(self, amqp_url: str, queue_name: str, on_message: Callable[[dict], None]):
        self.amqp_url = amqp_url
        self.queue_name = queue_name
        self.on_message = on_message
        self._stop_event = threading.Event()
        self._consumer_thread: threading.Thread | None = None

    def _connection_params(self) -> pika.URLParameters:
        params = pika.URLParameters(self.amqp_url)
        params.heartbeat = 30
        params.blocked_connection_timeout = 30
        return params

    def _dead_letter_exchange(self) -> str:
        return os.getenv("RABBITMQ_DLX", "vitaltrack.dlx")

    def _dead_letter_queue(self, queue_name: str) -> str:
        return f"{queue_name}.dlq"

    def _ensure_queue(
        self, channel: pika.adapters.blocking_connection.BlockingChannel, queue_name: str | None = None
    ) -> pika.adapters.blocking_connection.BlockingChannel:
        resolved_queue = queue_name or self.queue_name
        dlx = self._dead_letter_exchange()
        dlq = self._dead_letter_queue(resolved_queue)
        routing_key = dlq

        try:
            channel.exchange_declare(exchange=dlx, exchange_type="direct", durable=True)
            channel.queue_declare(
                queue=resolved_queue,
                durable=True,
                arguments={
                    "x-dead-letter-exchange": dlx,
                    "x-dead-letter-routing-key": routing_key,
                },
            )
            channel.queue_declare(queue=dlq, durable=True)
            channel.queue_bind(exchange=dlx, queue=dlq, routing_key=routing_key)
            return channel
        except pika.exceptions.ChannelClosedByBroker as exc:
            # Existing queue may have been declared earlier without DLX args.
            # In prototype mode we can redeclare to converge config safely.
            if exc.reply_code != 406:
                raise
            logger.warning("Queue %s has incompatible args for DLQ: %s", resolved_queue, exc)
            channel = channel.connection.channel()
            force_redeclare = os.getenv("RABBITMQ_FORCE_DLX_REDECLARE", "true").lower() in {"1", "true", "yes"}
            if force_redeclare:
                try:
                    channel.queue_purge(queue=resolved_queue)
                except Exception:
                    logger.warning("Queue purge failed before redeclare: %s", resolved_queue)
                channel.queue_delete(queue=resolved_queue)
                channel.queue_declare(
                    queue=resolved_queue,
                    durable=True,
                    arguments={
                        "x-dead-letter-exchange": dlx,
                        "x-dead-letter-routing-key": routing_key,
                    },
                )
                channel.queue_declare(queue=dlq, durable=True)
                channel.queue_bind(exchange=dlx, queue=dlq, routing_key=routing_key)
                logger.warning("Queue %s recreated to enable DLQ", resolved_queue)
            else:
                channel.queue_declare(queue=resolved_queue, durable=True)
                channel.queue_declare(queue=dlq, durable=True)
                logger.warning(
                    "Queue %s kept as-is (DLQ args not applied). Set RABBITMQ_FORCE_DLX_REDECLARE=true to enforce.",
                    resolved_queue,
                )
            return channel

    def publish_event(self, payload: dict, queue_name: str | None = None) -> bool:
        try:
            conn = pika.BlockingConnection(self._connection_params())
            ch = conn.channel()
            target_queue = queue_name or self.queue_name
            ch = self._ensure_queue(ch, target_queue)
            ch.basic_publish(
                exchange="",
                routing_key=target_queue,
                body=json.dumps(payload).encode(),
                properties=pika.BasicProperties(delivery_mode=2),
            )
            conn.close()
            return True
        except Exception:
            return False

    def publish_alert_created(self, payload: dict) -> bool:
        # Backward-compatible alias.
        return self.publish_event(payload, self.queue_name)

    def _consume_loop(self):
        while not self._stop_event.is_set():
            try:
                conn = pika.BlockingConnection(self._connection_params())
                ch = conn.channel()
                ch = self._ensure_queue(ch)

                def callback(channel, method, properties, body):  # noqa: ANN001
                    try:
                        payload = json.loads(body.decode())
                        self.on_message(payload)
                        channel.basic_ack(delivery_tag=method.delivery_tag)
                    except Exception:
                        # Dead-letter failed messages so they can be inspected/replayed safely.
                        logger.exception("RabbitMQ consumer callback failed; dead-lettering message")
                        channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)

                ch.basic_qos(prefetch_count=10)
                ch.basic_consume(queue=self.queue_name, on_message_callback=callback)

                while not self._stop_event.is_set():
                    conn.process_data_events(time_limit=1)

                try:
                    ch.stop_consuming()
                except Exception:
                    pass
                conn.close()
            except Exception:
                time.sleep(2)

    def start_consumer(self):
        if self._consumer_thread and self._consumer_thread.is_alive():
            return
        self._stop_event.clear()
        self._consumer_thread = threading.Thread(target=self._consume_loop, daemon=True)
        self._consumer_thread.start()

    def stop_consumer(self):
        self._stop_event.set()
        if self._consumer_thread:
            self._consumer_thread.join(timeout=3)
