"""统计：排行榜、概率盘、历史记录。

排行榜直接读 students 的缓存列（已建索引），不聚合账本；
概率盘直接把权重归一化后的概率给前端 —— 概率是公开可解释的，
恰恰能证明「随机不是编的」。
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..core.weights import probabilities, weight_of
from ..errors import NotFoundError
from ..models import MODE_SEQUENTIAL, PointLog, RollCall, Student
from .draw import current_session, get_session, next_candidates


def ranking(db: Session, top: int = 10, class_name: str | None = None) -> dict:
    """Top N 排行榜，同时给出 ECharts 直接能用的数组。

    class_name 传了就只统计这个班，否则是全体学生。
    """
    top = max(1, min(int(top), 100))
    stmt = select(Student).order_by(
        Student.total_points.desc(),
        Student.call_count.asc(),
        Student.student_no.asc(),
    )
    name = (class_name or "").strip()
    if name:
        stmt = stmt.where(Student.class_name == name)
    rows = list(db.scalars(stmt.limit(top)))

    items = []
    for i, s in enumerate(rows, start=1):
        items.append(
            {
                "rank": i,
                "student_id": s.id,
                "student_no": s.student_no,
                "name": s.name,
                "major": s.major,
                "total_points": round(float(s.total_points), 1),
                "call_count": s.call_count,
                "random_call_count": s.random_call_count,
            }
        )

    return {
        "items": items,
        "categories": [f"{s.name}" for s in rows],
        "points_series": [round(float(s.total_points), 1) for s in rows],
        "calls_series": [s.random_call_count for s in rows],
    }


def probability(
    db: Session,
    session_id: int | None = None,
    alpha: float | None = None,
    class_name: str | None = None,
) -> dict:
    """预览下一次点名：轮次、候选和顺序模式均与实际点名共用规则。

    有场次时以场次绑定的班级为准；没场次时才用传入的 class_name 预览。
    """
    alpha = settings.alpha if alpha is None else float(alpha)

    if session_id is not None:
        session = get_session(db, session_id)
    else:
        session = current_session(db)

    # 预览范围：优先场次绑定的班级，其次是显式传入的班级
    scope_name = (session.class_name if session is not None else None) or class_name
    scope_name = (scope_name or "").strip()
    stu_stmt = select(Student).order_by(Student.student_no)
    if scope_name:
        stu_stmt = stu_stmt.where(Student.class_name == scope_name)
    students = list(db.scalars(stu_stmt))
    round_no, is_new_round = 1, False
    remaining = students
    if session is not None:
        remaining, round_no, is_new_round = next_candidates(db, session)
    eligible_ids = {s.id for s in remaining}
    weights = [weight_of(s.total_points, alpha) for s in students]
    remaining_idx = [i for i, s in enumerate(students) if s.id in eligible_ids]
    final = [0.0] * len(students)
    if session and session.mode == MODE_SEQUENTIAL and remaining:
        final = [float(s.id == remaining[0].id) for s in students]
    elif remaining_idx:
        part = probabilities([weights[i] for i in remaining_idx])
        for pos, i in enumerate(remaining_idx):
            final[i] = part[pos]

    items = []
    for i, s in enumerate(students):
        items.append(
            {
                "student_id": s.id,
                "student_no": s.student_no,
                "name": s.name,
                "total_points": round(float(s.total_points), 1),
                "weight": round(weights[i], 6),
                "probability": round(final[i], 6),
                "excluded": s.id not in eligible_ids,
            }
        )

    return {
        "session_id": session.id if session else 0,
        "mode": session.mode if session else "random",
        "round_no": round_no,
        "is_new_round": is_new_round,
        "alpha": alpha,
        "items": items,
    }


def student_points(db: Session, student_id: int) -> dict:
    """按账本追加顺序还原个人积分；改判也作为后续变动保留。"""
    student = db.get(Student, student_id)
    if student is None:
        raise NotFoundError("学生不存在")
    logs = list(db.scalars(select(PointLog).where(PointLog.student_id == student_id).order_by(PointLog.id)))
    total = 0.0
    items = []
    for log in logs:
        total = round(total + float(log.delta), 1)
        items.append({
            "log_id": log.id, "rollcall_id": log.rollcall_id,
            "delta": float(log.delta), "total_points": total,
            "reason": log.reason, "note": log.note, "created_at": log.created_at,
        })
    return {
        "student": student, "items": items,
        "categories": ["初始"] + [f"流水 {item['log_id']}" for item in items],
        "points_series": [0.0] + [item["total_points"] for item in items],
    }


def history(
    db: Session,
    *,
    page: int = 1,
    size: int = 20,
    session_id: int | None = None,
    class_name: str | None = None,
) -> dict:
    stmt = select(RollCall)
    count_stmt = select(func.count()).select_from(RollCall)
    if session_id is not None:
        stmt = stmt.where(RollCall.session_id == session_id)
        count_stmt = count_stmt.where(RollCall.session_id == session_id)

    name = (class_name or "").strip()
    if name:
        stmt = stmt.join(Student, Student.id == RollCall.student_id).where(
            Student.class_name == name
        )
        count_stmt = count_stmt.join(Student, Student.id == RollCall.student_id).where(
            Student.class_name == name
        )

    total = int(db.scalar(count_stmt) or 0)
    rows = list(
        db.scalars(
            stmt.order_by(RollCall.id.desc()).offset((page - 1) * size).limit(size)
        )
    )

    items = []
    for rc in rows:
        student = rc.student  # lazy="joined"，这里不会再发 SQL
        items.append(
            {
                "rollcall_id": rc.id,
                "student_no": student.student_no if student else "",
                "name": student.name if student else "",
                "mode": rc.mode,
                "round_no": rc.round_no,
                "seq": rc.seq,
                "arrived": rc.arrived,
                "repeat_ok": rc.repeat_ok,
                "answer_score": float(rc.answer_score) if rc.answer_score is not None else None,
                "point_delta": round(float(rc.point_delta), 1),
                "status": rc.status,
                "called_at": rc.created_at,
            }
        )
    return {"total": total, "page": page, "size": size, "items": items}


def export_rows(db: Session, class_name: str | None = None) -> list[dict]:
    """导出用的行：5 列一一对应，顺序按学号。传 class_name 则只导出该班。"""
    stmt = select(Student).order_by(Student.student_no)
    name = (class_name or "").strip()
    if name:
        stmt = stmt.where(Student.class_name == name)
    rows = list(db.scalars(stmt))
    return [
        {
            "student_no": s.student_no,
            "name": s.name,
            "major": s.major,
            "random_call_count": s.random_call_count,
            "total_points": round(float(s.total_points), 1),
        }
        for s in rows
    ]
