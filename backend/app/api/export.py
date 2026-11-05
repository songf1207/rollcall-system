"""导出接口：积分详单 xlsx。"""

from __future__ import annotations

from datetime import datetime
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from ..database import get_db
from ..services import stats as stats_svc
from ..services.excel_io import build_points_workbook

router = APIRouter(prefix="/api/export", tags=["导出"])

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@router.get("/points", summary="导出积分详单（学号/姓名/专业/随机点名次数/总积分）")
def export_points(
    class_name: str | None = Query(None, description="只导出这个班级；不传为全体学生"),
    db: Session = Depends(get_db),
):
    rows = stats_svc.export_rows(db, class_name)
    content = build_points_workbook(rows)
    prefix = (class_name or "全部").strip() or "全部"
    filename = f"积分详单_{prefix}_{datetime.now():%Y%m%d_%H%M%S}.xlsx"
    return Response(
        content=content,
        media_type=XLSX_MIME,
        headers={
            # RFC 5987：中文文件名必须编码，否则部分浏览器会截断
            "Content-Disposition": f"attachment; filename=\"points.xlsx\"; filename*=UTF-8''{quote(filename)}"
        },
    )
