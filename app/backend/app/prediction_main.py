from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
import math
import os
from typing import Protocol

from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import engine
from .messaging import RabbitMQBridge, build_event_envelope, parse_event_envelope
from .models import PredictionBaseline, PredictionRecord


class PredictionStrategy(Protocol):
    def run(self, db: Session, vital_event: dict) -> dict:
        ...


def _ews_points_heart_rate(hr: float) -> int:
    if hr <= 40:
        return 3
    if hr <= 50:
        return 1
    if hr <= 90:
        return 0
    if hr <= 110:
        return 1
    if hr <= 130:
        return 2
    return 3


def _ews_points_spo2(spo2: float) -> int:
    if spo2 <= 91:
        return 3
    if spo2 <= 93:
        return 2
    if spo2 <= 95:
        return 1
    return 0


def _ews_points_temp(temp: float) -> int:
    if temp <= 35.0:
        return 3
    if temp <= 36.0:
        return 1
    if temp <= 38.0:
        return 0
    if temp <= 39.0:
        return 1
    return 2


def _ews_points_resp(rr: float) -> int:
    if rr <= 8:
        return 3
    if rr <= 11:
        return 1
    if rr <= 20:
        return 0
    if rr <= 24:
        return 2
    return 3


def _ews_points_sys(sys: float) -> int:
    if sys <= 90:
        return 3
    if sys <= 100:
        return 2
    if sys <= 110:
        return 1
    if sys <= 219:
        return 0
    return 3


def _severity_from_score(score: float) -> str:
    if score >= 9:
        return "CRITICAL"
    if score >= 7:
        return "HIGH"
    if score >= 5:
        return "MEDIUM"
    return "LOW"


class EarlyWarningStrategy:
    version = "news2-inspired-v1"

    def run(self, db: Session, vital_event: dict) -> dict:
        del db
        hr = float(vital_event.get("heart_rate", 0))
        spo2 = float(vital_event.get("spo2", 0))
        temp = float(vital_event.get("temperature", 0))
        rr = float(vital_event.get("respiratory_rate", 16))
        bp_sys = float(vital_event.get("bp_sys", 0))
        score = (
            _ews_points_heart_rate(hr)
            + _ews_points_spo2(spo2)
            + _ews_points_temp(temp)
            + _ews_points_resp(rr)
            + _ews_points_sys(bp_sys)
        )
        return {"score": score, "severity": _severity_from_score(score), "version": self.version}


class PersonalizedBaselineStrategy:
    version = "rolling-zscore-v1"
    warmup_samples = 10
    z_threshold = 2.5

    def _update_baseline(self, row: PredictionBaseline, value: float):
        row.sample_count += 1
        delta = value - row.mean
        row.mean += delta / row.sample_count
        delta2 = value - row.mean
        row.m2 += delta * delta2
        row.updated_at = datetime.now(timezone.utc)

    def _std(self, row: PredictionBaseline) -> float:
        if row.sample_count < 2:
            return 0.0
        return math.sqrt(row.m2 / (row.sample_count - 1))

    def run(self, db: Session, vital_event: dict) -> dict:
        patient_id = int(vital_event["patient_id"])
        vital_keys = ["heart_rate", "spo2", "bp_sys", "bp_dia", "temperature", "respiratory_rate"]
        max_z = 0.0
        factors: list[dict] = []
        warmup = False

        for key in vital_keys:
            value = float(vital_event.get(key, 0))
            baseline = db.scalar(
                select(PredictionBaseline).where(
                    PredictionBaseline.patient_id == patient_id,
                    PredictionBaseline.vital_name == key,
                )
            )
            if baseline is None:
                baseline = PredictionBaseline(patient_id=patient_id, vital_name=key, sample_count=0, mean=0.0, m2=0.0)
                db.add(baseline)
                db.flush()

            std = self._std(baseline)
            if baseline.sample_count >= self.warmup_samples and std > 0:
                z = abs((value - baseline.mean) / std)
                if z > max_z:
                    max_z = z
                if z >= self.z_threshold:
                    factors.append({"vital": key, "z_score": round(z, 3)})
            else:
                warmup = True

            self._update_baseline(baseline, value)

        severity = "LOW"
        if max_z >= 4:
            severity = "CRITICAL"
        elif max_z >= 3:
            severity = "HIGH"
        elif max_z >= self.z_threshold:
            severity = "MEDIUM"

        return {
            "max_z_score": round(max_z, 3),
            "severity": severity,
            "factors": factors,
            "warmup": warmup,
            "version": self.version,
        }


def _combine_severity(*values: str) -> str:
    rank = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}
    return max(values, key=lambda v: rank.get(v, 0))


notification_bridge: RabbitMQBridge | None = None
ews_strategy = EarlyWarningStrategy()
baseline_strategy = PersonalizedBaselineStrategy()


def _handle_vital_received(payload: dict):
    event_type, data = parse_event_envelope(payload)
    if event_type is not None and event_type != "VITAL_RECEIVED":
        return
    if not data.get("patient_id"):
        return
    with Session(engine) as db:
        ews = ews_strategy.run(db, data)
        baseline = baseline_strategy.run(db, data)
        combined = _combine_severity(ews["severity"], baseline["severity"])
        prediction_event = build_event_envelope(
            "RISK_PREDICTED",
            {
                "patient_id": data["patient_id"],
                "reading_id": data.get("reading_id"),
                "ews_score": ews["score"],
                "ews_severity": ews["severity"],
                "baseline_max_z": baseline["max_z_score"],
                "baseline_severity": baseline["severity"],
                "combined_severity": combined,
                "contributing_factors": baseline["factors"],
                "strategy_versions": {"ews": ews["version"], "baseline": baseline["version"]},
                "predicted_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        db.add(
            PredictionRecord(
                patient_id=int(data["patient_id"]),
                reading_id=int(data["reading_id"]) if data.get("reading_id") is not None else None,
                ews_score=float(ews["score"]),
                ews_severity=str(ews["severity"]),
                baseline_max_z=float(baseline["max_z_score"]),
                baseline_severity=str(baseline["severity"]),
                combined_severity=str(combined),
                strategy_versions={
                    "ews": ews["version"],
                    "baseline": baseline["version"],
                },
                factors=baseline["factors"],
                predicted_at=datetime.now(timezone.utc),
            )
        )
        db.commit()
    if notification_bridge is not None:
        notification_bridge.publish_event(
            prediction_event, queue_name=os.getenv("RABBITMQ_NOTIFICATION_QUEUE", "vitaltrack.notifications.events")
        )


@asynccontextmanager
async def lifespan(_: FastAPI):
    global notification_bridge
    rabbit_url = os.getenv("RABBITMQ_URL", "")
    vital_queue = os.getenv("RABBITMQ_VITAL_QUEUE", "vitaltrack.vitals.received")
    notification_queue = os.getenv("RABBITMQ_NOTIFICATION_QUEUE", "vitaltrack.notifications.events")
    consumer_bridge: RabbitMQBridge | None = None
    if rabbit_url:
        notification_bridge = RabbitMQBridge(rabbit_url, notification_queue, lambda _: None)
        consumer_bridge = RabbitMQBridge(rabbit_url, vital_queue, _handle_vital_received)
        consumer_bridge.start_consumer()
    yield
    if consumer_bridge is not None:
        consumer_bridge.stop_consumer()


app = FastAPI(title="VitalTrack Prediction Service", lifespan=lifespan)


@app.get("/v1/prediction/health")
def prediction_health():
    return {"status": "ok"}
