"""The staged study's gates at run time (CALIBRATION.md sections 6 and 7). grid.py holds the rules as pure functions:
eligibility (may this run start?), stage_caps and cap_status (may this stage make another charged call?). This module
gives them their inputs and enforces their answers:

  * the plan: the folder grid.py wrote (manifest.json and one spec per run); a staged spec is run from it;
  * the control file, control.json beside the manifest, holding what only the operator can attest:
        {"stage_a_complete": bool, "codex_verification_pending": bool,
         "predictions": {stage: {"five_hour": points, "seven_day": points}}}
    A missing file attests nothing, so only Stage A can start; Codex verification counts as pending until it says not;
  * the records: each planned run's calibration.json in the results folder (run.py writes them);
  * B1's verdict, computed by the registered analysis (analyze.py) from the 15 finished B1 runs, never typed in;
  * the stage book in the account ledger (account.py), shared by every host: each stage's baseline burn, caps and
    prediction, fixed when it opens and kept across resumes, and the Codex arm's charged requests.

run.py calls dispatch() before a staged run starts (eligibility, then opening its stage) and call_gate() before each
launch; the shim calls call_gate() before every charged Claude call and reserve_codex() before every Codex exec. A
stage at its cap writes STOP and waits for Ephraiem's go, which nothing here gives.

The point caps are checked against the burn the ledger has summed so far (meter.py: the chronological sum of every
reading's positive increment, apart from the high-water marks that drive the pauses), so a stage can pass its cap by
at most the burn of the calls already in flight (a run's jobs) plus what the scheduler's cache has not yet seen (its
cache_seconds). While the ledger is out of reach the burn cannot be counted, so a staged call is refused (STOP).
Codex requests are countable before they run, so each is reserved: the ledger grants one only while the count is
below the cap, under its lock, and concurrent calls can never overshoot it.
"""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import account  # noqa: E402
import grid  # noqa: E402

CONTROL = "control.json"
CONTROL_DEFAULTS = {"stage_a_complete": False, "codex_verification_pending": True, "predictions": {}}
CODEX_STAGE = "C"               # every Codex exec counts against the Codex arm, which is stage C
POINT_WINDOWS = ("five_hour", "seven_day")


# ---- the plan, the control file and the records ---------------------------------------------------------------------
def load_plan(folder):
    """(specs, control): every planned spec in the manifest's order, and the control file over its defaults. Raises
    ValueError for a folder that is not a grid plan or a control file of the wrong shape."""
    folder = Path(folder)
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema") != "probatio-calibration-grid/1" or not isinstance(manifest.get("runs"), list):
        raise ValueError(f"{folder} holds no calibration grid plan (grid.py --out writes one)")
    specs = [json.loads((folder / f"{run_id}.json").read_text(encoding="utf-8")) for run_id in manifest["runs"]]
    path = folder / CONTROL
    given = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    if not isinstance(given, dict) or set(given) - set(CONTROL_DEFAULTS):
        raise ValueError(f"{path} may hold only {', '.join(CONTROL_DEFAULTS)}")
    control = dict(CONTROL_DEFAULTS, **given)
    if not isinstance(control["stage_a_complete"], bool) or not isinstance(control["codex_verification_pending"], bool):
        raise ValueError(f"{path}: stage_a_complete and codex_verification_pending must be true or false")
    if not isinstance(control["predictions"], dict):
        raise ValueError(f"{path}: predictions must map a stage to its predicted points")
    return specs, control


def records(results_dir, specs):
    """{run_id: calibration.json} for every planned run that has a readable record (an unreadable one is not a
    finished run)."""
    out = {}
    for spec in specs:
        path = Path(results_dir) / spec["run_id"] / "calibration.json"
        try:
            out[spec["run_id"]] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    return out


def b1_verdict(results_dir, specs, recs):
    """(verdict, why): B1's verdict by the registered analysis once all of B1 is done, else (None, the reason)."""
    b1 = [s for s in specs if s["stage"] == "B1"]
    unfinished = [s["run_id"] for s in b1 if recs.get(s["run_id"], {}).get("outcome") != "done"]
    if not b1 or unfinished:
        return None, "B1 is not finished"
    import analyze                                  # the decision code, only when a verdict is needed
    try:
        runs = [analyze.read_run(Path(results_dir) / s["run_id"]) for s in b1]
        return analyze.evaluate_b1(runs)["verdict"], None
    except (ValueError, OSError, KeyError) as e:
        return None, f"B1's scorecards could not be analyzed ({e})"


