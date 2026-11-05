"""按保存的权重和随机数复核抽签，独立于当前积分与名单顺序。"""
import math

from sqlalchemy import select

from ..core.weights import ALGO_A_RES, verify_draw
from ..errors import NotFoundError
from ..models import DrawCandidate, DrawLog, Student
from .points import get_rollcall


def draw_audit(db, rollcall_id: int) -> dict:
    call = get_rollcall(db, rollcall_id)
    draw = db.scalar(select(DrawLog).where(DrawLog.session_id == call.session_id, DrawLog.seq == call.seq))
    if draw is None:
        raise NotFoundError("该点名没有抽签记录")
    snapshots = list(db.scalars(select(DrawCandidate).where(DrawCandidate.draw_id == draw.id).order_by(DrawCandidate.id)))
    ids = [s.student_id for s in snapshots]
    students = {s.id: s for s in db.scalars(select(Student).where(Student.id.in_(ids)))} if ids else {}
    total_weight = sum(c.weight for c in snapshots)
    valid_total = math.isfinite(total_weight) and total_weight > 0
    valid = bool(snapshots) and len(ids) == len(set(ids))
    strict_keys, approximate_keys = True, True
    items = []
    for candidate in snapshots:
        student = students.get(candidate.student_id)
        good = math.isfinite(candidate.weight) and candidate.weight > 0 and 0 < candidate.rand_u <= 1
        key = -math.log(candidate.rand_u) / candidate.weight if good else None
        good = good and math.isfinite(key) and math.isfinite(candidate.sort_key)
        matches = good and math.isclose(key, candidate.sort_key, rel_tol=1e-12, abs_tol=1e-12)
        valid = valid and good
        strict_keys = strict_keys and matches
        approximate_keys = approximate_keys and good and math.isclose(key, candidate.sort_key, rel_tol=1e-6, abs_tol=1e-6)
        items.append({
            "student_id": candidate.student_id,
            "student_no": student.student_no if student else None,
            "name": student.name if student else None,
            "weight": candidate.weight, "rand_u": candidate.rand_u,
            "stored_key": candidate.sort_key,
            "recomputed_key": key if good else None,
            "probability": candidate.weight / total_weight if valid_total else None,
            "key_matches": bool(matches),
        })
    replay = verify_draw((c.student_id, c.weight, c.rand_u) for c in snapshots) if valid else None
    if draw.algorithm == "sequential":
        verified, status = None, "not_applicable"
        explanation = "这是按学号顺序点名，不涉及随机抽签复核。"
    elif draw.algorithm != ALGO_A_RES:
        verified, status = None, "unavailable"
        explanation = "该记录未采用标准随机键算法，不能按当前方法复核。"
    else:
        consistent_winner = bool(valid and replay == draw.winner_id)
        if consistent_winner and strict_keys:
            verified, status = True, "verified"
            explanation = "保存的随机键和中签者一致。"
        elif consistent_winner and approximate_keys:
            verified, status = None, "precision_limited"
            explanation = "中签者可复算一致，但键值存在微小误差。旧版单精度快照可能发生此情况，不能标记为精确复核通过。"
        else:
            verified, status = False, "mismatch"
            explanation = "抽签记录缺失、精度不足或复算结果不一致，请检查记录。"
    return {
        "draw_id": draw.id, "session_id": draw.session_id,
        "round_no": draw.round_no, "seq": draw.seq, "algorithm": draw.algorithm,
        "alpha": float(draw.alpha), "winner_id": draw.winner_id,
        "rollcall_student_id": call.student_id, "replayed_winner_id": replay,
        "verified": verified, "status": status, "explanation": explanation,
        "created_at": draw.created_at, "candidates": items,
        "limitation": "此结果仅核对已保存的抽签记录；不单独证明随机源或记录未被有权限者修改。学生姓名、学号为当前名单信息。",
    }
