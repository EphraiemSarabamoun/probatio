"""Agent contracts and the one optional extension file, without importing the runner.

Callbacks keep agent-specific execution at the runner's edges. Capability facts are immutable declarations,
so planning, command-line choices and the form can all read the same registered specification.
"""
import contextlib
import importlib.util
import io
import inspect
import json
import math
import os
import re
import sys
import subprocess
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, fields
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from typing import Callable, Optional, Tuple


FAMILIES = ("v3", "v4", "cross", "compare", "versus", "demo", "rescore")
CLASSES = ("I", "II", "III")
MEMORY = ("on", "off", "both")
EFFORTS = ("low", "medium", "high", "xhigh", "max")
EXTENSION_FILE = "probatio_ext.py"
ERROR_CHARS = 512
ID = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")
ENVIRONMENT = re.compile(r"[A-Z_][A-Z0-9_]*\Z")
# Unicode's Default_Ignorable_Code_Point set (DerivedCoreProperties.txt, Unicode 15.1), which the Hangul fillers
# U+115F, U+1160, U+3164 and U+FFA0 belong to, and U+2800 BRAILLE PATTERN BLANK, a blank outside it; inclusive ranges.
INVISIBLE = ((0x00AD, 0x00AD), (0x034F, 0x034F), (0x061C, 0x061C), (0x115F, 0x1160), (0x17B4, 0x17B5),
             (0x180B, 0x180F), (0x200B, 0x200F), (0x202A, 0x202E), (0x2060, 0x206F), (0x2800, 0x2800),
             (0x3164, 0x3164), (0xFE00, 0xFE0F), (0xFEFF, 0xFEFF), (0xFFA0, 0xFFA0), (0xFFF0, 0xFFF8),
             (0x1BCA0, 0x1BCA3), (0x1D173, 0x1D17A), (0xE0000, 0xE0FFF))


def invisible(ch):
    """Whether a character is one of INVISIBLE's."""
    return any(lo <= ord(ch) <= hi for lo, hi in INVISIBLE)


class RegistryError(ValueError):
    """A contract or loading refusal; the caller prints reason with path and exits with status 2."""
    def __init__(self, reason, path=None):
        super().__init__(reason)
        self.reason = reason
        self.path = path


class CallbackError(ValueError):
    """An adapter failed or broke its return contract; presentation belongs to the runner."""
    def __init__(self, spec, callback, reason, kind="exception"):
        super().__init__(reason)
        self.agent_id, self.agent_label = spec.id, spec.label
        self.callback, self.reason, self.kind = callback, reason, kind


# Bind representative arguments, never execute callbacks during registration. The alternate forms are part
# of the public contract: callers may pass server positionally or by keyword and optional hooks by keyword.
_ARG = object()
CALLBACK_FORMS = {
    "call": (((_ARG, _ARG, _ARG), {}), ((_ARG, _ARG, _ARG, _ARG), {}),
             ((_ARG, _ARG, _ARG), {"server": _ARG})),
    "options": (((_ARG,), {}),),
    "memory_files": (((_ARG, _ARG, _ARG), {}),),
    "config_dir": (((), {}),),
    "version": (((), {}),),
    "setup_run": (((_ARG, _ARG, _ARG), {}),),
    "identity": (((_ARG, _ARG), {}),),
    "rates_profile": (((_ARG, _ARG), {}),),
    "memory_candidates": (((_ARG,), {}), ((), {"in_use": _ARG})),
    "preflight_lines": (((_ARG,), {}), ((), {"opts": _ARG})),
    "report_lines": (((_ARG,), {}), ((), {"meta": _ARG})),
    "extra_requests": (((_ARG,), {}), ((), {"calls": _ARG})),
}


def _callback_signature(name, function):
    """Refuse signatures that cannot accept every documented invocation form."""
    if not callable(function):
        raise RegistryError(f"{name} must be callable")
    try:
        signature = inspect.signature(function)
        for args, kwargs in CALLBACK_FORMS[name]:
            signature.bind(*args, **kwargs)
    except KeyboardInterrupt:
        raise
    except BaseException:
        raise RegistryError(f"{name} must accept its documented arguments") from None


def _path(value):
    """Accept text filesystem paths only, normalizing them without filesystem access."""
    if not isinstance(value, (str, os.PathLike)):
        raise ValueError("expected a path")
    return Path(value)


def _identity(value):
    """Identity is a finite JSON object, with string keys and JSON collections rather than Python objects."""
    def valid(item):
        if isinstance(item, dict):
            return all(isinstance(k, str) and valid(v) for k, v in item.items())
        if isinstance(item, list):
            return all(valid(v) for v in item)
        return item is None or type(item) in (str, int, float, bool)
    if not isinstance(value, dict) or not valid(value):
        raise ValueError("expected a finite JSON object")
    return json.loads(json.dumps(value, allow_nan=False))


