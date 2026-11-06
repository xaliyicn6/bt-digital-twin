"""M05 API-14：几何任务受理的即时性、状态流转、失败分支与纯逻辑。

数据库用例沿用 conftest 约定：未设 ``BT_TEST_DATABASE_URL`` 时整体 skip；
不依赖 DB 的纯逻辑用例（``Location`` 拼装、状态映射、点云定位、缺文件异常）始终执行。
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path

import mock_data
import pytest
from app.services.geometry_task import (
    CloudFileMissingError,
    claim_task,
    execute_analysis,
    execute_claimed,
    is_terminal,
    progress_for_status,
    resolve_cloud_file,
    run_once,
    task_location,
)
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import Connection, text
from sqlalchemy.orm import Session, sessionmaker

COMPONENT_CODE = "B001-K03-PIR-P01-001"
GEOMETRY = "/api/v1/geometry/tasks"
BATCH = f"{GEOMETRY}/batch"


# ------------------------------------------------------------------ 纯逻辑（不依赖 DB）


def test_task_location_is_relative_query_path() -> None:
    task_id = uuid.uuid4()
    assert task_location(task_id) == f"/api/v1/geometry/tasks/{task_id}"


def test_progress_mapping_matches_task_status_enum() -> None:
    assert progress_for_status("QUEUED") == 0
    assert progress_for_status("RUNNING") == 30
    assert progress_for_status("SUCCEEDED") == 100
    assert progress_for_status("PENDING") == 0  # 未知状态不抛异常，只回退 0
    assert is_terminal("SUCCEEDED") and is_terminal("FAILED")
    assert not is_terminal("QUEUED") and not is_terminal("RUNNING")


def test_resolve_cloud_file_prefers_pointcloud_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("GEOMETRY_MOCK_DIR", str(tmp_path))
    cloud_id = uuid.uuid4()
    assert resolve_cloud_file(COMPONENT_CODE, cloud_id) == tmp_path / f"{cloud_id}.ply"
    assert resolve_cloud_file(COMPONENT_CODE, None) == tmp_path / f"{COMPONENT_CODE}.ply"


def test_execute_analysis_raises_when_cloud_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GEOMETRY_SYNTHETIC_FALLBACK", raising=False)
    with pytest.raises(CloudFileMissingError):
        execute_analysis({"analysis_type": "VERTICALITY"}, tmp_path / "absent.ply")


def test_synthetic_fallback_succeeds_for_verticality(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GEOMETRY_SYNTHETIC_FALLBACK", "1")
    outcome = execute_analysis({"analysis_type": "VERTICALITY"}, tmp_path / "absent.ply")

    assert outcome.synthetic is True
    assert outcome.unit == "permille"
    assert 0.0 < outcome.value < 20.0, outcome.value
    assert isinstance(outcome.detail["tilt_permille"], float)
    # 契约无 data_source 字段，数据来源用既有 degraded/degrade_reason 表达
    assert outcome.detail["degraded"] is True
    assert str(outcome.detail["degrade_reason"]).startswith("SYNTHETIC_FALLBACK")


def test_synthetic_fallback_does_not_apply_to_tunnel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GEOMETRY_SYNTHETIC_FALLBACK", "1")
    with pytest.raises(CloudFileMissingError):
        execute_analysis({"analysis_type": "CLEARANCE"}, tmp_path / "absent.ply")


# ------------------------------------------------------------------ DB 夹具


@pytest.fixture()
def component_code(session: Session, seeded: dict[str, uuid.UUID]) -> str:
    session.execute(
        text(
            "INSERT INTO component (id, project_id, struct_kind, bridge_id, component_code, "
            "component_name, level, component_type) VALUES "
            "(:id, :pid, 'BRIDGE', :bid, :code, '试验墩', 3, 'PIR')"
        ),
        {
            "id": str(uuid.uuid4()),
            "pid": str(seeded["project_id"]),
            "bid": str(seeded["bridge_id"]),
            "code": COMPONENT_CODE,
        },
    )
    session.flush()
    return COMPONENT_CODE


def _write_ascii_ply(path: Path, points: object) -> None:
    import numpy as np

    arr = np.asarray(points, dtype=np.float64)
    header = (
        "ply\nformat ascii 1.0\n"
        f"element vertex {arr.shape[0]}\n"
        "property float x\nproperty float y\nproperty float z\nend_header\n"
    )
    body = "\n".join(f"{x:.6f} {y:.6f} {z:.6f}" for x, y, z in arr)
    path.write_text(header + body + "\n", encoding="utf-8")


def _post(
    client: TestClient,
    code: str,
    analysis_type: str = "VERTICALITY",
    key: str | None = None,
) -> object:
    headers = {"Idempotency-Key": key} if key else {}
    return client.post(
        GEOMETRY,
        json={"component_code": code, "analysis_type": analysis_type},
        headers=headers,
    )


def _fresh_key() -> str:
    return f"idem-{uuid.uuid4().hex}"


# ------------------------------------------------------------------ 受理与即时性


def test_post_returns_202_with_location_and_task_id(
    client: TestClient, component_code: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GEOMETRY_TASK_INLINE", raising=False)
    resp = _post(client, component_code, key=_fresh_key())
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "QUEUED"
    assert uuid.UUID(body["task_id"])
    assert resp.headers["Location"] == f"{GEOMETRY}/{body['task_id']}"
    assert body["created_at"]


def test_post_returns_immediately_while_analysis_is_slow(
    client: TestClient, component_code: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GEOMETRY_TASK_INLINE", raising=False)
    called: list[str] = []

    def slow_analysis(payload: dict[str, object], path: Path) -> object:
        called.append(str(path))
        time.sleep(1.0)
        raise CloudFileMissingError(path)

    monkeypatch.setattr("app.services.geometry_task.execute_analysis", slow_analysis)
    started = time.perf_counter()
    resp = _post(client, component_code, key=_fresh_key())
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    assert resp.status_code == 202
    assert called == [], "主路径不应在 API 进程内执行几何计算"
    assert elapsed_ms < 200.0, f"实测 {elapsed_ms:.1f} ms，说明请求被计算阻塞"
    print(f"[geometry-api] 202 响应耗时 {elapsed_ms:.1f} ms（后台计算 1000 ms 未执行）")


def test_post_replays_same_idempotency_key(
    client: TestClient, component_code: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GEOMETRY_TASK_INLINE", raising=False)
    key = _fresh_key()
    first = _post(client, component_code, key=key)
    second = _post(client, component_code, key=key)
    assert first.status_code == second.status_code == 202
    assert first.json()["task_id"] == second.json()["task_id"]


def test_post_rejects_missing_idempotency_key(client: TestClient, component_code: str) -> None:
    resp = _post(client, component_code)
    assert resp.status_code == 400
    assert resp.json()["code"] == "BT-10002"


def test_post_rejects_invalid_analysis_type(client: TestClient, component_code: str) -> None:
    resp = _post(client, component_code, analysis_type="PLUMB", key=_fresh_key())
    assert resp.status_code == 422
    assert resp.json()["code"] == "BT-10003"


def test_post_returns_404_for_unknown_component(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GEOMETRY_TASK_INLINE", raising=False)
    resp = _post(client, "NOPE-001", key=_fresh_key())
    assert resp.status_code == 404
    assert resp.json()["code"] == "BT-30001"


def test_post_returns_422_for_cross_project_duplicate_code(
    client: TestClient, session: Session, seeded: dict[str, uuid.UUID], component_code: str
) -> None:
    second_project = uuid.uuid4()
    session.execute(
        text(
            "INSERT INTO project (id, project_code, project_name, calc_srid) "
            "VALUES (:pid, 'P002', '第二项目', 4547)"
        ),
        {"pid": str(second_project)},
    )
    session.execute(
        text(
            "INSERT INTO component (id, project_id, struct_kind, bridge_id, component_code, "
            "component_name, level, component_type) VALUES "
            "(:id, :pid, 'BRIDGE', :bid, :code, '重复编码墩', 3, 'PIR')"
        ),
        {
            "id": str(uuid.uuid4()),
            "pid": str(second_project),
            "bid": str(seeded["bridge_id"]),
            "code": component_code,
        },
    )
    session.flush()
    resp = _post(client, component_code, key=_fresh_key())
    assert resp.status_code == 422
    assert resp.json()["code"] == "BT-30003"


# ------------------------------------------------------------------ 批量流水线


def _batch_items(code: str, count: int = 10) -> list[dict[str, str]]:
    return [{"component_code": code, "analysis_type": "VERTICALITY"} for _ in range(count)]


def _post_batch(client: TestClient, items: list[dict[str, str]], key: str | None) -> Response:
    headers = {"Idempotency-Key": key} if key else {}
    return client.post(BATCH, json={"items": items}, headers=headers)


def test_batch_creates_ten_queued_tasks(
    client: TestClient, session: Session, component_code: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GEOMETRY_TASK_INLINE", raising=False)
    key = _fresh_key()
    resp = _post_batch(client, _batch_items(component_code), key)
    assert resp.status_code == 202
    items = resp.json()["items"]
    assert len(items) == 10
    assert len({item["task_id"] for item in items}) == 10
    assert all(item["status"] == "QUEUED" for item in items)

    count = session.execute(
        text("SELECT count(*) FROM async_task WHERE idem_key LIKE :prefix"),
        {"prefix": f"{key[:48]}-%"},
    ).scalar_one()
    assert count == 10, "一次批量请求必须在 async_task 生成 10 条记录"

    for item in items:
        detail = client.get(f"{GEOMETRY}/{item['task_id']}").json()
        assert detail["status"] == "QUEUED"


def test_batch_replays_same_idempotency_key(
    client: TestClient, component_code: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GEOMETRY_TASK_INLINE", raising=False)
    key = _fresh_key()
    first = _post_batch(client, _batch_items(component_code), key)
    second = _post_batch(client, _batch_items(component_code), key)
    assert first.status_code == second.status_code == 202
    assert [item["task_id"] for item in first.json()["items"]] == [
        item["task_id"] for item in second.json()["items"]
    ]


def test_batch_rejects_missing_idempotency_key(client: TestClient, component_code: str) -> None:
    resp = _post_batch(client, _batch_items(component_code), None)
    assert resp.status_code == 400
    assert resp.json()["code"] == "BT-10002"


def test_batch_rejects_unknown_component_without_partial_write(
    client: TestClient, session: Session, component_code: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GEOMETRY_TASK_INLINE", raising=False)
    key = _fresh_key()
    items = [
        {"component_code": component_code, "analysis_type": "VERTICALITY"},
        {"component_code": "NOPE-001", "analysis_type": "VERTICALITY"},
    ]
    resp = _post_batch(client, items, key)
    assert resp.status_code == 404
    assert resp.json()["code"] == "BT-30001"
    count = session.execute(
        text("SELECT count(*) FROM async_task WHERE idem_key LIKE :prefix"),
        {"prefix": f"{key[:48]}-%"},
    ).scalar_one()
    assert count == 0, "整批校验失败时不得留下半批脏数据"


def test_batch_rejects_more_than_fifty_items(client: TestClient, component_code: str) -> None:
    resp = _post_batch(client, _batch_items(component_code, count=51), _fresh_key())
    assert resp.status_code == 422
    assert resp.json()["code"] == "BT-10003"


def test_batch_and_single_tasks_do_not_interfere(
    client: TestClient, component_code: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GEOMETRY_TASK_INLINE", raising=False)
    single = _post(client, component_code, key=_fresh_key())
    batch = _post_batch(client, _batch_items(component_code), _fresh_key())
    assert single.status_code == 202
    assert batch.status_code == 202
    single_id = single.json()["task_id"]
    batch_ids = {item["task_id"] for item in batch.json()["items"]}
    assert single_id not in batch_ids
    assert len(batch_ids) == 10
    assert client.get(f"{GEOMETRY}/{single_id}").json()["status"] == "QUEUED"
    for task_id in batch_ids:
        assert client.get(f"{GEOMETRY}/{task_id}").json()["status"] == "QUEUED"


# ------------------------------------------------------------------ 查询与状态流转


def test_get_returns_404_for_unknown_task(client: TestClient) -> None:
    resp = client.get(f"{GEOMETRY}/{uuid.uuid4()}")
    assert resp.status_code == 404
    assert resp.json()["code"] == "BT-60003"


def test_get_returns_422_for_non_uuid(client: TestClient) -> None:
    resp = client.get(f"{GEOMETRY}/not-a-uuid")
    assert resp.status_code == 422
    assert resp.json()["code"] == "BT-10003"


def test_status_flow_queued_running_succeeded(
    client: TestClient,
    session: Session,
    component_code: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("GEOMETRY_TASK_INLINE", raising=False)
    monkeypatch.delenv("GEOMETRY_SYNTHETIC_FALLBACK", raising=False)
    monkeypatch.setenv("GEOMETRY_MOCK_DIR", str(tmp_path))
    # 关掉默认的中部遮挡：遮挡会走 M07 的 degraded 分支，垂直度不再等于 5‰ 真值
    cloud = mock_data.generate_pier_point_cloud(seed=42, n=20_000, occlusion_drop=0.0)
    _write_ascii_ply(tmp_path / f"{component_code}.ply", cloud)

    resp = _post(client, component_code, key=_fresh_key())
    task_id = resp.json()["task_id"]
    assert client.get(f"{GEOMETRY}/{task_id}").json()["status"] == "QUEUED"

    task_uuid = uuid.UUID(task_id)
    assert claim_task(session, task_uuid)
    session.flush()
    running = client.get(f"{GEOMETRY}/{task_id}").json()
    assert running["status"] == "RUNNING"
    assert running["progress"] == 30

    assert execute_claimed(session, task_uuid) == "SUCCEEDED"
    session.flush()
    done = client.get(f"{GEOMETRY}/{task_id}").json()
    assert done["status"] == "SUCCEEDED"
    assert done["progress"] == 100
    result = done["result"]
    for field in (
        "analysis_type",
        "tilt_permille",
        "degraded",
        "degrade_reason",
        "valid_layers",
        "z_bottom",
        "z_top",
        "residual_rms_mm",
    ):
        assert field in result
    assert done["error_code"] is None
    assert 4.0 <= result["tilt_permille"] <= 7.0, result["tilt_permille"]


def test_missing_cloud_marks_failed_with_bt_30005(
    client: TestClient,
    session: Session,
    component_code: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("GEOMETRY_TASK_INLINE", raising=False)
    monkeypatch.setenv("GEOMETRY_MOCK_DIR", str(tmp_path))  # 空目录，文件必缺
    monkeypatch.delenv("GEOMETRY_SYNTHETIC_FALLBACK", raising=False)

    resp = _post(client, component_code, key=_fresh_key())
    task_uuid = uuid.UUID(resp.json()["task_id"])
    assert execute_claimed(session, task_uuid) == "FAILED"
    session.flush()
    body = client.get(f"{GEOMETRY}/{resp.json()['task_id']}").json()
    assert body["status"] == "FAILED"
    assert body["error_code"] == "BT-30005"
    assert "点云文件不存在" in body["error_detail"]
    assert body["result"] is None


def test_synthetic_fallback_marks_succeeded_with_tilt(
    client: TestClient,
    session: Session,
    component_code: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """开关打开 + 无点云：VERTICALITY 走内存合成点云，任务 SUCCEEDED。"""
    monkeypatch.delenv("GEOMETRY_TASK_INLINE", raising=False)
    monkeypatch.setenv("GEOMETRY_MOCK_DIR", str(tmp_path))  # 空目录，真实点云必缺
    monkeypatch.setenv("GEOMETRY_SYNTHETIC_FALLBACK", "1")

    resp = _post(client, component_code, key=_fresh_key())
    task_uuid = uuid.UUID(resp.json()["task_id"])
    assert execute_claimed(session, task_uuid) == "SUCCEEDED"
    session.flush()

    body = client.get(f"{GEOMETRY}/{resp.json()['task_id']}").json()
    assert body["status"] == "SUCCEEDED"
    assert body["progress"] == 100
    assert body["error_code"] is None
    result = body["result"]
    assert isinstance(result["tilt_permille"], int | float)
    assert result["degraded"] is True
    assert "SYNTHETIC_FALLBACK" in result["degrade_reason"]


def test_inline_flag_schedules_background_task(
    client: TestClient, component_code: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GEOMETRY_TASK_INLINE", "1")
    seen: list[uuid.UUID] = []
    monkeypatch.setattr(
        "app.api.v1.geometry.run_task_now",
        lambda factory, task_id: seen.append(task_id) or "SUCCEEDED",
    )
    resp = _post(client, component_code, key=_fresh_key())
    assert resp.status_code == 202
    assert seen == [uuid.UUID(resp.json()["task_id"])]


def test_inline_disabled_by_default(
    client: TestClient, component_code: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GEOMETRY_TASK_INLINE", raising=False)
    seen: list[uuid.UUID] = []
    monkeypatch.setattr(
        "app.api.v1.geometry.run_task_now",
        lambda factory, task_id: seen.append(task_id) or "SUCCEEDED",
    )
    _post(client, component_code, key=_fresh_key())
    assert seen == []


def test_run_once_claims_one_queued_task(
    client: TestClient,
    db_connection: Connection,
    component_code: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("GEOMETRY_TASK_INLINE", raising=False)
    monkeypatch.setenv("GEOMETRY_MOCK_DIR", str(tmp_path))
    _write_ascii_ply(
        tmp_path / f"{component_code}.ply",
        mock_data.generate_pier_point_cloud(seed=42, n=20_000, occlusion_drop=0.0),
    )
    resp = _post(client, component_code, key=_fresh_key())
    factory = sessionmaker(
        bind=db_connection, join_transaction_mode="create_savepoint", expire_on_commit=False
    )
    claimed = run_once(factory)
    assert claimed == uuid.UUID(resp.json()["task_id"])
    assert client.get(f"{GEOMETRY}/{resp.json()['task_id']}").json()["status"] == "SUCCEEDED"
