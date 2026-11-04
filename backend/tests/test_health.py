"""T-02 验证用例：健康检查与 traceId 贯通。"""

from __future__ import annotations

from app.main import create_app
from fastapi.testclient import TestClient


def test_healthz_ok() -> None:
    client = TestClient(create_app())
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_healthz_echoes_request_id() -> None:
    client = TestClient(create_app())
    resp = client.get("/healthz", headers={"X-Request-Id": "trace-abc"})
    assert resp.headers["X-Request-Id"] == "trace-abc"


def test_readyz_reports_db_state() -> None:
    client = TestClient(create_app())
    resp = client.get("/readyz")
    assert resp.status_code in (200, 503)
    body = resp.json()
    assert "db" in body["checks"]
    assert body["traceId"] != ""