OPTION_FIELDS = ("model", "effort", "workdir", "config_dir", "config_source", "features")


def _options(value):
    """Read the adapter's documented fields and public extra state into a runner-owned namespace.

    Frozen objects and named tuples are valid callback results. Read descriptors here, inside the checked
    boundary; the runner can then set its own config, effort, quota and per-call fields without writing to them.
    Extra state belongs to the adapter's later hooks; the runner does not mutate its contents.
    """
    names = set(OPTION_FIELDS) | {name for name in dir(value) if not name.startswith("_")}
    result = SimpleNamespace(**{name: getattr(value, name) for name in sorted(names)})
    if (not (result.model is None or isinstance(result.model, str))
            or not (result.effort is None or isinstance(result.effort, str))
            or not isinstance(result.workdir, str)
            or not (result.config_dir is None or isinstance(result.config_dir, (str, os.PathLike)))
            or not isinstance(result.config_source, str)
            or not isinstance(result.features, (list, tuple))
            or not all(isinstance(item, str) for item in result.features)):
        raise ValueError("invalid options return value")
    if result.config_dir is not None:
        _path(result.config_dir)
    result.features = list(result.features)
    return result


def _memory_candidate(value):
    """Config suggestions carry the exact path and optional file metadata consumed by the CLI and form."""
    if not isinstance(value, dict) or not isinstance(value.get("path"), str) or "claude_md" not in value:
        return False
    metadata = value["claude_md"]
    return metadata is None or (isinstance(metadata, dict) and type(metadata.get("bytes")) is int
                                and _seconds(metadata["bytes"]) and "title" in metadata
                                and (metadata["title"] is None or isinstance(metadata["title"], str)))


def _callback_result(name, value):
    """Validate each callback at its shared boundary; path hooks return normalized Paths."""
    optional_text = lambda item: item is None or isinstance(item, str)
    sequence = lambda item: isinstance(item, (list, tuple))
    if name == "call":
        if sequence(value) and len(value) == 3 and all(optional_text(item) for item in value):
            return tuple(value)
    elif name == "options":
        return _options(value)
    elif name == "version":
        if isinstance(value, str):
            return value
    elif name == "memory_files":
        if sequence(value):
            return [_path(item) for item in value]
    elif name == "config_dir":
        return None if value is None else _path(value)
    elif name == "identity":
        return _identity(value)
    elif name == "setup_run":
        if value is None:
            return value
    elif name == "rates_profile":
        if (sequence(value) and len(value) == 2 and (value[0] is None or type(value[0]) is str)
                and isinstance(value[1], str)):
            return tuple(value)
    elif name == "memory_candidates":
        if isinstance(value, list) and all(_memory_candidate(item) for item in value):
            return [dict(item, claude_md=dict(item["claude_md"]) if item["claude_md"] is not None else None)
                    for item in value]
    elif name in ("preflight_lines", "report_lines"):
        if isinstance(value, list) and all(isinstance(item, str) for item in value):
            return list(value)
    elif name == "extra_requests":
        if type(value) is int and value >= 0:
            return value
    raise ValueError(f"invalid {name} return value")


def invoke(spec, name, *args, function=None, preserve_errors=False, **kwargs):
    """Invoke one registered hook, separating adapter exceptions from invalid return values.

    The runner decides whether a CallbackError is a setup refusal or a recorded failed call. A call timeout keeps
    its original type so the existing ask path can retain its timeout message. Ctrl-C always interrupts. The
    runner may preserve native callback exceptions for its original built-in spec objects, whose refusal text
    predates this boundary; return validation still applies. Callbacks return data rather than printing it.
    """
    try:
        value = (getattr(spec, name) if function is None else function)(*args, **kwargs)
    except KeyboardInterrupt:
        raise
    except BaseException as error:
        if preserve_errors or (name == "call" and isinstance(error, subprocess.TimeoutExpired)):
            raise
        raise CallbackError(spec, name, _reason(error, str)) from None
    try:
        return _callback_result(name, value)
    except KeyboardInterrupt:
        raise
    except BaseException as error:
        raise CallbackError(spec, name, _reason(error, str), kind="shape") from None


def _no_candidates(in_use=None):
    """No extra config folders are offered unless the agent declares a search hook."""
    return []


def _no_preflight_lines(opts):
    """An agent without extra preflight details adds no lines."""
    return []


def _no_report_lines(meta):
    """An agent without extra report details adds no lines."""
    return []


def _no_requests(calls):
    """Ordinary calls need no extra requests beyond the plan."""
    return 0


