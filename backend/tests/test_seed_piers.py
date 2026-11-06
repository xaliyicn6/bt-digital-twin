"""10 桥墩种子 + 按构件派生倾斜率 + SUCCEEDED 回写 tech_grade。

覆盖本轮演示扩量需求：
- ``db/seeds/seed_piers_k01.sql`` 重复执行仍只有 10 条墩，初值覆盖 1–5 类；
- 同一构件种子稳定、10 个构件互不相等；``BT_SYNTHETIC_SEED`` 可整体覆盖；
- 1 号墩保持既有 4.562‰ 口径；其余 9 个覆盖 1–5 类且实测值各不相同；
- 任务 SUCCEEDED 在同一事务把实算等级回写 ``component.tech_grade``；
- 批量 10 个真实构件各自得到不同结果，逐任务接口可直接驱动前端表格。

数据库用例沿用 conftest 约定：未设 ``BT_TEST_DATABASE_URL`` 时整体 skip。
"""

from __future__ import annotations

import uuid
from pathlib import Path

import mock_data
import pytest
from app.services.geometry_task import (
    _synthetic_pier_cloud,
    _verticality,
    claim_task,
    execute_claimed,
)
from fastapi.testclient import TestClient
from sqlalchemy import Connection, text
from sqlalchemy.orm import Session

SEED_SQL = Path(__file__).resolve().parents[2] / "db" / "seeds" / "seed_piers_k01.sql"
GEOMETRY = "/api/v1/geometry/tasks"
BATCH = f"{GEOMETRY}/batch"
PIER_CODES = [f"PIR {i}号桥墩" for i in range(1, 11)]


def _fresh_key() -> str:
    return uuid.uuid4().hex


def _strip_transaction_wrapper(sql: str) -> str:
    """测试库用例跑在外层事务里，去掉脚本自带的 BEGIN;/COMMIT; 以保留回滚能力。"""
    return "\n".join(line for line in sql.splitlines() if line.strip() not in {"BEGIN;", "COMMIT;"})


def _run_seed(conn: Connection) -> None:
    conn.exec_driver_sql(_strip_transaction_wrapper(SEED_SQL.read_text(encoding="utf-8")))


# ------------------------------------------------------------------ 纯逻辑：派生种子与倾斜率


def test_component_seeds_are_deterministic_and_distinct() -> None:
    first = [mock_data.component_seed(code) for code in PIER_CODES]
    second = [mock_data.component_seed(code) for code in PIER_CODES]
    assert first == second, "同一构件必须永远得到同一合成种子"
    assert len(set(first)) == 10, "10 个构件不能共用同一随机流"

    overridden = [mock_data.component_seed(code, base_seed=7) for code in PIER_CODES]
    assert overridden != first, "BT_SYNTHETIC_SEED 必须能整体覆盖基础种子"
    # 1 号墩沿用基础种子（保留既有 4.562‰ 演示口径），其余构件按编码派生。
    assert mock_data.component_seed("PIR 1号桥墩", base_seed=7) == 7


