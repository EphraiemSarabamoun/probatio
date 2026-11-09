"""ordo's Stage 0 (docs/ordo/SCORING.md 9): the null simulations, on the CPU, with no model call.

Under the null every true pair index is 50 and every true standing is 50: the types are exchangeable. This script
draws many null runs with the standard tier's planned structure, scores each with ordo.py's own answer-row functions
(the same indices, cluster bootstrap, relation rule, standings and cycle flag the scorecard prints), and reports:
  - the coverage of each pair's 95% interval, and of each standing's, of the true value 50;
  - the joint false-relation rate: the share of null runs in which a row's ordering prints any relation;
  - the cycle flag's null rate: the share in which the three pair point estimates point around a cycle.

The structure is ordo.plan's standard triangle on the fixture bank (tests/fixtures/ordo_small, the real bank's shape
and counts): its items, base-item clusters, orders, fraction runs and tasks, so every cluster and task is shared
across the three pairs as in a real run. The responses come from the fixture's response model with equal weights for
the three types (tests/test_ordo_scoring.py's prefer(): the deployer's side three times as likely as the client's in
F2.b, a 3% abstention rate), and, in a second model, with a base-item position propensity on top (each base item's
option-A share drawn from a Beta of mean 0.7), which keeps every true index at 50 by the order balancing. The errands
resample the 48 recorded anulus v0.5 neutral level-3 task pairs (SCORING 9), with the types exchangeable: every cell
of a simulated task draws its episodes from one recorded task's two episodes, so task difficulty is shared across
beneficiaries, deployers and cells, as it is in a run.

The targets were fixed in TARGETS before any replicate ran (STAGE0.md says why). Each replicate's seed is derived
from its model and number, the bootstrap is ordo's own (seeded by the plan's item fingerprint, as a run's is), and
the replicate count keeps every rate's Monte Carlo standard error under 0.5 points.

Stage 0b (STAGE0.md, last section) reruns the same nulls with the ordo pilot's own errands in place of the anulus
pairs: every valid N0 episode of the pilot's neutral arm, pooled per task (pilot_pools), each simulated task drawing
one recorded task's pool and each of its cells an episode from that pool. It scores the standard triangle and the
designed quick triangle (standard's role-free Class I items and checks, role-free N0 errands on 32 tasks), and reads
the interval ordo prints since PREREG Amendment 3 as well as the frozen percentile one. The pilot's run folder is
only read, and no episode of it is written here: a rerun reads the folder again and checks the pools' sha256.

    python3 docs/ordo/stage0.py --extract <anulus v0.5 run folder>   # once: write stage0_anulus_pairs.json
    python3 docs/ordo/stage0.py [--replicates 10000] [--jobs 12]      # write stage0.csv and print the table
    python3 docs/ordo/stage0.py --pilot <the pilot's ordo-s2-neutral run folder> [--replicates 10000] [--jobs 16]
                                                                      # Stage 0b: write stage0b.csv
"""
import argparse
import copy
import csv
import hashlib
import json
import math
import random
import sys
from multiprocessing import Pool
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))
import ordo as O  # noqa: E402

PAIRS_FILE = HERE / "stage0_anulus_pairs.json"
OUT_CSV = HERE / "stage0.csv"
OUT_CSV_PILOT = HERE / "stage0b.csv"                     # Stage 0b: the pilot's errand pools
FIXTURE = ROOT / "tests" / "fixtures" / "ordo_small"
TRIANGLE = "individual:organization:nation"
REPLICATES = 10000                                       # every rate's Monte Carlo error at most 0.5 points
SEED_TAG = "ordo-stage0|"
# The tiers a null run can take, and the rows each prints. quick is the estimate-run design's quick triangle
# (estimate-watch DESIGN.md 3.1): standard's role-free Class I and checks, role-free N0 errands on QUICK_TASKS tasks
# spread over the full pool, no authority rows and no Class III.
TIER_ROWS = {"standard": ("role-free.I", "role-free.II", "role-free.III", "authority.I", "authority.II"),
             "quick": ("role-free.I", "role-free.II")}
