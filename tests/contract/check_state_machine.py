# -*- coding: utf-8 -*-
"""P0 门 3：工单状态机配置自检。CI 与本地共用同一脚本。"""

from __future__ import annotations

import pathlib
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
CONFIG = ROOT / "state" / "work_order_state_machine.yaml"


def main() -> int:
    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    states = list(cfg["state_enum"])
    transitions = cfg["transitions"]
    errors: list[str] = []

    if len(states) != 12:
        errors.append(f"状态数应为 12，实际 {len(states)}")
    if len(transitions) != 19:
        errors.append(f"迁移数应为 19，实际 {len(transitions)}")

    state_set = set(states)
    for t in transitions:
        if t["to"] not in state_set:
            errors.append(f"{t['id']}: to={t['to']} 不在 state_enum")
        if t["from"] != "*" and t["from"] not in state_set:
            errors.append(f"{t['id']}: from={t['from']} 不在 state_enum")
        role_set = set(cfg["roles"])
        for r in t["allowed_roles"]:
            if r not in role_set:
                errors.append(f"{t['id']}: 角色 {r} 未在 roles 中定义")

    # 非初始状态必须有入边
    for s in states:
        if s == cfg["initial_state"]:
            continue
        if not any(t["to"] == s for t in transitions):
            errors.append(f"非初始状态 {s} 没有入边")

    # 从初始态可达所有状态
    reach, todo = {cfg["initial_state"]}, [cfg["initial_state"]]
    while todo:
        cur = todo.pop()
        for t in transitions:
            if t["from"] in (cur, "*") and t["to"] not in reach:
                reach.add(t["to"])
                todo.append(t["to"])
    unreachable = state_set - reach
    if unreachable:
        errors.append(f"不可达状态：{sorted(unreachable)}")

    if errors:
        for e in errors:
            print("[FAIL]", e)
        return 1
    print(f"[OK] states={len(states)} transitions={len(transitions)} 全部 to/from 合法，均可达")
    return 0


if __name__ == "__main__":
    sys.exit(main())
