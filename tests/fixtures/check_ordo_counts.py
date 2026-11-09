"""Check phase 5's dry runs (docs/ordo/DESIGN.md 15.2): every count against budget.json, and today's v4 plan unchanged.

    python3 tests/fixtures/check_ordo_counts.py docs/ordo/budget.json SCRATCH
    python3 tests/fixtures/check_ordo_counts.py --same-plan DRY_RUN_NEW DRY_RUN_OLD

The first form reads each dry-run folder's rendered.jsonl (one line per planned item, with its meta and world, as
probatio's --dry-run --out writes it), counts it with ordo.count_plan, and compares every number with the budget.json
case that folder stands for (DRY_RUNS). The second form compares two dry runs' rendered.jsonl byte for byte, which
is requirement 1: `--target-type organization --target Mozilla` must plan exactly what 86e918c plans. Either form
exits 1 and names the first difference.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import ordo  # noqa: E402

# dry-run folder (under SCRATCH) -> the budget.json case it must reproduce (budget.json["cases"])
DRY_RUNS = {"dry-smoke": "smoke: individual:organization:nation",
            "dry-quick": "quick: individual:organization:nation",
            "dry-standard": "standard: individual:organization:nation",
            "dry-full": "full: individual:organization:nation",
            "dry-named": "standard: individual:organization:nation + named organization",
            "dry-quick-named": "quick: individual:organization:nation + named organization",
            "dry-quick-named-each": "quick: individual:organization:nation + a named target of each type",
            "dry-standard-named-each": "standard: individual:organization:nation + a named target of each type",
            "dry-pair": "standard: individual:organization",
            "dry-codex": "standard: individual:organization:nation"}
KEYS = ("questions", "episodes", "units", "by_family", "claude_requests", "codex_requests", "retry_ceiling_claude",
        "retry_ceiling_codex", "sonnet_min", "opus_min", "codex_min", "warn")
MINUTES_TOLERANCE = 6e-4                # budget.json rounds minutes to three decimals


def rendered(folder):
    path = Path(folder) / "rendered.jsonl"
    if not path.is_file():
        raise SystemExit(f"{path} is missing; run probatio.py --dry-run --out {folder} first")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def differences(budget, scratch):
    """Every (folder, key, counted, budgeted) that disagrees."""
    out = []
    for folder, case in DRY_RUNS.items():
        counted, want = ordo.count_plan(rendered(Path(scratch) / folder)), budget["cases"][case]
        out += [(folder, k, counted[k], want[k]) for k in KEYS if k in want and not same(counted[k], want[k])]
    return out


def same(counted, budgeted):
    """Equal, minutes within budget.json's rounding."""
    if isinstance(budgeted, float):
        return abs(counted - budgeted) <= MINUTES_TOLERANCE
    return counted == budgeted


def same_plan(new, old):
    """The first line where two dry runs' rendered.jsonl differ, or None when they are byte-identical."""
    a = (Path(new) / "rendered.jsonl").read_bytes().splitlines()
    b = (Path(old) / "rendered.jsonl").read_bytes().splitlines()
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return f"line {i + 1} differs"
    return None if len(a) == len(b) else f"{len(a)} lines against {len(b)}"


def main(argv):
    if argv[:1] == ["--same-plan"]:
        diff = same_plan(argv[1], argv[2])
        if diff:
            raise SystemExit(f"the plans differ: {diff}")
        print("same plan, byte for byte")
        return
    budget = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    bad = differences(budget, argv[1])
    for folder, k, got, want in bad:
        print(f"{folder}: {k} counted {got}, budget.json says {want}", file=sys.stderr)
    if bad:
        raise SystemExit(1)
    print(f"every dry run matches budget.json ({len(DRY_RUNS)} folders)")


if __name__ == "__main__":
    main(sys.argv[1:])
