"""M03 资产台账（构件）测试。

运行方式与共享夹具（engine/session/client/seeded）见 ``tests/conftest.py``；
需要 ``BT_TEST_DATABASE_URL`` 指向 PostGIS 测试库，否则数据库相关用例整体 skip。

并发用例必须真实 commit（多线程各自独立 Session），因此用真实 PG 连接池而非 sqlite：
sqlite 没有 ON CONFLICT / 行级锁 / PostGIS，测不出取号竞态。
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor

import pytest
from app.core.errors import BizError
from app.models.component import PierType, StructKind
from app.schemas.component import ComponentCreate
from app.services.component import ComponentService, build_component_code, next_sequence
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session


def _make(seeded: dict[str, uuid.UUID], **kwargs: object) -> ComponentCreate:
    """默认按桥构件建档；L1 自动补齐归属字段，L2–L4 只给 parent_id。"""
    level = int(kwargs.pop("level", 1))  # type: ignore[arg-type]
    fields: dict[str, object] = {
        "level": level,
        "component_type": kwargs.pop("component_type", "PIR"),
        "code_prefix": kwargs.pop("code_prefix", "B001"),
        "component_name": kwargs.pop("component_name", "构件"),
    }
    if level == 1:
        fields.update(
            project_id=seeded["project_id"],
            struct_kind=StructKind.BRIDGE,
            bridge_id=seeded["bridge_id"],
        )
    fields.update(kwargs)
    return ComponentCreate(**fields)  # type: ignore[arg-type]


# --------------------------------------------------------------- 纯函数（不依赖数据库）


def test_build_component_code() -> None:
    assert build_component_code("B001-K01-PIR", 1) == "B001-K01-PIR-001"
    assert build_component_code("B001-K01-PIR", 12) == "B001-K01-PIR-012"
    assert build_component_code("B001-K01-PIR", 999) == "B001-K01-PIR-999"
    with pytest.raises(ValueError):
        build_component_code("B001", 0)
    # 超 999 不扩位（扩位会破坏按编码排序与台账人工核对），由调用方转 409 BT-30004
    with pytest.raises(ValueError):
        build_component_code("B001-K01-PIR", 1000)


def test_next_sequence_by_prefix() -> None:
    codes = ["B001-K01-PIR-001", "B001-K01-PIR-003", "B001-K01-SPN-002", "B001-K01-PIR-P01-001"]
    assert next_sequence(codes, "B001-K01-PIR") == 4
    assert next_sequence([], "B001-K01-PIR") == 1
    assert next_sequence(codes, "B001-K01-PIR-P01") == 2


# --------------------------------------------------------------- 仓储 + 服务


def test_create_l1_l2_l3_chain(session: Session, seeded: dict[str, uuid.UUID]) -> None:
    svc = ComponentService(session)

    l1 = svc.create_component(_make(seeded, level=1, code_prefix="B001", component_type="BRG"))
    assert l1.level == 1
    assert l1.component_code == "B001-001"
    assert l1.parent_id is None
    assert l1.bridge_id == seeded["bridge_id"]

    l2 = svc.create_component(
        _make(seeded, level=2, parent_id=l1.id, code_prefix="B001-K01", component_type="SPN")
    )
    assert l2.level == 2
    assert l2.parent_id == l1.id
    assert l2.component_code == "B001-K01-001"
    # 子节点继承父节点归属
    assert l2.project_id == l1.project_id
    assert l2.bridge_id == l1.bridge_id

    l3 = svc.create_component(
        _make(
            seeded,
            level=3,
            parent_id=l2.id,
            code_prefix="B001-K01-PIR",
            pier_type=PierType.RECT,
            lon=116.397,
            lat=39.909,
            z=48.5,
        )
    )
    assert l3.level == 3
    assert l3.parent_id == l2.id
    assert l3.component_code == "B001-K01-PIR-001"
    assert l3.pier_type == PierType.RECT

    # 同一前缀再建一个，流水号 +1
    l3b = svc.create_component(
        _make(seeded, level=3, parent_id=l2.id, code_prefix="B001-K01-PIR", component_type="PIR")
    )
    assert l3b.component_code == "B001-K01-PIR-002"


def test_parent_missing_returns_404(session: Session, seeded: dict[str, uuid.UUID]) -> None:
    svc = ComponentService(session)
    with pytest.raises(BizError) as err:
        svc.create_component(
            _make(
                seeded,
                level=2,
                parent_id=uuid.uuid4(),
                code_prefix="B001-K09",
                component_type="SPN",
            )
        )
    assert err.value.code == "BT-30001"
    assert err.value.status == 404


def test_level_must_be_parent_plus_one(session: Session, seeded: dict[str, uuid.UUID]) -> None:
    svc = ComponentService(session)
    l1 = svc.create_component(_make(seeded, level=1, code_prefix="B001"))
    with pytest.raises(BizError) as err:
        svc.create_component(_make(seeded, level=3, parent_id=l1.id, code_prefix="B001-K01"))
    assert err.value.code == "BT-30003"
    assert err.value.status == 422


def test_duplicate_component_code_conflict(session: Session, seeded: dict[str, uuid.UUID]) -> None:
    svc = ComponentService(session)
    svc.create_component(
        _make(seeded, level=1, code_prefix="B001", component_code="B001-K01-PIR-001")
    )
    with pytest.raises(BizError) as err:
        svc.create_component(
            _make(seeded, level=1, code_prefix="B001", component_code="B001-K01-PIR-001")
        )
    assert err.value.code == "BT-30002"
    assert err.value.status == 409
    assert err.value.errors[0]["field"] == "component_code"


def test_soft_delete_is_idempotent(session: Session, seeded: dict[str, uuid.UUID]) -> None:
    svc = ComponentService(session)
    comp = svc.create_component(_make(seeded, level=1, code_prefix="B001"))

    svc.delete_component(comp.component_code)
    svc.delete_component(comp.component_code)  # 重复删除幂等，不抛 404

    assert svc.repo.get_by_code(comp.component_code) is None
    deleted = svc.repo.get_by_code(comp.component_code, include_deleted=True)
    assert deleted is not None
    assert deleted.deleted_at is not None
    with pytest.raises(BizError) as err:
        svc.get_component(comp.component_code)
    assert err.value.status == 404


# --------------------------------------------------------------- API


def _seed_chain(session: Session, seeded: dict[str, uuid.UUID]) -> tuple[str, uuid.UUID]:
    svc = ComponentService(session)
    l1 = svc.create_component(_make(seeded, level=1, code_prefix="B001", component_type="BRG"))
    l2 = svc.create_component(
        _make(seeded, level=2, parent_id=l1.id, code_prefix="B001-K01", component_type="SPN")
    )
    for _ in range(3):
        svc.create_component(
            _make(
                seeded, level=3, parent_id=l2.id, code_prefix="B001-K01-PIR", component_type="PIR"
            )
        )
    return l1.component_code, l2.id


def test_list_pagination_and_filters(
    session: Session, seeded: dict[str, uuid.UUID], client: TestClient
) -> None:
    _seed_chain(session, seeded)

    first = client.get("/api/v1/components", params={"struct_code": "B001", "level": 3, "limit": 2})
    assert first.status_code == 200
    body = first.json()
    assert [item["component_code"] for item in body["items"]] == [
        "B001-K01-PIR-001",
        "B001-K01-PIR-002",
    ]
    assert body["next_cursor"]

    second = client.get(
        "/api/v1/components",
        params={"struct_code": "B001", "level": 3, "limit": 2, "cursor": body["next_cursor"]},
    )
    assert second.status_code == 200
    body2 = second.json()
    assert [item["component_code"] for item in body2["items"]] == ["B001-K01-PIR-003"]
    assert body2["next_cursor"] is None

    # 类型过滤：SPN 只有 1 条
    by_type = client.get("/api/v1/components", params={"struct_code": "B001", "type": "SPN"})
    assert by_type.status_code == 200
    assert [item["component_type"] for item in by_type.json()["items"]] == ["SPN"]

    # 稳定排序：level asc 后 component_code asc
    all_items = client.get("/api/v1/components", params={"struct_code": "B001", "limit": 100})
    keys = [(item["level"], item["component_code"]) for item in all_items.json()["items"]]
    assert keys == sorted(keys)


def test_detail_geometry_is_4490_degrees(
    session: Session, seeded: dict[str, uuid.UUID], client: TestClient
) -> None:
    svc = ComponentService(session)
    comp = svc.create_component(
        _make(
            seeded,
            level=1,
            code_prefix="B001",
            component_type="BRG",
            pier_type=PierType.ROUND_END,
            lon=116.397,
            lat=39.909,
            z=48.5,
        )
    )

    resp = client.get(f"/api/v1/components/{comp.component_code}")
    assert resp.status_code == 200
    body = resp.json()

    assert body["pier_type"] == "ROUND_END"
    assert "geom_solid" in body and body["geom_solid"] is None
    assert body["geom"]["type"] == "Point"
    lon, lat, z = body["geom"]["coordinates"]
    # 关键断言：返回的是 4490 经纬度，不是 4547 的米制坐标
    assert -180.0 <= lon <= 180.0
    assert -90.0 <= lat <= 90.0
    assert abs(z - 48.5) < 1e-6
    assert lon == pytest.approx(116.397, abs=1e-6)
    assert lat == pytest.approx(39.909, abs=1e-6)
    assert body["geom_srid"] == 4490
    assert body["srid"] == 4547


def test_detail_not_found_returns_problem_json(client: TestClient) -> None:
    resp = client.get("/api/v1/components/B001-K99-UNKNOWN")
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/problem+json")
    body = resp.json()
    assert body["code"] == "BT-30001"
    assert body["traceId"]
    assert {"type", "title", "status", "detail"} <= set(body)


def test_limit_over_100_returns_422(
    session: Session, seeded: dict[str, uuid.UUID], client: TestClient
) -> None:
    _seed_chain(session, seeded)
    resp = client.get("/api/v1/components", params={"struct_code": "B001", "limit": 200})
    assert resp.status_code == 422


def test_requires_auth_without_dev_bypass(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("BT_DEV_ANONYMOUS", raising=False)
    resp = client.get("/api/v1/components", params={"struct_code": "B001"})
    assert resp.status_code == 401
    assert resp.json()["code"] == "BT-10001"
    assert resp.headers["WWW-Authenticate"] == "Bearer"


# --------------------------------------------------------------- 第二轮：POST / DELETE


def test_post_create_l1_l2_l3_over_http(seeded: dict[str, uuid.UUID], client: TestClient) -> None:
    l1 = client.post(
        "/api/v1/components",
        json={
            "level": 1,
            "project_id": str(seeded["project_id"]),
            "struct_kind": "BRIDGE",
            "bridge_id": str(seeded["bridge_id"]),
            "component_type": "BRG",
            "component_name": "示例桥",
            "code_prefix": "B001",
        },
    )
    assert l1.status_code == 201, l1.text
    l1_body = l1.json()
    assert l1_body["component_code"] == "B001-001"
    assert l1_body["level"] == 1
    assert l1_body["parent_id"] is None
    assert l1_body["geom"] is None and l1_body["geom_solid"] is None

    l2 = client.post(
        "/api/v1/components",
        json={
            "level": 2,
            "parent_id": l1_body["id"],
            "component_type": "SPN",
            "component_name": "1 号墩",
            "code_prefix": "B001-K01",
        },
    )
    assert l2.status_code == 201, l2.text
    l2_body = l2.json()
    assert l2_body["component_code"] == "B001-K01-001"
    assert l2_body["parent_id"] == l1_body["id"]

    l3 = client.post(
        "/api/v1/components",
        json={
            "level": 3,
            "parent_id": l2_body["id"],
            "component_type": "PIR",
            "component_name": "墩身",
            "code_prefix": "B001-K01-PIR",
            "pier_type": "RECT",
            "lon": 116.397,
            "lat": 39.909,
            "z": 48.5,
        },
    )
    assert l3.status_code == 201, l3.text
    l3_body = l3.json()
    assert l3_body["component_code"] == "B001-K01-PIR-001"
    assert l3_body["pier_type"] == "RECT"
    assert l3_body["geom_srid"] == 4490
    lon, lat, _z = l3_body["geom"]["coordinates"]
    assert lon == pytest.approx(116.397, abs=1e-6)
    assert lat == pytest.approx(39.909, abs=1e-6)


def test_post_duplicate_code_returns_409(seeded: dict[str, uuid.UUID], client: TestClient) -> None:
    payload = {
        "level": 1,
        "project_id": str(seeded["project_id"]),
        "struct_kind": "BRIDGE",
        "bridge_id": str(seeded["bridge_id"]),
        "component_type": "BRG",
        "component_name": "重名构件",
        "code_prefix": "B001",
        "component_code": "B001-001",
    }
    assert client.post("/api/v1/components", json=payload).status_code == 201
    dup = client.post("/api/v1/components", json=payload)
    assert dup.status_code == 409
    assert dup.headers["content-type"].startswith("application/problem+json")
    assert dup.json()["code"] == "BT-30002"
    assert dup.json()["errors"][0]["field"] == "component_code"


def test_post_parent_missing_returns_404(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/components",
        json={
            "level": 2,
            "parent_id": str(uuid.uuid4()),
            "component_type": "SPN",
            "component_name": "孤儿构件",
            "code_prefix": "B001-K99",
        },
    )
    assert resp.status_code == 404
    assert resp.json()["code"] == "BT-30001"


def test_delete_is_idempotent_over_http(seeded: dict[str, uuid.UUID], client: TestClient) -> None:
    created = client.post(
        "/api/v1/components",
        json={
            "level": 1,
            "project_id": str(seeded["project_id"]),
            "struct_kind": "BRIDGE",
            "bridge_id": str(seeded["bridge_id"]),
            "component_type": "BRG",
            "component_name": "待删构件",
            "code_prefix": "B001",
        },
    )
    assert created.status_code == 201
    code = created.json()["component_code"]

    first = client.delete(f"/api/v1/components/{code}")
    second = client.delete(f"/api/v1/components/{code}")
    assert first.status_code == 204
    assert second.status_code == 204
    assert first.content == b"" and second.content == b""

    after = client.get(f"/api/v1/components/{code}")
    assert after.status_code == 404
    assert after.json()["code"] == "BT-30001"

    missing = client.delete("/api/v1/components/B001-K99-UNKNOWN")
    assert missing.status_code == 404


def _walk_coords(coords: object) -> Iterator[list[float]]:
    """递归遍历 GeoJSON 任意嵌套坐标，产出最内层 [x, y, z] 数组。"""
    assert isinstance(coords, list)
    if coords and isinstance(coords[0], int | float):
        yield coords  # type: ignore[misc]
        return
    for child in coords:
        yield from _walk_coords(child)


def test_detail_geom_solid_null_then_multipolygon(
    seeded: dict[str, uuid.UUID], session: Session, client: TestClient
) -> None:
    created = client.post(
        "/api/v1/components",
        json={
            "level": 1,
            "project_id": str(seeded["project_id"]),
            "struct_kind": "BRIDGE",
            "bridge_id": str(seeded["bridge_id"]),
            "component_type": "BRG",
            "component_name": "实体几何构件",
            "code_prefix": "B001",
        },
    )
    assert created.status_code == 201
    code = created.json()["component_code"]

    # 未造实体几何 → null
    none_body = client.get(f"/api/v1/components/{code}").json()
    assert none_body["geom_solid"] is None

    # 造一条真实 PolyhedralSurfaceZ（4547 = CGCS2000 3 度带 CM 114E，x=500000 → lon≈114）
    ewkt = (
        "SRID=4547;POLYHEDRALSURFACE Z ("
        "((499995 4399995 48, 500005 4399995 48, 500005 4400005 48, "
        "499995 4400005 48, 499995 4399995 48)), "
        "((499995 4399995 58, 500005 4399995 58, 500005 4400005 58, "
        "499995 4400005 58, 499995 4399995 58)))"
    )
    session.execute(
        text(
            "UPDATE component SET geom_solid = ST_GeomFromEWKT(:ewkt) "
            "WHERE component_code = :code"
        ),
        {"ewkt": ewkt, "code": code},
    )

    body = client.get(f"/api/v1/components/{code}").json()
    solid = body["geom_solid"]
    assert solid is not None
    assert solid["type"] == "MultiPolygon"
    assert len(solid["coordinates"]) == 2  # 两个立面
    for point in _walk_coords(solid["coordinates"]):
        x, y = point[0], point[1]
        assert -180.0 <= x <= 180.0
        assert -90.0 <= y <= 90.0
        assert x == pytest.approx(114.0, abs=0.5)
        assert y == pytest.approx(39.7, abs=0.5)


# --------------------------------------------------------------- 并发取号（真实 PG 会话）


def test_concurrent_create_same_prefix_is_unique(engine: Engine) -> None:
    """50 个并发任务对同一 (project, prefix) 建构件：编码必须互不重复且连续。

    这里刻意不用 ``session``/``db_connection`` 夹具：它们把用例包在一个外层事务里，
    多线程之间无法真实提交，也就测不出竞态。每个线程用独立 Session + 真实 commit，
    依赖的是 PostgreSQL 的 ON CONFLICT 与行级锁，sqlite 不具备这些语义。
    """
    prefix = "CC" + uuid.uuid4().hex[:8]
    project_id = uuid.uuid4()
    bridge_id = uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO project (id, project_code, project_name, calc_srid) "
                "VALUES (:pid, :code, '并发测试项目', 4547)"
            ),
            {"pid": str(project_id), "code": f"P{uuid.uuid4().hex[:8].upper()}"},
        )
        conn.execute(
            text(
                "INSERT INTO bridge (id, project_id, bridge_code, bridge_name, struct_type) "
                "VALUES (:bid, :pid, :bcode, '并发测试桥', 'BEAM')"
            ),
            {"bid": str(bridge_id), "pid": str(project_id), "bcode": f"B{uuid.uuid4().hex[:6]}"},
        )

    def _create_one(index: int) -> str:
        db = Session(engine, expire_on_commit=False)
        try:
            payload = ComponentCreate(
                level=1,
                project_id=project_id,
                struct_kind=StructKind.BRIDGE,
                bridge_id=bridge_id,
                component_type="BRG",
                component_name=f"并发构件 {index}",
                code_prefix=prefix,
            )
            return ComponentService(db).create_component(payload).component_code
        finally:
            db.close()

    try:
        with ThreadPoolExecutor(max_workers=10) as pool:
            codes = list(pool.map(_create_one, range(50)))

        assert len(codes) == 50
        assert len(set(codes)) == 50, "出现重复编码"
        assert sorted(codes) == [f"{prefix}-{i:03d}" for i in range(1, 51)], "流水号不连续"

        with engine.connect() as conn:
            row_count = conn.execute(
                text("SELECT count(*) FROM component WHERE project_id = :pid"),
                {"pid": str(project_id)},
            ).scalar_one()
            last_seq = conn.execute(
                text(
                    "SELECT last_seq FROM component_code_seq "
                    "WHERE project_id = :pid AND prefix = :prefix"
                ),
                {"pid": str(project_id), "prefix": prefix},
            ).scalar_one()
        assert row_count == 50
        assert last_seq == 50
    finally:
        # 真实落库，必须显式清理（component → bridge → project，计数表随 project 级联删除）
        with engine.begin() as conn:
            conn.execute(
                text("DELETE FROM component WHERE project_id = :pid"), {"pid": str(project_id)}
            )
            conn.execute(text("DELETE FROM bridge WHERE id = :bid"), {"bid": str(bridge_id)})
            conn.execute(text("DELETE FROM project WHERE id = :pid"), {"pid": str(project_id)})
