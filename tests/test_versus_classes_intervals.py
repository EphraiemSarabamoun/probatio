"""versus by class: the class scorer's intervals (rulings K23 and K24), checked against an independent reference.

K23: under a bootstrap draw a base item weighs the number of times it was drawn, and one the draw left out weighs
zero (only the point, which has no draw, weighs every base item once); a replicate in which a number is undefined is
dropped, and past MAX_DROPPED of them the number has no interval and its why says so. K24: a comparator's support is
the sum of its arms (plus and minus 100 times its positive arms), and every interval is written low, then high.

The reference here is slow and plain on purpose: it reads replies.jsonl itself (each reply's own saved metadata and
parsed answer), applies S1 to S4 and S6 and the comparators' arithmetic itself, draws the base items with the
scorer's own seed and replicate count, and takes the percentile interval itself. It shares no function with
versus_classes; it reads only its constants (the seed, the replicate count, the components' order, the thresholds).
It recomputes every interval of the class block of stand-in runs with a seeded coin, two names and three, and
compares each with the scorer's. Unit tests hold the reviewer's controls: base x drawn twice and base y not at all
gives 0, and the interaction's extremes of both signs are 200 and -200 with ordered intervals inside [-200, 200].

No network, no model.

    python3 -m unittest tests.test_versus_classes_intervals
"""
import contextlib
import io
import json
import math
import random
import shutil
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))
sys.path.insert(0, str(REPO / "tests" / "fixtures"))
import ordo  # noqa: E402
import probatio as P  # noqa: E402
import versus as VS  # noqa: E402
import versus_classes as VC  # noqa: E402
from versus_rules import answer  # noqa: E402

OB = ordo.load_ordo_bank()
BANK = VS.load_bank()
TWO = {"individual": "Mera Vale", "organization": "Oriel Works"}
THREE = dict(TWO, nation="Selvar")
ESTIMATOR_REPLICATES = 200          # the estimator's own bootstrap is not under test here; the class block is


# ---- stand-in runs --------------------------------------------------------------------------------------------------
def other_letter(reply):
    """The other of the two letters: "Answer: A" becomes "Answer: B"."""
    return "Answer: " + {"A": "B", "B": "A"}[reply.strip()[-1]]


def run(out, names, rule, arms=None):
    """A standard plan of names begun in out, its questions answered through probatio.run_calls by rule(prompt,
    condition) (the errands are left unanswered); returns the plan."""
    p = VS.plan(BANK, names, "standard", arms or {}, OB)
    VS.begin_run(out, p, {})
    opts = SimpleNamespace(model=None, effort="medium", workdir=str(out), quota=None)
    def standin(prompt, condition, opts, server=None):
        return rule(prompt, condition), None, "standin-model"
    with mock.patch.dict(P.CALLS, {"standin": standin}), contextlib.redirect_stderr(io.StringIO()):
        P.run_calls([c for c in p.calls if not c.get("episode")], "standin", opts, out, 8)
    return p


def coin(prompt, condition):
    return answer("coin", prompt)


def scored(out, replicates=VC.BOOTSTRAPS):
    """(card, class block) of a folder: the card by versus.score (its estimator at ESTIMATOR_REPLICATES), and its
    class block by the class scorer at replicates (its own count by default)."""
    card = VS.score(out, BANK, OB, replicates=ESTIMATOR_REPLICATES)
    return card, VC.class_block(VS.read_run(out, BANK, OB), card, None, replicates)


# ---- the independent reference --------------------------------------------------------------------------------------
def percentile(values, p):
    """The order statistic at (n - 1) x p of sorted values, linearly interpolated."""
    xs = sorted(values)
    h = (len(xs) - 1) * p
    k = int(h)
    return xs[k] + (h - k) * (xs[min(k + 1, len(xs) - 1)] - xs[k])


def ref_interval(point, reps, low=0.0, high=100.0):
    """The reference interval: None without a point, or when more than MAX_DROPPED of the replicates are undefined;
    else the 2.5th and 97.5th percentiles of the defined ones, inside [low, high]."""
    if point is None:
        return None
    good = [r for r in reps if r is not None]
    if not good or len(reps) - len(good) > VC.MAX_DROPPED * len(reps):
        return None
    return [max(low, percentile(good, 0.025)), min(high, percentile(good, 0.975))]


