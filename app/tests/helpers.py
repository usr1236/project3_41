from __future__ import annotations

import json
import time
from typing import Any
from urllib import parse, request


def http_json(
    method: str,
    url: str,
    *,
    token: str | None = None,
    body: dict[str, Any] | None = None,
    timeout: float = 20.0,
) -> Any:
    payload: bytes | None = None
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        headers["Content-Type"] = "application/json"
        payload = json.dumps(body).encode("utf-8")
    req = request.Request(url=url, method=method.upper(), data=payload, headers=headers)
    with request.urlopen(req, timeout=timeout) as resp:
        data = resp.read().decode("utf-8")
        return json.loads(data) if data else {}


def post_form(url: str, form_data: dict[str, str], *, timeout: float = 20.0) -> Any:
    encoded = parse.urlencode(form_data).encode("utf-8")
    req = request.Request(
        url=url,
        method="POST",
        data=encoded,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def wait_until(predicate, *, timeout_s: float = 20.0, interval_s: float = 0.5):
    start = time.time()
    while time.time() - start < timeout_s:
        result = predicate()
        if result:
            return result
        time.sleep(interval_s)
    return None
