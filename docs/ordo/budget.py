"""ordo's budget (DESIGN.md 8.6, revision 2.1): every count and time, derived from the plan rules (Codex R4).

Counts are never multiplied out of constants. They come from enumerating the plan:
- a single-type group runs v4's own planner (probatio.plan_v4) for each target at the selected tier, with the run's
  comparators and memory mode;
- a cross-type group enumerates ordo's items from bank/ordo/class1.jsonl and the task counts in bank/ordo/manifest.json,
  under the plan rules of SCORING.md 12 (name pairings per tier, the F2.b half, repetitions, red-flag cells, named
  targets on the measured baseline pairing only, with every target item carrying the key of its fictional twin, and
  measured in Class I only at quick).
The counts are then priced with one table (PRICING, written to budget.json), the table probatio's preflight reads.

    estimate(selection)  -> {questions, episodes, units, by_family, claude_requests, codex_requests,
                             retry_ceiling_claude, retry_ceiling_codex, sonnet_min, opus_min, codex_min}
    cross_items(...)     -> the enumerated cross-type items (each with its family and, for a named target, its twin)
    price(by_family, cells) -> minutes per profile (a question by its family, an errand by its cell)

The rates are measured where PRICING["measured"] says (the ordo pilot, 2026-11-08: docs/ordo/timing.json, which
docs/ordo/timing.py writes from the pilot's per-call records) and assumed for the other profiles, as their notes say.
The tiers are smoke (the plan called quick until 2026-11-08), quick (the 15-minute estimate: standard's role-free
rules and checks, no authority frame, the role-free errands on ordo.QUICK_TASKS of the full pool, no Class III, a named
target's Class I questions but not its errands, and for one target probatio's TIER_PARTS), standard and full.

A selection is a dict: {"tier": "smoke"|"quick"|"standard"|"full", "memory": "on"|"off"|"both", "classes": "I,II,III",
"single": [{"type", "targets", "comparators", "custom_controls"}], "cross": {"pairs": [[A, B], ...], "names": {type: 1|2},
"named": [type, ...]}}. Every key but tier is optional.

    python3 docs/ordo/budget.py           # print budget.json's content
    python3 docs/ordo/budget.py --write   # write docs/ordo/budget.json
"""
import json
import sys
from itertools import combinations
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))

TYPES = ("individual", "organization", "nation")
TIERS = ("smoke", "quick", "standard", "full")

# ---- pricing: one table, read by the preflight (budget.json "pricing") -----------------------------------------------
PRICING = {
    "jobs": 4,
    # where the sonnet profile was measured: probatio.rates_profile calls a run measured only in this context
    "measured": {"date": "2026-11-08", "cli": "Claude Code 2.1.309", "model": "claude-sonnet-5-5", "effort": "medium",
                 "jobs": 4, "host": "calibration-host", "evidence": "docs/ordo/timing.json"},
    "profiles": {
        "sonnet": "measured 2026-11-08: the ordo pilot's four runs, 1,286 calls, Claude Code 2.1.309, "
                  "claude-sonnet-5-5, effort medium, --jobs 4 (docs/ordo/timing.json)",
        "opus": "assumed: v3's claude-opus-5-5 question means; 45 s an errand, not measured",
        "codex": "assumed: v3's Codex question means; a 60 s placeholder errand with its resumed report leg"},
    "question_seconds": {   # family: sonnet (measured), opus (v3's Claude Code means), codex (v3's Codex means)
        "survey": {"sonnet": 3.91, "opus": 7.5, "codex": 5.3},
        "obey": {"sonnet": 3.65, "opus": 6.8, "codex": 4.4},
        "devotio": {"sonnet": 4.03, "opus": 9.1, "codex": 7.1},
        "salus": {"sonnet": 4.63, "opus": 9.8, "codex": 7.4}},
    "episode_seconds": {    # one errand with its report, by cell: sonnet measured, the others assumed
        "sonnet": {"N0": 24.73, "U": 26.8, "O": 24.76},
        "opus": {"N0": 45, "U": 45, "O": 45},
        "codex": {"N0": 60, "U": 60, "O": 60}},
    "codex_requests_per_episode": 2,                                # the errand, then the resumed report leg
    "retry_factor": 2,                                              # v3's one retry per failure: the request ceiling
    "warning_minutes": 45,
    "family_of": {          # every planned question's component (ordo) or plan (v4) -> its rate family
        "F1.a": "survey", "F1.b": "devotio", "F1.c": "survey", "F1.tri": "survey", "F2.a": "obey", "F2.b": "survey",
        "F2.tri": "survey", "V12": "survey", "RF1": "devotio", "RF2": "salus", "RF4": "salus",
        "pair": "survey", "conflict": "obey", "principal": "survey", "sacrifice": "devotio", "honesty": "devotio",
        "harm": "salus", "rating": "survey", "recognize": "survey"},
}
FAMILIES = tuple(PRICING["question_seconds"])
PROFILES = tuple(PRICING["episode_seconds"])
CELLS = ("N0", "U", "O")

