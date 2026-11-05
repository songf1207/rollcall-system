"""使用本地密码初始化 TiDB，并在成功建表后更新 .env。不会删库或清空数据。"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import ssl
import sys
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True)
    parser.add_argument("--user", required=True, help="控制台提供的完整用户名，包含前缀")
    parser.add_argument("--port", type=int, default=4000)
    parser.add_argument("--database", default="classroom_rollcall")
    parser.add_argument("--password-file", type=Path, default=ROOT / ".tidb-password.txt")
    parser.add_argument("--ca", type=Path, help="可选：控制台下载的 CA 证书")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", args.database):
        parser.error("数据库名需以英文字母开头，仅含字母、数字和下划线，最多 64 字符")
    password = args.password_file.read_text(encoding="utf-8-sig").strip()
    if not password:
        parser.error("本地密码文件为空")

    import pymysql
    from sqlalchemy.engine import URL

    connection = None
    app_engine = None
    url = URL.create("mysql+pymysql", username=args.user, password=password,
                     host=args.host, port=args.port, database=args.database,
                     query={"charset": "utf8mb4"})
    try:
        context = ssl.create_default_context(cafile=str(args.ca.resolve()) if args.ca else None)
        connection = pymysql.connect(host=args.host, port=args.port, user=args.user,
                                     password=password, charset="utf8mb4", ssl=context,
                                     connect_timeout=10, read_timeout=20, write_timeout=20)
        print("TLS:", connection._sock.version())
        with connection.cursor() as cursor:
            cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{args.database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
        connection.close()
        connection = None

        values = {"DATABASE_URL": url.render_as_string(hide_password=False),
                  "DB_SSL": "1", "DB_SSL_CA": str(args.ca.resolve()).replace("\\", "/") if args.ca else ""}
        os.environ.update(values)
        from app.database import Base, engine
        from app import models  # noqa: F401
        app_engine = engine
        Base.metadata.create_all(engine)

        env_path = ROOT / ".env"
        original = env_path.read_text(encoding="utf-8-sig") if env_path.exists() else ""
        backup = ROOT / ".env.before-tidb"
        if original and not backup.exists():
            backup.write_text(original, encoding="utf-8")
        updated = []
        remaining = dict(values)
        for line in original.splitlines():
            key = line.partition("=")[0].strip()
            if key in values and not line.lstrip().startswith("#"):
                if key in remaining:
                    updated.append(f"{key}={remaining.pop(key)}")
            else:
                updated.append(line)
        updated.extend(f"{key}={value}" for key, value in remaining.items())
        env_path.write_text("\n".join(updated) + "\n", encoding="utf-8")
        print("[OK] 数据库已就绪（六张业务表及事务锁表）：", args.database)
        print("[OK] .env 已更新，原配置备份为 .env.before-tidb；两者均不应提交 Git。")
        return 0
    except Exception as exc:
        message = str(exc).replace(password, "[redacted]").replace(quote(password, safe=""), "[redacted]")
        print(f"[FAILED] {type(exc).__name__}: {message}")
        print("本地 .env 尚未更新；如建表中断，可修复后重跑，不会删除已有数据。")
        return 1
    finally:
        if connection:
            connection.close()
        if app_engine:
            app_engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