def test_bt_synthetic_seed_env_overrides_base(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BT_SYNTHETIC_SEED", "123")
    assert mock_data.current_base_seed() == 123
    assert mock_data.component_seed("PIR 1号桥墩") == 123
    assert mock_data.component_tilt_ratio("PIR 1号桥墩") == mock_data.DEMO_TILT_RATIO

    monkeypatch.setenv("BT_SYNTHETIC_SEED", "not-a-number")
    with pytest.raises(ValueError, match="BT_SYNTHETIC_SEED"):
        mock_data.current_base_seed()


def test_ten_piers_yield_distinct_tilts_covering_all_grades() -> None:
    """用生产参数实算 10 个构件：1 号墩 4.562‰，其余互不相同且覆盖 1–5 类。"""
    measured: dict[str, tuple[float, int]] = {}
    for code in PIER_CODES:
        outcome = _verticality(_synthetic_pier_cloud(code), synthetic=True)
        measured[code] = (float(outcome.detail["tilt_permille"]), int(outcome.detail["tech_grade"]))

    assert measured["PIR 1号桥墩"][0] == pytest.approx(4.562, abs=0.01)
    tilts = [tilt for tilt, _ in measured.values()]
    assert len(set(tilts)) == 10, f"倾斜率必须互不相同：{tilts}"
    assert {grade for _, grade in measured.values()} == {1, 2, 3, 4, 5}
    # 5 类样本必须真实超过 5‰，而不是靠标签写死。
    five_class = [tilt for tilt, grade in measured.values() if grade == 5]
    assert five_class and all(tilt > 5.0 for tilt in five_class)


# ------------------------------------------------------------------ 数据库：种子幂等 / 回写 / 批量


def test_seed_piers_is_idempotent_and_covers_all_grades(db_connection: Connection) -> None:
    _run_seed(db_connection)
    _run_seed(db_connection)

    rows = db_connection.execute(
        text(
            "SELECT c.component_code, c.level, c.component_type, c.pier_type, c.tech_grade, "
            "c.struct_kind, parent.component_code AS parent_code "
            "FROM component c "
            "JOIN project p ON p.id = c.project_id "
            "LEFT JOIN component parent ON parent.id = c.parent_id "
            "WHERE p.project_code = 'DEMO-P001' AND c.level = 3 AND c.deleted_at IS NULL "
            "AND c.component_code IN ("
            "'PIR 1号桥墩', 'PIR 2号桥墩', 'PIR 3号桥墩', 'PIR 4号桥墩', 'PIR 5号桥墩', "
            "'PIR 6号桥墩', 'PIR 7号桥墩', 'PIR 8号桥墩', 'PIR 9号桥墩', 'PIR 10号桥墩')"
        )
    ).all()

    assert len(rows) == 10, f"重复执行种子后必须仍为 10 条墩，实际 {len(rows)}"
    assert {row.tech_grade for row in rows} == {1, 2, 3, 4, 5}
    for row in rows:
        assert row.level == 3
        assert row.component_type == "PIER"
        assert row.pier_type == "RECT"
        assert row.struct_kind == "BRIDGE"
        assert row.parent_code == "K01 第1跨"


@pytest.fixture()
def pier_component(session: Session, seeded: dict[str, uuid.UUID]) -> str:
    code = "PIR 1号桥墩"
    session.execute(
        text(
            "INSERT INTO component (id, project_id, struct_kind, bridge_id, component_code, "
            "component_name, level, component_type, tech_grade) VALUES "
            "(:id, :pid, 'BRIDGE', :bid, :code, :code, 3, 'PIER', 1)"
        ),
        {
            "id": str(uuid.uuid4()),
            "pid": str(seeded["project_id"]),
            "bid": str(seeded["bridge_id"]),
            "code": code,
        },
    )
    session.flush()
    return code


def test_succeeded_writes_back_tech_grade(
    client: TestClient,
    session: Session,
    pier_component: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("GEOMETRY_TASK_INLINE", raising=False)
    monkeypatch.setenv("GEOMETRY_MOCK_DIR", str(tmp_path))  # 空目录 → 走合成兜底
    monkeypatch.setenv("GEOMETRY_SYNTHETIC_FALLBACK", "1")

    resp = client.post(
        GEOMETRY,
        json={"component_code": pier_component, "analysis_type": "VERTICALITY"},
        headers={"Idempotency-Key": _fresh_key()},
    )
    assert resp.status_code == 202
    task_id = uuid.UUID(resp.json()["task_id"])
    assert claim_task(session, task_id)
    session.flush()
    assert execute_claimed(session, task_id) == "SUCCEEDED"
    session.flush()

    body = client.get(f"{GEOMETRY}/{task_id}").json()
    assert body["status"] == "SUCCEEDED"
    computed = body["result"]["tech_grade"]
    assert 1 <= computed <= 5

    stored = session.execute(
        text(
            "SELECT tech_grade FROM component "
            "WHERE component_code = :code AND deleted_at IS NULL"
        ),
        {"code": pier_component},
    ).scalar_one()
    assert stored == computed, "SUCCEEDED 必须把实算等级回写台账（同一事务）"


def test_batch_of_ten_piers_yields_distinct_results(
    client: TestClient,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """批量 10 个真实构件：逐行表格所需字段（状态/倾斜率/等级）各不相同。"""
    _run_seed(session.connection())
    session.flush()
    monkeypatch.delenv("GEOMETRY_TASK_INLINE", raising=False)
    monkeypatch.setenv("GEOMETRY_MOCK_DIR", str(tmp_path))
    monkeypatch.setenv("GEOMETRY_SYNTHETIC_FALLBACK", "1")

    resp = client.post(
        BATCH,
        json={
            "items": [
                {"component_code": code, "analysis_type": "VERTICALITY"} for code in PIER_CODES
            ]
        },
        headers={"Idempotency-Key": _fresh_key()},
    )
    assert resp.status_code == 202
    task_ids = [item["task_id"] for item in resp.json()["items"]]
    assert len(task_ids) == 10

    for task_id in task_ids:
        task_uuid = uuid.UUID(task_id)
        assert claim_task(session, task_uuid)
        session.flush()
        assert execute_claimed(session, task_uuid) == "SUCCEEDED"

    rows = []
    for code, task_id in zip(PIER_CODES, task_ids, strict=True):
        detail = client.get(f"{GEOMETRY}/{task_id}").json()
        assert detail["status"] == "SUCCEEDED"
        result = detail["result"]
        rows.append((code, result["tilt_permille"], result["tech_grade"]))

    assert len({tilt for _, tilt, _ in rows}) == 10, f"10 条任务不能是同一结果：{rows}"
    assert {grade for _, _, grade in rows} == {1, 2, 3, 4, 5}
    first = next(row for row in rows if row[0] == "PIR 1号桥墩")
    assert first[1] == pytest.approx(4.562, abs=0.01)

    # 回写后资产树读取的台账等级与批量结果逐构件一致。
    stored = {
        row.component_code: row.tech_grade
        for row in session.execute(
            text(
                "SELECT component_code, tech_grade FROM component "
                "WHERE component_code IN ("
                "'PIR 1号桥墩', 'PIR 2号桥墩', 'PIR 3号桥墩', 'PIR 4号桥墩', 'PIR 5号桥墩', "
                "'PIR 6号桥墩', 'PIR 7号桥墩', 'PIR 8号桥墩', 'PIR 9号桥墩', 'PIR 10号桥墩') "
                "AND deleted_at IS NULL"
            )
        )
    }
    for code, _, grade in rows:
        assert stored[code] == grade
