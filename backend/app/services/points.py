"""记分 / 改判 / 转移 —— 积分账本的核心逻辑。

积分规则（作业原文）：
- 点到且到达课堂        +1
- 准确复述所提问题      +0.5，没复述对 -1
- 准确回答问题          +0.5 ~ +3（未作答记 0）

设计要点：
- 账本（point_logs）只追加。改判不 Update 旧记录，而是补一条 reason=manual 的
  冲正记录，delta = 新分 - 旧分。这样「谁在什么时候改了谁的分」永远查得到。
- 缓存列（students.total_points 等）与账本在同一个事务里更新，保证不会对不上。
"""

from __future__ import annotations

import math
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..errors import AppError, ConflictError, NotFoundError
from ..models import (
    RC_PENDING,
    RC_SCORED,
    RC_TRANSFERRED,
    REASON_ANSWER,
    REASON_ARRIVE,
    REASON_MANUAL,
    REASON_REPEAT,
    REASON_TRANSFER,
    PointLog,
    RollCall,
    Student,
    RollSession,
    SESSION_OPEN,
)
from .transactions import serialized_write


def get_rollcall(db: Session, rollcall_id: int) -> RollCall:
    rollcall = db.get(RollCall, rollcall_id)
    if rollcall is None:
        raise NotFoundError(f"点名记录 {rollcall_id} 不存在")
    return rollcall


def compute_components(
    arrived: bool, repeat_ok: bool | None, answer_score: float | None
) -> list[tuple[str, float, str]]:
    """把一次记分拆成若干条账本条目，返回 [(reason, delta, note), ...]。"""
    parts: list[tuple[str, float, str]] = []

    if not arrived and (repeat_ok is not None or answer_score not in (None, 0)):
        raise AppError("缺席时不能填写复述或回答得分，请将复述设为 null、回答设为 0 或 null")
    if answer_score is not None:
        raw_score = float(answer_score)
        low, high = settings.answer_min, settings.answer_max
        if not math.isfinite(raw_score) or (raw_score != 0 and not low <= raw_score <= high):
            raise AppError(f"回答问题得分需在 {low}~{high} 之间（未作答传 0 或 null）")

    if arrived:
        parts.append((REASON_ARRIVE, float(settings.point_arrive), "到达课堂"))

    if repeat_ok is True:
        parts.append((REASON_REPEAT, float(settings.point_repeat_ok), "准确复述问题"))
    elif repeat_ok is False:
        parts.append((REASON_REPEAT, float(settings.point_repeat_fail), "未准确复述问题"))

    if answer_score is not None and float(answer_score) > 0:
        score = round(float(answer_score), 1)
        parts.append((REASON_ANSWER, score, "回答问题"))

    return parts


def register_call(student: Student, *, randomly_selected: bool) -> None:
    """点到即计数；被转移到的学生增加总次数，但不是随机抽签中签。"""
    student.call_count += 1
    if randomly_selected:
        student.random_call_count += 1
    if settings.transfer_every > 0 and student.call_count % settings.transfer_every == 0:
        student.transfer_credit += 1


def _sync_cache(student: Student, delta: float, *, arrived: bool) -> None:
    """记分只更新积分和到场次数，避免再次增加点名次数。"""
    student.total_points = round(float(student.total_points) + delta, 1)
    if arrived:
        student.arrive_count += 1


@serialized_write
def score_rollcall(
    db: Session,
    rollcall: RollCall,
    *,
    arrived: bool,
    repeat_ok: bool | None,
    answer_score: float | None,
) -> RollCall:
    """首次记分。重复记分会被拒绝，引导前端改用改判接口。"""
    if rollcall.status != RC_PENDING:
        raise ConflictError("本次点名已经记过分了，如需修改请调用改判接口（PATCH）")

    parts = compute_components(arrived, repeat_ok, answer_score)
    delta = round(sum(item[1] for item in parts), 1)

    student = db.get(Student, rollcall.student_id)
    if student is None:
        raise NotFoundError("被点名的学生已被删除")

    rollcall.arrived = arrived
    rollcall.repeat_ok = repeat_ok
    rollcall.answer_score = round(float(answer_score), 1) if answer_score is not None else None
    rollcall.point_delta = delta
    rollcall.status = RC_SCORED

    _sync_cache(student, delta, arrived=arrived)

    for reason, item_delta, note in parts:
        db.add(
            PointLog(
                student_id=student.id,
                rollcall_id=rollcall.id,
                delta=item_delta,
                reason=reason,
                note=note,
            )
        )

    db.flush()
    db.refresh(rollcall)
    return rollcall