# ---- the cross-type plan rules (SCORING.md 12) -------------------------------------------------------------------------
STANDARD_RULES = {"f1": "crossed", "f2a": "crossed", "f2b": "diagonals", "reps": 1, "tri_sets": 2, "v12": 2,
                  "rf1_reps": 3, "rf2_modes": ("explicit", "discretion"), "rf2_cells": ("S4C4",), "rf4_sets": 2,
                  "authority": True, "classes": "I,II,III", "named": "I,II"}
TIER_RULES = {          # named: the classes a named target is measured in (its questions; its counterfactual errands)
    "smoke": {"f1": "baseline", "f2a": "baseline", "f2b": "first diagonal", "reps": 1, "tri_sets": 1, "v12": 1,
              "rf1_reps": 1, "rf2_modes": ("explicit",), "rf2_cells": ("S4C4",), "rf4_sets": 1, "authority": True,
              "classes": "I,II,III", "named": "I,II"},
    "quick": dict(STANDARD_RULES, authority=False, classes="I,II", named="I"),   # role-free only; no Class III, and
    "standard": STANDARD_RULES,                                                 # a named target's questions only
    "full": {"f1": "crossed", "f2a": "crossed", "f2b": "diagonals", "reps": 2, "tri_sets": 2, "v12": 4,
             "rf1_reps": 6, "rf2_modes": ("explicit", "discretion"), "rf2_cells": ("S2C2", "S4C4"), "rf4_sets": 2,
             "authority": True, "classes": "I,II,III", "named": "I,II"},
}
MANIFEST_KEY = {"smoke": "quick"}          # bank/ordo/manifest.json is pinned and names smoke's task counts "quick"
F2B_HALF = {0: "+1", 1: "-1"}       # repetition r of F2.b runs this half (the full tier's second completes the crossing)


def ordo_bank():
    """bank/ordo's rows, name sets and task counts, read from the files (a counting tool: it does not pin)."""
    bank = ROOT / "bank" / "ordo"
    rows = [json.loads(line) for line in (bank / "class1.jsonl").read_text(encoding="utf-8").splitlines()]
    ent = json.loads((bank / "entities.json").read_text(encoding="utf-8"))
    manifest = json.loads((bank / "manifest.json").read_text(encoding="utf-8"))
    return rows, ent["name_sets"], manifest["tier_tasks"]


def names_of(name_sets, t, n):
    """The invented names of type t a run uses: set 1's, and set 2's when the run has two controls for t. With one
    control (a user's file with one entry), that one name stands in both name sets (SCORING.md 12)."""
    return [name_sets["1"][t]] if n == 1 else [name_sets["1"][t], name_sets["2"][t]]


def crossed(a_names, b_names):
    return [(x, y) for x in a_names for y in b_names]


def diagonals(a_names, b_names):
    """The set-consistent pairings (set 1 with set 1, set 2 with set 2), without repeats when a type has one name."""
    out = []
    for i in range(2):
        p = (a_names[i % len(a_names)], b_names[i % len(b_names)])
        if p not in out:
            out.append(p)
    return out


def pairings(rule, a_names, b_names):
    """The name pairings a component runs for a pair at a tier: the baseline pairing (set 1 with set 1), the first
    diagonal (the same), every crossing, or the diagonals."""
    if rule in ("baseline", "first diagonal"):
        return [(a_names[0], b_names[0])]
    return crossed(a_names, b_names) if rule == "crossed" else diagonals(a_names, b_names)


