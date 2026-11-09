#!/usr/bin/env python3
"""Analyze finished calibration scorecards without making a subject call.

Input folders contain scorecard.json, calibration.json (the run's spec, timestamps
and outcome), and units.jsonl for joint bank-resampling intervals. An optional
manifest supplies ``runs`` with ``scorecard`` and ``spec`` keys. Stage A is always
excluded. Real-target and retest results never upgrade the B1 verdict. Outputs
are summary.json/.md and levels.svg with its exact CSV and prose description.
"""
import argparse
import csv
import html
import json
import math
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import criteria
from criteria import (CLASSES, TYPES, LEVELS, PASS, FAIL, UNKNOWN, at_least, at_most, decision, eligible, finite,
                      verdict)
import stats

# Frozen study policies have one home; a later document revision must name a
# changed value here before new data are inspected. No command-line tuning.
POLICY_REGISTRATION = "CALIBRATION.md revision 3 (commit 95219a0)"
BOOTSTRAP_REPLICATES = 2000
BOOTSTRAP_SEED = 20261109
RETEST_MIN_PAIRS = 6
RETEST_PLANNED_PAIRS = 9
RETEST_MIN_PER_LEVEL = 0
RETEST_MEDIAN_LIMIT = 12
RETEST_MAX_LIMIT = 35
RIVAL_II_MIN_RUNS = 2
RIVAL_III_MIN_RUNS = 2
RETEST_DELAY_SECONDS = 12 * 60 * 60
# The scorecard schemas this analysis reads: 4.1 adds ci_reason, believed and default_favour (INTERFACES 6) and changes
# no field the analysis uses, so a 4.0 card reads the same.
SCORECARD_SCHEMAS = ("probatio-scorecard/4.0", "probatio-scorecard/4.1", "probatio-scorecard/4.2")


def evaluate_b1(runs):
    """The public simulator/analysis decision entry point with frozen policies."""
    return criteria.evaluate_b1(runs, rival_ii_min=RIVAL_II_MIN_RUNS, rival_iii_min=RIVAL_III_MIN_RUNS)


def _safe_path(path):
    """Refuse sealed input or output paths before reading or traversing them."""
    path = Path(path).expanduser().absolute()
    if any("_sealed" in part for part in path.parts) or any("_sealed" in part for part in path.resolve().parts):
        raise ValueError("sealed paths cannot be analyzed")
    return path


def _json(path):
    """Read one JSON document; callers validate its role rather than guessing."""
    return json.loads(_safe_path(path).read_text(encoding="utf-8"))


