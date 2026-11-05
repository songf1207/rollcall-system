"""按 ORM 创建缺失表；不会删除已有表或数据，也不执行演示数据重置。"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import Base, engine
from app import models  # noqa: F401


if __name__ == "__main__":
    try:
        Base.metadata.create_all(engine)
        print("数据库七张表已初始化（含事务锁，已有表和数据保留）。")
    finally:
        engine.dispose()
