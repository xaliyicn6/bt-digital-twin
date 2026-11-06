"""倾斜率 → 缺陷等级阈值（字典配置，禁止在业务代码里硬编码）。

配置来源：
    默认 ``backend/config/defect_level_thresholds.yaml``；
    可用 ``DEFECT_LEVEL_CONFIG`` 指向别的 YAML（便于现场调整，无需改代码）。
    服务端按文件 mtime 缓存：运维改完文件即生效，不必重启进程。

语义：
    tilt_permille > L1 → L1；> L2 → L2；否则 default_level。
判定结果只是"参考值"，最终以人工复核与设计规范为准（契约字段 threshold_note）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

CONFIG_ENV = "DEFECT_LEVEL_CONFIG"
DEFAULT_PATH = Path(__file__).resolve().parents[2] / "config" / "defect_level_thresholds.yaml"
REFERENCE_NOTE = "参考值，以最终人工复核及设计规范为准"

_cache: tuple[float, DefectLevelThresholds] | None = None


@dataclass(frozen=True)
class DefectLevelThresholds:
    version: str
    note: str
    l1: float
    l2: float
    default_level: str

    def suggest(self, tilt_permille: float) -> str:
        if tilt_permille > self.l1:
            return "L1"
        if tilt_permille > self.l2:
            return "L2"
        return self.default_level

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "note": self.note,
            "tilt_permille": {"L1": self.l1, "L2": self.l2, "default_level": self.default_level},
        }


def config_path() -> Path:
    configured = os.environ.get(CONFIG_ENV)
    return Path(configured) if configured else DEFAULT_PATH


def _parse(raw: Any, path: Path) -> DefectLevelThresholds:
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: 顶层必须是对象")
    tilt = raw.get("tilt_permille")
    if not isinstance(tilt, dict):
        raise ValueError(f"{path}: 缺少 tilt_permille")
    try:
        l1 = float(tilt["L1"])
        l2 = float(tilt["L2"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{path}: L1/L2 必须是数字") from exc
    if l1 <= l2:
        raise ValueError(f"{path}: L1 阈值必须大于 L2 阈值")
    default_level = str(tilt.get("default_level", "L3"))
    if default_level not in {"L1", "L2", "L3"}:
        raise ValueError(f"{path}: default_level 必须是 L1/L2/L3")
    return DefectLevelThresholds(
        version=str(raw.get("version", "unknown")),
        note=str(raw.get("note", REFERENCE_NOTE)),
        l1=l1,
        l2=l2,
        default_level=default_level,
    )


def load_thresholds() -> DefectLevelThresholds:
    """读取阈值配置；文件缺失/格式错误时抛 ValueError（由调用方转 503/500）。"""
    global _cache
    path = config_path()
    mtime = path.stat().st_mtime
    if _cache is not None and _cache[0] == mtime:
        return _cache[1]
    with path.open("r", encoding="utf-8") as handle:
        parsed = _parse(yaml.safe_load(handle), path)
    _cache = (mtime, parsed)
    return parsed


def reset_cache() -> None:
    """测试用：丢弃 mtime 缓存。"""
    global _cache
    _cache = None
