"""Repair audit: old cells, independent stress generators and a mixture search.

No model calls. Three worker processes at most. Every reported direction calls
the reference decide_claims on precomputed 10,000-draw replicate arrays. Display
work is skipped, not the support or undefined-replicate decision checks.
"""
import argparse
import concurrent.futures
import csv
import itertools
import json
import math
from pathlib import Path
import statistics

import numpy as np
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # the repository root: versus_estimator and its modules
import versus_estimator as E
import accelerated as A
import stratified_study as T
import stress_generators as G
from synthetic import generate
from simulate import wilson

ENGINES = {}


def engine(names):
    """Cache standard draws per shape, sharing every scenario across pairs."""
    if names not in ENGINES:
        rows=generate(E.TYPES[:names],tier='standard')
        u,_=E.prepare(rows,E.TYPES[:names]);d=E.make_draws(u)
        ENGINES[names]=(d,A.make_engine(d))
    return ENGINES[names]


def evaluate(rows, parts):
    """Use actual planned grids/guards and the sole reference decision rule."""
    E.validate_plan(rows,parts,'standard')
    _,ps=E.prepare(rows,parts)
    records={k:p for k,p in ps['names:on'].items() if p['metric'] in ('rho','kappa')}
    draws,eng=engine(len(parts))
    boot=eng({'names:on':records},draws)['names:on']
    out=E.decide_claims(records,boot,tier='standard',display=False)
    return records,boot,out


def false_page(page,truth):
    """A page fails if any permitted direction contradicts conditional truth."""
    return any((r['decision']=='above' and truth[k]<=50+E.EPS) or
               (r['decision']=='below' and truth[k]>=50-E.EPS)
               for k,r in page['metrics'].items())


def cells():
    """Old 40 standard cells, all verifier generators, full heterogeneity grid."""
    for names,tier,stability,family,shared,case in T.configurations():
        if tier=='standard':
            yield {'kind':'old','names':names,'stability':stability,'family':family,
                   'shared':shared,'case':case}
    for name in G.GENS:
        if name.startswith('blocking_'):continue
        yield {'kind':'verifier','name':name,'names':2 if '2names' in name else 3}
    for names,count,a,noisy,variant,base in itertools.product(
            (2,3),(1,2,3),(.10,.15,.20,.25,.30,1/3,.35,.40,5/12,.45),
            (False,True),('V1','V2'),('deployer','W')):
        yield {'kind':'near','names':names,'erratic_count':count,'a':a,
               'noisy':noisy,'variant':variant,'base':base}

    for name in G.GENS:
        if name.startswith('blocking_'):
            yield {'kind':'verifier','name':name,'names':2 if '2names' in name else 3}


def sample(cell,seed):
    """Return a complete standard page and its analytic conditional truth."""
    parts=E.TYPES[:cell['names']]
    if cell['kind']=='old':
        rows,effects=generate(parts,tier='standard',seed=seed,stability=cell['stability'],
                             family_effect=cell['family'],shared_pairs=cell['shared'],
                             mixed=cell['case']=='conditional_mixed',
                             balanced_families=cell['case']!='conditional_unbalanced',return_effects=True)
        truth=T.conditional_truth(effects,parts,cell['case']=='conditional_mixed')
    elif cell['kind']=='verifier':
        rows,truth=G.GENS[cell['name']](seed)
    else:
        rows,truth=G.near_saturated(parts,seed,**{k:cell[k] for k in
                                   ('erratic_count','a','noisy','variant','base')})
    return rows,truth


