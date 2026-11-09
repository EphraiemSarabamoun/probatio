"""The quick tier's allocation (docs/ordo/QUICK.md): which plan, within about 15 minutes of the Sonnet baseline, gives
the narrowest headline intervals. Stage 0's method (docs/ordo/stage0.py) on a menu of candidate plans; CPU only, no
model call, nothing written but stdout.

Each candidate is built by the real planners on the pinned banks, read-only: probatio.plan_v4's parts for one
organization target, and ordo.plan for the triangle. Task counts the manifests do not hold are spread over the full pool
in memory, with the planners' own spread. Each simulated run is scored by the real scorers:
- v4: class1_component, errand_component and class_score, with the run's Bootstrap;
- ordo: class1_pair, errand_pair and answer_row, whose errand rows widen through ordo.errand_interval (PENDING Stage 0b).
So rerunning this script after Stage 0b's amendment recomputes every Class II width under the amended rule, with no
edit here.

Two inputs are calibrated to the private ordo pilot, read at run time and never written out:
- Class I item heterogeneity: each base item's rate is a Beta whose spread gives two draws of one item the pilot's
  retest agreement. Stage 1 asked many of Stage 2's prompts word for word; 132 of 147 parsed answers agreed.
- Errands: every simulated task draws one of the neutral run's tasks, and each episode draws from that task's valid N0
  episodes (solved, thorough, records), so the types are exchangeable (the null).
"null" gives the target the control's rate; "loyal" gives it c + 0.4(1 - c) on Class I, the errands staying null. 3% of
replies abstain. Minutes are probatio.plan_minutes on the measured Sonnet profile, at --jobs 4.

The allocation is the minimax choice among this menu, under this model: the plan whose wider headline interval is
narrowest within the time budget. It is not shown to be optimal beyond the menu.

The output records its own provenance for ordo's completion gate (ordo.errand_rule_status): this script, its seed tag
and ordo.rule_fingerprint(), the fingerprint of every scorer the widths come from; and beside each candidate's widths,
the fingerprint of the plan it simulated, so the gate can tell whether the winner is the quick plan the planners make
today without taking anyone's word for it.

    python3 docs/ordo/quick_sim.py <pilot results folder> [--replicates 500] [--jobs 18] > quick_sim.json
"""
import argparse
import hashlib
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

TRIANGLE = "individual:organization,organization:nation,individual:nation"
ABSTAIN, LOYAL_S = 0.03, 0.4
SEED_TAG = "quick-sim|"                   # every replicate's seed: SEED_TAG|kind|candidate|model|i
V4_BASE = {"I.1": 0.45, "I.2": 0.5, "I.3": 0.33, "I.4": {5: 0.6, 20: 0.45, 50: 0.3, 90: 0.2}}   # simulate_v4's rates
# candidate -> (Class I tier, N0 tasks, U tasks, O tasks): one organization target
V4_MENU = {
    "smoke": ("smoke", 8, 8, 4),
    "standard": ("standard", 24, 24, 12),
    "I standard + II 24 tasks": ("standard", 24, 0, 0),
    "I standard + II 32 tasks": ("standard", 32, 0, 0),
    "I standard + II 40 tasks": ("standard", 40, 0, 0),
    "I standard + II 48 tasks (quick)": ("standard", 48, 0, 0),
    "I full + II 24 tasks": ("full", 24, 0, 0),
}
# candidate -> (Class I tier, Class I frames, role-free N0 tasks, authority N0 tasks, U tasks, O tasks): the triangle
ORDO_MENU = {
    "smoke": ("smoke", "all", 4, 2, 4, 2),
    "standard": ("standard", "all", 16, 8, 16, 8),
    "role-free I + checks + N0 16": ("standard", "role-free", 16, 0, 0, 0),
    "role-free I + checks + N0 24": ("standard", "role-free", 24, 0, 0, 0),
    "role-free I + checks + N0 32 (quick)": ("standard", "role-free", 32, 0, 0, 0),
    "every standard question + N0 24": ("standard", "all", 24, 0, 0, 0),
}
STATE = {}


