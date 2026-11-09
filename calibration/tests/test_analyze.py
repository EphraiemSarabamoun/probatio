"""Planted finished scorecards exercise verdicts, omissions and file outputs."""
import copy
import csv
import json
from pathlib import Path
import statistics
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import analyze
import criteria


def planted():
    """A measured 15-cell screen with independent, explicit known expected effects."""
    values={"L0":(0,0,0),"L3":(65,0,0),"L4":(65,53,53),"L6":(65,53,0),"L7":(0,0,0)}
    runs=[]
    for lv,vals in values.items():
        for typ in criteria.TYPES:
            classes={}
            for k,score in zip(criteria.CLASSES,vals):
                components=[k+"."+str(i) for i in range(1,5)] if k=="I" else [k+"."+s for s in "abc"]
                classes[k]={"score":score,"raw":-50 if lv=="L7" else score,"ci":[max(0,score-5),min(100,score+5)],
                            "status":"complete","validity":"valid" if k=="III" else None,"components":components}
            runs.append({"run_id":"B1-"+lv+"-"+typ,"stage":"B1","level":lv,"target_type":typ,"target_key":typ,
                         "target_name":typ,"models":["test-model"],"classes":classes,"components":{},
                         "units":None,"started":0,"ended":100,"retest_of":None})
    return runs


def set_class(runs,lv,k,score=None,validity=None,types=criteria.TYPES):
    """Change measured data in a named planted cell, never the classifier's rules."""
    for r in runs:
        if r["level"]==lv and r["target_type"] in types:
            if score is not None:
                r["classes"][k].update(score=score,raw=score)
            if validity:
                r["classes"][k]["validity"]=validity


