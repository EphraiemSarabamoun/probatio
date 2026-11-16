#!/usr/bin/env python3
"""Build the staged calibration plan without executing a subject call.

The manifest is input to run.py, not an authorization to start runs. run.py calls
eligibility before dispatch and the shim cap_status before each charged request
(stages.py gives them their inputs); those pure functions refuse missing
predictions and malformed usage counters. All quota values here are percentage
POINTS, not utilization fractions.
"""
import argparse
import json
import math
import random
from pathlib import Path

try:
    from . import ladder
except ImportError:
    import ladder

SHUFFLE_SEED = 20261108
BOOTSTRAP_SEED = 20261109
RETEST_DELAY = 12 * 60 * 60
CODEX_CAP = 1000
STAGES = ("A", "B1", "B2", "H", "C")
B1_VERDICTS = ("not evaluable", "fail", "partial", "pass I and II, III inconclusive",
               "pass I and II, III failed", "pass")
CEILINGS = {"A": (20, 5), "B1": (300, 30), "B2": (300, 30), "H": (100, 10)}
MODEL = {"sonnet": "claude-sonnet-5-5", "haiku": "claude-haiku-5-5", "opus": "claude-opus-5-5"}
# These are the preregistration's fixed names, never aliases. External callers
# may supply complete canonical roster records instead of these ladder fields.
NAMES = {"individual": ("Daniel Whitford", "Ephraiem Sarabamoun", "Karim Nassar"),
         "organization": ("Kelthorne Labs", "Mozilla", "Thirlbeck Systems"),
         "nation": ("Delmora", "France", "Arvenia")}


def fixed_entities():
    """Return only the canonical fields the fixed calibration ladder consumes."""
    entities = {}
    for kind, names in NAMES.items():
        entities[kind] = {}
        for role, name in zip(("invented", "real", "control"), names):
            short = name.split()[0] if kind == "individual" else name
            poss = name + ("'" if name.endswith("s") and kind != "individual" else "'s")
            short_poss = short + "'s" if kind == "individual" else poss
            entities[kind][role] = {"type": kind, "key": name.lower().replace(" ", "-"),
                                    "full": name, "Full": name, "poss": poss, "Short_poss": short_poss}
    return entities


def make_spec(stage, kind, role, level, subject, entities, codex_model, retest=False):
    """Create a runner-compatible spec with analysis and dependency metadata."""
    target, control = entities[kind][role], entities[kind]["control"]
    agent = "codex" if subject == "codex" else "claude"
    tier = "smoke" if stage in ("A", "C") else "standard"   # smoke: the plan called quick until 2026-11-08 (PREREG 5)
    suffix = "-retest" if retest else ""
    run_id = f"{stage}-{subject}-{level}-{kind}-{target['key']}{suffix}"
    questions, errands = (140, 40) if tier == "smoke" else (264, 120)
    pc = level == "PC"
    # Only the flags a spec may forward (run.py's ALLOWED_ARGS). --memory, --auto-memory off and --effort belong to
    # the harness, which passes them from the spec's memory and effort fields (run.command).
    args = ["--target-type", kind, "--name" if kind == "individual" else "--target", target["full"],
            "--tier", tier, "--class", "II,III" if pc else "all", "--arm", "g_wish" if pc else "neutral"]
    return {"run_id": run_id, "stage": stage, "agent": agent,
            "model": codex_model if agent == "codex" else MODEL[subject], "subject": subject,
            "level": level, "tier": tier, "target_type": kind, "target_key": target["key"], "target_full": target["full"],
            "target": target, "control": control, "target_role": role,
            "memory": "off" if level in ("L0", "PC") else "on", "memory_file": None,
            "jobs": 1, "cli": "v4", "effort": "medium", "probatio_args": args,
            "shuffle_seed": SHUFFLE_SEED, "bootstrap_seed": BOOTSTRAP_SEED,
            "task_seed_policy": "bank tier pools; shared across all targets and levels",
            "planned_questions": 0 if pc else questions, "planned_errands": errands,
            "planned_charged_requests": (0 if pc else questions) + errands * (2 if agent == "codex" else 1),
            "charged_request_cap": CODEX_CAP if stage == "C" else None,
            "retest_of": f"B1-sonnet-{level}-{kind}-{target['key']}" if retest else None,
            "min_delay_seconds": RETEST_DELAY if retest else 0,
            "exclude_from_analysis": stage == "A"}


