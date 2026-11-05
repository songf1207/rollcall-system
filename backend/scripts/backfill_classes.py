"""把已有学生的班级名登记进 classes 表（幂等，可重复运行）。

历史数据只有 students.class_name，没有 classes 记录；
班级管理功能上线后跑一次，班级列表里才会有「正规」的班级条目，
否则这些班级只以 id=0 的「未登记」形式出现（功能可用，但改不了名）。

用法：
    .\\.venv\\Scripts\\python.exe scripts/backfill_classes.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select  # noqa: E402

from app.database import Base, SessionLocal, engine  # noqa: E402
from app.models import Klass, Student  # noqa: E402


def main() -> int:
    Base.metadata.create_all(bind=engine)  # 顺带建出 classes 表

    db = SessionLocal()
    try:
        names = [
            (name or "").strip()
            for (name,) in db.execute(select(Student.class_name).distinct()).all()
        ]
        names = sorted({n for n in names if n})

        created = 0
        for name in names:
            exists = db.scalar(select(Klass).where(Klass.name == name))
            if exists is None:
                db.add(Klass(name=name[:50]))
                created += 1
        db.commit()

        classes = list(db.scalars(select(Klass).order_by(Klass.name)))
        counts = dict(
            db.execute(
                select(Student.class_name, func.count()).group_by(Student.class_name)
            ).all()
        )
        print(f"扫描到 {len(names)} 个班级名，新建 {created} 条，当前共 {len(classes)} 个班级：")
        for klass in classes:
            print(f"  - {klass.name}: {counts.get(klass.name, 0)} 人")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
