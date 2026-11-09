#!/usr/bin/env python3
"""The ordo pilot (docs/ordo/DESIGN.md 8.7, docs/ordo/PREREG.md): the frozen private plan, checked, and the runner that
makes its calls through v4's calibration room after Ephraiem's go (2026-11-08). docs/ordo/PILOT_RUNBOOK.md gives the
commands in order.

calibration/ordo/plan.json freezes the pilot: three arms, each a memory file the calibration room loads as CLAUDE.md
(neutral, byte for byte v4's ladder L0; g_wish_individual and g_wish_nation, L0 with labores's g_wish policy at type
level), the subject recipe, the seeds, Stage 1's gate and the primary hypothesis. The policy arms ask a subset of the
standard triangle (role-free Class I with its three-way item, and the role-free N0 errands), chosen here through
ordo.plan's item filter, so no public probatio flag exists for it.

The commands:
  check (the default)  each arm file has the sha256 plan.json records, the neutral arm is L0 byte for byte, every arm
                       plans through ordo.plan with its filter to exactly the units plan.json states, and the stages
                       add up to plan.json's total; a recorded item-id hash must match the bank's plan. No call.
  --freeze             records each arm's item-id sha256 against the pinned bank, once (done: PREREG Amendment 2)
  stage1               PREREG 4's Stage 1: one audited canary call in each arm's registered room (3 calls), then the
                       quick triangle under the neutral file (206 units). Then it writes the gate's reading list.
  gate                 Stage 1's pass conditions, exactly as frozen (ordo_gate.py): PASS or FAIL with each number
  stage2               refuses unless the gate passes now; then neutral at standard (624 units), g_wish_individual and
                       g_wish_nation (228 each), with exactly the frozen item filters
  analyze              PREREG 5's primary test, S1, and PREREG 6's secondary and descriptive results (ordo_analysis.py)
stage1 and stage2 take --dry-run: every room is built, audited and checked and every arm's preflight runs, but no
model is called and nothing is registered.

Every call goes through v4's calibration room unchanged (isolate.py, shim.py, guard.py): an isolated root, an empty
CLAUDE_CONFIG_DIR, the subscription token copied by name, exactly the arm's CLAUDE.md, auto memory off, the pinned
Claude Code build, effort medium and the registered model, with shim.py checking each call before it starts. The quota
is the account's (scheduler.py, account.py): a pause at five-hour 0.60, a resume only below 0.40, a latched stop at
seven-day 0.60 until the operator releases it after Ephraiem's go, and a ledger that cannot be read holds. A stage is
resumable: running it again skips every recorded call. One calling stage runs at a time (pilot.lock), and the room's
STOP halts it at the next call boundary. Results go to the private folder local.json names (ordo_pilot_results), which
must lie outside every git tree; nothing there is committed or uploaded.

    python3 calibration/ordo_pilot.py [check] [--bank bank/ordo] [--freeze]
    python3 calibration/ordo_pilot.py stage1|gate|stage2|analyze [--local calibration/local.json] [--results DIR]
                                      [--dry-run]
"""
import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import socket
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))
import ordo  # noqa: E402

PILOT = HERE / "ordo"
PLAN = PILOT / "plan.json"
L0 = HERE / "ladder" / "L0.individual.md.tpl"
RESULTS_KEY = "ordo_pilot_results"      # local.json's key for the private results folder (git-ignored, host-specific)
LOCK = "pilot.lock"
STAGE_BOOK = {1: "stage1.json", 2: "stage2.json"}
# The pilot's approved host recipe (Codex's verification of a40f925, findings 1 and 2), checked before any room or call
# and registered with every arm-run: the subscription token's source variable (its value is never read here), the
# calibration root whose local account ledger (<root>/account) every call publishes to, and the quota limits Ephraiem
# approved. Tests inject a recipe of their own, explicitly (Pilot.recipe); the command line always uses this one.
RECIPE = {"token_name": "CLAUDE_CODE_OAUTH_TOKEN_BACKUP", "root": "/var/tmp/probatio-cal",
          "quota": {"home": "local", "five_hour": 0.60, "seven_day": 0.60}}
