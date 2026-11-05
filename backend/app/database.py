"""数据库连接与会话管理。

本地开发默认用 SQLite（零配置），在线使用 MySQL：
只要改 DATABASE_URL 一行，业务代码完全不用动。
"""

from __future__ import annotations

import os
import ssl

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings


def build_engine(url: str):
    kwargs: dict = {"echo": os.getenv("SQL_ECHO") == "1", "future": True, "hide_parameters": True}
    if url.startswith("sqlite"):
        # FastAPI 的线程池会跨线程使用连接
        kwargs["connect_args"] = {"check_same_thread": False}
    else:
        kwargs.update(
            # TiDB 的 RR 是快照隔离：等待互斥行后，普通 SELECT 仍可能读旧快照。
            # 配合写事务互斥行使用 RC，让锁内状态读取看到前一事务的提交。
            isolation_level="READ COMMITTED",
            pool_pre_ping=True,   # 免费库会掐空闲连接，取连接前先探活
            pool_recycle=280,     # 早于 MySQL 默认 wait_timeout(28800s) 回收
            pool_size=5,
            max_overflow=5,
        )
        kwargs["connect_args"] = {"connect_timeout": 10}
        if os.getenv("DB_SSL") == "1" or os.getenv("DB_SSL_CA"):
            # 与数据库自检共用；验证服务端证书和主机名。
            kwargs["connect_args"]["ssl"] = ssl.create_default_context(cafile=os.getenv("DB_SSL_CA") or None)
    return create_engine(url, **kwargs)


engine = build_engine(settings.database_url)

# expire_on_commit=False：commit 之后还要读对象属性（返回给前端），
# 否则会触发一次多余的 SELECT 甚至 DetachedInstanceError。
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    """FastAPI 依赖注入：每个请求一个 Session，请求结束自动关闭。"""
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()
