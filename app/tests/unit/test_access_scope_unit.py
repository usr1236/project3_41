from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import MagicMock

os.environ.setdefault("JWT_SECRET", "test-secret-0123456789-abcdefghijklmnopqrstuvwxyz")

from app.models import UserRole
from app.notification_main import _can_access_patient


def _user(role: UserRole, user_id: int = 10):
    return SimpleNamespace(role=role.value, id=user_id)


def test_access_scope_admin_and_doctor_have_global_access():
    db = MagicMock()
    assert _can_access_patient(db, _user(UserRole.ADMIN), 99) is True
    assert _can_access_patient(db, _user(UserRole.DOCTOR), 99) is True


def test_access_scope_patient_only_to_own_patient():
    db = MagicMock()
    db.scalar.return_value = SimpleNamespace(id=7)

    assert _can_access_patient(db, _user(UserRole.PATIENT, user_id=21), 7) is True
    assert _can_access_patient(db, _user(UserRole.PATIENT, user_id=21), 8) is False


def test_access_scope_caregiver_requires_assignment():
    db = MagicMock()
    db.scalar.return_value = SimpleNamespace(patient_id=3)

    assert _can_access_patient(db, _user(UserRole.CAREGIVER, user_id=55), 3) is True

    db.scalar.return_value = None
    assert _can_access_patient(db, _user(UserRole.CAREGIVER, user_id=55), 3) is False
