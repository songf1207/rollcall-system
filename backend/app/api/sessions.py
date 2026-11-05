"""场次与点名接口。"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..schemas import (
    ClockCandidateItem,
    ClockCandidatesOut,
    DrawOut,
    DrawProgress,
    PickIn,
    Resp,
    RollCallOut,
    SessionCreate,
    SessionOut,
)
from ..services import draw as draw_svc

router = APIRouter(prefix="/api/sessions", tags=["点名场次"])


@router.post("", response_model=Resp[SessionOut], summary="新建点名场次")
def create_session(payload: SessionCreate, db: Session = Depends(get_db)):
    """新建场次时会自动关闭之前未结束的场次，保证「当前场次」唯一。

    payload.class_name 指定本场次点哪个班；不传表示全部学生。
    """
    session = draw_svc.create_session(db, payload.mode, payload.class_name)
    return Resp[SessionOut](data=SessionOut.model_validate(session))


@router.get("/current", response_model=Resp[SessionOut], summary="当前场次")
def current_session(db: Session = Depends(get_db)):
    session = draw_svc.current_session(db)
    return Resp[SessionOut](data=SessionOut.model_validate(session) if session else None)


@router.get("/{session_id}", response_model=Resp[SessionOut], summary="场次详情")
def session_detail(session_id: int, db: Session = Depends(get_db)):
    session = draw_svc.get_session(db, session_id)
    return Resp[SessionOut](data=SessionOut.model_validate(session))


@router.post("/{session_id}/draw", response_model=Resp[DrawOut], summary="点下一位")
def draw_next(
    session_id: int,
    alpha: float | None = Query(None, ge=0, le=5, description="权重衰减指数，默认取服务端配置"),
    db: Session = Depends(get_db),
):
    """随机模式按积分递减权重抽签且一轮不重复；负分权重见 /docs 应用说明。"""
    session = draw_svc.get_session(db, session_id)
    rollcall, progress = draw_svc.draw_next(db, session, alpha)
    return Resp[DrawOut](
        data=DrawOut(
            rollcall=RollCallOut.model_validate(rollcall),
            progress=DrawProgress(**progress),
        )
    )


@router.get("/{session_id}/clock-candidates", response_model=Resp[ClockCandidatesOut], summary="按时间点名的候选")
def clock_candidates(
    session_id: int,
    digit: int | None = Query(None, ge=0, le=9, description="学号尾号；不传则取当前分钟个位"),
    db: Session = Depends(get_db),
):
    """筛出「学号尾号 = 当前分钟个位」的本班学生（本轮已点过的排除）。"""
    session = draw_svc.get_session(db, session_id)
    if digit is None:
        digit = datetime.now().minute % 10
    rows, progress = draw_svc.clock_candidates(db, session, digit)
    return Resp[ClockCandidatesOut](
        data=ClockCandidatesOut(
            digit=digit,
            items=[
                ClockCandidateItem(
                    student_id=row["student"].id,
                    student_no=row["student"].student_no,
                    name=row["student"].name,
                    major=row["student"].major,
                    called_count=row["called_count"],
                )
                for row in rows
            ],
            progress=DrawProgress(**progress),
        )
    )


@router.post("/{session_id}/pick", response_model=Resp[DrawOut], summary="按时间点名：选定某位同学")
def pick(
    session_id: int,
    payload: PickIn,
    db: Session = Depends(get_db),
):
    """老师从候选里挑人；同样计入随机点名次数，后续记分流程与随机模式一致。"""
    session = draw_svc.get_session(db, session_id)
    rollcall, progress = draw_svc.pick_student(db, session, payload.student_id)
    return Resp[DrawOut](
        data=DrawOut(
            rollcall=RollCallOut.model_validate(rollcall),
            progress=DrawProgress(**progress),
        )
    )


@router.post("/{session_id}/pick-random", response_model=Resp[DrawOut], summary="按时间点名：在候选里随机抽一个")
def pick_random(
    session_id: int,
    digit: int | None = Query(None, ge=0, le=9, description="学号尾号；不传则取当前分钟个位"),
    alpha: float | None = Query(None, ge=0, le=5, description="权重衰减指数，默认取服务端配置"),
    db: Session = Depends(get_db),
):
    """候选池 = 学号尾号相同的同学；抽取规则与随机点名一致（积分越高越不容易被抽中）。"""
    session = draw_svc.get_session(db, session_id)
    if digit is None:
        digit = datetime.now().minute % 10
    rollcall, progress = draw_svc.pick_random_in_clock(db, session, digit, alpha)
    return Resp[DrawOut](
        data=DrawOut(
            rollcall=RollCallOut.model_validate(rollcall),
            progress=DrawProgress(**progress),
        )
    )


@router.post("/{session_id}/close", response_model=Resp[SessionOut], summary="结束场次")
def close_session(session_id: int, db: Session = Depends(get_db)):
    session = draw_svc.get_session(db, session_id)
    return Resp[SessionOut](data=SessionOut.model_validate(draw_svc.close_session(db, session)))
