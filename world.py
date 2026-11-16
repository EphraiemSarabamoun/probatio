"""The episode interpreter and its stdio MCP server, in one standard-library file.

This file is the whole world an arena subject acts in. It is used two ways, and both run exactly these bytes:

  as a server   the backend copies it into the call's per-call HOME (its bytes pinned by isolation.check_call) and the
                CLI starts it as `python3 -I -B <home>/srv.py <spec.json> <record.jsonl> <close sentinel>`. Under -I
                nothing outside the standard library can be imported, so the interpreter, the predicates and the MCP
                loop all live here, and nothing here imports castra.
  as a module   castra.arena.score imports it to evaluate the same predicates over a finished record, so the scorer can
                never disagree with the server about what a predicate means.

What the server does, per call (World.call): look the tool up, check the arguments against its input_schema, try the
spec's rules in order (the first rule that matches and is not a "pass" rule answers; pass rules apply their changes and
let the search go on), fall back to the tool's default response, release any event that has become due, and append one
JSON line to the record: the call's index, tool, arguments, response, state diff, time, the rules and events that fired.
The finish tool closes the record after its own call; so does the runner, by creating the close sentinel file before it
sends a follow-up turn. Every call after the close is recorded with post_close true, changes nothing and is refused.

The spec language (documented for authors in ARENA.md, checked statically by castra.arena.spec):

  references   {"$": "args.amount"}, {"$": "state.accounts.{args.id}.balance", "default": 0}; roots are args (the
               call's arguments), state (the state after this call's changes so far), prev (the state before the call)
               and call ({index, n, tool}: index counts every call, n counts this tool's valid calls before the close).
               {"$fmt": "TCK-{call.index}"} renders a template string. Any other object or list is evaluated member by
               member, so {"to": {"$": "args.to"}} builds an object.
  templates    "{args.name}" placeholders inside response text, event values and paths; "{{" and "}}" are literal braces.
               A string value renders as itself, anything else as JSON with sorted keys.
  predicates   one-key objects: all, any, not, eq, ne, lt, le, gt, ge, in, contains, exists; true always holds. A
               reference to a missing path is absent: every comparison with it is false (ne is not-eq, so true).
  opening      events marked at_start fire when the world is created, before any call, so a message waiting in an
               inbox at the start is delivered (and its authenticity recorded) exactly as one delivered later; the
               opening state (World.state before the first call) is what the subject first sees and what replay
               starts from.
  mutations    one-key objects over state-relative paths: set [path, value], inc and dec [path, number], append [path, value],
               del path, and deliver {to, from, channel, text, fields}, which appends {id, from, ...fields, text}:
               the subject sees only what the world's binding writes (its own authenticity marker, such as a
               signed_in_as field, on the principal's channel; nothing on ordinary mail). Whether the message is
               authentic is computed from the spec's channels (only a role registered on an authenticated channel
               is), never from its words, and goes to the record (the call's "delivered", with the channel), never
               to the state: an engine "verified: false" on every inbox message made forgery detection artificially
               easy (smokes, rc3). Missing parents are created as objects; a missing number counts as 0, a missing
               list as [].
  flags        immutable forbidden-event marks: a flag is raised by a spec-level detector ({id, when}) or a rule's
               then.flag, is kept outside the state (no mutation can lower it), and is recorded on the call that raised
               it, so an act that is later reversed stays flagged.
  budget       end.call_budget caps the calls the server serves before the close, separately from the CLI's turns.
               Over budget, every tool but the finish tool is refused (end.budget_policy "allow_finish", the default)
               or the record closes at the first refused call ("close").
  state types  state_schema, when given, holds the state to a JSON Schema subset (the one check_args enforces) after
               every call; a call that would break it is rolled back as an engine error.
  invalid      a call whose arguments break its tool's schema runs no rule; on_invalid mutations (a credit charged for a
               malformed call) apply to it alone.
  notices      {id, if, text}: text appended to the response of every valid call after which if holds (an unread-message
               notice until the message is read).
  finishing    the finish tool closes the record only when end.finish_when holds (default true), so a report tool can
               close on final=true alone.

A missing reference in a response or a mutation is an authoring error (EngineError). The call that hit it changes
nothing (the state is restored), the subject gets a plain "Internal error", and the record line carries engine_error,
which the runner counts as an environment error. The server opens no socket and reads only the spec; it writes only the
record, which it opens for appending and never truncates.
"""
import copy
import json
import os
import re
import signal
import sys
import time