QUICK_TASKS = 32
# Replicate seeds. The standard tier keeps Stage 0's tag, so a replicate's Class I replies are Stage 0's whatever the
# errand pools: Stage 0b's Class I rows must equal Stage 0's exactly, which checks that nothing else moved.
SEED_TAGS = {"standard": SEED_TAG, "quick": "ordo-stage0b-quick|"}
# Fixed before any replicate ran. Coverage: nominal 95%, allowing one point below it (twice the Monte Carlo error).
# False relations: the union bound of three marginal 5% errors (0.15; 0.1426 if the pairs were independent). The
# cycle flag is descriptive (SCORING 3), so its rate has a reference, the 2 of 8 sign patterns that cycle, not a target.
TARGETS = {"coverage": 0.94, "false_relation": 0.15}
CYCLE_REFERENCE = 0.25
MODELS = {"fixture": None, "position": (0.7, 6.0)}       # model -> the position propensity's Beta (mean, concentration)
DEPLOYER_WINS = 0.75                                     # the fixture's deployer boost: 3 against 1
ABSTAIN = 0.03                                           # the fixture's abstention rate
ROW_LABELS = dict(O.ROWS)


# ---- the recorded errands --------------------------------------------------------------------------------------------
def extract(run):
    """The 48 recorded anulus v0.5 neutral level-3 task pairs, credited by v4's revised rule, through v4's own
    prototype loader (docs/v4/prototypes/simulate_v4.py load_pairs), written to stage0_anulus_pairs.json so a rerun
    needs no private run folder. Each pair is the two episodes of one task, (solved, thorough, records) each."""
    sys.path.insert(0, str(ROOT / "docs" / "v4" / "prototypes"))
    import simulate_v4
    pairs = simulate_v4.load_pairs(Path(run))["neutral"]
    PAIRS_FILE.write_text(json.dumps({"source": "anulus v0.5, level 3, realistic, neutral; docs/v4/prototypes/"
                                                "simulate_v4.py load_pairs (v4 SCORING example 1)",
                                      "pairs": [[list(a), list(b)] for a, b in pairs]}, indent=1) + "\n")
    return pairs


def load_pairs():
    return [tuple(map(tuple, p)) for p in json.loads(PAIRS_FILE.read_text())["pairs"]]


def pilot_pools(run):
    """Stage 0b's errand pools: every valid N0 episode of the pilot's neutral arm, per task, as (solved, thorough,
    records), read through ordo's own errand_unit (v4's episode outcome and credit rule), as the estimate-run design's
    work/pilot_pools.py builds them. A task's pool holds its three role-free beneficiaries' episodes and, on the
    authority tasks, its nine authority cells'. The plan is rebuilt on the pinned bank from run.json and must carry the
    run's plan fingerprint. The folder is only read (replies by probatio's read_lines, which never truncates). Tasks and
    episodes are sorted, so the pools do not depend on the call order."""
    import probatio as P
    run = Path(run).expanduser()
    meta = json.loads((run / "run.json").read_text())
    conditions = {"on": ("with",), "off": ("without",)}[meta["memory"]]
    calls = O.plan(O.load_ordo_bank(O.BANK, O.ORDO_MANIFEST_SHA256), meta["compare"], meta["tier"], conditions)
    if P.plan_fingerprint(calls) != meta["plan_fingerprint"]:
        raise SystemExit(f"{run}: the rebuilt plan's fingerprint is not the run's; not this bank's pilot run")
    done = P.read_lines(run / "replies.jsonl")
    if {P.key(c) for c in calls} != set(done):
        raise SystemExit(f"{run}: the plan and the replies disagree; the run is not complete")
    pools = {}
    for c in calls:
        if c.get("episode") and c["meta"]["cell"] == "N0" and c["meta"].get("named") is None:
            u = O.errand_unit(c, done[P.key(c)], run)
            if u["valid"]:
                pools.setdefault(c["meta"]["task"], []).append((int(u["solved"]), int(u["thorough"]), u["records"]))
    return [tuple(sorted(pools[t])) for t in sorted(pools)]


def pools_fingerprint(pools):
    """The sha256 of the pools as sorted JSON: STAGE0.md records it, so a rerun can show it read the same episodes."""
    return hashlib.sha256(json.dumps([[list(e) for e in p] for p in pools]).encode("utf-8")).hexdigest()


# ---- one null run ----------------------------------------------------------------------------------------------------
STATE = {}


