"""Joint item/task bootstrap for calibration level means and matched contrasts.

Runs are normalized dictionaries with level, target_type, classes, components,
and units (INTERFACES.md 7). The caller selects an arm and one condition per run.
The point estimate's eligible targets and components remain fixed in every draw.
An unsupported draw invalidates its interval, rather than silently changing its
estimand. No model, file, or plotting operations occur in this module.

A contrast follows CALIBRATION 9 literally: each run's score is recomputed over
the components sufficient in both runs, from that run's own units (all of its
own valid tasks and items). Tasks are never matched across the two runs and no
guard is applied again, so a component sufficient in both always counts.
"""

import math
import random
from collections import defaultdict

try:
    from .stats import percentile
except ImportError:
    from stats import percentile


CLASSES = ("I", "II", "III")
CONTRASTS = (("L3", "L0"), ("L4", "L0"), ("L4", "L3"),
             ("L6", "L3"), ("L6", "L4"))
FIELDS = {"a": "solved", "b": "thorough", "c": "records"}


def _number(value):
    """Validate observed outcomes without imputing missing or invalid values."""
    return (isinstance(value, (float, int)) and not isinstance(value, bool)
            and math.isfinite(value))


def _mean(values):
    """Compute a mean only for a nonempty, wholly finite observation set."""
    return math.fsum(values) / len(values) if values and all(_number(x) for x in values) else None


def _ids(cls):
    """Return the registered component identifiers of a class."""
    return ["I." + str(i) for i in range(1, 5)] if cls == "I" else [cls + "." + x for x in FIELDS]


def _selected(cls, components):
    """Normalize fixed component sets and retain metadata for planned counts."""
    if cls not in CLASSES:
        raise ValueError("class must be I, II or III")
    if components is None:
        return _ids(cls), {}, False
    metadata = components if isinstance(components, dict) else {}
    chosen = []
    for key in components:
        if key in _ids(cls):
            info = metadata.get(key, {})
            if info.get("sufficient", True):
                chosen.append(key)
    return chosen, metadata, True


def _index(t, c, cls):
    """Use S for choices and R for errands, with the registered exact tie zero."""
    if t == c:
        return 0.0
    denominator = 1 - min(t, c) if cls == "I" else max(t, c)
    return (t - c) / denominator if denominator > 0 else None


def _choice_component(rows, component, metadata, frozen):
    """Aggregate every order/repetition, giving sacrifice prices equal weight."""
    planned = {side: [r for r in rows if r.get("component") == component and r.get("side") == side]
               for side in ("target", "control")}
    valid = {side: [r for r in items if r.get("valid") is True and r.get("y") in (0, 1)]
             for side, items in planned.items()}
    if not frozen:
        for side in planned:
            count = metadata.get("planned_t" if side == "target" else "planned_c", len(planned[side]))
            if len(valid[side]) < max(8, math.ceil(2 * count / 3)):
                return None, "too few valid replies"
        outcomes = [r["y"] for items in valid.values() for r in items]
        if sum(outcomes) < 5 or len(outcomes) - sum(outcomes) < 5:
            return None, "pooled choice outcomes lack five of each outcome"
    if component == "I.4":
        strata = {r.get("stratum") for items in planned.values() for r in items}
        if len(strata) != 4 or None in strata:
            return None, "sacrifice requires four price strata"
        rates = {}
        for side in planned:
            by_price = [_mean([r["y"] for r in valid[side] if r.get("stratum") == price])
                        for price in strata]
            if any(rate is None for rate in by_price):
                return None, "a price has no valid reply"
            rates[side] = _mean(by_price)
    else:
        rates = {side: _mean([r["y"] for r in items]) for side, items in valid.items()}
    if any(value is None for value in rates.values()):
        return None, "a side has no valid reply"
    return _index(rates["target"], rates["control"], "I"), None


