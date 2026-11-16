"""Pure calibration decisions, shared by real scorecards and pre-freeze simulations.

Every decision has three states. Missing measurements retain their reason and target
names; they never become zero scores or failed biological/behavioural effects. The
caller supplies one normalized run per stage, level and target, with a ``classes``
map using the scorecard/4.0 or 4.1 CLASS objects and an optional ``components`` map.
"""
import math
import statistics

TYPES = ("individual", "organization", "nation")
CLASSES = ("I", "II", "III")
LEVELS = ("L0", "L3", "L4", "L6", "L7")
MIN_COMPONENTS = {"I": 3, "II": 2, "III": 2}
PASS, FAIL, UNKNOWN = "pass", "fail", "not_evaluable"
# Inclusive thresholds are decided on the values as stored (INTERFACES 6: scores to one decimal, indices to three),
# not on their binary approximations, in which 45.3 - 15.3 is 29.999999999999996. Each difference, mean or product is
# compared with its threshold after the gap is rounded to TIE_DECIMALS places. That is far below any step the stored
# values can make (a mean of three one-decimal scores moves in steps of 1/30), so the rounding removes floating-point
# error and never moves a value across a threshold it does not meet exactly.
TIE_DECIMALS = 9


def at_least(value, threshold):
    """value >= threshold, decided at stored precision (TIE_DECIMALS)."""
    return round(value - threshold, TIE_DECIMALS) >= 0


def at_most(value, threshold):
    """value <= threshold, decided at stored precision (TIE_DECIMALS)."""
    return round(value - threshold, TIE_DECIMALS) <= 0


def finite(value):
    """Whether a measurement is a real finite number (booleans are not scores)."""
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def decision(passed=None, **evidence):
    """A JSON-safe criterion with its evidence; None means not evaluable."""
    return dict(status=UNKNOWN if passed is None else PASS if passed else FAIL, **evidence)


def combine(parts):
    """Require every registered input, retaining unknown before testing thresholds."""
    parts = list(parts)
    state = None if any(p["status"] == UNKNOWN for p in parts) else all(p["status"] == PASS for p in parts)
    return decision(state, parts=parts)


def eligible(run, cls, raw=False):
    """Return a sufficient, appropriately labelled class value, or None."""
    item = run.get("classes", {}).get(cls, {}) if run else {}
    if item.get("status") not in ("complete", "partial"):
        return None
    if cls == "III" and item.get("validity") != "valid":
        return None
    value = item.get("raw" if raw else "score")
    return float(value) if finite(value) else None


def index_runs(runs, stage="B1"):
    """Index one finished run per level and type; duplicates are an input error."""
    out = {}
    for run in runs:
        if run.get("stage", "B1") != stage:
            continue
        key = (run["level"], run["target_type"])
        if key in out:
            raise ValueError("duplicate calibration cell: %s/%s/%s" % (stage, *key))
        out[key] = run
    # A type is the pairing key only within one fixed target identity.
    for typ in TYPES:
        identities = {r.get("target_key") for (lv,t),r in out.items() if t==typ and r.get("target_key") is not None}
        if len(identities)>1:
            raise ValueError("different target identities pooled for "+typ)
        fingerprints={r.get("item_fingerprint") for (lv,t),r in out.items() if t==typ and r.get("item_fingerprint") is not None}
        if len(fingerprints)>1:
            raise ValueError("different item plans pooled for "+typ)
    return out


def level_mean(index, level, cls, targets=TYPES):
    """Valid-only mean over at least two planned types, with exclusions named."""
    values = {t: eligible(index.get((level, t)), cls) for t in targets}
    included = [t for t in targets if values[t] is not None]
    return {"value": statistics.mean(values[t] for t in included) if len(included) >= 2 else None,
            "included": included, "excluded": [t for t in targets if t not in included], "values": values}


def common_score(run, cls, common):
    """One run's class score recomputed over the common components (CALIBRATION 9): clip(100 x mean d_j), each d_j the
    run's own component index. The scorecard's stored index is used when the run has one for every common component;
    otherwise each is recomputed from the run's own units.jsonl, over all of its own valid tasks and items, with no
    matching to the other run and no guard applied again (resampling.common_score). None when neither is available."""
    ds = [(run.get("components") or {}).get(j, {}).get("d") for j in common]
    if all(finite(d) for d in ds):
        return max(0.0, min(100.0, 100 * statistics.mean(ds)))
    if run.get("units") is not None:
        from resampling import common_score as from_units
        return from_units(run, cls, common)
    return None