ROOM_POLICY = {"ledger": "required"}    # every pilot room refuses a call when the quota ledger cannot be read


class Refusal(SystemExit):
    """The pilot will not go on: the message says why and what to do."""


# ---- the frozen plan --------------------------------------------------------------------------------------------------
def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def selected(call, items):
    """Whether a planned call belongs to an arm's item selection: every item when the selection names no frame;
    otherwise the frame's Class I calls of the named components, and its fictional errands in the named cells."""
    meta = call["meta"]
    if items["frame"] is None:
        return True
    if meta.get("frame") != items["frame"] or meta.get("named"):
        return False
    if call.get("episode"):
        return meta["cell"] in items["cells"]
    return meta["component"] in items["components"]


def arm_calls(bank, plan, arm):
    """An arm's calls: the tier's plan narrowed by ordo.plan's item filter (8.7), under memory on only."""
    items = plan["arms"][arm]["items"]
    full = ordo.plan(bank, plan["compare"], plan_word(items["tier"]))
    ids = sorted({c["id"] for c in full if selected(c, items)})
    return ordo.plan(bank, plan["compare"], plan_word(items["tier"]), only=ids), ids


def ids_sha256(ids):
    return hashlib.sha256("\n".join(sorted(ids)).encode("utf-8")).hexdigest()


def pilot_runs(plan):
    """The pilot's arm-runs in their registered order: Stage 1's quick triangle under its arm, then Stage 2's arms in
    plan.json's order. Each run id names its room and its results folder."""
    smoke = plan["stage_1"]["smoke"]
    return ([{"stage": 1, "arm": smoke["arm"], "run_id": f"ordo-s1-{smoke['arm']}-{smoke['tier']}"}]
            + [{"stage": 2, "arm": arm, "run_id": f"ordo-s2-{arm}"} for arm in plan["stage_2"]["arms"]])


# PREREG.md Amendment 5 (2026-11-08): probatio's quick tier became the 15-minute estimate, and the plan this pilot froze
# as "quick" is now called smoke, item for item. plan.json is frozen and keeps its word; plan_word maps it at every
# planning boundary (arm_calls, planned_calls, check, and the --tier ordo_arm.py passes to probatio). stage_tier keeps
# the recorded word, since it is part of each arm-run's registration identity (ordo_run.registration), and pilot_runs'
# historical run ids (ordo-s1-neutral-quick) embed it.
FROZEN_TIER = {"quick": "smoke"}


def plan_word(word):
    """The planner tier for a tier word frozen in plan.json (FROZEN_TIER: quick plans as smoke)."""
    return FROZEN_TIER.get(word, word)


def stage_tier(plan, stage, arm):
    """The tier word an arm-run was registered at, as plan.json records it: Stage 1's smoke tier, or the arm's own in
    Stage 2. Plan with plan_word(stage_tier(...))."""
    return plan["stage_1"]["smoke"]["tier"] if stage == 1 else plan["arms"][arm]["items"]["tier"]


def planned_calls(bank, plan, stage, arm):
    """(calls, item ids) of one arm-run: Stage 1 asks the whole quick triangle under its arm; Stage 2 asks the arm's
    frozen selection (arm_calls)."""
    if stage == 1:
        if arm != plan["stage_1"]["smoke"]["arm"]:
            raise Refusal(f"Stage 1 runs under {plan['stage_1']['smoke']['arm']}, not {arm}")
        calls = ordo.plan(bank, plan["compare"], plan_word(stage_tier(plan, 1, arm)))
        return calls, sorted({c["id"] for c in calls})
    return arm_calls(bank, plan, arm)


