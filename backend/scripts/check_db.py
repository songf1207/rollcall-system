"""数据库连通性自检 —— 与应用使用相同的连接池及 TLS 配置。

    python scripts/check_db.py

它会告诉你：连不连得上、慢不慢、表建全了没有、数据对不对。
连不上时把 MySQL 错误码翻译成人话，省掉两小时的瞎试。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import inspect, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402
from sqlalchemy.exc import OperationalError, SQLAlchemyError  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import engine  # noqa: E402

REQUIRED = {
    "write_locks": ("id", "revision"),
    "students": ("student_no", "name", "major", "total_points", "call_count", "random_call_count"),
    "sessions": ("mode", "seq_cursor", "round_no", "draw_count", "status"),
    "rollcalls": ("student_id", "round_no", "seq", "point_delta", "status"),
    "point_logs": ("student_id", "delta", "reason"),
    "draw_logs": ("session_id", "alpha", "algorithm", "winner_id"),
    "draw_candidates": ("draw_id", "student_id", "weight", "rand_u", "sort_key"),
}

ERROR_HINTS = {
    "1045": "用户名或密码错误，检查 URL 中的特殊字符是否已编码",
    "1049": "数据库不存在，请按服务商控制台提供的库名填写",
    "2003": "连不上主机/端口，检查服务状态和网络访问权限",
    "10060": "网络超时（校园网/代理可能挡了 3306，换手机热点试试）",
    "1044": "这个账号没有该库的权限（库名写错了）",
    "1130": "对方不允许你从当前 IP 连接",
}


def mask(url: str) -> str:
    return make_url(url).render_as_string(hide_password=True)


def main() -> int:
    url = settings.database_url
    print(f"目标数据库: {mask(url)}\n")

    # 1. 连通性与延迟
    start = time.perf_counter()
    try:
        with engine.connect() as conn:
            latency = (time.perf_counter() - start) * 1000
            version = conn.execute(
                text("SELECT sqlite_version()" if url.startswith("sqlite") else "SELECT VERSION()")
            ).scalar()
            print(f"[1/4] 连接成功    服务端版本 {version}    首次握手 {latency:.0f} ms")

            samples = []
            for _ in range(3):
                t = time.perf_counter()
                conn.execute(text("SELECT 1")).scalar()
                samples.append((time.perf_counter() - t) * 1000)
            avg = sum(samples) / len(samples)
            print(f"[2/4] 往返延迟    {avg:.0f} ms（SELECT 1，三次平均）")
    except OperationalError as exc:
        message = str(exc)
        print(f"[!] 连接失败：{message.splitlines()[0]}")
        for code, hint in ERROR_HINTS.items():
            if code in message:
                print(f"    → 错误码 {code}：{hint}")
                break
        else:
            print("    → 没匹配到已知错误码，把上面这行原文发到群里问")
        print("\n排查顺序：")
        print("  1) .env 里的 DATABASE_URL 是否形如")
        print("     mysql+pymysql://用户名:已编码密码@主机:端口/库名?charset=utf8mb4")
        print("  2) 是否已装驱动：python -m pip show pymysql")
        print("  3) 核对服务商控制台的主机、端口、库名及访问权限")
        print("  4) 若要求 TLS，设置 DB_SSL=1；自签 CA 则配置 DB_SSL_CA")
        return 1
    except SQLAlchemyError as exc:
        print(f"[!] 连接失败：{exc}")
        return 1

    # 2. 表结构
    inspector = inspect(engine)
    existing = set(inspector.get_table_names())
    missing_tables = [name for name in REQUIRED if name not in existing]
    if missing_tables:
        print(f"[3/4] 缺少表：{'、'.join(missing_tables)}")
        print("      → python scripts/init_db.py（只创建缺失表，不删除数据）")
        return 1
    print(f"[3/4] {len(REQUIRED)} 张表齐全")

    broken = []
    for table, columns in REQUIRED.items():
        actual = {col["name"] for col in inspector.get_columns(table)}
        lost = [c for c in columns if c not in actual]
        if lost:
            broken.append(f"{table} 缺列 {lost}")
    if broken:
        print("      表结构不对：")
        for item in broken:
            print(f"      - {item}")
        print("      → 先备份现有数据，再核对 sql/schema.sql 并制定迁移；不要直接删库")
        return 1
    print("      关键列全部对得上")

    # 3. 数据
    with engine.connect() as conn:
        guard = conn.execute(text("SELECT COUNT(*) FROM write_locks WHERE id = 1")).scalar()
        if guard != 1:
            print("[!] 写事务锁未初始化，请检查 write_locks 中的 id=1 记录")
            return 1
        students = conn.execute(text("SELECT COUNT(*) FROM students")).scalar()
        rollcalls = conn.execute(text("SELECT COUNT(*) FROM rollcalls")).scalar()
        points = conn.execute(text("SELECT COUNT(*) FROM point_logs")).scalar()
    print(f"[4/4] 现有数据    学生 {students} 人 · 点名 {rollcalls} 次 · 积分流水 {points} 条")

    if students == 0:
        print("\n提示：库里还没有学生。用 POST /api/students/import 导入 Excel，")
        print("      演示积分需在名单导入后再运行 scripts/seed_demo.py。")

    if url.startswith("sqlite"):
        print("\n[OK] 本地 SQLite 自检通过；在线 MySQL 尚需单独配置和验证。")
    else:
        print("\n[OK] 在线数据库自检通过，部署时使用相同连接与 TLS 配置。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
