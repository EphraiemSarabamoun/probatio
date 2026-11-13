"""Independent algebra, guard regression and shared production-fixture checks."""
import copy
import json
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # the repository root: versus_estimator and its modules
import versus_estimator as E
from fixture_io import unpack_rows
from synthetic import generate


class EstimatorTests(unittest.TestCase):
    """Verify menu mapping, inferential families and missing-data behavior."""

    def test_registry(self):
        self.assertEqual(len(E.registry(E.TYPES)), 6)
        self.assertEqual(len(E.estimands(E.TYPES)),21)
        self.assertEqual(len(E.registry(E.TYPES[:2])), 2)
        for bad in ([], ['individual'], ['individual', 'individual']):
            with self.assertRaises(ValueError):
                E.registry(bad)

    def test_fraction(self):
        rows = generate(E.TYPES[:2], tier='quick', adversary='deployer')
        group = [r for r in rows if r['scenario'] == 'vc-01']
        for a in ('T', 'L', 'N', 'W'):
            self.assertEqual(sum(r[a] for r in group), 0)
            for b in ('T', 'L', 'N', 'W'):
                if a != b:
                    self.assertEqual(sum(r[a]*r[b] for r in group), 0)
        _, p = E.prepare(rows, E.TYPES[:2])
        vals = {k.rsplit('/',1)[1]: r['estimate'] for k,r in p['names:on'].items()}
        self.assertEqual([vals[k] for k in ('a1','a2','rho','kappa')], [100,0,100,50])
        self.assertTrue(p['names:on']['individual:organization/rho']['eligibility']['eligible'])

    def test_half_ties(self):
        rows = generate(E.TYPES[:2], tier='quick', adversary='all_C')
        _, p = E.prepare(rows, E.TYPES[:2])
        r = p['names:on']['individual:organization/allocation']
        self.assertEqual(r['estimate'],50)
        self.assertTrue(r['eligibility']['eligible'])
        self.assertEqual(r['eligibility']['diagnostics']['decided']['state'],'not_applicable_all_C')

    def test_relative_means_not_mean_ratios(self):
        obs = [[1,1,10,0,0,0], [0,0,0,1,1,1]]*4
        result, used = E.errand_value(obs)
        expected = 50*(1+(0+0+0.9)/3)
        self.assertAlmostEqual(result, expected)
        self.assertEqual(len(used),3)
        swapped = [x[3:]+x[:3] for x in obs]
        self.assertAlmostEqual(E.errand_value(swapped)[0],100-result)

    def test_guard_codes_and_counts(self):
        with open(Path(__file__).parent/'fixtures/catalog.json') as f:
            cases=json.load(f)
        reasons=set()
        for case in cases:
            x=json.loads((Path(__file__).parent/'fixtures'/case['fixture']).read_text())
            for page in x['expected'].get('arms',{}).values():
                for r in page['metrics'].values():
                    reasons.update(r['withheld_reasons'])
        self.assertTrue({'too_few_complete_clusters','too_few_valid_in_cell','missing_domain_support',
                         'decided_diagnostic_insufficient','decided_position_driven','C_order_dependent',
                         'conflict_letter_driven','conflict_narrative_driven','conflict_wording_driven',
                         'triad_position_driven','too_few_errand_components','too_many_undefined_replicates',
                         'too_many_joint_undefined_replicates','saturated_nonlinear_errand_no_sentence'} <= reasons)

    def test_bad_inputs(self):
        rows=generate(E.TYPES[:2],tier='quick')
        with self.assertRaises(ValueError): E.prepare(rows+[rows[0]],E.TYPES[:2])
        with self.assertRaises(ValueError): E.prepare(rows[1:],E.TYPES[:2])
        r=copy.deepcopy(rows)
        next(x for x in r if x['frame']=='conflict')['W'] *= -1
        with self.assertRaises(ValueError): E.prepare(r,E.TYPES[:2])
        r=copy.deepcopy(rows)
        next(x for x in r if x['frame']=='errand')['values']['records']=float('nan')
        with self.assertRaises(ValueError): E.prepare(r,E.TYPES[:2])

    def test_common_draw_axes(self):
        rows=generate(E.TYPES[:2],tier='quick')
        other=[dict(r,roster='unfamiliar') for r in rows]
        out=E.estimate(rows+other,E.TYPES[:2],replicates=40,tier='quick')
        cmp=out['comparators']['replacement:on']
        self.assertEqual(cmp['lost_twins'],0)
        for m in cmp['metrics'].values():
            self.assertEqual(m['estimate'],0)
            self.assertEqual(m['marginal'],[0,0])

    def test_comparator_common_components(self):
        f=json.loads((Path(__file__).parent/'fixtures/18_component_intersection.json').read_text())
        rec=f['expected']['comparators']['replacement:on']['metrics']['individual:organization/errand']
        self.assertIsNone(rec['estimate'])
        self.assertFalse(rec['eligible'])
        self.assertTrue(all('too_few_common_errand_components' in x['reasons'] for x in rec['sides'].values()))

    def test_unmatched_plan(self):
        rows=generate(E.TYPES[:2],tier='quick')
        other=[dict(r,roster='unfamiliar') for r in rows if r['scenario']!='va-01']
        with self.assertRaisesRegex(ValueError,'plan does not match'):
            E.estimate(rows+other,E.TYPES[:2],replicates=20,tier='quick')

    def test_joint_undefined(self):
        rows=generate(E.TYPES[:2],tier='quick')
        _,p=E.prepare(rows,E.TYPES[:2]);rec=p['names:on']
        b={k:[None]*20 for k in rec}
        out=E.simultaneous(rec,b,tier='standard')
        self.assertTrue(all(r['decision'] in ('not_resolved','descriptive') for r in out['metrics'].values()))

    def test_strata_are_fixed(self):
        rows=generate(tier='quick')
        u,p=E.prepare(rows,E.TYPES)
        for draw in E.make_draws(u,20):
            for frame,groups in u['_strata'].items():
                for family,ids in groups.items():
                    self.assertEqual(sum(draw[frame].get(s,0) for s in ids),len(ids))
        self.assertEqual(len(E.registry(E.TYPES)),6)

    def test_descriptive_rows_never_drive_sentences(self):
        out=E.estimate(generate(E.TYPES[:2],tier='quick',adversary='same_name'),E.TYPES[:2],replicates=40,tier='quick')
        for key,r in out['arms']['names:on']['metrics'].items():
            if not key.endswith(('/rho','/kappa')):
                self.assertEqual(r['decision'],'descriptive')
                self.assertIsNone(r['simultaneous'])

    def test_claim_family_cannot_shrink(self):
        rows=generate(E.TYPES[:2],tier='quick')
        u,p=E.prepare(rows,E.TYPES[:2])
        rs=p['names:on'];d=E.make_draws(u,20)
        b=E.bootstrap_panels(p,d)['names:on']
        k='individual:organization/rho'
        with self.assertRaises(ValueError): E.simultaneous({k:rs[k]},{k:b[k]},tier='standard')

    def test_quick_never_writes_a_direction(self):
        rows=generate(E.TYPES[:2],tier='quick',adversary='deployer')
        out=E.estimate(rows,E.TYPES[:2],replicates=40,tier='quick')
        page=out['arms']['names:on']
        self.assertFalse(page['sentences_authorized'])
        self.assertEqual(page['active_family_size'],0)
        for rec in page['metrics'].values():
            self.assertEqual(rec['decision'],'descriptive')
            self.assertFalse(rec['inferential'])
            self.assertIsNone(rec['simultaneous'])

    def test_scope_line_once_and_descriptive_gates(self):
        """Pin page-level scope without granting quick or comparator conclusions."""
        fixture = json.loads((Path(__file__).parent/'fixtures/02_three_names.json').read_text())
        page = fixture['expected']['arms']['names:on']
        referents = fixture['identity']['participants']
        line = ("Conclusions use the conflict rows only. At this bank's size, the other rows "
                "could not support a useful page-level error guarantee in the synthetic study.")
        self.assertEqual(E.sentences(page,referents).count(line),1)
        self.assertEqual(len(E.sentences(page,referents)),7)
        page = dict(page, claim_policy='quick_descriptive_only',sentences_authorized=False)
        self.assertEqual(E.sentences(page,referents),['Conclusions require the standard tier.'])
        page['claim_policy'] = 'non_primary_arm_descriptive'
        self.assertEqual(E.sentences(page,referents),
                         ['This arm is descriptive; conclusions use the primary arm only.'])

    def test_hoeffding_boundary(self):
        rows=generate(E.TYPES[:2],tier='standard',adversary='same_name')
        out=E.estimate(rows,E.TYPES[:2],replicates=30,tier='standard')
        m=out['arms']['names:on']['metrics']['individual:organization/kappa']
        rad=100*math.sqrt(math.log(4/.05)/(2*48))
        self.assertAlmostEqual(m['simultaneous'][0],100-rad)
        self.assertEqual(m['decision'],'above')


