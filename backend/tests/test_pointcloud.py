"""M04 点云准备阶段测试：Range 解析、流式读取、tileset 结构、路径安全。

占位文件是 1 MiB 确定性字节序列（不是真实 COPC），只用于验证 Range 语义与
3D Tiles 结构；真实 ``.copc.laz`` 待 PDAL worker 接入后替换。
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from app.core.errors import BizError
from app.models.component import StructKind
from app.schemas.component import ComponentCreate
from app.services.component import ComponentService
from app.services.pointcloud import (
    CHUNK_SIZE,
    RangeNotSatisfiableError,
    build_tileset,
    iter_file_chunks,
    parse_range,
    resolve_artifact,
)
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

SIZE = 1 << 20  # 1 MiB
PATTERN = bytes(range(256))
BLOB = (PATTERN * (SIZE // len(PATTERN) + 1))[:SIZE]


@pytest.fixture()
def mock_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "mock_pointcloud"
    root.mkdir()
    monkeypatch.setenv("POINTCLOUD_MOCK_DIR", str(root))
    return root


@pytest.fixture()
def component_id(session: Session, seeded: dict[str, uuid.UUID]) -> uuid.UUID:
    comp = ComponentService(session).create_component(
        ComponentCreate(
            level=1,
            project_id=seeded["project_id"],
            struct_kind=StructKind.BRIDGE,
            bridge_id=seeded["bridge_id"],
            component_type="PIR",
            component_name="点云测试构件",
            code_prefix="PC001",
            lon=116.397,
            lat=39.909,
            z=48.5,
        )
    )
    return comp.id


def _write_mock(root: Path, component_id: uuid.UUID) -> Path:
    path = root / f"{component_id}.copc.laz"
    path.write_bytes(BLOB)
    return path


# --------------------------------------------------------------- Range 解析（纯函数）


@pytest.mark.parametrize(
    ("header", "total", "expected"),
    [
        ("bytes=0-1023", 4096, (0, 1023)),
        ("bytes=1024-2047", 4096, (1024, 2047)),
        ("bytes=1024-", 4096, (1024, 4095)),
        ("bytes=-16", 1024, (1008, 1023)),
        ("bytes=-2048", 1024, (0, 1023)),  # 后缀大于文件 → 全量
        ("bytes=0-9999", 1024, (0, 1023)),  # end 越界 → 截到末字节
        ("Bytes=0-1", 8, (0, 1)),  # 单位大小写不敏感
    ],
)
def test_parse_range_ok(header: str, total: int, expected: tuple[int, int]) -> None:
    assert parse_range(header, total) == expected


@pytest.mark.parametrize(
    "header",
    ["bytes=0-1,3-4", "bytes=10-5", "bytes=5000-", "bytes=-0", "bytes=abc-def", "items=0-1"],
)
def test_parse_range_rejects(header: str) -> None:
    with pytest.raises(RangeNotSatisfiableError):
        parse_range(header, 1024)


def test_parse_range_rejects_empty_file() -> None:
    with pytest.raises(RangeNotSatisfiableError):
        parse_range("bytes=0-0", 0)


def test_iter_file_chunks_is_chunked(tmp_path: Path) -> None:
    path = tmp_path / "blob.copc.laz"
    path.write_bytes(BLOB)
    chunks = list(iter_file_chunks(path, 0, SIZE))
    assert sum(len(chunk) for chunk in chunks) == SIZE
    assert all(len(chunk) <= CHUNK_SIZE for chunk in chunks)
    assert b"".join(chunks) == BLOB
    assert b"".join(iter_file_chunks(path, 1000, 5000)) == BLOB[1000:6000]


# --------------------------------------------------------------- 路径安全


def test_resolve_artifact_blocks_traversal(tmp_path: Path) -> None:
    root = tmp_path
    root.mkdir(exist_ok=True)
    with pytest.raises(BizError) as exc:
        resolve_artifact("../../etc/passwd", root)
    assert exc.value.code == "BT-30005"
    assert resolve_artifact(str(uuid.uuid4()), root).parent == root


# --------------------------------------------------------------- API-08：Range


def test_range_206_headers_and_bytes(
    client: TestClient, component_id: uuid.UUID, mock_dir: Path
) -> None:
    _write_mock(mock_dir, component_id)
    resp = client.get(
        f"/api/v1/pointcloud/{component_id}/range", headers={"Range": "bytes=0-65535"}
    )
    assert resp.status_code == 206
    assert resp.headers["content-range"] == f"bytes 0-65535/{SIZE}"
    assert resp.headers["content-length"] == "65536"
    assert resp.headers["accept-ranges"] == "bytes"
    assert resp.headers["etag"].startswith('"')
    assert resp.headers["last-modified"]
    assert resp.headers["content-type"].startswith("application/octet-stream")
    assert resp.content == BLOB[0:65536]


def test_range_suffix_returns_tail(
    client: TestClient, component_id: uuid.UUID, mock_dir: Path
) -> None:
    _write_mock(mock_dir, component_id)
    resp = client.get(f"/api/v1/pointcloud/{component_id}/range", headers={"Range": "bytes=-4096"})
    assert resp.status_code == 206
    assert resp.headers["content-range"] == f"bytes {SIZE - 4096}-{SIZE - 1}/{SIZE}"
    assert resp.content == BLOB[-4096:]


def test_range_without_header_returns_full_200(
    client: TestClient, component_id: uuid.UUID, mock_dir: Path
) -> None:
    _write_mock(mock_dir, component_id)
    resp = client.get(f"/api/v1/pointcloud/{component_id}/range")
    assert resp.status_code == 200
    assert resp.headers["content-length"] == str(SIZE)
    assert resp.headers["accept-ranges"] == "bytes"
    assert resp.content == BLOB


@pytest.mark.parametrize("header", ["bytes=9999999-", "bytes=0-1,3-4", "bytes=10-5"])
def test_range_416_has_content_range(
    client: TestClient, component_id: uuid.UUID, mock_dir: Path, header: str
) -> None:
    _write_mock(mock_dir, component_id)
    resp = client.get(f"/api/v1/pointcloud/{component_id}/range", headers={"Range": header})
    assert resp.status_code == 416
    assert resp.headers["content-range"] == f"bytes */{SIZE}"


def test_range_missing_component_returns_404(client: TestClient) -> None:
    resp = client.get(f"/api/v1/pointcloud/{uuid.uuid4()}/range")
    assert resp.status_code == 404
    assert resp.json()["code"] == "BT-30001"


def test_range_missing_file_returns_404(
    client: TestClient, component_id: uuid.UUID, mock_dir: Path
) -> None:
    resp = client.get(f"/api/v1/pointcloud/{component_id}/range")
    assert resp.status_code == 404
    assert resp.json()["code"] == "BT-30005"


def test_range_non_uuid_returns_422_problem_json(client: TestClient) -> None:
    resp = client.get("/api/v1/pointcloud/not-a-uuid/range", headers={"Range": "bytes=0-1"})
    assert resp.status_code == 422
    assert resp.headers["content-type"].startswith("application/problem+json")
    body = resp.json()
    assert body["code"] == "BT-10003"
    assert body["errors"][0]["field"] == "path.componentId"
    assert body["errors"][0]["type"] == "uuid_parsing"


def test_range_path_traversal_component_id_rejected(client: TestClient) -> None:
    # 含 "/" 的穿越串根本无法匹配单段路径参数 → 404，不会触达文件系统；
    # 真正的白名单校验见 test_resolve_artifact_blocks_traversal
    resp = client.get("/api/v1/pointcloud/..%2F..%2Fetc%2Fpasswd/range")
    assert resp.status_code == 404
    assert resp.text == '{"detail":"Not Found"}'


# --------------------------------------------------------------- API-07：tileset


def test_tileset_structure(client: TestClient, component_id: uuid.UUID) -> None:
    body = client.get(f"/api/v1/pointcloud/{component_id}/tileset").json()
    assert body["asset"]["version"] == "1.1"
    assert isinstance(body["geometricError"], float)
    root = body["root"]
    assert root["refine"] in {"ADD", "REPLACE", "MERGE"}
    box = root["boundingVolume"]["box"]
    assert len(box) == 12
    assert all(isinstance(value, int | float) for value in box)
    # 中心取构件站位的 4490 经纬度（Mock 用局部 ENU，非 ECEF）
    assert box[0] == pytest.approx(116.397, abs=1e-6)
    assert box[1] == pytest.approx(39.909, abs=1e-6)
    assert box[2] == pytest.approx(48.5, abs=1e-6)
    uri = root["content"]["uri"]
    assert uri == f"/api/v1/pointcloud/{component_id}/range"


def test_tileset_placeholder_box_when_no_geom(
    client: TestClient, session: Session, seeded: dict[str, uuid.UUID]
) -> None:
    comp = ComponentService(session).create_component(
        ComponentCreate(
            level=1,
            project_id=seeded["project_id"],
            struct_kind=StructKind.BRIDGE,
            bridge_id=seeded["bridge_id"],
            component_type="PIR",
            component_name="无坐标构件",
            code_prefix="PC002",
        )
    )
    body = client.get(f"/api/v1/pointcloud/{comp.id}/tileset").json()
    assert len(body["root"]["boundingVolume"]["box"]) == 12
    assert body["root"]["boundingVolume"]["box"][:3] == [0.0, 0.0, 0.0]


def test_tileset_missing_component_returns_404(client: TestClient) -> None:
    resp = client.get(f"/api/v1/pointcloud/{uuid.uuid4()}/tileset")
    assert resp.status_code == 404
    assert resp.json()["code"] == "BT-30001"


def test_tileset_non_uuid_returns_422(client: TestClient) -> None:
    resp = client.get("/api/v1/pointcloud/not-a-uuid/tileset")
    assert resp.status_code == 422
    assert resp.json()["code"] == "BT-10003"


def test_build_tileset_is_pure() -> None:
    body = build_tileset("cid", (1.0, 2.0, 3.0))
    assert body["root"]["boundingVolume"]["box"][:3] == [1.0, 2.0, 3.0]
