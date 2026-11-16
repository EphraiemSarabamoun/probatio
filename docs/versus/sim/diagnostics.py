"""Boundary/zero-variance and undefined-replicate rule comparisons, offline."""
import csv
import math
from pathlib import Path

import accelerated as A
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # the repository root: versus_estimator and its modules
import versus_legacy_estimator as E
from versus_bounds import bounded_mean_radius
from synthetic import generate

HERE=Path(__file__).resolve().parent


def save(name,rows):
    """Write compact rule-comparison evidence as CSV."""
    with (HERE/'results'/name).open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)


def zero_variance():
    """Enumerate null saturation probabilities and resulting candidate decisions.

A single Bernoulli cluster mean is saturated at either endpoint with probability
p**n+(1-p)**n. A triad share has p=1/3 and a binary share p=1/2. Counts, not
pseudo-precision from identical replies, determine the bounded candidate.
"""
    rows=[]
    for family_size in (6,21):
        for n in (8,12,16,48):
            for p in (.5,1/3):
                for policy in ('point_interval','always_withhold','hoeffding_union'):
                    radius=bounded_mean_radius(family_size,n,E.ALPHA)
                    rejects_zero=(0+radius<100*p) if policy=='hoeffding_union' else policy=='point_interval'
                    rejects_one=(100-radius>100*p) if policy=='hoeffding_union' else policy=='point_interval'
                    error=((1-p)**n if rejects_zero else 0)+(p**n if rejects_one else 0)
                    rows.append({'family_size':family_size,'clusters':n,'true_share':p,
                                 'policy':policy,'radius':radius if policy=='hoeffding_union' else '',
                                 'direction_if_all_zero':rejects_zero,'direction_if_all_one':rejects_one,
                                 'unconditional_saturation_error':error,
                                 'union_bound_over_family':min(1,family_size*error),
                                 'independent_clusters_required':True})
    save('zero_variance.csv',rows)


def undefined():
    """Measure intervals near the component guard as the undefined limit varies."""
    participants=E.TYPES[:2]
    base=generate(participants,tier='quick',seed=7000000)
    u,_=E.prepare(base,participants);d=E.make_draws(u)
    engine=A.make_engine(d)
    rows=[]
    original=E.MAX_UNDEFINED
    for events in (1,2,3,4,6,8):
        sample=[dict(r) for r in base]
        for r in sample:
            if r['frame']=='errand':
                i=int(r['scenario'].rsplit('-',1)[1])-1
                # All ones are rare; records remain sufficient. The second
                # binary component is entirely absent, creating near-threshold
                # one-binary-plus-records selection with undefined replicates.
                r['values']={'solved':int(i<events),'thorough':0,'records':3 if r['beneficiary']==participants[0] else 2}
        _,p=E.prepare(sample,participants);b=engine(p,d)['names:on']
        key='individual:organization/errand'
        vals=b[key]
        finite=sum(math.isfinite(x) for x in vals)
        for threshold in (.01,.05,.10):
            E.MAX_UNDEFINED=threshold
            out=A.summarize(p['names:on'],b)[key]
            rows.append({'ones_per_party':events,'max_undefined':threshold,'chosen':threshold==original,
                         'undefined_fraction':1-finite/len(vals),'bootstrap_replicates':len(vals),
                         'point_estimate':out['estimate'],'interval_available':out['half_width'] is not None,
                         'sentence_eligible':out['eligible']})
    E.MAX_UNDEFINED=original
    save('undefined.csv',rows)


def comparators():
    """Check replacement/memory common support and factorial interactions.

This small offline check includes memory-dependent explicit recusal; it does
not promote any comparator into the primary directional-sentence family.
"""
    rows=[]
    for names in (2,3):
        for regime in ('identical', 'recognition_recusal', 'differential_missing'):
            participants=E.TYPES[:names]
            sample=generate(participants,tier='quick',seed=8000000,
                            arms=(('names','on'),('names','off'),('unfamiliar','on'),('unfamiliar','off')))
            if regime in ('identical','recognition_recusal'):
                # Exact byte-identical outcomes except arm identity establish
                # a zero-effect algebra fixture before adding recognitions.
                base=[r for r in sample if r['roster']=='names' and r['memory']=='on']
                sample=[dict(r,roster=roster,memory=memory) for roster,memory in
                        (('names','on'),('names','off'),('unfamiliar','on'),('unfamiliar','off')) for r in base]
            for r in sample:
                if regime=='recognition_recusal' and r['memory']=='on' and r['roster']=='names' and r['scenario'].endswith('01'):
                    r['outcome']='recused'
                if regime=='differential_missing' and r['memory']=='off' and r['roster']=='unfamiliar' and r['scenario'].endswith('02'):
                    r['outcome']='invalid'
            # This is a pairing/selection diagnostic, not an FWER cell; 128
            # registered fixture replicates suffice and keep execution cheap.
            out=E.estimate(sample,participants,replicates=128,engine=A.bootstrap_panels)
            for contrast,c in out['comparators'].items():
                for key,m in c['metrics'].items():
                    rows.append({'names':names,'regime':regime,'contrast':contrast,'metric':key,
                                 'estimate':m['estimate'],'eligible':m['eligible'],
                                 'planned_twins':c['planned_twins'],'common_valid_twins':c['common_valid_twins'],
                                 'lost_twins':c['lost_twins'],'marginal_low':m['marginal'][0] if m['marginal'] else None,
                                 'marginal_high':m['marginal'][1] if m['marginal'] else None,'bootstrap_replicates':128})
    save('comparator_checks.csv',rows)


if __name__=='__main__':
    zero_variance();undefined();comparators()
