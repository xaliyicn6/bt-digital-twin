"""本地开发启动后端：读取 backend/.env 注入环境变量，再拉起 uvicorn。

app/core/config.py 只读进程环境变量（不加载 .env），所以本地必须显式注入；
本脚本只做注入 + 启动，不改变应用行为。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent

for line in (BACKEND / ".env").read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    os.environ.setdefault(key.strip(), value.strip())

os.environ.setdefault("BT_DEV_ANONYMOUS", "1")

import uvicorn  # noqa: E402

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    uvicorn.run("app.main:app", host="127.0.0.1", port=port, log_level="info")
