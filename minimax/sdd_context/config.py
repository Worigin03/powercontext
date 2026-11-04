"""Runtime configuration for the SDD context module.

Every value comes from the environment so the same code runs against seekdb,
OceanBase and the local PowerContext server without edits.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env(name: str, default: str) -> str:
    value = os.environ.get(name)
    return default if value is None or value == "" else value


@dataclass(frozen=True)
class DatabaseConfig:
    host: str = "127.0.0.1"
    port: int = 2881
    user: str = "root"
    password: str = ""
    database: str = "sdd_context"
    charset: str = "utf8mb4"

    @classmethod
    def from_env(cls) -> "DatabaseConfig":
        return cls(
            host=_env("SDD_DB_HOST", cls.host),
            port=int(_env("SDD_DB_PORT", str(cls.port))),
            user=_env("SDD_DB_USER", cls.user),
            password=os.environ.get("SDD_DB_PASSWORD", cls.password),
            database=_env("SDD_DB_NAME", cls.database),
        )

    def dsn(self) -> str:
        return f"mysql://{self.user}@{self.host}:{self.port}/{self.database}"


@dataclass(frozen=True)
class PowerContextConfig:
    base_url: str = "http://127.0.0.1:8000"
    scope_id: str | None = None
    timeout: float = 15.0

    @classmethod
    def from_env(cls) -> "PowerContextConfig":
        scope = os.environ.get("SDD_POWERCONTEXT_SCOPE") or None
        return cls(
            base_url=_env("POWERCONTEXT_URL", cls.base_url).rstrip("/"),
            scope_id=scope,
            timeout=float(_env("SDD_POWERCONTEXT_TIMEOUT", "15")),
        )


@dataclass(frozen=True)
class Settings:
    database: DatabaseConfig
    powercontext: PowerContextConfig
    query_log: str | None

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database=DatabaseConfig.from_env(),
            powercontext=PowerContextConfig.from_env(),
            query_log=os.environ.get("SDD_QUERY_LOG") or None,
        )