def compact_near(cell,seed,return_table=False):
    """Exact sufficient-statistic sampler for the near-saturated family.

All 48 clusters and eight fixed families remain. Each role has four runs.
The stable type is shared by all eight; the noisy type is redrawn per run.
All guards are conservatively passed here, so this can only increase false
sentences. Actual-grid equivalence is checked separately. Real stratified
replicates are still calculated; the reference makes every decision.
"""
    import random
    rng=random.Random(seed)
    parts=E.TYPES[:cell['names']]
    records={}
    choices_by_run={} if return_table else None
    a=cell['a'];count=cell['erratic_count']
    for pair in itertools.combinations(parts,2):
        vals={'rho':{},'kappa':{}}
        for i in range(48):
            erratic=i//6<count
            stable=rng.random()
            roles=[]
            for t in (-1,1):
                choices=[]
                for l,n in itertools.product((-1,1),repeat=2):
                    u=rng.random() if cell['noisy'] else stable
                    if erratic and u<2*a:
                        first=u<a
                    else:
                        pattern=('deployer' if cell['variant']=='V1' else 'W') if erratic else cell['base']
                        first=t==1 if pattern=='deployer' else t*l*n==1
                    choices.append(float(first))
                    if return_table:
                        choices_by_run[('vc-%02d'%(i+1),pair,t,l,n)]=pair[0] if first else pair[1]
                roles.append(statistics.mean(choices))
            a2,a1=roles
            sid='vc-%02d'%(i+1)
            vals['rho'][sid]=100*(a1+1-a2)/2
            vals['kappa'][sid]=100*(a1+a2)/2
        for metric in vals:
            key=':'.join(pair)+'/'+metric
            records[key]={'key':key,'metric':metric,'frame':'conflict','estimate':statistics.mean(vals[metric].values()),
                          'null':50.,'values':vals[metric],
                          'eligibility':{'eligible':True,'reasons':[],'complete_clusters':48}}
    draws,eng=engine(len(parts))
    boot=eng({'names:on':records},draws)['names:on']
    page=E.decide_claims(records,boot,tier='standard',display=False)
    rb=100 if cell['base']=='deployer' else 50
    re=100*(1-a) if cell['variant']=='V1' else 50
    truth={k:(rb*(8-count)+re*count)/8 if k.endswith('/rho') else 50 for k in records}
    if return_table:
        rows=G.M.build(parts,seed=seed,conflict=lambda c:choices_by_run[(c['sid'],c['pair'],c['T'],c['L'],c['N'])])
        return page,truth,records,rows
    return page,truth


def error_job(job):
    """One cell on a stream independent of rule development and the old audit."""
    cell,stage,index,outer=job
    seed_base=(21000000 if stage=='selection' else 22000000)+index*1000
    failures=0
    for i in range(outer):
        if cell['kind']=='near':
            page,truth=compact_near(cell,seed_base+i)
        else:
            rows,truth=sample(cell,seed_base+i)
            _,_,page=evaluate(rows,E.TYPES[:cell['names']])
        failures+=false_page(page,truth)
    lo,hi=wilson(failures,outer)
    return dict(stage=stage,cell=json.dumps(cell,sort_keys=True,separators=(',',':')),
                pages=outer,false_pages=failures,rate=failures/outer,upper95=hi,lower95=lo,
                seed_base=seed_base,bootstrap_seed=E.SEED,replicates=E.BOOTSTRAPS,
                path='conservative_sufficient_statistics' if cell['kind']=='near' else 'full_plan_and_guards')


def write_csv(path,rows):
    """Keep portable compact summaries beside the registered code."""
    with path.open('w') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]),lineterminator="\n");w.writeheader();w.writerows(rows)


def main():
    """Run a declared stream; each completed cell is recoverable in raw JSONL."""
    parser=argparse.ArgumentParser()
    parser.add_argument('stage',choices=('selection','validation'))
    parser.add_argument('--raw',type=Path,required=True)
    parser.add_argument('--supplement',action='store_true')
    parser.add_argument('--workers',type=int,default=3,choices=(1,2,3))
    args=parser.parse_args();output=[]
    jobs=[(c,args.stage,j,100 if c['kind']=='near' or args.stage=='selection' else 400)
          for j,c in enumerate(cells()) if not args.supplement or j>=532]
    fn=error_job
    with args.raw.open('w') as log,concurrent.futures.ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures={pool.submit(fn,j):j for j in jobs}
        for fut in concurrent.futures.as_completed(futures):
            rec=fut.result();output.append(rec)
            log.write(json.dumps(rec)+'\n');log.flush()
            print(args.stage,len(output),'/',len(jobs),rec.get('false_pages',rec.get('new_power')),flush=True)
    output.sort(key=lambda r:r.get('cell',json.dumps(r,sort_keys=True)))
    write_csv(Path(__file__).parent/'results'/('repair_'+args.stage+('_supplement' if args.supplement else '')+'.csv'),output)


if __name__=='__main__':
    main()
