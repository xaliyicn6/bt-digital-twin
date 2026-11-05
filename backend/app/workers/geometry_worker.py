"""几何任务 worker（独立进程，不属于 API 进程）。

启动：
    python -m app.workers.geometry_worker

连接串取 ``DATABASE_URL``；空闲轮询间隔取 ``GEOMETRY_WORKER_POLL_SEC``（默认 2 秒）。
领取用 ``FOR UPDATE SKIP LOCKED``，多个 worker 可同时跑；状态迁移严格按
``db/schema.sql`` 的 ``task_status``：QUEUED/RETRYING → RUNNING → SUCCEEDED/FAILED。
"""

from __future__ import annotations

import contextlib
import os
import time

from app.db.session import get_session_factory
from app.services.geometry_task import run_once

POLL_ENV = "GEOMETRY_WORKER_POLL_SEC"
DEFAULT_POLL_SEC = 2.0


def _poll_interval() -> float:
    raw = os.environ.get(POLL_ENV)
    if raw is None:
        return DEFAULT_POLL_SEC
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_POLL_SEC
    return value if value > 0.0 else DEFAULT_POLL_SEC


def main() -> None:
    factory = get_session_factory()
    interval = _poll_interval()
    while True:
        task_id = run_once(factory)
        if task_id is None:
            time.sleep(interval)


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        main()
