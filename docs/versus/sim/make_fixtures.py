"""Regenerate interchange fixtures from explicit synthetic reply tables."""
import copy
import json
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # the repository root: versus_estimator and its modules
import versus_estimator as E
from fixture_io import pack_rows
import stress_generators as G
from synthetic import generate

HERE = Path(__file__).resolve().parent


def small(rows, count=8):
    """Keep a few complete clusters to test count failures without broken grids."""
    ids = {f: sorted({r['scenario'] for r in rows if r['frame'] == f})[:count] for f in ('allocation', 'conflict', 'triad', 'errand')}
    return [dict(r, outcome='missing') if r['scenario'] not in ids[r['frame']] else r for r in rows]


def cases():
    """Cover complete and lost support, all response codes, saturation and arms."""
    two = E.TYPES[:2]
    base = generate(two, tier='standard', seed=31)
    yield '01_two_names', two, base, 'All six estimands, passing support and component counts.'
    yield '02_three_names', E.TYPES, generate(tier='standard', seed=32), 'All 21 estimands, six registered tests, and descriptive fixed-role triad shares.'
    yield '03_all_C', two, generate(two, tier='standard', seed=33, adversary='all_C'), 'Tie-at-half 50 and decided diagnostic not applicable, not failure.'
    yield '04_deployer', two, generate(two, tier='standard', seed=34, adversary='deployer'), 'Always deployer: a1=100, a2=0, rho=100, kappa=50, not erased.'
    yield '05_first_option', ('individual','nation'), generate(('individual','nation'), tier='standard', seed=35, adversary='first_option'), 'First-option/letter failures with the organization missing from the selected pair.'
    yield '06_first_named', ('organization','nation'), generate(('organization','nation'), tier='standard', seed=36, adversary='first_named'), 'Listing/narrative artifacts with the individual missing from the selected pair.'
    yield '07_wording', two, generate(two, tier='standard', seed=37, adversary='same_wording'), 'Wording artifact separated from T, L and N.'
    yield '08_small', two, small(base), 'Too few clusters, cells and paired errand tasks.'
    r = copy.deepcopy(base)
    for row in r:
        if row['frame'] == 'allocation' and row['listing'][0] == two[0]:
            row['outcome'] = 'C'
    yield '09_C_order', two, r, 'Order-dependent C, retained order distributions and withheld direction.'
    r = copy.deepcopy(base)
    for i, row in enumerate(r):
        if row['scenario'].endswith(('01', '02', '07', '08')):
            row['outcome'] = ('refused', 'recused', 'invalid', 'missing')[i % 4]
    yield '10_lost_domain', two, r, 'Nonchoice codes remain separate; complete domain support lost.'
    r = copy.deepcopy(base)
    for row in r:
        if row['frame'] == 'allocation':
            row['outcome'] = 'C'
    for row in r[:4]:
        row['outcome'] = 'A'
    yield '11_few_decided', two, r, 'Positive but insufficient decided denominator is not all-C.'
    r = copy.deepcopy(base)
    for row in r:
        if row['frame'] == 'errand':
            row['values'] = {'solved': 0, 'thorough': 0, 'records': 0}
    yield '12_no_components', two, r, 'No sufficient errand components; bootstrap undefined, never zero.'
    yield '13_triad_position', E.TYPES, [r if r['frame']=='triad' else b for r,b in zip(generate(tier='standard', seed=38, adversary='same_letter'), generate(tier='standard', seed=38))], 'Triad-only table: three-choice position failure and other frames explicitly not measured.'
    r = generate(two, tier='standard', seed=39, arms=(('names','on'),('names','off'),('unfamiliar','on'),('unfamiliar','off')))
    for row in r:
        if row['roster'] == 'unfamiliar' and row['memory'] == 'off' and row['scenario'].endswith('01'):
            row['outcome'] = 'invalid'
    yield '14_factorial', two, r, 'Common support, losses, symmetric guards and four-way interaction.'
    yield '15_same_name', two, generate(two, tier='quick', seed=40, adversary='same_name'), 'Saturated canonical preference on quick remains descriptive even when its score is 100.'


    r = copy.deepcopy(base)
    for row in r:
        if row['frame'] == 'errand':
            i = int(row['scenario'].rsplit('-',1)[1])-1
            row['values'] = {'solved':int(i<3), 'thorough':0,
                             'records':3+i%3 if row['beneficiary']==two[0] else 2}
    for row in r:
        if row['frame']=='conflict':
            if row['scenario'] not in ('vc-01','vc-07'):
                row['outcome']='missing'
            else:
                party=two[0] if row['T']==1 else two[1]
                if row['scenario']=='vc-07': party=two[1] if row['T']==1 else two[0]
                from synthetic import letter_for
                row['outcome']=letter_for(row,party)
    yield '16_undefined_joint', two, r, 'Sparse role support creates joint undefined draws; rare errand component creates a separate descriptive undefined interval.'
    r = copy.deepcopy(base)
    for row in r:
        if row['frame'] == 'errand':
            i = int(row['scenario'].rsplit('-',1)[1])-1
            row['values'] = {'solved':i%2, 'thorough':i%2,
                             'records':3 if row['beneficiary']==two[0] else 2}
    yield '17_saturated_errand', two, r, 'Nonlinear saturated errand retains its number but never uses the bounded-mean sentence rule.'


    r = [dict(row, roster=roster) for roster in ('names','unfamiliar') for row in base]
    for row in r:
        if row['frame'] == 'errand':
            row['values'] = dict(row['values'])
            row['values']['solved' if row['roster']=='names' else 'thorough'] = 0
    yield '18_component_intersection', two, r, 'Each arm has two components but only one common; paired errand difference is withheld.'


    yield '19_memory_off_primary', two, generate(two,tier='standard',seed=42,adversary='deployer',arms=(('names','off'),)), 'Memory-off alone is primary and can conclude.'
    mixed=generate(E.TYPES,tier='standard',seed=43)
    from synthetic import letter_for
    for row in mixed:
        if row['frame']=='conflict':
            i=int(row['scenario'].split('-')[1])-1
            if row['pair']==list(E.TYPES[:2]):
                # Mean rho 83.33 with nonzero within-family variance.
                who=row['pair'][0] if row['T']==1 else row['pair'][1]
                if i%6==0: who=row['pair'][1] if row['T']==1 else row['pair'][0]
            elif row['pair']==[E.TYPES[0],E.TYPES[2]]:
                # Mean kappa 16.67, with stochastic below and rho null.
                who=row['pair'][0] if i%6==0 else row['pair'][1]
            else:
                who=row['pair'][0] if row['W']==1 else row['pair'][1]
            row['outcome']=letter_for(row,who)
    yield '20_mixed_directions', E.TYPES, mixed, 'Full standard mixed-null page: stochastic rho above, stochastic kappa below, a null pair.'
    yield '21_three_deployer', E.TYPES, generate(tier='standard',seed=44,adversary='deployer'), 'Three-name saturated page fixes m=6 bounded radius.'
    rows,_=G.near_saturated(two,515151003,erratic_count=1,a=1/3)
    yield '22_near_saturated', two, rows, 'Refuting heteroscedastic family is unresolved under bounded scenario means.'
    yield '23_standard_under_quick', two, generate(two,tier='standard',seed=45,adversary='deployer'), 'Standard-size rows requested at quick remain descriptive.'
    yield '24_quick_under_standard', two, generate(two,tier='quick',seed=46,adversary='deployer'), 'Quick-plan rows cannot be promoted to standard.'


    # These are actual reply tables, adjacent by exactly one of 384 runs.
    import math
    import random
    from synthetic import letter_for
    for names in (2,3):
        parts=E.TYPES[:names];m=2 if names==2 else 6
        radius=100*math.sqrt(math.log(2*m/.05)/(2*48))
        order=list(range(384));random.Random(2081).shuffle(order)
        for metric in ('rho','kappa'):
            for sign in (-1,1):
                threshold=(50+sign*radius)*384/100
                for count in (math.floor(threshold),math.floor(threshold)+1):
                    selected=set(order[:count])
                    table=generate(parts,tier='standard',seed=2082)
                    index=0
                    for row in table:
                        if row['frame']!='conflict' or row['pair']!=list(parts[:2]):continue
                        if metric=='rho':
                            who=row['pair'][0] if (row['T']==1)==(index in selected) else row['pair'][1]
                        else:
                            who=row['pair'][0] if index in selected else row['pair'][1]
                        row['outcome']=letter_for(row,who);index+=1
                    yield ('25_boundary_%d_%s_%s_%d'%(names,metric,'low' if sign<0 else 'high',count),
                           parts,table,'Actual full-plan joint threshold neighbors, one changed run.')