def check(bank, plan):
    """Every check of the frozen plan; returns (rows for the printout, problems, total requests). The item-id hashes
    were frozen against the pinned bank, so they are compared only when bank is that bank (a staged or fixture bank
    holds other template ids)."""
    problems, rows = [], []
    pinned = bank.sha256 == ordo.ORDO_MANIFEST_SHA256
    if PILOT.joinpath("arms", "neutral.md").read_bytes() != L0.read_bytes():
        problems.append("arms/neutral.md is not calibration/ladder/L0.individual.md.tpl byte for byte")
    for arm, spec in plan["arms"].items():
        got = sha256_file(PILOT / spec["file"])
        if got != spec["sha256"]:
            problems.append(f"{spec['file']} has sha256 {got[:12]}, plan.json records {spec['sha256'][:12]}")
        calls, ids = arm_calls(bank, plan, arm)
        n = ordo.count_plan(calls)
        if n["units"] != spec["units"]:
            problems.append(f"{arm} plans {n['units']} units; plan.json states {spec['units']}")
        frozen = plan["item_ids_sha256"].get(arm)
        if pinned and frozen and frozen != ids_sha256(ids):
            problems.append(f"{arm}'s items differ from those frozen against the pinned bank")
        rows.append((f"stage 2: {arm}", n))
    smoke = ordo.count_plan(ordo.plan(bank, plan["compare"], plan_word(plan["stage_1"]["smoke"]["tier"])))
    if smoke["units"] != plan["stage_1"]["smoke"]["units"]:
        problems.append(f"Stage 1 plans {smoke['units']} units; plan.json states {plan['stage_1']['smoke']['units']}")
    rows.insert(0, ("stage 1: neutral, quick", smoke))
    total = sum(n["claude_requests"] for _, n in rows) + plan["stage_1"]["canary_calls"]
    if total != plan["total"]["claude_requests"]:
        problems.append(f"the stages add to {total} requests; plan.json states {plan['total']['claude_requests']}")
    return rows, problems, total


# ---- the canary (PREREG 4, Amendment 1) -------------------------------------------------------------------------------
def audit_room(work_dir, plan, arm):
    """PREREG Amendment 1: before an arm's one canary call (isolate.canary_call), its room's working directory must
    hold exactly one file, CLAUDE.md, whose bytes are the arm's source at its registered sha256. Returns the problems
    (none means the call may be made); it makes no call itself. Every Stage 2 room is audited the same way."""
    work_dir, spec = Path(work_dir), plan["arms"][arm]
    held = sorted(p.name for p in work_dir.iterdir())
    if held != ["CLAUDE.md"]:
        return [f"{arm}'s room holds {held}, not exactly CLAUDE.md"]
    got = sha256_file(work_dir / "CLAUDE.md")
    problems = []
    if got != spec["sha256"]:
        problems.append(f"{arm}'s room memory has sha256 {got[:12]}, the arm is registered at {spec['sha256'][:12]}")
    if (work_dir / "CLAUDE.md").read_bytes() != (PILOT / spec["file"]).read_bytes():
        problems.append(f"{arm}'s room memory is not {spec['file']} byte for byte")
    return problems


def canary_room(cfg, plan, arm):
    """An arm's canary room, as Stage 1 registers it: memory on, the arm's bytes, the pinned effort and model, and the
    pilot's room policy (a quota ledger that cannot be read refuses the call in the shim)."""
    import isolate
    return isolate.build(cfg, f"ordo-canary-{arm}", plan["subject"]["agent"], "on", isolate.EFFORT,
                         (PILOT / plan["arms"][arm]["file"]).read_bytes(), model=plan["subject"]["model"],
                         policy=ROOM_POLICY)


def canary(cfg, plan, arm, model=None):
    """Stage 1's canary for one arm (PREREG Amendment 1, Codex R6): build the arm's registered room with the arm's
    bytes as its memory file (isolate.build, memory on, the pinned effort), refuse unless audit_room finds exactly
    that file at its registered sha256 and the room passes isolate.check, then make exactly one isolate.canary_call.
    Returns (reply, context tokens), the reply redacted. A model call: stage1 makes it, after Ephraiem's go."""
    import isolate
    import redact
    model = model or plan["subject"]["model"]
    room = canary_room(cfg, plan, arm)
    problems = audit_room(room.work_dir, plan, arm)
    if problems:
        raise SystemExit(f"ordo canary refused for {arm}: " + "; ".join(problems))
    env = isolate.environment(room, cfg)
    isolate.check(room, cfg, env)
    reply, tokens = isolate.canary_call(room, cfg, model)
    return redact.Redactor(env)(reply), tokens


