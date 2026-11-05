"""性能对比脚本 —— 博客 3.5「性能分析与改进」的原始数据来源。

    python scripts/benchmark.py            # 跑全部对比
    python scripts/benchmark.py --profile  # 额外输出 cProfile 热点函数排名

三组对比和热点分析：
1. 抽样算法：朴素「抽一个剔除一个」O(k·n)  vs  A-Res 一次排序 O(n log n)；
2. 批量导入：逐行 commit（1000 次事务）  vs  一次 add_all + commit；
3. 单人抽样：完整排序 vs 扫描最小键；cProfile 分析从 1000 人中抽 1 人。
"""

from __future__ import annotations

import argparse
import cProfile
import io
import json
import math
import platform
import pstats
import random
import sys
import tempfile
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.core.weights import Candidate, _draw_u, sample_without_replacement, weight_of  # noqa: E402


def sort_reference(ids, weights, rng):
    """优化前的单人抽样：相同随机键、相同快照，完整排序后只取第一人。"""
    candidates = []
    for sid, weight in zip(ids, weights):
        u = _draw_u(rng)
        weight = float(weight)
        key = math.inf if weight <= 0 or not math.isfinite(weight) else -math.log(u) / weight
        candidates.append(Candidate(student_id=int(sid), weight=weight, rand_u=u, sort_key=key))
    finite = [c for c in candidates if math.isfinite(c.sort_key)]
    return [c.student_id for c in sorted(finite, key=lambda c: c.sort_key)[:1]], candidates


def bench_single(n: int, repeat: int = 100) -> dict:
    ids = list(range(n))
    weights = [weight_of(i % 12) for i in ids]
    timings = []
    for sampler in (sort_reference, lambda ids, weights, rng: sample_without_replacement(ids, weights, 1, rng)):
        rng = random.Random(2026)
        start = time.perf_counter()
        for _ in range(repeat):
            sampler(ids, weights, rng)
        timings.append((time.perf_counter() - start) * 1000 / repeat)
    return {"n": n, "sort_ms": timings[0], "min_ms": timings[1], "repeat": repeat}


# ----------------------------------------------------------------------
# 1. 抽样算法
# ----------------------------------------------------------------------
def naive_sample_without_replacement(ids, weights, k, rng):
    """对照组：每次重新累加权重抽一个，抽完从池子里删掉。复杂度 O(k·n)。"""
    pool = list(zip(ids, weights))
    picked = []
    for _ in range(k):
        total = sum(w for _, w in pool)
        target = rng.random() * total
        acc = 0.0
        for idx, (_sid, w) in enumerate(pool):
            acc += w
            if target <= acc:
                picked.append(pool.pop(idx)[0])
                break
        else:
            picked.append(pool.pop()[0])
    return picked


def bench_sampling(n: int, repeat: int = 5) -> tuple[float, float]:
    ids = list(range(1, n + 1))
    weights = [weight_of(i % 12) for i in ids]

    rng = random.Random(2026)
    start = time.perf_counter()
    for _ in range(repeat):
        naive_sample_without_replacement(ids, weights, n, rng)
    naive_ms = (time.perf_counter() - start) / repeat * 1000

    rng = random.Random(2026)
    start = time.perf_counter()
    for _ in range(repeat):
        sample_without_replacement(ids, weights, n, rng)
    ares_ms = (time.perf_counter() - start) / repeat * 1000
    return naive_ms, ares_ms


# ----------------------------------------------------------------------
# 2. 批量导入
# ----------------------------------------------------------------------
def bench_import(n: int = 1000) -> tuple[float, float]:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.database import Base
    from app.models import Student

    tmp = Path(tempfile.mkdtemp(prefix="rollcall-bench-")) / "bench.db"
    engine = create_engine(f"sqlite:///{tmp.as_posix()}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    rows = [(f"2024{i:05d}", f"学生{i}") for i in range(n)]

    # 逐行提交
    session = Session()
    start = time.perf_counter()
    for no, name in rows:
        session.add(Student(student_no=no, name=name))
        session.commit()
    per_row_ms = (time.perf_counter() - start) * 1000
    session.close()

    engine.dispose()
    tmp.unlink(missing_ok=True)

    engine = create_engine(f"sqlite:///{tmp.as_posix()}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    # 批量提交
    session = Session()
    start = time.perf_counter()
    session.add_all([Student(student_no=no, name=name) for no, name in rows])
    session.commit()
    batch_ms = (time.perf_counter() - start) * 1000
    session.close()

    engine.dispose()
    tmp.unlink(missing_ok=True)
    return per_row_ms, batch_ms


def profile_sampling(n: int = 1000) -> str:
    ids = list(range(1, n + 1))
    weights = [weight_of(i % 12) for i in ids]
    rng = random.Random(1)

    pr = cProfile.Profile()
    pr.enable()
    for _ in range(20):
        sample_without_replacement(ids, weights, 1, rng)
    pr.disable()

    buf = io.StringIO()
    pstats.Stats(pr, stream=buf).sort_stats("cumulative").print_stats(12)
    return buf.getvalue()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", action="store_true", help="额外输出 cProfile 热点")
    parser.add_argument("--output", type=Path, help="写入本机实测 JSON；不包含数据库密码")
    args = parser.parse_args()

    print("口径：算法测试使用固定种子与内存候选；导入测试使用临时 SQLite。均不代表线上 API 延迟。\n")
    report = {"python": platform.python_version(), "platform": platform.platform(), "batch": [], "single": []}

    print("## 1. 加权不重复抽样：抽满全班的耗时（毫秒）\n")
    print("| 人数 n | 朴素 O(k·n) | A-Res O(n log n) | 提速 |")
    print("| ---: | ---: | ---: | ---: |")
    for n in (100, 300, 1000):
        naive_ms, ares_ms = bench_sampling(n)
        report["batch"].append({"n": n, "naive_ms": naive_ms, "ares_ms": ares_ms})
        speedup = naive_ms / ares_ms if ares_ms else float("inf")
        print(f"| {n} | {naive_ms:.2f} | {ares_ms:.2f} | {speedup:.1f}x |")

    print("\n## 2. 导入 1000 名学生：逐行提交 vs 批量提交（毫秒）\n")
    per_row_ms, batch_ms = bench_import()
    report["import"] = {"n": 1000, "per_row_ms": per_row_ms, "batch_ms": batch_ms, "database": "temporary SQLite"}
    print("| 方式 | 耗时 | 提速 |")
    print("| --- | ---: | ---: |")
    print(f"| 逐行 add + commit | {per_row_ms:.1f} | 1.0x |")
    print(f"| 一次 add_all + commit | {batch_ms:.1f} | {per_row_ms / batch_ms:.1f}x |")

    print("\n## 3. 单人抽样：完整排序 vs 扫描最小键（含候选快照，毫秒）\n")
    print("| 候选人数 | 完整排序 O(n log n) | 扫描 O(n) |\n| ---: | ---: | ---: |")
    for n in (100, 300, 1000):
        measurement = bench_single(n)
        report["single"].append(measurement)
        print(f"| {n} | {measurement['sort_ms']:.4f} | {measurement['min_ms']:.4f} |")

    if args.profile:
        print("\n## 4. cProfile 热点（1000 人中抽 1 人 × 20 次）\n")
        print("```")
        print(profile_sampling())
        print("```")
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
