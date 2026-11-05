"""将旧 MySQL/TiDB 快照三列从 FLOAT 升为 DOUBLE。保留数据，不回填已丢失的历史精度。"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import inspect, text
from app.database import engine


def main():
    if engine.dialect.name != "mysql":
        print("当前不是 MySQL/TiDB，无需执行此迁移。")
        return
    columns = {c["name"]: str(c["type"]).upper() for c in inspect(engine).get_columns("draw_candidates")}
    names = ("weight", "rand_u", "sort_key")
    if all(columns.get(name) in ("DOUBLE", "DOUBLE PRECISION") for name in names):
        print("候选快照已使用 DOUBLE，无需迁移。")
        return
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE draw_candidates MODIFY COLUMN weight DOUBLE NOT NULL, MODIFY COLUMN rand_u DOUBLE NOT NULL, MODIFY COLUMN sort_key DOUBLE NOT NULL"))
    updated = {c["name"]: str(c["type"]).upper() for c in inspect(engine).get_columns("draw_candidates")}
    assert all(updated.get(name) in ("DOUBLE", "DOUBLE PRECISION") for name in names)
    print("三列已升级为 DOUBLE，所有记录保留。旧记录已损失的精度无法恢复，复核接口会区分精度受限状态。")


if __name__ == "__main__":
    try:
        main()
    finally:
        engine.dispose()