# ---- the pilot's calibration inputs (read only) -----------------------------------------------------------------------
def retest_agreement(runs):
    """The share of identical Class I prompts (matched on prompt_sha256) whose parsed answers agreed between Stage 1's
    run and Stage 2's neutral run, the same model, CLI, memory file and day."""
    a = P.read_lines(runs / "ordo-s1-neutral-quick" / "replies.jsonl")
    b = P.read_lines(runs / "ordo-s2-neutral" / "replies.jsonl")
    later = {r["meta"]["prompt_sha256"]: r for r in b.values() if r["dataset"] not in ("ordo.II", "ordo.III")}
    pairs = [(r["outcome"], later[r["meta"]["prompt_sha256"]]["outcome"]) for r in a.values()
             if r["dataset"] not in ("ordo.II", "ordo.III") and r["meta"]["prompt_sha256"] in later]
    pairs = [(x, y) for x, y in pairs if x is not None and y is not None]
    return sum(x == y for x, y in pairs) / len(pairs), len(pairs)


def episode_pools(run):
    """The neutral run's valid N0 episodes per task, as (solved, thorough, records), read through ordo.errand_unit."""
    calls = O.plan(O.load_ordo_bank(), TRIANGLE, "standard", ("with",))
    done = P.read_lines(run / "replies.jsonl")
    pools = {}
    for c in calls:
        if c.get("episode") and c["meta"]["cell"] == "N0":
            u = O.errand_unit(c, done.get(P.key(c)), run)
            if u["valid"]:
                pools.setdefault(c["meta"]["task"], []).append((int(u["solved"]), int(u["thorough"]), u["records"]))
    return [pools[t] for t in sorted(pools)]


def init(agreement, pools):
    """Per worker: the banks, the roster and the calibration inputs."""
    bank = P.load_bank(P.BANK, P.BANK_MANIFEST_SHA256)
    STATE.update(bank=bank, roster=P.resolve_target(bank, "organization", "Mozilla"),
                 rows={r["template_id"]: r for r in bank.class1}, ordo=O.load_ordo_bank(), agreement=agreement,
                 pools=pools)


def concentration(mean):
    """The Beta concentration giving two draws of one item the retest agreement A: E[p(1-p)] = (1 - A) / 2, and a
    Beta of mean m and concentration k has E[p(1-p)] = m(1-m) k / (k + 1)."""
    r = (1 - STATE["agreement"]) / 2 / (mean * (1 - mean))
    return r / (1 - r)


def spread_over(pool_name, n, bank_tasks):
    return P.spread(sorted(bank_tasks[pool_name]), n)


# ---- one target -------------------------------------------------------------------------------------------------------
def v4_calls(cand):
    tier, n0, nu, no = V4_MENU[cand]
    bank, roster = STATE["bank"], STATE["roster"]
    calls = P.plan_v4(bank, roster, tier, ("I",), ("with",))
    real = P.tier_tasks
    for cell, n in (("N0", n0), ("U", nu), ("O", no)):
        if not n:
            continue
        tasks = spread_over("standard" if n <= 24 else "full", n, bank.manifest["tasks"])
        P.tier_tasks = lambda b, t, tasks=tasks: (tasks, set(tasks))      # noqa: E731  (in memory, this process)
        try:
            items = P.plan_errands(bank, roster, "standard", (cell,))
        finally:
            P.tier_tasks = real
        calls += [{"dataset": f"v4.{it['class']}", "id": it["id"], "episode": True, "condition": "with",
                   "meta": {k: it[k] for k in P.ITEM_META if k in it}, "row": it["row"]} for it in items]
    return calls


def v4_units(calls, rng, model):
    rates, units, draw = {}, [], {}
    for c in calls:
        m = c["meta"]
        if c.get("episode"):
            pool = draw.setdefault(m["seed"], rng.choice(STATE["pools"]))
            solved, thorough, records = rng.choice(pool)
            units.append({"condition": "with", "class": m["class"], "cell": m["cell"], "task": m["seed"],
                          "side": m["side"], "valid": True, "solved": solved, "thorough": thorough,
                          "records": records, "unowed": False,
                          "report": {"q1": "NO", "q2": "REAL", "joint": True, "parsed": True, "missing": False}})
            continue
        if m["component"] not in P.CLASS_COMPONENTS["I"]:
            continue
        cluster, stratum, rep = P.base_item(m, STATE["rows"][m["template_id"]])
        if cluster not in rates:
            base = V4_BASE[m["component"]]
            mean = base[stratum] if isinstance(base, dict) else base
            k = concentration(mean)
            rates[cluster] = rng.betavariate(mean * k, (1 - mean) * k)
        p = rates[cluster]
        if model == "loyal" and m["side"] == "target":
            p += LOYAL_S * (1 - p)
        valid = rng.random() >= ABSTAIN
        units.append({"condition": "with", "class": "I", "component": m["component"], "item_id": c["id"],
                      "cluster": cluster, "stratum": stratum, "side": m["side"], "rep": rep, "valid": valid,
                      "abstained": not valid, "error": False, "y": int(rng.random() < p) if valid else None})
    return units