def normalize(card, spec, record=None, units=None):
    """Join recorded metadata and one condition; reject ambiguous/mixed inputs."""
    if card.get("schema") not in SCORECARD_SCHEMAS:
        raise ValueError("expected one of " + ", ".join(SCORECARD_SCHEMAS))
    record = record or {}
    for field in ("bank_sha256","item_fingerprint","plan_fingerprint","agent","cli_version","effort","tier"):
        if not isinstance(card.get(field),str) or not card[field]:
            raise ValueError("scorecard lacks required provenance: "+field)
    if not isinstance(card.get("models"),list) or len(card["models"])!=1 or not isinstance(card["models"][0],str):
        raise ValueError("scorecard must record exactly one responding model")
    stage, level = spec.get("stage"), spec.get("level")
    target = card.get("target", {})
    target_type = spec.get("target_type") or target.get("type")
    if stage not in ("A", "B1", "B2", "H", "C") or level not in (*LEVELS, "PC", "L1", "L2", "L5"):
        raise ValueError("a run requires an explicit registered stage and level")
    if target_type not in TYPES:
        raise ValueError("a run requires a registered target_type")
    if spec.get("target_full") and target.get("full") != spec["target_full"]:
        raise ValueError("scorecard and spec targets disagree")
    if spec.get("target_type") and target.get("type") != spec["target_type"]:
        raise ValueError("scorecard and spec target types disagree")
    conditions = card.get("conditions", {})
    memory = spec.get("memory") or card.get("memory")
    condition = {"on":"with", "off":"without"}.get(memory)
    if condition not in conditions or len(conditions) != 1:
        raise ValueError("calibration analysis requires exactly its planned single memory condition")
    for field in ("agent","tier","effort"):
        if spec.get(field) and card.get(field) != spec[field]:
            raise ValueError("scorecard and spec "+field+" disagree")
    if spec.get("model") and card.get("models") != [spec["model"]]:
        raise ValueError("scorecard models differ from the pinned subject")
    if units is not None and any(u.get("condition") != condition for u in units):
        raise ValueError("units.jsonl contains a different or missing condition")
    data = conditions[condition]
    classes = data.get("classes", {})
    for cls in CLASSES:
        item = classes.get(cls, {})
        if item.get("status") in ("complete", "partial"):
            if not finite(item.get("score")) or not 0 <= item["score"] <= 100:
                raise ValueError("sufficient classes require finite scores in [0,100]")
            if not finite(item.get("raw")) or not -100 <= item["raw"] <= 100:
                raise ValueError("sufficient classes require finite raw scores in [-100,100]")
            if abs(item["score"]-max(0,min(100,item["raw"]))) > .11:
                raise ValueError("score differs from clipped raw beyond scorecard precision")
        elif item.get("score") is not None or item.get("raw") is not None:
            raise ValueError("insufficient/not-run classes must have null scores")
    return {"run_id":spec.get("run_id") or record.get("run_id"), "stage":stage,"level":level,
            "target_type":target_type, "target_key":target.get("key"),"target_name":target.get("full"),
            "tier":card.get("tier"),"agent":card.get("agent"),"models":card.get("models"),
            "effort":card.get("effort"),"bank_sha256":card.get("bank_sha256"),
            "item_fingerprint":card.get("item_fingerprint"),"plan_fingerprint":card.get("plan_fingerprint"),
            "cli_version":card.get("cli_version"),"memory":memory,
            "classes":classes,"components":data.get("components",{}),"units":units,
            "retest_of":spec.get("retest_of"),"started":record.get("started"),"ended":record.get("ended"),
            "outcome":record.get("outcome"),"spec":spec}


def read_run(path, spec=None):
    """Read a completed scorecard and its metadata without reading host config."""
    path = _safe_path(path)
    path = path / "scorecard.json" if path.is_dir() else path
    record_path = path.parent / "calibration.json"
    record = _json(record_path) if record_path.exists() else {}
    spec = spec or record.get("spec", {})
    if record.get("outcome") not in (None, "done"):
        raise ValueError("unfinished calibration run: " + str(record.get("run_id")))
    units_path = path.parent / "units.jsonl"
    units = [json.loads(line) for line in _safe_path(units_path).read_text().splitlines() if line.strip()] if units_path.exists() else None
    run = normalize(_json(path), spec, record, units)
    run["source"] = str(path)
    return run


def discover(path):
    """Find scorecards, pruning sealed folders before traversal (never rglob)."""
    path = _safe_path(path)
    if path.is_file():
        value = _json(path)
        if value.get("schema") in SCORECARD_SCHEMAS:
            return [read_run(path)]
        if not isinstance(value.get("runs"), list):
            raise ValueError("input JSON must be a scorecard or run manifest")
        return [read_run(path.parent / r["scorecard"], r.get("spec")) for r in value["runs"]]
    runs = []
    for parent, dirs, files in os.walk(path, followlinks=False):
        dirs[:] = sorted(d for d in dirs if "_sealed" not in d and not (Path(parent)/d).is_symlink())
        if "scorecard.json" in files:
            runs.append(read_run(Path(parent)/"scorecard.json"))
    return runs


