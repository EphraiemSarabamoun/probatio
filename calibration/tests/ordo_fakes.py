"""A scratch host for the ordo pilot's tests: v4's fake host (fakes.Host) with the ordo fake Claude Code
(fake_ordo_claude.py) as its pinned build, this checkout's probatio.py, a private results folder, and the fake's script
written from the plan, so every planned question has a reply that parses and every errand its answer key. Nothing here
calls a model or the network."""
import hashlib
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CAL = HERE.parent
ROOT = CAL.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(CAL))
sys.path.insert(0, str(HERE))
import fakes  # noqa: E402
import ordo  # noqa: E402
import ordo_pilot  # noqa: E402
import probatio  # noqa: E402

FAKE = HERE / "fake_ordo_claude.py"


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def candidates(row):
    """Replies that might parse for a row: each choice's label alone and on an answer or action line; for a
    three-line item, the first label of each menu on its line."""
    if row["answer_format"] == "three_final_lines":
        menus = {}
        for c in row["choices"]:
            menus.setdefault(c["menu"], c["label"])
        return ["\n".join(f"{m.upper()}: {menus.get(m, 'A')}" for m in ("action", "message", "outside"))]
    if row["answer_format"] == "integer_0_10":
        return ["7", "3"]
    out = []
    for c in row["choices"]:
        out += [c["label"], f"ANSWER: {c['label']}", f"ACTION: {c['label']}"]
    return out


def reply_for(call):
    """A reply that parses for this question, chosen among those that do by the prompt's hash (so answers vary)."""
    good = [r for r in candidates(call["row"]) if probatio.score(call["row"], r)[1] is not None]
    if not good:
        raise AssertionError(f"no candidate reply parses for {call['id']}")
    return good[int(sha(call["prompt"]), 16) % len(good)]


def script_for(calls, **extra):
    """The fake's script for these calls: replies by prompt hash, errands by world hash (the sides of a task share a
    prompt), and any planted events."""
    replies, errands = {}, {}
    for c in calls:
        if c.get("episode"):
            row = c["row"]
            errands[sha(c["world"])] = {"side": c["meta"]["side"], "finish_tool": row["finish_tool"],
                                         "answer_argument": row["answer_argument"], "answer_key": row["answer_key"]}
        else:
            replies[sha(c["prompt"])] = reply_for(c)
    return dict({"replies": replies, "errands": errands}, **extra)


class OrdoHost:
    """fakes.Host with the ordo fake as the pinned Claude Code build and this checkout's probatio.py."""

    def __init__(self, plan=None):
        self.host = fakes.Host()
        self.dir = self.host.dir
        text = FAKE.read_text()
        self.claude = self.host.claude
        self.claude.write_text(f"#!{sys.executable}\n" + text)
        self.claude.chmod(0o755)
        self.results = self.dir / "results"
        cfg = json.loads(self.host.local.read_text())
        cfg.update(probatio=str(ROOT / "probatio.py"), ordo_pilot_results=str(self.results))
        self.host.local.write_text(json.dumps(cfg))
        self.local = self.host.local
        self.root = self.host.root
        self.plan = plan or json.loads(ordo_pilot.PLAN.read_text())
        self.plan_path = self.dir / "plan.json"
        self.plan_path.write_text(json.dumps(self.plan))

    @property
    def script_path(self):
        return self.claude.parent / "ordo-fake.json"

    @property
    def calls_path(self):
        return self.claude.parent / "ordo-fake-calls.jsonl"

    def write_script(self, bank, **extra):
        calls = []
        for unit in ordo_pilot.pilot_runs(self.plan):
            calls += ordo_pilot.planned_calls(bank, self.plan, unit["stage"], unit["arm"])[0]
        self.script_path.write_text(json.dumps(script_for(calls, **extra)))

    def fake_calls(self):
        return [json.loads(x) for x in self.calls_path.read_text().splitlines()] if self.calls_path.exists() else []

    def close(self):
        self.host.close()


def clean_environ():
    """The caller's environment without the variables the room refuses or drops (fakes.clean_environ)."""
    return fakes.clean_environ()
