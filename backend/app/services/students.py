"""学生名单：分页查询 + Excel 幂等导入。

幂等的意思是：同一个名单文件传两遍，不会变成两套数据，也不会把已有积分清零。
实现方式是以 student_no 为唯一键做「有则更新、无则插入」。
"""

from __future__ import annotations

from typing import Iterable, Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..errors import AppError, ConflictError
from ..models import PointLog, RollCall, Student
from .transactions import serialized_write

CHUNK = 400  # SQLite 单条 SQL 的参数上限是 999，分块查询更稳妥


def _chunks(seq: Sequence[str]) -> Iterable[Sequence[str]]:
    for i in range(0, len(seq), CHUNK):
        yield seq[i : i + CHUNK]


def list_students(
    db: Session,
    *,
    page: int = 1,
    size: int = 20,
    keyword: str | None = None,
    class_name: str | None = None,
) -> tuple[int, list[Student]]:
    stmt = select(Student)
    count_stmt = select(func.count()).select_from(Student)

    keyword = (keyword or "").strip()
    if keyword:
        like = f"%{keyword}%"
        cond = or_(Student.student_no.like(like), Student.name.like(like), Student.major.like(like))
        stmt = stmt.where(cond)
        count_stmt = count_stmt.where(cond)

    class_name = (class_name or "").strip()
    if class_name:
        stmt = stmt.where(Student.class_name == class_name)
        count_stmt = count_stmt.where(Student.class_name == class_name)

    total = int(db.scalar(count_stmt) or 0)
    items = list(
        db.scalars(stmt.order_by(Student.student_no).offset((page - 1) * size).limit(size))
    )
    return total, items


@serialized_write
def upsert_students(
    db: Session, rows: list[dict], class_override: str | None = None
) -> tuple[int, int]:
    """按学号幂等写入，返回 (新增数, 更新数)。

    class_override：从「某个班级」里导入时，把整批学生强制归到这个班
    （忽略 Excel 里可能写了别的班级）。传 None 时按 Excel 的班级列。
    """
    if not rows:
        return 0, 0

    override = (class_override or "").strip() or None

    by_no: dict[str, dict] = {}
    for row in rows:
        by_no.setdefault(row["student_no"], row)  # 文件内重复已在解析阶段拦掉

    existing: dict[str, Student] = {}
    for chunk in _chunks(list(by_no)):
        for student in db.scalars(select(Student).where(Student.student_no.in_(list(chunk)))):
            existing[student.student_no] = student

    inserted = 0
    updated = 0
    to_insert: list[Student] = []
    for student_no, data in by_no.items():
        target_class = override or data.get("class_name")
        student = existing.get(student_no)
        if student is None:
            to_insert.append(
                Student(
                    student_no=student_no,
                    name=data["name"],
                    major=data.get("major"),
                    class_name=target_class,
                )
            )
            inserted += 1
        else:
            # 只覆盖基本信息，积分/次数等统计列一律保留
            student.name = data["name"]
            student.major = data.get("major")
            student.class_name = target_class
            updated += 1

    if to_insert:
        db.add_all(to_insert)  # 一次 flush 走 executemany，比逐行 commit 快一个数量级
    db.flush()

    if override:
        from .classes import ensure_class  # 局部导入避免循环依赖

        ensure_class(db, override)

    return inserted, updated


@serialized_write
def delete_student(db: Session, student_id: int) -> Student:
    """删除学生。

    已有点名记录的学生不允许删除：rollcalls / point_logs 是积分与公平性的审计依据，
    删掉学生等于让历史流水对不上号。想清空测试数据请重置数据库。
    """
    student = db.get(Student, student_id)
    if student is None:
        raise AppError("学生不存在", status_code=404)

    rollcalls = int(
        db.scalar(
            select(func.count()).select_from(RollCall).where(RollCall.student_id == student_id)
        )
        or 0
    )
    if rollcalls > 0:
        raise ConflictError(
            f"{student.name}（{student.student_no}）已有 {rollcalls} 次点名记录，"
            "为保留积分流水不能删除；如需清空请重置数据库"
        )

    db.execute(
        # 积分账本没有点名记录时也一并清掉，避免留下孤儿流水
        PointLog.__table__.delete().where(PointLog.student_id == student_id)
    )
    db.delete(student)
    db.flush()
    return student


def count_students(db: Session) -> int:
    return int(db.scalar(select(func.count()).select_from(Student)) or 0)
