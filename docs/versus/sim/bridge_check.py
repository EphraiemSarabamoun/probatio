"""Check sufficient-statistic search pages against coupled complete reply grids."""
import itertools
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # the repository root: versus_estimator and its modules
import versus_estimator as E
import repair_study as R


def main():
    """Match every cluster value and current interval across 32 coupled pages."""
    members=pages=0
    for names,count,noisy,variant,base in itertools.product((2,3),(1,3),(False,True),('V1','V2'),('deployer','W')):
        cell={'kind':'near','names':names,'erratic_count':count,'a':5/12,
              'noisy':noisy,'variant':variant,'base':base}
        compact,truth,records,rows=R.compact_near(cell,24000000+pages,return_table=True)
        parts=E.TYPES[:names]
        full,boot,page=R.evaluate(rows,parts)
        for key,rec in records.items():
            assert rec['values']==full[key]['values']
            assert abs(rec['estimate']-full[key]['estimate'])<1e-10
            if full[key]['eligibility']['eligible']:
                assert compact['metrics'][key]['simultaneous']==page['metrics'][key]['simultaneous']
                assert compact['metrics'][key]['decision']==page['metrics'][key]['decision']
            members+=1
        assert not R.false_page(compact,truth)
        pages+=1
    result={'coupled_full_pages':pages,'cluster_vectors_and_decisions_checked':members,
            'mismatches':0,'stream':24000000,'replicates':E.BOOTSTRAPS}
    (Path(__file__).parent/'results/repair_bridge.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result))


if __name__=='__main__':main()
