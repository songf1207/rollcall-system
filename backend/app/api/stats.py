"""统计接口：排行榜、概率盘、历史记录。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..schemas import HistoryPage, ProbabilityOut, RankOut, Resp, StudentPointsOut
from ..services import stats as stats_svc

router = APIRouter(prefix="/api/stats", tags=["统计"])


@router.get("/ranking", response_model=Resp[RankOut], summary="积分排行榜 Top N")
def ranking(
    top: int = Query(10, ge=1, le=100),
    class_name: str | None = Query(None, description="只看这个班级；不传为全体学生"),
    db: Session = Depends(get_db),
):
    """返回 items 供表格展示，同时给出 categories / points_series / calls_series，
    calls_series 为随机点名次数，用于「积分柱状 + 随机次数折线」组合图。
    """
    return Resp[RankOut](data=RankOut(**stats_svc.ranking(db, top, class_name)))


@router.get("/probability", response_model=Resp[ProbabilityOut], summary="概率盘")
def probability(
    session_id: int | None = Query(None, description="不传则取当前场次"),
    alpha: float | None = Query(None, ge=0, le=5),
    class_name: str | None = Query(None, description="没有场次时按这个班级预览"),
    db: Session = Depends(get_db),
):
    return Resp[ProbabilityOut](
        data=ProbabilityOut(**stats_svc.probability(db, session_id, alpha, class_name))
    )


@router.get("/students/{student_id}/points", response_model=Resp[StudentPointsOut], summary="个人积分变化曲线")
def student_points(student_id: int, db: Session = Depends(get_db)):
    return Resp[StudentPointsOut](data=StudentPointsOut(**stats_svc.student_points(db, student_id)))


@router.get("/history", response_model=Resp[HistoryPage], summary="历史点名记录")
def history(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=200),
    session_id: int | None = Query(None),
    class_name: str | None = Query(None, description="只看这个班级的历史"),
    db: Session = Depends(get_db),
):
    return Resp[HistoryPage](
        data=HistoryPage(
            **stats_svc.history(db, page=page, size=size, session_id=session_id, class_name=class_name)
        )
    )


@router.get("/config", summary="当前积分规则（前端展示用）")
def config():
    return Resp[dict](
        data={
            "alpha": settings.alpha,
            "point_arrive": settings.point_arrive,
            "point_repeat_ok": settings.point_repeat_ok,
            "point_repeat_fail": settings.point_repeat_fail,
            "answer_min": settings.answer_min,
            "answer_max": settings.answer_max,
            "transfer_every": settings.transfer_every,
        }
    )
