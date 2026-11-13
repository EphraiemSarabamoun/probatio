"""Final-policy loss and deterministic-adversary checks, with conditional support."""
import itertools
import json
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # the repository root: versus_estimator and its modules
import versus_estimator as E
from repair_study import false_page
import simulate as S
import stratified_study as T
from synthetic import FAMILIES, generate

RESULTS=Path(__file__).resolve().parent/'results'
OUTER=200


def support_truth(panel,effects,metric):
    """Condition on retained scenario counts when attrition unbalances families."""
    counts=panel['eligibility']['family_complete_counts']
    total=sum(counts.values())
    if not total:
        return 50.
    if metric=='rho': return 50.
    return 100*(.5+sum(n*effects[('conflict',f)] for f,n in counts.items())/total)


def losses():
    """Run all final support/guard logic under independent nonchoice losses."""
    eng=T.engines()
    output=[]
    regimes={'invalid10':{'invalid':.1},'refused10':{'refusal':.1},
             'recused10':{'recusal':.1,'recognition':True},'invalid20':{'invalid':.2}}
    for names,stability,family in itertools.product((2,3),('stable','noisy'),(False,True)):
        d,engine=eng[(names,'standard')]
        for regime,kwargs in regimes.items():
            errors=0;eligible=0
            for i in range(OUTER):
                rows,effects=generate(E.TYPES[:names],tier='standard',seed=18000000+i,
                                      stability=stability,family_effect=family,shared_pairs=True,
                                      balanced_families=True,return_effects=True,**kwargs)
                _,p=E.prepare(rows,E.TYPES[:names])
                primary={k:v for k,v in p['names:on'].items() if v['metric'] in ('rho','kappa')}
                boot=engine({'names:on':primary},d)['names:on']
                page=E.decide_claims(primary,boot,tier='standard',display=False)
                truth={k:support_truth(v,effects,v['metric']) for k,v in primary.items()}
                errors+=int(false_page(page,truth))
                eligible+=sum(not v['withheld_reasons'] for v in page['metrics'].values())
            lo,hi=S.wilson(errors,OUTER)
            output.append({'names':names,'tier':'standard','stability':stability,'family_effects':family,
                           'regime':regime,'joint_rule':E.JOINT_RULE,'outer_pages':OUTER,
                           'false_pages':errors,'false_rate':errors/OUTER,'mc95_low':lo,'mc95_high':hi,
                           'eligible_rate':eligible/(OUTER*(2 if names==2 else 6)),
                           'conditioning':'fixed family effects and surviving-scenario weights'})
        print('loss controls',names,stability,family,flush=True)
    S.write_csv('repair_losses.csv',output)


def adversaries():
    """Show current-policy estimates, guard reasons and standard/quick decisions."""
    output=[]
    for names,tier,policy in itertools.product((2,3),('quick','standard'),
                ('first_option','first_named','deployer','all_C','same_wording','same_letter','same_name')):
        rows=generate(E.TYPES[:names],tier=tier,seed=19000000,adversary=policy)
        # Fixture-length draws establish deterministic decoding/guard behavior;
        # all inferential Monte Carlo tables use 10,000 draws separately.
        out=E.estimate(rows,E.TYPES[:names],replicates=128,tier=tier)
        for key,rec in out['arms']['names:on']['metrics'].items():
            # Other pairs' deterministic mapping is identical. Preserve a
            # compact representative pair and every triad share publicly.
            if not key.startswith('individual:organization/') and not key.startswith('triad/'):
                continue
            output.append({'names':names,'tier':tier,'policy':policy,'metric':key,
                           'estimate':rec['estimate'],'inferential':rec['inferential'],
                           'decision':rec['decision'],'eligible':rec['eligibility']['eligible'],
                           'reasons':'|'.join(rec['withheld_reasons'])})
    S.write_csv('repair_adversaries.csv',output)


if __name__=='__main__':
    losses();adversaries()