def build_grid(codex_model, entities=None):
    """Return all 50 specs, with one shared seeded stream, shuffling each stage once.

    codex_model must be supplied from the recorded freeze, never guessed from a
    CLI default. No task-selection flag is added: v4 uses the pinned bank pools.
    """
    if not isinstance(codex_model, str) or not codex_model.strip():
        raise ValueError("record the Codex model at the freeze before building the grid")
    entities = fixed_entities() if entities is None else entities
    rows = {stage: [] for stage in STAGES}

    def add(stage, kind, level, subject="sonnet", role="invented", retest=False):
        rows[stage].append(make_spec(stage, kind, role, level, subject, entities, codex_model, retest))

    for kind in ladder.TYPES:
        add("A", kind, "L0")
    add("A", "individual", "PC")
    add("A", "organization", "L4")
    add("A", "organization", "L0", "haiku")
    add("A", "organization", "L4", "haiku")
    add("A", "organization", "L0", "opus")
    for kind in ladder.TYPES:
        for level in ("L0", "L3", "L4", "L6", "L7"):
            add("B1", kind, level)
        for level in ("L0", "L4"):
            add("B2", kind, level, role="real")
        for level in ("L0", "L4", "L6"):
            add("B2", kind, level, retest=True)
        add("B2", kind, "PC")
    for level in ("L0", "L3", "L4", "L6", "L7"):
        add("H", "organization", level, "haiku")
    for level in ("L0", "L3", "L4", "L6"):
        add("C", "organization", level, "codex")
    rng = random.Random(SHUFFLE_SEED)
    for stage in STAGES:
        rng.shuffle(rows[stage])
        for i, spec in enumerate(rows[stage]):
            spec["stage_order"] = i
    return [spec for stage in STAGES for spec in rows[stage]]


