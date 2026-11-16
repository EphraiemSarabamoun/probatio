"""Historical draft-2 conditional-family calibration, audit and power, no model calls.

The conflict claim family is rho/kappa per pair. Resampling is within fixed
families. Balanced family effects supply genuine conditional global/mixed
nulls. An additional unbalanced-family grid evaluates direction against each
realized family's conditional truth instead of the obsolete unconditional null.
"""
import csv
import itertools
import json
import math
from pathlib import Path
import statistics

import numpy as np
import accelerated as A
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # the repository root: versus_estimator and its modules
import versus_estimator as E
import policy_options as P
import simulate as S
from synthetic import FAMILIES, generate

HERE=Path(__file__).resolve().parent
RESULTS=HERE/'results'
INFLATIONS=tuple(round(1+i/10,1) for i in range(21))
SELECTION_OUTER=100
VALIDATION_OUTER=300
POWER_OUTER=120


def engines():
    """Build one fixed stratified 10,000-draw stream for each page shape."""
    out={}
    for names,tier in itertools.product((2,3),('quick','standard')):
        rows=generate(E.TYPES[:names],tier=tier)
        u,_=E.prepare(rows,E.TYPES[:names])
        d=E.make_draws(u)
        out[(names,tier)]=(d,A.make_engine(d))
    return out


def conditional_truth(effects,participants,mixed=False,rho=.5,kappa=.5):
    """Define rho/kappa expectations conditional on each fixed family's effect."""
    truth={}
    for index,pair in enumerate(itertools.combinations(participants,2)):
        rr=.8 if mixed and index==0 else rho
        role=[]
        for family in FAMILIES:
            delta=effects[('conflict',family)]
            a1=min(1,max(0,kappa+rr-.5+delta))
            a2=min(1,max(0,kappa-rr+.5+delta))
            role.append(((a1+1-a2)/2,(a1+a2)/2))
        for j,metric in enumerate(('rho','kappa')):
            truth[':'.join(pair)+'/'+metric]=100*statistics.mean(x[j] for x in role)
    return truth


def one(eng,tier,names,stability,family,shared,seed,mixed=False,balanced=True,
        all_metrics=False,**kwargs):
    """Return one page's shared bootstrap and its exact conditional primary truth."""
    participants=E.TYPES[:names]
    rows,effects=generate(participants,tier=tier,seed=seed,stability=stability,
                          family_effect=family,shared_pairs=shared,mixed=mixed,
                          balanced_families=balanced,return_effects=True,**kwargs)
    _,panels=E.prepare(rows,participants)
    records=panels['names:on']
    primary={k:p for k,p in records.items() if p['metric'] in ('rho','kappa')}
    draws,engine=eng[(names,tier)]
    selected=records if all_metrics else primary
    boot=engine({'names:on':selected},draws)['names:on']
    summary=P.summaries(primary,{k:boot[k] for k in primary},'full_page')
    truth=conditional_truth(effects,participants,mixed,kwargs.get('rho',.5),kwargs.get('kappa',.5))
    return records,boot,summary,truth


def false_page(summary,truth,inflation):
    """Count a false conditional direction, including wrong signs off the null."""
    decisions=P.decisions(summary,inflation)
    for key,value in decisions.items():
        if value['decision']=='above' and truth[key]<=50+E.EPS:
            return True
        if value['decision']=='below' and truth[key]>=50-E.EPS:
            return True
    return False


def configurations():
    """Cross both page shapes and dependency regimes with strong-error nulls."""
    for names,tier,stability,family,shared in itertools.product((2,3),('quick','standard'),('stable','noisy'),(False,True),(False,True)):
        for case in ('conditional_global','conditional_mixed'):
            yield names,tier,stability,family,shared,case
        if family:
            yield names,tier,stability,family,shared,'conditional_unbalanced'


def error_grid(eng,stage,outer,cs):
    """Use independent selection/audit seeds with the full support/guard logic."""
    output=[]
    for names,tier,stability,family,shared,case in configurations():
        failures={c:0 for c in cs};eligible=0
        mintruth=100.;maxtruth=0.
        for i in range(outer):
            seed=(13000000 if stage=='selection' else 14000000)+i
            _,_,summary,truth=one(eng,tier,names,stability,family,shared,seed,
                                  mixed=case=='conditional_mixed',balanced=case!='conditional_unbalanced')
            eligible+=sum(m['eligible'] for m in summary.values())
            mintruth=min(mintruth,*truth.values());maxtruth=max(maxtruth,*truth.values())
            for c in cs: failures[c]+=int(false_page(summary,truth,c))
        for c in cs:
            lo,hi=S.wilson(failures[c],outer)
            output.append({'stage':stage,'names':names,'tier':tier,'stability':stability,
                           'family_effects':family,'shared_pairs':shared,'case':case,
                           'inflation':c,'outer_pages':outer,'false_pages':failures[c],
                           'false_rate':failures[c]/outer,'mc95_low':lo,'mc95_high':hi,
                           'upper_under_05':hi<.05,'eligible_rate':eligible/(outer*(2 if names==2 else 6)),
                           'conditional_truth_min':mintruth,'conditional_truth_max':maxtruth,
                           'bootstrap_replicates':E.BOOTSTRAPS})
        print(stage,names,tier,stability,family,shared,case,'errors',failures,flush=True)
    # Keep the large 21-candidate selection cross in a compact worst-cell table.
    if stage=='selection':
        compact=[]
        for c in cs:
            rs=[r for r in output if r['inflation']==c]
            for family in (False,True):
                chosen=[r for r in rs if r['family_effects']==family]
                worst=max(chosen,key=lambda r:r['false_rate'])
                compact.append({'inflation':c,'family_effects':family,'cells':len(chosen),
                                'outer_per_cell':outer,'worst_rate':worst['false_rate'],
                                'worst_mc95_high':max(r['mc95_high'] for r in chosen),
                                'worst_cell':json.dumps({k:worst[k] for k in ('names','tier','stability','shared_pairs','case')},separators=(',',':'))})
        S.write_csv('stratified_selection.csv',compact)
    else:
        S.write_csv('stratified_validation.csv',output)
    return output