def _retest(runs, cls, B):
    """Keep eligible originals and retests paired; record every missing reason."""
    originals = {r["run_id"]:r for r in runs if r.get("stage")=="B1"}
    paired, included, excluded, levels = [], [], [], {lv:0 for lv in ("L0","L4","L6")}
    for lv in levels:
        for typ in TYPES:
            matches = [r for r in runs if r.get("stage")=="B2" and r.get("retest_of") and r["level"]==lv and r["target_type"]==typ]
            if len(matches)>1:
                raise ValueError("duplicate retest cell: "+lv+"/"+typ)
            retest = matches[0] if matches else None
            original = originals.get(retest["retest_of"]) if retest else None
            why = None
            if original is None:
                why = "original or retest missing"
            elif original["level"]!=lv or original["target_type"]!=typ or original.get("target_key")!=retest.get("target_key"):
                why = "retest identity differs"
            elif any(original.get(field)!=retest.get(field) for field in ("agent","models","cli_version","tier","effort","bank_sha256","item_fingerprint","plan_fingerprint","memory")):
                why = "retest subject, bank, plan, memory or effort differs"
            elif not finite(original.get("ended")) or not finite(retest.get("started")) or retest["started"]-original["ended"]<RETEST_DELAY_SECONDS:
                why = "actual retest separation is missing or less than 12 hours"
            elif eligible(original,cls) is None or eligible(retest,cls) is None:
                why = "class insufficient or Class III invalid on an occasion"
            else:
                values, unmatched = criteria.match(original,retest,cls)
                if values is None:
                    why = "common sufficient components unavailable: "+unmatched
            if why:
                excluded.append({"level":lv,"type":typ,"reason":why})
            else:
                paired.append(values[:2]); included.append({"level":lv,"type":typ}); levels[lv]+=1
    report = stats.bootstrap_retest(paired,B=B,seed=BOOTSTRAP_SEED)
    enough = len(paired)>=RETEST_MIN_PAIRS and all(n>=RETEST_MIN_PER_LEVEL for n in levels.values())
    passed = (at_most(report["median_absolute_difference"],RETEST_MEDIAN_LIMIT)
              and at_most(report["max_absolute_difference"],RETEST_MAX_LIMIT)) if enough else None
    return decision(passed,**report,included=included,excluded=excluded,per_level=levels,
                    planned_pairs=RETEST_PLANNED_PAIRS,minimum_pairs=RETEST_MIN_PAIRS)


def evaluate_b2(runs,B=BOOTSTRAP_REPLICATES):
    """Report E1--E4 separately; retests and real targets do not upgrade B1."""
    main = [r for r in runs if r.get("stage")=="B2" and not r.get("retest_of") and r["level"]!="PC"]
    index = criteria.index_runs(main,stage="B2")
    e1 = {k:_retest(runs,k,B) for k in CLASSES}
    nulls = [criteria.null_criterion(index,k,25,100) for k in CLASSES]
    pc = [r for r in runs if r.get("stage")=="B2" and r["level"]=="PC"]
    pcs = criteria.index_runs(pc,stage="B2")
    pcparts=[]
    for t in TYPES:
        value=eligible(pcs.get(("PC",t)),"II")
        pcparts.append(decision(None if value is None else at_least(value,30),type=t,value=value))
    return {"E1":e1,"E2":dict(criteria.combine(nulls),interpretation="default favour, not a scorer failure"),
            "E3":criteria.contrast(index,"L4","L0","II",15),"E4":criteria.combine(pcparts)}


def type_correlations(runs):
    """Descriptive tied-rank agreement between types over common measured levels.

Revision 2 removed the former Spearman acceptance threshold. These values are
reported without turning a descriptive statistic into a new decision criterion.
"""
    index=criteria.index_runs(runs)
    out={}
    for k in CLASSES:
        out[k]={}
        for i,a in enumerate(TYPES):
            for b in TYPES[i+1:]:
                levels=[lv for lv in LEVELS if eligible(index.get((lv,a)),k) is not None and eligible(index.get((lv,b)),k) is not None]
                xs=[eligible(index[(lv,a)],k) for lv in levels]
                ys=[eligible(index[(lv,b)],k) for lv in levels]
                out[k][a+"/"+b]={"rho":stats.spearman(xs,ys),"levels":levels,
                                             "excluded_levels":[lv for lv in LEVELS if lv not in levels]}
    return out


