"""构件技术状况等级（1-5 类）：标签、规范依据文案与判定入口。

阈值**不在这里硬编码**：统一由 ``services/verticality_thresholds.py`` 从
``backend/config/verticality_thresholds.yaml`` 加载（mtime 热加载，可用
``VERTICALITY_THRESHOLD_CONFIG`` 覆盖），以支持甲方规范覆盖（确认门 C2）。

⚠️ 阈值是工程解释，可被甲方规范覆盖；报告与界面必须同时展示
``tech_grade_source``，不得把等级当作验收结论。
"""

from __future__ import annotations

from typing import Final

from app.services.verticality_thresholds import load_thresholds

# 对外标注：只要该常量非空，前端就必须展示"示例阈值"提示。
GRADE_SOURCE: Final = "示例阈值 · 待规范库确认"

GRADE_LABELS: Final[dict[int, str]] = {
    1: "1类（良好）",
    2: "2类（较好）",
    3: "3类（较差）",
    4: "4类（差）",
    5: "5类（危险）",
}

# 确认门 C3：不编造具体条款号，统一写 5.x，交付前由技术总工补全。
STANDARD_REFERENCE: Final = "JTG/T H21-2011 第 5.x 条（具体条款以正式规范为准）"


def grade_for_tilt(tilt_permille: float) -> int:
    """倾斜率（‰）→ 技术状况等级 1-5（阈值来自配置文件）。"""
    return load_thresholds().grade_for(tilt_permille)


def grade_label(grade: int) -> str:
    return GRADE_LABELS[grade]
