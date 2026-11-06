"""M07 工单 API：派发（缺陷 + PENDING_CONFIRM）、班组查询、主管复核流转。

未设置 ``BT_TEST_DATABASE_URL`` 时整体 skip（沿用 conftest 约定，不回落生产库）。
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session


def _insert_user(session: Session, *, username: str, display_name: str) -> uuid.UUID:
    user_id = uuid.uuid4()
    session.execute(
        text(
            "INSERT INTO sys_user (id, username, password_hash, display_name, status) "
            "VALUES (:id, :username, 'test-hash', :display_name, 'ACTIVE')"
        ),
        {"id": user_id, "username": username, "display_name": display_name},
    )
    return user_id


def _insert_team(session: Session, project_id: uuid.UUID, leader_id: uuid.UUID) -> uuid.UUID:
    team_id = uuid.uuid4()
    session.execute(
        text(
            "INSERT INTO maintenance_team "
            "(id, project_id, team_code, team_name, leader_id, contact_phone, line_scope) "
            "VALUES (:id, :pid, :code, '测试养护班', :leader, '0571-00000000', 'B001 K01 跨')"
        ),
        {
            "id": team_id,
            "pid": project_id,
            "code": f"MT-{team_id.hex[:6].upper()}",
            "leader": leader_id,
        },
    )
    session.execute(
        text(
            "INSERT INTO maintenance_team_member (team_id, user_id, role_in_team) "
            "VALUES (:team, :user, 'LEADER')"
        ),
        {"team": team_id, "user": leader_id},
    )
    return team_id


def _insert_component(session: Session, project_id: uuid.UUID, bridge_id: uuid.UUID) -> str:
    code = "PIR-TEST-001"
    session.execute(
        text(
            "INSERT INTO component "
            "(id, project_id, struct_kind, bridge_id, parent_id, component_code, "
            " component_name, level, component_type, geom) "
            "VALUES (:id, :pid, 'BRIDGE', :bid, NULL, :code, '测试桥墩', 1, 'STRUCTURE', NULL)"
        ),
        {"id": uuid.uuid4(), "pid": project_id, "bid": bridge_id, "code": code},
    )
    return code


def _insert_task(
    session: Session,
    *,
    project_id: uuid.UUID,
    component_code: str,
    analysis_type: str,
    status: str,
    tilt: float | None,
) -> uuid.UUID:
    task_id = uuid.uuid4()
    result_id = uuid.uuid4()
    session.execute(
        text(
            "INSERT INTO async_task (id, project_id, biz_type, status, idem_key, payload) "
            "VALUES (:id, :pid, 'GEOMETRY', CAST(:status AS task_status), :idem, "
            " CAST(:payload AS jsonb))"
        ),
        {
            "id": task_id,
            "pid": project_id,
            "status": status,
            "idem": f"test-{task_id.hex}",
            "payload": json.dumps(
                {"component_code": component_code, "analysis_type": analysis_type}
            ),
        },
    )
    detail: dict[str, Any] = {
        "analysis_type": analysis_type,
        "degraded": False,
        "degrade_reason": None,
        "algorithm_version": "v1.0-tls",
    }
    if tilt is not None:
        detail.update({"tilt_permille": tilt, "value": tilt, "unit": "permille"})
    session.execute(
        text(
            "INSERT INTO geometry_result "
            "(id, project_id, task_id, component_code, analysis_type, unit, "
            " algorithm_version, params_hash, srid, detail_json) "
            "VALUES (:id, :pid, :tid, :code, CAST(:at AS analysis_type), 'permille', "
            " 'v1.0-tls', :hash, 4547, CAST(:detail AS jsonb))"
        ),
        {
            "id": result_id,
            "pid": project_id,
            "tid": task_id,
            "code": component_code,
            "at": analysis_type,
            "hash": "0" * 64,
            "detail": json.dumps(detail),
        },
    )
    session.execute(
        text("UPDATE async_task SET result_ref = :ref, progress = 100 WHERE id = :id"),
        {"ref": str(result_id), "id": task_id},
    )
    return task_id


@pytest.fixture()
def world(session: Session, seeded: dict[str, uuid.UUID]) -> dict[str, Any]:
    project_id = seeded["project_id"]
    leader = _insert_user(session, username="leader_wo", display_name="工单负责人")
    team_id = _insert_team(session, project_id, leader)
    code = _insert_component(session, project_id, seeded["bridge_id"])
    task_id = _insert_task(
        session,
        project_id=project_id,
        component_code=code,
        analysis_type="VERTICALITY",
        status="SUCCEEDED",
        tilt=4.0,
    )
    return {
        "project_id": project_id,
        "team_id": team_id,
        "leader_id": leader,
        "component_code": code,
        "task_id": task_id,
    }


def _dispatch(client: Any, world: dict[str, Any]) -> Any:
    return client.post(
        "/api/v1/workorders/dispatch",
        headers={"Idempotency-Key": "test-dispatch-permille"},
        json={
            "source_task_id": str(world["task_id"]),
            "component_code": world["component_code"],
            "priority": "P2",
            "team_id": str(world["team_id"]),
        },
    )


def test_dispatch_creates_pending_confirm_order(client: Any, world: dict[str, Any]) -> None:
    response = _dispatch(client, world)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "PENDING_CONFIRM"
    assert body["priority"] == "P2"
    assert body["suggested_level"] == "L2"
    assert body["threshold_note"] == "参考值，以最终人工复核及设计规范为准"
    assert body["source_snapshot"]["tilt_permille"] == 4.0
    assert body["source_snapshot"]["task_id"] == str(world["task_id"])
    assert body["team"]["team_id"] == str(world["team_id"])


def test_dispatch_rejects_duplicate_active_order(client: Any, world: dict[str, Any]) -> None:
    assert _dispatch(client, world).status_code == 201
    second = _dispatch(client, world)
    assert second.status_code == 409
    assert second.json()["code"] == "BT-70002"


def test_dispatch_rejects_unsucceeded_task(
    client: Any, session: Session, world: dict[str, Any]
) -> None:
    session.execute(
        text("UPDATE async_task SET status = 'RUNNING' WHERE id = :id"),
        {"id": world["task_id"]},
    )
    response = _dispatch(client, world)
    assert response.status_code == 409
    assert response.json()["code"] == "BT-70003"


def test_dispatch_rejects_unsupported_analysis(
    client: Any, session: Session, world: dict[str, Any]
) -> None:
    task_id = _insert_task(
        session,
        project_id=world["project_id"],
        component_code=world["component_code"],
        analysis_type="CLEARANCE",
        status="SUCCEEDED",
        tilt=None,
    )
    response = client.post(
        "/api/v1/workorders/dispatch",
        headers={"Idempotency-Key": "test-dispatch-clearance"},
        json={
            "source_task_id": str(task_id),
            "component_code": world["component_code"],
            "priority": "P1",
            "team_id": str(world["team_id"]),
        },
    )
    assert response.status_code == 422
    assert response.json()["code"] == "BT-70004"


def test_dispatch_requires_team_out_of_scope(
    client: Any, session: Session, seeded: dict[str, uuid.UUID], world: dict[str, Any]
) -> None:
    other_project = uuid.uuid4()
    session.execute(
        text(
            "INSERT INTO project (id, project_code, project_name, calc_srid) "
            "VALUES (:id, 'P002', '另一个项目', 4547)"
        ),
        {"id": other_project},
    )
    leader = _insert_user(session, username="leader_other", display_name="他项目负责人")
    other_team = _insert_team(session, other_project, leader)
    response = client.post(
        "/api/v1/workorders/dispatch",
        headers={"Idempotency-Key": "test-dispatch-scope"},
        json={
            "source_task_id": str(world["task_id"]),
            "component_code": world["component_code"],
            "priority": "P2",
            "team_id": str(other_team),
        },
    )
    assert response.status_code == 403
    assert response.json()["code"] == "BT-70005"


def test_supervisor_approve_then_assign(
    client: Any, session: Session, world: dict[str, Any]
) -> None:
    created = _dispatch(client, world)
    assert created.status_code == 201
    order_id = created.json()["id"]

    approved = client.post(
        f"/api/v1/workorders/{order_id}/transitions",
        json={"event": "APPROVE", "expected_version": 0},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "CONFIRMED"
    assert approved.json()["version"] == 1

    assigned = client.post(
        f"/api/v1/workorders/{order_id}/transitions",
        json={
            "event": "ASSIGN",
            "expected_version": 1,
            "payload": {"handler_id": str(world["leader_id"])},
        },
    )
    assert assigned.status_code == 200, assigned.text
    assert assigned.json()["status"] == "ASSIGNED"


def test_assign_cannot_jump_from_pending_confirm(client: Any, world: dict[str, Any]) -> None:
    created = _dispatch(client, world)
    order_id = created.json()["id"]
    response = client.post(
        f"/api/v1/workorders/{order_id}/transitions",
        json={"event": "ASSIGN", "expected_version": 0},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "BT-70001"


def test_list_teams_returns_seeded_team(client: Any, world: dict[str, Any]) -> None:
    response = client.get(
        "/api/v1/maintenance/teams", params={"component_code": world["component_code"]}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    codes = [item["team_code"] for item in body["items"]]
    assert any(code.startswith("MT-") for code in codes)
    assert body["items"][0]["leader_name"] == "工单负责人"


def test_threshold_dictionary_endpoint(client: Any) -> None:
    response = client.get("/api/v1/dict/defect-level-thresholds")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["tilt_permille"]["L1"] == 5.0
    assert body["tilt_permille"]["L2"] == 3.0
    assert body["note"].startswith("参考值")


def test_order_not_found(client: Any) -> None:
    response = client.get(f"/api/v1/workorders/{uuid.uuid4()}")
    assert response.status_code == 404
    assert response.json()["code"] == "BT-70008"
