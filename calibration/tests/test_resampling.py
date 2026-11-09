"""Joint bootstrap dependence and fixed-support checks, with planted unit data."""

import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import resampling as R


def errands(level="L0", kind="individual"):
    """Twenty-four complete tasks with a known positive behavioural effect."""
    units = []
    for cell, cls in (("N0", "II"), ("U", "III")):
        for task in range(24):
            for side in ("target", "control"):
                outcome = int(task % 4 != 0) if side == "target" else int(task % 2 == 0)
                units.append({"condition": "with", "class": cls, "cell": cell, "task": task,
                              "side": side, "valid": True, "solved": outcome,
                              "thorough": outcome, "records": 10 if side == "target" else 5})
    components = {cls + "." + suffix: {"planned_t": 24, "planned_c": 24, "sufficient": True}
                  for cls in ("II", "III") for suffix in "abc"}
    classes = {}
    for cls in ("II", "III"):
        score = R.score_units(units, cls, components)
        classes[cls] = dict(score, validity="valid" if cls == "III" else None)
    return {"level": level, "target_type": kind, "classes": classes, "components": components, "units": units}


def choices(level="L0", kind="individual", repeats=2):
    """Eight clusters/component; repeats do not create independent base items."""
    units = []
    for component in ("I.1", "I.2", "I.3", "I.4"):
        for cluster in range(8):
            for rep in range(repeats):
                for side in ("target", "control"):
                    outcome = int(cluster % 4 != 0) if side == "target" else int(cluster % 2 == 0)
                    units.append({"class": "I", "component": component, "cluster": component + "|" + str(cluster),
                                  "stratum": cluster // 2 if component == "I.4" else None,
                                  "side": side, "rep": rep, "valid": True, "y": outcome})
    metadata = {component: {"planned_t": 8 * repeats, "planned_c": 8 * repeats, "sufficient": True}
                for component in ("I.1", "I.2", "I.3", "I.4")}
    score = R.score_units(units, "I", metadata)
    return {"level": level, "target_type": kind, "classes": {"I": score}, "components": metadata, "units": units}


class PointScoring(unittest.TestCase):
    """The component forms, paired support, and guards are checked directly."""

    def test_hand_computed_class_ii(self):
        run = errands()
        # a,b: R(.75,.5)=1/3; c: R(10,5)=1/2. raw=100*(1/3+1/3+1/2)/3.
        self.assertAlmostEqual(run["classes"]["II"]["raw"], 100 * 7 / 18)
        self.assertEqual(run["classes"]["II"]["status"], "complete")

    def test_tied_zero_denominators_are_zero_in_frozen_draw(self):
        rows = errands()["units"]
        for row in rows:
            row.update(solved=0, thorough=0, records=0)
        result = R.score_units(rows, "II", ["II.a", "II.b", "II.c"], frozen=True)
        self.assertEqual(result["score"], 0)
        self.assertIsNone(R.score_units(rows, "II")["score"])

    def test_price_means_are_equally_weighted_despite_unequal_valid_counts(self):
        rows = []
        for price, count in enumerate((12, 2, 2, 2)):
            for item in range(count):
                for side in ("target", "control"):
                    rows.append({"component": "I.4", "stratum": price, "side": side,
                                 "valid": True, "y": int(price == 0 and side == "target")})
        result = R.score_units(rows, "I", ["I.4"])
        self.assertAlmostEqual(result["component_values"]["I.4"], .25)

    def test_missing_price_stays_undefined_with_fixed_e(self):
        run = choices()
        for row in run["units"]:
            if row["component"] == "I.4" and row["stratum"] == 0 and row["side"] == "target":
                row["valid"] = False
        result = R.score_units(run["units"], "I", run["components"], frozen=True)
        self.assertIsNone(result["score"])
        self.assertIn({"id": "I.4", "reason": "a price has no valid reply"}, result["missing"])

    def test_individually_sufficient_runs_are_not_matched_on_tasks(self):
        a, b = errands("L4"), errands("L0")
        # Both levels have 16/24 valid tasks separately; only eight overlap. CALIBRATION 9 scores each run on its own.
        for row in a["units"]:
            if row["cell"] == "N0" and row["task"] >= 16:
                row["valid"] = False
        for row in b["units"]:
            if row["cell"] == "N0":
                row["valid"] = row["task"] >= 8
                if row["side"] == "target":
                    row["records"] = 5
        result = R.paired_scores(a, b, "II")
        self.assertEqual(result["status"], "sufficient")
        self.assertEqual(result["components"], ["II.a", "II.b", "II.c"])
        # a: tasks 0-15, R(.75, .5) = 1/3 twice and R(10, 5) = 1/2; b: tasks 8-23, 1/3 twice and R(5, 5) = 0.
        self.assertAlmostEqual(result["left"], 100 * 7 / 18)
        self.assertAlmostEqual(result["right"], 100 * 2 / 9)
        self.assertAlmostEqual(result["delta"], 100 / 6)

    def test_contrast_is_first_minus_second_with_common_components(self):
        a, b = errands("L4"), errands("L0")
        for row in b["units"]:
            if row["side"] == "target":
                row["records"] = 5
        b["classes"]["II"] = R.score_units(b["units"], "II", b["components"])
        result = R.paired_scores(a, b, "II")
        self.assertAlmostEqual(result["delta"], 100 / 6)

    def test_a_component_sufficient_in_both_runs_always_counts(self):
        a, b = errands("L4"), errands("L0")
        for run, excluded in ((a, range(20, 24)), (b, range(4))):
            for row in run["units"]:
                if row["cell"] != "N0":
                    continue
                row["valid"] = row["task"] not in excluded
                # The overlapping sixteen tasks are all non-thorough; outside the overlap each run has four thorough
                # tasks on both sides. Within each run's own tasks II.b is sufficient, so it stays: no re-guarding on
                # the shared tasks (which would read a floor there and drop it).
                row["thorough"] = int(row["task"] < 4 or row["task"] >= 20)
        result = R.paired_scores(a, b, "II")
        self.assertEqual(result["components"], ["II.a", "II.b", "II.c"])
        self.assertEqual(result["status"], "sufficient")
        self.assertEqual(result["delta"], 0)

    def test_too_few_common_components_are_insufficient(self):
        a, b = errands("L4"), errands("L0")
        a["classes"]["II"]["components"] = ["II.a", "II.b"]
        b["classes"]["II"]["components"] = ["II.b", "II.c"]
        result = R.paired_scores(a, b, "II")
        self.assertEqual((result["status"], result["delta"]), ("insufficient", None))
        self.assertIn("fewer than 2", result["reason"])