def _visible(value, maximum):
    """Simple display text cannot contain terminal controls, hidden format characters or separators."""
    return (isinstance(value, str) and 0 < len(value) <= maximum and value == value.strip()
            and all(c == " " or (c.isprintable() and unicodedata.category(c)[0] not in "CZ" and not invisible(c))
                    for c in value))


def _choices(name, values, allowed=None, empty=False):
    """Facts are ordered tuples, with no ambiguous duplicate or undeclared capability."""
    if not isinstance(values, tuple) or (not values and not empty):
        raise RegistryError(f"{name} must be {'a' if empty else 'a nonempty'} tuple")
    if any(not _visible(value, 200) for value in values):
        raise RegistryError(f"{name} must contain display-safe strings")
    if len(set(values)) != len(values):
        raise RegistryError(f"{name} must not contain duplicates")
    if allowed is not None and any(value not in allowed for value in values):
        raise RegistryError(f"{name} contains an unsupported value")


def _seconds(value):
    """Timing facts must survive the numeric conversions used by estimates."""
    if value is None:
        return True
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value) and value >= 0
    except OverflowError:
        return False


@dataclass(frozen=True)
class AgentSpec:
    """One agent's executable hooks and supported choices.

    call(prompt, condition, opts, server=None) returns (reply, error, model); options(args) returns its namespace.
    memory_files(workdir, auto_memory, config_dir) returns paths; config_dir() returns a default Path or None;
    version() returns text; setup_run(opts, out, family) prepares an execution; identity(args, opts) returns a JSON
    dict. rates_profile(model, effort) returns (profile, note),
    where a None profile means no estimate. Optional memory_candidates(in_use), preflight_lines(opts) and
    report_lines(meta) return lists; extra_requests(calls) returns an integer. Callback signatures and return values
    are checked by signature binding without executing agent code; invoke validates each returned value.
    """
    id: str
    label: str
    call: Callable
    options: Callable
    memory_files: Callable
    config_dir: Callable
    version: Callable
    setup_run: Callable
    identity: Callable
    rates_profile: Callable
    families: Tuple[str, ...]
    classes: Tuple[str, ...]
    memory: Tuple[str, ...]
    efforts: Tuple[str, ...]
    models: Tuple[str, ...]
    config_variable: Optional[str] = None
    config_required: bool = False  # whether a recorded config folder must be a path rather than None
    legacy_seconds: Optional[Mapping] = None
    report_leg: bool = False
    reference_features: Tuple[str, ...] = ()  # writer-shape examples only; never facts about a launched run
    default_model_label: str = "default"
    time_note: str = "Time estimate unknown"  # presentation only; rates_profile decides whether an estimate exists
    memory_candidates: Callable = _no_candidates
    preflight_lines: Callable = _no_preflight_lines
    report_lines: Callable = _no_report_lines
    extra_requests: Callable = _no_requests

    def __post_init__(self):
        """Reject incomplete contracts, and copy mutable timing input before anyone registers the spec."""
        self.validate()
        if self.legacy_seconds is not None:
            object.__setattr__(self, "legacy_seconds", MappingProxyType(dict(self.legacy_seconds)))

    def freeze(self):
        """Keep registered mapping facts immutable even when extension setup supplied a later replacement."""
        if self.legacy_seconds is not None and not isinstance(self.legacy_seconds, MappingProxyType):
            object.__setattr__(self, "legacy_seconds", MappingProxyType(dict(self.legacy_seconds)))

    def validate(self):
        """Check again at registration and after extension registration, without invoking any callback."""
        if not isinstance(self.id, str) or ID.fullmatch(self.id) is None:
            raise RegistryError("id must be a lowercase identifier of at most 64 characters")
        if not _visible(self.label, 80):
            raise RegistryError("label must be display-safe text of at most 80 characters")
        for name, maximum in (("default_model_label", 80), ("time_note", 512)):
            if not _visible(getattr(self, name), maximum):
                raise RegistryError(f"{name} must be display-safe text of at most {maximum} characters")
        for name in CALLBACK_FORMS:
            _callback_signature(name, getattr(self, name))
        for name, allowed, empty in (("families", FAMILIES, False), ("classes", CLASSES, False),
                                     ("memory", MEMORY, False), ("efforts", EFFORTS, True), ("models", None, True)):
            _choices(name, getattr(self, name), allowed, empty)
        if any(model.startswith("-") or any(c.isspace() for c in model) for model in self.models):
            raise RegistryError("models must contain one-word names not starting with -")
        if self.config_variable is not None and (not isinstance(self.config_variable, str)
                                                 or ENVIRONMENT.fullmatch(self.config_variable) is None):
            raise RegistryError("config_variable must be an environment variable name or None")
        if not isinstance(self.report_leg, bool):
            raise RegistryError("report_leg must be a bool")
        if not isinstance(self.config_required, bool):
            raise RegistryError("config_required must be a bool")
        _choices("reference_features", self.reference_features, empty=True)
        if self.legacy_seconds is not None:
            if not isinstance(self.legacy_seconds, Mapping):
                raise RegistryError("legacy_seconds must be a mapping or None")
            for name, seconds in self.legacy_seconds.items():
                if not isinstance(name, str) or ID.fullmatch(name) is None:
                    raise RegistryError("legacy_seconds keys must be dataset identifiers")
                if not _seconds(seconds):
                    raise RegistryError("legacy_seconds values must be nonnegative finite numbers or None")


