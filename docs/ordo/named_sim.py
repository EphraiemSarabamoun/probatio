"""What a named target costs and how well it reads at quick-sized plans (docs/ordo/QUICK.md, "Named targets"): the
evidence for quick@3's rule, a named target's Class I questions and none of its errands. quick_sim.py's method and null
model (docs/ordo/quick_sim.py: Class I item heterogeneity from the pilot's retest agreement, errands drawn from the
pilot's valid N0 episodes per task), with one named organization in the triangle; CPU only, no model call, nothing
written but stdout.

Each candidate is ordo.plan's quick or standard plan with the named target's units kept or cut: its Class I questions on
the tier's pairings or the set-1 pairing alone (the R5 baseline pairing), and its counterfactual errands on all, some or
none of the tier's role-free tasks. Under the null the target is exchangeable with its type's invented entity, so a
95% interval of its effect that excludes 0 is a false effect. Each simulated run is scored by ordo.named_effects. A
candidate with errands on fewer tasks than the run's is scored on those tasks in both worlds, which is what such a plan
would need (today's named_class2 counts the run's every task as planned), so its numbers are the best that plan could
read.

    python3 docs/ordo/named_sim.py <pilot results folder> [--replicates 500] [--jobs 18] > named_sim.json
"""
import argparse
import json
import random
import statistics
import sys
from multiprocessing import Pool
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))
import ordo as O  # noqa: E402
import probatio as P  # noqa: E402
import quick_sim as Q  # noqa: E402
import stage0  # noqa: E402

TARGET = ("organization", "Mozilla")
SEED_TAG = "named"                        # every replicate's seed: quick_sim's, with this kind
# candidate -> (tier, the named target's Class I pairings: "tier" or "set 1", its errand tasks: a count spread over
# the tier's role-free tasks, 0 for none)
MENU = {
    "quick@2: Class I as the tier, errands on 32 tasks": ("quick", "tier", 32),
    "Class I as the tier, errands on 16 tasks": ("quick", "tier", 16),
    "Class I set 1, errands on 16 tasks": ("quick", "set 1", 16),
    "Class I set 1, errands on 8 tasks": ("quick", "set 1", 8),
    "Class I set 1, errands on 4 tasks": ("quick", "set 1", 4),
    "Class I set 1, no errands": ("quick", "set 1", 0),
    "Class I as the tier, no errands (quick@3)": ("quick", "tier", 0),
    "standard: Class I as the tier, errands on 16 tasks": ("standard", "tier", 16),
}
SET1 = "s11"                              # the pairing label of set 1 with set 1 (ordo.pairing_label)
STATE = {}


def init(agreement, pools):
    """Per worker: quick_sim's banks and calibration inputs, and the named target's record."""
    Q.init(agreement, pools)
    bank = P.load_bank(P.BANK, P.BANK_MANIFEST_SHA256)
    t, name = TARGET
    ents = bank.entities[t]
    STATE["target"] = dict(P.entity_spec(t, name, "target", ents["comparators"] + ents["fictional"] + ents["reserves"]),
                           type=t)


def calls_of(cand):
    """The candidate's plan: every base call of the tier's plan with a named target, and the named target's calls the
    candidate keeps."""
    tier, pairing, n = MENU[cand]
    bank = O.load_ordo_bank()
    keep = set(P.spread(O.tier_tasks(bank, tier)[0], n)) if n else set()
    out = []
    for c in plan_with_named(bank, tier):
        m = c["meta"]
        if m.get("named") and c.get("episode") and m["task"] not in keep:
            continue
        if m.get("named") and not c.get("episode") and pairing == "set 1" and m.get("pairing") != SET1:
            continue
        out.append(c)
    return out


def plan_with_named(bank, tier):
    """The tier's plan with the named target's questions and every one of its errands (quick@2's rule at quick)."""
    calls = O.plan(bank, Q.TRIANGLE, tier, ("with",), named=[STATE["target"]])
    if any(c.get("episode") and c["meta"].get("named") for c in calls):
        return calls
    real = O.named_classes
    O.named_classes = lambda t: ("I", "II")      # noqa: E731  (in memory, this process: quick@2's named rule)
    try:
        return O.plan(bank, Q.TRIANGLE, tier, ("with",), named=[STATE["target"]])
    finally:
        O.named_classes = real