def choose(selection):
    """Choose the narrowest candidate with <=1% error in every selection cell."""
    accepted=[c for c in INFLATIONS if max(r['false_rate'] for r in selection if r['inflation']==c)<=.01]
    chosen=min(accepted) if accepted else max(INFLATIONS)
    record={'version':'versus-reference-draft-3','claim_policy':'rho_kappa_only','resampling':'within_fixed_family',
            'inflation':chosen,'grid_step':.1,'grid_min':1.,'grid_max':3.,
            'selection_outer_per_cell':SELECTION_OUTER,'selection_criterion':'every cell rate <=1%',
            'largest_candidate_fallback':not bool(accepted),'selection_seed':13000000,'validation_seed':14000000,
            'bootstrap_seed':E.SEED,'bootstrap_replicates':E.BOOTSTRAPS,
            'registered':False,'live_calibrated':False}
    (RESULTS/'stratified_rule.json').write_text(json.dumps(record,indent=2)+'\n')
    return chosen


def power(eng,inflation):
    """Report all original requested truths with only conflict conclusions tested."""
    output=[]
    alternatives=[('allocation',x) for x in (.60,.65,.70)]+[('rho',x) for x in (.65,.8)]+[('kappa',x) for x in (.60,.7)]+[('errand',x) for x in (.60,.65)]
    for names,tier,stability,family in itertools.product((2,3),('quick','standard'),('stable','noisy'),(False,True)):
        for metric,target in alternatives:
            success=0;eligible=0;widths=[];points=[];truths=[]
            for i in range(POWER_OUTER):
                rs,boot,summary,truth=one(eng,tier,names,stability,family,True,15000000+i,
                                         all_metrics=True,**{metric:target})
                key='individual:organization/'+metric
                point=rs[key]['estimate']
                if point is not None: points.append(point)
                marg=E.base.interval_record(point,[None if not np.isfinite(v) else float(v) for v in boot[key]],E.MARGINAL_INFLATION)
                if marg['marginal'] is not None: widths.append((marg['marginal'][1]-marg['marginal'][0])/2)
                if key in summary:
                    decision=P.decisions(summary,inflation)[key]['decision']
                    success+=int(decision=='above');eligible+=int(summary[key]['eligible']);truths.append(truth[key])
            inferential=metric in ('rho','kappa')
            lo,hi=S.wilson(success,POWER_OUTER)
            output.append({'names':names,'tier':tier,'stability':stability,'family_effects':family,
                           'metric':metric,'nominal_truth':target*100,
                           'conditional_truth':statistics.mean(truths) if truths else target*100,
                           'inferential':inferential,'outer_pages':POWER_OUTER,
                           'power':success/POWER_OUTER if inferential else None,
                           'power_mc95_low':lo if inferential else None,'power_mc95_high':hi if inferential else None,
                           'eligible_rate':eligible/POWER_OUTER if inferential else None,
                           'median_marginal_half_width':statistics.median(widths) if widths else None,
                           'mean_estimate':statistics.mean(points) if points else None,
                           'primary_inflation':inflation,'marginal_inflation':E.MARGINAL_INFLATION,
                           'bootstrap_replicates':E.BOOTSTRAPS})
        print('stratified power',names,tier,stability,family,flush=True)
    S.write_csv('stratified_power_width.csv',output)


def main():
    """Select once, audit untouched seeds, then quantify power and descriptive width."""
    eng=engines()
    selection=error_grid(eng,'selection',SELECTION_OUTER,INFLATIONS)
    chosen=choose(selection)
    print('chosen inflation',chosen,flush=True)
    validation=error_grid(eng,'validation',VALIDATION_OUTER,[chosen])
    record=json.loads((RESULTS/'stratified_rule.json').read_text())
    record.update(validation_cells=len(validation),
                  synthetic_grid_passed=all(r['mc95_high']<.05 for r in validation),
                  worst_validation_mc95_high=max(r['mc95_high'] for r in validation))
    (RESULTS/'stratified_rule.json').write_text(json.dumps(record,indent=2)+'\n')
    power(eng,chosen)


if __name__=='__main__':
    main()
