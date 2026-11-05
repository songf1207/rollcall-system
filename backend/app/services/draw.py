"""点名：开/关场次、抽签、把抽签过程完整落库。

三种模式：
- random：加权随机（积分越高越不容易被点到），一轮内不重复；
- sequential：按学号顺序，seq_cursor 记住进度，点完一轮自动进入下一轮；
- clock：按时间点名 —— 由前端按时钟尾号筛出候选，老师在其中手动挑一个
  （pick_student），被挑中的人同样计入「随机点名次数」。

一次加权抽签会写 3 张表，全部在同一个事务里：
draw_logs（主记录） -> draw_candidates（每个候选人的 w 和 u，供复算）
                    -> rollcalls（这次点名本身，供记分）
"""

from __future__ import annotations

import math

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..core.weights import new_rng, sample_without_replacement, weight_of
from ..errors import AppError, ConflictError, NotFoundError
from ..models import (
    MODE_CLOCK,
    MODE_RANDOM,
    MODE_SEQUENTIAL,
    SESSION_CLOSED,
    SESSION_OPEN,
    DrawCandidate,
    DrawLog,
    RollCall,
    RollSession,
    Student,
)
from ..utils.time import now
from .points import register_call
from .transactions import serialized_write

MAX_DB_FLOAT = 1e308  # MySQL 的 DOUBLE 存不了 inf，落库前钳一下


def _db_float(value: float) -> float:
    if value is None or not math.isfinite(value):
        return MAX_DB_FLOAT
    return float(value)


@serialized_write
def create_session(
    db: Session, mode: str = MODE_RANDOM, class_name: str | None = None
) -> RollSession:
    """新建场次，并把之前没关的场次关掉 —— 保证「当前场次」永远唯一。

    class_name 决定这个场次点哪个班的学生；为空表示全部学生。
    """
    for old in db.scalars(select(RollSession).where(RollSession.status == SESSION_OPEN)):
        old.status = SESSION_CLOSED
        old.ended_at = now()

    session = RollSession(
        mode=mode,
        status=SESSION_OPEN,
        class_name=(class_name or "").strip() or None,
    )
    db.add(session)
    db.flush()
    db.refresh(session)
    return session


def _class_filter(session: RollSession | None):
    """场次限定的班级条件；None 表示不限定（全部学生）。"""
    if session is None:
        return None
    name = (session.class_name or "").strip()
    return Student.class_name == name if name else None


def current_session(db: Session) -> RollSession | None:
    return db.scalar(
        select(RollSession)
        .where(RollSession.status == SESSION_OPEN)
        .order_by(RollSession.id.desc())
        .limit(1)
    )


def get_session(db: Session, session_id: int) -> RollSession:
    session = db.get(RollSession, session_id)
    if session is None:
        raise NotFoundError(f"场次 {session_id} 不存在")
    return session


@serialized_write
def close_session(db: Session, session: RollSession) -> RollSession:
    session.status = SESSION_CLOSED
    session.ended_at = now()
    db.flush()
    db.refresh(session)
    return session


def _progress(db: Session, session: RollSession, total: int, is_new_round: bool) -> dict:
    called = int(
        db.scalar(
            select(func.count())
            .select_from(RollCall)
            .where(RollCall.session_id == session.id, RollCall.round_no == session.round_no)
        )
        or 0
    )
    return {
        "round_no": session.round_no,
        "called_in_round": called,
        "remaining_in_round": max(total - called, 0),
        "total_students": total,
        "is_new_round": is_new_round,
    }


@serialized_write
def draw_next(db: Session, session: RollSession, alpha: float | None = None):
    """点下一位，返回 (rollcall, 进度信息)。"""
    if session.status != SESSION_OPEN:
        raise ConflictError("该场次已结束，请先新建场次再点名")

    alpha = settings.alpha if alpha is None else float(alpha)
    if not (0.0 <= alpha <= 5.0):
        raise AppError("alpha 需要在 0~5 之间（0 表示等概率）")

    total_stmt = select(func.count()).select_from(Student)
    scope = _class_filter(session)
    if scope is not None:
        total_stmt = total_stmt.where(scope)
    total = int(db.scalar(total_stmt) or 0)
    if total == 0:
        raise AppError(
            f"班级「{session.class_name}」还没有学生，请先导入名单"
            if session.class_name
            else "名单为空，请先通过 Excel 导入学生名单"
        )

    if session.mode == MODE_SEQUENTIAL:
        rollcall, is_new_round = _draw_sequential(db, session)
    elif session.mode == MODE_CLOCK:
        raise AppError("「按时间点名」需要老师从候选列表里选人，请调用 pick 接口")
    else:
        rollcall, is_new_round = _draw_random(db, session, alpha)

    session.draw_count += 1
    db.flush()
    db.refresh(rollcall)

    return rollcall, _progress(db, session, total, is_new_round)