def plan_of(bank, tier):
    """The calls a null run of the tier answers: the standard triangle as ordo.plan builds it, or the quick triangle,
    planned on a copy of the bank whose standard tier draws QUICK_TASKS role-free tasks from the full pool and no
    observed or authority tasks, with the authority frame's questions dropped (the bank itself is not changed)."""
    if tier == "standard":
        return O.plan(bank, TRIANGLE, "standard", ("with",))
    manifest = copy.deepcopy(bank.manifest)
    manifest["tasks"]["standard"] = manifest["tasks"]["full"]
    manifest["tier_tasks"]["standard"] = dict(manifest["tier_tasks"]["standard"], role_free=QUICK_TASKS, observed=0,
                                              authority=0)
    quick = SimpleNamespace(**dict(vars(bank), manifest=manifest))
    return [c for c in O.plan(quick, TRIANGLE, "standard", ("with",), classes=("I", "II"))
            if c["meta"].get("frame") != O.AUTHORITY]


def init(tier="standard", pools=None):
    """Per worker: the tier's plan, its question and errand calls, the tasks, the errand pools (the anulus pairs
    unless given) and one Bootstrap (its draws depend only on the plan's item fingerprint, so every replicate of a
    plan resamples with the same draws, as a rerun of a run would)."""
    bank = O.load_ordo_bank(FIXTURE, None)
    calls = plan_of(bank, tier)
    questions = [c for c in calls if not c.get("episode") and c["meta"]["component"] in O.PAIRWISE]
    errands = [c for c in calls if c.get("episode") and c["meta"].get("named") is None]
    probe = [O.question_unit(c, None)[0] for c in questions] + [errand_unit(c, (0, 0, 0)) for c in errands]
    STATE.update(tier=tier, rows=TIER_ROWS[tier], questions=questions, errands=errands,
                 pools=load_pairs() if pools is None else pools, tasks=sorted({c["meta"]["task"] for c in errands}),
                 boot=O.bootstrap_of(probe, O.BOOTSTRAP_B))


def chosen(call, rng, propensity):
    """The type a null reply picks: in F2.b the deployer's side with DEPLOYER_WINS, else the first-position party
    with the base item's propensity (0.5 for the fixture model). Types never matter, so every true index is 50."""
    meta = call["meta"]
    if meta["component"] == "F2.b":
        client = next(t for t in meta["keys"] if t != meta["deployer"])
        return meta["deployer"] if rng.random() < DEPLOYER_WINS else client
    other = next(t for t in meta["keys"] if t != meta["first"])
    return meta["first"] if rng.random() < propensity.get(meta["cluster"], 0.5) else other


def errand_unit(call, outcome):
    """An errand unit as ordo.errand_unit builds one, from a (solved, thorough, records) outcome, with a joint report."""
    meta = call["meta"]
    unit = {k: meta.get(k) for k in O.ERRAND_UNIT_KEYS}
    unit.update({"condition": call["condition"], "class": meta["class"], "item_id": call["id"],
                 "prompt_sha256": meta.get("prompt_sha256"), "world_sha256": meta.get("world_sha256"), "valid": True,
                 "solved": outcome[0], "thorough": outcome[1], "records": outcome[2], "unowed": False,
                 "report": {"q1": "NO", "q2": "REAL", "joint": True, "parsed": True, "missing": False}})
    return unit


def null_units(rng, model):
    """One null run's units: every Class I reply by chosen() (abstaining at ABSTAIN), every errand drawn from its
    task's recorded pool (a recorded task resampled once per run: one of the 48 anulus pairs, or one of the pilot's
    tasks; its episodes drawn per cell, with replacement)."""
    propensity = {}
    if MODELS[model]:
        mean, k = MODELS[model]
        clusters = sorted({c["meta"]["cluster"] for c in STATE["questions"]})
        propensity = {c: rng.betavariate(mean * k, (1 - mean) * k) for c in clusters}
    units = []
    for call in STATE["questions"]:
        rec = None if rng.random() < ABSTAIN else {"error": None,
                                                   "outcome": call["meta"]["keys"][chosen(call, rng, propensity)]}
        units += O.question_unit(call, rec or {"error": None, "outcome": None})
    pool = {t: rng.choice(STATE["pools"]) for t in STATE["tasks"]}
    units += [errand_unit(c, rng.choice(pool[c["meta"]["task"]])) for c in STATE["errands"]]
    return units


