"""M07 工单纯逻辑：阈值字典、状态机复用、单号/游标格式（不依赖数据库）。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from app.services import workorder as service
from app.services.defect_levels import DefectLevelThresholds, load_thresholds, reset_cache


def test_thresholds_from_yaml_and_suggestion() -> None:
    thresholds = load_thresholds()
    assert thresholds.note == "参考值，以最终人工复核及设计规范为准"
    assert thresholds.l1 == 5.0
    assert thresholds.l2 == 3.0
    assert thresholds.suggest(6.0) == "L1"
    assert thresholds.suggest(5.0) == "L2"  # 严格大于 L1 才是 L1
    assert thresholds.suggest(4.0) == "L2"
    assert thresholds.suggest(3.0) == service.load_thresholds().default_level  # 严格大于 L2
    assert thresholds.suggest(1.0) == "L3"


def test_thresholds_reject_inverted_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("tilt_permille: {L1: 2.0, L2: 4.0}\n", encoding="utf-8")
    monkeypatch.setenv("DEFECT_LEVEL_CONFIG", str(bad))
    reset_cache()
    with pytest.raises(ValueError, match="L1 阈值必须大于 L2 阈值"):
        load_thresholds()


def test_watched_events_all_exist_in_state_machine() -> None:
    """契约里的每个可触发事件都必须能在状态机 YAML 里找到，禁止编造事件号。"""
    from app.schemas.workorder import WorkOrderEventName

    known = {str(item["event"]) for item in service.transitions()}
    for event in WorkOrderEventName:
        assert event.value in known, f"{event.value} 不在 state/work_order_state_machine.yaml"


def test_required_transitions_present() -> None:
    items = service.transitions()
    submit = [item for item in items if item["event"] == "SUBMIT" and item["from"] == "DRAFT"]
    approve = [
        item for item in items if item["event"] == "APPROVE" and item["from"] == "PENDING_CONFIRM"
    ]
    assign = [item for item in items if item["event"] == "ASSIGN" and item["from"] == "CONFIRMED"]
    assert submit and submit[0]["to"] == "PENDING_CONFIRM"
    assert approve and approve[0]["to"] == "CONFIRMED"
    assert assign and assign[0]["to"] == "ASSIGNED"
    assert "MAINT_MANAGER" in approve[0]["allowed_roles"]


def test_order_no_format() -> None:
    from datetime import date

    no = service._new_order_no(date(2026, 11, 6))
    assert no.startswith("WO-20261106-")
    assert len(no.split("-")[-1]) == 6


def test_cursor_roundtrip() -> None:
    order_id = uuid.uuid4()
    created = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)
    cursor = service._cursor_encode(created, order_id)
    assert service._cursor_decode(cursor) == (created, order_id)


def test_default_level_definition() -> None:
    thresholds = DefectLevelThresholds(version="t", note="n", l1=5.0, l2=3.0, default_level="L3")
    assert thresholds.as_dict()["tilt_permille"] == {"L1": 5.0, "L2": 3.0, "default_level": "L3"}