@serialized_write
def rejudge_rollcall(
    db: Session,
    rollcall: RollCall,
    *,
    arrived: bool,
    repeat_ok: bool | None,
    answer_score: float | None,
) -> RollCall:
    """改判：补一条冲正记录，而不是覆盖旧数据。"""
    if rollcall.status == RC_TRANSFERRED:
        raise ConflictError("这次点名已经被转移给别的同学了，无法改判")
    if rollcall.status != RC_SCORED:
        raise ConflictError("这次点名还没记分，请先调用记分接口（POST）")

    old_delta = float(rollcall.point_delta)
    old_arrived = bool(rollcall.arrived)

    parts = compute_components(arrived, repeat_ok, answer_score)
    new_delta = round(sum(item[1] for item in parts), 1)
    diff = round(new_delta - old_delta, 1)

    student = db.get(Student, rollcall.student_id)
    if student is None:
        raise NotFoundError("被点名的学生已被删除")

    rollcall.arrived = arrived
    rollcall.repeat_ok = repeat_ok
    rollcall.answer_score = round(float(answer_score), 1) if answer_score is not None else None
    rollcall.point_delta = new_delta

    student.total_points = round(float(student.total_points) + diff, 1)
    if arrived != old_arrived:
        student.arrive_count += 1 if arrived else -1

    if diff != 0 or arrived != old_arrived:
        db.add(
            PointLog(
                student_id=student.id,
                rollcall_id=rollcall.id,
                delta=diff,
                reason=REASON_MANUAL,
                note=f"改判：{old_delta} -> {new_delta}",
            )
        )

    db.flush()
    db.refresh(rollcall)
    return rollcall


@serialized_write
def transfer_rollcall(db: Session, rollcall: RollCall, to_student_id: int) -> RollCall:
    """点名转移：把这次点名让给另一位同学，消耗原被点者 1 次转移权。"""
    if rollcall.status != RC_PENDING:
        raise ConflictError("只能转移「已点但还没记分」的那次点名")
    session = db.get(RollSession, rollcall.session_id)
    if session is None or session.status != SESSION_OPEN:
        raise ConflictError("该场次已结束，不能再转移点名")
    if rollcall.round_no != session.round_no:
        raise ConflictError("只能转移当前轮次的点名记录")
    if settings.transfer_every <= 0:
        raise ConflictError("点名转移功能已关闭")

    source = db.get(Student, rollcall.student_id)
    target = db.get(Student, to_student_id)
    if source is None or target is None:
        raise NotFoundError("学生不存在")
    if target.id == source.id:
        raise AppError("不能把点名转移给自己")
    if source.transfer_credit < 1:
        raise ConflictError(
            f"{source.name} 还没有可用的点名转移权（每被点 {settings.transfer_every} 次获得 1 次）"
        )

    already = db.scalar(
        select(RollCall).where(
            RollCall.session_id == rollcall.session_id,
            RollCall.round_no == rollcall.round_no,
            RollCall.student_id == target.id,
        )
    )
    if already is not None:
        raise ConflictError(f"{target.name} 本轮已经被点过了，换一位同学吧")

    source.transfer_credit -= 1
    register_call(target, randomly_selected=False)
    rollcall.status = RC_TRANSFERRED
    rollcall.point_delta = 0

    new_rollcall = RollCall(
        session_id=rollcall.session_id,
        student_id=target.id,
        round_no=rollcall.round_no,
        seq=rollcall.seq,
        mode=rollcall.mode,
        status=RC_PENDING,
        transferred_from=source.id,
    )
    db.add(new_rollcall)
    db.add(
        PointLog(
            student_id=source.id,
            rollcall_id=rollcall.id,
            delta=0,
            reason=REASON_TRANSFER,
            note=f"点名转移给 {target.name}（{target.student_no}）",
        )
    )
    db.flush()
    db.refresh(new_rollcall)
    return new_rollcall