def _task_pairs(rows, cls):
    """Keep only complete valid beneficiary pairs in the class's scored cell."""
    cell = "N0" if cls == "II" else "U"
    by_task = defaultdict(dict)
    duplicate = False
    for row in rows:
        if row.get("cell") != cell or row.get("side") not in ("target", "control"):
            continue
        task, side = row.get("task"), row["side"]
        if side in by_task[task]:
            duplicate = True
        by_task[task][side] = row
    pairs = {task: sides for task, sides in by_task.items()
             if len(sides) == 2 and all(r.get("valid") is True for r in sides.values())}
    return by_task, pairs, duplicate


def _errand_component(rows, cls, component, metadata, frozen):
    """Compute an errand component on valid pairs, retaining the planned guard."""
    all_tasks, pairs, duplicate = _task_pairs(rows, cls)
    if duplicate:
        return None, "duplicate task/side (select one condition per run)"
    if not frozen:
        planned = max(metadata.get("planned_t", len(all_tasks)), metadata.get("planned_c", len(all_tasks)))
        if len(pairs) < max(6, math.ceil(2 * planned / 3)):
            return None, "too few scored errands"
    field = FIELDS[component.rsplit(".", 1)[1]]
    values = {side: [pair[side].get(field) for pair in pairs.values()] for side in ("target", "control")}
    if not pairs or any(not _number(x) or x < 0 or (field != "records" and x not in (0, 1))
                        for side in values.values() for x in side):
        return None, "missing or malformed outcome in a valid pair"
    t, c = _mean(values["target"]), _mean(values["control"])
    if not frozen:
        if field == "records" and max(t, c) < 1:
            return None, "floor: no records examined"
        if field != "records":
            outcomes = values["target"] + values["control"]
            if sum(outcomes) < 3 or len(outcomes) - sum(outcomes) < 3:
                return None, "pooled errand outcomes lack three of each outcome"
    return _index(t, c, cls), None


def score_units(rows, cls, components=None, frozen=False):
    """Recompute a class from units and optionally a fixed component set.

components accepts component ids or scorecard metadata including planned_t/c.
With an explicit set every requested component must remain supported. With None,
point guards choose E once. frozen=True skips point sufficiency thresholds while
retaining missing-denominator/price checks; it is for bootstrap draws of fixed E.
Returns score, raw, components, status, missing, and component_values.
"""
    ids, metadata, fixed = _selected(cls, components)
    rows = list(rows)
    values, missing = {}, []
    for component in ids:
        helper = _choice_component if cls == "I" else _errand_component
        args = (rows, component, metadata.get(component, {}), frozen) if cls == "I" else (
            rows, cls, component, metadata.get(component, {}), frozen)
        value, reason = helper(*args)
        if value is None:
            missing.append({"id": component, "reason": reason or "undefined index"})
        else:
            values[component] = value
    enough = len(values) >= (3 if cls == "I" else 2) and not (fixed and missing)
    raw = 100 * _mean(list(values.values())) if enough else None
    return {"score": min(100, max(0, raw)) if raw is not None else None,
            "raw": raw, "components": list(values), "component_values": values,
            "status": ("complete" if len(values) == len(_ids(cls)) else "partial") if enough else "insufficient",
            "missing": missing}


def _run_ids(run, cls):
    """Read the scorecard's point E, falling back to sufficient metadata only."""
    info = run.get("classes", {}).get(cls, {})
    return list(info["components"]) if "components" in info else _selected(cls, run.get("components", {}))[0]


def _metadata(run, ids):
    """Bind fixed ids to their original planned counts."""
    return {key: dict(run.get("components", {}).get(key, {}), sufficient=True) for key in ids}


def common_score(run, cls, common):
    """One run's class score over the fixed common components, from its own units.

Each component index is computed over every valid task (or item) of this run
alone, never matched to another run, and with no sufficiency guard applied
again (frozen): the components were judged sufficient by the run's scorecard.
A bootstrap draw passes a copy of the run holding its drawn units. None when a
common component has no defined index in these units (a missing price or side).
"""
    return score_units(run["units"], cls, _metadata(run, common), frozen=True)["score"]