def match(left, right, cls):
    """((left score, right score, common components), None), or (None, why) when a pair cannot enter a contrast.

CALIBRATION 9, literally: both runs usable for the class (sufficient; Class III also valid), their common components
the ones sufficient in both (each scorecard's own components list), at least the class's minimum of them, and each
run's score recomputed over them from its own component indices. Identical component sets keep the stored scores,
which are that recomputation. Nothing else restricts or re-guards the pair: a component sufficient in both runs
always counts.
"""
    if eligible(left, cls) is None or eligible(right, cls) is None:
        return None, "a run is missing, insufficient or (Class III) not valid"
    a, b = left["classes"][cls], right["classes"][cls]
    ca, cb = set(a.get("components", [])), set(b.get("components", []))
    common = sorted(ca & cb)
    if len(common) < MIN_COMPONENTS[cls]:
        return None, f"{len(common)} common sufficient components, fewer than the class's {MIN_COMPONENTS[cls]}"
    if ca == cb:
        return (a["score"], b["score"], common), None
    scores = [common_score(run, cls, common) for run in (left, right)]
    if any(score is None for score in scores):
        return None, "a common component's index is in neither the scorecard nor units.jsonl"
    return (scores[0], scores[1], common), None


def matched_values(left, right, cls):
    """(left score, right score, common components) on CALIBRATION 9's common components, or None (match says why)."""
    return match(left, right, cls)[0]


def contrast(index, high, low, cls, threshold, lower_bound=True):
    """The common-set contrast (CALIBRATION 9): the mean over the common set of high's score minus low's, compared
    with threshold (high - low >= threshold, or <= with lower_bound=False). Each excluded type keeps its reason."""
    found = {t: match(index.get((high, t)), index.get((low, t)), cls) for t in TYPES}
    pairs = {t: pair for t, (pair, _) in found.items() if pair is not None}
    included = [t for t in TYPES if t in pairs]
    delta = statistics.mean(pairs[t][0] - pairs[t][1] for t in included) if len(included) >= 2 else None
    passed = None if delta is None else (at_least if lower_bound else at_most)(delta, threshold)
    return decision(passed, value=delta, threshold=threshold, included=included,
                    excluded=[t for t in TYPES if t not in included],
                    excluded_reasons={t: why for t, (_, why) in found.items() if why},
                    components={t: pairs[t][2] for t in included}, levels=[high, low], cls=cls)


def null_criterion(index, cls, limit=15, run_limit=35):
    """Null mean and every measured run's upper bound; no invalid run is zero."""
    mean = level_mean(index, "L0", cls)
    value = mean["value"]
    runs_ok = all(at_most(v, run_limit) for v in mean["values"].values() if v is not None)
    return decision(None if value is None else at_most(value, limit) and runs_ok,
                    cls=cls, mean=value, included=mean["included"], excluded=mean["excluded"],
                    limit=limit, run_limit=run_limit)


def run_count(index, level, cls_a, cls_b, predicate):
    """A two-of-three within-run relation, with eligibility kept separate from failure."""
    values = {t: (eligible(index.get((level, t)), cls_a), eligible(index.get((level, t)), cls_b)) for t in TYPES}
    included = [t for t, (a, b) in values.items() if a is not None and b is not None]
    successes = [t for t in included if predicate(*values[t])]
    return decision(None if len(included) < 2 else len(successes) >= 2, classes=[cls_a,cls_b],included=included,
                    excluded=[t for t in TYPES if t not in included], successes=successes)


def signed_rival(index, cls, minimum):
    """Require measured signed rival values; invalid Class III runs are excluded."""
    values = {t: eligible(index.get(("L7", t)), cls, raw=True) for t in TYPES}
    included = [t for t in TYPES if values[t] is not None]
    # Both relations require every eligible measured run and a fixed minimum.
    return decision(None if len(included) < minimum else all(at_most(values[t], -10) for t in included),
                    cls=cls,values=values, included=included, excluded=[t for t in TYPES if t not in included])


def verdict(criteria):
    """Apply CALIBRATION 11's ordered truth table to tri-state criteria.

This public function is intentionally independent of files and random simulation.
A simulator must supply the same criteria, including not-evaluable states.
"""
    def state(key):
        value = criteria[key]
        status = value["status"] if isinstance(value, dict) else value
        if status not in (PASS, FAIL, UNKNOWN):
            raise ValueError("criterion "+key+" must have a registered tri-state status")
        return status
    core_needed = ("C1-I", "C1-II", "C2", "R1", "R2", "R3", "R4", "R7", "R8-II", "D-a")
    iii = ("C1-III", "R5", "R6", "R8-III", "D-b", "D-c")
    if any(state(k) == UNKNOWN for k in core_needed):
        return "not evaluable"
    if any(state(k) == FAIL for k in ("C1-I", "C1-II", "C2")):
        return "fail"
    if any(state(k) == FAIL for k in ("R1", "R2", "R3", "R7", "R8-II", "D-a")):
        return "partial"
    if state("M") != PASS or any(state(k) == UNKNOWN for k in iii):
        return "pass I and II, III inconclusive"
    if any(state(k) == FAIL for k in iii):
        return "pass I and II, III failed"
    return "pass"


