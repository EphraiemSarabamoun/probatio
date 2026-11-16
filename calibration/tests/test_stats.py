"""Hand-derived statistics and pairing regressions; no model or network calls."""

import math
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import stats as S


class Agreement(unittest.TestCase):
    """Distinguish absolute agreement from consistency and ordinary correlation."""

    def test_hand_calculated_systematic_occasion_shift(self):
        # MSR = 2, MSC = 3/2, MSE = 0; ICC = 2 / (2 + 2*(3/2)/3).
        pairs = [(1, 2), (2, 3), (3, 4)]
        self.assertAlmostEqual(S.icc_a1(pairs), 2 / 3)
        self.assertAlmostEqual(S.spearman(*zip(*pairs)), 1)

    def test_hand_calculated_nonzero_residual(self):
        # MSR = 9/2, MSC = 8/3, MSE = 1/6, giving (13/3)/(19/3).
        self.assertAlmostEqual(S.icc_a1([(1, 2), (2, 4), (4, 5)]), 13 / 19)

    def test_negative_estimate_is_not_clipped(self):
        self.assertAlmostEqual(S.icc_a1([(1, 3), (2, 2), (3, 1)]), -3)

    def test_identical_and_constant_pairs(self):
        self.assertAlmostEqual(S.icc_a1([(1, 1), (2, 2), (3, 3)]), 1)
        self.assertAlmostEqual(S.icc_a1([(1, 2), (1, 2), (1, 2)]), 0)
        for pairs in ([], [(1, 2)], [(0, 0), (0, 0)], [(5, 5), (5, 5)]):
            self.assertIsNone(S.icc_a1(pairs))

    def test_common_scale_and_occasion_order_do_not_change_icc(self):
        base = [(1, 2), (2, 4), (4, 5)]
        expected = 13 / 19
        for scale in (1e-200, 1e200, -3):
            self.assertAlmostEqual(S.icc_a1([(a * scale, b * scale) for a, b in base]), expected)
        self.assertAlmostEqual(S.icc_a1([(b, a) for a, b in base]), expected)


class RankCorrelation(unittest.TestCase):
    """Tied observations use average ranks, not the untied shortcut formula."""

    def test_hand_calculated_ties_on_both_axes(self):
        # Ranks (1.5,1.5,3,4) and (1,2.5,2.5,4): cross sum 3.75,
        # squared sums 4.5 each, so rho = 5/6.
        self.assertAlmostEqual(S.spearman([1, 1, 2, 3], [1, 2, 2, 3]), 5 / 6)

    def test_reverse_and_constant(self):
        self.assertAlmostEqual(S.spearman([2, 3, 9], [8, 5, -2]), -1)
        for xs, ys in (([], []), ([1], [2]), ([1, 1], [1, 2])):
            self.assertIsNone(S.spearman(xs, ys))

    def test_mismatched_lengths_are_not_truncated(self):
        with self.assertRaises(ValueError):
            S.spearman([1, 2], [1])


class RetestBootstrap(unittest.TestCase):
    """Resampling moves original and retest together and records degeneracy."""

    def test_repeatable_paired_bootstrap_and_observed_differences(self):
        pairs = [(1, 2), (2, 4), (4, 5), (8, 11)]
        result = S.bootstrap_retest(pairs, B=80, seed=7)
        self.assertEqual(result, S.bootstrap_retest(iter(pairs), B=80, seed=7))
        self.assertEqual(result["n"], 4)
        self.assertEqual(result["median_absolute_difference"], 1.5)
        self.assertEqual(result["max_absolute_difference"], 3)
        # Independently select row indices, then apply the ICC to the intact rows.
        rng = random.Random(7)
        expected = [S.icc_a1([pairs[rng.randrange(4)] for _ in range(4)]) for _ in range(80)]
        self.assertTrue(all(value is not None for value in expected))
        self.assertEqual(result["icc_ci"], [S.percentile(expected, .025), S.percentile(expected, .975)])
        self.assertEqual(result["defined_bootstrap_replicates"], 80)
        self.assertEqual(result["undefined_bootstrap_replicates"], 0)

    def test_undefined_draws_are_reported_and_do_not_become_zeros(self):
        result = S.bootstrap_retest([(1, 1), (2, 2)], B=100, seed=3)
        self.assertIsNone(result["icc_ci"])
        self.assertGreater(result["undefined_bootstrap_replicates"], 0)
        self.assertGreater(result["defined_bootstrap_replicates"], 0)
        self.assertEqual(result["defined_bootstrap_replicates"] + result["undefined_bootstrap_replicates"], 100)
        all_constant = S.bootstrap_retest([(5, 5)] * 4, B=30)
        self.assertIsNone(all_constant["icc"])
        self.assertEqual(all_constant["undefined_bootstrap_replicates"], 30)

    def test_no_pairs_and_one_pair_do_not_claim_a_retest_interval(self):
        empty = S.bootstrap_retest([])
        self.assertEqual(empty["n"], 0)
        self.assertIsNone(empty["median_absolute_difference"])
        one = S.bootstrap_retest([(10, 12)])
        self.assertIsNone(one["icc"])
        self.assertIsNone(one["icc_ci"])
        self.assertEqual(one["median_absolute_difference"], 2)
        self.assertEqual(one["defined_bootstrap_replicates"], 0)

    def test_quantiles_interpolate_and_preserve_endpoints(self):
        self.assertEqual(S.percentile([1, 2, 3, 4], .25), 1.75)
        self.assertEqual(S.percentile([4, 3, 2, 1], 0), 1)
        self.assertEqual(S.percentile([4, 3, 2, 1], 1), 4)
        self.assertIsNone(S.percentile([], .5))

    def test_finite_large_differences_do_not_overflow_the_median(self):
        result = S.bootstrap_retest([(0, 1e308), (0, 1e308)], B=2)
        self.assertEqual(result["median_absolute_difference"], 1e308)
        with self.assertRaises(ValueError):
            S.bootstrap_retest([(-1e308, 1e308)])


class Validation(unittest.TestCase):
    """A missing, nonfinite or malformed observation is never silently removed."""

    def test_invalid_observations(self):
        for bad in (None, True, "1", math.nan, math.inf, -math.inf):
            with self.subTest(bad=bad):
                for call in (lambda: S.icc_a1([(1, bad), (2, 3)]),
                             lambda: S.spearman([1, bad], [1, 2]),
                             lambda: S.bootstrap_retest([(1, bad)]),
                             lambda: S.percentile([1, bad], .5)):
                    with self.assertRaises(ValueError):
                        call()

    def test_pair_shape_and_bootstrap_count(self):
        for bad in ([1], [[1]], [[1, 2, 3]]):
            with self.assertRaises(ValueError):
                S.icc_a1(bad)
        for bad in (0, -1, True, 3.5):
            with self.assertRaises(ValueError):
                S.bootstrap_retest([(1, 2)], B=bad)
        for bad in (-.1, 1.1, True, math.nan, math.inf):
            with self.assertRaises(ValueError):
                S.percentile([1, 2], bad)


if __name__ == "__main__":
    unittest.main()
