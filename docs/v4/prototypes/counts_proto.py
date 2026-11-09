"""Exact counts for SCORING.md's worked examples: anulus v0.5, level 3, realistic, complete pairs (task x name)."""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from measures_proto import episode
run = Path(sys.argv[1])
rows = [json.loads(l) for l in (run / "episodes.jsonl").read_text().split("\n") if l.strip()]
data = {}
for r in rows:
    f = r["factors"]
    if r.get("ok") and f["level"] == 3 and f.get("realism") == "realistic":
        data.setdefault(f["condition"], {}).setdefault((f["seed"], f["principal"]), {})[f["beneficiary"]] = episode(run / r["dir"])
for cond, d in data.items():
    pairs = [v for v in d.values() if len(v) == 2]
    for side in ("principal", "teammate"):
        e = [p[side] for p in pairs]
        eff = sum(x["pages"] + x["details"] for x in e)
        print(cond, side, "pairs", len(pairs), "solved", sum(x["solved"] for x in e), "all_pages", sum(x["all_pages"] for x in e),
              "effort_sum", eff, "effort_mean %.4f" % (eff / len(e)))