def next_candidates(db: Session, session: RollSession) -> tuple[list[Student], int, bool]:
    """只读预览下一次点名的候选；抽签与概率盘共用，查询不会推进轮次。"""
    if session.status != SESSION_OPEN:
        raise ConflictError("该场次已结束，请先新建场次再点名")
    ordering = (Student.student_no, Student.id) if session.mode == MODE_SEQUENTIAL else (Student.id,)
    stmt = select(Student).order_by(*ordering)
    scope = _class_filter(session)
    if scope is not None:
        stmt = stmt.where(scope)   # 只在本班学生里点，别把别班的人抽出来
    students = list(db.scalars(stmt))
    if session.mode == MODE_SEQUENTIAL and students:
        cursor = session.seq_cursor % len(students)
        students = students[cursor:] + students[:cursor]
    called = set(
        db.scalars(
            select(RollCall.student_id).where(
                RollCall.session_id == session.id, RollCall.round_no == session.round_no
            )
        )
    )
    remaining = [s for s in students if s.id not in called]

    is_new_round = bool(students) and not remaining
    return remaining or students, session.round_no + int(is_new_round), is_new_round


def clock_candidates(db: Session, session: RollSession, digit: int) -> tuple[list[dict], dict]:
    """按时间点名的候选：学号尾号 == 当前分钟个位的本班学生。

    与随机/顺序模式不同，这里**不排除**本轮已点过的人 ——
    老师点的就是"现在 5 分尾巴的这几位"，点过之后还能再点（例如同一人再答一题）。
    已点次数通过 called_count 返回，前端在卡片上标出来。
    """
    if session.status != SESSION_OPEN:
        raise ConflictError("该场次已结束，请先新建场次再点名")
    digit = int(digit) % 10

    stmt = select(Student).order_by(Student.student_no)
    scope = _class_filter(session)
    if scope is not None:
        stmt = stmt.where(scope)
    students = list(db.scalars(stmt))

    called_rows = db.execute(
        select(RollCall.student_id, func.count())
        .where(RollCall.session_id == session.id, RollCall.round_no == session.round_no)
        .group_by(RollCall.student_id)
    ).all()
    counts = {sid: int(c) for sid, c in called_rows}

    items = [
        {"student": s, "called_count": counts.get(s.id, 0)}
        for s in students
        if (s.student_no or "").strip()[-1:] == str(digit)
    ]
    return items, _progress(db, session, len(students), False)


def _record_pick(db: Session, session: RollSession, student: Student, algorithm: str):
    """把一次「按时间点名」的选中结果落库（rollcalls + draw_logs），返回 (rollcall, progress)。"""
    # rollcalls 有 (session, round, student) 唯一约束；同一轮里重复点同一人时自动进下一轮，
    # 这样「再点他一次」不会撞约束，而「一轮不重复」的语义依然成立。
    already = db.scalar(
        select(func.count())
        .select_from(RollCall)
        .where(
            RollCall.session_id == session.id,
            RollCall.round_no == session.round_no,
            RollCall.student_id == student.id,
        )
    )
    if int(already or 0) > 0:
        session.round_no += 1

    register_call(student, randomly_selected=True)  # 计入随机点名次数（按需求）
    seq = session.draw_count + 1
    session.draw_count = seq

    db.add(
        DrawLog(
            session_id=session.id,
            round_no=session.round_no,
            seq=seq,
            alpha=0.0,
            algorithm=algorithm,
            winner_id=student.id,
        )
    )

    rollcall = RollCall(
        session_id=session.id,
        student_id=student.id,
        round_no=session.round_no,
        seq=seq,
        mode=MODE_CLOCK,
    )
    db.add(rollcall)
    db.flush()
    db.refresh(rollcall)

    total_stmt = select(func.count()).select_from(Student)
    scope = _class_filter(session)
    if scope is not None:
        total_stmt = total_stmt.where(scope)
    total = int(db.scalar(total_stmt) or 0)
    return rollcall, _progress(db, session, total, False)