# ---- before a run ---------------------------------------------------------------------------------------------------
def dispatch(spec, cfg, now, conf, open_stage=True):
    """Before a staged run starts (resumes included): refuse it unless grid.eligibility says it is ready, then open its
    stage in the ledger with the caps grid.stage_caps fixes (from the control file's prediction for B1, B2 and H). A
    dry run (open_stage=False) checks the same but opens nothing, so it cannot start a stage's burn count early.
    Returns what was decided, for the run's record. Raises ValueError with the reasons; account.Unreachable when the
    ledger cannot be reached."""
    specs, control = load_plan(spec["plan_dir"])
    planned = next((s for s in specs if s["run_id"] == spec["run_id"]), None)
    if planned is None or planned.get("stage") != spec.get("stage"):
        raise ValueError(f"{spec['run_id']} is not a stage {spec.get('stage')} run of the plan in {spec['plan_dir']}")
    stage = planned["stage"]
    recs = records(cfg["results_dir"], specs)
    verdict, why = b1_verdict(cfg["results_dir"], specs, recs) if stage in ("B2", "C") else (None, None)
    gate = grid.eligibility(planned, recs, now, b1_verdict=verdict, stage_a_complete=control["stage_a_complete"],
                            codex_verification_pending=control["codex_verification_pending"], specs=specs)
    if not gate["ready"]:
        raise ValueError("; ".join(gate["reasons"] + ([why] if why else [])))
    predicted = control["predictions"].get(stage)
    caps = grid.stage_caps(stage, predicted)
    baseline = None
    if open_stage:
        state = account.call(conf, "stage-open", {"stage": stage, "caps": caps, "predicted": predicted})
        baseline = state["stages"][stage]["baseline"]
    return {"stage": stage, "eligibility": gate, "b1_verdict": verdict, "caps": caps, "predicted": predicted,
            "baseline": baseline}


# ---- before every charged call --------------------------------------------------------------------------------------
def usage(state, stage):
    """(the stage's usage so far, its ledger entry): the burn since it opened, in points per window, and its charged
    requests; (None, entry) when the stage has not been opened."""
    entry = ((state or {}).get("stages") or {}).get(stage) or {}
    if entry.get("opened") is None:
        return None, entry
    burn = ((state or {}).get("burn") or {}).get("total") or {}
    used = {w: round(burn.get(w, 0.0) - entry["baseline"].get(w, 0.0), 6) for w in POINT_WINDOWS}
    used["charged_requests"] = entry.get("charged_requests", 0)
    return used, entry


def call_gate(stage, state, degraded=False):
    """None when the stage may make another charged call, else why not (the caller writes STOP). No stage (a run
    outside the staged study) has no stage cap. With the ledger out of reach (degraded: the scheduler is deciding from
    a cached copy) the stage's burn cannot be counted, so a staged call is refused rather than run on a stale count."""
    if not stage:
        return None
    if degraded:
        return f"the quota ledger is out of reach, so stage {stage}'s burn cannot be counted"
    used, entry = usage(state, stage)
    if used is None:
        return f"stage {stage} is not open in the quota ledger (run.py opens it before the run starts)"
    status = grid.cap_status(stage, used, entry.get("predicted"))
    if status["allowed"]:
        return None
    shown = ", ".join(f"{k} {used[k]:g}" for k in [k for k in (status["caps"] or {}) if k in used] or used)
    return f"stage {stage}: {status['reason']} ({shown} of {status['caps']}); the stage waits for Ephraiem's go"


def reserve_codex(conf, cap):
    """(granted, count): reserve one charged Codex request against the arm's cap in the ledger, atomically. The ledger
    applies cap_status's rule (granted only while the count is below the cap) under its lock. Raises
    account.Unreachable when the ledger cannot be reached."""
    state = account.call(conf, "reserve", {"stage": CODEX_STAGE, "cap": cap})
    return state.get("reserved") is True, state["stages"][CODEX_STAGE]["charged_requests"]