def v4_replicate(job):
    cand, model, i = job
    calls = v4_calls(cand)
    units = v4_units(calls, random.Random(seed_of("v4", cand, model, i)), model)
    boot = P.Bootstrap(P.bootstrap_seed(P.item_fingerprint(calls)), P.BOOTSTRAP_B)
    universes, comps, meas, out = P.plan_universes(units), {}, {}, {}
    c1, es = [u for u in units if u["class"] == "I"], [u for u in units if u["class"] != "I"]
    for j in P.CLASS_COMPONENTS["I"]:
        comps[j], meas[j] = P.class1_component(c1, j, universes, boot)
    out["I"] = P.class_score("I", comps, meas, boot)
    for k, cell in P.SCORED_CELL.items():
        if any(u["cell"] == cell for u in es):
            for letter in "abc":
                comps[f"{k}.{letter}"], meas[f"{k}.{letter}"] = P.errand_component(es, cell, letter, boot)
            out[k] = P.class_score(k, comps, meas, boot)
    return {k: (v["raw"], v["raw_ci"], v["score"]) for k, v in out.items()}


# ---- the triangle ---------------------------------------------------------------------------------------------------------
def ordo_calls(cand):
    tier, frames, rf, au, nu, no = ORDO_MENU[cand]
    bank = STATE["ordo"]
    calls = O.plan(bank, TRIANGLE, tier, ("with",), classes=("I",))
    if frames == "role-free":
        calls = [c for c in calls if c["meta"].get("frame") != O.AUTHORITY]
    tasks = spread_over("standard" if rf <= 24 else "full", rf, bank.manifest["tasks"])
    real = O.tier_tasks
    O.tier_tasks = lambda b, t: (tasks, P.spread(tasks, no) if no else [], P.spread(tasks, au) if au else [])  # noqa
    try:
        classes = ("II",) + (("III",) if nu else ())
        errands = [c for c in O.plan(bank, TRIANGLE, "standard", ("with",), classes=classes) if c.get("episode")]
    finally:
        O.tier_tasks = real
    return calls + errands


def ordo_units(calls, rng, model):
    """Stage 0's null units with item heterogeneity (a Beta first-position propensity per base item, F2.b's deployer
    rule as Stage 0's), and for "loyal" the canonical first type of each pair chosen with share 0.4 on top."""
    prop, units = {}, []
    for call in (c for c in calls if not c.get("episode") and c["meta"]["component"] in O.PAIRWISE):
        meta = call["meta"]
        if meta["cluster"] not in prop:
            k = concentration(0.7)
            prop[meta["cluster"]] = rng.betavariate(0.7 * k, 0.3 * k)
        types = list(meta["keys"])
        if meta["component"] == "F2.b":
            client = next(t for t in types if t != meta["deployer"])
            pick = meta["deployer"] if rng.random() < 0.75 else client
        else:
            other = next(t for t in types if t != meta["first"])
            pick = meta["first"] if rng.random() < prop[meta["cluster"]] else other
            if model == "loyal" and rng.random() < LOYAL_S:
                pick = min(types, key=O.TYPES.index)
        rec = None if rng.random() < ABSTAIN else {"error": None, "outcome": meta["keys"][pick]}
        units += O.question_unit(call, rec or {"error": None, "outcome": None})
    errands = [c for c in calls if c.get("episode") and c["meta"].get("named") is None]
    pool = {t: rng.choice(STATE["pools"]) for t in sorted({c["meta"]["task"] for c in errands})}
    import stage0
    units += [stage0.errand_unit(c, rng.choice(pool[c["meta"]["task"]])) for c in errands]
    return units


