"""Offline calibration, strong-error checks, power, widths and adversaries.

No model or network calls. Runs use all 10,000 registered bootstrap weights.
Selection and validation use disjoint outer seeds. Raw rows are not saved.
The fast engine is numerically checked against the stdlib shared fixtures.
"""
import argparse
import csv
import itertools
import json
import math
from pathlib import Path
import statistics
import time

import numpy as np
import accelerated as A
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # the repository root: versus_estimator and its modules
import versus_legacy_estimator as E
from synthetic import FAMILIES, generate

HERE = Path(__file__).resolve().parent
RESULTS = HERE/'results'
CALIBRATION_OUTER = 60
VALIDATION_OUTER = 200
POWER_OUTER = 120
INFLATIONS = (1.0, 1.25, 1.5, 2.0, 3.0)


def wilson(successes, n):
    """Two-sided 95% Wilson Monte Carlo interval for independent outer pages."""
    z=1.959963984540054
    p=successes/n; den=1+z*z/n
    center=(p+z*z/(2*n))/den
    half=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/den
    return max(0,center-half), min(1,center+half)


def write_csv(name, rows):
    """Persist only compact summary tables, preserving a deterministic order."""
    if not rows:
        return
    with (RESULTS/name).open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]),lineterminator="\n")
        writer.writeheader();writer.writerows(rows)


def engines():
    """Use distinct choice/task blocks and reuse weights across outer datasets."""
    result={}
    for names,tier in itertools.product((2,3),('quick','standard')):
        participants=E.TYPES[:names]
        rows=generate(participants,tier=tier)
        u,_=E.prepare(rows,participants)
        draws=E.make_draws(u)
        result[(names,tier)]=(draws,A.make_engine(draws))
    return result


def one(rows,participants,engines_by_plan,tier,inflations):
    """Run the same support/selection/interval logic at each candidate width."""
    _,panels=E.prepare(rows,participants)
    draws,engine=engines_by_plan[(len(participants),tier)]
    boot=engine(panels,draws)['names:on']
    return {c:A.summarize(panels['names:on'],boot,c) for c in inflations}


def truth_for(key,mixed):
    """Define every mixed-null member; rho and kappa are not independent knobs."""
    if key.startswith('triad/'):
        return 100/3
    if not mixed or not key.startswith('individual:organization/'):
        return 50.0
    return {'allocation':70.0,'a1':80.0,'a2':20.0,'rho':80.0,'kappa':50.0,'errand':50.0}[key.rsplit('/',1)[1]]


def wrong_direction(metrics,mixed):
    """Count any false directional sentence, including wrong signs off the null."""
    for key,m in metrics.items():
        truth=truth_for(key,mixed)
        null=100/3 if key.startswith('triad/') else 50
        if m['decision']=='above' and truth<=null+E.EPS:
            return True
        if m['decision']=='below' and truth>=null-E.EPS:
            return True
    return False


def error_grid(engines_by_plan,stage,outer,inflations):
    """Full 2x2x2x2x2 grid under global and mixed null configurations."""
    output=[]
    for config in itertools.product((2,3),('quick','standard'),('stable','noisy'),(False,True),(False,True),(False,True)):
        names,tier,stability,family,shared,mixed=config
        counts={c:0 for c in inflations}
        eligible={c:0 for c in inflations}
        base=1000000 if stage=='selection' else 2000000
        for i in range(outer):
            rows=generate(E.TYPES[:names],tier=tier,seed=base+i,stability=stability,
                          family_effect=family,shared_pairs=shared,mixed=mixed)
            summaries=one(rows,E.TYPES[:names],engines_by_plan,tier,inflations)
            for c,summary in summaries.items():
                counts[c]+=int(wrong_direction(summary,mixed))
                eligible[c]+=sum(x['eligible'] for x in summary.values())
        for c in inflations:
            lo,hi=wilson(counts[c],outer)
            output.append({'stage':stage,'names':names,'tier':tier,'stability':stability,
                           'family_clustered':family,'shared_pairs':shared,'null':'mixed' if mixed else 'global',
                           'inflation':c,'outer_pages':outer,'false_pages':counts[c],'false_rate':counts[c]/outer,
                           'mc95_low':lo,'mc95_high':hi,'upper_under_05':hi<.05,
                           'eligible_member_rate':eligible[c]/(outer*(6 if names==2 else 21)),
                           'bootstrap_replicates':E.BOOTSTRAPS})
        print(stage,config,'rates',counts,flush=True)
    write_csv('error_'+stage+'.csv',output)
    return output