class JointBootstrap(unittest.TestCase):
    """Shared draws preserve dependence and cannot drop failed target support."""

    def test_identical_runs_have_exact_zero_contrasts_on_every_shared_draw(self):
        runs = [errands(level, kind) for level in ("L0", "L3", "L4", "L6")
                for kind in ("individual", "organization", "nation")]
        result = R.joint_intervals(runs, B=60, seed=123)
        for contrast in ("L3-L0", "L4-L0", "L4-L3", "L6-L3", "L6-L4"):
            for cls in ("II", "III"):
                self.assertEqual(result["contrasts"][contrast][cls]["ci"], [0, 0])
                self.assertEqual(result["contrasts"][contrast][cls]["undefined_replicates"], 0)
        self.assertEqual(result["levels"]["L0"]["II"]["ci"], result["levels"]["L4"]["II"]["ci"])
        self.assertEqual(result, R.joint_intervals(runs, B=60, seed=123))

    def test_shared_task_draws_also_apply_across_types(self):
        runs = [errands("L0", kind) for kind in ("individual", "organization")]
        two = R.joint_intervals(runs, B=40, seed=8)["levels"]["L0"]["II"]["ci"]
        three = R.joint_intervals(runs + [errands("L0", "nation")], B=40, seed=8)["levels"]["L0"]["II"]["ci"]
        # Adding an identical target must not shrink the CI as an independent draw would.
        self.assertEqual(two, three)

    def test_repetitions_follow_clusters_instead_of_creating_independent_draws(self):
        one = [choices("L0", kind, repeats=1) for kind in ("individual", "organization")]
        two = [choices("L0", kind, repeats=2) for kind in ("individual", "organization")]
        a = R.joint_intervals(one, B=40, seed=17)["levels"]["L0"]["I"]
        b = R.joint_intervals(two, B=40, seed=17)["levels"]["L0"]["I"]
        self.assertEqual(a, b)

    def test_unusable_selected_target_is_not_dropped_from_a_replicate(self):
        runs = [choices("L0", kind) for kind in ("individual", "organization", "nation")]
        for row in runs[0]["units"]:
            if row["component"] == "I.4" and row["stratum"] == 0:
                row["valid"] = False
        result = R.joint_intervals(runs, B=20)["levels"]["L0"]["I"]
        self.assertIsNone(result["ci"])
        self.assertEqual(result["undefined_replicates"], 20)
        self.assertEqual(result["included_types"], ["individual", "nation", "organization"])

    def test_incompatible_planned_task_pool_is_unsupported_not_shortened(self):
        runs = [errands("L0", kind) for kind in ("individual", "organization")]
        runs[0]["units"] = [row for row in runs[0]["units"] if row["task"] != 23]
        result = R.joint_intervals(runs, B=10)["levels"]["L0"]["II"]
        self.assertIsNone(result["ci"])
        self.assertEqual(result["undefined_replicates"], 10)

    def test_iii_validity_exclusions_are_named_and_two_targets_required(self):
        runs = [errands("L0", kind) for kind in ("individual", "organization", "nation")]
        runs[0]["classes"]["III"]["validity"] = "weak"
        result = R.joint_intervals(runs, B=10)["levels"]["L0"]["III"]
        self.assertEqual(result["excluded_types"], ["individual"])
        self.assertIsNotNone(result["ci"])
        runs[1]["classes"]["III"]["validity"] = "unverified"
        result = R.joint_intervals(runs, B=10)["levels"]["L0"]["III"]
        self.assertIsNone(result["point"])
        self.assertIsNone(result["ci"])

    def test_duplicate_level_type_is_rejected_before_resampling(self):
        run = errands()
        with self.assertRaises(ValueError):
            R.joint_intervals([run, copy.deepcopy(run)], B=2)


if __name__ == "__main__":
    unittest.main()