def q(component, key, twin=None):
    return {"kind": "question", "component": component, "family": PRICING["family_of"][component], "key": key,
            "twin": twin}


def cross_items(tier, pairs, names=None, named=(), classes="I,II,III", bank=None):
    """Every item of the cross-type groups of one selection, as dicts with kind (question or episode), component,
    family, key and, for a named target's question, twin (the key of the baseline question it is compared with).
    pairs: (type, type) tuples in any order; names: {type: 1 or 2 controls}; named: the named targets' types."""
    rows, name_sets, tier_tasks = bank or ordo_bank()
    rule = TIER_RULES[tier]
    wanted = set(classes.split(",")) & set(rule["classes"].split(","))
    measured = set(rule["named"].split(","))            # the classes the named targets are measured in at the tier
    pairs = [tuple(sorted(p, key=TYPES.index)) for p in pairs]
    types = [t for t in TYPES if any(t in p for p in pairs)]
    n = {t: (names or {}).get(t, 2) for t in TYPES}
    nm = {t: names_of(name_sets, t, n[t]) for t in TYPES}
    by = {}
    for r in rows:
        by.setdefault(r["component"], []).append(r)
    items = []
    if "I" in wanted:
        for a, b in pairs:
            pair = f"{a}:{b}"
            # role-free Class I: every row of the pair in both orders, on the tier's pairings, per repetition
            for comp in ("F1.a", "F1.b", "F1.c"):
                for row in (r for r in by[comp] if r["pair"] == pair):
                    for x, y in pairings(rule["f1"], nm[a], nm[b]):
                        for rep in range(rule["reps"]):
                            items.append(q(comp, ("F1", row["template_id"], (a, x), (b, y), rep)))
            if not rule["authority"]:
                continue
            # F2.a: pairing k uses word pair k mod 4, both name orders and both assignments
            words = sorted({r["words"] for r in by["F2.a"]}, key=["greet", "colour", "compass", "fruit"].index)
            for k, (x, y) in enumerate(pairings(rule["f2a"], nm[a], nm[b])):
                for row in (r for r in by["F2.a"] if r["pair"] == pair and r["words"] == words[k % len(words)]):
                    for rep in range(rule["reps"]):
                        items.append(q("F2.a", ("F2.a", row["template_id"], (a, x), (b, y), rep)))
            # F2.b: the tier's diagonals, repetition r on half F2B_HALF[r]
            for x, y in pairings(rule["f2b"], nm[a], nm[b]):
                for rep in range(rule["reps"]):
                    for row in (r for r in by["F2.b"] if r["pair"] == pair and r["design"]["half"] == F2B_HALF[rep]):
                        items.append(q("F2.b", ("F2.b", row["template_id"], (a, x), (b, y), rep)))
        if len(pairs) == 3:
            sets = []
            for s in range(rule["tri_sets"]):
                trio = tuple((t, nm[t][s % len(nm[t])]) for t in TYPES)
                if trio not in sets:
                    sets.append(trio)
            for comp in ("F1.tri", "F2.tri") if rule["authority"] else ("F1.tri",):
                for row in by[comp]:
                    for trio in sets:
                        for rep in range(rule["reps"]):
                            items.append(q(comp, (comp, row["template_id"], trio, rep)))
        for t in types:
            for name in nm[t]:
                for rep in range(rule["v12"]):
                    items.append(q("V12", ("V12", f"V12.recognize.{t}", name, rep)))
            for row in (r for r in by["RF1"] if r["pair"] == t):
                for rep in range(rule["rf1_reps"]):
                    items.append(q("RF1", ("RF1", row["template_id"], nm[t][0], rep)))
            for row in (r for r in by["RF2"] if r["pair"] == t and r["mode"] in rule["rf2_modes"]
                        and r["cell"] in rule["rf2_cells"]):
                items.append(q("RF2", ("RF2", row["template_id"], nm[t][0], 0)))
            for row in (r for r in by["RF4"] if r["pair"] == t):
                for name in nm[t][:rule["rf4_sets"]]:
                    for rep in range(rule["reps"]):
                        items.append(q("RF4", ("RF4", row["template_id"], name, rep)))
        if types:   # RF1's anonymous twin, once, on the first selected type's rows
            for row in (r for r in by["RF1"] if r["pair"] == types[0]):
                for rep in range(rule["rf1_reps"]):
                    items.append(q("RF1", ("RF1", row["template_id"], "anon", rep)))
        # named targets: the baseline role-free items whose slot of the target's type holds that type's set-1 name,
        # at the tier's measured pairings, with the target in its place; the twin is that baseline item's key (the
        # baseline list is taken once, so a second target's items never start from the first target's)
        base_items = [i for i in items if i["key"][0] == "F1"]
        baseline = {i["key"] for i in base_items}
        for t in named if "I" in measured else ():
            for item in base_items:
                _, tid, (ta, xa), (tb, xb), rep = item["key"]
                if t not in (ta, tb) or (xa if ta == t else xb) != nm[t][0]:
                    continue
                me = (ta, f"@{t}") if ta == t else (tb, f"@{t}")
                key = ("F1", tid, me if ta == t else (ta, xa), me if tb == t else (tb, xb), rep)
                assert item["key"] in baseline
                items.append(q(item["component"], key, twin=item["key"]))
    tt = quick_tasks() if tier == "quick" else tier_tasks[MANIFEST_KEY.get(tier, tier)]
    k, cells = len(types), sum(1 + sum(d in p for p in pairs) for d in types)    # authority: own errand + partners'
    episodes = []
    if "II" in wanted:
        episodes += [("N0", "secretariat", b, s) for s in range(tt["role_free"]) for b in types]
        episodes += [("N0", "authority", c, s) for s in range(tt["authority"]) for c in range(cells)]
        episodes += [("N0", f"@{t}", b, s) for t in (named if "II" in measured else ())
                     for s in range(tt["role_free"]) for b in types]
    if "III" in wanted:
        episodes += [("U", "secretariat", b, s) for s in range(tt["role_free"]) for b in types]
        episodes += [("O", "secretariat", b, s) for s in range(tt["observed"]) for b in types]
    items += [{"kind": "episode", "component": e[0], "family": "episode", "key": e, "twin": None} for e in episodes]
    return items


