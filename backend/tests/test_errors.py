"""A：参数校验异常统一为 problem+json（BT-10003）。"""

from __future__ import annotations

import json

import pytest
from app.main import json_safe
from fastapi.testclient import TestClient


def test_json_safe_handles_unserializable_ctx() -> None:
    """pydantic 的 ctx 里可能是 ValueError 实例；不清洗会让 handler 自身 500。"""
    payload = {"ctx": {"error": ValueError("boo")}, "input": object(), "loc": ("body",)}
    cleaned = json_safe(payload)
    assert json.dumps(cleaned, ensure_ascii=False)  # 不抛 TypeError
    assert cleaned["ctx"]["error"].startswith("boo")


def test_limit_over_max_returns_422_problem_json(client: TestClient) -> None:
    resp = client.get("/api/v1/components", params={"struct_code": "B001", "limit": 200})
    assert resp.status_code == 422
    assert resp.headers["content-type"].startswith("application/problem+json")
    body = resp.json()
    assert body["code"] == "BT-10003"
    assert body["status"] == 422
    assert body["title"] == "请求参数校验失败"
    assert body["instance"] == "/api/v1/components"
    assert body["traceId"] == resp.headers["X-Request-Id"]
    assert body["errors"][0]["field"] == "query.limit"
    assert body["errors"][0]["type"] == "less_than_equal"


def test_missing_required_query_returns_422(client: TestClient) -> None:
    resp = client.get("/api/v1/components")
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == "BT-10003"
    assert body["errors"][0]["field"] == "query.struct_code"
    assert body["errors"][0]["type"] == "missing"
    assert body["detail"].startswith("query.struct_code:")


def test_bad_uuid_query_returns_422(client: TestClient) -> None:
    resp = client.get(
        "/api/v1/components", params={"struct_code": "B001", "parent_id": "not-a-uuid"}
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == "BT-10003"
    assert body["errors"][0]["field"] == "query.parent_id"
    assert body["errors"][0]["type"] == "uuid_parsing"


def test_post_missing_body_fields_returns_422(client: TestClient) -> None:
    resp = client.post("/api/v1/components", json={})
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == "BT-10003"
    fields = {item["field"] for item in body["errors"]}
    assert "body.level" in fields
    # detail 只给摘要，不塞 pydantic 原始 ctx
    assert "ctx" not in body["detail"]


def test_malformed_json_body_is_422_not_500(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/components",
        content=b'{"level": ',
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "BT-10003"


def test_biz_error_and_not_found_unaffected(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """两条异常处理器互不覆盖：业务 404 仍走 BT-30001，缺 Authorization 仍 401。"""
    not_found = client.get("/api/v1/components/B001-K99-UNKNOWN")
    assert not_found.status_code == 404
    assert not_found.json()["code"] == "BT-30001"

    monkeypatch.delenv("BT_DEV_ANONYMOUS", raising=False)
    unauthorized = client.get("/api/v1/components", params={"struct_code": "B001"})
    assert unauthorized.status_code == 401
    assert unauthorized.json()["code"] == "BT-10001"
    assert unauthorized.headers["WWW-Authenticate"] == "Bearer"
    # 未认证分支不受校验 handler 影响：即便 query 合法也是 401
    assert "errors" not in unauthorized.json()