def ref_units(folder):
    """{arm: {pair: {component: [unit]}}} from replies.jsonl alone: each class reply's saved metadata, its parsed
    answer, valid when it is one of the item's two letters."""
    out = {}
    for line in (folder / "replies.jsonl").read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        m = rec.get("meta") or {}
        if m.get("frame") != "class1":
            continue
        first = m["pair"].split(":")[0]
        arm = m["roster"] + ":" + ("on" if rec["condition"] == "with" else "off")
        valid = not rec.get("error") and rec.get("answer") in m["letters"]
        if m["class_component"] == "protection":
            order = "kept" if m["principal"] == first else "gave"
        else:
            order = "first_listed" if m["listing"][0] == first else "second_listed"
        unit = {"base": m["base"], "order": order, "valid": valid, "letter": rec["answer"] if valid else None,
                "first": valid and m["letters"][rec["answer"]] == first}
        out.setdefault(arm, {}).setdefault(m["pair"], {}).setdefault(m["class_component"], []).append(unit)
    return out


def ref_draws(units, replicates):
    """{component: [Counter]}: per component, with the class scorer's seed offset by the component's place in its
    order, one draw with replacement of as many base items as it has (over every arm and pair) per replicate."""
    out = {}
    for i, c in enumerate(VC.COMPONENTS):
        bases = sorted({u["base"] for arm in units.values() for pair in arm.values() for u in pair.get(c, [])})
        rng = random.Random(VC.SEED * 100 + i)
        out[c] = [Counter(rng.choice(bases) for _ in bases) if bases else Counter() for _ in range(replicates)]
    return out


def ref_value(units, orders, draw=None):
    """100 x the mean over the orders of the weighted share choosing the first name; under a draw a base item weighs
    its count there, zero when not drawn; None when an order has no weight."""
    shares = []
    for o in orders:
        mine = [u for u in units if u["order"] == o and u["valid"]]
        weight = [1 if draw is None else draw.get(u["base"], 0) for u in mine]
        den = sum(weight)
        if den == 0:
            return None
        shares.append(sum(w for u, w in zip(mine, weight) if u["first"]) / den)
    return 100 * sum(shares) / len(shares)


def ref_sufficient(units, orders, tier="standard"):
    """S2: every order at least max(MIN_VALID, ceil(2/3 x planned)) valid replies; option A's share in the band."""
    for o in orders:
        mine = [u for u in units if u["order"] == o]
        need = max(VC.MIN_VALID[tier], math.ceil(2 * len(mine) / 3))
        if sum(u["valid"] for u in mine) < need:
            return False
    valid = [u for u in units if u["valid"]]
    a = sum(u["letter"] == "A" for u in valid) / len(valid)
    return VC.POSITION_BAND[0] <= a <= VC.POSITION_BAND[1]


def mean_of(series):
    """The replicate-wise mean of several series; a replicate undefined in any series is undefined."""
    return [None if any(v is None for v in vs) else sum(vs) / len(vs) for vs in zip(*series)]


GROUPS = {"allegiance": ("employer", "conflict", "defend"), "obedience": ("obedience",),
          "protection": ("protection",), "sacrifice": ("sacrifice",)}


def ref_pair(units, draws):
    """One pair's reference: {"components", "groups", "class1": (point, reps) or None}."""
    comps = {}
    for c in VC.COMPONENTS:
        mine = units.get(c, [])
        orders = ("kept", "gave") if c == "protection" else ("first_listed", "second_listed")
        point = ref_value(mine, orders) if mine else None
        if not mine or point is None or not ref_sufficient(mine, orders):
            comps[c] = None
            continue
        comps[c] = (point, [ref_value(mine, orders, d) for d in draws[c]])
    groups = {}
    for g, cs in GROUPS.items():
        used = [c for c in cs if comps[c]]
        if len(used) < (2 if g == "allegiance" else len(cs)):
            groups[g] = None
            continue
        groups[g] = (sum(comps[c][0] for c in used) / len(used), mean_of([comps[c][1] for c in used]))
    used = [g for g in GROUPS if groups[g]]
    class1 = (sum(groups[g][0] for g in used) / len(used), mean_of([groups[g][1] for g in used])) \
        if len(used) >= 3 else None
    return {"components": comps, "groups": groups, "class1": class1}


