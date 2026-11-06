"""检测报告导出：HTML 渲染（纯函数）+ 截图校验 + 批量 CSV + 路由注册。"""

from __future__ import annotations

import base64
import uuid
from datetime import datetime

import pytest
from app.main import create_app
from app.services.report import (
    SCREENSHOT_PLACEHOLDER,
    ReportContext,
    render_report_html,
    validate_screenshot,
)
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

REPORTS = "/api/v1/reports"


def _ctx(result: dict[str, object] | None) -> ReportContext:
    return ReportContext(
        task_id=uuid.UUID("5f0c2a17-8f52-4d36-9c0a-2c1f3f9a6b7d"),
        project_name="测试项目",
        component_code="B001-K03-PIR-P01-001",
        component_name="1号桥墩",
        analysis_type="VERTICALITY",
        status="SUCCEEDED",
        result=result,
        created_at=datetime(2026, 11, 6, 10, 0, 0),
        finished_at=datetime(2026, 11, 6, 10, 0, 5),
    )


def _verticality_result() -> dict[str, object]:
    return {
        "value": 4.562,
        "unit": "permille",
        "tilt_permille": 4.562,
        "tech_grade": 4,
        "tech_grade_label": "4类（差）",
        "tech_grade_source": "示例阈值 · 待规范库确认",
        "standard_reference": "JTG/T H21-2011 第 5.x 条（具体条款以正式规范为准）",
        "algorithm_version": "v1.0-tls",
        "valid_layers": 10,
        "residual_rms_mm": 3.2,
        "degraded": False,
    }


def _jpeg_with_sof(width: int, height: int) -> bytes:
    """构造只含 SOF0 的最小 JPEG 头；足够让尺寸解析器读取宽高（不做像素解码）。"""
    sof = (
        b"\xff\xc0"
        + (17).to_bytes(2, "big")
        + b"\x08"
        + height.to_bytes(2, "big")
        + width.to_bytes(2, "big")
        + b"\x03"
        + b"\x01\x11\x00"
        + b"\x02\x11\x01"
        + b"\x03\x11\x01"
    )
    return b"\xff\xd8" + sof + b"\xff\xd9"


def _jpeg_base64(width: int = 200, height: int = 100) -> str:
    return base64.b64encode(_jpeg_with_sof(width, height)).decode()


# ------------------------------------------------------------------ HTML 渲染


def test_report_contains_cover_metrics_and_grade() -> None:
    html = render_report_html(_ctx(_verticality_result()))
    assert "桥隧结构检测报告" in html
    assert "测试项目" in html
    assert "B001-K03-PIR-P01-001" in html
    assert "4类（差）" in html
    assert "4.562 permille" in html
    assert "（盖章处）" in html
    assert SCREENSHOT_PLACEHOLDER in html


def test_report_always_shows_unverified_threshold_notice() -> None:
    html = render_report_html(_ctx(_verticality_result()))
    assert "示例阈值 · 待规范库确认" in html
    assert "不得作为验收依据" in html
    assert "JTG/T H21-2011 第 5.x 条" in html
    assert "以正式规范为准" in html


def test_report_escapes_html_injected_values() -> None:
    ctx = _ctx(_verticality_result())
    hostile = ReportContext(
        task_id=ctx.task_id,
        project_name="<script>alert(1)</script>",
        component_code="B001",
        component_name="<img src=x onerror=alert(1)>",
        analysis_type="VERTICALITY",
        status="SUCCEEDED",
        result=ctx.result,
        created_at=ctx.created_at,
        finished_at=ctx.finished_at,
    )
    html = render_report_html(hostile)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_report_without_result_marks_placeholder() -> None:
    html = render_report_html(_ctx(None))
    assert "无结构化结果" in html
    assert "算法待接入" in html


def test_report_marks_degraded_result() -> None:
    result = {**_verticality_result(), "degraded": True, "degrade_reason": "合成点云兜底"}
    html = render_report_html(_ctx(result))
    assert "合成点云兜底" in html
    assert "禁止用于验收" in html