class FixtureTests(unittest.TestCase):
    """Replay every full reference output and compare the optional fast path."""

    def compare(self,a,b):
        """Accept only floating rounding differences, never changed semantics."""
        if isinstance(a,dict):
            self.assertEqual(set(a),set(b))
            for k in a: self.compare(a[k],b[k])
        elif isinstance(a,list):
            self.assertEqual(len(a),len(b))
            for x,y in zip(a,b): self.compare(x,y)
        elif isinstance(a,(int,float)) and not isinstance(a,bool):
            self.assertAlmostEqual(a,b,places=8)
        else:
            self.assertEqual(a,b)


    def test_all_reference_fixtures(self):
        for path in sorted((Path(__file__).parent/'fixtures').glob('[0-9]*.json')):
            with self.subTest(fixture=path.name):
                f=json.loads(path.read_text());rows=unpack_rows(f['row_table'])
                if 'refused' in f['expected']:
                    with self.assertRaisesRegex(ValueError,'plan does not match'):
                        E.estimate(rows,f['participants'],replicates=f['replicates'],tier=f['tier'])
                    continue
                result=E.estimate(rows,f['participants'],f['replicates'],f['seed'],tier=f['tier'])
                self.compare(f['expected'],result)
                for arm,page in result['arms'].items():
                    self.assertEqual(E.sentences(page,f['identity']['participants']),f['sentences'][arm])

    def test_reported_rule_matches_reference(self):
        rule=json.loads((Path(__file__).parent/'results/repair_rule.json').read_text())
        self.assertEqual(E.JOINT_RULE,rule['rule'])
        self.assertEqual(E.MARGINAL_INFLATION,1.)

    def test_numpy_matches_every_fixture(self):
        try:
            import accelerated as A
        except ImportError:
            self.skipTest('NumPy optional; stdlib reference remains tested')
        for path in sorted((Path(__file__).parent/'fixtures').glob('[0-9]*.json')):
            with self.subTest(fixture=path.name):
                f=json.loads(path.read_text())
                if 'refused' in f['expected']: continue
                rows=unpack_rows(f['row_table'])
                result=E.estimate(rows,f['participants'],f['replicates'],f['seed'],engine=A.bootstrap_panels,tier=f['tier'])
                self.compare(f['expected'],result)
                u,p=E.prepare(rows,f['participants']);d=E.make_draws(u,f['replicates'],f['seed'])
                boot=A.make_engine(d)(p,d)
                for arm,rs in p.items():
                    claims={k:v for k,v in rs.items() if v['metric'] in ('rho','kappa')}
                    fast=E.decide_claims(claims,boot[arm],tier=f['tier'],primary=arm==result['primary_arm'],display=False)
                    for k,v in fast['metrics'].items():
                        self.assertEqual(v['decision'],result['arms'][arm]['metrics'][k]['decision'])
                        self.assertEqual(v['withheld_reasons'],result['arms'][arm]['metrics'][k]['withheld_reasons'])
                        self.assertEqual(v['simultaneous'],result['arms'][arm]['metrics'][k]['simultaneous'])


