"""班级接口：列表、新建、改名、删除。

注意：students.class_name 是学生归属班级的字段（按名称关联），
改名会在一个事务里同时改 classes.name 和学生的 class_name。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..schemas import ClassIn, ClassListOut, ClassOut, Resp
from ..services import classes as class_svc

router = APIRouter(prefix="/api/classes", tags=["班级"])


def _out(klass, count: int = 0) -> ClassOut:
    return ClassOut(id=klass.id, name=klass.name, note=klass.note, student_count=count)


@router.get("", response_model=Resp[ClassListOut], summary="班级列表（含各班人数）")
def list_classes(db: Session = Depends(get_db)):
    return Resp[ClassListOut](data=ClassListOut(**class_svc.list_classes(db)))


@router.post("", response_model=Resp[ClassOut], summary="新建班级")
def create_class(payload: ClassIn, db: Session = Depends(get_db)):
    klass = class_svc.create_class(db, payload.name, payload.note)
    return Resp[ClassOut](data=_out(klass))


@router.patch("/{class_id}", response_model=Resp[ClassOut], summary="修改班级名称")
def rename_class(class_id: int, payload: ClassIn, db: Session = Depends(get_db)):
    klass = class_svc.rename_class(db, class_id, payload.name)
    return Resp[ClassOut](data=_out(klass))


@router.delete("/{class_id}", response_model=Resp[dict], summary="删除班级（可连学生一起删）")
def delete_class(
    class_id: int,
    force: bool = Query(False, description="true = 连该班级的学生一起删除（不可恢复）"),
    db: Session = Depends(get_db),
):
    return Resp[dict](data=class_svc.delete_class(db, class_id, force=force))