# ---- single-type subplans: v4's own planner ------------------------------------------------------------------------
_V4 = {}


def v4_bank():
    if "bank" not in _V4:
        import probatio as P
        _V4["P"], _V4["bank"] = P, P.load_bank(ROOT / "bank")
    return _V4["P"], _V4["bank"]


def quick_tasks():
    """The quick tier's task counts, which the pinned ordo manifest predates: ordo.QUICK_TASKS, their one home."""
    sys.path.insert(0, str(ROOT))
    import ordo
    return ordo.QUICK_TASKS


def single_counts(type_, tier, comparators=5, classes="I,II,III"):
    """(questions by family, episodes by cell) of one single-type target at the tier, from probatio.plan_v4 with a
    roster of the given number of comparators (the baseline's, then synthetic stand-ins; only the count matters to the
    plan). plan_v4 itself asks quick's parts (TIER_PARTS) and drops the classes a tier does not plan."""
    P, bank = v4_bank()
    roster = P.resolve_target(bank, type_, bank.entities[type_]["calibration_targets"][0]["full"]
                              if bank.entities[type_].get("calibration_targets") else "Ada Lovelace")
    base = roster["K"]
    roster = dict(roster, K=(base + [P.entity(type_, f"Stand In {i}", key=f"standin{i}") for i in range(8)])
                  [:comparators])
    calls = P.plan_v4(bank, roster, tier, tuple(classes.split(",")), ("with",))
    by, cells = {f: 0 for f in FAMILIES}, {c: 0 for c in CELLS}
    for c in calls:
        if c.get("episode"):
            cells[c["meta"]["cell"]] += 1
        else:
            by[PRICING["family_of"][c["meta"]["plan"]]] += 1
    return by, cells


# ---- totals ---------------------------------------------------------------------------------------------------------
def price(by_family, cells):
    """Minutes per profile for counts of questions by family and errands by cell, at PRICING's jobs: a question at
    its family's rate, an errand at its cell's."""
    s, ep, jobs = PRICING["question_seconds"], PRICING["episode_seconds"], PRICING["jobs"]
    return {f"{p}_min": (sum(by_family[f] * s[f][p] for f in FAMILIES) + sum(cells[c] * ep[p][c] for c in CELLS))
            / jobs / 60 for p in PROFILES}


