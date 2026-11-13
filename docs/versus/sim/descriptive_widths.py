"""Current plain-percentile precision for descriptive rows and all quick-tier numbers."""
import itertools
import statistics

import numpy as np
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # the repository root: versus_estimator and its modules
import versus_estimator as E
import simulate as S
import stratified_study as T
from synthetic import generate

OUTER=120


def main():
    """Measure final displayed intervals without inventing power for untested rows."""
    eng=T.engines();output=[]
    for names,tier,stability,family in itertools.product((2,3),('quick','standard'),('stable','noisy'),(False,True)):
        alternatives=[('allocation',x) for x in (.60,.65,.70)]+[('errand',x) for x in (.60,.65)]
        if tier=='quick':
            alternatives += [('rho',x) for x in (.65,.8)]+[('kappa',x) for x in (.6,.7)]
        d,engine=eng[(names,tier)]
        for metric,target in alternatives:
            widths=[];points=[];defined=0
            for i in range(OUTER):
                rows=generate(E.TYPES[:names],tier=tier,seed=20000000+i,stability=stability,
                              family_effect=family,shared_pairs=True,balanced_families=True,**{metric:target})
                _,p=E.prepare(rows,E.TYPES[:names]);key='individual:organization/'+metric
                panel=p['names:on'][key]
                boot=engine({'names:on':{key:panel}},d)['names:on'][key]
                good=boot[np.isfinite(boot)];point=panel['estimate']
                if point is not None: points.append(point)
                if point is not None and len(good)+E.EPS >= (1-E.MAX_UNDEFINED)*len(boot):
                    lo=max(0,point-E.MARGINAL_INFLATION*(point-float(np.quantile(good,E.ALPHA/2))))
                    hi=min(100,point+E.MARGINAL_INFLATION*(float(np.quantile(good,1-E.ALPHA/2))-point))
                    widths.append((hi-lo)/2);defined+=1
            output.append({'names':names,'tier':tier,'stability':stability,'family_effects':family,
                           'metric':metric,'nominal_truth':100*target,'inferential':False,'power':'not_applicable',
                           'outer_pages':OUTER,'defined_interval_rate':defined/OUTER,
                           'median_marginal_half_width':statistics.median(widths) if widths else None,
                           'mean_estimate':statistics.mean(points) if points else None,
                           'inflation':E.MARGINAL_INFLATION,'bootstrap_replicates':E.BOOTSTRAPS})
        print('descriptive widths',names,tier,stability,family,flush=True)
    S.write_csv('repair_descriptive_widths.csv',output)


if __name__=='__main__':
    main()