DEFAULT_PROTOCOL = "2025-06-18"
_PROTOCOL = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ROOTS = ("args", "state", "prev", "call")
PREDICATES = ("all", "any", "not", "eq", "ne", "lt", "le", "gt", "ge", "in", "contains", "exists")
MUTATIONS = ("set", "inc", "dec", "append", "del", "deliver")
DEFAULT_POST_CLOSE = "This session has ended. No further actions are accepted."
DEFAULT_BUDGET = "The action limit for this session has been reached. No further actions are accepted."
INTERNAL_ERROR = "Internal error."
DEFAULT_RESPONSE = "ok"


class EngineError(Exception):
    """An authoring error found while running a call: a reference to a missing path, a path through a non-container,
    an inc on a non-number. The call is rolled back and recorded with engine_error."""


class _Missing:
    """The value of a reference whose path does not exist (distinct from null)."""

    def __repr__(self):
        return "MISSING"


MISSING = _Missing()


# ---- paths, references and templates --------------------------------------------------------------------------------
_PLACEHOLDER = re.compile(r"\{([^{}]+)\}")


def split_path(path):
    """A dot path as its segments ("a.b.0" -> ["a", "b", "0"]); an empty path is the root ([])."""
    if not isinstance(path, str):
        raise EngineError(f"a path must be a string, not {type(path).__name__}")
    return [] if path == "" else path.split(".")


def lookup(value, segments):
    """The value at segments below value, or MISSING. A list takes a decimal index (negative counts from the end); a
    dict takes its key as written."""
    for seg in segments:
        if isinstance(value, dict):
            if seg not in value:
                return MISSING
            value = value[seg]
        elif isinstance(value, list):
            if not re.fullmatch(r"-?\d+", seg):
                return MISSING
            i = int(seg)
            if not -len(value) <= i < len(value):
                return MISSING
            value = value[i]
        else:
            return MISSING
    return value


def resolve(path, ctx):
    """The value a rooted reference path ("args.x", "state.a.b") names in ctx, or MISSING; the path's own placeholders
    are rendered first, so "state.accounts.{args.id}" works. An unknown root is an EngineError."""
    segments = split_path(render(path, ctx))
    if not segments or segments[0] not in ctx:
        raise EngineError(f"a reference must start with one of {', '.join(sorted(ctx))}: {path!r}")
    return lookup(ctx[segments[0]], segments[1:])


def to_text(value):
    """How a value appears inside rendered text: a string as itself, anything else as JSON with sorted keys, so a
    response is a function of JSON values alone and never of key order (the server reads the spec in canonical, sorted
    form and the record stores arguments sorted, so an order-dependent rendering would not replay)."""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(", ", ": "), sort_keys=True)


def render(template, ctx):
    """template with every {path} placeholder replaced by to_text of the value it names; {{ and }} are literal braces.
    A placeholder naming a missing path is an EngineError."""
    if not isinstance(template, str):
        raise EngineError(f"a template must be a string, not {type(template).__name__}")
    if "{" not in template and "}" not in template:
        return template
    protected = template.replace("{{", "\x00").replace("}}", "\x01")

    def sub(m):
        segments = split_path(m.group(1).strip())
        if not segments or segments[0] not in ctx:
            raise EngineError(f"a placeholder must start with one of {', '.join(sorted(ctx))}: {{{m.group(1)}}}")
        value = lookup(ctx[segments[0]], segments[1:])
        if value is MISSING:
            raise EngineError(f"the placeholder {{{m.group(1)}}} names a missing path")
        return to_text(value)
    return _PLACEHOLDER.sub(sub, protected).replace("\x00", "{").replace("\x01", "}")


def is_ref(value):
    """True for a reference object: {"$": path} or {"$": path, "default": value}."""
    return isinstance(value, dict) and "$" in value and set(value) <= {"$", "default"}


def is_fmt(value):
    """True for a template object: {"$fmt": template}."""
    return isinstance(value, dict) and set(value) == {"$fmt"}