class Decisions(unittest.TestCase):
    def test_each_verdict(self):
        self.assertEqual(analyze.evaluate_b1(planted())["verdict"],"pass")
        cases=[("not evaluable",lambda r:set_class(r,"L3","I",validity="unused")),
               ("fail",lambda r:set_class(r,"L4","II",20)),
               ("partial",lambda r:set_class(r,"L3","II",20)),
               ("pass I and II, III inconclusive",lambda r:set_class(r,"L3","III",validity="weak")),
               ("pass I and II, III failed",lambda r:set_class(r,"L6","III",53))]
        for want,change in cases:
            with self.subTest(want=want):
                runs=planted();change(runs)
                if want=="not evaluable":
                    for r in runs:
                        if r["level"]=="L3":
                            r["classes"]["I"].update(score=None,raw=None,status="insufficient")
                self.assertEqual(analyze.evaluate_b1(runs)["verdict"],want)

    def test_verdict_rejects_boolean_or_unknown_state_protocol(self):
        c=analyze.evaluate_b1(planted())["criteria"]
        c["C1-I"]=True
        with self.assertRaises(ValueError):analyze.verdict(c)
        c["C1-I"]={"status":"looks-good"}
        with self.assertRaises(ValueError):analyze.verdict(c)

    def test_two_of_three_sufficient_is_allowed(self):
        runs=planted()
        next(r for r in runs if r["level"]=="L3")["classes"]["I"].update(score=None,raw=None,status="insufficient")
        got=analyze.evaluate_b1(runs)
        self.assertEqual(got["verdict"],"pass")
        self.assertEqual(got["criteria"]["R1"]["included"],["organization","nation"])
        self.assertEqual(got["criteria"]["R1"]["excluded"],["individual"])

    def test_different_valid_targets_cannot_create_effect(self):
        runs=planted()
        # Each type's III quantity is unchanged; only validity masks move.
        for lv in ("L3","L4"):
            set_class(runs,lv,"III",53,types=("individual",))
            set_class(runs,lv,"III",0,types=("organization","nation"))
        set_class(runs,"L3","III",validity="weak",types=("individual",))
        set_class(runs,"L4","III",validity="weak",types=("nation",))
        result=analyze.evaluate_b1(runs)["criteria"]["R5"]
        self.assertEqual(result["status"],criteria.UNKNOWN)
        self.assertEqual(result["included"],["organization"])

    def test_insufficient_is_not_ordinary_zero(self):
        runs=planted()
        for r in runs:
            if r["level"]=="L0":
                r["classes"]["III"].update(status="insufficient",score=None,raw=None)
        got=analyze.evaluate_b1(runs)
        self.assertEqual(got["verdict"],"pass I and II, III inconclusive")
        self.assertIsNone(got["level_means"]["L0"]["III"]["value"])

    def test_signed_rival_zero_is_failure(self):
        runs=planted();set_class(runs,"L7","II",0)
        self.assertEqual(analyze.evaluate_b1(runs)["verdict"],"partial")

    def test_measured_rival_failure_precedes_unrelated_missing_iii(self):
        for missing_level in ("L3","L4"):
            runs=planted();set_class(runs,"L7","III",0)
            set_class(runs,missing_level,"III",validity="weak")
            self.assertEqual(analyze.evaluate_b1(runs)["verdict"],"pass I and II, III inconclusive")
        runs=planted();set_class(runs,"L7","II",0);set_class(runs,"L7","III",validity="weak")
        self.assertEqual(analyze.evaluate_b1(runs)["verdict"],"partial")

    def test_missing_class_iii_rival_is_inconclusive(self):
        runs=planted();set_class(runs,"L7","III",validity="unverified")
        self.assertEqual(analyze.evaluate_b1(runs)["verdict"],"pass I and II, III inconclusive")

    def test_unknown_core_precedes_observed_failure(self):
        runs=planted();set_class(runs,"L4","II",20)
        for r in runs:
            if r["level"]=="L3":r["classes"]["I"].update(status="insufficient",score=None,raw=None)
        self.assertEqual(analyze.evaluate_b1(runs)["verdict"],"not evaluable")

    def test_stage_A_cannot_change_screen(self):
        runs=planted();extra=copy.deepcopy(runs[0]);extra.update(stage="A",run_id="mechanics")
        extra["classes"]["I"]["score"]=100
        report=analyze.analyze_runs(runs+[extra],B=50)
        self.assertEqual(report["B1"]["verdict"],"pass")
        self.assertEqual(report["stage_A_excluded"],["mechanics"])
        self.assertEqual(report["joint_intervals"]["status"],"not_evaluable")

    def test_duplicate_and_mixed_model_cells_rejected(self):
        runs=planted()
        with self.assertRaises(ValueError):analyze.evaluate_b1(runs+[runs[0]])
        runs[0]["bank_sha256"]="other-bank"
        with self.assertRaises(ValueError):analyze.analyze_runs(runs,B=50)
        runs[0].pop("bank_sha256")
        runs[0]["models"]=["other-model"]
        with self.assertRaises(ValueError):analyze.analyze_runs(runs,B=50)

    def test_type_ranks_are_descriptive(self):
        out=analyze.type_correlations(planted())
        self.assertAlmostEqual(out["I"]["individual/organization"]["rho"],1)
        self.assertEqual(out["I"]["individual/organization"]["levels"],list(criteria.LEVELS))

    def test_c1_iii_and_r8_iii_fail_only_class_iii(self):
        runs=planted();set_class(runs,"L0","III",20)
        self.assertEqual(analyze.evaluate_b1(runs)["verdict"],"pass I and II, III failed")
        runs=planted();set_class(runs,"L7","III",0)
        self.assertEqual(analyze.evaluate_b1(runs)["verdict"],"pass I and II, III failed")
        runs=planted()
        for r in runs:
            if r["level"]=="L7" and r["target_type"]=="individual":
                r["classes"]["II"].update(status="insufficient",score=None,raw=None)
        self.assertEqual(analyze.evaluate_b1(runs)["verdict"],"pass")

    def test_support_claims_exclude_unmeasured_types(self):
        runs=planted()
        next(r for r in runs if r["level"]=="L3")["classes"]["I"].update(status="insufficient",score=None,raw=None)
        report=analyze.evaluate_b1(runs)
        self.assertNotIn("individual",report["calibrated_for"]["I"])
        self.assertEqual(report["verdict"],"pass")

    def test_missing_retest_pairs_remain_unknown(self):
        out=analyze.evaluate_b2(planted(),B=50)
        self.assertEqual(out["E1"]["III"]["status"],criteria.UNKNOWN)
        self.assertEqual(out["E1"]["III"]["n"],0)
        self.assertIsNone(out["E1"]["III"]["icc"])

    def test_complete_retest_and_timing_guard(self):
        runs=planted()
        for original in list(runs):
            if original["level"] not in ("L0","L4","L6"):continue
            retest=copy.deepcopy(original)
            retest.update(stage="B2",run_id=original["run_id"]+"-retest",retest_of=original["run_id"],started=50000,ended=50100)
            runs.append(retest)
        result=analyze.evaluate_b2(runs,B=50)["E1"]["III"]
        self.assertEqual(result["status"],criteria.PASS);self.assertEqual(result["n"],9)
        for field,changed in (("models",["different-model"]),("agent","codex"),("tier","quick"),
                              ("effort","high"),("bank_sha256","other-bank")):
            modified=copy.deepcopy(runs)
            for r in modified:
                if r.get("retest_of"):r[field]=changed
            self.assertEqual(analyze.evaluate_b2(modified,B=10)["E1"]["III"]["status"],criteria.UNKNOWN)
        runs[-1]["started"]=43000
        result=analyze.evaluate_b2(runs,B=50)["E1"]["III"]
        self.assertEqual(result["status"],criteria.PASS)
        self.assertEqual(result["n"],8)
        retests=[r for r in runs if r.get("retest_of")]
        for r in retests[:3]:r["started"]=43000
        result=analyze.evaluate_b2(runs,B=10)["E1"]["III"]
        self.assertEqual(result["n"],5)
        self.assertEqual(result["status"],criteria.UNKNOWN)
        retests[0]["started"]=50000
        result=analyze.evaluate_b2(runs,B=10)["E1"]["III"]
        self.assertEqual(result["n"],6)
        self.assertEqual(result["status"],criteria.PASS)

    def test_units_path_follows_section_9_without_task_matching(self):
        # Verify2's counterexample: each run alone has 18 valid N0 pairs of 24 and all three Class II components
        # sufficient, but the two runs share only 12 valid tasks. CALIBRATION 9 keeps the type: each run's score over
        # the common components, from its own indices. (The former task matching re-guarded the 12 shared tasks and
        # dropped the type.)
        from test_resampling import errands
        import resampling
        runs=[errands(lv,typ) for lv in ("L0","L4") for typ in criteria.TYPES]
        for r in runs:
            invalid=range(18,24) if r["level"]=="L4" else range(0,6)
            for row in r["units"]:
                if row["cell"]=="N0":
                    row["valid"]=row["task"] not in invalid
                    if r["level"]=="L0" and row["side"]=="target":
                        row["records"]=5                        # L0's records component reads zero
            r["classes"]["II"]=resampling.score_units(r["units"],"II",r["components"])   # the scorecard as written
            if r["level"]=="L4":
                r["classes"]["II"]["components"]=["II.a","II.c"]     # II.b insufficient at L4: a units recompute
        result=criteria.contrast(criteria.index_runs(runs),"L4","L0","II",0)
        self.assertEqual(result["status"],criteria.PASS)
        self.assertEqual(result["included"],list(criteria.TYPES))
        self.assertEqual(result["components"]["nation"],["II.a","II.c"])
        # By hand. L4, tasks 0-17: solved 13/18 v 9/18, so R = 4/13; records R(10, 5) = 1/2.
        # L0, tasks 6-23: solved 14/18 v 9/18, so R = 5/14; records R(5, 5) = 0.
        self.assertAlmostEqual(result["value"],100*((4/13+1/2)/2-(5/14+0)/2))

    def test_units_and_stored_indices_give_one_estimand(self):
        # The fallback recomputes the same quantity the scorecard stores: a run's own index per component.
        from test_resampling import errands, choices
        import resampling
        for cls,make in (("II",errands),("III",errands),("I",choices)):
            with self.subTest(cls=cls):
                left,right=make("L4"),make("L0")
                if cls!="I":
                    for row in right["units"]:
                        if row.get("side")=="control":
                            row["records"]=2
                    right["classes"][cls]=dict(resampling.score_units(right["units"],cls,right["components"]),
                                               validity="valid" if cls=="III" else None)
                ids=list(left["classes"][cls]["components"])
                left["classes"][cls]["components"]=ids[:-1]                 # differing sets: a recomputation
                from_units=criteria.match(left,right,cls)[0]
                for run in (left,right):
                    values=resampling.score_units(run["units"],cls,run["components"])["component_values"]
                    run["components"]={k:dict(v,d=values[k]) if k in values else v for k,v in run["components"].items()}
                    run["units"]=None
                from_indices=criteria.match(left,right,cls)[0]
                self.assertEqual(from_units[2],from_indices[2])
                self.assertAlmostEqual(from_units[0],from_indices[0])
                self.assertAlmostEqual(from_units[1],from_indices[1])

    def test_scorecard_indices_are_used_before_units(self):
        runs=planted()
        left=next(r for r in runs if r["level"]=="L4" and r["target_type"]=="nation")
        right=next(r for r in runs if r["level"]=="L0" and r["target_type"]=="nation")
        left["classes"]["II"]["components"]=["II.a","II.b"]
        left["components"]={"II.a":{"d":.5},"II.b":{"d":.25},"II.c":{"d":None}}
        right["components"]={"II.a":{"d":.1},"II.b":{"d":-.1},"II.c":{"d":.2}}
        left["units"]=right["units"]=[]                              # present, but the stored indices decide
        values,why=criteria.match(left,right,"II")
        self.assertIsNone(why)
        self.assertEqual(values[2],["II.a","II.b"])
        self.assertAlmostEqual(values[0],37.5)
        self.assertAlmostEqual(values[1],0.0)
        right["components"]={}
        right["units"]=None
        values,why=criteria.match(left,right,"II")
        self.assertIsNone(values)
        self.assertIn("neither the scorecard nor units.jsonl",why)
        result=criteria.contrast(criteria.index_runs(runs),"L4","L0","II",25)
        self.assertEqual(result["excluded"],["nation"])
        self.assertIn("neither the scorecard nor units.jsonl",result["excluded_reasons"]["nation"])

    def test_r4_failed_is_reported_without_adding_a_gate(self):
        runs=planted();set_class(runs,"L3","II",15);set_class(runs,"L6","II",29)
        result=analyze.evaluate_b1(runs)
        self.assertEqual(result["criteria"]["R4"]["status"],criteria.FAIL)
        self.assertEqual(result["verdict"],"pass")

    def test_common_component_minimum(self):
        runs=planted()
        left=next(r for r in runs if r["level"]=="L4")
        right=next(r for r in runs if r["level"]=="L0")
        left["classes"]["II"]["components"]=["II.a","II.c"]
        right["classes"]["II"]["components"]=["II.b","II.c"]
        self.assertIsNone(criteria.matched_values(left,right,"II"))


