"""名单相关接口：Excel 导入 + 分页查询 + 删除。"""

from __future__ import annotations

from starlette.concurrency import run_in_threadpool
from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from sqlalchemy.orm import Session

from ..database import get_db
from ..errors import AppError
from ..schemas import (
    DeleteStudentResult,
    ImportErrorItem,
    ImportResult,
    Resp,
    StudentOut,
    StudentPage,
)
from ..services import students as student_svc
from ..services.excel_io import parse_workbook

router = APIRouter(prefix="/api/students", tags=["名单"])

MAX_UPLOAD_BYTES = 5 * 1024 * 1024


@router.post("/import", response_model=Resp[ImportResult], summary="从 Excel 导入学生名单")
async def import_students(
    file: UploadFile = File(..., description=".xlsx 文件，表头需含学号与姓名"),
    class_name: str | None = Form(
        default=None, description="指定班级：传入后整批学生强制归入该班（忽略 Excel 班级列）"
    ),
    db: Session = Depends(get_db),
):
    """按学号幂等导入：已存在的学生只更新姓名/专业，积分与点名次数保留。

    脏数据不会导致整批失败 —— 能导的先入库，出错的行连同 Excel 行号一起返回。
    """
    filename = (file.filename or "").lower()
    if not filename.endswith((".xlsx", ".xlsm")):
        raise AppError("只支持 .xlsx 文件（.xls 请先在 Excel 里另存为 .xlsx）")

    data = await file.read()
    if not data:
        raise AppError("上传的文件是空的")
    if len(data) > MAX_UPLOAD_BYTES:
        raise AppError("文件超过 5MB，请先清理无用的列和行")

    def import_in_worker():
        parsed = parse_workbook(data)
        inserted, updated = student_svc.upsert_students(db, parsed.rows, class_override=class_name)
        return parsed, inserted, updated

    parsed, inserted, updated = await run_in_threadpool(import_in_worker)

    result = ImportResult(
        total_rows=parsed.raw_count,
        inserted=inserted,
        updated=updated,
        failed=[ImportErrorItem(row=e.row, reason=e.reason) for e in parsed.errors],
        header_map=parsed.header_map,
    )
    return Resp[ImportResult](data=result)


@router.get("", response_model=Resp[StudentPage], summary="学生名单（分页 + 搜索 + 按班级筛选）")
def list_students(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=200),
    keyword: str | None = Query(None, description="按学号/姓名/专业模糊搜索"),
    class_name: str | None = Query(None, description="只看这个班级的学生"),
    db: Session = Depends(get_db),
):
    total, items = student_svc.list_students(
        db, page=page, size=size, keyword=keyword, class_name=class_name
    )
    return Resp[StudentPage](
        data=StudentPage(
            total=total,
            page=page,
            size=size,
            items=[StudentOut.model_validate(item) for item in items],
        )
    )


@router.delete("/{student_id}", response_model=Resp[DeleteStudentResult], summary="删除学生")
def delete_student(student_id: int, db: Session = Depends(get_db)):
    """删除学生。已有点名记录的学生会被拒绝（409），以保留积分流水的可审计性。"""
    student = student_svc.delete_student(db, student_id)
    return Resp[DeleteStudentResult](
        data=DeleteStudentResult(
            student_id=student.id, student_no=student.student_no, name=student.name
        )
    )
