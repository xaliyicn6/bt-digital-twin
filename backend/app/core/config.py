"""配置：全部来自环境变量，禁止在代码里写死口令与地址。"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    api_version: str
    database_url: str
    minio_endpoint: str
    redis_url: str

    @staticmethod
    def from_env() -> Settings:
        return Settings(
            api_version="v1",
            database_url=os.environ.get("DATABASE_URL", "postgresql://localhost:5432/bt"),
            minio_endpoint=os.environ.get("MINIO_ENDPOINT", "127.0.0.1:9000"),
            redis_url=os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0"),
        )
