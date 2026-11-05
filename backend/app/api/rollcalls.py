"""单次点名的后续操作：记分、改判、转移。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_db
from ..schemas import DrawAuditOut, Resp, RollCallOut, ScoreIn, TransferIn
from ..services import points as point_svc
from ..services.audit import draw_audit

router = APIRouter(prefix="/api/rollcalls", tags=["记分"])


@router.get("/{rollcall_id}/audit", response_model=Resp[DrawAuditOut], summary="抽签复核：概率、随机键与中签结果")
def audit(rollcall_id: int, db: Session = Depends(get_db)):
    return Resp[DrawAuditOut](data=DrawAuditOut(**draw_audit(db, rollcall_id)))


@router.get("/{rollcall_id}", response_model=Resp[RollCallOut], summary="单次点名详情")
def rollcall_detail(rollcall_id: int, db: Session = Depends(get_db)):
    rollcall = point_svc.get_rollcall(db, rollcall_id)
    return Resp[RollCallOut](data=RollCallOut.model_validate(rollcall))


@router.post("/{rollcall_id}/score", response_model=Resp[RollCallOut], summary="记分")
def score(
    rollcall_id: int,
    payload: ScoreIn,
    db: Session = Depends(get_db),
):
    """到达课堂 +1；复述对 +0.5、没复述对 -1；回答问题 +0.5~3。

    同一次点名只能记一次分，改分请用 PATCH 改判。
    """
    rollcall = point_svc.get_rollcall(db, rollcall_id)
    updated = point_svc.score_rollcall(
        db,
        rollcall,
        arrived=payload.arrived,
        repeat_ok=payload.repeat_ok,
        answer_score=payload.answer_score,
    )
    return Resp[RollCallOut](data=RollCallOut.model_validate(updated))


@router.patch("/{rollcall_id}/score", response_model=Resp[RollCallOut], summary="改判")
def rejudge(
    rollcall_id: int,
    payload: ScoreIn,
    db: Session = Depends(get_db),
):
    """改判不是覆盖旧分，而是往账本里补一条冲正记录（reason=manual），全程可追溯。"""
    rollcall = point_svc.get_rollcall(db, rollcall_id)
    updated = point_svc.rejudge_rollcall(
        db,
        rollcall,
        arrived=payload.arrived,
        repeat_ok=payload.repeat_ok,
        answer_score=payload.answer_score,
    )
    return Resp[RollCallOut](data=RollCallOut.model_validate(updated))


@router.post("/{rollcall_id}/transfer", response_model=Resp[RollCallOut], summary="点名转移")
def transfer(
    rollcall_id: int,
    payload: TransferIn,
    db: Session = Depends(get_db),
):
    """把这次点名转给另一位同学，消耗原被点者 1 次转移权（每被点 N 次获得 1 次）。"""
    rollcall = point_svc.get_rollcall(db, rollcall_id)
    new_rollcall = point_svc.transfer_rollcall(db, rollcall, payload.to_student_id)
    return Resp[RollCallOut](data=RollCallOut.model_validate(new_rollcall))
