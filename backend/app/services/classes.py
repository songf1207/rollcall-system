"""班级业务逻辑。

存储方案：classes 表只管班级本身（名称 + 备注），
学生通过 students.class_name 这个既有字符串列按名称归属，
因此不需要给 students 加外键、不用迁移历史数据、导入逻辑零改动。

改名的代价是「事务内批量改 students.class_name」，
好处是任何已有数据（包括手工导入的班级名）都能直接出现在班级列表里。
"""

from __future__ import annotations

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from ..errors import AppError, ConflictError, NotFoundError
from ..models import Klass, PointLog, RollCall, Student
from .transactions import serialized_write

MAX_NAME = 50


def _normalize(name: str | None) -> str:
    return (name or "").strip()


def _validate(name: str) -> str:
    name = _normalize(name)
    if not name:
        raise AppError("班级名称不能为空")
    if len(name) > MAX_NAME:
        raise AppError(f"班级名称不能超过 {MAX_NAME} 个字符")
    return name


def _count_map(db: Session) -> dict[str, int]:
    rows = db.execute(
        select(Student.class_name, func.count()).group_by(Student.class_name)
    ).all()
    return {(name or ""): int(count) for name, count in rows}


def list_classes(db: Session) -> dict:
    """班级列表 + 每个班人数 + 学生总数。"""
    counts = _count_map(db)
    classes = list(db.scalars(select(Klass).order_by(Klass.name)))
    items = [
        {"id": c.id, "name": c.name, "note": c.note, "student_count": counts.get(c.name, 0)}
        for c in classes
    ]

    # 历史数据里可能只有 students.class_name 而没有 classes 记录（比如手工导入的名单），
    # 这些班级也要能在列表里看到，用 id=0 表示「还没登记成班级」。
    known = {c.name for c in classes}
    for name, count in counts.items():
        if name and name not in known:
            items.append({"id": 0, "name": name, "note": None, "student_count": count})
    items.sort(key=lambda x: x["name"])

    return {"items": items, "total_students": sum(counts.values())}


def get_class(db: Session, class_id: int) -> Klass:
    klass = db.get(Klass, class_id)
    if klass is None:
        raise NotFoundError(f"班级 {class_id} 不存在")
    return klass


@serialized_write
def create_class(db: Session, name: str, note: str | None = None) -> Klass:
    name = _validate(name)
    if db.scalar(select(Klass).where(Klass.name == name)) is not None:
        raise ConflictError(f"班级「{name}」已存在")
    klass = Klass(name=name, note=_normalize(note) or None)
    db.add(klass)
    db.flush()
    return klass


@serialized_write
def rename_class(db: Session, class_id: int, name: str) -> Klass:
    klass = db.get(Klass, class_id)
    if klass is None:
        raise NotFoundError(f"班级 {class_id} 不存在")
    name = _validate(name)
    if name == klass.name:
        return klass
    if db.scalar(select(Klass).where(Klass.name == name, Klass.id != class_id)) is not None:
        raise ConflictError(f"班级「{name}」已存在")

    old = klass.name
    klass.name = name
    # 学生按名称关联，改名要同步改过去（同一个事务内完成）
    db.execute(
        update(Student).where(Student.class_name == old).values(class_name=name),
        execution_options={"synchronize_session": False},
    )
    db.flush()
    return klass


@serialized_write
def delete_class(db: Session, class_id: int, force: bool = False) -> dict:
    """删除班级。

    force=False（默认）：班级下还有学生就拒绝，避免误删。
    force=True：连班级里的学生一起删掉（学生会连同自己的点名记录、积分流水一起清除）。
                 这是「整批清掉导错的名单」用的，属不可恢复操作。
    """
    klass = db.get(Klass, class_id)
    if klass is None:
        raise NotFoundError(f"班级 {class_id} 不存在")

    students = list(db.scalars(select(Student).where(Student.class_name == klass.name)))
    if students and not force:
        raise ConflictError(
            f"班级「{klass.name}」下还有 {len(students)} 名学生，"
            "请先移走学生，或选择「连学生一起删除」"
        )

    deleted_students = 0
    if students:
        ids = [s.id for s in students]
        # 先清子表再删学生，避免外键报错或留下孤儿流水
        db.execute(delete(PointLog).where(PointLog.student_id.in_(ids)))
        db.execute(delete(RollCall).where(RollCall.student_id.in_(ids)))
        db.execute(delete(Student).where(Student.id.in_(ids)))
        deleted_students = len(ids)

    db.delete(klass)
    db.flush()
    return {"deleted": class_id, "deleted_students": deleted_students}


def ensure_class(db: Session, name: str) -> Klass | None:
    """导入名单时顺带把班级登记进 classes 表（已存在就跳过）。"""
    name = _normalize(name)
    if not name:
        return None
    klass = db.scalar(select(Klass).where(Klass.name == name))
    if klass is not None:
        return klass
    klass = Klass(name=name[:MAX_NAME])
    db.add(klass)
    db.flush()
    return klass
