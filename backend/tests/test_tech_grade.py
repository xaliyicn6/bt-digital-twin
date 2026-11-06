"""tech_grade：等级阈值映射 + 配置热加载 + 迁移列（C1/C2/C3）。

阈值是工程解释（见 ``backend/config/verticality_thresholds.yaml``），本测试只锁
"映射关系、配置覆盖与'未核验'标注存在"，不把任何等级当作规范验收结论。
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import mock_data
import numpy as np
import pytest
from app.services.geometry_task import _verticality
from app.services.tech_grade import (
    GRADE_LABELS,
    GRADE_SOURCE,
    STANDARD_REFERENCE,
    grade_for_tilt,
    grade_label,
)
from app.services.verticality_thresholds import (
    CONFIG_ENV,
    load_thresholds,
    reset_cache,
)
from sqlalchemy import text
from sqlalchemy.orm import Session


@pytest.mark.parametrize(
    ("tilt_permille", "expected"),
    [
        (0.0, 1),
        (1.586, 1),
        (1.999, 1),
        (2.0, 2),
        (2.999, 2),
        (3.0, 3),
        (3.999, 3),
        (4.0, 4),
        (4.999, 4),
        (5.0, 5),
        (999.0, 5),
    ],
)
def test_grade_threshold_boundaries(tilt_permille: float, expected: int) -> None:
    assert grade_for_tilt(tilt_permille) == expected


def test_grade_uses_magnitude_of_tilt() -> None:
    # 倾斜方向不应改变等级：正负号只表达方位，不表达严重程度。
    assert grade_for_tilt(-4.5) == grade_for_tilt(4.5) == 4


def test_grade_labels_cover_all_grades() -> None:
    assert set(GRADE_LABELS) == {1, 2, 3, 4, 5}
    assert grade_label(3) == "3类（较差）"
    assert grade_label(5) == "5类（危险）"


def test_thresholds_and_clause_are_marked_unverified() -> None:
    # AGENTS.md 强制规则 2：示例阈值与条款号必须显式标注"示例/以正式规范为准"。
    assert "示例" in GRADE_SOURCE and "待" in GRADE_SOURCE
    # 确认门 C3：统一 5.x，不编造具体条款号。
    assert "JTG/T H21-2011 第 5.x 条" in STANDARD_REFERENCE
    assert "以正式规范为准" in STANDARD_REFERENCE


def test_default_config_matches_confirmed_thresholds() -> None:
    thresholds = load_thresholds()
    assert (
        thresholds.grade_1_below,
        thresholds.grade_2_below,
        thresholds.grade_3_below,
        thresholds.grade_4_below,
    ) == (2.0, 3.0, 4.0, 5.0)
    assert "工程解释" in thresholds.note
    assert "5.x" in thresholds.source


def _write_thresholds(path: Path, values: tuple[float, float, float, float]) -> None:
    g1, g2, g3, g4 = values
    path.write_text(
        "version: test\n"
        "note: 工程解释，可被甲方规范覆盖\n"
        "source: JTG/T H21-2011 第 5.x 条（具体条款以正式规范为准）\n"
        "tilt_permille:\n"
        f"  grade_1_below: {g1}\n"
        f"  grade_2_below: {g2}\n"
        f"  grade_3_below: {g3}\n"
        f"  grade_4_below: {g4}\n",
        encoding="utf-8",
    )


def test_env_override_and_mtime_hot_reload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """C2：环境变量可覆盖配置路径，改文件 mtime 后无需重启即生效。"""
    path = tmp_path / "verticality.yaml"
    _write_thresholds(path, (2.0, 3.0, 4.0, 5.0))
    monkeypatch.setenv(CONFIG_ENV, str(path))
    reset_cache()
    assert grade_for_tilt(4.5) == 4

    # 换成更严格的规范口径：4‰ 即 5 类。显式推进 mtime，避开文件系统毫秒级精度。
    _write_thresholds(path, (1.0, 2.0, 3.0, 4.0))
    stamp = path.stat().st_mtime + 10.0
    os.utime(path, (stamp, stamp))
    assert grade_for_tilt(4.5) == 5
    reset_cache()


def test_invalid_thresholds_are_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "bad.yaml"
    _write_thresholds(path, (3.0, 2.0, 1.0, 0.0))
    monkeypatch.setenv(CONFIG_ENV, str(path))
    reset_cache()
    with pytest.raises(ValueError, match="严格递增"):
        load_thresholds()
    reset_cache()


def test_verticality_detail_carries_grade_and_source() -> None:
    cloud = np.asarray(
        mock_data.generate_pier_point_cloud(n=20_000, tilt_ratio=0.0045),
        dtype=np.float64,
    )
    detail = _verticality(cloud).detail
    grade = detail["tech_grade"]
    assert grade == grade_for_tilt(detail["tilt_permille"])
    assert detail["tech_grade_label"] == grade_label(grade)
    assert detail["tech_grade_source"] == GRADE_SOURCE
    assert detail["standard_reference"] == STANDARD_REFERENCE


def test_component_tech_grade_migration_is_nullable(
    session: Session, seeded: dict[str, uuid.UUID]
) -> None:
    """C1：Alembic 0005 加列成功，默认 NULL，且带 1-5 范围约束。"""
    column = session.execute(
        text(
            "SELECT is_nullable, data_type FROM information_schema.columns "
            "WHERE table_name = 'component' AND column_name = 'tech_grade'"
        )
    ).first()
    assert column is not None, "缺少 component.tech_grade 列（迁移 0005 未执行）"
    assert column[0] == "YES"
    assert column[1] == "smallint"

    constraint = session.execute(
        text("SELECT conname FROM pg_constraint WHERE conname = 'ck_component_tech_grade_range'")
    ).first()
    assert constraint is not None

    component_id = uuid.uuid4()
    session.execute(
        text(
            "INSERT INTO component (id, project_id, struct_kind, bridge_id, component_code, "
            "component_name, level, component_type) VALUES "
            "(:id, :pid, 'BRIDGE', :bid, :code, '等级迁移试验墩', 3, 'PIR')"
        ),
        {
            "id": str(component_id),
            "pid": str(seeded["project_id"]),
            "bid": str(seeded["bridge_id"]),
            "code": f"MIG-{component_id.hex[:8]}",
        },
    )
    stored = session.execute(
        text("SELECT tech_grade FROM component WHERE id = :id"), {"id": str(component_id)}
    ).scalar_one()
    assert stored is None