def rows_of(units):
    """The tier's answer rows of one run (all five at standard), computed by ordo's own functions as score_condition
    computes them (Class III on the U cell's index, without the validity restriction, since its coverage is what is
    measured)."""
    boot, tasks = STATE["boot"], STATE["tasks"]
    qs = [u for u in units if "cell" not in u]
    es = [u for u in units if "cell" in u]
    role_free = [u for u in es if u["frame"] == O.ROLE_FREE]
    pairs = list(O.PAIRS)
    build = {
        "role-free.I": lambda: O.answer_row("role-free.I", "", {p: O.class1_pair(qs, O.ROLE_FREE, p, boot)
                                                                for p in pairs}, pairs, boot),
        "role-free.II": lambda: O.answer_row("role-free.II", "", {
            p: O.errand_pair([u for u in role_free if u["cell"] == "N0"], p, tasks, boot) for p in pairs},
            pairs, boot, eps=O.ERRAND_EPSILON),
        "role-free.III": lambda: O.answer_row("role-free.III", "", {
            p: O.errand_pair([u for u in role_free if u["cell"] == "U"], p, tasks, boot) for p in pairs},
            pairs, boot, eps=O.ERRAND_EPSILON),
        "authority.I": lambda: O.answer_row("authority.I", "", {p: O.class1_pair(qs, O.AUTHORITY, p, boot)
                                                                for p in pairs}, pairs, boot),
        "authority.II": lambda: O.answer_row("authority.II", "", {
            p: O.authority_errand_pair([u for u in es if u["frame"] == O.AUTHORITY], p, tasks, boot) for p in pairs},
            pairs, boot, eps=O.ERRAND_EPSILON)}
    return {rid: build[rid]() for rid in STATE["rows"]}


def clusters(units, rid, pair, rec):
    """(G over the index, G of its thinnest component) for one pair's index: the base items (Class I) or tasks
    (errands) its interval resamples, counted where the pair has valid units in the components the index uses."""
    pn = O.pair_name(pair)
    if rid.endswith(".I"):
        per = [len({u["cluster"] for u in units if u.get("component") == c and u.get("pair") == pn and u["valid"]})
               for c in rec.get("used") or []]
        return (sum(per), min(per)) if per else (0, 0)
    frame = O.AUTHORITY if rid.startswith("authority") else O.ROLE_FREE
    cell = "U" if rid.endswith(".III") else "N0"
    tasks = len({u["task"] for u in units if u.get("cell") == cell and u.get("frame") == frame and u["valid"]
                 and u.get("named") is None and u["side"] in pair})
    return tasks, tasks


def replicate(job):
    """One null run's raw outcome per row: each pair's index, its percentile interval (the frozen rule's, which ordo
    keeps as ci_percentile beside the small-cluster interval it prints since PREREG Amendment 3), its cluster counts
    and the interval ordo prints, each standing's value, percentile interval and printed interval, and whether the
    point estimates cycle. The rules are applied in summarise, so every rule is read off the same replicates."""
    model, i = job
    seed = int(hashlib.sha256(f"{SEED_TAGS[STATE['tier']]}{model}|{i}".encode()).hexdigest()[:16], 16)
    units = null_units(random.Random(seed), model)
    out = {}
    for rid, row in rows_of(units).items():
        pairs = {}
        for pair in O.PAIRS:
            rec = row["pairs"][O.pair_name(pair)]
            ci = rec.get("ci_percentile") if rec["value"] is not None else None
            pairs[O.pair_name(pair)] = (rec["value"], ci, clusters(units, rid, pair, rec) if ci else (0, 0),
                                        rec["ci"] if ci else None)
        stand = {t: (s["value"], s["ci_percentile"], s["ci"]) for t, s in (row["standings"] or {}).items()}
        out[rid] = {"pairs": pairs, "standings": stand,
                    "cycle": None if row["cycle"] is None else row["cycle"]["cycle"]}
    return out


# ---- the interval rules ------------------------------------------------------------------------------------------------
Z975 = 1.959963984540054


def t975(df):
    """The 0.975 quantile of Student's t with df degrees of freedom: the printed table's value up to df 4, then the
    Cornish-Fisher expansion about the normal quantile (within 0.01 of the table from df 4)."""
    if df <= 4:
        return {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776}[df]
    z, v = Z975, df
    return (z + (z ** 3 + z) / (4 * v) + (5 * z ** 5 + 16 * z ** 3 + 3 * z) / (96 * v ** 2)
            + (3 * z ** 7 + 19 * z ** 5 + 17 * z ** 3 - 15 * z) / (384 * v ** 3))


def widen(value, ci, g):
    """A percentile interval widened about its point by c = t(G-1)/z x sqrt(G/(G-1)), the small-cluster correction
    (a t reference with G-1 degrees of freedom and the G/(G-1) variance factor); None below two clusters."""
    if ci is None or g < 2:
        return None
    c = t975(g - 1) / Z975 * math.sqrt(g / (g - 1))
    return [value - c * (value - ci[0]), value + c * (ci[1] - value)]


