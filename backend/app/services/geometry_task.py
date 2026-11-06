"""M05 API-14：几何分析任务的受理、执行与结果落库（``async_task.biz_type='GEOMETRY'``）。

主路径（生产）：POST 只做校验 + 落 ``async_task``（``status='QUEUED'``）后立刻 202；
执行由独立 worker 进程 ``python -m app.workers.geometry_worker`` 用
``FOR UPDATE SKIP LOCKED`` 领取（状态迁移严格用 ``db/schema.sql`` 的 ``task_status``：
QUEUED → RUNNING → SUCCEEDED/FAILED）。``BackgroundTasks`` 只在
``GEOMETRY_TASK_INLINE=1`` 时兜底（进程内执行，重启即丢、无重试、无并发上限），**仅用于开发**。

点云来源（**临时联调约定，不是正式设计**）：
    ``GEOMETRY_MOCK_DIR``（默认 ``backend/var/mock_geometry``）下的
        ``{pointcloud_id}.ply``（请求带 pointcloud_id 时优先）
        ``{component_code}.ply``（否则）
    正式设计应走 ``pointcloud_file.object_key`` + COPC（需 PDAL/对象存储，本轮未接）。
    文件缺失时任务落 ``FAILED`` 且 ``error_code='BT-30005'``（点云文件不存在），不静默成功。

开发兜底（默认关闭）：
    ``GEOMETRY_SYNTHETIC_FALLBACK=1`` 且 ``analysis_type='VERTICALITY'`` 时，真实 PLY 缺失
    改用 ``tests/mock_data.generate_pier_point_cloud()`` 的内存合成点云；结果以
    ``degraded=true`` + ``degrade_reason='SYNTHETIC_FALLBACK…'`` 标记数据来源，
    并打一条带 traceId 的 warning 日志。未开启时行为与既有 BT-30005 完全一致。

``component.geom`` 只是 ``POINTZ`` 单点，本身不携带点云；上面的目录约定就是补这个缺口。
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.core.errors import api_error
from app.core.logging import bind_trace_id, get_logger
from app.services.geometry import InsufficientGeometryError, calculate_plumb
from app.services.tunnel import analyze_tunnel, load_tunnel_ply

BIZ_TYPE = "GEOMETRY"
ALGORITHM_VERSION = "v1.0-tls"
MOCK_DIR_ENV = "GEOMETRY_MOCK_DIR"
INLINE_ENV = "GEOMETRY_TASK_INLINE"
# 开发兜底开关：默认关闭；开启后仅 VERTICALITY 在点云缺失时使用合成点云。
SYNTHETIC_FALLBACK_ENV = "GEOMETRY_SYNTHETIC_FALLBACK"
API_PREFIX = "/api/v1"
MIN_CLOUD_POINTS = 100

log = get_logger()

_PROGRESS = {"QUEUED": 0, "RUNNING": 30, "SUCCEEDED": 100, "FAILED": 0, "RETRYING": 0}
_TERMINAL = frozenset({"SUCCEEDED", "FAILED", "CANCELLED", "TIMEOUT"})


class CloudFileMissingError(Exception):
    """点云文件不存在（映射到 BT-30005，任务落 FAILED）。"""

    def __init__(self, path: Path) -> None:
        super().__init__(f"点云文件不存在：{path}")
        self.path = path


@dataclass(frozen=True)
class AnalysisOutcome:
    """一次几何计算的产物：detail_json + 入库列。

    ``synthetic`` 仅用于内部日志与排查，不写入契约外的 API 字段；
    对外通过契约已定义的 ``degraded/degrade_reason`` 标注数据来源。
    """

    detail: dict[str, Any]
    value: float
    unit: str
    direction_deg: float | None
    valid_layers: int
    worst_section_stake: str | None
    synthetic: bool = False


def task_location(task_id: uuid.UUID) -> str:
    """``Location`` 头：指向 GET /geometry/tasks/{id}（相对路径，客户端按基址解析）。"""
    return f"{API_PREFIX}/geometry/tasks/{task_id}"


def progress_for_status(status: str) -> int:
    """状态 → 展示用进度（DDL 注释明确：progress 仅用于展示，不作为状态判据）。"""
    return _PROGRESS.get(status, 0)


def is_terminal(status: str) -> bool:
    return status in _TERMINAL


def mock_dir() -> Path:
    configured = os.environ.get(MOCK_DIR_ENV)
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[2] / "var" / "mock_geometry"


def resolve_cloud_file(component_code: str, pointcloud_id: uuid.UUID | None) -> Path:
    """按临时约定定位 PLY；找不到由调用方转成 BT-30005。"""
    name = f"{pointcloud_id}.ply" if pointcloud_id is not None else f"{component_code}.ply"
    return mock_dir() / name


def _sha256(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _verticality(cloud: npt.NDArray[np.float64], *, synthetic: bool = False) -> AnalysisOutcome:
    plumb = calculate_plumb(cloud)
    degraded = bool(plumb.degraded)
    degrade_reason = plumb.degrade_reason
    if synthetic:
        # 契约没有 data_source 字段；用既有 degraded/degrade_reason 表达"非实测数据"。
        degraded = True
        reason = "SYNTHETIC_FALLBACK：点云缺失，使用合成点云（开发兜底，结果不可用于验收）"
        degrade_reason = (
            f"{reason}；算法降级：{plumb.degrade_reason}" if plumb.degrade_reason else reason
        )
    detail: dict[str, Any] = {
        "analysis_type": "VERTICALITY",
        "tilt_permille": round(float(plumb.tilt_permille), 3),
        "horizontal_offset_mm": round(float(plumb.horizontal_offset_mm), 3),
        "direction_deg": None if plumb.azimuth_deg is None else round(float(plumb.azimuth_deg), 2),
        "degraded": degraded,
        "degrade_reason": degrade_reason,
        "valid_layers": int(plumb.valid_layers),
        "total_layers": int(plumb.total_layers),
        "z_bottom": round(float(plumb.z_bottom), 3),
        "z_top": round(float(plumb.z_top), 3),
        "residual_rms_mm": round(float(plumb.residual_rms_mm), 3),
        "cover_mean": round(float(plumb.cover_mean), 3),
        "value": round(float(plumb.tilt_permille), 3),
        "unit": "permille",
        "algorithm_version": ALGORITHM_VERSION,
    }
    direction = None if plumb.azimuth_deg is None else float(plumb.azimuth_deg)
    return AnalysisOutcome(
        detail,
        float(plumb.tilt_permille),
        "permille",
        direction,
        int(plumb.valid_layers),
        None,
        synthetic,
    )


def _tunnel(cloud: npt.NDArray[np.float64], analysis_type: str) -> AnalysisOutcome:
    result = analyze_tunnel(cloud)
    sections = result.sections
    if not sections:
        raise InsufficientGeometryError("隧道点云未能切出有效断面")
    radii = np.array([s.radius for s in sections], dtype=np.float64)
    clearances = np.array([s.min_clearance_mm for s in sections], dtype=np.float64)
    # 最不利断面 = 净空最小的一片（argmax 会挑到最宽松的一片，语义相反）
    worst = sections[int(np.argmin(clearances))]
    detail: dict[str, Any] = {
        "analysis_type": analysis_type,
        "radius_m": round(float(np.median(radii)), 3),
        "center_u_m": round(float(np.median([s.center_u for s in sections])), 3),
        "center_v_m": round(float(np.median([s.center_v for s in sections])), 3),
        "slice_count": len(sections),
        "total_slices": int(result.total_slices),
        "skipped_slices": int(result.skipped_slices),
        "axis": [round(float(v), 6) for v in result.axis],
        # API 字段名固定为 clearance_reference_mm：语义是“点到拟合圆弧的净空参考（估算）”，
        # 不是建筑限界侵限值；禁止在返回体里出现 limit_intrusion 之类命名。
        "clearance_reference_mm": round(float(clearances.min()), 3),
        "clearance_rms_mm": round(float(np.sqrt(np.mean(clearances**2))), 3),
        "inlier_ratio_mean": round(float(np.mean([s.inlier_ratio for s in sections])), 3),
        "degraded": False,
        "degrade_reason": None,
        "valid_layers": len(sections),
        # 隧道侧 z 取点云实际高程极值；t_center 是沿轴里程，不是高程，不能混用
        "z_bottom": round(float(cloud[:, 2].min()), 3),
        "z_top": round(float(cloud[:, 2].max()), 3),
        "residual_rms_mm": round(float(np.sqrt(np.mean(clearances**2))), 3),
        "algorithm_version": ALGORITHM_VERSION,
        "sections": [
            {
                "index": int(s.index),
                "t_m": round(float(s.t_center), 3),
                "radius_m": round(float(s.radius), 3),
                "center_u_m": round(float(s.center_u), 3),
                "center_v_m": round(float(s.center_v), 3),
                "inlier_ratio": round(float(s.inlier_ratio), 3),
                "clearance_reference_mm": round(float(s.min_clearance_mm), 3),
                "n_points": int(s.n_points),
            }
            for s in sections
        ],
    }
    if analysis_type == "CLEARANCE":
        detail["value"] = round(float(clearances.min()), 3)
        detail["unit"] = "mm"
        outcome_value = float(clearances.min())
        unit = "mm"
    else:
        detail["value"] = round(float(np.median(radii)) * 1000.0, 3)
        detail["unit"] = "mm"
        outcome_value = float(np.median(radii)) * 1000.0
        unit = "mm"
    stake = f"{worst.t_center:.3f}"
    return AnalysisOutcome(detail, outcome_value, unit, None, len(sections), stake)


def synthetic_fallback_enabled() -> bool:
    """``GEOMETRY_SYNTHETIC_FALLBACK=1`` 时允许 VERTICALITY 缺失点云时使用合成数据。

    默认关闭：关闭时保持原有行为（BT-30005 + FAILED），仅用于本地联调。
    """
    return os.environ.get(SYNTHETIC_FALLBACK_ENV) == "1"


def _synthetic_pier_cloud() -> npt.NDArray[np.float64]:
    """复用 ``tests/mock_data.py`` 的 ``generate_pier_point_cloud()``，不新建重复实现。

    该模块只在开发/测试环境存在；部署包未带 tests 时明确报错（最终落 FAILED），
    不会静默返回空点云。演示参数：``DEFAULT_SEED`` 固定种子、``DEMO_TILT_RATIO``
    4.5‰ 倾斜（实测约 4.562‰，建议 L2），n=200k、10 m 高、2 m 截面。
    """
    try:
        # pytest 会把 tests/ 目录加入 sys.path，此时模块名是 mock_data；
        # 以 backend 为工作目录运行时则是 tests.mock_data。两种都兼容，不复制实现。
        generator = None
        for module_name in ("tests.mock_data", "mock_data"):
            try:
                module = importlib.import_module(module_name)
            except ImportError:
                continue
            generator = getattr(module, "generate_pier_point_cloud", None)
            if generator is not None:
                break
        if generator is None:
            raise ImportError("未找到 generate_pier_point_cloud")
        demo_seed = getattr(module, "DEFAULT_SEED", 42)
        demo_tilt = getattr(module, "DEMO_TILT_RATIO", 0.0045)
    except ImportError as exc:
        raise InsufficientGeometryError(
            "已开启 GEOMETRY_SYNTHETIC_FALLBACK，但合成点云生成器不可用"
            "（tests/mock_data.py 未随部署包提供）"
        ) from exc
    return np.asarray(generator(seed=demo_seed, tilt_ratio=demo_tilt), dtype=np.float64)


def execute_analysis(payload: dict[str, Any], cloud_path: Path) -> AnalysisOutcome:
    """同步执行一次几何分析（worker 与 inline 兜底共用；不碰数据库）。"""
    analysis_type = str(payload.get("analysis_type", ""))
    path = Path(cloud_path)
    synthetic = False
    if path.exists():
        cloud = load_tunnel_ply(path, voxel_size=0.2, return_report=False)
    elif analysis_type == "VERTICALITY" and synthetic_fallback_enabled():
        # 真实点云不存在时才走兜底；其它分析类型仍按 BT-30005 失败。
        cloud = _synthetic_pier_cloud()
        synthetic = True
    else:
        raise CloudFileMissingError(path)
    if cloud.shape[0] < MIN_CLOUD_POINTS:
        raise InsufficientGeometryError(f"点云点数 {cloud.shape[0]} 少于 {MIN_CLOUD_POINTS}")
    if analysis_type == "VERTICALITY":
        return _verticality(cloud, synthetic=synthetic)
    if analysis_type in ("CLEARANCE", "SECTION_SHAPE"):
        return _tunnel(cloud, analysis_type)
    raise ValueError(f"未知 analysis_type：{analysis_type}")


# ---------------------------------------------------------------------------- 持久化


def find_component(session: Session, component_code: str) -> dict[str, Any]:
    """按编码定位构件（未软删）。跨项目重名时无法消歧 → 422 BT-30003。"""
    rows = session.execute(
        text(
            "SELECT c.id, c.project_id, c.component_code, p.calc_srid "
            "FROM component c JOIN project p ON p.id = c.project_id "
            "WHERE c.component_code = :code AND c.deleted_at IS NULL"
        ),
        {"code": component_code},
    ).all()
    if not rows:
        raise api_error("BT-30001", f"构件不存在：{component_code}")
    if len(rows) > 1:
        raise api_error(
            "BT-30003",
            f"构件编码 {component_code} 在 {len(rows)} 个项目中重名，请补充项目范围后再提交",
        )
    row = rows[0]
    return {
        "id": str(row[0]),
        "project_id": str(row[1]),
        "component_code": str(row[2]),
        "calc_srid": int(row[3]),
    }


def create_task(
    session: Session,
    *,
    component: dict[str, Any],
    payload: dict[str, Any],
    idem_key: str,
) -> tuple[uuid.UUID, str, datetime, bool]:
    """落一行 QUEUED 任务；同 idem_key 24h 内复用（返回 ``replayed=True``）。"""
    existing = session.execute(
        text("SELECT id, biz_type, status, created_at FROM async_task WHERE idem_key = :key"),
        {"key": idem_key},
    ).first()
    if existing is not None:
        # uq_task_idem 是全局唯一键；若被其它 biz_type 占用，不能当作本接口的重放
        if str(existing[1]) != BIZ_TYPE:
            raise api_error(
                "BT-10002",
                f"幂等键 {idem_key} 已被 {existing[1]} 类型任务占用，请更换 Idempotency-Key",
            )
        return uuid.UUID(str(existing[0])), str(existing[2]), existing[3], True
    inserted = session.execute(
        text(
            "INSERT INTO async_task (project_id, biz_type, status, idem_key, payload) "
            "VALUES (:pid, :biz, 'QUEUED', :key, CAST(:payload AS jsonb)) "
            "RETURNING id, status, created_at"
        ),
        {
            "pid": component["project_id"],
            "biz": BIZ_TYPE,
            "key": idem_key,
            "payload": json.dumps(payload, ensure_ascii=False),
        },
    ).first()
    assert inserted is not None
    session.flush()
    return uuid.UUID(str(inserted[0])), str(inserted[1]), inserted[2], False


def get_task_row(session: Session, task_id: uuid.UUID) -> dict[str, Any] | None:
    row = session.execute(
        text(
            "SELECT id, status, payload, progress, error_code, error_detail, "
            "created_at, started_at, finished_at, result_ref "
            "FROM async_task WHERE id = :id AND biz_type = :biz"
        ),
        {"id": str(task_id), "biz": BIZ_TYPE},
    ).first()
    if row is None:
        return None
    return {
        "id": uuid.UUID(str(row[0])),
        "status": str(row[1]),
        "payload": row[2] if isinstance(row[2], dict) else {},
        "progress": int(row[3]),
        "error_code": row[4],
        "error_detail": row[5],
        "created_at": row[6],
        "started_at": row[7],
        "finished_at": row[8],
        "result_ref": row[9],
    }


def claim_task(session: Session, task_id: uuid.UUID) -> bool:
    """QUEUED → RUNNING；并发下只有一个 worker 能领到（SKIP LOCKED 在 run_once 里）。"""
    claimed = session.execute(
        text(
            "UPDATE async_task SET status='RUNNING', progress=30, "
            "started_at=COALESCE(started_at, now()), attempt=attempt+1 "
            "WHERE id = :id AND status IN ('QUEUED', 'RETRYING') RETURNING id"
        ),
        {"id": str(task_id)},
    ).first()
    return claimed is not None


def _fail(session: Session, task_id: uuid.UUID, code: str, detail: str) -> None:
    session.execute(
        text(
            "UPDATE async_task SET status='FAILED', error_code=:code, error_detail=:detail, "
            "finished_at=now() WHERE id = :id"
        ),
        {"id": str(task_id), "code": code, "detail": detail[:2000]},
    )


def _existing_id(session: Session, table: str, raw: Any) -> str | None:
    """只写真实存在的外键值：``geometry_result.pointcloud_id/baseline_id`` 都有 FK，
    请求里带的是“提示”而不是已登记记录时写 NULL，避免 FK 违约把任务卡在 RUNNING。

    ``table`` 只允许白名单内的两张表，不接受调用方传入任意表名。
    """
    if raw is None:
        return None
    if table not in ("pointcloud_file", "geometry_baseline"):
        raise ValueError(f"不允许的外键表：{table}")
    found = session.execute(
        text(f"SELECT id FROM {table} WHERE id = :id"), {"id": str(raw)}
    ).first()
    return None if found is None else str(raw)


def fetch_result_detail(session: Session, task_id: uuid.UUID) -> dict[str, Any] | None:
    """取该任务最近一次结构化结果（``geometry_result.detail_json``）。"""
    row = session.execute(
        text(
            "SELECT detail_json FROM geometry_result WHERE task_id = :id "
            "ORDER BY created_at DESC LIMIT 1"
        ),
        {"id": str(task_id)},
    ).first()
    if row is None:
        return None
    return row[0] if isinstance(row[0], dict) else None


def process_task(session: Session, task_id: uuid.UUID) -> str:
    """领取 → 计算 → 落库，返回最终状态（领不到返回 'SKIPPED'）。"""
    if not claim_task(session, task_id):
        return "SKIPPED"
    return execute_claimed(session, task_id)


def execute_claimed(session: Session, task_id: uuid.UUID) -> str:
    """执行一条**已领取**（RUNNING）的任务，返回最终状态。"""
    # worker 没有 HTTP 请求上下文；用 task_id 作为 traceId，保证日志可关联。
    bind_trace_id(str(task_id))
    row = get_task_row(session, task_id)
    assert row is not None
    payload = row["payload"]
    analysis_type = str(payload.get("analysis_type", "VERTICALITY"))
    try:
        component = find_component(session, str(payload["component_code"]))
    except Exception as exc:  # 构件在任务排队后被删除等
        _fail(session, task_id, "BT-30001", str(exc))
        return "FAILED"

    cloud_path = resolve_cloud_file(str(payload["component_code"]), _optional_uuid(payload))
    try:
        outcome = execute_analysis(payload, cloud_path)
    except CloudFileMissingError as exc:
        _fail(session, task_id, "BT-30005", str(exc))
        return "FAILED"
    except (InsufficientGeometryError, ValueError) as exc:
        _fail(session, task_id, "BT-40002", f"点云数据不足，无法完成几何分析：{exc}")
        return "FAILED"
    except Exception as exc:  # noqa: BLE001 - 未预期异常必须落 FAILED，不能让任务卡在 RUNNING
        _fail(session, task_id, "BT-90001", f"几何分析执行失败：{exc}")
        return "FAILED"

    if outcome.synthetic:
        log.warning(
            "使用合成点云兜底：本次为开发联调数据，结果不可用于验收",
            extra={
                "bt_extra": {
                    "task_id": str(task_id),
                    "component_code": component["component_code"],
                    "analysis_type": analysis_type,
                    "synthetic": True,
                    "synthetic_source": "tests.mock_data.generate_pier_point_cloud",
                }
            },
        )

    detail = dict(outcome.detail)
    detail["srid"] = component["calc_srid"]
    detail["params_hash"] = _sha256(payload)
    detail["excluded_layers"] = []
    detail["worst_section_stake"] = outcome.worst_section_stake
    detail["intrusion_area_sqm"] = None
    if not isinstance(detail.get("direction_deg"), int | float):
        detail["direction_deg"] = outcome.direction_deg
    detail["component_code"] = component["component_code"]
    pointcloud_ref = _existing_id(session, "pointcloud_file", payload.get("pointcloud_id"))
    baseline_ref = _existing_id(session, "geometry_baseline", payload.get("baseline_id"))

    result_id = session.execute(
        text(
            "INSERT INTO geometry_result (project_id, task_id, component_code, pointcloud_id, "
            "baseline_id, analysis_type, value, unit, direction_deg, worst_section_stake, "
            "valid_layers, srid, algorithm_version, params_hash, detail_json) "
            "VALUES (:pid, :tid, :code, :pcid, :bid, :atype, :value, :unit, :dir, :stake, "
            ":layers, :srid, :ver, :hash, CAST(:detail AS jsonb)) RETURNING id"
        ),
        {
            "pid": component["project_id"],
            "tid": str(task_id),
            "code": component["component_code"],
            "pcid": pointcloud_ref,
            "bid": baseline_ref,
            "atype": analysis_type,
            "value": outcome.value,
            "unit": outcome.unit,
            "dir": outcome.direction_deg,
            "stake": outcome.worst_section_stake,
            "layers": outcome.valid_layers,
            "srid": component["calc_srid"],
            "ver": ALGORITHM_VERSION,
            "hash": detail["params_hash"],
            "detail": json.dumps(detail, ensure_ascii=False),
        },
    ).first()
    assert result_id is not None
    session.execute(
        text(
            "UPDATE async_task SET status='SUCCEEDED', progress=100, result_ref=:ref, "
            "finished_at=now() WHERE id = :id"
        ),
        {"id": str(task_id), "ref": str(result_id[0])},
    )
    session.flush()
    return "SUCCEEDED"


def _optional_uuid(payload: dict[str, Any]) -> uuid.UUID | None:
    raw = payload.get("pointcloud_id")
    if raw is None:
        return None
    return uuid.UUID(str(raw))


def run_once(factory: sessionmaker[Session]) -> uuid.UUID | None:
    """worker 取一条任务并处理；无任务返回 None。"""
    with factory() as session:
        row = session.execute(
            text(
                "SELECT id FROM async_task WHERE biz_type = :biz "
                "AND status IN ('QUEUED', 'RETRYING') "
                "ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1"
            ),
            {"biz": BIZ_TYPE},
        ).first()
        if row is None:
            session.rollback()
            return None
        task_id = uuid.UUID(str(row[0]))
        process_task(session, task_id)
        session.commit()
        return task_id


def inline_enabled() -> bool:
    """``GEOMETRY_TASK_INLINE=1`` 时用 BackgroundTasks 进程内执行。

    **仅限本地开发**：进程重启任务即丢、无重试、无并发上限，生产必须关掉。
    """
    return os.environ.get(INLINE_ENV) == "1"


def run_task_now(factory: sessionmaker[Session], task_id: uuid.UUID) -> str:
    """指定任务立即执行（inline 兜底用，不走 SKIP LOCKED 队列）。"""
    with factory() as session:
        status = process_task(session, task_id)
        session.commit()
        return status