class Registry:
    """An insertion-ordered collection; built-ins and extensions use the same duplicate-id guard."""
    def __init__(self):
        self._specs = {}

    def register(self, spec):
        """Register one complete contract without replacing any previously registered agent."""
        if not isinstance(spec, AgentSpec):
            raise RegistryError("registered agents must be AgentSpec instances")
        spec.validate()
        if spec.id in self._specs:
            raise RegistryError(f"agent id '{spec.id}' is already registered")
        spec.freeze()
        self._specs[spec.id] = spec
        return spec

    def validate(self):
        """A registration function must leave every registered contract valid."""
        for name, spec in self._specs.items():
            if not isinstance(spec, AgentSpec) or name != spec.id:
                raise RegistryError("registry entries must match their AgentSpec ids")
            spec.validate()
            spec.freeze()

    def __getitem__(self, name):
        return self._specs[name]

    def get(self, name, default=None):
        return self._specs.get(name, default)

    def __iter__(self):
        return iter(self._specs)

    def __len__(self):
        return len(self._specs)

    def values(self):
        """Return a stable snapshot in registration order, not a mutable view of the registry."""
        return tuple(self._specs.values())


class _Quiet(io.TextIOBase):
    """Extension setup cannot print extra lines before the caller's one-line failure."""
    def write(self, text):
        return len(text)


def _reason(error, display):
    """Bound a sanitized explanation even when an extension exception has unusual text."""
    try:
        text = str(error)
    except KeyboardInterrupt:
        raise
    except BaseException:
        text = "exception could not be described"
    if not isinstance(error, RegistryError):
        text = f"{type(error).__name__}: {text}"
    shown = str(display(text))
    shown = "".join(c if c.isprintable() and unicodedata.category(c)[0] not in "CZ" else
                    c.encode("unicode_escape").decode("ascii") for c in shown)
    return shown if len(shown) <= ERROR_CHARS else shown[:ERROR_CHARS - 3] + "..."


def load_extension(registry, here, there, display, *, file_there, link_there):
    """Load only here/probatio_ext.py by path; absent means unchanged, any setup failure refuses the command.

    The runner injects its path-look and display policies. No import search path is added or consulted to find
    the extension. A temporary module entry lets ordinary Python decorators work during import, then restores
    any prior entry. The caller adds the displayed error.path and exits 2 on RegistryError; failed loads are never
    silently replaced by the original registry.
    """
    path = Path(here) / EXTENSION_FILE
    try:
        if link_there(path):
            raise RegistryError("symbolic links are not allowed")
        if not there(path):
            return registry
        if not file_there(path):
            raise RegistryError("not a regular file")
        # This protects registry entries, including setup bypassing register(). Extensions are trusted Python
        # code: this guard cannot prevent them from rebinding the runner's own functions or other globals.
        existing = {name: (registry[name], tuple((field.name, getattr(registry[name], field.name))
                                               for field in fields(AgentSpec))) for name in registry}
        with contextlib.redirect_stdout(_Quiet()), contextlib.redirect_stderr(_Quiet()):
            spec = importlib.util.spec_from_file_location("_probatio_extension", str(path))
            if spec is None or spec.loader is None:
                raise RegistryError("cannot load the extension file")
            module = importlib.util.module_from_spec(spec)
            missing = object()
            previous = sys.modules.get(spec.name, missing)
            sys.modules[spec.name] = module
            try:
                exec(compile(path.read_bytes(), str(path), "exec"), module.__dict__)
                register = getattr(module, "register", None)
                if not callable(register):
                    raise RegistryError("extension must define callable register(registry)")
                register(registry)
                for name, (original, facts) in existing.items():
                    if registry.get(name) is not original or any(getattr(original, key, None) is not value
                                                                 for key, value in facts):
                        raise RegistryError(f"existing agent '{name}' cannot be replaced")
                registry.validate()
            finally:
                if previous is missing:
                    sys.modules.pop(spec.name, None)
                else:
                    sys.modules[spec.name] = previous
    except KeyboardInterrupt:
        raise
    except BaseException as error:
        raise RegistryError(_reason(error, display), path=path) from None
    return registry