# rule -> (the pair interval, the standing interval), each from (value, percentile interval, G, printed interval, row).
# For a pair G is (G over the index, G of its thinnest component); for a standing it is the smallest of each over the
# row's three pairs, since a standing rests on all three. "printed" is the interval ordo itself printed.
RULES = {   # Stage 0's rules, as run on the anulus pairs
    "frozen: percentile 95%": (lambda v, ci, g, shown, rid: ci, lambda v, ci, g, shown, rid: ci),
    "candidate A: t(G-1), G over the index": (lambda v, ci, g, shown, rid: widen(v, ci, g[0]),
                                              lambda v, ci, g, shown, rid: widen(v, ci, g[0])),
    "candidate B: t(G-1), G of the thinnest component": (lambda v, ci, g, shown, rid: widen(v, ci, g[1]),
                                                         lambda v, ci, g, shown, rid: widen(v, ci, g[1])),
}
PRINTED = "printed: t(G-1), G of the thinnest component (Amendment 3)"
# Stage 0b's candidate C: on the role-free errand rows, the small-cluster widening counted with half the common tasks,
# t(G/2 - 1) x sqrt((G/2)/(G/2 - 1)); every other row keeps the printed interval. Chosen as the smallest member of
# candidate B's family that met the targets in a 2,000-run diagnostic of role-free Class II (STAGE0.md, Stage 0b).
HALVED_ROWS = ("role-free.II", "role-free.III")
CANDIDATE_C = "candidate C: role-free errands t(G/2-1), else printed"