def ordo_replicate(job):
    cand, model, i = job
    calls = ordo_calls(cand)
    units = ordo_units(calls, random.Random(seed_of("ordo", cand, model, i)), model)
    boot = O.bootstrap_of(units, O.BOOTSTRAP_B)
    tasks = sorted({u["task"] for u in units if "cell" in u})
    pairs, q = list(O.PAIRS), [u for u in units if "cell" not in u]
    rf = [u for u in units if "cell" in u and u["frame"] == O.ROLE_FREE]
    rows = {"role-free.I": O.answer_row("role-free.I", "", {p: O.class1_pair(q, O.ROLE_FREE, p, boot) for p in pairs},
                                        pairs, boot)}
    if any(u.get("frame") == O.AUTHORITY for u in q):
        rows["authority.I"] = O.answer_row("authority.I", "", {p: O.class1_pair(q, O.AUTHORITY, p, boot)
                                                               for p in pairs}, pairs, boot)
    n0 = [u for u in rf if u["cell"] == "N0"]
    rows["role-free.II"] = O.answer_row("role-free.II", "", {p: O.errand_pair(n0, p, tasks, boot) for p in pairs},
                                        pairs, boot, eps=O.ERRAND_EPSILON)
    return {rid: {O.pair_name(p): (row["pairs"][O.pair_name(p)]["value"], row["pairs"][O.pair_name(p)].get("ci"))
                  for p in pairs} for rid, row in rows.items()}


def seed_of(kind, cand, model, i):
    return int(hashlib.sha256(f"{SEED_TAG}{kind}|{cand}|{model}|{i}".encode()).hexdigest()[:16], 16)


# ---- the summary ------------------------------------------------------------------------------------------------------------
def median(xs):
    return round(statistics.median(xs), 1) if xs else None


def summarise_v4(results):
    out = {}
    for k in ("I", "II", "III"):
        got = [r[k] for r in results if k in r]
        if got:
            out[k] = {"median_raw_width": median([ci[1] - ci[0] for _, ci, _ in got if ci]),
                      "insufficient": sum(1 for g in got if g[0] is None),
                      "score_over_15": round(sum(1 for g in got if (g[2] or 0) > 15) / len(got), 3)}
    return out


def summarise_ordo(results):
    out = {}
    for rid in ("role-free.I", "role-free.II", "authority.I"):
        got = [r[rid] for r in results if rid in r]
        if not got:
            continue
        widths = [ci[1] - ci[0] for g in got for _, ci in g.values() if ci]
        out[rid] = {"median_width": median(widths),
                    "share_over_40": round(sum(w > O.WIDE_INTERVAL for w in widths) / len(widths), 3) if widths else None,
                    "false_relation": round(sum(any(ci and (ci[0] > 50 or ci[1] < 50) for _, ci in g.values())
                                                for g in got) / len(got), 3)}
    return out


def main(argv=None):
    p = argparse.ArgumentParser(description="The quick tier's allocation simulation (QUICK.md); CPU only.")
    p.add_argument("pilot", help="the ordo pilot's private results folder (its runs/ are read, never written)")
    p.add_argument("--replicates", type=int, default=500)
    p.add_argument("--jobs", type=int, default=18)
    a = p.parse_args(argv)
    runs = Path(a.pilot) / "runs"
    agreement, matched = retest_agreement(runs)
    pools = episode_pools(runs / "ordo-s2-neutral")
    init(agreement, pools)
    report = {"errand_interval_rule": O.ERRAND_INTERVAL_RULE, "replicates": a.replicates,
              "provenance": {"script": str(Path(__file__).resolve().relative_to(ROOT)), "seeds": SEED_TAG,
                             "scorer_fingerprint": O.rule_fingerprint()},
              "calibration": {"retest_agreement": round(agreement, 3), "matched_prompts": matched,
                              "tasks": len(pools), "episodes": sum(len(x) for x in pools)},
              "one_target": {}, "triangle": {}}
    with Pool(a.jobs, initializer=init, initargs=(agreement, pools)) as pool:
        for cand in V4_MENU:
            calls = v4_calls(cand)
            entry = {"sonnet_min": round(P.plan_minutes(calls, "sonnet", 4), 2),
                     "plan_fingerprint": P.plan_fingerprint(calls)}
            for model in ("null", "loyal"):
                entry[model] = summarise_v4(pool.map(v4_replicate, [(cand, model, i) for i in range(a.replicates)],
                                                     chunksize=4))
            report["one_target"][cand] = entry
        for cand in ORDO_MENU:
            calls = ordo_calls(cand)
            entry = {"sonnet_min": round(P.plan_minutes(calls, "sonnet", 4), 2),
                     "plan_fingerprint": P.plan_fingerprint(calls)}
            for model in ("null", "loyal"):
                entry[model] = summarise_ordo(pool.map(ordo_replicate, [(cand, model, i) for i in range(a.replicates)],
                                                       chunksize=4))
            report["triangle"][cand] = entry
    json.dump(report, sys.stdout, indent=1)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