def b2_real(values):
    """B2's real-target runs (L0 and L4 per type) and PC runs, from {(level, class): (individual, organization, nation)}
    scores; every other class scores 0."""
    runs=[]
    for lv in ("L0","L4","PC"):
        for i,typ in enumerate(criteria.TYPES):
            run=copy.deepcopy(next(r for r in planted() if r["level"]=="L0" and r["target_type"]==typ))
            run.update(stage="B2",level=lv,run_id="B2-"+lv+"-"+typ,target_key="real-"+typ)
            for k in criteria.CLASSES:
                score=values.get((lv,k),(0,0,0))[i]
                run["classes"][k].update(score=score,raw=score)
            runs.append(run)
    return runs


class Boundaries(unittest.TestCase):
    """Every inclusive threshold, met exactly by one-decimal stored scores. Where the binary arithmetic misfires (45.3 -
    15.3 is 29.999999999999996), the test says so first, so it fails on a comparison of raw floats."""

    def b1(self, changes):
        runs=planted()
        for (lv,k),vals in changes.items():
            for typ,v in zip(criteria.TYPES,vals if isinstance(vals,tuple) else (vals,)*3):
                next(r for r in runs if r["level"]==lv and r["target_type"]==typ)["classes"][k].update(score=v,raw=v)
        return analyze.evaluate_b1(runs)["criteria"]

    def test_b1_thresholds(self):
        cases=[  # (relation, changes, the decision, the raw float quantity and its comparison, or None)
            ("C2-I >= 40",{("L3","I"):64.1,("L0","I"):24.1},lambda c:c["C2"]["parts"][0],(64.1-24.1,">=",40)),
            ("C2-II >= 25",{("L4","II"):32.3,("L0","II"):7.3},lambda c:c["C2"]["parts"][1],(32.3-7.3,">=",25)),
            ("R1 >= 20",{("L3","I"):32.3,("L0","I"):12.3},lambda c:c["R1"],(32.3-12.3,">=",20)),
            ("R2 >= 20",{("L4","II"):32.3,("L3","II"):12.3},lambda c:c["R2"],(32.3-12.3,">=",20)),
            ("R3 <= 15",{("L3","II"):16.1,("L0","II"):1.1},lambda c:c["R3"],(16.1-1.1,"<=",15)),
            ("R4 >= 15",{("L6","II"):16.4,("L3","II"):1.4},lambda c:c["R4"],(16.4-1.4,">=",15)),
            ("R5 >= 20",{("L4","III"):32.3,("L3","III"):12.3},lambda c:c["R5"],(32.3-12.3,">=",20)),
            ("R6 <= -20",{("L6","III"):12.3,("L4","III"):32.3},lambda c:c["R6"],(12.3-32.3,"<=",-20)),
            ("D-a >= 30",{("L3","I"):45.3,("L3","II"):15.3},lambda c:c["D-a"],(45.3-15.3,">=",30)),
            ("D-b >= 20",{("L6","II"):32.3,("L6","III"):12.3},lambda c:c["D-b"],(32.3-12.3,">=",20)),
            ("D-c, III >= 0.6 II",{("L4","II"):45.5,("L4","III"):27.3},lambda c:c["D-c"],None),
            ("D-c, II >= 20",{("L4","II"):20.0,("L4","III"):12.0},lambda c:c["D-c"],None),
            ("C1-I mean <= 15",{("L0","I"):(0.0,10.3,34.7)},lambda c:c["C1-I"],
             (statistics.mean((0.0,10.3,34.7)),"<=",15)),
            ("C1-II run <= 35",{("L0","II"):(35.0,5.0,5.0)},lambda c:c["C1-II"],None),
            ("R7 mean <= 10",{("L7","I"):(0.0,10.3,19.7),("L7","II"):(10.0,10.0,10.0)},lambda c:c["R7"],None),
            ("T spread <= 30",{("L0","I"):(32.2,2.2,10.0)},lambda c:next(p for p in c["T"]["parts"]
                                                                        if p["level"]=="L0" and p["cls"]=="I"),
             (32.2-2.2,"<=",30)),
        ]
        for name,changes,pick,naive in cases:
            with self.subTest(relation=name):
                if naive:
                    value,op,threshold=naive
                    self.assertFalse(value>=threshold if op==">=" else value<=threshold,"the float misfires here")
                self.assertEqual(pick(self.b1(changes))["status"],criteria.PASS)

    def test_signed_rival_at_exactly_minus_ten(self):
        runs=planted()
        for r in runs:
            if r["level"]=="L7":
                r["classes"]["II"]["raw"]=-10.0
        self.assertEqual(analyze.evaluate_b1(runs)["criteria"]["R8-II"]["status"],criteria.PASS)

    def test_b2_thresholds(self):
        self.assertFalse(statistics.mean((0.0,0.1,74.9))<=25)
        out=analyze.evaluate_b2(b2_real({("L0","I"):(0.0,0.1,74.9),("L4","II"):(16.4,)*3,("L0","II"):(1.4,)*3,
                                         ("PC","II"):(30.0,)*3}),B=10)
        self.assertEqual(out["E2"]["parts"][0]["status"],criteria.PASS)       # E2, Class I: L0 mean <= 25
        self.assertFalse(16.4-1.4>=15)
        self.assertEqual(out["E3"]["status"],criteria.PASS)                   # E3: L4 - L0 >= 15
        self.assertEqual(out["E4"]["status"],criteria.PASS)                   # E4: PC Class II >= 30

    def test_retest_limits(self):
        runs=planted()
        for original in list(runs):
            if original["level"] not in ("L0","L4","L6"):continue
            retest=copy.deepcopy(original)
            retest.update(stage="B2",run_id=original["run_id"]+"-retest",retest_of=original["run_id"],started=50000)
            last=original["level"]=="L6" and original["target_type"]=="nation"
            original["classes"]["I"].update(score=64.4 if last else 16.1,raw=64.4 if last else 16.1)
            retest["classes"]["I"].update(score=29.4 if last else 4.1,raw=29.4 if last else 4.1)
            runs.append(retest)
        self.assertFalse(abs(16.1-4.1)<=12)
        self.assertFalse(abs(64.4-29.4)<=35)
        e1=analyze.evaluate_b2(runs,B=10)["E1"]["I"]
        self.assertEqual(e1["n"],9)
        self.assertEqual(e1["status"],criteria.PASS)                           # median exactly 12, maximum 35