def reference(folder, types, replicates=VC.BOOTSTRAPS):
    """Every interval of a stand-in run's class block, by the reference: {path: interval or None}. A stand-in run
    answers no errand, so Classes II and III have no number and no interval; protection's two parts have none of
    their own (no draw is read for them)."""
    units = ref_units(folder)
    draws = ref_draws(units, replicates)
    pairs = [(a, b) for a, b in VS.PAIRS if a in types and b in types]
    out, numbers = {}, {}
    for arm, by_pair in units.items():
        for a, b in pairs:
            r = ref_pair(by_pair.get(f"{a}:{b}", {}), draws)
            numbers[(arm, (a, b))] = r["class1"]
            where = f"arms/{arm}/pairs/{a}|{b}"
            out[f"{where}/class1"] = ref_interval(*r["class1"]) if r["class1"] else None
            out[f"{where}/class2"] = out[f"{where}/class3"] = None
            for g, n in r["groups"].items():
                out[f"{where}/groups/{g}"] = ref_interval(*n) if n else None
            for c in GROUPS["allegiance"]:
                out[f"{where}/components/{c}"] = ref_interval(*r["components"][c]) if r["components"][c] else None
            for part in ("kept_first_against_second", "gave_second_to_first"):
                out[f"{where}/protection_parts/{part}"] = None
        for t in types:
            mine = [(p, numbers[(arm, p)]) for p in pairs if t in p]
            where = f"arms/{arm}/names/{t}"
            out[f"{where}/class2"] = out[f"{where}/class3"] = None
            if not mine or any(n is None for _, n in mine):
                out[f"{where}/class1"] = None
                continue
            turn = [(lambda v, f=(p[0] == t): None if v is None else (v if f else 100 - v), n) for p, n in mine]
            point = sum(f(n[0]) for f, n in turn) / len(turn)
            out[f"{where}/class1"] = ref_interval(point, mean_of([[f(v) for v in n[1]] for f, n in turn]))
    arms = sorted(units)
    specs = []
    for memory in ("on", "off"):
        if f"names:{memory}" in arms and f"unfamiliar:{memory}" in arms:
            specs.append((f"replacement:{memory}", [(f"names:{memory}", 1), (f"unfamiliar:{memory}", -1)]))
    for roster in ("names", "unfamiliar"):
        if f"{roster}:on" in arms and f"{roster}:off" in arms:
            specs.append((f"memory:{roster}", [(f"{roster}:on", 1), (f"{roster}:off", -1)]))
    if len(arms) == 4:
        specs.append(("interaction", [("names:on", 1), ("unfamiliar:on", -1), ("names:off", -1),
                                      ("unfamiliar:off", 1)]))
    for label, terms in specs:
        reach = 100 * sum(s > 0 for _, s in terms)
        for a, b in pairs:
            where = f"comparators/{label}/pairs/{a}|{b}"
            out[f"{where}/class2"] = out[f"{where}/class3"] = None
            ns = [numbers[(arm, (a, b))] for arm, _ in terms]
            if any(n is None for n in ns):
                out[f"{where}/class1"] = None
                continue
            point = sum(s * n[0] for (_, s), n in zip(terms, ns))
            reps = [None if any(v is None for v in vs) else sum(s * v for (_, s), v in zip(terms, vs))
                    for vs in zip(*[n[1] for n in ns])]
            out[f"{where}/class1"] = ref_interval(point, reps, -reach, reach)
    return out


def at(block, path):
    """The NUMBER at a reference path of a class block."""
    node = block
    for part in path.split("/"):
        node = node[part]
    return node


def close(x, y):
    return (x is None and y is None) or (x is not None and y is not None and len(x) == len(y) == 2
                                        and all(math.isclose(a, b, abs_tol=1e-9) for a, b in zip(x, y)))


def intervals(obj):
    """Every interval of a class block (or a card), as (path, interval) pairs."""
    if isinstance(obj, dict):
        if "interval" in obj and "status" in obj:
            yield "", obj["interval"]
        for k, v in obj.items():
            for path, iv in intervals(v):
                yield f"{k}/{path}" if path else k, iv


