from datetime import datetime

from pydantic import BaseModel, Field


class VitalIngestRequest(BaseModel):
    patient_id: int
    heart_rate: int = Field(ge=20, le=240)
    spo2: float = Field(ge=50, le=100)
    bp_sys: int = Field(ge=50, le=260)
    bp_dia: int = Field(ge=30, le=160)
    temperature: float = Field(ge=30, le=45)
    source: str = "simulator"


class VitalIngestResponse(BaseModel):
    reading_id: int
    alert_created: bool
    risk_score: float
    severity: str | None = None


class AlertOut(BaseModel):
    id: int
    patient_id: int
    severity: str
    rule_code: str
    message: str
    status: str
    created_at: datetime
    ack_by: int | None
    ack_at: datetime | None

    class Config:
        from_attributes = True


class DashboardResponse(BaseModel):
    active_alerts: list[AlertOut]
    total_open_alerts: int


class AckResponse(BaseModel):
    alert_id: int
    status: str
    ack_by: str


class FailedRetryResponse(BaseModel):
    retried: int
    resolved: int
    pending: int
    exhausted: int


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=6, max_length=128)
    role: str
    full_name: str | None = Field(default=None, max_length=120)
    patient_id: int | None = None


class RegisterResponse(BaseModel):
    user_id: int
    username: str
    role: str
    patient_id: int | None = None
    caregiver_request_id: int | None = None
    caregiver_request_status: str | None = None


class AdminCreateUserRequest(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=6, max_length=128)
    role: str
    full_name: str | None = Field(default=None, max_length=120)


class CaregiverAssignmentRequest(BaseModel):
    caregiver_username: str
    patient_id: int


class CaregiverRequestReviewRequest(BaseModel):
    notes: str | None = None


class VitalsSeriesResponse(BaseModel):
    patient_id: int
    points: list[dict]


class PatientStatsResponse(BaseModel):
    patient_id: int
    window_minutes: int
    summary: dict


class PatientPortalResponse(BaseModel):
    patient_id: int
    full_name: str
    recent_vitals: list[dict]
    recent_alerts: list[dict]


class CaregiverDashboardResponse(BaseModel):
    caregiver_username: str
    assigned_patients: list[dict]