def canary_verdict(reply, tokens, cfg, plan, arm):
    """Whether an arm's canary shows its file and nothing else (the problems; none is a pass), by isolate.canary's
    tests for a memory-on room: the reply must show the arm's file (its first line, which the audit prompt asks it to
    quote), no host persona marker, and a context no larger than a clean call plus one small file. Which arm's bytes
    the room holds is the audit's job (audit_room), since every arm file opens with the same L0 lines."""
    import isolate
    first = (PILOT / plan["arms"][arm]["file"]).read_text(encoding="utf-8").splitlines()[0].lstrip("# ").strip()
    ceiling = isolate.CANARY_MAX_OFF_TOKENS + isolate.CANARY_MAX_FILE_TOKENS
    problems = []
    if reply.strip() == "NONE":
        problems.append("the reply says no memory file was loaded")
    elif first not in reply:
        problems.append(f"the reply does not show the arm's file (its first line, {first!r})")
    problems += ["the reply shows a persona marker" for m in cfg.get("persona_markers") or [] if m and m in reply]
    if not 0 < tokens <= ceiling:
        problems.append(f"the call used {tokens} context tokens; a clean call with one small file uses at most "
                        f"{ceiling}")
    return problems


# ---- the pilot's context and its preconditions ------------------------------------------------------------------------
@dataclass
class Pilot:
    """What every stage needs: the host's settings, the frozen plan (and its file), the pinned ordo bank, the private
    results folder, the approved host recipe (RECIPE; tests inject their own), and the clock, sleep and log (injected
    in tests)."""
    cfg: dict
    plan: dict
    plan_path: Path
    bank: object
    results: Path
    now: Callable = time.time
    sleep: Callable = time.sleep
    echo: Callable = print
    lines: list = field(default_factory=list)
    recipe: dict = field(default_factory=lambda: RECIPE)

    def output(self, path):
        """path, checked as an output of this pilot (output_path)."""
        return output_path(self.results, path)

    def log(self, message):
        """Print a line and append it, timestamped, to <results>/logs/pilot.log."""
        self.echo(message, flush=True)
        path = self.output(self.results / "logs" / "pilot.log")
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(self.now()))} {message}\n")


def git_tree(path):
    """The git work tree holding path (a .git entry in it or a parent), or None. A leading ~ is expanded first, so an
    unexpanded setting is never read against the working directory."""
    path = Path(path).expanduser().resolve()
    return next((d for d in (path, *path.parents) if (d / ".git").exists()), None)


def output_path(results, path):
    """path, refused unless it is the results folder or lies beneath it with no symlink among its existing components,
    and it (its existing ancestors resolved) lies in no git tree (Codex's finding 4): a symlink placed anywhere under
    the results folder cannot carry an output into a repository."""
    root, path = Path(results), Path(path)
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        raise Refusal(f"{path} lies outside the results folder {root}") from None
    cur = root
    for part in parts:
        cur = cur / part
        if cur.is_symlink():
            raise Refusal(f"{cur} is a symlink; nothing beneath the results folder may be one")
    tree = git_tree(path)
    if tree:
        raise Refusal(f"{path} lies inside the git tree {tree}; every pilot output must lie outside every git tree")
    return path


def check_tree(results, folder):
    """Refuse an existing output folder that holds a symlink or a git tree anywhere beneath it, so a resumed run, its
    episodes and the analysis never write through one. Returns folder."""
    output_path(results, folder)
    for dirpath, dirnames, filenames in os.walk(folder, followlinks=False):
        for name in dirnames + filenames:
            p = Path(dirpath) / name
            if p.is_symlink():
                raise Refusal(f"{p} is a symlink; nothing beneath the results folder may be one")
            if name == ".git":
                raise Refusal(f"{p.parent} is a git tree inside the results folder; outputs must lie outside every one")
    return folder