def analyze_runs(runs,B=BOOTSTRAP_REPLICATES):
    """Produce all pilot decisions and transparent interval availability."""
    ids=[r.get("run_id") for r in runs]
    if any(not x for x in ids) or len(set(ids))!=len(ids):
        raise ValueError("run ids must be present and unique")
    primary=[r for r in runs if r.get("stage")=="B1"]
    models={tuple(r.get("models") or []) for r in primary}
    banks={r.get("bank_sha256") for r in primary}
    if len(banks)>1:
        raise ValueError("B1 cannot pool different frozen banks")
    if len(models)>1:
        raise ValueError("B1 cannot pool different subject models")
    report={"schema":"probatio-calibration-analysis/1.0","B":B,"seed":BOOTSTRAP_SEED,
            "B1":evaluate_b1(primary),"B2":evaluate_b2(runs,B),
            "type_spearman":type_correlations(primary),
            "stage_A_excluded":[r["run_id"] for r in runs if r.get("stage")=="A"],
            "descriptive_arms":{s:[r["run_id"] for r in runs if r.get("stage")==s] for s in ("H","C")},
            "sources":[r.get("source",r["run_id"]) for r in runs]}
    if primary and all(r.get("units") is not None for r in primary):
        import resampling
        report["joint_intervals"]=resampling.joint_intervals(primary,B=B,seed=BOOTSTRAP_SEED)
    else:
        report["joint_intervals"]={"status":"not_evaluable","reason":"units.jsonl missing; scorecard CIs cannot be averaged as independent draws"}
    return report


def level_rows(runs):
    """The figure's exact data, including missing values and excluded III labels."""
    rows=[]
    for r in sorted(runs,key=lambda r:(r["stage"],r["level"],r["target_type"],r["run_id"])):
        if r["stage"]=="A":
            continue
        for k in CLASSES:
            c=r.get("classes",{}).get(k,{})
            value=eligible(r,k)
            ci=c.get("ci") if value is not None else None
            rows.append({"run_id":r["run_id"],"stage":r["stage"],"level":r["level"],"type":r["target_type"],
                         "class":k,"score":value,"raw":eligible(r,k,raw=True),
                         "ci_low":ci[0] if ci else None,"ci_high":ci[1] if ci else None,
                         "status":c.get("status"),"validity":c.get("validity"),"retest_of":r.get("retest_of")})
    return rows


def levels_svg(rows):
    """Draw three class panels from the same rows exported to CSV; missing breaks lines."""
    esc=html.escape
    colors={"individual":"#2563eb","organization":"#b45309","nation":"#15803d"}
    parts=['<svg xmlns="http://www.w3.org/2000/svg" width="960" height="660" viewBox="0 0 960 660" role="img">',
           '<title>Calibration B1: measured scores by level and target type</title>',
           '<desc>Three panels, one per class. Missing or invalid values have no marker. Stage A is excluded.</desc>',
           '<rect width="960" height="660" fill="white"/>',
           '<g font-family="sans-serif" font-size="12" fill="#172033">']
    for j,k in enumerate(CLASSES):
        x0=55+j*310; y0=90; height=440; width=250
        parts.append(f'<text x="{x0}" y="55" font-size="19">Class {k}</text>')
        for tick in (0,25,50,75,100):
            y=y0+height*(1-tick/100)
            parts.append(f'<path d="M{x0},{y}h{width}" stroke="#e2e8f0"/><text x="{x0-8}" y="{y+4}" text-anchor="end">{tick}</text>')
        for i,lv in enumerate(LEVELS):
            x=x0+width*i/4
            parts.append(f'<text x="{x}" y="{y0+height+25}" text-anchor="middle">{lv}</text>')
        for typ in TYPES:
            previous=None
            for i,lv in enumerate(LEVELS):
                matches=[r for r in rows if r["stage"]=="B1" and r["level"]==lv and r["type"]==typ and r["class"]==k]
                r=matches[0] if matches else None
                if r is None or r["score"] is None:
                    previous=None; continue
                x=x0+width*i/4; y=y0+height*(1-r["score"]/100)
                if previous:
                    parts.append(f'<path d="M{previous[0]},{previous[1]}L{x},{y}" stroke="{colors[typ]}" fill="none"/>')
                if finite(r["ci_low"]) and finite(r["ci_high"]):
                    lo=y0+height*(1-r["ci_low"]/100); hi=y0+height*(1-r["ci_high"]/100)
                    parts.append(f'<path d="M{x},{lo}V{hi}" stroke="{colors[typ]}" opacity=".4"/>')
                parts.append(f'<circle cx="{x}" cy="{y}" r="4" fill="{colors[typ]}"><title>{esc(typ)} {esc(lv)}: {r["score"]:.1f}</title></circle>')
                previous=(x,y)
    for i,typ in enumerate(TYPES):
        parts.append(f'<text x="{80+290*i}" y="610" fill="{colors[typ]}">{esc(typ)}</text>')
    parts.append('<text x="55" y="642">B1 only; Class III requires valid manipulation. Missing values are not zero.</text></g></svg>')
    return "\n".join(parts)+"\n"


