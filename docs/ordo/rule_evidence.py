"""The errand rule's evidence (PREREG.md Amendment 6): docs/ordo/errand_rule.json, which ordo.errand_rule_status reads.

It records what adopting candidate C rests on, so the completion gate can check it rather than trust a heading:
- the amendment, the rule, its rows, and ordo.rule_fingerprint(), the fingerprint of every scorer the evidence rests on;
- the null simulations that accepted it: Stage 0b's stage0b.csv by sha256, with how it was run (the script, the commit
  that wrote the csv, the pools, the seeds and B that stage0.py used, the replicates the csv counts) and the candidates
  it tested;
- the quick tier's allocation rerun under the rule: quick_sim.py's output by sha256, its rule and replicates, and each
  menu's minimax winner with the plan fingerprint the simulation recorded beside it.
Every value is read from the artifacts and the scripts that wrote them, never from ordo's frozen record, so the gate's
comparison of the two means something. Before writing, the builder runs the gate's own checks and refuses (exit 2,
every problem on stderr, nothing written) an artifact the gate would refuse: a wrong rule, a missing or failed cell,
other candidates, a stale scorer fingerprint, a winner that is not today's quick plan. Nothing here runs a simulation.

    python3 docs/ordo/rule_evidence.py docs/ordo/quick_sim.json --out docs/ordo/errand_rule.json
"""
import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))
import ordo as O  # noqa: E402

STAGE0B = HERE / "stage0b.csv"


class EvidenceError(ValueError):
    """An artifact the completion gate would refuse; the message names every problem."""


def refuse_if(problems, what):
    if problems:
        raise EvidenceError(f"the {what} cannot back the rule:\n" + "\n".join(f"- {p}" for p in problems))


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def relative(path):
    return str(Path(path).resolve().relative_to(ROOT))


def writing_commit(path):
    """The commit that last wrote a committed, unmodified file: its generator's provenance."""
    rel = relative(path)
    git = ["git", "-C", str(ROOT)]
    if subprocess.run(git + ["diff", "--quiet", "HEAD", "--", rel]).returncode != 0:
        raise EvidenceError(f"{rel} has uncommitted changes, so no commit wrote it")
    out = subprocess.run(git + ["log", "-1", "--format=%H", "--", rel], capture_output=True, text=True)
    if out.returncode != 0 or not out.stdout.strip():
        raise EvidenceError(f"git cannot say which commit wrote {rel}")
    return out.stdout.strip()


def null_acceptance():
    """Stage 0b's record: the csv by sha256, the script and commit that wrote it, the seeds and B stage0.py used, the
    pools, replicates and candidates the csv holds, and the adopted candidate. Refused when the csv fails the gate's
    checks (ordo.stage0b_problems) or the record differs from what the gate expects (ordo.null_record)."""
    import stage0
    rows, unreadable = O.read_csv_rows(STAGE0B, "null simulations")
    refuse_if(unreadable or O.stage0b_problems(rows), "null simulations")
    mine = [r for r in rows if r["rule"] == O.ADOPTED_CANDIDATE and r["target"]]
    rec = {"path": relative(STAGE0B), "sha256": sha256_file(STAGE0B), "script": relative(stage0.__file__),
           "commit": writing_commit(STAGE0B), "pools": rows[0]["pools"], "seeds": dict(stage0.SEED_TAGS),
           "B": O.BOOTSTRAP_B,                     # stage0.init's bootstrap_of(probe, O.BOOTSTRAP_B)
           "replicates": int(mine[0]["n"]) + int(mine[0]["undefined"]),     # stage0b_problems: the same in every cell
           "candidates": sorted({r["rule"] for r in rows}), "adopted": O.ADOPTED_CANDIDATE}
    refuse_if([f"its {k} is {rec[k]!r}, not {want!r}" for k, want in O.null_record().items() if rec[k] != want],
              "null simulations")
    return rec


def allocation(path):
    """quick_sim.py's output by sha256, its rule and replicates, and each menu's minimax winner recomputed from its
    widths, with the plan fingerprint the simulation recorded. Refused when the file fails the gate's checks
    (ordo.allocation_contents) or a winner is not today's quick plan."""
    sim, unreadable = O.read_json_object(path, "allocation")
    refuse_if(unreadable, "allocation")
    winners, problems = O.allocation_contents(sim)
    plans = O.quick_plan_fingerprints()
    problems += [f"the {menu} menu's minimax winner is not today's quick plan" for menu, won in winners.items()
                 if won["plan_fingerprint"] != plans[menu]]
    refuse_if(problems, "allocation")
    return {"path": relative(path), "sha256": sha256_file(path), "errand_interval_rule": sim["errand_interval_rule"],
            "replicates": sim["replicates"], "winners": winners}


def evidence(alloc_path):
    """The whole record, refused unless the gate reads it adopted."""
    out = {"amendment": O.RULE_AMENDMENT, "rule": O.ERRAND_INTERVAL_RULE, "rows": list(O.HALVED_ROWS),
           "scorer_fingerprint": O.rule_fingerprint(), "null_acceptance": null_acceptance(),
           "allocation": allocation(alloc_path)}
    with tempfile.TemporaryDirectory() as tmp:
        trial = Path(tmp) / "errand_rule.json"
        trial.write_text(json.dumps(out), encoding="utf-8")
        status = O.errand_rule_status(trial)
    refuse_if(status["problems"], "evidence")
    return out


def main(argv=None):
    p = argparse.ArgumentParser(description="Write the errand rule's evidence (PREREG.md Amendment 6).")
    p.add_argument("allocation", help="quick_sim.py's output, such as docs/ordo/quick_sim.json")
    p.add_argument("--out", help="write the evidence here (default: stdout)")
    a = p.parse_args(argv)
    try:
        text = json.dumps(evidence(a.allocation), indent=1) + "\n"
    except EvidenceError as e:
        print(f"rule_evidence: {e}", file=sys.stderr)
        return 2
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