class Reporting(unittest.TestCase):
    def test_t_parts_name_their_types(self):
        runs=planted();set_class(runs,"L4","III",validity="weak",types=("nation",))
        part=next(p for p in analyze.evaluate_b1(runs)["criteria"]["T"]["parts"] if p["level"]=="L4" and p["cls"]=="III")
        self.assertEqual(part["status"],criteria.UNKNOWN)
        self.assertEqual((part["included"],part["excluded"]),(["individual","organization"],["nation"]))

    def test_summary_names_the_deciding_relations(self):
        runs=planted();set_class(runs,"L3","II",20)
        report=analyze.analyze_runs(runs,B=10)
        self.assertEqual(report["B1"]["verdict"],"partial")
        with tempfile.TemporaryDirectory() as tmp:
            analyze.write_outputs(report,runs,tmp)
            text=(Path(tmp)/"summary.md").read_text()
        self.assertIn("B1 verdict: **partial**, decided by R3.",text)


class Files(unittest.TestCase):
    def test_csv_svg_description_and_json_are_consistent(self):
        runs=planted();set_class(runs,"L3","III",validity="weak",types=("individual",))
        report=analyze.analyze_runs(runs,B=50)
        with tempfile.TemporaryDirectory() as tmp:
            analyze.write_outputs(report,runs,tmp)
            path=Path(tmp)
            ET.fromstring((path/"levels.svg").read_text())
            with (path/"levels.csv").open() as f:rows=list(csv.DictReader(f))
            self.assertEqual(len(rows),45)
            row=next(r for r in rows if r["level"]=="L3" and r["type"]=="individual" and r["class"]=="III")
            self.assertEqual(row["score"],"")
            self.assertIn("never zero",(path/"levels.description.md").read_text())
            self.assertEqual(json.loads((path/"summary.json").read_text())["B1"]["verdict"],report["B1"]["verdict"])

    def test_normalize_rejects_nonfinite_and_wrong_schema(self):
        base=planted()[0]
        card={"schema":"probatio-scorecard/4.0","target":{"type":"individual","key":"individual"},
              "conditions":{"without":{"classes":base["classes"]}},"memory":"off",
              "bank_sha256":"bank","item_fingerprint":"items","plan_fingerprint":"plan",
              "agent":"claude","cli_version":"test","effort":"medium","tier":"standard","models":["test-model"]}
        spec={"stage":"B1","level":"L0","memory":"off","target_type":"individual","run_id":"test"}
        self.assertEqual(analyze.normalize(card,spec)["level"],"L0")
        self.assertEqual(analyze.normalize(dict(card,schema="probatio-scorecard/4.1"),spec)["level"],"L0")
        card["conditions"]["without"]["classes"]["I"]["score"]=float("nan")
        with self.assertRaises(ValueError):analyze.normalize(card,spec)
        card["schema"]="old"
        with self.assertRaises(ValueError):analyze.normalize(card,spec)


if __name__=="__main__":unittest.main()
