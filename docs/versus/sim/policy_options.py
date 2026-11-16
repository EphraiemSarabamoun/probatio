"""Exploratory alternative sentence policies; never changes the registered draft.

Three-name pages only, because per-pair and page-level six-test control coincide
for two names. Select inflation on new selection seeds, audit on new validation
seeds, then measure power on a third stream. All use 10,000 joint replicates.
"""
import csv
import itertools
import json
import math
from pathlib import Path

import numpy as np
import accelerated as A
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # the repository root: versus_estimator and its modules
import versus_legacy_estimator as E
import simulate as S
from synthetic import generate

HERE=Path(__file__).resolve().parent
POLICIES=('full_page','rho_kappa','rho_only','kappa_only','per_pair')
SELECTION_OUTER=60
VALIDATION_OUTER=200
POWER_OUTER=120


def views(panels,policy):
    """Declare exactly which tests remain inferential under an alternative."""
    keys=list(panels)
    if policy=='full_page': return [keys]
    if policy=='rho_kappa': return [[k for k in keys if k.endswith(('/rho','/kappa'))]]
    if policy=='rho_only': return [[k for k in keys if k.endswith('/rho')]]
    if policy=='kappa_only': return [[k for k in keys if k.endswith('/kappa')]]
    return [[k for k in keys if k.startswith(pair+'/')] for pair in
            ('individual:organization','individual:nation','organization:nation')]


class HistoricalSummary(dict):
    """Keep historical input so candidate decisions use the reference itself."""
    pass


def summaries(panels,boot,policy):
    """Historical metadata computed by the frozen historical reference."""
    result=HistoricalSummary()
    result.groups=[]
    for keys in views(panels,policy):
        ps={k:panels[k] for k in keys}
        bs={k:[None if not np.isfinite(x) else float(x) for x in boot[k]] for k in keys}
        page=E.simultaneous(ps,bs,len(keys),1.)
        result.groups.append((ps,bs))
        for k,r in page['metrics'].items():
            point=r['estimate'];ci=r['simultaneous']
            result[k]={'estimate':point,'null':panels[k]['null'],
                       'eligible':not r['withheld_reasons'],
                       'radius':None if ci is None else max(point-ci[0],ci[1]-point),
                       'saturated':r['saturated'],
                       'scope':k.split('/')[0] if policy=='per_pair' else 'page'}
    return result


def decisions(summary,inflation):
    """Historical decisions have one implementation in legacy_estimator."""
    out={}
    for ps,bs in summary.groups:
        for k,r in E.simultaneous(ps,bs,len(ps),inflation)['metrics'].items():
            out[k]={'decision':r['decision'],'estimate':r['estimate']}
    return out


def wrong_scopes(summary,inflation,mixed):
    """Expose both any-page error and individual-pair error for per-pair control."""
    ds=decisions(summary,inflation)
    bad={m['scope']:False for m in summary.values()}
    for k,m in summary.items():
        truth=S.truth_for(k,mixed)
        dec=ds[k]['decision']
        if (dec=='above' and truth<=m['null']+E.EPS) or (dec=='below' and truth>=m['null']-E.EPS):
            bad[m['scope']]=True
    return bad


def page(engines_by_plan,tier,stability,family,seed,**kwargs):
    """Generate one full page and share its bootstrap across every policy option."""
    rows=generate(tier=tier,seed=seed,stability=stability,family_effect=family,shared_pairs=True,**kwargs)
    _,p=E.prepare(rows,E.TYPES)
    d,e=engines_by_plan[(3,tier)]
    b=e(p,d)['names:on']
    return {policy:summaries(p['names:on'],b,policy) for policy in POLICIES}