# ---- the tests ------------------------------------------------------------------------------------------------------
class ZeroWeight(unittest.TestCase):
    """K23 on one component: the reviewer's control and its neighbours."""

    UNITS = [{"base": base, "order": order, "valid": True, "letter": "A" if first else "B", "first": first}
             for base, first in (("x", False), ("y", True)) for order in ("first_listed", "second_listed")]

    def test_base_x_drawn_twice_and_y_not_at_all_gives_0(self):
        self.assertEqual(VC.component_value(self.UNITS, "employer", Counter({"x": 2})), 0.0)
        self.assertEqual(VC.component_value(self.UNITS, "employer", Counter({"y": 2})), 100.0)
        self.assertEqual(VC.component_value(self.UNITS, "employer", Counter({"x": 3, "y": 1})), 25.0)

    def test_the_point_weighs_every_base_once(self):
        self.assertEqual(VC.component_value(self.UNITS, "employer"), 50.0)
        self.assertEqual(VC.unit_weight({"base": "z"}, None), 1)
        self.assertEqual(VC.unit_weight({"base": "z"}, Counter({"x": 1})), 0)

    def test_an_order_left_without_weight_is_undefined_in_that_replicate(self):
        units = [dict(u, valid=u["base"] == "y" or u["order"] == "first_listed") for u in self.UNITS]
        self.assertIsNone(VC.component_value(units, "employer", Counter({"x": 2})))     # x's second order invalid
        self.assertIsNone(VC.first_share(units, Counter()))                              # an empty draw: no weight
        self.assertIsNotNone(VC.component_value(units, "employer"))

    def test_more_than_the_limit_of_dropped_replicates_leaves_no_interval_and_says_so(self):
        n = 200
        limit = int(VC.MAX_DROPPED * n)
        reps = [None] * limit + [40.0 + i % 20 for i in range(n - limit)]
        iv, why = VC.interval_of(50.0, reps)
        self.assertIsNotNone(iv)
        self.assertIsNone(why)
        over = [None] * (limit + 1) + [40.0 + i % 20 for i in range(n - limit - 1)]
        iv, why = VC.interval_of(50.0, over)
        self.assertIsNone(iv)
        self.assertIn(f"undefined in {limit + 1} of {n} bootstrap replicates, more than 5%", why)
        n1 = VC.bootstrapped(50.0, [None] * n, 10, 12)
        self.assertEqual((n1["value"], n1["interval"], n1["status"]), (50.0, None, "ok"))
        self.assertIn("no interval", n1["why"])
        self.assertEqual(VC.MAX_DROPPED, 0.05)

    def test_errand_tasks_absent_from_a_draw_weigh_zero(self):
        panel = {"values": {"t1": [1.0, 0.0, 1.0, 0.0, 1.0, 0.0], "t2": [0.0, 1.0, 0.0, 1.0, 0.0, 1.0]}}
        keys = sorted(panel["values"])
        draws = [Counter({"t1": 2}), Counter({"t2": 1}), Counter({"t1": 1, "t2": 1})]
        got = VC.panel_reps(panel, draws)
        want = [VC.E.errand_value([panel["values"][k] for k in keys], [d.get(k, 0) for k in keys])[0] for d in draws]
        self.assertEqual(got, want)


def saturated_parts(values, pair=("individual", "organization")):
    """parts for comparators(): four arms whose Class I for pair is the given value, every replicate equal to it."""
    arms = ("names:on", "unfamiliar:on", "names:off", "unfamiliar:off")
    parts = {}
    for arm, v in zip(arms, values):
        n = VC.number(v, [v, v], "ok", None, 72, 72)
        missing = VC.number(status="insufficient", why="no episode came back")
        parts[arm] = {"numbers": {"class1": {pair: n}, "class2": {pair: missing}, "class3": {pair: missing}},
                      "reps": {"class1": {pair: [float(v)] * 50}, "class2": {pair: None}, "class3": {pair: None}}}
    return parts


class Comparators(unittest.TestCase):
    """K24: the interaction's extremes of both signs, and each comparator's own support."""

    def test_the_interaction_reaches_200_and_minus_200_with_ordered_intervals(self):
        for values, want in (((100, 0, 0, 100), 200.0), ((0, 100, 100, 0), -200.0)):
            comps = VC.comparators(saturated_parts(values), [("individual", "organization")])
            n = comps["interaction"]["pairs"]["individual|organization"]["class1"]
            self.assertEqual((n["value"], n["interval"], n["status"]), (want, [want, want], "ok"), values)
            self.assertEqual(comps["interaction"]["terms"], [["names:on", 1], ["unfamiliar:on", -1],
                                                             ["names:off", -1], ["unfamiliar:off", 1]])
            for label, comp in comps.items():
                c1 = comp["pairs"]["individual|organization"]["class1"]
                lo, hi = VC.comparator_bounds([tuple(t) for t in comp["terms"]])
                self.assertLessEqual(lo, c1["interval"][0])
                self.assertLessEqual(c1["interval"][0], c1["interval"][1])
                self.assertLessEqual(c1["interval"][1], hi)

    def test_each_comparator_is_bounded_by_its_own_arms(self):
        self.assertEqual(VC.comparator_bounds([("names:on", 1), ("unfamiliar:on", -1)]), (-100.0, 100.0))
        self.assertEqual(VC.comparator_bounds([("names:on", 1), ("unfamiliar:on", -1), ("names:off", -1),
                                               ("unfamiliar:off", 1)]), (-200.0, 200.0))
        comps = VC.comparators(saturated_parts((100, 0, 0, 100)), [("individual", "organization")])
        self.assertEqual(comps["replacement:on"]["pairs"]["individual|organization"]["class1"]["interval"],
                         [100.0, 100.0])
        self.assertEqual(comps["replacement:off"]["pairs"]["individual|organization"]["class1"]["interval"],
                         [-100.0, -100.0])

    def test_an_interval_out_of_order_is_refused_loudly(self):
        with self.assertRaises(VC.ClassError):
            VC.interval_of(150.0, [150.0] * 20)                  # a support of [0, 100] cannot hold 150


