"""A stand-in for `claude -p` for the versus command's tests: no model. A question call (the prompt on stdin as plain
text) is answered by the rule VERSUS_RULE names (versus_rules.answer; default "first"), in Claude Code's stream-json
shape, through tests/fake_claude.py's own init and result events. An errand's dialogue (--input-format stream-json)
is tests/fake_claude.py's episode mode, which drives the real world.py. --version prints VERSUS_AGENT_VERSION (default
"0.0-fake (Claude Code)"); VERSUS_AGENT_DELAY, when set, makes each question wait that many seconds. When
VERSUS_AGENT_LOG is set, every start appends one JSON line with its argv and nothing
else: no variable, no value of the environment, is ever written. The tests put a `claude` on PATH that runs this file.
"""
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))           # tests/: fake_claude
sys.path.insert(0, str(HERE))                  # tests/fixtures/: versus_rules
import fake_claude as F  # noqa: E402
from versus_rules import answer  # noqa: E402


def main(argv):
    """One start of the stand-in CLI."""
    if os.environ.get("VERSUS_AGENT_LOG"):
        with open(os.environ["VERSUS_AGENT_LOG"], "a", encoding="utf-8") as f:
            f.write(json.dumps({"argv": argv[1:]}) + "\n")
    if argv[1:] == ["--version"]:
        print(os.environ.get("VERSUS_AGENT_VERSION", "0.0-fake (Claude Code)"))
        return 0
    if "--input-format" in argv:
        F.MODE = "episode"
        return F.main(argv)
    prompt = sys.stdin.read()
    if os.environ.get("VERSUS_AGENT_DELAY"):          # a slow agent, so a test can stop a run between calls
        import time
        time.sleep(float(os.environ["VERSUS_AGENT_DELAY"]))
    F.init()
    F.result(answer(os.environ.get("VERSUS_RULE", "first"), prompt))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
