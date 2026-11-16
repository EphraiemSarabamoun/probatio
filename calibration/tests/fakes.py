"""Stand-ins for the real CLIs, so the calibration tests run without a model, a network or the host's config:
a fake Claude Code build, a fake Codex CLI, a fake probatio.py, and a scratch host (local.json, secrets file) in a
temporary folder. Each fake is a small Python script; what it was asked and with which environment it writes to a log
beside it, which the tests read."""
import json
import os
import sys
import tempfile
from pathlib import Path

TOKEN = "fake-oauth-token-NOT-A-SECRET-31337"        # tests check this string never reaches a file or the output
TOKEN_NAME = "PROBATIO_TEST_TOKEN"

FAKE_CLAUDE = r'''#!PYTHON
"""Fake Claude Code: --version, auth status, and a print call that answers in stream-json as the real CLI does (an
init naming its model and build, a rate_limit_event, a result naming its model), shaped by SCRIPT (claude-script.json
beside it: {"api_key_source", "model", "version", "rate_limit_info", "no_rate", "no_init", "result", "five_hour_steps",
"seven_day_steps"}). The steps give each window's utilization on the first, second, ... call (the last repeats).
It logs its argv and the environment it saw (the token's presence only, never its value)."""
import json, os, sys, time
from pathlib import Path
here = Path(__file__).resolve().parent
args = sys.argv[1:]
if args[:1] == ["--version"]:
    print(os.environ.get("FAKE_CLAUDE_VERSION", "2.1.309 (Claude Code)")); sys.exit(0)
if args[:2] == ["auth", "status"]:
    ok = bool(os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")) and not os.environ.get("ANTHROPIC_API_KEY")
    print(json.dumps({"loggedIn": ok, "authMethod": "oauth_token" if ok else "none"})); sys.exit(0)
script = json.loads((here / "claude-script.json").read_text()) if (here / "claude-script.json").exists() else {}
keep = {k: v for k, v in os.environ.items() if k.startswith(("CLAUDE_CODE_DISABLE", "CLAUDE_CODE_EFFORT",
                                                             "DISABLE_AUTOUPDATER", "ANTHROPIC"))}
keep["token_present"] = bool(os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"))
with open(here / "claude-calls.jsonl", "a") as f:
    f.write(json.dumps({"args": args, "env": keep, "stdin": sys.stdin.read(), "cwd": os.getcwd()}) + "\n")
model = script.get("model") or (args[args.index("--model") + 1] if "--model" in args else "claude-sonnet-5-5")
if not script.get("no_init"):
    print(json.dumps({"type": "system", "subtype": "init", "apiKeySource": script.get("api_key_source", "none"),
                      "model": model, "claude_code_version": script.get("version", "2.1.309")}))
now = time.time()
steps, weekly = script.get("five_hour_steps") or [0.10], script.get("seven_day_steps") or [0.05]
calls = len((here / "claude-calls.jsonl").read_text().splitlines())
info = script.get("rate_limit_info") or {"status": "allowed", "unifiedWindows": {
    "five_hour": {"utilization": steps[min(calls, len(steps)) - 1], "resetsAt": now + 3600},
    "seven_day": {"utilization": weekly[min(calls, len(weekly)) - 1], "resetsAt": now + 5e5}}}
if not script.get("no_rate"):
    print(json.dumps({"type": "rate_limit_event", "rate_limit_info": info}))
print(json.dumps(dict({"type": "result", "subtype": "success", "is_error": False, "result": "OK",
                       "modelUsage": {model: {}}, "usage": {"input_tokens": 2}},
                      **script.get("result", {}))))
'''

FAKE_CODEX = r'''#!PYTHON
"""Fake Codex CLI: logs argv and whether OPENAI_API_KEY reached it."""
import json, os, sys
from pathlib import Path
here = Path(__file__).resolve().parent
if sys.argv[1:2] == ["--version"]:
    print("codex-cli 0.159.2"); sys.exit(0)
with open(here / "codex-calls.jsonl", "a") as f:
    f.write(json.dumps({"args": sys.argv[1:], "openai_key": "OPENAI_API_KEY" in os.environ,
                        "codex_home": os.environ.get("CODEX_HOME"),
                        "claude_token": "CLAUDE_CODE_OAUTH_TOKEN" in os.environ}) + "\n")
'''

FAKE_SSH = r'''#!PYTHON
"""Fake ssh: logs its argv to ssh-calls.jsonl beside it, drops the -o options and the target, and runs the remote
command on this host with stdin passed through, so `ssh host python3 - op blob` exercises the real transport."""
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
with open(Path(__file__).resolve().parent / "ssh-calls.jsonl", "a") as f:
    f.write(json.dumps(args) + "\n")
i = 0
while args[i] == "-o":
    i += 2
rest = args[i + 1:]
if rest[0] == "python3":                           # the home's python3, here the interpreter running the tests
    rest = [sys.executable] + rest[1:]
os.execvp(rest[0], rest)
'''

FAKE_SSH_DOWN = r'''#!PYTHON
"""Fake ssh to a host that does not answer."""
import sys
sys.stderr.write("ssh: connect to host fakehome port 22: Connection refused\n")
sys.exit(255)
'''

