"""A deterministic, in-process contract fixture copied beside a scratch runner by integration tests."""
import json
from pathlib import Path
from types import SimpleNamespace

from agent_registry import AgentSpec


def options(args):
    """Read a revision only from the test's chosen config folder, never from environment routing."""
    workdir = Path(args.workdir).resolve()
    folder = args.config_dir
    try:
        revision = (Path(folder) / "revision.txt").read_text(encoding="utf-8").strip() if folder else "one"
    except FileNotFoundError:
        revision = "one"
    return SimpleNamespace(model=args.model or "sample-model", effort=None, workdir=str(workdir),
                           config_dir=folder, config_source=args.config_source, features=[], homes=None,
                           auto_memory=getattr(args, "auto_memory", "as-configured"), revision=revision)


def append(workdir, name, record):
    """Keep proof of fixture callbacks under the explicit scratch workdir."""
    with (Path(workdir) / name).open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, sort_keys=True) + "\n")


def call(prompt, condition, opts, server=None):
    """A question gets a deterministic reply, without a subprocess, model or service."""
    if server is not None:
        raise AssertionError("the sample fixture serves questions only")
    append(opts.workdir, "sample-calls.jsonl", {"condition": condition, "revision": opts.revision})
    return "A", None, "sample-model"


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
    """Declare one limited fixture through the public extension interface."""
    registry.register(AgentSpec(
        id="sample", label="Sample", call=call, options=options, memory_files=memory_files, config_dir=config_dir,
        version=version, setup_run=setup_run, identity=identity, rates_profile=rates_profile,
        families=("v4", "rescore"), classes=("I",), memory=("on", "off"), efforts=(), models=("sample-model",)))
