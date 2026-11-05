"""加权随机抽样 —— 本项目的技术核心（对应博客 3.3 / 3.4）。

权重公式（本项目设计，题目只要求积分越高概率越低）：

    积分 >= 0: w_i = (1 + 积分_i)^(-α)
    积分 <  0: w_i = (1 - 积分_i)^α

- 积分越高，w 越小，被随机点到的概率越低，符合「总积分越高，被点概率越低」的要求。
- α 是衰减指数：α=0 退化成等概率，α 越大高积分同学被点到的概率下降得越明显。

两个容易被忽略的坑：

1) 积分可能为负。积分规则里有「没复述对 -1」，一个同学被扣几次就成负数了，
   此时 1+积分 <= 0：等于 0 会除零，小于 0 会得到负权重（概率失去意义）。
   因此负积分使用分段函数；α>0 时在整个积分范围严格递减，0 分处连续。

2) 「一轮不重复」不能靠「抽到重复的就重抽」。
   重复抽取后拒绝的方式会增加重试；这里直接从本轮尚未点到的候选中抽取。
   这里用 Efraimidis–Spirakis 的 A-Res 算法：

       对每个候选生成 u_i ~ U(0,1)，排序键 key_i = -ln(u_i) / w_i，取 key 最小的 k 个。

   数学上等价于按权重做「无放回」抽样。k=1 扫描取最小键为 O(n)，批量排序为 O(n log n)。
   而且 -ln(u)/w 这种形式把随机数和权重都显式暴露出来，可以原样落库供人复算
   —— 这正是应对「抽取非随机」质疑的证据。
"""

from __future__ import annotations

import math
import secrets
from dataclasses import dataclass
from typing import Iterable, Sequence

ALGO_A_RES = "A-Res"
ALGO_FALLBACK = "A-Res+uniform-fallback"


@dataclass(frozen=True)
class Candidate:
    """一次抽签中某个候选人的快照，全部字段都会写进 draw_candidates 表。"""

    student_id: int
    weight: float
    rand_u: float
    sort_key: float


def weight_of(points: float, alpha: float = 1.0) -> float:
    """正负分均有效；α=0 是显式的等概率实验模式，默认 α=1。"""
    points, alpha = float(points), float(alpha)
    if not math.isfinite(points) or not math.isfinite(alpha) or alpha < 0:
        raise ValueError("积分和 alpha 必须是有限数值，alpha 不能为负数")
    return math.pow(1.0 + abs(points), alpha if points < 0 else -alpha)


def new_rng() -> secrets.SystemRandom:
    """用操作系统的熵源，而不是被 seed 固定住的 random.random()。"""
    return secrets.SystemRandom()


def _draw_u(rng) -> float:
    """取 (0, 1] 上的随机数；排除 0 是为了 -ln(u) 不炸。"""
    u = rng.random()
    while u <= 0.0:
        u = rng.random()
    return u


def probability_ratio(points: float, alpha: float) -> float:
    """积分 points 的同学，被点概率是 0 分同学的多少倍。

    用于博客里的「参数敏感性」小节：α=1 时，9 分同学的中签概率只有 0 分同学的 1/10。
    """
    return weight_of(points, alpha) / weight_of(0, alpha)


def probabilities(weights: Sequence[float]) -> list[float]:
    """把权重归一化成「单次抽签」的概率，用于概率盘展示。"""
    total = sum(w for w in weights if w > 0 and math.isfinite(w))
    if total <= 0:
        n = len(weights)
        return [1.0 / n] * n if n else []
    return [(w / total) if (w > 0 and math.isfinite(w)) else 0.0 for w in weights]


def sample_without_replacement(
    student_ids: Sequence[int],
    weights: Sequence[float],
    k: int = 1,
    rng=None,
) -> tuple[list[int], list[Candidate], str]:
    """按权重无放回抽 k 个，返回 (被抽中的 student_id 列表, 全量候选快照, 算法名)。

    k=1 时就是「加权随机点名」；k=n 时相当于给全班排了一个加权随机顺序。
    """
    if rng is None:
        rng = new_rng()
    if k <= 0 or not student_ids:
        return [], [], ALGO_A_RES

    n = len(student_ids)
    k = min(k, n)

    candidates: list[Candidate] = []
    for sid, w in zip(student_ids, weights):
        u = _draw_u(rng)
        w = float(w)
        if w <= 0 or not math.isfinite(w):
            # 权重异常（0、负数、inf/nan）的候选排到最后，等同不参与
            key = math.inf
        else:
            key = -math.log(u) / w
        candidates.append(Candidate(student_id=int(sid), weight=w, rand_u=u, sort_key=key))

    finite = [c for c in candidates if math.isfinite(c.sort_key)]
    if not finite:
        # 所有人权重都异常：退化成等概率，保证课堂不会卡住
        picked_idx = rng.sample(range(n), k)
        picked = sorted(student_ids[i] for i in picked_idx)
        return [int(x) for x in picked], candidates, ALGO_FALLBACK

    # HTTP 点名只取 1 人，无需对全部候选排序；快照与随机数完全保留。
    ordered = [min(finite, key=lambda c: c.sort_key)] if k == 1 else sorted(finite, key=lambda c: c.sort_key)[:k]
    return [c.student_id for c in ordered], candidates, ALGO_A_RES


def verify_draw(candidates: Iterable[tuple[int, float, float]]) -> int | None:
    """校验函数：给一组 (student_id, weight, rand_u)，重算出中签者。

    前端或评委可以拿 draw_candidates 表里的原始数据调用本函数，
    复算结果必须与 draw_logs.winner_id 一致 —— 这就是「随机不是编的」的证据。
    """
    best_id: int | None = None
    best_key = math.inf
    for sid, w, u in candidates:
        key = math.inf if (w <= 0 or not math.isfinite(w)) else -math.log(u) / w
        if key < best_key:
            best_key, best_id = key, sid
    return best_id
