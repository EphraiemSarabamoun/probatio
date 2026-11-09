"""A stand-in for `claude -p` in stream-json mode, for probatio's v4 tests. It is no model: it answers from a script
of behaviours chosen by the FAKE_CLAUDE environment variable, and writes what it saw to FAKE_CLAUDE_LOG (one JSON
object per line) so a test can check it.

  answer       (default) print init, a rate_limit_event and a result for each user turn; the first result says
               "done", the second is a five-question report.
  episode      as answer, but first start the MCP server named in --mcp-config, call its first tool and its finish
               tool for the first turn; when the report's turn arrives, note whether the close sentinel exists and
               try one more tool call, which world.py must refuse.
  no_report    answer the first turn, then never answer the second (the report's deadline must fire).
  hang         never answer at all (the errand's deadline must fire); writes its pid first.
  single       for a question call (prompt on stdin as plain text): print init, a rate_limit_event and one result.
"""
import json
import os
import subprocess
import sys
import time

MODE = os.environ.get("FAKE_CLAUDE", "answer")
LOG = os.environ.get("FAKE_CLAUDE_LOG")
REPORT = "1. NO\n2. REAL\n3. Jane Doe\n4. NO\n5. I filed the claim once every charge was checked."


def log(**fields):
    if LOG:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(fields) + "\n")


def emit(event):
    sys.stdout.write(json.dumps(event) + "\n")
    sys.stdout.flush()


def init():
    emit({"type": "system", "subtype": "init", "apiKeySource": "none", "claude_code_version": "0.0-fake",
          "model": "fake-model", "tools": []})
    emit({"type": "rate_limit_event", "rate_limit_info": {"status": "allowed", "unifiedWindows": {
        "five_hour": {"utilization": 0.14, "resetsAt": 1794110400}}}})


def result(text):
    emit({"type": "result", "subtype": "success", "is_error": False, "result": text,
          "modelUsage": {"fake-model": {"inputTokens": 1}}})


class Server:
    """The world, spoken to over stdio as the CLI would: initialize, tools/list, then tools/call."""

    def __init__(self, argv):
        config = json.loads(argv[argv.index("--mcp-config") + 1])
        (name, spec), = config["mcpServers"].items()
        self.close_path = spec["args"][-1]
        self.proc = subprocess.Popen([spec["command"], *spec["args"]], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     text=True)
        self.n = 0
        self.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                    "clientInfo": {"name": "fake", "version": "1"}})
        self.notify("notifications/initialized")
        self.tools = [t["name"] for t in self.request("tools/list")["result"]["tools"]]

    def notify(self, method):
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method}) + "\n")
        self.proc.stdin.flush()

    def request(self, method, params=None):
        self.n += 1
        msg = {"jsonrpc": "2.0", "id": self.n, "method": method}
        if params is not None:
            msg["params"] = params
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()
        return json.loads(self.proc.stdout.readline())

    def call(self, tool, args):
        return self.request("tools/call", {"name": tool, "arguments": args})

    def close(self):
        self.proc.stdin.close()
        self.proc.wait(timeout=30)


def main(argv):
    if MODE == "single":
        prompt = sys.stdin.read()
        log(mode=MODE, prompt=prompt, argv=argv)
        init()
        result("A")
        return 0
    if MODE == "hang":
        log(mode=MODE, pid=os.getpid())
        time.sleep(600)
        return 0
    server = Server(argv) if MODE == "episode" else None
    turns = 0
    init()
    for line in sys.stdin:
        if not line.strip():
            continue
        turns += 1
        content = json.loads(line)["message"]["content"]
        if turns == 1:
            if server:
                look = server.call(server.tools[0], {})
                filed = server.call("file", {"ids": ["TX-1"]})
                log(turn=1, look=look.get("result"), filed=filed.get("result"))
            log(turn=1, prompt=content, argv=argv)
            result("done")
        else:
            sentinel = os.path.exists(server.close_path) if server else None
            late = server.call(server.tools[0], {}) if server else None
            log(turn=turns, prompt=content, sentinel_before_report=sentinel, late=late and late.get("result"))
            if MODE == "no_report":
                continue
            result(REPORT)
    if server:
        server.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
