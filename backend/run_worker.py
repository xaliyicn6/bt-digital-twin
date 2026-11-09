"""本地开发启动几何 worker：注入 backend/.env 后运行 app.workers.geometry_worker。

API 进程只把任务写进 async_task（QUEUED），真正算点云的是这个独立进程；
之前只起了 uvicorn，任务会一直停在 QUEUED。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent

env = os.environ.copy()
for line in (BACKEND / ".env").read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    env.setdefault(key.strip(), value.strip())

raise SystemExit(
    subprocess.call(
        [sys.executable, "-m", "app.workers.geometry_worker"],
        cwd=BACKEND,
        env=env,
    )
)
