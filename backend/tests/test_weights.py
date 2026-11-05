"""核心算法单元测试（对应博客 3.6）。

这里最重要的两个用例是：
- test_no_duplicate_within_round：一轮不重复；
- test_draw_distribution_matches_weight：用统计检验证明「随机」不是嘴上说说。
"""

from __future__ import annotations

import math
import random

import pytest

from app.core.weights import (
    probabilities,
    probability_ratio,
    sample_without_replacement,
    verify_draw,
    weight_of,
)


# ----------------------------------------------------------------------
# 权重公式
# ----------------------------------------------------------------------
def test_negative_points_have_strictly_decreasing_positive_weights():
    points = [-100, -2, -1, -0.5, 0, 0.5, 1, 9]
    weights = [weight_of(p) for p in points]
    assert all(math.isfinite(w) and w > 0 for w in weights)
    assert all(a > b for a, b in zip(weights, weights[1:]))
    assert weight_of(-1) == 2.0


@pytest.mark.parametrize("points,alpha", [(math.nan, 1), (math.inf, 1), (0, math.nan), (0, math.inf)])
def test_nonfinite_weight_parameters_rejected(points, alpha):
    with pytest.raises(ValueError):
        weight_of(points, alpha)


def test_single_pick_matches_full_sort_with_same_random_numbers():
    ids = list(range(30))
    weights = [weight_of(i - 5) for i in ids]
    single, snapshots, _ = sample_without_replacement(ids, weights, 1, random.Random(42))
    batch, full_snapshots, _ = sample_without_replacement(ids, weights, len(ids), random.Random(42))
    assert single == batch[:1]
    assert snapshots == full_snapshots


def test_weight_decreases_as_points_grow():
    weights = [weight_of(p) for p in (0, 1, 3, 9)]
    assert weights == sorted(weights, reverse=True)
    assert weight_of(9) == pytest.approx(0.1)


def test_alpha_zero_is_uniform():
    assert weight_of(9, alpha=0) == 1.0
    assert weight_of(-3, alpha=0) == 1.0


def test_negative_alpha_rejected():
    with pytest.raises(ValueError):
        weight_of(1, alpha=-1)


def test_probabilities_sum_to_one():
    probs = probabilities([1.0, 0.5, 0.25, 0.25])
    assert sum(probs) == pytest.approx(1.0)
    assert probs[0] == pytest.approx(0.5)


def test_probabilities_all_zero_falls_back_to_uniform():
    probs = probabilities([0.0, 0.0, 0.0, 0.0])
    assert probs == pytest.approx([0.25] * 4)


def test_probability_ratio_quantifies_alpha_effect():
    """α=1 时，9 分同学的中签概率只有 0 分同学的 1/10。"""
    assert probability_ratio(9, 1.0) == pytest.approx(0.1)
    assert probability_ratio(9, 0.0) == pytest.approx(1.0)
    assert probability_ratio(4, 2.0) == pytest.approx(1 / 25)


# ----------------------------------------------------------------------
# 抽样
# ----------------------------------------------------------------------
def test_empty_input():
    picked, candidates, _algo = sample_without_replacement([], [], 1, random.Random(1))
    assert picked == []
    assert candidates == []


def test_no_duplicate_within_round():
    """抽满 n 次必须刚好覆盖全班，不重不漏 —— 即「一轮不重复」。"""
    ids = list(range(1, 41))
    rng = random.Random(7)
    weights = [weight_of(i % 5) for i in ids]

    picked, candidates, algorithm = sample_without_replacement(ids, weights, len(ids), rng)

    assert algorithm == "A-Res"
    assert len(candidates) == 40
    assert sorted(picked) == ids
    assert len(set(picked)) == len(picked)


def test_k_larger_than_population():
    picked, _, _ = sample_without_replacement([1, 2, 3], [1.0, 1.0, 1.0], 10, random.Random(2))
    assert sorted(picked) == [1, 2, 3]


def test_abnormal_weights_fall_back_to_uniform():
    """权重全为 0 时不能崩，也不能返回空，退化成等概率。"""
    picked, _candidates, algorithm = sample_without_replacement(
        [1, 2, 3], [0.0, 0.0, 0.0], 1, random.Random(1)
    )
    assert algorithm.endswith("uniform-fallback")
    assert picked and picked[0] in (1, 2, 3)


def test_verify_draw_reproduces_winner():
    """落库的 (w, u) 必须能被任何人复算出同一个中签者。"""
    rng = random.Random(3)
    ids = [1, 2, 3, 4]
    weights = [weight_of(0), weight_of(1), weight_of(3), weight_of(9)]

    picked, candidates, _ = sample_without_replacement(ids, weights, 1, rng)
    replay = verify_draw((c.student_id, c.weight, c.rand_u) for c in candidates)

    assert replay == picked[0]


def test_draw_distribution_matches_weight():
    """积分 9 分的同学（w=0.1）对 0 分同学（w=1.0）的理论概率是 0.1/1.1。

    用 20000 次抽样 + 4σ 区间做检验：既证明权重生效，又不会因为随机波动偶发失败。
    """
    rng = random.Random(2026)
    ids = [1, 2]
    weights = [weight_of(9), weight_of(0)]

    n = 20000
    hits = 0
    for _ in range(n):
        picked, _, _ = sample_without_replacement(ids, weights, 1, rng)
        hits += 1 if picked[0] == 1 else 0

    p_expected = 0.1 / 1.1
    sigma = math.sqrt(p_expected * (1 - p_expected) / n)
    assert abs(hits / n - p_expected) < 4 * sigma, f"{hits / n:.4f} vs {p_expected:.4f}"


def test_larger_alpha_suppresses_top_student_more():
    """α 越大，高积分同学越不容易被点到（参数敏感性实验）。"""
    rng_lo = random.Random(11)
    rng_hi = random.Random(11)
    ids = [1, 2]
    n = 4000

    def hits(alpha: float, rng) -> int:
        weights = [weight_of(9, alpha), weight_of(0, alpha)]
        return sum(1 for _ in range(n) if sample_without_replacement(ids, weights, 1, rng)[0][0] == 1)

    assert hits(2.0, rng_hi) < hits(1.0, rng_lo)