def error_stage(engines_by_plan,stage,outer,rules=None):
    """Compare global/mixed nulls, retaining the distinction between control units."""
    result=[]
    for family,tier,stability,mixed in itertools.product((False,True),('quick','standard'),('stable','noisy'),(False,True)):
        counts={}
        for policy in POLICIES:
            cs=S.INFLATIONS if rules is None else [rules[(policy,family)]]
            for c in cs: counts[(policy,c)]={'page':0,'scopes':{}}
        for i in range(outer):
            seed=(9000000 if stage=='selection' else 10000000)+i
            summaries_by_policy=page(engines_by_plan,tier,stability,family,seed,mixed=mixed)
            for (policy,c),count in counts.items():
                bad=wrong_scopes(summaries_by_policy[policy],c,mixed)
                count['page']+=int(any(bad.values()))
                for scope,wrong in bad.items(): count['scopes'][scope]=count['scopes'].get(scope,0)+int(wrong)
        for (policy,c),count in counts.items():
            worst=max(count['scopes'].values())
            lo,hi=S.wilson(worst,outer)
            plo,phi=S.wilson(count['page'],outer)
            result.append({'stage':stage,'policy':policy,'family_clustered':family,'tier':tier,'stability':stability,
                           'null':'mixed' if mixed else 'global','inflation':c,'outer_pages':outer,
                           'controlled_unit':'pair' if policy=='per_pair' else 'page',
                           'worst_scope_errors':worst,'worst_scope_rate':worst/outer,'scope_mc95_high':hi,
                           'any_page_errors':count['page'],'any_page_rate':count['page']/outer,'page_mc95_high':phi,
                           'bootstrap_replicates':E.BOOTSTRAPS})
        print('policy',stage,family,tier,stability,mixed,flush=True)
    S.write_csv('policy_'+stage+'.csv',result)
    return result


def choose(selection):
    """Pick a common rule per policy and family-dependence case across both tiers."""
    rules={};table=[]
    for policy,family in itertools.product(POLICIES,(False,True)):
        chosen=None
        for c in S.INFLATIONS:
            rows=[r for r in selection if r['policy']==policy and r['family_clustered']==family and r['inflation']==c]
            if max(r['worst_scope_rate'] for r in rows)<=.015:
                chosen=c;break
        fallback=chosen is None
        chosen=max(S.INFLATIONS) if chosen is None else chosen
        rules[(policy,family)]=chosen
        table.append({'policy':policy,'family_clustered':family,'selected_inflation':chosen,
                      'largest_candidate_fallback':fallback,'selection_outer_per_cell':SELECTION_OUTER,
                      'criterion':'worst controlled-unit selection rate <=1.5%; otherwise largest candidate',
                      'registered':False})
    S.write_csv('policy_rules.csv',table)
    return rules


def power(engines_by_plan,rules,validation):
    """Measure rho and kappa power under each applicable alternative family."""
    output=[]
    for family,tier,stability in itertools.product((False,True),('quick','standard'),('stable','noisy')):
        for metric,truth in (('rho',.65),('rho',.8),('kappa',.6),('kappa',.7)):
            successes={p:0 for p in POLICIES};used={p:0 for p in POLICIES}
            for i in range(POWER_OUTER):
                ss=page(engines_by_plan,tier,stability,family,11000000+i,**{metric:truth})
                for policy in POLICIES:
                    key='individual:organization/'+metric
                    if key not in ss[policy]: continue
                    ds=decisions(ss[policy],rules[(policy,family)])
                    successes[policy]+=int(ds[key]['decision']=='above')
                    used[policy]+=int(ss[policy][key]['eligible'])
            for policy in POLICIES:
                applicable=(policy!='rho_only' or metric=='rho') and (policy!='kappa_only' or metric=='kappa')
                audit=[r for r in validation if r['policy']==policy and r['family_clustered']==family]
                lo,hi=S.wilson(successes[policy],POWER_OUTER)
                output.append({'names':3,'policy':policy,'family_clustered':family,'tier':tier,'stability':stability,
                               'metric':metric,'truth':truth*100,'applicable':applicable,
                               'selected_inflation':rules[(policy,family)],'outer_pages':POWER_OUTER,
                               'power':successes[policy]/POWER_OUTER if applicable else None,
                               'power_mc95_low':lo if applicable else None,'power_mc95_high':hi if applicable else None,
                               'eligible_rate':used[policy]/POWER_OUTER if applicable else None,
                               'worst_validation_scope_mc95_high':max(r['scope_mc95_high'] for r in audit),
                               'worst_validation_page_mc95_high':max(r['page_mc95_high'] for r in audit),
                               'scope_target_passed':all(r['scope_mc95_high']<.05 for r in audit),
                               'page_target_passed':all(r['page_mc95_high']<.05 for r in audit),
                               'bootstrap_replicates':E.BOOTSTRAPS})
        print('policy power',family,tier,stability,flush=True)
    S.write_csv('policy_comparison.csv',output)


def main():
    """Run selection, untouched audit, then third-stream power for policy options."""
    eng=S.engines()
    selection=error_stage(eng,'selection',SELECTION_OUTER)
    rules=choose(selection)
    validation=error_stage(eng,'validation',VALIDATION_OUTER,rules)
    power(eng,rules,validation)


if __name__=='__main__':
    main()
