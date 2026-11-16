"""Final standard-only selection and fresh audit after excluding quick conclusions.

Reuse the declared selection stream, restricted prospectively to the standard
policy. A new validation stream is untouched by the earlier two-tier audit.
The earlier quick evidence remains available and is not rerated into a pass.
"""
import argparse
import ast
import json
from pathlib import Path
import statistics

import numpy as np
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # the repository root: versus_estimator and its modules
import versus_estimator as E
import policy_options as P
import simulate as S
import stratified_study as T

RESULTS=Path(__file__).resolve().parent/'results'
VALIDATION_OUTER=400
POWER_OUTER=160
VALIDATION_SEED=16000000
POWER_SEED=17000000


def selection_from_log(path):
    """Project an existing fixed selection stream, preserving its rejected cells."""
    out=[]
    for line in path.read_text().splitlines():
        if not line.startswith('selection ') or ' errors ' not in line:
            continue
        header,values=line.split(' errors ',1)
        _,names,tier,stability,family,shared,case=header.split()
        if tier!='standard': continue
        for c,n in ast.literal_eval(values).items():
            out.append({'names':int(names),'tier':tier,'stability':stability,
                        'family_effects':family=='True','shared_pairs':shared=='True',
                        'case':case,'inflation':float(c),'false_pages':n,
                        'outer_pages':T.SELECTION_OUTER,'false_rate':n/T.SELECTION_OUTER})
    if len(out)!=40*len(T.INFLATIONS):
        raise ValueError('standard selection stream incomplete')
    return out


def select(rows):
    """Choose the narrowest standard-only candidate at <=1% in every cell."""
    allowed=[c for c in T.INFLATIONS if max(r['false_rate'] for r in rows if r['inflation']==c)<=.01]
    chosen=min(allowed) if allowed else max(T.INFLATIONS)
    compact=[]
    for c in T.INFLATIONS:
        for family in (False,True):
            rs=[r for r in rows if r['inflation']==c and r['family_effects']==family]
            worst=max(rs,key=lambda r:r['false_rate'])
            compact.append({'inflation':c,'family_effects':family,'cells':len(rs),
                            'outer_per_cell':T.SELECTION_OUTER,'worst_rate':worst['false_rate'],
                            'worst_cell':json.dumps({k:worst[k] for k in ('names','stability','shared_pairs','case')},separators=(',',':'))})
    S.write_csv('standard_selection.csv',compact)
    rule={'version':'versus-reference-draft-3','claim_policy':'standard_rho_kappa_only',
          'quick':'descriptive_only','inflation':chosen,'marginal_inflation':chosen,
          'selection_outer_per_cell':T.SELECTION_OUTER,'selection_seed':13000000,
          'selection_criterion':'every standard cell rate <=1%',
          'grid_min':1.,'grid_max':3.,'grid_step':.1,'largest_candidate_fallback':not bool(allowed),
          'validation_seed':VALIDATION_SEED,'power_seed':POWER_SEED,
          'bootstrap_seed':E.SEED,'bootstrap_replicates':E.BOOTSTRAPS,
          'registered':False,'live_calibrated':False}
    (RESULTS/'standard_rule.json').write_text(json.dumps(rule,indent=2)+'\n')
    return chosen


def validate(eng,c):
    """Audit standard-only conditional global, mixed and unbalanced cases afresh."""
    output=[]
    for names,tier,stability,family,shared,case in T.configurations():
        if tier!='standard': continue
        errors=0;eligible=0
        for i in range(VALIDATION_OUTER):
            _,_,summary,truth=T.one(eng,tier,names,stability,family,shared,VALIDATION_SEED+i,
                                    mixed=case=='conditional_mixed',balanced=case!='conditional_unbalanced')
            errors+=int(T.false_page(summary,truth,c));eligible+=sum(m['eligible'] for m in summary.values())
        lo,hi=S.wilson(errors,VALIDATION_OUTER)
        output.append({'names':names,'tier':tier,'stability':stability,'family_effects':family,
                       'shared_pairs':shared,'case':case,'inflation':c,'outer_pages':VALIDATION_OUTER,
                       'false_pages':errors,'false_rate':errors/VALIDATION_OUTER,
                       'mc95_low':lo,'mc95_high':hi,'upper_under_05':hi<.05,
                       'eligible_rate':eligible/(VALIDATION_OUTER*(2 if names==2 else 6)),
                       'bootstrap_replicates':E.BOOTSTRAPS})
        print('standard audit',names,stability,family,shared,case,errors,hi,flush=True)
    S.write_csv('standard_validation.csv',output)
    rule=json.loads((RESULTS/'standard_rule.json').read_text())
    rule.update(synthetic_grid_passed=all(r['mc95_high']<.05 for r in output),validation_cells=len(output),
                validation_outer_per_cell=VALIDATION_OUTER,worst_validation_mc95_high=max(r['mc95_high'] for r in output))
    (RESULTS/'standard_rule.json').write_text(json.dumps(rule,indent=2)+'\n')
    return output


def power(eng,c):
    """Measure standard conditional-alternative power and marginal half-widths."""
    output=[]
    for names in (2,3):
        for stability in ('stable','noisy'):
            for family in (False,True):
                for metric,target in (('rho',.65),('rho',.8),('kappa',.6),('kappa',.7)):
                    success=0;eligible=0;widths=[];points=[]
                    for i in range(POWER_OUTER):
                        records,boot,summary,truth=T.one(eng,'standard',names,stability,family,True,POWER_SEED+i,**{metric:target})
                        key='individual:organization/'+metric
                        assert abs(truth[key]-100*target)<1e-9
                        success+=int(P.decisions(summary,c)[key]['decision']=='above')
                        eligible+=int(summary[key]['eligible']);points.append(records[key]['estimate'])
                        vals=boot[key];point=records[key]['estimate']
                        lo=max(0,point-c*(point-float(np.quantile(vals,E.ALPHA/2))))
                        hi=min(100,point+c*(float(np.quantile(vals,1-E.ALPHA/2))-point))
                        widths.append((hi-lo)/2)
                    lo,hi=S.wilson(success,POWER_OUTER)
                    output.append({'names':names,'tier':'standard','stability':stability,'family_effects':family,
                                   'metric':metric,'conditional_truth':target*100,'inflation':c,
                                   'outer_pages':POWER_OUTER,'power':success/POWER_OUTER,
                                   'power_mc95_low':lo,'power_mc95_high':hi,
                                   'eligible_rate':eligible/POWER_OUTER,
                                   'median_marginal_half_width':statistics.median(widths),
                                   'mean_estimate':statistics.mean(points),'bootstrap_replicates':E.BOOTSTRAPS})
                print('standard power',names,stability,family,flush=True)
    S.write_csv('standard_power_width.csv',output)


def main():
    """Select from fixed standard development data and use two new outer streams."""
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--selection-log',type=Path)
    args=parser.parse_args()
    eng=T.engines()
    if args.selection_log:
        selection=selection_from_log(args.selection_log)
    else:
        # Full public rerun needs no private artifact. Only the standard cells
        # are selected; quick remains excluded from all live claims.
        all_selection=T.error_grid(eng,'selection',T.SELECTION_OUTER,T.INFLATIONS)
        selection=[r for r in all_selection if r['tier']=='standard']
    c=select(selection)
    print('standard selected',c,flush=True)
    validate(eng,c)
    power(eng,c)


if __name__=='__main__':
    main()
