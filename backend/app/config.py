"""全局配置。

所有可变项都从环境变量（或 backend/.env）读取，
目的是：数据库密码永远不写进代码、不提交到仓库。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path | None = None) -> None:
    """极简 .env 解析，避免为了一件事多装一个依赖。"""
    env_file = path or BACKEND_DIR / ".env"
    if not env_file.exists():
        return
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.split(" #", 1)[0].strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)


if os.getenv("ROLLCALL_TESTING") != "1":
    _load_dotenv()


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    """运行期配置。改完 .env 需要重启服务。"""

    database_url: str
    alpha: float
    point_arrive: float
    point_repeat_ok: float
    point_repeat_fail: float
    answer_min: float
    answer_max: float
    # 每被点名 N 次获得 1 次「点名转移权」，设 0 表示关闭该玩法
    transfer_every: int
    auto_create_tables: bool
    timezone: str = "Asia/Shanghai"
    teacher_api_key: str = ""
    # 课堂演示用开关：OPEN_ACCESS=1 时不再校验教师口令（任何人打开即可用）
    open_access: bool = False
    cors_origins: list[str] = field(default_factory=list)


def load_settings() -> Settings:
    default_db = f"sqlite:///{(BACKEND_DIR / 'dev.db').as_posix()}"
    origins_raw = os.getenv("CORS_ORIGINS", "*")
    origins = [item.strip() for item in origins_raw.split(",") if item.strip()]
    return Settings(
        database_url=os.getenv("DATABASE_URL", default_db) or default_db,
        # α 越大，「积分高的人被点到的概率」下降得越狠；1.0 是作业公式的默认值
        alpha=_env_float("ALPHA", 1.0),
        point_arrive=_env_float("POINT_ARRIVE", 1.0),
        point_repeat_ok=_env_float("POINT_REPEAT_OK", 0.5),
        point_repeat_fail=_env_float("POINT_REPEAT_FAIL", -1.0),
        answer_min=_env_float("ANSWER_MIN", 0.5),
        answer_max=_env_float("ANSWER_MAX", 3.0),
        transfer_every=int(_env_float("TRANSFER_EVERY", 2)),
        auto_create_tables=_env_bool("AUTO_CREATE_TABLES", True),
        timezone=os.getenv("TIMEZONE", "Asia/Shanghai") or "Asia/Shanghai",
        teacher_api_key=os.getenv("TEACHER_API_KEY", ""),
        open_access=_env_bool("OPEN_ACCESS", False),
        cors_origins=origins or ["*"],
    )


settings = load_settings()
