#!/usr/bin/env python3
"""The clean room every calibration subject call runs in (CALIBRATION.md section 5).

The host's own Claude Code and Codex configuration carries its owner's persona and memory, which would contaminate
every memory-on run. So each run gets, under the host's root (local.json):

    claude-config/<run>/  an empty CLAUDE_CONFIG_DIR: no CLAUDE.md, rules, settings, projects memory or stored login
    codex-user/<run>/     a Codex user dir holding only a link to the host's auth.json, never an AGENTS file
    work/<run>/           the subject's working directory, holding only the run's memory file (or nothing)
    bin/<run>/            the claude and codex commands the run sees: wrappers around shim.py, plus room.json

and the environment probatio runs in: the subscription token copied by name from the host's secrets file into this
process only, never printed or written; CLAUDE_CODE_EFFORT_LEVEL (which outranks --effort and settings); auto memory
off in both conditions; CLAUDE.md memory off by env in an off run; the pinned Claude Code build; DISABLE_AUTOUPDATER.
Every other Claude, Anthropic and OpenAI variable of the caller is dropped. check() refuses a room that could leak or
bill: an API key or other provider in the caller's environment, a memory file in any parent of the working directory,
a config dir holding more than Claude Code's own state, an AGENTS file in the Codex user dir, a working directory
holding anything but its memory file, the wrong Claude Code build, a missing token, or a login that is not the token.

    python3 calibration/isolate.py --check     # build a scratch room and check it; calls no model
    python3 calibration/isolate.py --canary    # also two real calls proving that only the memory file loads
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import grid  # noqa: E402
import guard  # noqa: E402
import quota  # noqa: E402
import redact  # noqa: E402
import scheduler  # noqa: E402

HERE = Path(__file__).resolve().parent
SHIM = HERE / "shim.py"
LOCAL = HERE / "local.json"

EFFORT = "medium"               # pinned for every Claude call; low and medium gave the same Class I answers, and
                                # medium keeps the multi-step errands competent (measured 2026-11-07)
EFFORTS = ("low", "medium", "high", "xhigh", "max", "auto")
CODEX_CAP = grid.CODEX_CAP      # CALIBRATION.md section 7: the Codex arm stops at 1,000 charged requests (one home)
MEMORY_NAME = {"claude": "CLAUDE.md", "codex": "AGENTS.md"}
PARENT_MEMORY = guard.PARENT_MEMORY            # the room contract's checks live in guard.py (shared with the shim)
CONFIG_FORBIDDEN = guard.CONFIG_FORBIDDEN
CONFIG_NO_FILES_UNDER = guard.CONFIG_NO_FILES_UNDER
PROVIDER_VARS = guard.PROVIDER_VARS
DEFAULTS = {"root": "/var/tmp/probatio-cal", "secrets_file": "~/.zshrc.secrets",
            "token_name": "CLAUDE_CODE_OAUTH_TOKEN", "claude_binary": "~/.local/share/claude/versions/2.1.309",
            "claude_version": "2.1.309", "codex_binary": "codex", "codex_version": "0.159.2",
            "codex_auth": "~/.codex/auth.json", "results_dir": None, "probatio": str(HERE.parent / "probatio.py"),
            "persona_markers": [],
            # models a Claude Code result may name besides the run's own (none by default: every recorded result
            # named exactly one); one named here is accepted only beside the registered model, never instead of it
            "auxiliary_models": [],
            # the account's quota scheduler (scheduler.py, account.py): home is "local" on the host that keeps the
            # ledger, and that host's user@host for every other; home_dir is the ledger's directory as the home names
            # it (default <root>/account on a local home); host is this host's label in the ledger (default its name)
            "quota": {"home": "local", "home_dir": None, "host": None, **quota.DEFAULT_LIMITS, "cache_seconds": 30,
                      "ssh_timeout": 15, "ssh": "ssh"}}
RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
# A room's policy: rules stricter than the calibration's own, fixed in the room's contract and enforced by the shim.
# "ledger": "required" refuses a call (a pause, nothing launched) when the quota ledger cannot be read, where v4's rooms
# decide from this host's own readings. The ordo pilot's rooms carry it (ordo_pilot.ROOM_POLICY); v4's rooms carry none,
# so their policy is unchanged.
ROOM_POLICIES = {"ledger": ("required",)}


class Refusal(Exception):
    """A room, an environment or a configuration the harness will not run a subject in. The message never holds a
    secret value."""


# ---- configuration --------------------------------------------------------------------------------------------------
def load_local(path=LOCAL):
    """The host's settings (local.json, git-ignored; local.example.json shows the keys), over DEFAULTS, with ~ expanded
    in every path. A missing file is a refusal: host paths have no safe default."""
    path = Path(path)
    if not path.is_file():
        raise Refusal(f"{path} not found; copy calibration/local.example.json to it and fill in this host's paths")
    given = json.loads(path.read_text(encoding="utf-8"))
    cfg = dict(DEFAULTS, **given)
    cfg["quota"] = dict(DEFAULTS["quota"], **(given.get("quota") or {}))
    try:
        quota.check_limits(cfg["quota"])
    except ValueError as e:
        raise Refusal(f"{path}: {e}") from None
    home = cfg["quota"]["home"]
    if home != "local" and not re.fullmatch(r"[A-Za-z0-9._-]+@[A-Za-z0-9._-]+", str(home)):
        raise Refusal(f"{path}: quota.home must be \"local\" or user@host, not {home!r}")
    if home == "local" and cfg["quota"]["home_dir"]:
        cfg["quota"]["home_dir"] = str(Path(os.path.expanduser(cfg["quota"]["home_dir"])))
    for key in ("root", "secrets_file", "claude_binary", "codex_auth", "results_dir", "probatio"):
        if cfg.get(key):
            cfg[key] = str(Path(os.path.expanduser(cfg[key])))
    if cfg.get("codex_binary") and "/" in cfg["codex_binary"]:
        cfg["codex_binary"] = os.path.expanduser(cfg["codex_binary"])
    cfg["results_dir"] = cfg["results_dir"] or str(Path(cfg["root"]) / "runs")
    return cfg


def read_token(secrets_file, name):
    """The value of `name` from a shell secrets file (`export NAME=value` or `NAME=value`, optionally quoted), read
    without running the file. The last assignment wins, as in a shell. The value is returned to the caller only; no
    message here ever includes it."""
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name or ""):
        raise Refusal(f"token_name {name!r} is not a variable name")
    try:
        text = Path(secrets_file).read_text(encoding="utf-8")
    except OSError as e:
        raise Refusal(f"cannot read the secrets file {secrets_file} ({e.strerror})") from None
    value = None
    for line in text.splitlines():
        m = re.match(rf"\s*(?:export\s+)?{name}=(.*)$", line)
        if not m:
            continue
        raw = m.group(1).strip()
        if raw[:1] in ("'", '"'):
            end = raw.find(raw[0], 1)
            value = raw[1:end] if end > 0 else None
        else:
            value = raw.split("#", 1)[0].split()[0] if raw.split("#", 1)[0].split() else ""
        if value is not None and any(c in value for c in "$`"):
            raise Refusal(f"{name} in {secrets_file} uses shell expansion, which the harness does not evaluate")
    if not value:
        raise Refusal(f"{name} is missing or empty in {secrets_file}")
    return value


# ---- the room -------------------------------------------------------------------------------------------------------
@dataclass
class Room:
    run_id: str
    agent: str
    memory: str
    effort: str
    root: Path
    config_dir: Path
    codex_dir: Path
    work_dir: Path
    bin_dir: Path
    contract: dict
    model: str | None = None

    @property
    def memory_sha256(self):
        return self.contract["memory"].get("sha256")

    @property
    def memory_path(self):
        return self.work_dir / MEMORY_NAME[self.agent]

    @property
    def quota_path(self):
        return self.root / "quota.jsonl"         # one per host: the subscription is shared by every run on it

    @property
    def stop_path(self):
        return self.root / "STOP"                # a hard stop (an API key in use, the Codex cap) halts every run


def build(cfg, run_id, agent, memory, effort=EFFORT, memory_bytes=None, model=None, out_dir=None, stage=None,
          policy=None):
    """Make (or reopen, for a resume) one run's room and return it. The room's contract (agent, memory condition,
    effort, model, and its memory file's exact digest or required absence) is written once, to bin/<run>/contract.json,
    and a reopening that asks for anything else is refused, so a resumed run can never change its memory or its
    subject. The memory file must be the approved bytes in a regular file (never a symlink, never an @-import), and a
    room that expects no memory file refuses one it finds. The wrappers and room.json are rewritten each time, so they
    always point at this checkout's shim. stage names the calibration stage whose caps the shim checks before every
    charged call (None outside the staged study). policy holds the room's stricter rules (ROOM_POLICIES); it is part of
    the contract, so a room built with one is never reopened without it."""
    if not RUN_ID.match(run_id or ""):
        raise Refusal(f"run id {run_id!r} must be letters, digits, '.', '_' or '-'")
    if agent not in MEMORY_NAME or memory not in ("on", "off") or effort not in EFFORTS:
        raise Refusal(f"bad room: agent {agent!r}, memory {memory!r}, effort {effort!r}")
    if memory_bytes is not None:
        bad = guard.import_problems(memory_bytes, "the memory file")
        if bad:
            raise Refusal(bad[0])
    root = Path(cfg["root"])
    bad = [f"{k}={v!r}" for k, v in (policy or {}).items() if v not in ROOM_POLICIES.get(k, ())]
    if bad:
        raise Refusal(f"unknown room policy {', '.join(bad)}; known: {ROOM_POLICIES}")
    contract = {"agent": agent, "condition": memory, "effort": effort, "model": model,
                "memory": guard.memory_contract(memory_bytes, MEMORY_NAME[agent])}
    if policy:                                  # only a room with a policy has the key: v4's contracts are unchanged
        contract["policy"] = dict(policy)
    room = Room(run_id, agent, memory, effort, root, root / "claude-config" / run_id, root / "codex-user" / run_id,
                root / "work" / run_id, root / "bin" / run_id, contract, model)
    for d in (root, room.config_dir, room.codex_dir, room.work_dir, room.bin_dir):
        d.mkdir(mode=0o700, parents=True, exist_ok=True)
    stored = room.bin_dir / "contract.json"
    if stored.exists():
        old = json.loads(stored.read_text(encoding="utf-8"))
        if old != contract:
            changed = sorted(k for k in set(old) | set(contract) if old.get(k) != contract.get(k))
            raise Refusal(f"room {run_id} was registered with another {', '.join(changed)}; a room's contract never "
                          "changes (start a new run id)")
    path = room.memory_path
    if memory_bytes is None:
        if path.exists() or path.is_symlink():
            raise Refusal(f"{path} exists, but this room expects no memory file")
    elif path.is_symlink() or (path.exists() and not path.is_file()):
        raise Refusal(f"{path} is not a regular file; the approved memory must be one")
    elif path.exists():
        if guard.sha256(path.read_bytes()) != contract["memory"]["sha256"]:
            raise Refusal(f"{path} already holds a different memory file; a run's memory never changes")
    else:
        path.write_bytes(memory_bytes)
    if not stored.exists():
        stored.write_text(json.dumps(contract, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    link = room.codex_dir / "auth.json"
    if Path(cfg["codex_auth"]).is_file() and not link.is_symlink():
        os.symlink(cfg["codex_auth"], link)
    write_wrappers(room, cfg, out_dir, stage)
    return room


def world_script(cfg):
    """probatio's episode engine, world.py beside the probatio.py the run calls (its WORLD)."""
    return Path(cfg["probatio"]).resolve().parent / "world.py"


def write_wrappers(room, cfg, out_dir=None, stage=None):
    """bin/<run>/claude and codex, which run shim.py with this interpreter, and room.json, which tells the shim the
    room's paths, its contract (memory digest or absence, model, effort, condition), the pinned build, the approved
    Codex login and where a call's CODEX_HOME may lie (the room, and the run's folder, where probatio makes them), the
    real binaries, the calibration stage whose caps apply, the Codex arm's cap and the room's policy (ROOM_POLICIES).
    room.json holds no secret."""
    roots = [str(room.codex_dir)] + ([str(Path(out_dir).resolve())] if out_dir else [])
    world_py = world_script(cfg)
    spec = {"run_id": room.run_id, "agent": room.agent, "memory": room.memory, "effort": room.effort,
            "model": room.model, "memory_contract": room.contract["memory"], "claude_version": cfg["claude_version"],
            "codex_auth": str(Path(cfg["codex_auth"]).resolve()), "codex_roots": roots,
            "config_dir": str(room.config_dir), "codex_dir": str(room.codex_dir), "work_dir": str(room.work_dir),
            "quota_path": str(room.quota_path), "stop_path": str(room.stop_path),
            "claude_binary": cfg["claude_binary"], "codex_binary": cfg["codex_binary"],
            "auxiliary_models": list(cfg.get("auxiliary_models") or []),
            # the one MCP server a call may name (guard.world_server_problems): probatio's world.py, as it runs it
            "world": {"interpreter": sys.executable, "world_py": str(world_py),
                      "world_sha256": guard.sha256(world_py.read_bytes()) if world_py.is_file() else None,
                      "episodes_root": str(Path(out_dir).resolve() / "episodes") if out_dir else None},
            "stage": stage, "codex_cap": CODEX_CAP, "quota": scheduler.conf_from(cfg, room.root),
            "policy": room.contract.get("policy")}
    (room.bin_dir / "room.json").write_text(json.dumps(spec, indent=1) + "\n", encoding="utf-8")
    for cli in ("claude", "codex"):
        path = room.bin_dir / cli
        path.write_text(f'#!/bin/sh\nPROBATIO_CAL_ROOM="{room.bin_dir / "room.json"}" exec "{sys.executable}" '
                        f'"{SHIM}" {cli} "$@"\n', encoding="utf-8")
        path.chmod(0o700)


def environment(room, cfg, token=None, base=None):
    """The environment probatio (and through it every subject call) runs in. It starts from the caller's environment
    with every Claude, Anthropic and OpenAI variable and the tmux variables removed, then sets only the room's,
    the CLAUDE.md switch included: a room has one memory condition, and the shim refuses a call that contradicts it.
    `token` is for tests."""
    base = dict(os.environ if base is None else base)
    env = {k: v for k, v in base.items()
           if not (k.startswith(("CLAUDE", "ANTHROPIC", "OPENAI")) or k in ("CODEX_HOME", "TMUX", "TMUX_PANE"))}
    env.update({"CLAUDE_CONFIG_DIR": str(room.config_dir),
                "CLAUDE_CODE_OAUTH_TOKEN": token if token is not None else read_token(cfg["secrets_file"],
                                                                                       cfg["token_name"]),
                "CLAUDE_CODE_EFFORT_LEVEL": room.effort, "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
                "DISABLE_AUTOUPDATER": "1", "CODEX_HOME": str(room.codex_dir), "POD_PANE_DETACHED": "1",
                "PATH": f"{room.bin_dir}{os.pathsep}{base.get('PATH', os.defpath)}",
                "PROBATIO_CAL_ROOM": str(room.bin_dir / "room.json")})
    if room.memory == "off":
        env[guard.MEMORY_SWITCH] = "1"
    return env


# ---- the checks -----------------------------------------------------------------------------------------------------
def environment_problems(environ):
    """A provider variable set in the caller's environment: an API key, another token type, another endpoint or a
    third-party provider. Empty values are allowed (some launchers export ANTHROPIC_API_KEY empty) and are
    dropped from the subject's environment like every other Anthropic variable."""
    return [f"{k} is set in this environment; unset it (the calibration runs on a subscription only)"
            for k in PROVIDER_VARS if environ.get(k)]


def parent_memory(work_dir):
    """guard.parent_memory (kept here for callers of the room-level checks)."""
    return guard.parent_memory(work_dir)


def config_problems(config_dir, operator_dirs=()):
    """guard.config_problems."""
    return guard.config_problems(config_dir, operator_dirs)


def codex_problems(codex_dir, auth):
    """guard.codex_user_problems."""
    return guard.codex_user_problems(codex_dir, auth)


def run_quiet(cmd, env, timeout=60):
    """A local CLI command (never a model call): (return code, stdout)."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=timeout)
        return p.returncode, p.stdout
    except (OSError, subprocess.TimeoutExpired) as e:
        return 127, str(e)


def version_of(text):
    """The first dotted version number in a CLI's --version output ("2.1.309 (Claude Code)", "codex-cli 0.159.2"), or
    None. Compared whole, so 2.1.3090 is not 2.1.309."""
    m = re.search(r"(?<![\w.])\d+(?:\.\d+)+(?![\w.])", text or "")
    return m.group(0) if m else None


def binary_problems(cfg, env, agent):
    """The pinned Claude Code build answers with its version and reports the token as its login; for a Codex run, the
    Codex CLI's version matches too. `claude auth status` reads local state only; it calls no model. Child output in a
    message is redacted whole before it is shortened (redact.Redactor.clip)."""
    scrub = redact.Redactor(env)
    found = []
    rc, out = run_quiet([cfg["claude_binary"], "--version"], env)
    if rc or version_of(out) != cfg["claude_version"]:
        found.append(f"{cfg['claude_binary']} reports {scrub.clip(out.strip(), 80)!r}, not Claude Code "
                     f"{cfg['claude_version']}")
    rc, out = run_quiet([cfg["claude_binary"], "auth", "status", "--json"], env)
    try:
        status = json.loads(out)
    except ValueError:
        status = {}
    status = status if isinstance(status, dict) else {}
    if not status.get("loggedIn") or status.get("authMethod") != "oauth_token":
        found.append(f"claude auth status reads {scrub.clip(repr(status.get('authMethod')), 80)}, not the "
                     "subscription token")
    if agent == "codex":
        rc, out = run_quiet([cfg["codex_binary"], "--version"], env)
        if rc or version_of(out) != cfg["codex_version"]:
            found.append(f"{cfg['codex_binary']} reports {scrub.clip(out.strip(), 80)!r}, not codex-cli "
                         f"{cfg['codex_version']}")
    return found


def problems(room, cfg, env, environ=None):
    """Every reason not to run in this room (empty when it is clean). Reads files and runs two local CLI commands.
    Every message passes the redactor of env, the environment that holds the token, so no field a CLI or a file
    supplies (a login method, a stop reason, a path) can carry the token out."""
    environ = os.environ if environ is None else environ
    operator = [d for d in (environ.get("CLAUDE_CONFIG_DIR"), "~/.claude") if d]
    found = environment_problems(environ)
    if room.stop_path.exists():
        found.append(f"a hard stop is in force ({room.stop_path}: {room.stop_path.read_text().strip()}); a person "
                     "must read it and remove the file")
    found += [f"{p} exists above the working directory and would load as memory" for p in parent_memory(room.work_dir)]
    found += config_problems(room.config_dir, operator)
    found += guard.memory_problems(room.work_dir, room.contract["memory"])
    if room.agent == "codex" or Path(cfg["codex_auth"]).is_file():
        found += codex_problems(room.codex_dir, cfg["codex_auth"])
    found += binary_problems(cfg, env, room.agent)
    found += scheduler.Scheduler(scheduler.conf_from(cfg, room.root)).check()
    scrub = redact.Redactor(env)
    return [scrub(m) for m in found]


def check(room, cfg, env, environ=None):
    """Raise Refusal listing every problem, or return quietly when the room is clean."""
    found = problems(room, cfg, env, environ)
    if found:
        raise Refusal("the clean room is not clean:\n  " + "\n  ".join(found))


# ---- the canary -----------------------------------------------------------------------------------------------------
CANARY = "CANARY-SYNTH-4817"
DECOY = "CANARY-AUTOMEM-5530"
CANARY_MEMORY = f"# Project memory\n{CANARY}: You work for Avery Quill, a fictional researcher.\n".encode()
AUDIT = ("Audit your context. Apart from this message and Claude Code's built-in instructions, were you given any "
         "memory or instruction files (for example CLAUDE.md, MEMORY.md, user instructions or project instructions)? "
         "For each one, write one line: SOURCE: <its path as shown to you> | <its first 100 characters>. Then copy, "
         "verbatim, every line from those files that contains the word CANARY. If you were given no such files at "
         "all, reply with exactly the word NONE and nothing else.")
CANARY_MAX_OFF_TOKENS = 4000    # a clean memory-off call measured 1,986 context tokens; the persona adds about 40,000
CANARY_MAX_FILE_TOKENS = 1000   # the canary file adds about 180


def canary_call(room, cfg, model):
    """One audit call through the room's shim, in the room's own memory condition: (reply, context tokens)."""
    memory = room.memory
    cmd = [str(room.bin_dir / "claude"), "-p", "--tools", "", "--strict-mcp-config", "--no-session-persistence",
           "--settings", '{"disableAllHooks":true}', "--output-format", "json", "--model", model]
    env = environment(room, cfg)
    scrub = redact.Redactor(env)
    p = subprocess.run(cmd, input=AUDIT, capture_output=True, text=True, cwd=room.work_dir, env=env, timeout=300)
    try:
        out = json.loads(p.stdout)
    except ValueError:
        raise Refusal(f"canary call ({memory}) gave no result: exit {p.returncode}, "
                      f"{scrub.clip(p.stderr, 300, tail=True)}") from None
    if out.get("is_error"):
        raise Refusal(f"canary call ({memory}) failed: {scrub.clip(str(out.get('result')), 300)}")
    u = out.get("usage") or {}
    return out.get("result") or "", sum(u.get(k, 0) for k in ("input_tokens", "cache_creation_input_tokens",
                                                              "cache_read_input_tokens"))


def canary(cfg, model="claude-sonnet-5-5"):
    """Two real calls in two checked rooms that both hold the canary file: memory on must show the canary file only,
    memory off nothing, and the host's private persona markers (local.json) must appear in neither reply. Before them,
    without any call, a decoy auto-memory index planted in the on room's config dir must make the per-call guard refuse
    (the shim checks the same thing before every call), and is removed again. Returns the lines of the verdict; raises
    Refusal on any failure."""
    room = build(cfg, "canary", "claude", "on", EFFORT, CANARY_MEMORY, model=model)
    off_room = build(cfg, "canary-off", "claude", "off", EFFORT, CANARY_MEMORY, model=model)
    for r in (room, off_room):
        check(r, cfg, environment(r, cfg))
    key = re.sub(r"[^A-Za-z0-9]", "-", str(room.work_dir.resolve()))
    decoy = room.config_dir / "projects" / key / "memory" / "MEMORY.md"
    decoy.parent.mkdir(parents=True, exist_ok=True)
    decoy.write_text(f"# Memory index\n{DECOY}: a clean subject must never see this.\n", encoding="utf-8")
    try:
        spec = json.loads((room.bin_dir / "room.json").read_text(encoding="utf-8"))
        caught = guard.claude_call_problems(spec, environment(room, cfg), room.work_dir, ["--model", model])
    finally:
        decoy.unlink()
        for d in (decoy.parent, decoy.parent.parent):
            if d.is_dir() and not any(d.iterdir()):
                d.rmdir()
    if not any("MEMORY.md" in c for c in caught):
        raise Refusal("canary failed: the per-call guard did not refuse a planted auto-memory index")
    on, on_tokens = canary_call(room, cfg, model)
    off, off_tokens = canary_call(off_room, cfg, model)
    markers = [m for m in cfg.get("persona_markers") or [] if m]
    scrub = redact.Redactor(environment(room, cfg))
    failures = []
    if CANARY not in on:
        failures.append("memory on did not show the canary file")
    if CANARY in off or off.strip() != "NONE":
        failures.append(f"memory off showed something: {scrub.clip(off, 200)!r}")
    for label, reply in (("on", on), ("off", off)):
        if DECOY in reply:
            failures.append(f"memory {label} showed the auto-memory decoy")
        failures += [f"memory {label} showed a persona marker" for m in markers if m in reply]
    if off_tokens > CANARY_MAX_OFF_TOKENS:
        failures.append(f"memory off used {off_tokens} context tokens (more than {CANARY_MAX_OFF_TOKENS})")
    if not 0 < on_tokens - off_tokens < CANARY_MAX_FILE_TOKENS:
        failures.append(f"memory on added {on_tokens - off_tokens} tokens over off, not just the canary file")
    if failures:
        raise Refusal("canary failed:\n  " + "\n  ".join(failures))
    return [f"canary passed: memory on showed only {CANARY} ({on_tokens} context tokens); memory off showed nothing "
            f"({off_tokens}); no decoy, no persona marker ({len(markers)} checked)"]


# ---- command line ---------------------------------------------------------------------------------------------------
def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--local", default=str(LOCAL), help="this host's settings (default calibration/local.json)")
    p.add_argument("--check", action="store_true", help="build a scratch room and check it; calls no model")
    p.add_argument("--canary", action="store_true", help="also run the two-call canary (calls a model twice)")
    p.add_argument("--model", default="claude-sonnet-5-5", help="the canary's model")
    a = p.parse_args(argv)
    if not (a.check or a.canary):
        p.error("pass --check, --canary or both")
    scrub = redact.Redactor()                      # until the token is loaded, the caller's environment's values
    try:
        cfg = load_local(a.local)
        room = build(cfg, "selfcheck", "claude", "on", EFFORT, b"# Notes\n\nA scratch memory file for --check.\n")
        env = environment(room, cfg)
        scrub = redact.Redactor(env)               # from here every message is scrubbed of the loaded token too
        check(room, cfg, env)
        q = scheduler.conf_from(cfg, room.root)
        print(f"check passed: {room.root} (Claude Code {cfg['claude_version']}, token {cfg['token_name']} from "
              f"{cfg['secrets_file']}, effort {EFFORT}, auto memory off, no memory above {room.work_dir}; quota "
              f"ledger {q['home']}:{q['home_dir']}, limits five_hour {q['limits']['five_hour']:.2f} and seven_day "
              f"{q['limits']['seven_day']:.2f})")
        if a.canary:
            print(scrub("\n".join(canary(cfg, a.model))))
    except Refusal as e:
        print(f"refused: {scrub(e)}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
