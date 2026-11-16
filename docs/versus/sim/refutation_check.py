"""Exact 729-configuration reproduction of the two-name bootstrap refutation."""
import csv
import itertools
import math
from pathlib import Path
import numpy as np
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # the repository root: versus_estimator and its modules
import versus_estimator as E
from synthetic import generate


def main():
    """Enumerate the erratic family's six types; use both reference decisions."""
    rows=generate(E.TYPES[:2],tier='standard',adversary='deployer')
    u,panels=E.prepare(rows,E.TYPES[:2]);draws=E.make_draws(u)
    records={k:p for k,p in panels['names:on'].items() if p['metric'] in ('rho','kappa')}
    ids=u['_strata']['conflict']['review']
    w=np.array([[d['conflict'].get(s,0) for s in ids] for d in draws],float)
    rho='individual:organization/rho';kap='individual:organization/kappa'
    records[rho]=dict(records[rho],estimate=93.75)
    boot={rho:np.full(E.BOOTSTRAPS,93.75)}
    configs=[]
    for cfg in itertools.product((0.,50.,100.),repeat=6):
        v=np.array(cfg);point=50+(v.mean()-50)/8
        records[kap]=dict(records[kap],estimate=float(point))
        boot[kap]=50+((w@v)/6-50)/8
        old=E.base.simultaneous(records,{k:E.finite_values(vs) for k,vs in boot.items()},2,1.7)
        new=E.decide_claims(records,boot,tier='standard',display=False)
        configs.append((cfg,old['metrics'][kap]['decision']!='not_resolved',
                        new['metrics'][kap]['decision']!='not_resolved'))
    output=[]
    for a in (.1,.15,.2,.25,.3,1/3,.35,.4,5/12,.45):
        old=new=mass=0.
        for cfg,bad_old,bad_new in configs:
            p=math.prod(a if x!=50 else 1-2*a for x in cfg)
            mass+=p;old+=p*bad_old;new+=p*bad_new
        output.append({'names':2,'a':a,'configurations':729,'probability_mass':mass,
                       'old_exact_false_probability':old,'new_exact_false_probability':new,
                       'bootstrap_seed':E.SEED,'replicates':E.BOOTSTRAPS})
    assert sum(old for _,old,_ in configs)==42
    assert not any(new for _,_,new in configs)
    with (Path(__file__).parent/'results/refutation_exact.csv').open('w') as f:
        writer=csv.DictWriter(f,fieldnames=list(output[0]),lineterminator="\n");writer.writeheader();writer.writerows(output)
    print('Old 42/729 at a=1/3; new 0/729. All ten mixture probabilities written.')


if __name__=='__main__':main()