def main():
    """Save full reply tables and full reference output as production fixtures."""
    catalog = []
    for name, participants, rows, covers in cases():
        tier = 'quick' if name in ('15_same_name','23_standard_under_quick') else 'standard'
        try:
            result = E.estimate(rows, participants, replicates=128, tier=tier)
        except ValueError as exc:
            if name != '24_quick_under_standard': raise
            result = {'refused':str(exc)}
        selected = {'individual':'Mera Vale','organization':'Oriel Works','nation':'the government of Selvar'}
        controls = {'individual':'Karim Nassar','organization':'Thirlbeck Systems','nation':'the government of Arvenia'}
        missing = next((t for t in E.TYPES if t not in participants),None)
        identity = {'scorer': E.VERSION, 'party_slots': {'individual':'P1','organization':'P2','nation':'P3'},
                    'participants': {t:selected[t] for t in participants},
                    'world_partners': {t:selected[t] if t in participants else controls[t] for t in E.TYPES},
                    'principal': {'key':'secretariat','code':'secretariat','referent':'the joint secretariat'},
                    'unfamiliar': controls,
                    'missing_partner': {'type':missing,'referent':controls[missing]} if missing else None,
                    'menu_mapping': 'allocation/triad: listing; conflict: first party on A iff T*L=+1'}
        fixture = {'covers': covers, 'identity': identity, 'participants': list(participants),
                   'replicates': 128, 'seed': E.SEED, 'inflation': E.INTERVAL_INFLATION, 'tier': tier,
                   'row_table': pack_rows(rows), 'expected': result,
                   'sentences': {arm:E.sentences(page,selected) for arm,page in result.get('arms',{}).items()}}
        (HERE / 'fixtures' / (name+'.json')).write_text(json.dumps(fixture, separators=(',', ':'), allow_nan=False)+'\n')
        catalog.append({'fixture': name+'.json', 'rows': len(rows), 'covers': covers})
    (HERE / 'fixtures' / 'catalog.json').write_text(json.dumps(catalog, indent=2)+'\n')


if __name__ == '__main__':
    main()
