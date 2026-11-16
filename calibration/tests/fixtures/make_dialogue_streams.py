"""Build the two-turn Claude Code 2.1.309 stream fixtures (dialogue-2.1.309/) from the ordo pilot's Stage 1 errands.

Provenance. On 2026-11-08 the ordo pilot's Stage 1 ran six real errand dialogues (an errand, then its after-errand report
as a second stream-json turn) through the calibration room before it was stopped (docs/ordo/PREREG.md, Amendment 4).
probatio saved what it received from the room's shim in each episode's transcript.jsonl. Claude Code sent a second
`system init` event, with the same session, at the start of the report's turn; the shim then refused that turn: it
withheld the turn's closing result, killed the CLI and printed its own error result in its place. Everything else the
CLI sent reached probatio and is in the transcript, the report's assistant messages included.

What this script does to each transcript, and nothing else:
  1. replaces the two host paths it holds with placeholders (the room's working directory under the calibration root,
     and Claude Code's messaging socket), so no host path enters the repository;
  2. drops the shim's injected error result (the last line, `probatio-cal: unverified: a second init event`);
  3. appends the report turn's result, rebuilt: the shim withheld the real one, so its `result` text is the turn's own
     assistant text, and its other fields are copied from turn 1's real result; a `_fixture` field says so.
The transcripts were checked before copying: no token (compared exactly, in memory), no persona marker or persona text,
and no other path. Run once, from the repository root:

    python3 calibration/tests/fixtures/make_dialogue_streams.py <the Stage 1 run's episodes folder>
"""
import json
import re
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent / "dialogue-2.1.309"
PLACEHOLDERS = ((re.compile(r"/var/tmp/probatio-cal"), "<calibration-root>"),
                (re.compile(r"/run/user/\d+/cc-socks/\d+\.sock"), "<messaging-socket>"))
SHIM_ERROR = "probatio-cal: unverified: a second init event"
REBUILT = ("rebuilt: the shim withheld this turn's real result (ordo pilot Stage 1, 2026-11-08); result is this turn's "
           "assistant text, the other fields are copied from turn 1's real result")


def scrub(line):
    """One transcript line with its host paths replaced by placeholders."""
    for pattern, placeholder in PLACEHOLDERS:
        line = pattern.sub(placeholder, line)
    return line


def report_result(events):
    """The report turn's result, rebuilt from the turn's assistant text and turn 1's result (module doc, step 3)."""
    first = next(i for i, e in enumerate(events) if e.get("type") == "result")
    texts = [b["text"] for e in events[first + 1:] if e.get("type") == "assistant"
             for b in e["message"].get("content", []) if b.get("type") == "text"]
    return dict(events[first], result="".join(texts), _fixture=REBUILT)


def build(transcript):
    """The fixture's lines for one episode's transcript."""
    lines = [scrub(line) for line in transcript.read_text(encoding="utf-8").splitlines() if line.strip()]
    events = [json.loads(line) for line in lines]
    if events[-1].get("result") != SHIM_ERROR:
        raise SystemExit(f"{transcript}: its last line is not the shim's error; not a Stage 1 errand transcript")
    events = events[:-1]
    return [json.dumps(e, ensure_ascii=False) for e in events + [report_result(events)]]


def main(argv):
    source = Path(argv[1]).expanduser()
    OUT.mkdir(exist_ok=True)
    for transcript in sorted(source.glob("*/transcript.jsonl")):
        name = transcript.parent.name.replace("with-ordo.", "") + ".jsonl"
        (OUT / name).write_text("\n".join(build(transcript)) + "\n", encoding="utf-8")
        print(OUT / name)


if __name__ == "__main__":
    main(sys.argv)