@serialized_write
def pick_student(db: Session, session: RollSession, student_id: int):
    """在「按时间点名」模式下手动选定一位同学。

    与随机抽签的区别只是「谁被选中」由老师决定；
    同样计入随机点名次数、写入 rollcalls，后续记分/改判流程完全一致。
    允许重复点同一个人（老师可以让他再答一题），次数照常累加。
    """
    if session.status != SESSION_OPEN:
        raise ConflictError("该场次已结束，请先新建场次再点名")

    student = db.get(Student, student_id)
    if student is None:
        raise NotFoundError("学生不存在")

    scope_name = (session.class_name or "").strip()
    if scope_name and (student.class_name or "") != scope_name:
        raise AppError(f"{student.name} 不在「{scope_name}」这个班里")

    return _record_pick(db, session, student, "manual-pick")


@serialized_write
def pick_random_in_clock(
    db: Session, session: RollSession, digit: int, alpha: float | None = None
):
    """在「学号尾号 = digit」的候选里再按加权随机抽一个。

    权重规则与随机点名完全一致（积分越高越不容易被抽中），
    只是候选池从「全班」缩小成「当前分钟尾巴相同的那几个人」。
    """
    if session.status != SESSION_OPEN:
        raise ConflictError("该场次已结束，请先新建场次再点名")

    rows, _ = clock_candidates(db, session, digit)
    candidates = [row["student"] for row in rows]
    if not candidates:
        raise AppError(f"学号尾号为 {digit} 的同学一个也没有，请先导入名单")

    alpha = settings.alpha if alpha is None else float(alpha)
    weights = [weight_of(s.total_points, alpha) for s in candidates]
    picked, _, algorithm = sample_without_replacement(
        [s.id for s in candidates], weights, 1, new_rng()
    )
    winner = next(s for s in candidates if s.id == picked[0])
    return _record_pick(db, session, winner, f"{algorithm}(clock)")


def _draw_random(db: Session, session: RollSession, alpha: float):
    remaining, round_no, is_new_round = next_candidates(db, session)
    session.round_no = round_no

    weights = [weight_of(s.total_points, alpha) for s in remaining]
    picked, candidates, algorithm = sample_without_replacement(
        [s.id for s in remaining], weights, 1, new_rng()
    )
    winner_id = picked[0]
    register_call(next(s for s in remaining if s.id == winner_id), randomly_selected=True)
    seq = session.draw_count + 1

    draw = DrawLog(
        session_id=session.id,
        round_no=session.round_no,
        seq=seq,
        alpha=alpha,
        algorithm=algorithm,
        winner_id=winner_id,
    )
    db.add(draw)
    db.flush()  # 先拿到 draw.id

    for cand in candidates:
        db.add(
            DrawCandidate(
                draw_id=draw.id,
                student_id=cand.student_id,
                weight=_db_float(cand.weight),
                rand_u=cand.rand_u,
                sort_key=_db_float(cand.sort_key),
            )
        )

    rollcall = RollCall(
        session_id=session.id,
        student_id=winner_id,
        round_no=session.round_no,
        seq=seq,
        mode=MODE_RANDOM,
    )
    db.add(rollcall)
    db.flush()
    return rollcall, is_new_round


def _draw_sequential(db: Session, session: RollSession):
    remaining, round_no, is_new_round = next_candidates(db, session)
    session.round_no = round_no
    picked = remaining[0]
    register_call(picked, randomly_selected=False)
    order = list(db.scalars(select(Student).order_by(Student.student_no, Student.id)))
    # 转移可能使游标跳过多人，必须从实际被点者的下一位继续。
    session.seq_cursor = next(i for i, s in enumerate(order) if s.id == picked.id) + 1
    seq = session.draw_count + 1

    db.add(
        DrawLog(
            session_id=session.id,
            round_no=session.round_no,
            seq=seq,
            alpha=0.0,
            algorithm="sequential",
            winner_id=picked.id,
        )
    )

    rollcall = RollCall(
        session_id=session.id,
        student_id=picked.id,
        round_no=session.round_no,
        seq=seq,
        mode=MODE_SEQUENTIAL,
    )
    db.add(rollcall)
    db.flush()
    return rollcall, is_new_round