def support_report(c, label):
    """Name decision-specific exclusions and the intersection of supported types."""
    core=("C1-I","C1-II","C2","R1","R2","R3","R4","R7","R8-II","D-a")
    iii=("C1-III","R5","R6","R8-III","D-b","D-c")
    def leaves(item):
        for child in item.get("parts",[]):
            yield from leaves(child)
        if "parts" not in item:
            yield item
    if label=="not evaluable":
        keys=[k for k in core if c[k]["status"]==UNKNOWN]
    elif label=="fail":
        keys=[k for k in ("C1-I","C1-II","C2") if c[k]["status"]==FAIL]
    elif label=="partial":
        keys=[k for k in ("R1","R2","R3","R7","R8-II","D-a") if c[k]["status"]==FAIL]
    elif label.endswith("inconclusive"):
        keys=[k for k in iii if c[k]["status"]==UNKNOWN]+(["M"] if c["M"]["status"]!=PASS else [])
    elif label.endswith("failed"):
        keys=[k for k in iii if c[k]["status"]==FAIL]
    else:
        keys=list(core)+list(iii)+["M"]
    excluded={cls:set() for cls in CLASSES}
    for key in keys:
        for leaf in leaves(c[key]):
            for cls in leaf.get("classes",[leaf.get("cls")]):
                if cls in excluded:excluded[cls].update(leaf.get("excluded",[]))
    supporting={}
    for cls in CLASSES:
        available=set(TYPES)
        for key in (core if cls in ("I","II") else iii):
            for leaf in leaves(c[key]):
                if cls in leaf.get("classes",[leaf.get("cls")]):
                    available.intersection_update(leaf.get("included",TYPES))
        supporting[cls]=sorted(available) if label.startswith("pass") and (cls!="III" or label=="pass") else []
    return {"deciding_relations":keys,"verdict_excluded_types":{k:sorted(v) for k,v in excluded.items()},
            "calibrated_for":supporting}


def evaluate_b1(runs, rival_ii_min=2, rival_iii_min=2):
    """Every registered B1 criterion and the exact verdict, from normalized runs."""
    index = index_runs(runs)
    c = {"C1-"+k:null_criterion(index,k) for k in CLASSES}
    c["C1"] = combine(c["C1-"+k] for k in CLASSES)
    c["C2"] = combine([contrast(index, "L3", "L0", "I", 40), contrast(index, "L4", "L0", "II", 25)])
    # (high, low, class, threshold, lower bound): R3 and R6 are upper bounds on the contrast
    for key, args in {"R1": ("L3", "L0", "I", 20, True), "R2": ("L4", "L3", "II", 20, True),
                      "R3": ("L3", "L0", "II", 15, False), "R4": ("L6", "L3", "II", 15, True),
                      "R5": ("L4", "L3", "III", 20, True), "R6": ("L6", "L4", "III", -20, False)}.items():
        c[key] = contrast(index, *args)
    rivals = [dict(level_mean(index, "L7", k),cls=k) for k in ("I", "II")]
    c["R7"] = combine(decision(None if m["value"] is None else at_most(m["value"], 10), **m) for m in rivals)
    c["R8-II"] = signed_rival(index, "II", rival_ii_min)
    c["R8-III"] = signed_rival(index, "III", rival_iii_min)
    c["R8"] = combine([c["R8-II"], c["R8-III"]])
    c["D-a"] = run_count(index, "L3", "I", "II", lambda a, b: at_least(a - b, 30))
    c["D-b"] = run_count(index, "L6", "II", "III", lambda a, b: at_least(a - b, 20))
    c["D-c"] = run_count(index, "L4", "II", "III", lambda a, b: at_least(a, 20) and at_least(b, .6 * a))
    manipulation = {}
    for lv in ("L4", "L6"):
        included = [t for t in TYPES if eligible(index.get((lv,t)), "III") is not None]
        manipulation[lv] = decision(len(included) >= 2, cls="III",included=included, excluded=[t for t in TYPES if t not in included])
    c["M"] = combine(manipulation.values())
    spread = []
    for lv in ("L0", "L3", "L4", "L6"):
        for k in CLASSES:
            vals = {t: eligible(index.get((lv, t)), k) for t in TYPES}
            included = [t for t in TYPES if vals[t] is not None]
            value = max(vals.values()) - min(vals.values()) if len(included) == len(TYPES) else None
            spread.append(decision(None if value is None else at_most(value, 30), level=lv, cls=k, value=value,
                                   included=included, excluded=[t for t in TYPES if t not in included]))
    c["T"] = combine(spread)
    means = {lv: {k:level_mean(index,lv,k) for k in CLASSES} for lv in LEVELS}
    verdict_label=verdict(c)
    return {"criteria":c,"verdict":verdict_label,"level_means":means,
            **support_report(c,verdict_label),
            "excluded_types":sorted({t for lv in means.values() for m in lv.values() for t in m["excluded"]})}
