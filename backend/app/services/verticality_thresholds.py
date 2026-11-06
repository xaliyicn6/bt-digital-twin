"""垂直度阈值配置加载（与 ``defect_levels`` 相同的 mtime 热加载约定）。

配置来源：
    默认 ``backend/config/verticality_thresholds.yaml``；
    ``VERTICALITY_THRESHOLD_CONFIG`` 可指向另一份 YAML（现场按甲方规范覆盖）。
    按文件 mtime 缓存：改文件即生效，不必重启进程。

语义（倾斜率取绝对值，单位 ‰）：
    < grade_1_below → 1 类；< grade_2_below → 2 类；< grade_3_below → 3 类；
    < grade_4_below → 4 类；否则 5 类。
阈值是工程解释，可被甲方规范覆盖，不得当作验收结论。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

CONFIG_ENV = "VERTICALITY_THRESHOLD_CONFIG"
DEFAULT_PATH = Path(__file__).resolve().parents[2] / "config" / "verticality_thresholds.yaml"
DEFAULT_NOTE = "工程解释，可被甲方规范覆盖"

_cache: tuple[str, float, VerticalityThresholds] | None = None


@dataclass(frozen=True)
class VerticalityThresholds:
    version: str
    note: str
    source: str
    grade_1_below: float
    grade_2_below: float
    grade_3_below: float
    grade_4_below: float

    def grade_for(self, tilt_permille: float) -> int:
        value = abs(float(tilt_permille))
        if value < self.grade_1_below:
            return 1
        if value < self.grade_2_below:
            return 2
        if value < self.grade_3_below:
            return 3
        if value < self.grade_4_below:
            return 4
        return 5

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "note": self.note,
            "source": self.source,
            "tilt_permille": {
                "grade_1_below": self.grade_1_below,
                "grade_2_below": self.grade_2_below,
                "grade_3_below": self.grade_3_below,
                "grade_4_below": self.grade_4_below,
            },
        }


def config_path() -> Path:
    configured = os.environ.get(CONFIG_ENV)
    return Path(configured) if configured else DEFAULT_PATH


def _parse(raw: Any, path: Path) -> VerticalityThresholds:
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: 顶层必须是对象")
    tilt = raw.get("tilt_permille")
    if not isinstance(tilt, dict):
        raise ValueError(f"{path}: 缺少 tilt_permille")
    keys = ("grade_1_below", "grade_2_below", "grade_3_below", "grade_4_below")
    try:
        values = [float(tilt[key]) for key in keys]
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{path}: {', '.join(keys)} 必须是数字") from exc
    if any(left >= right for left, right in zip(values, values[1:], strict=False)):
        raise ValueError(f"{path}: 阈值必须严格递增")
    return VerticalityThresholds(
        version=str(raw.get("version", "unknown")),
        note=str(raw.get("note", DEFAULT_NOTE)),
        source=str(raw.get("source", "")),
        grade_1_below=values[0],
        grade_2_below=values[1],
        grade_3_below=values[2],
        grade_4_below=values[3],
    )


def load_thresholds() -> VerticalityThresholds:
    """读取阈值；文件缺失/格式错误抛 ValueError（由调用方转 problem+json）。"""
    global _cache
    path = config_path()
    mtime = path.stat().st_mtime
    key = str(path.resolve())
    if _cache is not None and _cache[0] == key and _cache[1] == mtime:
        return _cache[2]
    with path.open("r", encoding="utf-8") as handle:
        parsed = _parse(yaml.safe_load(handle), path)
    _cache = (key, mtime, parsed)
    return parsed


def reset_cache() -> None:
    """测试用：丢弃 mtime 缓存。"""
    global _cache
    _cache = None
