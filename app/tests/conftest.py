from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib import request

import pytest
from tests.helpers import post_form


ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


@pytest.fixture(scope="session")
def base_url() -> str:
    return os.getenv("VITALTRACK_BASE_URL", "http://localhost:8000").rstrip("/")


@pytest.fixture(scope="session")
def ensure_runtime_available(base_url: str) -> None:
    health_urls = [
        f"{base_url}/docs",
        f"{base_url}/v1/prediction/health",
    ]
    for url in health_urls:
        try:
            request.urlopen(url, timeout=8).read()
        except Exception as exc:  # pragma: no cover - environment dependent
            pytest.skip(f"Runtime integration tests skipped (service not reachable at {url}): {exc}")


@pytest.fixture(scope="session")
def token_provider(base_url: str, ensure_runtime_available: None):
    def _get(username: str, password: str) -> str:
        payload = post_form(
            f"{base_url}/auth/token",
            {"username": username, "password": password},
            timeout=20.0,
        )
        token = payload.get("access_token")
        if not token:
            raise AssertionError(f"No access_token in auth response for user {username!r}: {payload}")
        return str(token)

    return _get


