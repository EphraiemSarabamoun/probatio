"""The measured time model's evidence: per-call seconds from the ordo pilot's kept runs, summarised (no model call).

The pilot (docs/ordo/PILOT_RUNBOOK.md) ran on one host on 2026-11-08: Claude Code 2.1.309, claude-sonnet-5-5, effort
medium, --jobs 4. Each kept run's replies.jsonl holds one record per call with its seconds (ask's one retry included),
and its quota.jsonl one reading per CLI process, so a call with two readings was retried. This script reads those
files without changing them and writes the aggregates docs/ordo/budget.py's PRICING cites for its sonnet profile:
seconds per question by rate family and per errand by cell, each run's units, minutes and jobs-4 efficiency (summed
call seconds over jobs times the runner's own elapsed counter, which the harness log prints), a two-parameter fit of
wall minutes, and the retries. It holds aggregates only: no prompt, reply or per-call record, since the pilot is
private. A test holds PRICING's sonnet rates equal to the means here at two decimals.

    python3 docs/ordo/timing.py <pilot results folder> > docs/ordo/timing.json
"""
import json
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from budget import PRICING  # noqa: E402  (the rate families, one home)

RUNS = ("ordo-s1-neutral-quick", "ordo-s2-neutral", "ordo-s2-g_wish_individual", "ordo-s2-g_wish_nation")
QUESTION_DATASETS = ("ordo.I", "ordo.RF", "ordo.check")
ELAPSED = re.compile(r"(\d+)/(\d+) · .*? · (\d+)m (\d+)s")       # the pilot runner's progress line
CONTEXT = {"source": "the ordo pilot's four kept runs (the stopped first Stage 1, kept as evidence, excluded)",
           "date": "2026-11-08", "cli": "Claude Code 2.1.309", "model": "claude-sonnet-5-5", "effort": "medium",
           "jobs": 4, "host": "calibration-host"}


def summary(xs):
    """n, mean, median and p90 of a list of seconds, rounded to hundredths."""
    xs = sorted(xs)
    return {"n": len(xs), "mean": round(statistics.mean(xs), 2), "median": round(statistics.median(xs), 2),
            "p90": round(xs[int(0.9 * (len(xs) - 1))], 2)}


def records(path):
    """The JSON lines of a file that parse, read without changing it."""
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def runner_seconds(log):
    """The runner's own elapsed seconds at its last progress line, or None."""
    last = None
    for m in ELAPSED.finditer(log.read_text(encoding="utf-8", errors="replace")):
        last = m
    return int(last.group(3)) * 60 + int(last.group(4)) if last else None


def is_question(rec):
    return rec["dataset"] in QUESTION_DATASETS


def fit(points):
    """Least squares through the origin of wall minutes = a * questions + b * errands (the normal equations)."""
    sqq = sum(q * q for q, e, w in points)
    see = sum(e * e for q, e, w in points)
    sqe = sum(q * e for q, e, w in points)
    sqw = sum(q * w for q, e, w in points)
    sew = sum(e * w for q, e, w in points)
    det = sqq * see - sqe * sqe
    return (sqw * see - sew * sqe) / det, (sew * sqq - sqw * sqe) / det


def main(root):
    family_of = PRICING["family_of"]
    by_family, by_cell, runs, points, retried, errors = defaultdict(list), defaultdict(list), {}, [], 0, 0
    for name in RUNS:
        folder = root / "runs" / name
        rows = records(folder / "replies.jsonl")
        readings = defaultdict(int)
        for q in records(folder / "quota.jsonl"):
            readings[q.get("call")] += 1
        retried += sum(1 for n in readings.values() if n > 1)
        errors += sum(1 for r in rows if r.get("error"))
        q = [r for r in rows if is_question(r)]
        e = [r for r in rows if not is_question(r)]
        for r in q:
            by_family[family_of[r["meta"]["component"]]].append(r["seconds"])
        for r in e:
            by_cell[r["meta"]["cell"]].append(r["seconds"])
        wall = runner_seconds(folder / "harness.log")
        busy = sum(r["seconds"] for r in rows)
        runs[name] = {"questions": len(q), "errands": len(e), "minutes": round(wall / 60, 2),
                      "efficiency": round(busy / (CONTEXT["jobs"] * wall), 3)}
        points.append((len(q), len(e), wall / 60))
    a, b = fit(points)
    out = dict(CONTEXT, runs=runs,
               question_seconds={f: summary(v) for f, v in sorted(by_family.items())},
               episode_seconds={c: summary(v) for c, v in sorted(by_cell.items())},
               fit={"minutes_per_question": round(a, 4), "minutes_per_errand": round(b, 4),
                    "largest_residual_minutes": round(max(abs(a * q + b * e - w) for q, e, w in points), 2)},
               retries={"retried_calls": retried, "error_records": errors})
    json.dump(out, sys.stdout, indent=1)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