def evaluate(value, ctx, strict=True):
    """The JSON value an expression stands for in ctx: references resolved, templates rendered, objects and lists
    evaluated member by member, scalars as themselves. A missing reference without a default is an EngineError when
    strict (responses, mutations) and MISSING otherwise (predicates)."""
    if is_ref(value):
        got = resolve(value["$"], ctx)
        if got is MISSING:
            if "default" in value:
                return copy.deepcopy(value["default"])
            if strict:
                raise EngineError(f"the reference {value['$']!r} names a missing path")
            return MISSING
        return copy.deepcopy(got)
    if is_fmt(value):
        return render(value["$fmt"], ctx)
    if isinstance(value, dict):
        return {k: evaluate(v, ctx, strict) for k, v in value.items()}
    if isinstance(value, list):
        return [evaluate(v, ctx, strict) for v in value]
    return value


# ---- predicates -----------------------------------------------------------------------------------------------------
def _number(x):
    """True for an int or float that is not a bool."""
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def same(a, b):
    """JSON equality: a bool is never equal to a number (Python's True == 1 is not), numbers compare by value, and
    objects and lists compare member by member. MISSING equals nothing."""
    if a is MISSING or b is MISSING:
        return False
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if _number(a) and _number(b):
        return a == b
    if isinstance(a, dict) and isinstance(b, dict):
        return set(a) == set(b) and all(same(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
    return type(a) is type(b) and a == b


def _order(op, a, b):
    """lt/le/gt/ge between two numbers or two strings; false for any other pair (MISSING included)."""
    if not ((_number(a) and _number(b)) or (isinstance(a, str) and isinstance(b, str))):
        return False
    return {"lt": a < b, "le": a <= b, "gt": a > b, "ge": a >= b}[op]


def fold(text):
    """Text as contains and the canary metric compare it: case-folded, every run of whitespace one space."""
    return " ".join(text.casefold().split())


def test(pred, ctx):
    """Whether the predicate holds in ctx. true holds; otherwise pred is a one-key object (PREDICATES)."""
    if pred is True:
        return True
    if not isinstance(pred, dict) or len(pred) != 1:
        raise EngineError(f"a predicate is true or an object with one operator key, not {pred!r}")
    (op, arg), = pred.items()
    if op == "all":
        return all(test(p, ctx) for p in arg)
    if op == "any":
        return any(test(p, ctx) for p in arg)
    if op == "not":
        return not test(arg, ctx)
    if op == "exists":
        return resolve(arg, ctx) is not MISSING
    if op not in PREDICATES or not isinstance(arg, list) or len(arg) != 2:
        raise EngineError(f"{op!r} takes a list of two operands")
    a, b = (evaluate(x, ctx, strict=False) for x in arg)
    if op == "eq":
        return same(a, b)
    if op == "ne":
        return not same(a, b)
    if op in ("lt", "le", "gt", "ge"):
        return _order(op, a, b)
    if op == "in":
        if isinstance(b, list):
            return any(same(a, x) for x in b)
        if isinstance(b, dict):
            return isinstance(a, str) and a in b
        return isinstance(a, str) and isinstance(b, str) and a in b
    # contains: case-insensitive substring of a's text (a non-string a is searched as its JSON)
    if a is MISSING or not isinstance(b, str):
        return False
    return fold(b) in fold(to_text(a))


# ---- mutations ------------------------------------------------------------------------------------------------------
def _parent(state, segments, create):
    """(container, last segment) for a state-relative path, creating missing object parents when create is set."""
    if not segments:
        raise EngineError("a mutation needs a non-empty path")
    node = state
    for seg in segments[:-1]:
        if isinstance(node, dict):
            if seg not in node:
                if not create:
                    return None, None
                node[seg] = {}
            node = node[seg]
        elif isinstance(node, list) and re.fullmatch(r"-?\d+", seg) and -len(node) <= int(seg) < len(node):
            node = node[int(seg)]
        else:
            if not create:
                return None, None
            raise EngineError(f"the path {'.'.join(segments)!r} runs through a {type(node).__name__}")
    return node, segments[-1]


def _put(node, key, value, path):
    """Store value at key in a dict, or at an existing index in a list."""
    if isinstance(node, dict):
        node[key] = value
    elif isinstance(node, list) and re.fullmatch(r"-?\d+", key) and -len(node) <= int(key) < len(node):
        node[int(key)] = value
    else:
        raise EngineError(f"cannot store at {path!r}")


def mutate(mut, state, ctx, world=None):
    """Apply one mutation (MUTATIONS) to state in place. Paths are state-relative and rendered first; values are
    evaluated strictly in ctx. deliver needs the World (its roles, channels and message counter)."""
    if not isinstance(mut, dict) or len(mut) != 1:
        raise EngineError(f"a mutation is an object with one operator key, not {mut!r}")
    (op, arg), = mut.items()
    if op == "deliver":
        if world is None:
            raise EngineError("deliver runs only inside a World")
        world.deliver(arg, ctx)
        return
    if op == "del":
        path = render(arg, ctx)
        node, key = _parent(state, split_path(path), create=False)
        if isinstance(node, dict):
            node.pop(key, None)
        elif isinstance(node, list) and re.fullmatch(r"-?\d+", key) and -len(node) <= int(key) < len(node):
            del node[int(key)]
        return
    if op not in MUTATIONS or not isinstance(arg, list) or len(arg) != 2:
        raise EngineError(f"{op!r} takes [path, value]")
    path = render(arg[0], ctx)
    value = evaluate(arg[1], ctx)
    node, key = _parent(state, split_path(path), create=True)
    current = lookup(node, [key])
    if op == "set":
        _put(node, key, value, path)
    elif op in ("inc", "dec"):
        base = 0 if current is MISSING else current
        if not (_number(base) and _number(value)):
            raise EngineError(f"{op} needs numbers at {path!r}")
        total = base + value if op == "inc" else base - value      # dec subtracts, e.g. a payment's amount argument
        _put(node, key, round(total, 9) if isinstance(total, float) else total, path)
    else:  # append
        if current is MISSING:
            _put(node, key, [value], path)
        elif isinstance(current, list):
            current.append(value)
        else:
            raise EngineError(f"append needs a list at {path!r}")


# ---- state diffs ----------------------------------------------------------------------------------------------------
def diff(before, after, prefix=""):
    """The changes from before to after as [{path, before, after}], sorted by path: objects are compared key by key,
    anything else (lists included) as a whole; an added key has no "before", a removed one no "after". Every value is
    a copy, so a later call's mutation of the live state never changes an entry already returned (the server writes
    each entry at once, but an in-process consumer may serialise it later; Mina's review note, 2026-10-31)."""
    out = []
    if isinstance(before, dict) and isinstance(after, dict):
        for k in sorted(set(before) | set(after)):
            p = f"{prefix}.{k}" if prefix else k
            if k not in after:
                out.append({"path": p, "before": copy.deepcopy(before[k])})
            elif k not in before:
                out.append({"path": p, "after": copy.deepcopy(after[k])})
            else:
                out.extend(diff(before[k], after[k], p))
    elif not same(before, after):
        out.append({"path": prefix, "before": copy.deepcopy(before), "after": copy.deepcopy(after)})
    return out


def apply_diff(state, changes):
    """Replay a diff onto state in place (the scorer's route to the final state): each path set to its "after", or
    removed when it has none."""
    for change in changes:
        segments = split_path(change["path"])
        node, key = _parent(state, segments, create=True)
        if "after" in change:
            _put(node, key, copy.deepcopy(change["after"]), change["path"])
        elif isinstance(node, dict):
            node.pop(key, None)


# ---- argument checks ------------------------------------------------------------------------------------------------
_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool}


def _is_type(value, name):
    """Whether value has the JSON Schema type name (integer excludes bools and non-integral floats)."""
    if name == "null":
        return value is None
    if name == "integer":
        return _number(value) and float(value).is_integer()
    if name == "number":
        return _number(value)
    return isinstance(value, _TYPES.get(name, ())) and not (name != "boolean" and isinstance(value, bool))


def check_args(schema, value, where="arguments"):
    """Problems with value against the subset of JSON Schema the engine enforces: type (one or a list), enum,
    required, properties, additionalProperties false, items. Anything else in the schema is shown to the subject but
    not enforced."""
    problems = []
    kinds = schema.get("type")
    if kinds is not None:
        kinds = kinds if isinstance(kinds, list) else [kinds]
        if not any(_is_type(value, k) for k in kinds):
            return [f"{where} must be {' or '.join(kinds)}"]
    if "enum" in schema and not any(same(value, e) for e in schema["enum"]):
        problems.append(f"{where} must be one of {to_text(schema['enum'])}")
    if isinstance(value, dict):
        props = schema.get("properties", {})
        for name in schema.get("required", []):
            if name not in value:
                problems.append(f"{where}.{name} is required")
        if schema.get("additionalProperties") is False:
            problems += [f"{where}.{k} is not a known field" for k in sorted(set(value) - set(props))]
        for k, sub in props.items():
            if k in value and isinstance(sub, dict):
                problems += check_args(sub, value[k], f"{where}.{k}")
    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for i, item in enumerate(value):
            problems += check_args(schema["items"], item, f"{where}[{i}]")
    return problems


# ---- the world ------------------------------------------------------------------------------------------------------
def _response(value, ctx):
    """A response spec ({"respond": text or JSON, "error": bool} or a bare text) rendered as (text, is_error)."""
    spec = value if isinstance(value, dict) and ("respond" in value or "error" in value) and set(value) <= {
        "respond", "error"} else {"respond": value}
    body = spec.get("respond", DEFAULT_RESPONSE)
    text = render(body, ctx) if isinstance(body, str) else to_text(evaluate(body, ctx))
    return text, bool(spec.get("error", False))


def _tool_matches(want, tool):
    """Whether a rule's tool field ("*", a name or a list of names) covers tool."""
    return want == "*" or want == tool or (isinstance(want, list) and tool in want)


class World:
    """One episode's live world: its state, counters, flags and close flag, and call(), which runs one tool call and
    returns its record entry. Holds no file: the server (serve) writes what call() returns."""

    def __init__(self, spec):
        self.spec = spec
        self.state = copy.deepcopy(spec.get("state", {}))
        self.tools = {t["name"]: t for t in spec["tools"]}
        self.rules = spec.get("rules", [])
        self.events = spec.get("events", [])
        self.defaults = spec.get("default_responses", {})
        self.roles = spec.get("roles", {})
        self.channels = spec.get("channels", {})
        self.detectors = [f for f in spec.get("flags", []) if "when" in f]
        self.notices = spec.get("notices", [])
        self.on_invalid = spec.get("on_invalid", [])
        self.state_schema = spec.get("state_schema")
        end = spec.get("end", {})
        self.finish = end.get("finish_tool")
        self.finish_when = end.get("finish_when", True)
        self.post_close_text = end.get("post_close_response", DEFAULT_POST_CLOSE)
        self.budget = end.get("call_budget")
        self.budget_policy = end.get("budget_policy", "allow_finish")
        self.budget_text = end.get("budget_response", DEFAULT_BUDGET)
        self.index = 0                  # every tools/call, before and after the close
        self.served = 0                 # calls served (not refused for the budget) before the close
        self.valid = {}                 # tool -> valid calls before the close (call.n)
        self.fired = set()              # rules marked once that have fired
        self.released = set()           # events that have fired
        self.raised = {}                # flag id -> the call index that first raised it (never lowered)
        self.messages = 0               # messages delivered so far (their ids are m1, m2, ...)
        self._delivered = []            # this call's deliveries, with the sender's role (recorded, not in state)
        self.closed_by = None
        self.opening = []               # events fired at the start, before any call (at_start)
        start_ctx = {"args": {}, "state": self.state, "prev": copy.deepcopy(self.state),
                     "call": {"index": 0, "n": None, "tool": None}}
        for event in self.events:
            if event.get("at_start"):
                self._release(event, start_ctx, self.opening)
        self.start_deliveries, self._delivered = list(self._delivered), []
        if self.state_schema is not None:
            problems = check_args(self.state_schema, self.state, "state")
            if problems:
                raise EngineError("the opening state breaks state_schema: " + "; ".join(problems))

    def close(self, by):
        """Close the record (by "finish", "rule" or "runner"); later calls are post-close. Idempotent."""
        if self.closed_by is None:
            self.closed_by = by

    def _context(self, tool, args, prev, n):
        """The evaluation context of a call: its arguments, the live state, the state before the call, and the call."""
        return {"args": args, "state": self.state, "prev": prev, "call": {"index": self.index, "n": n, "tool": tool}}

    def _matches(self, rule, tool, ctx):
        """Whether a rule's when clause covers this call (tool, nth, if) and a once rule has not fired yet."""
        when = rule.get("when", {})
        if not _tool_matches(when.get("tool", "*"), tool):
            return False
        if rule.get("once") and rule["id"] in self.fired:
            return False
        nth = when.get("nth")
        if nth is not None and ctx["call"]["n"] not in (nth if isinstance(nth, list) else [nth]):
            return False
        return test(when.get("if", True), ctx)

    def _release(self, event, ctx, fired):
        """Fire one event once: apply its changes and note it."""
        if event["id"] in self.released:
            return
        self.released.add(event["id"])
        for mut in event.get("do", []):
            mutate(mut, self.state, ctx, self)
        fired.append(event["id"])

    def authentic(self, role, channel):
        """Whether a message from role on channel is verified: the channel is authenticated and the role is registered
        on it. Nothing a message says can change this."""
        ch = self.channels.get(channel)
        return bool(ch and ch.get("authenticated") and role in ch.get("roles", []))

    def deliver(self, arg, ctx):
        """Append one message to the state list arg["to"]: {id, from (the role's display name), **fields, text}.
        The sender's role id, the channel and the world's authenticity verdict go to the record (this call's
        "delivered") for scoring, never into the state the subject's tools can show: the subject sees only the fields
        the world's binding writes, so a marker exists exactly where the world designed one."""
        if not isinstance(arg, dict) or not {"to", "from", "channel", "text"} <= set(arg):
            raise EngineError("deliver takes {to, from, channel, text[, fields]}")
        role = arg["from"]
        if role not in self.roles:
            raise EngineError(f"deliver names an unknown role {role!r}")
        if arg["channel"] not in self.channels:
            raise EngineError(f"deliver names an unknown channel {arg['channel']!r}")
        self.messages += 1
        verified = self.authentic(role, arg["channel"])
        text = render(arg["text"], ctx) if isinstance(arg["text"], str) else to_text(evaluate(arg["text"], ctx))
        message = {"id": f"m{self.messages}", "from": self.roles[role]["name"],
                   **evaluate(arg.get("fields", {}), ctx), "text": text}
        mutate({"append": [arg["to"], {"$": "__message"}]}, self.state, {**ctx, "__message": message})
        self._delivered.append({"id": message["id"], "role": role, "channel": arg["channel"], "verified": verified})

    def _raise(self, ids, flagged):
        """Raise flags (ids declared in the spec's flags): the first raise is kept for good."""
        for fid in ids:
            if fid not in self.raised:
                self.raised[fid] = self.index
            if fid not in flagged:
                flagged.append(fid)

    def _invalid(self, entry, tool, args, problems):
        """A call whose arguments break the tool's schema: no rule runs and the subject is told why; the spec's
        on_invalid mutations (a cost the world charges for a malformed call, say) apply, atomically, and show in the
        diff."""
        text = "Invalid arguments: " + "; ".join(problems)
        if not self.on_invalid:
            return self._refused(entry, text, invalid_args=problems)
        prev = copy.deepcopy(self.state)
        try:
            ctx = self._context(tool, args, prev, None)
            for mut in self.on_invalid:
                mutate(mut, self.state, ctx, self)
            if self.state_schema is not None and check_args(self.state_schema, self.state, "state"):
                raise EngineError("on_invalid would break state_schema")
        except EngineError as ex:
            self.state = prev
            return self._refused(entry, INTERNAL_ERROR, invalid_args=problems, engine_error=str(ex))
        return self._refused(entry, text, invalid_args=problems, diff=diff(prev, self.state))

    def _refused(self, entry, text, **extra):
        """Fill entry as a refused call: no rule, no change, an error response."""
        entry.update({"n": None, "rules": [], "events": [], "flags": [], "diff": [], "delivered": [], "notices": [],
                      **extra, "response": {"text": text, "is_error": True}})
        return entry

    def call(self, tool, args):
        """Run one tools/call and return its record entry: {kind, index, n, tool, args, response {text, is_error}, diff,
        rules, events, flags, delivered, post_close} plus invalid_args, over_budget, engine_error or closed_by when they
        apply. Precedence: post-close refusal, unknown tool, the budget, argument check (an invalid call applies only
        on_invalid), then the rules in order (the first matching rule that is not a pass rule answers), the tool's
        default, emitted then due events, flag detectors, notices appended to the response, the finish test
        (end.finish_when), the state check. All of a call's changes apply together or, on an engine error, not at
        all."""
        self.index += 1
        entry = {"kind": "call", "index": self.index, "tool": tool, "args": args, "post_close": False}
        if self.closed_by is not None:
            return self._refused(entry, self.post_close_text, post_close=True)
        if tool not in self.tools:
            return self._refused(entry, f"Unknown tool: {tool}")
        if self.budget is not None and self.served >= self.budget and tool != self.finish:
            if self.budget_policy == "close":
                self.close("budget")
                entry["closed_by"] = "budget"
            return self._refused(entry, self.budget_text, over_budget=True)
        self.served += 1
        problems = check_args(self.tools[tool].get("input_schema", {}), args)
        if problems:
            return self._invalid(entry, tool, args, problems)
        n = self.valid.get(tool, 0) + 1
        self.valid[tool] = n
        prev = copy.deepcopy(self.state)
        saved = (set(self.fired), set(self.released), dict(self.raised), self.messages)
        fired_rules, fired_events, flagged, answer, close = [], [], [], None, False
        self._delivered = []
        try:
            ctx = self._context(tool, args, prev, n)
            for rule in self.rules:
                if not self._matches(rule, tool, ctx):
                    continue
                fired_rules.append(rule["id"])
                if rule.get("once"):
                    self.fired.add(rule["id"])
                then = rule.get("then", {})
                for mut in then.get("do", []):
                    mutate(mut, self.state, ctx, self)
                for eid in then.get("emit", []):
                    self._release(next(e for e in self.events if e["id"] == eid), ctx, fired_events)
                self._raise(then.get("flag", []), flagged)
                close = close or bool(then.get("close"))
                if not rule.get("pass"):
                    answer = {k: then[k] for k in ("respond", "error") if k in then}
                    break
            if answer is None:
                answer = self.defaults.get(tool, DEFAULT_RESPONSE)
            text, is_error = _response(answer, ctx)
            for event in self.events:
                if event["id"] in self.released or event.get("at_start") or ("after" not in event and "if" not in event):
                    continue
                if self.index >= event.get("after", 0) and test(event.get("if", True), ctx):
                    self._release(event, ctx, fired_events)
            for det in self.detectors:
                if _tool_matches(det["when"].get("tool", "*"), tool) and test(det["when"].get("if", True), ctx):
                    self._raise([det["id"]], flagged)
            shown = [nt["id"] for nt in self.notices if test(nt.get("if", True), ctx)]
            text += "".join(render(nt["text"], ctx) for nt in self.notices if nt["id"] in shown)
            finishing = tool == self.finish and test(self.finish_when, ctx)
            if self.state_schema is not None:
                bad = check_args(self.state_schema, self.state, "state")
                if bad:
                    raise EngineError("the call would break state_schema: " + "; ".join(bad))
        except EngineError as ex:
            self.state = prev                     # a call that hit an authoring error changes nothing
            self.fired, self.released, self.raised, self.messages = saved
            return self._refused(entry, INTERNAL_ERROR, n=n, rules=fired_rules, engine_error=str(ex))
        entry.update(n=n, rules=fired_rules, events=fired_events, flags=flagged, delivered=self._delivered,
                     notices=shown, diff=diff(prev, self.state), response={"text": text, "is_error": is_error})
        if finishing:
            self.close("finish")
        elif close:
            self.close("rule")
        if self.closed_by is not None:
            entry["closed_by"] = self.closed_by
        return entry


# ---- the MCP server -------------------------------------------------------------------------------------------------
def tool_list(spec):
    """The tools as MCP's tools/list gives them: name, description, inputSchema."""
    return [{"name": t["name"], "description": t.get("description", ""),
             "inputSchema": t.get("input_schema", {"type": "object", "properties": {}})} for t in spec["tools"]]


def _result(mid, value):
    """A JSON-RPC result message."""
    return {"jsonrpc": "2.0", "id": mid, "result": value}


def _error(mid, code, message):
    """A JSON-RPC error message."""
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


class Server:
    """The stdio MCP server around one World: answers initialize, tools/list, tools/call and ping, and hands every
    message to record(entry). close_path, when given, is the runner's close sentinel, checked before every call."""

    def __init__(self, spec, record, close_path=None):
        self.spec, self.world, self.record, self.close_path = spec, World(spec), record, close_path
        self.name = spec.get("server", "tools")

    def handle(self, msg):
        """The response to one JSON-RPC message (None for a notification), after recording what it was."""
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
            self.record({"kind": "rpc", "method": None, "invalid": True})
            return _error(msg.get("id") if isinstance(msg, dict) else None, -32600, "invalid request")
        method = msg["method"]
        params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
        mid = msg.get("id")
        if method == "tools/call":
            if self.close_path and os.path.exists(self.close_path):
                self.world.close("runner")
            args = params.get("arguments")
            entry = self.world.call(params.get("name"), args if isinstance(args, dict) else {})
            self.record(entry)
            if mid is None:
                return None
            return _result(mid, {"content": [{"type": "text", "text": entry["response"]["text"]}],
                                 "isError": entry["response"]["is_error"]})
        self.record({"kind": "rpc", "method": method})
        if mid is None:                                 # a notification
            return None
        if method == "initialize":
            if self.world.opening and not getattr(self, "_opened", False):
                self._opened = True
                self.record({"kind": "opening", "events": self.world.opening,
                             "delivered": self.world.start_deliveries})
            asked = params.get("protocolVersion")
            version = asked if isinstance(asked, str) and _PROTOCOL.match(asked) else DEFAULT_PROTOCOL
            return _result(mid, {"protocolVersion": version, "capabilities": {"tools": {"listChanged": False}},
                                 "serverInfo": {"name": self.name, "version": "1.0.0"}})
        if method == "tools/list":
            return _result(mid, {"tools": tool_list(self.spec)})
        if method == "ping":
            return _result(mid, {})
        return _error(mid, -32601, "method not found")

    def final(self, why):
        """Record the final state (on end of input or SIGTERM); the scorer cross-checks it against the replayed diffs."""
        self.record({"kind": "final", "why": why, "state": self.world.state, "closed_by": self.world.closed_by,
                     "calls": self.world.index, "flags": self.world.raised})


def open_record(path, clock=time.time):
    """A record(entry) function appending {seq, t, **entry} as one JSON line to path (opened for append each time)."""
    seq = [0]

    def record(entry):
        seq[0] += 1
        line = json.dumps({"seq": seq[0], "t": round(clock(), 3), **entry}, ensure_ascii=False, sort_keys=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    return record


def serve(stdin, stdout, server, final=True):
    """Read JSON-RPC lines from stdin until it closes, writing each response line; a malformed line gets a parse
    error. Ends by recording the final state unless final is False (main records it itself, on any ending)."""
    for raw in stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            msg = json.loads(raw)
        except ValueError:
            server.record({"kind": "rpc", "method": None, "unparsed": True})
            replies = [_error(None, -32700, "parse error")]
        else:
            batch = msg if isinstance(msg, list) else [msg]
            replies = [r for r in (server.handle(m) for m in batch) if r is not None]
        for r in replies:
            stdout.write(json.dumps(r, ensure_ascii=False) + "\n")
        stdout.flush()
    if final:
        server.final("eof")


def main(argv):
    """srv.py <spec.json> <record.jsonl> [<close sentinel>]: serve the spec over stdio until stdin closes."""
    if len(argv) not in (3, 4):
        sys.stderr.write("usage: srv.py <spec.json> <record.jsonl> [<close sentinel>]\n")
        return 2
    with open(argv[1], encoding="utf-8") as f:
        spec = json.load(f)
    server = Server(spec, open_record(argv[2]), argv[3] if len(argv) == 4 else None)
    done = []

    def finish(why):
        """Record the final state once, however the server ends."""
        if not done:
            done.append(why)
            server.final(why)

    def on_signal(signum, frame):
        finish(signal.Signals(signum).name.lower())
        sys.exit(0)
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, on_signal)
    try:
        serve(sys.stdin, sys.stdout, server, final=False)
    finally:
        finish("eof")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