class BoundaryTests(unittest.TestCase):
    compare = FixtureTests.compare
    """Count boundaries, reference API gates and exact sentence direction."""

    def test_guard_boundary_tables(self):
        cases=json.loads((Path(__file__).parent/'fixtures/boundaries.json').read_text())
        for f in cases:
            with self.subTest(boundary=f['name']):
                _,ps=E.prepare(unpack_rows(f['row_table']),f['participants'])
                self.compare(f['eligibility'],ps['names:on'][f['key']]['eligibility'])

    def test_defined_draw_boundary(self):
        fixtures=json.loads((Path(__file__).parent/'fixtures/replicate_boundaries.json').read_text())
        for f in fixtures:
            with self.subTest(fixture=f['name']):
                _,ps=E.prepare(unpack_rows(f['row_table']),f['participants'])
                claims={k:v for k,v in ps['names:on'].items() if v['metric'] in ('rho','kappa')}
                defined=f['defined'];n=f['replicates']
                boot={k:[p['estimate']]*defined+[None]*(n-defined) for k,p in claims.items()}
                page=E.decide_claims(claims,boot,tier='standard')
                self.compare(f['expected'],page)
                fast=E.decide_claims(claims,boot,tier='standard',display=False)
                for key,r in page['metrics'].items():
                    self.assertEqual(r['decision'],fast['metrics'][key]['decision'])
                    self.assertEqual(r['withheld_reasons'],fast['metrics'][key]['withheld_reasons'])

    def test_tier_is_required_and_plan_cannot_be_promoted(self):
        rows=generate(E.TYPES[:2],tier='quick',adversary='deployer')
        with self.assertRaises(TypeError): E.estimate(rows,E.TYPES[:2],replicates=10)
        with self.assertRaisesRegex(ValueError,'plan does not match standard'):
            E.estimate(rows,E.TYPES[:2],replicates=10,tier='standard')
        with self.assertRaises(ValueError): E.estimate(rows,E.TYPES[:2],replicates=10,tier='other')
        for tier in ('quick','standard'):
            rows=generate(E.TYPES[:2],tier=tier,adversary='deployer')
            out=E.estimate(rows,E.TYPES[:2],replicates=10,tier='quick')
            for p in out['arms'].values():
                for r in p['metrics'].values():
                    self.assertFalse(any('rule' in k for k in r))
                    self.assertEqual(r['decision'],'descriptive')

    def test_nonprimary_arms_are_descriptive(self):
        rows=generate(E.TYPES[:2],tier='standard',adversary='deployer',
                      arms=(('names','on'),('names','off'),('unfamiliar','on'),('unfamiliar','off')))
        out=E.estimate(rows,E.TYPES[:2],replicates=10,tier='standard')
        for arm,page in out['arms'].items():
            if arm==out['primary_arm']:continue
            for r in page['metrics'].values():
                self.assertFalse(r['inferential']);self.assertIsNone(r['simultaneous'])
                self.assertEqual(r['decision'],'descriptive')

    def test_plain_marginal(self):
        for shape in (2,3):
            u,ps=E.prepare(generate(E.TYPES[:shape],tier='standard'),E.TYPES[:shape])
            draws=E.make_draws(u,128);boot=E.bootstrap_panels(ps,draws)['names:on']
            page=E.simultaneous(ps['names:on'],boot,tier='standard')
            for key,rec in page['metrics'].items():
                good=[v for v in boot[key] if v is not None]
                if rec['marginal'] is not None:
                    self.compare(rec['marginal'],[E.quantile(good,.025),E.quantile(good,.975)])
                self.assertEqual(rec['marginal_label'],'exploratory')

    def test_deployer_at_minimum_complete_support(self):
        from synthetic import letter_for
        order=['vc-%02d'%(family*6+j+1) for j in range(6) for family in range(8)]
        cells=[(t,l,n) for t in (-1,1) for l in (-1,1) for n in (-1,1)]
        for names in (2,3):
            parts=E.TYPES[:names]
            for n in (12,11):
                rows=generate(parts,tier='standard',adversary='deployer')
                keep=set(order[:n])
                for row in rows:
                    if row['frame']=='conflict' and row['scenario'] not in keep:
                        idx=int(row['scenario'].split('-')[1])-1
                        if (row['T'],row['L'],row['N'])==cells[idx%8]:row['outcome']='missing'
                out=E.estimate(rows,parts,replicates=128,tier='standard')
                for key,rec in out['arms']['names:on']['metrics'].items():
                    if key.endswith('/rho'):
                        self.assertEqual(rec['decision'],'above' if n==12 else 'not_resolved')
                        self.assertEqual(rec['eligibility']['complete_clusters'],n)

    def test_count_neighbor_decision_boundaries(self):
        # A cluster changes by 12.5 points for one of its eight choice runs.
        # Fix all other counts; the adjacent totals straddle the joint null.
        for m in (2,6):
            participants=E.TYPES[:2] if m==2 else E.TYPES
            _,ps=E.prepare(generate(participants,tier='standard'),participants)
            records={k:v for k,v in ps['names:on'].items() if v['metric'] in ('rho','kappa')}
            rad=E.bounded_radius(m,48);step=100/384
            for sign in (-1,1):
                cut=(50+sign*rad)/step
                totals=(math.floor(cut),math.floor(cut)+1)
                for total in totals:
                    rec=copy.deepcopy(records)
                    key=next(iter(rec));point=total*step
                    rec[key]['estimate']=point
                    boot={k:[p['estimate']]*20 for k,p in rec.items()}
                    page=E.decide_claims(rec,boot,tier='standard')
                    expected='above' if point>50+rad else 'below' if point<50-rad else 'not_resolved'
                    self.assertEqual(page['metrics'][key]['decision'],expected)


if __name__=='__main__':
    unittest.main()
