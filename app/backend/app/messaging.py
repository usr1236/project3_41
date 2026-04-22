from __future__ import annotations

import json
import logging
import threading
import time
from typing import Callable

import pika

logger = logging.getLogger("vitaltrack.messaging")


class RabbitMQBridge:
    def __init__(self, amqp_url: str, queue_name: str, on_alert_created: Callable[[dict], None]):
        self.amqp_url = amqp_url
        self.queue_name = queue_name
        self.on_alert_created = on_alert_created
        self._stop_event = threading.Event()
        self._consumer_thread: threading.Thread | None = None

    def _connection_params(self) -> pika.URLParameters:
        params = pika.URLParameters(self.amqp_url)
        params.heartbeat = 30
        params.blocked_connection_timeout = 30
        return params

    def _ensure_queue(self, channel: pika.adapters.blocking_connection.BlockingChannel):
        channel.queue_declare(queue=self.queue_name, durable=True)

    def publish_alert_created(self, payload: dict) -> bool:
        try:
            conn = pika.BlockingConnection(self._connection_params())
            ch = conn.channel()
            self._ensure_queue(ch)
            ch.basic_publish(
                exchange="",
                routing_key=self.queue_name,
                body=json.dumps(payload).encode(),
                properties=pika.BasicProperties(delivery_mode=2),
            )
            conn.close()
            return True
        except Exception:
            return False

    def _consume_loop(self):
        while not self._stop_event.is_set():
            try:
                conn = pika.BlockingConnection(self._connection_params())
                ch = conn.channel()
                self._ensure_queue(ch)

                def callback(channel, method, properties, body):  # noqa: ANN001
                    try:
                        payload = json.loads(body.decode())
                        self.on_alert_created(payload)
                        channel.basic_ack(delivery_tag=method.delivery_tag)
                    except Exception:
                        # Requeue so transient issues can recover.
                        logger.exception("RabbitMQ consumer callback failed; requeueing message")
                        channel.basic_nack(delivery_tag=method.delivery_tag, requeue=True)

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