def results_folder(cfg, given):
    """The private results folder: --results, else local.json's ordo_pilot_results; refused inside any git tree, so
    nothing a run writes can be committed by accident."""
    path = given or cfg.get(RESULTS_KEY)
    if not path:
        raise Refusal(f"no results folder: set {RESULTS_KEY} in calibration/local.json (git-ignored), or pass --results")
    path = Path(path).expanduser().resolve()
    tree = git_tree(path)
    if tree:
        raise Refusal(f"the results folder {path} lies inside the git tree {tree}; it must lie outside every git tree")
    path.mkdir(parents=True, exist_ok=True)
    return path


def ledger_dir(root):
    """The local account ledger of a calibration root: <root>/account, scheduler.conf_from's home for a local ledger."""
    return (Path(root).expanduser() / "account").resolve()


def recipe_problems(cfg, plan, host=None, recipe=None):
    """How this host's settings differ from the frozen subject recipe (PREREG 2) and the approved host recipe
    (RECIPE): the pinned Claude Code version, this checkout's probatio.py (the child imports it), the registered host,
    the token's source variable, the calibration root, a local account ledger at <root>/account, and the 0.60 and 0.60
    quota limits. A message names what is required, never what local.json holds."""
    import scheduler
    s, host, r = plan["subject"], host or socket.gethostname().split(".")[0], recipe or RECIPE
    missing = [k for k in ("claude_version", "probatio", "root", "quota") if not cfg.get(k)]
    if missing:                                 # isolate.load_local fills these; a bare dict is not a host's settings
        return [f"the host settings lack {', '.join(missing)}; load them with isolate.load_local"]
    found = []
    if cfg["claude_version"] != s["claude_code"]:
        found.append(f"local.json does not pin Claude Code {s['claude_code']}, the pilot's recipe")
    if Path(cfg["probatio"]).resolve() != (ROOT / "probatio.py").resolve():
        found.append(f"local.json's probatio is not this checkout's {ROOT / 'probatio.py'}, which the pilot runs")
    if host != s["host"]:
        found.append(f"this host is not {s['host']}, where the pilot is registered to run (PREREG 2)")
    if cfg.get("token_name") != r["token_name"]:
        found.append(f"local.json's token_name is not {r['token_name']}, the pilot's registered token source")
    if Path(cfg["root"]).expanduser().resolve() != Path(r["root"]).expanduser().resolve():
        found.append(f"local.json's root is not {r['root']}, the pilot's calibration root")
    q = cfg["quota"]
    if q.get("home") != r["quota"]["home"]:
        found.append("local.json's quota home is not local: the pilot publishes to the local account ledger")
    elif Path(scheduler.conf_from(cfg, Path(cfg["root"]))["home_dir"]).expanduser().resolve() != ledger_dir(r["root"]):
        found.append(f"local.json's quota ledger is not {ledger_dir(r['root'])}, the pilot's account ledger")
    for w in ("five_hour", "seven_day"):
        if q.get(w) != r["quota"][w]:
            found.append(f"local.json's {w} limit is not {r['quota'][w]:.2f}, the limit Ephraiem approved")
    return found


def quota_identity(cfg):
    """The quota policy and ledger identity an arm-run registers (no secret): the ledger's home and directory, this
    host's label in it, the limits, this host's quota log, and the pilot rooms' policy."""
    import scheduler
    conf = scheduler.conf_from(cfg, Path(cfg["root"]))
    return {"home": conf["home"], "home_dir": str(Path(conf["home_dir"]).resolve()), "host": conf["host"],
            "limits": conf["limits"], "log_path": str(Path(conf["log_path"]).resolve()), "room_policy": ROOM_POLICY}


