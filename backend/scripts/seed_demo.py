"""灌演示数据 —— 录视频前必跑，否则排行榜和概率盘全是 0，画面很难看。

    python scripts/seed_demo.py                  # 默认点 25 次
    python scripts/seed_demo.py --draws 40 --seed 2026

它会：新建一个随机模式场次 -> 反复抽签并随机记分 -> 打印排行榜。
全部走真实的 service 层，所以积分账本、转移权、缓存列都和手工点出来的一模一样。
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import func, select  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models import MODE_RANDOM, Student  # noqa: E402
from app.services import draw as draw_svc  # noqa: E402
from app.services import points as point_svc  # noqa: E402

ANSWER_CHOICES = [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0]


def score_for(rng: random.Random) -> dict:
    """模拟一位同学的课堂表现：85% 到场，60% 复述对，答对按正态偏好给分。"""
    arrived = rng.random() < 0.85
    if not arrived:
        return {"arrived": False, "repeat_ok": None, "answer_score": None}
    repeat_ok = rng.random() < 0.6
    answer = rng.choice(ANSWER_CHOICES) if repeat_ok else rng.choice([0.0, 0.0, 0.5])
    return {"arrived": True, "repeat_ok": repeat_ok, "answer_score": answer}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--draws", type=int, default=25, help="点名次数")
    parser.add_argument("--seed", type=int, default=2026, help="模拟评分的随机种子；实际抽签仍使用系统随机源")
    args = parser.parse_args()

    rng = random.Random(args.seed)

    with SessionLocal() as db:
        total = int(db.scalar(select(func.count()).select_from(Student)) or 0)
        if total == 0:
            print("库里还没有学生。先导入 Excel：")
            print("  python -c \"...\" 或者直接调 POST /api/students/import")
            print("最简单的方式：启动服务后在 /docs 里上传 samples/sample_roster.xlsx")
            return 1

        print(f"现有学生 {total} 人，开始模拟 {args.draws} 次点名…")
        session = draw_svc.create_session(db, MODE_RANDOM)

        scored = 0
        for i in range(args.draws):
            rollcall, progress = draw_svc.draw_next(db, session)
            point_svc.score_rollcall(db, rollcall, **score_for(rng))
            scored += 1
            if (i + 1) % 5 == 0:
                print(
                    f"  第 {i + 1} 次：{rollcall.student.name}  "
                    f"第 {progress['round_no']} 轮 · 本轮还剩 {progress['remaining_in_round']} 人"
                )

        session = draw_svc.close_session(db, session)

        print(f"\n完成：{scored} 次点名已记分，场次 #{session.id} 已结束。\n")

        top = list(
            db.scalars(
                select(Student).order_by(Student.total_points.desc(), Student.student_no).limit(8)
            )
        )
        print("当前排行榜 Top 8：")
        print("  名次  学号      姓名      总积分  点名次数")
        for i, s in enumerate(top, start=1):
            print(
                f"  {i:>3}   {s.student_no:<9} {s.name:<8} {float(s.total_points):>6.1f}  {s.call_count:>4}"
            )

    print("\n接下来：python -m uvicorn app.main:app --reload  然后打开 /docs 看效果")
    return 0


if __name__ == "__main__":
    sys.exit(main())