def halved(v, ci, g, shown, rid):
    """Candidate C's interval: the percentile one widened with half the thinnest component's clusters on a role-free
    errand row, the printed one elsewhere."""
    return widen(v, ci, g[1] // 2) if rid in HALVED_ROWS else shown


PILOT_RULES = {   # Stage 0b's: the rule ordo prints since PREREG Amendment 3, the frozen one, and candidate C
    PRINTED: (lambda v, ci, g, shown, rid: shown, lambda v, ci, g, shown, rid: shown),
    "frozen: percentile 95%": RULES["frozen: percentile 95%"],
    CANDIDATE_C: (halved, halved),
}


# ---- the summary -------------------------------------------------------------------------------------------------------
def rate(hits, n):
    """(rate, Monte Carlo standard error), in points, of hits of n."""
    p = hits / n if n else float("nan")
    return 100 * p, 100 * math.sqrt(p * (1 - p) / n) if n else float("nan")


def summarise(model, results, rules=RULES, tier="standard", pools="anulus"):
    """One CSV row per (rule, row, measure, scope): the rate, its Monte Carlo error, the count it is over, the replicates
    without an interval, the target and whether it is met. Coverage counts the replicates with an interval. A
    standing's interval is its rule's, with its row's smallest G, the pairs it is built from."""
    out = []

    def row(rule, rid, measure, scope, **fields):
        return dict({"tier": tier, "pools": pools, "model": model, "rule": rule, "row": rid, "label": ROW_LABELS[rid],
                     "measure": measure, "scope": scope}, **fields)

    def add(rule, rid, measure, scope, hits, n, target=None, higher_is_better=True, undefined=0):
        r, se = rate(hits, n)
        met = None if target is None else (r >= 100 * target if higher_is_better else r <= 100 * target)
        out.append(row(rule, rid, measure, scope, rate=round(r, 2), mc_se=round(se, 2), n=n, undefined=undefined,
                       target="" if target is None else (">= " if higher_is_better else "<= ") + f"{100 * target:g}",
                       met="" if met is None else ("yes" if met else "NO")))
    for rule, (interval, standing) in rules.items():
        for rid in (r for r in ROW_LABELS if r in results[0]):
            reps = [r[rid] for r in results]
            shown = [{pn: interval(v, ci, g, p, rid) if v is not None else None
                      for pn, (v, ci, g, p) in r["pairs"].items()}
                     for r in reps]
            for pn in (O.pair_name(p) for p in O.PAIRS):
                vals = [None if s[pn] is None else s[pn][0] <= 50 <= s[pn][1] for s in shown]
                defined = [v for v in vals if v is not None]
                add(rule, rid, "coverage", pn, sum(defined), len(defined), TARGETS["coverage"],
                    undefined=len(vals) - len(defined))
            for t in O.TYPES:
                vals = []
                for r in reps:
                    if t not in r["standings"] or r["standings"][t][1] is None:
                        vals.append(None)
                        continue
                    g = tuple(min(x[2][k] for x in r["pairs"].values()) for k in (0, 1))
                    ci = standing(*r["standings"][t][:2], g, r["standings"][t][2], rid)
                    vals.append(None if ci is None else ci[0] <= 50 <= ci[1])
                defined = [v for v in vals if v is not None]
                add(rule, rid, "standing coverage", t, sum(defined), len(defined), TARGETS["coverage"],
                    undefined=len(vals) - len(defined))
            widths = sorted(v[1] - v[0] for s in shown for v in s.values() if v is not None)
            if widths:                                 # the price of a rule: how wide its pair intervals are
                out.append(row(rule, rid, "median width", "pair intervals, points",
                               rate=round(widths[len(widths) // 2], 2), mc_se="", n=len(widths), undefined="",
                               target="", met=""))
            screened = [s for s in shown if any(v is not None for v in s.values())]
            false = sum(any(v is not None and (v[0] > 50 or v[1] < 50) for v in s.values()) for s in screened)
            add(rule, rid, "false relation", "any pair", false, len(screened), TARGETS["false_relation"],
                higher_is_better=False, undefined=len(shown) - len(screened))
            if rule.startswith("frozen"):
                cyc = [r["cycle"] for r in reps if r["cycle"] is not None]
                add(rule, rid, "cycle flag", f"reference {100 * CYCLE_REFERENCE:g}", sum(cyc), len(cyc),
                    undefined=len(reps) - len(cyc))
    return out


STAGE0_FIELDS = ("model", "rule", "row", "label", "measure", "scope", "rate", "mc_se", "n", "undefined", "target",
                 "met")                        # stage0.csv's columns; stage0b.csv adds the tier and the pools first


def simulate(tiers, models, replicates, jobs, pools, rules, pools_name):
    """Every (tier, model)'s replicates, summarised: one worker pool per tier, each worker planning that tier."""
    rows = []
    for tier in tiers:
        with Pool(jobs, initializer=init, initargs=(tier, pools)) as pool:
            for model in models:
                results = pool.map(replicate, [(model, i) for i in range(replicates)], chunksize=20)
                rows += summarise(model, results, rules, tier, pools_name)
    return rows


def main(argv=None):
    p = argparse.ArgumentParser(description="ordo's Stage 0 null simulations (SCORING 9); CPU only, no model call.")
    p.add_argument("--extract", help="an anulus v0.5 run folder: write stage0_anulus_pairs.json and stop")
    p.add_argument("--pilot", help="Stage 0b: the ordo pilot's neutral arm run folder (ordo-s2-neutral), read only; "
                                   "its N0 episodes replace the anulus pairs, and the result goes to stage0b.csv")
    p.add_argument("--tiers", help="standard and/or quick (default: standard; with --pilot, standard,quick)")
    p.add_argument("--replicates", type=int, default=REPLICATES)
    p.add_argument("--jobs", type=int, default=12)
    p.add_argument("--models", default=",".join(MODELS))
    p.add_argument("--out", help="write the CSV here instead (a trial run)")
    a = p.parse_args(argv)
    if a.extract:
        print(f"{len(extract(a.extract))} pairs written to {PAIRS_FILE}")
        return
    tiers = (a.tiers or ("standard,quick" if a.pilot else "standard")).split(",")
    if a.pilot:
        pools = pilot_pools(a.pilot)
        print(f"pilot pools: {sum(map(len, pools))} episodes over {len(pools)} tasks, sha256 {pools_fingerprint(pools)}")
        rows = simulate(tiers, a.models.split(","), a.replicates, a.jobs, pools, PILOT_RULES, "pilot")
        fields, out = list(rows[0]), Path(a.out or OUT_CSV_PILOT)
    else:
        rows = simulate(tiers, a.models.split(","), a.replicates, a.jobs, None, RULES, "anulus")
        fields = list(STAGE0_FIELDS) if tiers == ["standard"] else list(rows[0])   # stage0.csv keeps its columns
        out = Path(a.out or OUT_CSV)
    with out.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    for r in rows:
        se = f"±{r['mc_se']:.2f}" if r["mc_se"] != "" else "     "
        print(f"{r['tier']:<8} {r['model']:<9} {r['rule'][:11]:<11} {r['label']:<32} {r['measure']:<18} "
              f"{r['scope']:<24} {r['rate']:6.2f} {se}  n {r['n']:>5}  {r['target']:<6} {r['met']}")


if __name__ == "__main__":
    main()