# ------------------------------------------------------------------ C8 截图


def test_validate_screenshot_accepts_jpeg_and_returns_data_url() -> None:
    raw = _jpeg_base64()
    assert validate_screenshot(raw) == f"data:image/jpeg;base64,{raw}"
    assert validate_screenshot(f"data:image/jpeg;base64,{raw}") == (f"data:image/jpeg;base64,{raw}")


def test_validate_screenshot_rejects_non_jpeg() -> None:
    with pytest.raises(ValueError, match="JPEG"):
        validate_screenshot(base64.b64encode(b"\x89PNG\r\n\x1a\n").decode())


def test_validate_screenshot_rejects_out_of_range_dimensions() -> None:
    with pytest.raises(ValueError, match="超出"):
        validate_screenshot(_jpeg_base64(width=50, height=50))


def test_render_report_embeds_screenshot_and_keeps_warning() -> None:
    data_url = f"data:image/jpeg;base64,{_jpeg_base64()}"
    html = render_report_html(_ctx(_verticality_result()), data_url)
    assert data_url in html
    assert 'class="shot-img"' in html
    assert SCREENSHOT_PLACEHOLDER not in html
    # 含截图的报告页眉仍保留示例警示条。
    assert "不得作为验收依据" in html


def test_report_routes_are_registered() -> None:
    routes = list(create_app().routes)
    export_path = f"{REPORTS}/{{task_id}}/export"
    export_routes = [route for route in routes if getattr(route, "path", "") == export_path]
    methods = {
        method for route in export_routes for method in (getattr(route, "methods", set()) or set())
    }
    assert {"GET", "POST"} <= methods
    assert any(getattr(route, "path", "") == f"{REPORTS}/batch/export" for route in routes)


def test_batch_export_rejects_invalid_task_ids(client: TestClient) -> None:
    resp = client.get(f"{REPORTS}/batch/export", params={"task_ids": "not-a-uuid"})
    assert resp.status_code == 422
    assert resp.json()["code"] == "BT-60006"


# ------------------------------------------------------------------ 批量 CSV


@pytest.fixture()
def component_code(session: Session, seeded: dict[str, uuid.UUID]) -> str:
    code = f"BATCH-{uuid.uuid4().hex[:8]}"
    session.execute(
        text(
            "INSERT INTO component (id, project_id, struct_kind, bridge_id, component_code, "
            "component_name, level, component_type) VALUES "
            "(:id, :pid, 'BRIDGE', :bid, :code, '批量试验墩', 3, 'PIR')"
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


def test_batch_export_csv_contains_all_rows_with_bom(
    client: TestClient, component_code: str
) -> None:
    key = f"idem-{uuid.uuid4().hex}"
    items = [{"component_code": component_code, "analysis_type": "VERTICALITY"}] * 10
    accepted = client.post(
        "/api/v1/geometry/tasks/batch",
        json={"items": items},
        headers={"Idempotency-Key": key},
    )
    assert accepted.status_code == 202
    task_ids = [item["task_id"] for item in accepted.json()["items"]]

    missing = str(uuid.uuid4())
    resp = client.get(
        f"{REPORTS}/batch/export",
        params={"task_ids": ",".join([*task_ids, missing])},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "attachment" in resp.headers["content-disposition"]

    decoded = resp.content.decode("utf-8")
    assert decoded.startswith("\ufeff"), "CSV 必须带 UTF-8 BOM，Excel 才能直接打开"
    lines = decoded.lstrip("\ufeff").splitlines()
    assert lines[0].startswith("任务ID,构件编码,分析类型,状态")
    assert len(lines) == 12, f"表头 + 10 条任务 + 1 条 NOT_FOUND，实际 {len(lines)} 行"
    assert all("QUEUED" in line for line in lines[1:11])
    assert "NOT_FOUND" in lines[11]
