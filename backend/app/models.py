"""ORM 模型。

表结构与 sql/schema.sql 一一对应：既可以用 schema.sql 在 MySQL 上手工建表，
也可以让 SQLAlchemy 的 create_all 自动建（本地 SQLite 开发用）。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DDL,
    event,
    DateTime,
    Enum,
    Double,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base
from .utils.time import now

# 常量集中放这里，避免各处手写字符串拼错
MODE_RANDOM = "random"
MODE_SEQUENTIAL = "sequential"
MODE_CLOCK = "clock"
MODE_LABELS = {MODE_RANDOM: "随机点名", MODE_SEQUENTIAL: "按学号顺序", MODE_CLOCK: "按时间点名"}

SESSION_OPEN = "open"
SESSION_CLOSED = "closed"

RC_PENDING = "pending"
RC_SCORED = "scored"
RC_TRANSFERRED = "transferred"

REASON_ARRIVE = "arrive"
REASON_REPEAT = "repeat"
REASON_ANSWER = "answer"
REASON_EVENT = "event"
REASON_TRANSFER = "transfer"
REASON_MANUAL = "manual"


def _enum(*values: str, length: int = 16) -> Enum:
    """用 VARCHAR + CHECK 模拟枚举：SQLite 和 MySQL 都能建，行为一致。"""
    return Enum(*values, native_enum=False, length=length, validate_strings=True)


class WriteLock(Base):
    """单班业务共用一行事务锁，阻止重复记分、抽签和开课的竞争。"""
    __tablename__ = "write_locks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")


event.listen(WriteLock.__table__, "after_create", DDL("INSERT INTO write_locks (id, revision) VALUES (1, 0)"))


class Klass(Base):
    """班级。名字叫 Klass 是为了避开 Python 的 class 关键字。

    设计取舍：学生表沿用已有的 class_name 字符串列关联班级（按名称关联），
    房间改名为「事务内同时更新 classes.name 与 students.class_name」。
    这样不必给 students 加外键、不用迁移历史数据，导入逻辑也完全不用改。
    """

    __tablename__ = "classes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    note: Mapped[str | None] = mapped_column(String(200), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Student(Base):
    __tablename__ = "students"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    student_no: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    major: Mapped[str | None] = mapped_column(String(100), default=None)
    class_name: Mapped[str | None] = mapped_column(String(50), default=None)

    # 以下四列是 point_logs 的「缓存」：查询排行榜/概率盘时不必聚合账本
    total_points: Mapped[float] = mapped_column(
        Numeric(6, 1, asdecimal=False), nullable=False, default=0.0, server_default="0"
    )
    call_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    random_call_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    arrive_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    transfer_credit: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)

    __table_args__ = (Index("idx_rank", "total_points", "call_count"),)


class RollSession(Base):
    """一场点名（一节课）。叫 Session 会和 SQLAlchemy 的 Session 撞名，故加 Roll 前缀。"""

    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 本场次只点这个班级的学生；为空表示「全部学生」（兼容没有分班的旧场次）
    class_name: Mapped[str | None] = mapped_column(String(50), default=None, index=True)
    mode: Mapped[str] = mapped_column(_enum(MODE_RANDOM, MODE_SEQUENTIAL, MODE_CLOCK), default=MODE_RANDOM)
    seq_cursor: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    round_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    draw_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(_enum(SESSION_OPEN, SESSION_CLOSED), default=SESSION_OPEN)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)

    rollcalls: Mapped[list["RollCall"]] = relationship(
        back_populates="session", cascade="all, delete-orphan", lazy="selectin"
    )


class RollCall(Base):
    """一次点名。status=pending 表示已点但还没记分，此时可以改判/转移。"""

    __tablename__ = "rollcalls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), index=True)
    round_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    seq: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    mode: Mapped[str] = mapped_column(_enum(MODE_RANDOM, MODE_SEQUENTIAL, MODE_CLOCK), default=MODE_RANDOM)

    arrived: Mapped[bool | None] = mapped_column(default=None)
    repeat_ok: Mapped[bool | None] = mapped_column(default=None)
    answer_score: Mapped[float | None] = mapped_column(Numeric(3, 1, asdecimal=False), default=None)
    point_delta: Mapped[float] = mapped_column(
        Numeric(5, 1, asdecimal=False), nullable=False, default=0.0, server_default="0"
    )
    status: Mapped[str] = mapped_column(_enum(RC_PENDING, RC_SCORED, RC_TRANSFERRED), default=RC_PENDING)
    transferred_from: Mapped[int | None] = mapped_column(Integer, default=None)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    session: Mapped["RollSession"] = relationship(back_populates="rollcalls")
    # lazy="joined"：列表页一次性把学生带出来，避免 N+1 查询
    student: Mapped["Student"] = relationship(lazy="joined")

    __table_args__ = (
        # 一轮不重复的数据库级兜底：应用层算错了也插不进去
        UniqueConstraint("session_id", "round_no", "student_id", name="uk_round"),
        Index("idx_session_seq", "session_id", "seq"),
    )


class PointLog(Base):
    """积分账本：只追加，不 Update、不 Delete。"""

    __tablename__ = "point_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), index=True)
    rollcall_id: Mapped[int | None] = mapped_column(
        ForeignKey("rollcalls.id", ondelete="SET NULL"), default=None
    )
    delta: Mapped[float] = mapped_column(Numeric(5, 1, asdecimal=False), nullable=False)
    reason: Mapped[str] = mapped_column(
        _enum(REASON_ARRIVE, REASON_REPEAT, REASON_ANSWER, REASON_EVENT, REASON_TRANSFER, REASON_MANUAL, length=16)
    )
    note: Mapped[str | None] = mapped_column(String(200), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)


class DrawLog(Base):
    """一次抽签的主记录：谁赢了、用的什么算法、α 是多少。"""

    __tablename__ = "draw_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    round_no: Mapped[int] = mapped_column(Integer, nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    alpha: Mapped[float] = mapped_column(Numeric(4, 2, asdecimal=False), nullable=False, default=1.0)
    algorithm: Mapped[str] = mapped_column(String(40), nullable=False, default="A-Res")
    winner_id: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    candidates: Mapped[list["DrawCandidate"]] = relationship(
        back_populates="draw", cascade="all, delete-orphan", lazy="selectin"
    )


class DrawCandidate(Base):
    """候选快照：权重 w 与随机数 u 落库，保证抽签结果可被任何人复算验证。"""

    __tablename__ = "draw_candidates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    draw_id: Mapped[int] = mapped_column(ForeignKey("draw_logs.id", ondelete="CASCADE"), index=True)
    student_id: Mapped[int] = mapped_column(Integer, nullable=False)
    weight: Mapped[float] = mapped_column(Double, nullable=False)
    rand_u: Mapped[float] = mapped_column(Double, nullable=False)
    sort_key: Mapped[float] = mapped_column(Double, nullable=False)

    draw: Mapped["DrawLog"] = relationship(back_populates="candidates")