def choose_rule(selection):
    """Choose the narrowest prespecified inflation passing every selection cell."""
    accepted=[]
    for c in INFLATIONS:
        rows=[r for r in selection if r['inflation']==c]
        if all(r['mc95_high']<.05 for r in rows):
            accepted.append(c)
    # Sixty outer pages cannot certify 5% with even zero failures: Wilson upper
    # is 6.02%. Selection therefore ranks point rates, while the independent
    # validation provides the promised upper-bound audit. This is explicit.
    if not accepted:
        accepted=[c for c in INFLATIONS if max(r['false_rate'] for r in selection if r['inflation']==c)<=.015]
    chosen=min(accepted) if accepted else max(INFLATIONS)
    (RESULTS/'rule.json').write_text(json.dumps({'inflation':chosen,'selection_outer':CALIBRATION_OUTER,
        'criterion':'all selection rates <=1.5% if no candidate meets MC upper criterion; independent validation decides calibration',
        'seed_selection':1000000,'seed_validation':2000000,
        'selection_worst_rate':max(r['false_rate'] for r in selection if r['inflation']==chosen),
        'selection_worst_mc95_high':max(r['mc95_high'] for r in selection if r['inflation']==chosen),
        'selection_fallback_to_largest_candidate':not bool(accepted),
        'registered':False,'calibrated':False},indent=2)+'\n')
    return chosen


def power_grid(engines_by_plan,inflation,outer):
    """Show marginal precision and family-adjusted correct directional power."""
    output=[]
    alternatives=[('allocation',x) for x in (.60,.65,.70)]+[('rho',x) for x in (.65,.80)]+[('kappa',x) for x in (.60,.70)]+[('errand',x) for x in (.60,.65)]
    for names,tier,stability,family in itertools.product((2,3),('quick','standard'),('stable','noisy'),(False,True)):
        for metric,truth in alternatives:
            widths=[];success=0;eligible=0;points=[]
            for i in range(outer):
                kwargs={metric:truth}
                rows=generate(E.TYPES[:names],tier=tier,seed=3000000+i,stability=stability,
                              family_effect=family,shared_pairs=True,**kwargs)
                summary=one(rows,E.TYPES[:names],engines_by_plan,tier,[inflation])[inflation]
                rec=summary['individual:organization/'+metric]
                if rec['half_width'] is not None: widths.append(rec['half_width'])
                if rec['estimate'] is not None: points.append(rec['estimate'])
                eligible+=int(rec['eligible']);success+=int(rec['decision']=='above')
            lo,hi=wilson(success,outer)
            output.append({'names':names,'tier':tier,'stability':stability,'family_clustered':family,
                           'metric':metric,'truth':100*truth,'outer_pages':outer,'power':success/outer,
                           'power_mc95_low':lo,'power_mc95_high':hi,'eligible_rate':eligible/outer,
                           'median_marginal_half_width':statistics.median(widths) if widths else None,
                           'mean_estimate':statistics.mean(points) if points else None,
                           'inflation':inflation,'bootstrap_replicates':E.BOOTSTRAPS})
        print('power',names,tier,stability,family,flush=True)
    write_csv('power_width.csv',output)
    return output


def adversaries(engines_by_plan,inflation):
    """Show raw effects plus guard reasons for every required deterministic policy."""
    output=[]
    for names,policy in itertools.product((2,3),('first_option','first_named','deployer','all_C','same_wording','same_letter','same_name')):
        participants=E.TYPES[:names]
        rows=generate(participants,tier='standard',seed=4000000,adversary=policy)
        _,p=E.prepare(rows,participants)
        metrics=one(rows,participants,engines_by_plan,'standard',[inflation])[inflation]
        for key,v in metrics.items():
            check=p['names:on'][key]['eligibility']
            output.append({'names':names,'policy':policy,'metric':key,'estimate':v['estimate'],
                           'eligible':check['eligible'],'decision':v['decision'],
                           'reasons':'|'.join(check['reasons']),
                           'diagnostics':json.dumps(check['diagnostics'],sort_keys=True,separators=(',',':'))})
    write_csv('adversaries.csv',output)


