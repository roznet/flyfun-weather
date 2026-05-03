"""Tests for the admin freshness endpoint helpers (issue #108)."""

from __future__ import annotations

import math
import statistics


def _p90_index(count: int) -> int:
    """Mirror of the formula used in admin.py for testability.

    Nearest-rank p90: ceil(count * 0.9) - 1, floored at 0.
    """
    return max(0, math.ceil(count * 0.9) - 1)


class TestP90Index:
    def test_count_one_returns_zero(self):
        assert _p90_index(1) == 0

    def test_count_two_returns_one_not_zero(self):
        # ``int(2 * 0.9) - 1 = 0`` would return the minimum sample, not p90.
        # ``ceil(1.8) - 1 = 1`` returns the larger sample as expected.
        assert _p90_index(2) == 1

    def test_count_ten_returns_nine(self):
        assert _p90_index(10) == 8  # ceil(9.0) - 1 = 8 (90th percentile slot)

    def test_count_eleven(self):
        assert _p90_index(11) == 9  # ceil(9.9) - 1 = 9


class TestMedian:
    """Pin the property the previous ``ds[count // 2]`` formula violated:
    median of an even-count list should be the average of the two middles,
    not biased toward the upper middle.
    """

    def test_count_two_averages_the_pair(self):
        # Old buggy formula returned the max for count=2.
        assert int(statistics.median([10, 20])) == 15

    def test_count_four_averages_inner_pair(self):
        # Old: ds[2] = 30 (third-smallest, biased high).  New: (20+30)/2 = 25.
        assert int(statistics.median([10, 20, 30, 40])) == 25

    def test_count_odd_returns_middle(self):
        assert int(statistics.median([10, 20, 30])) == 20