def token_source(cfg):
    """Where the subscription token is read from, as an arm-run registers it: the variable's name and the secrets
    file's canonical path. Never the value."""
    return {"token_name": cfg["token_name"], "secrets_file": str(Path(cfg["secrets_file"]).expanduser().resolve())}


def preconditions(pilot):
    """Refuse a stage, before any room or call, unless the plan checks clean against the pinned bank, this host
    matches both recipes, the account ledger answers with the approved limits and no study stop, and no output folder
    holds a symlink or a git tree."""
    import scheduler
    if pilot.bank.sha256 != ordo.ORDO_MANIFEST_SHA256 and pilot.plan_path == PLAN:
        raise Refusal("the pilot runs only against the pinned ordo bank")
    _, problems, _ = check(pilot.bank, pilot.plan)
    problems += recipe_problems(pilot.cfg, pilot.plan, recipe=pilot.recipe)
    if not problems:                            # only a ledger the recipe approves is ever opened
        problems += scheduler.Scheduler(scheduler.conf_from(pilot.cfg, Path(pilot.cfg["root"]))).check()
    if problems:
        raise Refusal("ordo pilot refused:\n  " + "\n  ".join(problems))
    check_tree(pilot.results, pilot.results)


@contextlib.contextmanager
def pilot_lock(results):
    """One calling stage at a time: an advisory lock on <results>/pilot.lock, released however the process ends."""
    f = open(Path(results) / LOCK, "a")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        f.close()
        raise Refusal(f"another pilot stage is running ({Path(results) / LOCK}); wait for it to end") from None
    try:
        yield
    finally:
        f.close()


def book_path(pilot, stage):
    """A stage's book, <results>/stage1.json or stage2.json, checked as an output."""
    return output_path(pilot.results, pilot.results / STAGE_BOOK[stage])


# ---- the stages -------------------------------------------------------------------------------------------------------
def stage1(pilot, dry_run=False):
    """PREREG 4's Stage 1 (module doc). Returns the stage's book. A canary that fails stops the stage: the arm's file
    did not reach the model as registered, and a person must look before anything else runs."""
    import isolate
    import ordo_gate
    import ordo_run
    import scheduler
    preconditions(pilot)
    unit = pilot_runs(pilot.plan)[0]
    calls, _ = planned_calls(pilot.bank, pilot.plan, 1, unit["arm"])
    if dry_run:
        for arm in pilot.plan["arms"]:
            room = canary_room(pilot.cfg, pilot.plan, arm)
            problems = audit_room(room.work_dir, pilot.plan, arm)
            if problems:
                raise Refusal(f"the {arm} canary room fails its audit: " + "; ".join(problems))
            isolate.check(room, pilot.cfg, isolate.environment(room, pilot.cfg))
            pilot.log(f"dry run: the {arm} canary room passes its audit and isolate.check (one call when live)")
        return dry_unit(pilot, unit, calls)
    with pilot_lock(pilot.results):
        book = ordo_run.load_json(book_path(pilot, 1), {"canaries": {}, "pauses": []})
        stop_path = Path(pilot.cfg["root"]) / "STOP"
        sched = scheduler.Scheduler(scheduler.conf_from(pilot.cfg, Path(pilot.cfg["root"])), clock=pilot.now)
        for arm in pilot.plan["arms"]:
            if (book["canaries"].get(arm) or {}).get("passed"):
                continue
            blocked = ordo_run.wait_clear(pilot, stop_path, sched, book["pauses"], f"canary {arm}")
            if blocked:
                book["outcome"] = f"stopped before the {arm} canary: {blocked}"
                ordo_run.save_json(book_path(pilot, 1), book)
                raise Refusal(f"Stage 1 {book['outcome']}")
            reply, tokens = canary(pilot.cfg, pilot.plan, arm)
            problems = canary_verdict(reply, tokens, pilot.cfg, pilot.plan, arm)
            book["canaries"][arm] = {"passed": not problems, "problems": problems, "tokens": tokens,
                                     "reply": reply[:1000], "t": pilot.now()}
            ordo_run.save_json(book_path(pilot, 1), book)
            pilot.log(f"canary {arm}: {'passed' if not problems else 'FAILED: ' + '; '.join(problems)} "
                      f"({tokens} context tokens)")
            if problems:
                book["outcome"] = f"stopped: the {arm} canary failed"
                ordo_run.save_json(book_path(pilot, 1), book)
                raise Refusal(f"Stage 1 stopped: the {arm} canary failed ({'; '.join(problems)}); a person must look "
                              "before anything else runs")
        record = ordo_run.run_unit(pilot, unit, calls)
        book["run"] = {"run_id": unit["run_id"], "outcome": record["outcome"]}
        book["outcome"] = record["outcome"]
        ordo_run.save_json(book_path(pilot, 1), book)
    if record["outcome"] == "done":
        reading = ordo_gate.prepare_reading(pilot)
        pilot.log(f"Stage 1 done. Read the 10 transcripts and check the sentences in {reading}, then run: "
                  "python3 calibration/ordo_pilot.py gate")
    return book