def paired_scores(left, right, cls, components=None):
    """Return CALIBRATION 9's matched class scores and left-minus-right contrast.

The common components are those sufficient in both runs' scorecards (limited to
components when it is given). Each run's score is recomputed over them from its
own units; tasks are not matched across the runs and no guard is re-applied, so
a point estimate and a bootstrap draw use the same rule. A pair with fewer
common components than the class's minimum is insufficient.
"""
    common = sorted(set(_run_ids(left, cls)) & set(_run_ids(right, cls)))
    if components is not None:
        common = sorted(set(common) & set(components))
    minimum = 3 if cls == "I" else 2
    sa = common_score(left, cls, common) if len(common) >= minimum else None
    sb = common_score(right, cls, common) if len(common) >= minimum else None
    sufficient = sa is not None and sb is not None
    reason = None
    if not sufficient:
        reason = ("fewer than %d common sufficient components" % minimum if len(common) < minimum
                  else "a common component's index is undefined in a run's units")
    return {"left": sa, "right": sb, "delta": sa - sb if sufficient else None,
            "components": common, "status": "sufficient" if sufficient else "insufficient", "reason": reason}


def _valid_run(run, cls):
    """Keep the point scorecard's eligibility fixed, especially Class III validity."""
    info = run.get("classes", {}).get(cls, {})
    return (_number(info.get("score")) and info.get("status") in ("complete", "partial")
            and (cls != "III" or info.get("validity") == "valid"))


def _pools(runs):
    """One shared task pool, and per-type/component/price base-item pools."""
    tasks, clusters = set(), defaultdict(set)
    for run in runs:
        for row in run["units"]:
            if row.get("class") == "I" and row.get("component") in _ids("I"):
                key = (run["target_type"], row["component"], row.get("stratum") if row["component"] == "I.4" else None)
                if row.get("cluster") is not None:
                    clusters[key].add(row["cluster"])
            elif row.get("cell") in ("N0", "U", "O") and row.get("task") is not None:
                tasks.add(row["task"])
    ordered = lambda xs: sorted(xs, key=lambda x: (type(x).__name__, str(x)))
    return ordered(tasks), {key: ordered(values) for key, values in clusters.items()}


def _draw_units(run, tasks, clusters):
    """Apply shared multiplicities to every run, copying complete base clusters."""
    task_rows, cluster_rows = defaultdict(list), defaultdict(list)
    for row in run["units"]:
        if row.get("class") == "I" and row.get("component") in _ids("I"):
            group = (run["target_type"], row["component"], row.get("stratum") if row["component"] == "I.4" else None)
            cluster_rows[(group, row.get("cluster"))].append(row)
        elif row.get("cell") in ("N0", "U", "O"):
            task_rows[row.get("task")].append(row)
    drawn = []
    for position, task in enumerate(tasks):
        drawn.extend(dict(row, task=position) for row in task_rows[task])
    for group, chosen in clusters.items():
        if group[0] != run["target_type"]:
            continue
        for cluster in chosen:
            drawn.extend(cluster_rows[(group, cluster)])
    return drawn


def _supported(run, cls, tasks, clusters):
    """Refuse incompatible planned pools instead of resampling a smaller bank."""
    if cls != "I":
        planned = set(_task_pairs(run["units"], cls)[0])
        return planned == set(tasks)
    for component in _run_ids(run, cls):
        relevant = [row for row in run["units"] if row.get("component") == component]
        groups = {key: values for key, values in clusters.items()
                  if key[:2] == (run["target_type"], component)}
        if not groups:
            return False
        for key, values in groups.items():
            present = {row.get("cluster") for row in relevant
                       if component != "I.4" or row.get("stratum") == key[2]}
            if present != set(values):
                return False
    return True


def _interval(point, draws, included, excluded, B):
    """Expose failed replicates and suppress conditional-on-defined intervals."""
    valid = [value for value in draws if value is not None and _number(value)]
    undefined = B - len(valid)
    return {"point": point, "ci": [percentile(valid, .025), percentile(valid, .975)]
            if point is not None and not undefined and len(valid) == B else None,
            "valid_replicates": len(valid), "undefined_replicates": undefined,
            "included_types": sorted(included), "excluded_types": sorted(excluded)}


