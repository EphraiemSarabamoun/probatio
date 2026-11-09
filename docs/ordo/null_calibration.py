"""Null calibration of the pilot's primary test (Codex R1), with eligibility allowed to change between datasets.

Each simulated dataset is one sharp-null orbit: 16 complete tasks whose four episodes are drawn independently given the
task, so the arm labels carry no information. Regimes vary the solved and thorough rates, so a component is eligible in
some datasets and not in others (eligibility is never fixed in advance across datasets). For every orbit, the size of
three procedures is computed exactly over all 2^16 labellings (primary_test.orbit_sizes): the frozen invariant rule,
revision 2's observed-E rule, and conditioning on E. The invariant rule's size is at most alpha on every orbit by
construction; the calibration shows it, and shows how often revision 2's rule exceeded alpha.

    python3 docs/ordo/null_calibration.py      # writes docs/ordo/null_calibration.json (a few minutes)
"""
import json
import random
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import primary_test as T  # noqa: E402

SEED = 20261108
TASKS = 16
PER_REGIME = 12
REGIMES = {   # name: (solved rate range, thorough rate range), per task
    "typical": ((0.40, 0.95), (0.30, 0.80)),
    "sparse_solved": ((0.00, 0.12), (0.30, 0.80)),
    "sparse_both": ((0.00, 0.10), (0.00, 0.10)),
    "near_ceiling": ((0.97, 1.00), (0.85, 1.00)),
}


def episode(rng, ps, pt):
    """One episode's (solved, thorough, records): thorough episodes examine 10 to 14 records, the rest 1 to 9."""
    thorough = int(rng.random() < pt)
    return int(rng.random() < ps), thorough, (10 + rng.randint(0, 4)) if thorough else rng.randint(1, 9)


def dataset(rng, regime):
    (s0, s1), (t0, t1) = REGIMES[regime]
    tasks = []
    for _ in range(TASKS):
        ps, pt = rng.uniform(s0, s1), rng.uniform(t0, t1)
        eps = [episode(rng, ps, pt) for _ in range(4)]
        tasks.append({c: tuple(e[i] for e in eps) for i, c in enumerate(T.COMPONENTS)})
    return tasks


def main():
    rng = random.Random(SEED)
    rows = []
    for regime in REGIMES:
        for k in range(PER_REGIME):
            tasks = dataset(rng, regime)
            E = T.eligible(tasks)
            sizes = T.orbit_sizes(tasks)
            rows.append({"regime": regime, "index": k, "eligible": list(E),
                         "invariant": None if sizes["invariant"] is None else float(sizes["invariant"]),
                         "rev2_observed_E": float(sizes["rev2_observed_E"]),
                         "conditional_on_E": float(sizes["conditional_on_E"])})
            print(rows[-1], flush=True)
    tested = [r for r in rows if r["invariant"] is not None]
    summary = {
        "orbits": len(rows), "tasks_per_orbit": TASKS, "alpha": float(T.ALPHA), "seed": SEED,
        "insufficient_orbits": len(rows) - len(tested),
        "eligible_sets": dict(sorted(Counter(",".join(r["eligible"]) or "none" for r in rows).items())),
        "invariant": {"max": max(r["invariant"] for r in tested), "mean": sum(r["invariant"] for r in tested) / len(tested)},
        "rev2_observed_E": {"max": max(r["rev2_observed_E"] for r in rows),
                            "orbits_above_alpha": sum(1 for r in rows if r["rev2_observed_E"] > float(T.ALPHA))},
        "conditional_on_E": {"max": max(r["conditional_on_E"] for r in rows)},
    }
    (HERE / "null_calibration.json").write_text(json.dumps({"summary": summary, "orbits": rows}, indent=1) + "\n")
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