def estimate(selection, bank=None):
    """The counts, requests, ceilings and minutes of one selection (the module docstring's shape)."""
    tier, classes = selection["tier"], selection.get("classes", "I,II,III")
    conditions = 2 if selection.get("memory", "on") == "both" else 1
    by, cells = {f: 0 for f in FAMILIES}, {c: 0 for c in CELLS}
    for group in selection.get("single", []):
        f, e = single_counts(group["type"], tier, group.get("comparators", 5), classes)
        for _ in range(group.get("targets", 1)):
            for fam in FAMILIES:
                by[fam] += f[fam]
            for c in CELLS:
                cells[c] += e[c]
        if "I" in classes.split(","):
            by["survey"] += 2 * group.get("custom_controls", 0)        # V12 for each custom control (11.4)
    cross = selection.get("cross")
    if cross and cross.get("pairs"):
        for item in cross_items(tier, cross["pairs"], cross.get("names"), cross.get("named", ()), classes, bank):
            if item["kind"] == "episode":
                cells[item["key"][0]] += 1
            else:
                by[item["family"]] += 1
    by = {f: v * conditions for f, v in by.items()}
    cells = {c: v * conditions for c, v in cells.items()}
    episodes = sum(cells.values())
    questions = sum(by.values())
    out = {"questions": questions, "episodes": episodes, "units": questions + episodes, "by_family": by,
           "claude_requests": questions + episodes,
           "codex_requests": questions + PRICING["codex_requests_per_episode"] * episodes}
    out["retry_ceiling_claude"] = PRICING["retry_factor"] * out["claude_requests"]
    out["retry_ceiling_codex"] = PRICING["retry_factor"] * out["codex_requests"]
    out.update(price(by, cells))
    out["warn"] = out["sonnet_min"] > PRICING["warning_minutes"]
    return out


# ---- the published cases --------------------------------------------------------------------------------------------
TRIANGLE = [list(p) for p in combinations(TYPES, 2)]
ONE_PAIR = [["individual", "organization"]]
TWO_PAIRS = [["individual", "organization"], ["organization", "nation"]]


def cases():
    """Every selection budget.json reports: the design's tables, plus the check cases Codex asked for (quick and full
    single-type, mixed single and cross-type at every tier, custom controls and comparators, one control per type,
    memory both)."""
    single = lambda t="organization", **kw: [dict(type=t, targets=1, **kw)]       # noqa: E731
    out = {}
    for tier in TIERS:
        out[f"{tier}: individual:organization:nation"] = {"tier": tier, "cross": {"pairs": TRIANGLE}}
        out[f"{tier}: organization (v4 single-type, one target)"] = {"tier": tier, "single": single()}
        out[f"{tier}: nation,individual:organization:nation"] = {"tier": tier, "single": single("nation"),
                                                                 "cross": {"pairs": TRIANGLE}}
        out[f"{tier}: individual:organization:nation + named organization"] = {
            "tier": tier, "cross": {"pairs": TRIANGLE, "named": ["organization"]}}
    out["standard: individual:organization"] = {"tier": "standard", "cross": {"pairs": ONE_PAIR}}
    for tier in ("quick", "standard"):
        out[f"{tier}: individual:organization + named organization"] = {
            "tier": tier, "cross": {"pairs": ONE_PAIR, "named": ["organization"]}}
        out[f"{tier}: individual:organization:nation + a named target of each type"] = {
            "tier": tier, "cross": {"pairs": TRIANGLE, "named": list(TYPES)}}
    out["standard: individual:organization,organization:nation"] = {"tier": "standard", "cross": {"pairs": TWO_PAIRS}}
    out["standard: organization, 3 comparators and 1 custom control"] = {
        "tier": "standard", "single": single(comparators=3, custom_controls=1)}
    out["standard: organization, 8 comparators, 2 targets"] = {
        "tier": "standard", "single": [dict(type="organization", targets=2, comparators=8)]}
    out["standard: individual:organization:nation, one control per type"] = {
        "tier": "standard", "cross": {"pairs": TRIANGLE, "names": {t: 1 for t in TYPES}}}
    out["quick: individual:organization:nation, one control for organization, named organization"] = {
        "tier": "quick", "cross": {"pairs": TRIANGLE, "names": {"organization": 1}, "named": ["organization"]}}
    out["standard: individual:organization:nation, memory both"] = {
        "tier": "standard", "memory": "both", "cross": {"pairs": TRIANGLE}}
    out["standard: individual:organization:nation, --class I"] = {
        "tier": "standard", "classes": "I", "cross": {"pairs": TRIANGLE}}
    return out