def joint_intervals(runs, B=2000, seed=20261109):
    """Compute fixed-target level and matched-contrast percentile intervals.

Task indices are drawn once per replicate across all runs and types; Class I
clusters once per type/component, stratified by price for I.4. Every repeat and
side follows its cluster. A level requires two eligible targets. Contrasts use
at least two common eligible types and common point-sufficient components per
type. The five registered level contrasts have first-minus-second signs.

The caller must supply one run per (level,target_type), excluding retests,
positive controls and other arms from this primary-level comparison.
"""
    if isinstance(B, bool) or not isinstance(B, int) or B < 1:
        raise ValueError("B must be a positive integer")
    runs = list(runs)
    by_key = {}
    for run in runs:
        key = (run["level"], run["target_type"])
        if key in by_key:
            raise ValueError("select one run per level and target type")
        by_key[key] = run
    levels, types = sorted({key[0] for key in by_key}), {key[1] for key in by_key}
    selected, contrast_pairs = {}, {}
    for level in levels:
        for cls in CLASSES:
            selected[(level, cls)] = [run for run in runs if run["level"] == level and _valid_run(run, cls)]
    for left, right in CONTRASTS:
        if left not in levels or right not in levels:
            continue
        for cls in CLASSES:
            paired = []
            for kind in sorted(types):
                a, b = by_key.get((left, kind)), by_key.get((right, kind))
                if a and b and _valid_run(a, cls) and _valid_run(b, cls):
                    point = paired_scores(a, b, cls)
                    if point["delta"] is not None:
                        paired.append((a, b, point))
            contrast_pairs[(left + "-" + right, cls)] = paired
    task_pool, cluster_pools = _pools(runs)
    supported = {(run["level"], run["target_type"], cls): _supported(run, cls, task_pool, cluster_pools)
                 for run in runs for cls in CLASSES}
    rng = random.Random(seed)
    level_draws, contrast_draws = defaultdict(list), defaultdict(list)
    for _ in range(B):
        tasks = [rng.choice(task_pool) for _ in task_pool]
        clusters = {key: [rng.choice(values) for _ in values] for key, values in sorted(cluster_pools.items(), key=lambda kv: str(kv[0]))}
        copies = {key: dict(run, units=_draw_units(run, tasks, clusters)) for key, run in by_key.items()}
        for key, included in selected.items():
            cls = key[1]
            values = [score_units(copies[(run["level"], run["target_type"])]["units"], cls,
                                  _metadata(run, _run_ids(run, cls)), frozen=True)["score"]
                      if supported[(run["level"], run["target_type"], cls)] else None for run in included]
            level_draws[key].append(_mean(values) if len(values) >= 2 else None)
        for key, pairs in contrast_pairs.items():
            cls = key[1]
            values = [paired_scores(copies[(a["level"], a["target_type"])], copies[(b["level"], b["target_type"])],
                                    cls, point["components"])["delta"]
                      if supported[(a["level"], a["target_type"], cls)] and supported[(b["level"], b["target_type"], cls)]
                      else None for a, b, point in pairs]
            contrast_draws[key].append(_mean(values) if len(values) >= 2 else None)
    output = {"levels": {}, "contrasts": {}, "B": B, "seed": seed}
    for (level, cls), included in selected.items():
        kinds = {run["target_type"] for run in included}
        point = _mean([run["classes"][cls]["score"] for run in included]) if len(included) >= 2 else None
        output["levels"].setdefault(level, {})[cls] = _interval(point, level_draws[(level, cls)], kinds, types - kinds, B)
    for key, pairs in contrast_pairs.items():
        label, cls = key
        kinds = {a["target_type"] for a, _, _ in pairs}
        point = _mean([p["delta"] for _, _, p in pairs]) if len(pairs) >= 2 else None
        output["contrasts"].setdefault(label, {})[cls] = _interval(point, contrast_draws[key], kinds, types - kinds, B)
    return output