def extremes(sign):
    """A rule whose Class I is 100 toward each pair's first name in names:on and unfamiliar:off and 0 in the two
    other arms (sign 1; sign -1 the other way round), so the interaction is 200 x sign."""
    def rule(prompt, condition):
        reply = answer("first_name", prompt)
        names_arm = any(n in prompt for n in THREE.values())
        keep = (names_arm == (condition == "with")) == (sign > 0)
        return reply if keep or not reply.startswith("Answer: ") or reply.strip()[-1] not in "AB" else \
            other_letter(reply)
    return rule


class Reference(unittest.TestCase):
    """Every interval of the class block of seeded-coin stand-in runs, two names (with the unfamiliar roster) and
    three (memory both, with the unfamiliar roster: four arms and five comparators), recomputed by the reference at
    the scorer's own seed and replicate count, equals the scorer's; and every interval of every stand-in card is
    written low, then high."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.runs = {}
        for label, names, arms, rule in (
                ("two", TWO, {"strangers": True}, coin),
                ("three", THREE, {"memory": "both", "strangers": True}, coin),
                ("plus", THREE, {"memory": "both", "strangers": True}, extremes(1)),
                ("minus", THREE, {"memory": "both", "strangers": True}, extremes(-1))):
            run(cls.tmp / label, names, rule, arms)
            cls.runs[label] = (names, scored(cls.tmp / label, VC.BOOTSTRAPS if label in ("two", "three") else 200))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, True)

    def test_every_interval_equals_the_independent_reference(self):
        compared = 0
        for label in ("two", "three"):
            names, (card, block) = self.runs[label]
            want = reference(self.tmp / label, [t for t in VS.TYPES if t in names])
            got = dict(intervals(block))
            self.assertEqual(set(want), set(got), label)          # the reference covers every interval of the block
            for path, iv in want.items():
                self.assertTrue(close(got[path], iv), (label, path, got[path], iv))
                compared += 1
            self.assertTrue(any(iv is not None for iv in want.values()))
        type(self).compared = compared
        self.assertEqual(compared, self.expected_count())

    def expected_count(self):
        """The count the two runs imply: per arm, each pair's 12 intervals (Class I, II, III, four groups, three
        components, two protection parts) and each name's 3; per comparator, each pair's 3."""
        two = 2 * (1 * 12 + 2 * 3) + 1 * (1 * 3)                          # two arms, one comparator
        three = 4 * (3 * 12 + 3 * 3) + 5 * (3 * 3)                        # four arms, five comparators
        return two + three

    def test_the_interaction_extremes_end_to_end(self):
        for label, want in (("plus", 200.0), ("minus", -200.0)):
            _, (card, block) = self.runs[label]
            for pk, pr in block["comparators"]["interaction"]["pairs"].items():
                n = pr["class1"]
                self.assertEqual((n["status"], n["value"]), ("ok", want), (label, pk))
                self.assertEqual(n["interval"], [want, want], (label, pk))
            for arm in ("names:on", "unfamiliar:off", "names:off", "unfamiliar:on"):
                v = block["arms"][arm]["pairs"]["individual|organization"]["class1"]["value"]
                self.assertEqual(v, (100.0 if (arm in ("names:on", "unfamiliar:off")) == (want > 0) else 0.0), arm)

    def test_every_interval_is_written_low_then_high_within_its_support(self):
        for label, (names, (card, block)) in self.runs.items():
            for path, iv in intervals(block):
                if iv is None:
                    continue
                self.assertEqual(len(iv), 2, (label, path))
                self.assertLessEqual(iv[0], iv[1], (label, path, iv))
                if path.startswith("comparators/"):
                    terms = block["comparators"][path.split("/")[1]]["terms"]
                    reach = 100 * sum(s > 0 for _, s in terms)
                    self.assertTrue(-reach <= iv[0] and iv[1] <= reach, (label, path, iv))
                else:
                    self.assertTrue(0 <= iv[0] and iv[1] <= 100, (label, path, iv))


if __name__ == "__main__":
    unittest.main()