def pilot():
    """The pilot (PREREG.md 4): Stage 1 the smoke triangle (the plan called quick when the pilot was frozen; Amendment
    5) under the neutral file plus 3 canary calls; Stage 2 the standard triangle under the neutral file, and each policy
    arm's private plan (role-free Class I with F1.tri, and the role-free N0 errands; no V12, no red flags)."""
    smoke = estimate({"tier": "smoke", "cross": {"pairs": TRIANGLE}})
    neutral = estimate({"tier": "standard", "cross": {"pairs": TRIANGLE}})
    arm_items = [i for i in cross_items("standard", TRIANGLE)
                 if (i["kind"] == "question" and i["component"] in ("F1.a", "F1.b", "F1.c", "F1.tri"))
                 or (i["kind"] == "episode" and i["key"][:2] == ("N0", "secretariat"))]
    by = {f: sum(1 for i in arm_items if i["family"] == f) for f in FAMILIES}
    arm_cells = {c: sum(1 for i in arm_items if i["kind"] == "episode" and i["key"][0] == c) for c in CELLS}
    arm_units = sum(by.values()) + sum(arm_cells.values())
    canary = 3
    units = smoke["units"] + canary + neutral["units"] + 2 * arm_units
    sonnet = (smoke["sonnet_min"] + neutral["sonnet_min"] + 2 * price(by, arm_cells)["sonnet_min"]
              + canary * PRICING["question_seconds"]["survey"]["sonnet"] / PRICING["jobs"] / 60)
    return {"smoke_units": smoke["units"], "canary_calls": canary, "neutral_units": neutral["units"],
            "arm_units": arm_units, "units": units, "claude_requests": units, "sonnet_min": sonnet}


def warning_example():
    """The 45-minute warning's worked example: the standard triangle with a named organization under --memory both
    (about 67 measured minutes; the single-condition selection, about 33, no longer passes the warning since the rates
    were measured), and what each of the hint's savings takes off it."""
    sel = lambda **kw: dict({"tier": "standard", "memory": "both",                     # noqa: E731
                             "cross": {"pairs": TRIANGLE, "named": ["organization"]}}, **kw)
    named = estimate(sel())
    base = estimate(sel(cross={"pairs": TRIANGLE}))
    nq = estimate(sel(classes="I"))
    qk = estimate(sel(tier="quick"))
    return {"total": named["sonnet_min"], "drop_named": named["sonnet_min"] - base["sonnet_min"],
            "class_I_only_saves": named["sonnet_min"] - nq["sonnet_min"],
            "tier_quick_saves": named["sonnet_min"] - qk["sonnet_min"]}


def report():
    est = {name: estimate(sel) for name, sel in cases().items()}
    named = estimate({"tier": "standard", "cross": {"pairs": TRIANGLE, "named": ["organization"]}})
    base = est["standard: individual:organization:nation"]
    return {
        "pricing": PRICING,
        "tiers": {t: est[f"{t}: individual:organization:nation"] for t in TIERS},
        "cases": est,
        "named_target_triangle_standard": {"questions": named["questions"] - base["questions"],
                                           "episodes": named["episodes"] - base["episodes"]},
        "warning_example": warning_example(),
        "pilot": pilot(),
    }


def rounded(obj):
    """obj with every float rounded to 3 decimals, so budget.json reads cleanly and rebuilds byte for byte."""
    if isinstance(obj, float):
        return round(obj, 3)
    if isinstance(obj, dict):
        return {k: rounded(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [rounded(v) for v in obj]
    return obj


if __name__ == "__main__":
    text = json.dumps(rounded(report()), indent=1) + "\n"
    if "--write" in sys.argv[1:]:
        (HERE / "budget.json").write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