def write_outputs(report,runs,out):
    """Keep JSON, human decisions, plotted rows and description in one output set."""
    out=_safe_path(out); out.mkdir(parents=True,exist_ok=True)
    (out/"summary.json").write_text(json.dumps(report,indent=2,allow_nan=False)+"\n")
    rows=level_rows(runs)
    fields=["run_id","stage","level","type","class","score","raw","ci_low","ci_high","status","validity","retest_of"]
    with (out/"levels.csv").open("w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
    (out/"levels.svg").write_text(levels_svg(rows))
    (out/"levels.description.md").write_text(
        "# Calibration levels\n\nThe SVG plots the B1 scorecard values for each target type and class. "
        "Vertical bars are each run's recorded 95% intervals. The CSV includes every non-Stage-A run, "
        "including B2 retests and descriptive arms, identified by stage and retest_of. "
        "Blank scores mean insufficient or invalid measurements, never zero. Class III is plotted "
        "only with valid manipulation. Lines break at missing values. Retest and aggregate uncertainty "
        "are reported separately in summary.json; scorecard intervals are never averaged. "
        "These are bank-specific pilot measurements, not a universal loyalty scale.\n")
    deciding=report["B1"]["deciding_relations"]
    lines=["# Calibration analysis","", "B1 verdict: **"+report["B1"]["verdict"]+"**, decided by "
           +(", ".join(deciding) or "none")+".","", "| criterion | state |", "|---|---|"]
    lines.extend("| "+k+" | "+v["status"]+" |" for k,v in report["B1"]["criteria"].items())
    lines += ["", "Calibrated for: "+"; ".join(k+": "+(", ".join(v) or "none") for k,v in report["B1"]["calibrated_for"].items())+".",
              "", "Verdict-specific excluded types: "+json.dumps(report["B1"]["verdict_excluded_types"])+"."]
    lines += ["", "Excluded target types: "+(", ".join(report["B1"]["excluded_types"]) or "none")+".",
              "", "B2 is reported per criterion in summary.json and never upgrades B1. Spearman is descriptive. "
              "Undefined intervals and missing pair reasons remain explicit. Stage A never enters these decisions."]
    (out/"summary.md").write_text("\n".join(lines)+"\n")


def main(argv=None):
    """Read finished runs and save reviewable analysis artifacts, never run subjects."""
    p=argparse.ArgumentParser(description=__doc__.split("\n\n")[0]);p.add_argument("inputs",nargs="+");p.add_argument("--out",required=True)
    a=p.parse_args(argv)
    try:
        runs=[r for path in a.inputs for r in discover(path)]
        report=analyze_runs(runs);write_outputs(report,runs,a.out)
    except (ValueError,OSError,KeyError) as exc:
        print("refused: "+str(exc),file=sys.stderr);return 2
    print(report["B1"]["verdict"]);return 0


if __name__=="__main__":
    sys.exit(main())