FAKE_PROBATIO = r'''#!PYTHON
"""Fake probatio.py. --dry-run prints a preflight whose memory list is CLAUDE.md in --workdir (plus FAKE_EXTRA_MEMORY
when set, or nothing when FAKE_NO_MEMORY is set). A real run follows FAKE_PLAN, a JSON list with one step per
attempt: {"quota": <a quota.jsonl row or null>, "records": [replies.jsonl rows], "rc": exit code, "claude_calls": n}.
claude_calls makes n print calls through the `claude` first on PATH (the room's shim), one replies.jsonl row each, and
exits 1 if any failed. Attempts are counted in a file beside the plan."""
import json, os, subprocess, sys, time
from pathlib import Path
args = sys.argv[1:]
opt = lambda name: args[args.index(name) + 1]
if "--dry-run" in args:
    files = [] if os.environ.get("FAKE_NO_MEMORY") else [str(Path(opt("--workdir")) / "CLAUDE.md")]
    files += [os.environ["FAKE_EXTRA_MEMORY"]] if os.environ.get("FAKE_EXTRA_MEMORY") else []
    print(f"probatio: claude (2.1.309 (Claude Code)), model {os.environ.get('FAKE_DRY_MODEL') or opt('--model')}")
    print(f"  standard tier at --jobs {os.environ.get('FAKE_DRY_JOBS') or opt('--jobs')}, each item asked once:")
    print(f"    fake         1 items     {os.environ.get('FAKE_PLANNED', '1')} calls   under a minute")
    print("  memory files for the with-memory run:" + ("".join("\n    " + f for f in files) or
                                                    " none found (the CLI may still load memory files)"))
    sys.exit(0)
plan = json.loads(Path(os.environ["FAKE_PLAN"]).read_text())
counter = Path(os.environ["FAKE_PLAN"] + ".count")
n = int(counter.read_text()) if counter.exists() else 0
counter.write_text(str(n + 1))
step = plan[min(n, len(plan) - 1)]
if step.get("quota"):
    row = dict(step["quota"], t=time.time())
    for key in ("five_hour_resets", "seven_day_resets", "resets"):
        if row.get(key) is not None:
            row[key] = time.time() + row[key]          # plans give reset times relative to now
    with open(os.environ["FAKE_QUOTA"], "a") as f:
        f.write(json.dumps(row) + "\n")
if step.get("stop"):
    Path(os.environ["FAKE_STOP"]).write_text("planted stop\n")
out = Path(opt("--out")); out.mkdir(parents=True, exist_ok=True)
records, failed = list(step.get("records", [])), False
condition = "with" if "--memory" in args and opt("--memory") == "on" else "without"
for i in range(step.get("claude_calls", 0)):
    call = subprocess.run(["claude", "-p", "--tools", "", "--strict-mcp-config", "--no-session-persistence",
                           "--settings", '{"disableAllHooks":true}', "--output-format", "json",
                           "--model", opt("--model")], input="synthetic prompt", capture_output=True, text=True)
    failed = failed or call.returncode != 0
    records.append({"condition": condition, "class": "I", "id": f"{n}-{i}",
                    "error": None if call.returncode == 0 else (call.stdout + call.stderr)[-300:] or "failed"})
with open(out / "replies.jsonl", "a") as f:
    for rec in records:
        f.write(json.dumps(rec) + "\n")
sys.exit(1 if failed else step.get("rc", 0))
'''


def script(path, text):
    """Write an executable fake with this interpreter in its #! line."""
    path = Path(path)
    path.write_text(text.replace("#!PYTHON", "#!" + sys.executable, 1))
    path.chmod(0o755)
    return path


class Host:
    """A scratch host: a temporary folder holding the fakes, a secrets file with TOKEN under TOKEN_NAME, a fake
    Codex login, a local.json pointing at all of it, and the root the rooms are built in."""

    def __init__(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="probatio-cal-test-")
        self.dir = Path(self._tmp.name)
        self.bin = self.dir / "fakebin"
        self.bin.mkdir()
        self.claude = script(self.bin / "claude-2.1.309", FAKE_CLAUDE)
        self.codex = script(self.bin / "codex", FAKE_CODEX)
        self.probatio = script(self.bin / "probatio.py", FAKE_PROBATIO)
        self.ssh = script(self.bin / "ssh", FAKE_SSH)
        self.ssh_down = script(self.bin / "ssh-down", FAKE_SSH_DOWN)
        self.secrets = self.dir / "secrets.sh"
        self.secrets.write_text(f"# test secrets\nexport OTHER=1\nexport {TOKEN_NAME}='{TOKEN}'\n")
        self.auth = self.dir / "codex-auth.json"
        self.auth.write_text("{}")
        self.root = self.dir / "root"
        self.local = self.dir / "local.json"
        self.local.write_text(json.dumps({
            "root": str(self.root), "secrets_file": str(self.secrets), "token_name": TOKEN_NAME,
            "claude_binary": str(self.claude), "claude_version": "2.1.309", "codex_binary": str(self.codex),
            "codex_version": "0.159.2", "codex_auth": str(self.auth), "probatio": str(self.probatio),
            "persona_markers": ["PERSONA-MARKER"]}))

    def ssh_calls(self):
        """The argv of every fake ssh call so far."""
        path = self.bin / "ssh-calls.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def files_containing(self, needle):
        """Every file under the scratch host whose bytes contain needle (the secrets file aside)."""
        hits = []
        for p in self.dir.rglob("*"):
            if p.is_file() and not p.is_symlink() and p != self.secrets and needle.encode() in p.read_bytes():
                hits.append(p)
        return hits

    def close(self):
        self._tmp.cleanup()


def clean_environ():
    """The caller's environment with every variable the harness refuses or drops removed, for tests that run the
    real checks."""
    return {k: v for k, v in os.environ.items() if not k.startswith(("ANTHROPIC", "CLAUDE", "OPENAI"))}