def units_of(calls, rng):
    """quick_sim's null units, and the named target's errands drawn from the same per-task pools as the fictional
    world's (the null: the target is exchangeable with its type's invented entity)."""
    units = Q.ordo_units(calls, rng, "null")
    pools = {}
    for c in (c for c in calls if c.get("episode") and c["meta"].get("named")):
        pool = pools.setdefault(c["meta"]["task"], rng.choice(Q.STATE["pools"]))
        units.append(stage0.errand_unit(c, rng.choice(pool)))
    return units


def replicate(job):
    """One null run's named effects: (value, ci) for Class I and Class II, None for a class not planned."""
    cand, i = job
    calls = calls_of(cand)
    units = units_of(calls, random.Random(Q.seed_of(SEED_TAG, cand, "null", i)))
    boot = O.bootstrap_of(units, O.BOOTSTRAP_B)
    qs, es = [u for u in units if "cell" not in u], [u for u in units if "cell" in u]
    mine = {u["task"] for u in es if u.get("named")}       # both worlds on the named world's tasks (see above)
    es = [u for u in es if u["task"] in mine] if mine else es
    effects = O.named_effects(qs, es, list(O.PAIRS), sorted({u["task"] for u in es}), boot)
    if not effects:
        return {"class_I": None, "class_II": None}
    return {k: (effects[0][k]["value"], effects[0][k]["ci"]) if effects[0][k] else None
            for k in ("class_I", "class_II")}


def summary(results):
    """Per class: the median interval width, the runs that read insufficient, and the share of runs with a false
    effect (an interval excluding 0)."""
    out = {}
    for k in ("class_I", "class_II"):
        got = [r[k] for r in results if r[k] is not None]
        if not got:
            out[k] = "not planned"
            continue
        widths = [ci[1] - ci[0] for v, ci in got if ci]
        out[k] = {"median_width": round(statistics.median(widths), 1) if widths else None,
                  "insufficient": sum(1 for v, ci in got if v is None),
                  "false_effect": round(sum(1 for v, ci in got if ci and (ci[0] > 0 or ci[1] < 0)) / len(got), 3)}
    return out


def main(argv=None):
    p = argparse.ArgumentParser(description="A named target's cost and precision at quick-sized plans; CPU only.")
    p.add_argument("pilot", help="the ordo pilot's private results folder (its runs/ are read, never written)")
    p.add_argument("--replicates", type=int, default=500)
    p.add_argument("--jobs", type=int, default=18)
    a = p.parse_args(argv)
    runs = Path(a.pilot) / "runs"
    agreement, matched = Q.retest_agreement(runs)
    pools = Q.episode_pools(runs / "ordo-s2-neutral")
    init(agreement, pools)
    report = {"replicates": a.replicates, "target": " ".join(TARGET), "compare": Q.TRIANGLE,
              "provenance": {"script": str(Path(__file__).resolve().relative_to(ROOT)), "seeds": Q.SEED_TAG + SEED_TAG},
              "calibration": {"retest_agreement": round(agreement, 3), "matched_prompts": matched,
                              "tasks": len(pools), "episodes": sum(len(x) for x in pools)},
              "candidates": {}}
    with Pool(a.jobs, initializer=init, initargs=(agreement, pools)) as pool:
        for cand in MENU:
            calls = calls_of(cand)
            named = [c for c in calls if c["meta"].get("named")]
            entry = {"sonnet_min": round(P.plan_minutes(calls, "sonnet", 4), 2),
                     "named_sonnet_min": round(P.plan_minutes(named, "sonnet", 4), 2),
                     "named_questions": sum(1 for c in named if not c.get("episode")),
                     "named_errands": sum(1 for c in named if c.get("episode"))}
            entry.update(summary(pool.map(replicate, [(cand, i) for i in range(a.replicates)], chunksize=4)))
            report["candidates"][cand] = entry
    json.dump(report, sys.stdout, indent=1)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