def stage2(pilot, dry_run=False):
    """PREREG 4's Stage 2: refused unless Stage 1's gate passes when it is computed now; then the arms in plan.json's
    order, each resumable. Returns the stage's book. A dry run reports the gate and runs every preflight, calling no
    model, so a real Stage 2's refusal is never bypassed."""
    import ordo_gate
    import ordo_run
    preconditions(pilot)
    gate = ordo_gate.evaluate(pilot)
    units = [u for u in pilot_runs(pilot.plan) if u["stage"] == 2]
    if dry_run:
        pilot.log(f"dry run: the gate reads {gate['verdict']}" + ("" if gate["verdict"] == "PASS" else
                                                                   "; a real stage2 refuses until it passes"))
        for unit in units:
            dry_unit(pilot, unit, planned_calls(pilot.bank, pilot.plan, 2, unit["arm"])[0])
        return {"dry_run": True, "gate": gate["verdict"]}
    if gate["verdict"] != "PASS":
        raise Refusal("Stage 2 refused: Stage 1's gate does not pass:\n" + ordo_gate.gate_text(gate))
    with pilot_lock(pilot.results):
        book = ordo_run.load_json(book_path(pilot, 2), {"runs": {}})
        book.setdefault("gates", []).append({"t": pilot.now(), "verdict": gate["verdict"],
                                             "numbers": {c["id"]: c["value"] for c in gate["conditions"]}})
        ordo_run.save_json(book_path(pilot, 2), book)
        for unit in units:
            if (book["runs"].get(unit["arm"]) or {}).get("outcome") == "done":
                continue
            record = ordo_run.run_unit(pilot, unit, planned_calls(pilot.bank, pilot.plan, 2, unit["arm"])[0])
            book["runs"][unit["arm"]] = {"run_id": unit["run_id"], "outcome": record["outcome"]}
            ordo_run.save_json(book_path(pilot, 2), book)
            if record["outcome"] != "done":
                raise Refusal(f"Stage 2 stopped at {unit['arm']}: {record['outcome']}")
        book["outcome"] = "done"
        ordo_run.save_json(book_path(pilot, 2), book)
    pilot.log("Stage 2 done. Run: python3 calibration/ordo_pilot.py analyze")
    return book


def dry_unit(pilot, unit, calls):
    """An arm-run's room and preflight with no call and nothing registered: the room is built and audited, the
    room's checks pass, and the child's dry run lists exactly the arm's file and the frozen plan's calls."""
    import isolate
    import ordo_run
    import redact
    out = ordo_run.out_dir(pilot, unit)
    room = ordo_run.open_room(pilot, unit, out)
    env = isolate.environment(room, pilot.cfg)
    scrub = redact.Redactor(env)
    try:
        text = ordo_run.preflight(pilot, unit, room, env, scrub, out, calls)
    except isolate.Refusal as e:
        raise Refusal(f"{unit['run_id']} dry run refused: {scrub(str(e))}") from None
    pilot.log(f"dry run of {unit['run_id']} (stage {unit['stage']}, {unit['arm']}): room {room.work_dir}\n"
              + scrub(text.rstrip()))
    return {"run_id": unit["run_id"], "preflight": scrub(text)}


