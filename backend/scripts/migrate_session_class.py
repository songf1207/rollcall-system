"""给已存在的 sessions 表补上 class_name 列（场次绑定班级）。

新库由 create_all 直接建好，不需要这个脚本；
已经跑过的库（本地 dev.db、云端 TiDB）要补一列才能用「按班级点名」。

可重复执行，已有该列时直接跳过。

用法：
    .\\.venv\\Scripts\\python.exe scripts/migrate_session_class.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import inspect, text  # noqa: E402

from app.database import engine  # noqa: E402


def main() -> int:
    insp = inspect(engine)
    if "sessions" not in insp.get_table_names():
        print("sessions 表还不存在，直接运行 scripts/init_db.py 建库即可。")
        return 0

    columns = {col["name"] for col in insp.get_columns("sessions")}
    if "class_name" in columns:
        print("sessions.class_name 已存在，无需迁移。")
        return 0

    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE sessions ADD COLUMN class_name VARCHAR(50)"))
        try:
            conn.execute(text("CREATE INDEX ix_sessions_class_name ON sessions (class_name)"))
        except Exception as exc:  # 索引已存在等无关紧要的情况
            print(f"索引未创建（可忽略）：{exc}")
    print("已为 sessions 表添加 class_name 列。旧场次保持 NULL（= 全体学生）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