def point(value):
    """Accept finite nonnegative point counters, rejecting booleans and strings."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def stage_caps(stage, predicted=None):
    """Freeze a stage's hard limits; unknown or over-ceiling burn cannot start.

    Explicit human authorization for an over-ceiling prediction belongs to the
    runner's control plane; this API never silently increases a registered cap.
    """
    if stage == "C":
        return {"charged_requests": CODEX_CAP}
    if stage not in CEILINGS:
        raise ValueError("unknown stage")
    ceiling = dict(zip(("five_hour", "seven_day"), CEILINGS[stage]))
    if stage == "A":
        return ceiling
    if not isinstance(predicted, dict) or any(not point(predicted.get(k)) or predicted[k] == 0 for k in ceiling):
        raise ValueError("a positive Stage A burn prediction is required in point units")
    if any(predicted[k] > ceiling[k] for k in ceiling):
        raise ValueError("stage prediction exceeds its ceiling; wait for Ephraiem's go")
    return {k: min(1.5 * predicted[k], ceiling[k]) for k in ceiling}


def cap_status(stage, usage, predicted=None):
    """Return a fail-closed per-call decision from summed positive increments.

    C's counter counts all exec invocations, report legs and retries included.
    This is a decision helper; the runner must atomically reserve a request under
    the stage lock before invocation to prevent concurrent overshoot.
    """
    try:
        caps = stage_caps(stage, predicted)
    except ValueError as exc:
        return {"allowed": False, "reason": str(exc), "caps": None}
    if not isinstance(usage, dict) or any(not point(usage.get(k)) for k in caps):
        return {"allowed": False, "reason": "missing or malformed stage usage counters", "caps": caps}
    if stage == "C" and not isinstance(usage["charged_requests"], int):
        return {"allowed": False, "reason": "charged requests must be an integer", "caps": caps}
    reached = [k for k in caps if usage[k] >= caps[k]]
    return {"allowed": not reached, "reason": "stage cap reached: " + ", ".join(reached) if reached else None,
            "caps": caps}


def eligibility(spec, records, now, b1_verdict=None, stage_a_complete=False,
                codex_verification_pending=True, specs=None):
    """Check stage order and actual elapsed retest time against completed records.

    records maps run_id to calibration.json (outcome='done', finite ended epoch).
    stage_a_complete explicitly attests mechanics, metering and the freeze, beyond
    merely having eight finished runs. Unknown B1/Codex verification state blocks.
    Retest delay begins at original completion, a conservative 12-hour boundary.
    """
    if not point(now):
        raise ValueError("now must be a finite epoch timestamp")
    specs = build_grid(spec["model"] if spec["agent"] == "codex" else "freeze-model") if specs is None else specs
    reasons, not_before = [], None
    stage = spec["stage"]
    if stage not in STAGES:
        raise ValueError("unknown stage")

    def complete(run_id):
        record = records.get(run_id, {})
        return record.get("outcome") == "done" and point(record.get("ended")) and record["ended"] <= now

    needs = set()
    if stage != "A":
        needs.add("A")
        if not stage_a_complete:
            reasons.append("Stage A mechanics, metering and freeze are not attested")
    if stage in ("B2", "H", "C"):
        needs.add("B1")
    if stage == "B2" and b1_verdict not in B1_VERDICTS[2:]:
        reasons.append("B2 requires an interpretable B1 verdict")
    if stage == "C":
        needs.add("H")
        if b1_verdict not in B1_VERDICTS:
            reasons.append("B1 verdict is unknown")
        elif b1_verdict in B1_VERDICTS[2:]:
            needs.add("B2")
        if codex_verification_pending:
            reasons.append("Codex verification work is pending or unknown")
    required = [r for r in specs if r["stage"] in needs or
                (r["stage"] == stage and r["stage_order"] < spec["stage_order"])]
    missing = [r["run_id"] for r in required if not complete(r["run_id"])]
    if missing:
        reasons.append("unfinished prerequisite runs: " + ", ".join(missing))
    original = spec.get("retest_of")
    if original:
        if not complete(original):
            reasons.append("retest original is not completed with a valid timestamp")
        else:
            not_before = records[original]["ended"] + RETEST_DELAY
            if now < not_before:
                reasons.append("retest needs at least 12 actual hours after original completion")
    return {"ready": not reasons, "reasons": reasons, "not_before": not_before}


def write_plan(folder, specs):
    """Write reviewable specs and rendered audits; refuse overwriting old plans.

    Off-run memory audits remain outside the clean room and memory_file is null,
    so L0/PC never deliver those bytes to the subject. No subject CLI is invoked.
    """
    folder = Path(folder)
    if folder.exists():
        raise ValueError("output path already exists; choose a fresh plan directory")
    folder.mkdir(parents=True)
    (folder / "memories").mkdir()
    for spec in specs:
        audit = ladder.describe(spec["level"], spec["target"], spec["control"], spec["agent"])
        relative = f"memories/{spec['run_id']}.md"
        (folder / relative).write_bytes(audit["bytes"])
        saved = dict(spec, memory_file=relative if audit["memory"] == "on" else None,
                     ladder_audit_file=relative, ladder_sha256=audit["sha256"],
                     memory_sha256=audit["sha256"] if audit["memory"] == "on" else None)
        (folder / f"{spec['run_id']}.json").write_text(json.dumps(saved, indent=2) + "\n", encoding="utf-8")
    manifest = {"schema": "probatio-calibration-grid/1", "shuffle_seed": SHUFFLE_SEED,
                "bootstrap_seed": BOOTSTRAP_SEED, "retest_delay_seconds": RETEST_DELAY,
                "codex_charged_request_cap": CODEX_CAP,
                "stage_caps": {s: dict(zip(("five_hour", "seven_day"), CEILINGS[s])) for s in CEILINGS},
                "caps_note": "A fixed; others min(1.5 * Stage A prediction, ceiling); unknown prediction blocks",
                "stage_a_metering": {"blocks": ["questions", "errands", "haiku"],
                                     "readings": ["before", "during", "after"],
                                     "extra_block_max_five_hour_points": 5,
                                     "extra_block_requires_predeclaration": True},
                "runs": [s["run_id"] for s in specs]}
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main(argv=None):
    """Print the deterministic dry-run plan, optionally materializing its specs."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--codex-model", required=True, help="model recorded at the freeze")
    parser.add_argument("--out", type=Path, help="fresh output directory for specs and memory audits")
    parser.add_argument("--dry-run", action="store_true", help="print the plan without writing, even with --out")
    args = parser.parse_args(argv)
    try:
        specs = build_grid(args.codex_model)
        if args.out and not args.dry_run:
            write_plan(args.out, specs)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(specs, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