# ---- the command line -------------------------------------------------------------------------------------------------
def print_check(bank, plan, freeze=False, staged=False):
    """The check command: the plan's counts and problems, and the one-time freeze."""
    rows, problems, total = check(bank, plan)
    for label, n in rows:
        print(f"  {label:<34} {n['units']:>5} units  {n['sonnet_min']:5.1f} min on Sonnet")
    print(f"  {'canaries':<34} {plan['stage_1']['canary_calls']:>5} calls")
    print(f"  {'total':<34} {total:>5} Claude Code requests")
    if problems:
        raise SystemExit("ordo pilot plan refused:\n  " + "\n  ".join(problems))
    if freeze:
        if staged:
            raise SystemExit("--freeze records hashes against the pinned bank only; drop --bank")
        if any(plan["item_ids_sha256"].values()):
            raise SystemExit("the item ids are already frozen; a frozen plan is never re-frozen")
        plan["item_ids_sha256"] = {arm: ids_sha256(arm_calls(bank, plan, arm)[1]) for arm in plan["arms"]}
        PLAN.write_text(json.dumps(plan, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"froze the item ids of {', '.join(plan['arms'])} in {PLAN}")
    print("The pilot runs with: python3 calibration/ordo_pilot.py stage1 (docs/ordo/PILOT_RUNBOOK.md).")


def load_pilot(local, results):
    """The Pilot for this host: local.json, the frozen plan, the pinned ordo bank and the private results folder."""
    import isolate
    try:
        cfg = isolate.load_local(local)
    except isolate.Refusal as e:
        raise Refusal(f"refused: {e}") from None
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    return Pilot(cfg=cfg, plan=plan, plan_path=PLAN, bank=ordo.load_ordo_bank(), results=results_folder(cfg, results))


def main(argv=None):
    p = argparse.ArgumentParser(description="Check, run and analyse the frozen ordo pilot (design 8.7, PREREG).")
    p.add_argument("command", nargs="?", default="check", choices=("check", "stage1", "gate", "stage2", "analyze"))
    p.add_argument("--bank", help="check only: a staged ordo bank folder, read unpinned (default: bank/ordo, pinned)")
    p.add_argument("--freeze", action="store_true", help="check only: record each arm's item-id sha256, once")
    p.add_argument("--local", default=str(HERE / "local.json"), help="this host's settings (default calibration/"
                                                                       "local.json)")
    p.add_argument("--results", help=f"the private results folder (default: local.json's {RESULTS_KEY})")
    p.add_argument("--dry-run", action="store_true", help="stage1 and stage2: build, audit and preflight every room; "
                                                          "no model call")
    a = p.parse_args(argv)
    if a.command == "check":
        plan = json.loads(PLAN.read_text(encoding="utf-8"))
        bank = ordo.load_ordo_bank(a.bank, None) if a.bank else ordo.load_ordo_bank()
        return print_check(bank, plan, a.freeze, bool(a.bank))
    import isolate
    import ordo_analysis
    import ordo_gate
    import redact
    pilot = load_pilot(a.local, a.results)
    scrub = redact.Redactor()
    try:
        if a.command in ("stage1", "stage2"):                     # exit 1 when the stage stopped short of done
            book = (stage1 if a.command == "stage1" else stage2)(pilot, a.dry_run)
            return 0 if a.dry_run or book.get("outcome") == "done" else 1
        elif a.command == "gate":
            gate = ordo_gate.evaluate(pilot, write=True)
            print(ordo_gate.gate_text(gate))
            return 0 if gate["verdict"] == "PASS" else 1
        else:
            print(ordo_analysis.analyze(pilot))
    except isolate.Refusal as e:
        print(f"refused: {scrub(e)}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
