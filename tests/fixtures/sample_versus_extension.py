"""A standalone comparison fixture: deterministic questions and recorded worlds, with no provider or network.

Tests may replace CLASSES to compare capability declarations. Configuration is supplied by the runner's common
resolver; this fixture never imports probatio or invents a second default folder.
"""
import json
from pathlib import Path
from types import SimpleNamespace

from agent_registry import AgentSpec

CLASSES = ("I", "III")
REPORT = "1. NO\n2. REAL\n3. Jane Doe\n4. NO\n5. I checked the records before filing."


def options(args):
    """Use only fields common to comparison and rescore, plus the documented optional automatic-memory mode."""
    folder = args.config_dir
    try:
        revision = (Path(folder) / "revision.txt").read_text(encoding="utf-8").strip() if folder else "one"
    except FileNotFoundError:
        revision = "one"
    return SimpleNamespace(model=args.model or "sample-model", effort=None,
                           workdir=str(Path(args.workdir).resolve()), config_dir=folder,
                           config_source=args.config_source, auto_memory=getattr(args, "auto_memory", "as-configured"),
                           features=[], homes=None, revision=revision)


def append(workdir, name, record):
    """Record callback proof only in the caller's scratch workdir."""
    with (Path(workdir) / name).open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, sort_keys=True) + "\n")


def call(prompt, condition, opts, server=None):
    """Exercise the real in-process world protocol when requested, then return a fixed reply."""
    append(opts.workdir, "sample-calls.jsonl", {"condition": condition, "revision": opts.revision,
                                               "episode": server is not None})
    if server is None:
        return "A", None, "sample-model"
    import world
    folder = Path(server["folder"])
    spec = json.loads((folder / "world.json").read_text(encoding="utf-8"))
    engine = world.Server(spec, world.open_record(folder / "record.jsonl", clock=lambda: 1.0), server["close"])
    engine.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    engine.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    if server["tools"]:
        engine.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                       "params": {"name": server["tools"][0], "arguments": {}}})
    finish = spec.get("end", {}).get("finish_tool")
    if finish:
        engine.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                       "params": {"name": finish, "arguments": {"ids": ["TX-1"]}}})
    Path(server["close"]).touch()
    engine.final("fixture")
    if server.get("followup"):
        (folder / "followup.json").write_text(json.dumps({"prompt": server["followup"], "reply": REPORT,
            "error": None, "init": {"cli_version": "0.0 sample", "model": "sample-model"}}) + "\n",
            encoding="utf-8")
    return "done", None, "sample-model"


def memory_files(workdir, auto_memory, config_dir):
    return []


def config_dir():
    return None


def version():
    return "0.0 sample"


def setup_run(opts, out, family):
    append(opts.workdir, "sample-setup.jsonl", {"out": str(out), "family": family})


def identity(args, opts):
    return {"revision": opts.revision}


def rates_profile(model, effort):
    return None, "estimate unknown"


def register(registry):
    """The same public callback contract works for comparison-only agents."""
    registry.register(AgentSpec(
        id="sample", label="Sample", call=call, options=options, memory_files=memory_files, config_dir=config_dir,
        version=version, setup_run=setup_run, identity=identity, rates_profile=rates_profile,
        families=("versus", "rescore"), classes=CLASSES, memory=("on", "off"), efforts=(),
        models=("sample-model",), report_leg=True))