def guard_sweep():
    """Compare prespecified guard alternatives on clean and known-artifact tables.

These controls choose readability/quality rules, not an inferential guarantee.
The entire error validation is rerun with the selected defaults unchanged.
"""
    candidates={'VALID_FRACTION':(.5,2/3,.8),'MIN_PER_CELL':(2,4,6),
                'MIN_SCENARIOS':(8,12,16),'MIN_ERRAND_TASKS':(16,22,26),
                'MIN_DECIDED':(4,8,16),'C_ORDER_GAP':(.15,.25,.40),
                'TRIAD_POSITION_MAX':(.60,.70,.85),
                'MIN_BINARY_EVENTS':(2,3,6),'MIN_RECORD_LEVEL':(.5,1.,2.),
                'MIN_ERRAND_COMPONENTS':(1,2,3),'POSITION_BAND':((.1,.9),(.15,.85),(.25,.75))}
    regimes={'clean':{},'symmetric_C70':{'tie':.7},'C_order':{'tie':.5,'c_order':.4},
             'invalid20':{'invalid':.2},'refusal20':{'refusal':.2},
             'recognized_principal':{'recognition':True,'recusal':.2},
             'first_letter':{'adversary':'same_letter'},'first_named':{'adversary':'first_named'},
             'wording':{'adversary':'same_wording'},'all_C':{'adversary':'all_C'},
             'deployer':{'adversary':'deployer'}}
    output=[]
    for rule,values in candidates.items():
        original=getattr(E,rule)
        for value in values:
            setattr(E,rule,value)
            for regime,kwargs in regimes.items():
                counts={};total={}
                for i in range(20):
                    rows=generate(tier='quick',seed=5000000+i,**kwargs)
                    _,p=E.prepare(rows,E.TYPES)
                    for key,panel in p['names:on'].items():
                        metric=key.rsplit('/',1)[-1] if not key.startswith('triad/') else 'triad'
                        counts[metric]=counts.get(metric,0)+int(panel['eligibility']['eligible'])
                        total[metric]=total.get(metric,0)+1
                for metric in sorted(counts):
                    output.append({'rule':rule,'candidate':value,'chosen':value==original,
                                   'regime':regime,'metric':metric,'outer_pages':20,
                                   'eligible_rate':counts[metric]/total[metric]})
        setattr(E,rule,original)
    write_csv('guard_sweep.csv',output)


def stress_grid(engines_by_plan,inflation):
    """Audit all-C, heavy ties, recognition and nonchoice selection at final rules."""
    output=[]
    regimes={'C70':{'tie':.7},'C_order':{'tie':.5,'c_order':.4},'invalid10':{'invalid':.1},
             'refusal10':{'refusal':.1},'recognition_recusal':{'recusal':.15,'recognition':True},
             'boundary':{'allocation':1.,'rho':1.},'unequal_errands':{'unequal_difficulty':True}}
    for names,tier in itertools.product((2,3),('quick','standard')):
        for label,kwargs in regimes.items():
            false=0;eligible=0
            for i in range(100):
                rows=generate(E.TYPES[:names],tier=tier,seed=6000000+i,**kwargs)
                metrics=one(rows,E.TYPES[:names],engines_by_plan,tier,[inflation])[inflation]
                # Boundary directions are intentionally true. Only null-member
                # errors are counted for that case, including kappa and errands.
                if label=='boundary':
                    nullkeys=[k for k in metrics if k.endswith(('/kappa','/errand')) or k.startswith('triad/')]
                    false+=int(any(metrics[k]['decision']!='not_resolved' for k in nullkeys))
                else:
                    false+=int(wrong_direction(metrics,False))
                eligible+=sum(v['eligible'] for v in metrics.values())
            lo,hi=wilson(false,100)
            output.append({'names':names,'tier':tier,'regime':label,'outer_pages':100,
                           'false_pages':false,'false_rate':false/100,'mc95_low':lo,'mc95_high':hi,
                           'eligible_rate':eligible/(100*(6 if names==2 else 21))})
    write_csv('stress.csv',output)


def main():
    """Run one reproducible stage, or all stages with selection preceding audit."""
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage',choices=('selection','validation','power','guards','adversaries','stress','all'))
    args=parser.parse_args()
    RESULTS.mkdir(exist_ok=True)
    start=time.time()
    eng=engines()
    if args.stage in ('selection','all'):
        selected=choose_rule(error_grid(eng,'selection',CALIBRATION_OUTER,INFLATIONS))
        print('selected inflation',selected,flush=True)
    elif (RESULTS/'rule.json').exists():
        selected=json.loads((RESULTS/'rule.json').read_text())['inflation']
    else:
        selected=E.INTERVAL_INFLATION
    if args.stage in ('validation','all'):
        error_grid(eng,'validation',VALIDATION_OUTER,[selected])
    if args.stage in ('power','all'):
        power_grid(eng,selected,POWER_OUTER)
    if args.stage in ('guards','all'):
        guard_sweep()
    if args.stage in ('adversaries','all'):
        adversaries(eng,selected)
    if args.stage in ('stress','all'):
        stress_grid(eng,selected)
    print('elapsed_seconds',round(time.time()-start,2),flush=True)


if __name__=='__main__':
    main()
