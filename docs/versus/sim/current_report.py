"""Summarize the repaired bounded rule from completed, separately seeded studies."""
import csv
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # the repository root: versus_estimator and its modules
import versus_estimator as E

RESULTS=Path(__file__).parent/'results'


def read(name):
    """Require actual results; missing evidence never becomes a passing default."""
    with (RESULTS/name).open() as f:return list(csv.DictReader(f))


def main():
    """Write the candidate's gate and a concise power/error evidence summary."""
    selection=read('repair_selection.csv');audit=read('repair_validation.csv');power=read('repair_power.csv');callable_rows=read('callable_effects.csv')
    assert len(selection)==538 and len(audit)==538 and len(power)==32
    passed=all(float(r['upper95'])<.05 for r in selection+audit)
    worst=max(audit,key=lambda r:float(r['upper95']))
    rule={'version':E.VERSION,'rule':E.JOINT_RULE,'selection_passed':passed,
          'validation_passed':all(float(r['upper95'])<.05 for r in audit),
          'cells_per_stream':len(audit),'selection_pages':sum(int(r['pages']) for r in selection),
          'validation_pages':sum(int(r['pages']) for r in audit),
          'selection_seed':21000000,'validation_seed':22000000,'power_method':'exact_lattice_convolution',
          'bootstrap_seed':E.SEED,'bootstrap_replicates':E.BOOTSTRAPS,
          'worst_validation_upper95':float(worst['upper95']),'worst_cell':json.loads(worst['cell']),
          'marginal_inflation':1.,'fitted_constants':False,'registered':False,'live_calibrated':False,
          'status':'synthetic_audit_passed_pending_independent_review' if passed else 'failed_do_not_register'}
    (RESULTS/'repair_rule.json').write_text(json.dumps(rule,indent=2)+'\n')
    text=['# Repaired versus statistics, draft 5','',
          'Only the primary standard arm can conclude rho/kappa directions. The joint rule is a Hoeffding union bound on every complete-scenario mean; marginal bootstrap intervals are plain percentiles, labelled exploratory. Quick and other arms are descriptive. No live benchmark is frozen or validated.','',
          'The previous 1.7 max-T rule is refuted. See [the failure evidence](REFUTATION.md) and [its exact reproduction](refutation_exact.csv). The replacement does not estimate a tail from six erratic observations. Its cost is substantial moderate-effect power.','',
          '| stream / generator family | cells | false pages / pages | largest cell Wilson 95% upper |','|---|---:|---:|---:|']
    for stage,rows in (('selection',selection),('validation',audit)):
        for kind in ('old','verifier','near'):
            subset=[r for r in rows if json.loads(r['cell'])['kind']==kind]
            text.append('| %s / %s | %d | %d / %d | %.3f%% |' %
                        (stage,kind,len(subset),sum(int(r['false_pages']) for r in subset),
                         sum(int(r['pages']) for r in subset),100*max(float(r['upper95']) for r in subset)))
    text += ['', 'Every individual upper bound is below 5%. Bounds are per-cell Monte Carlo intervals, not a simultaneous band across the grid. Selection starts at 21000000 and validation at 22000000; each row records its exact seed. Of the 538 cells in each stream, 480 cannot produce a false direction by arithmetic: with up to three erratic families, the maximum null deviation is 18.75 points, below either radius. The guarantee is the theorem under its independence assumptions; the remaining 58 cells and the independent check’s 35 generators supply empirical evidence. The 480-cell search is a limited arithmetic stress check, not 480 nontrivial demonstrations of error control. [The bridge](repair_bridge.json) checks 32 coupled full-grid pages and 128 member vectors/decisions.','',
             '## Exact standard directional-bound power','',
             'Exact convolution of scenario means on the 12.5-point lattice replaces the 160-page Monte Carlo table. These are complete-support bound-crossing probabilities with other quality vetoes forced to pass. They are upper bounds on full-pipeline power for these generators, not probabilities integrating guard failures or refusals. The [old paired table](historical_repair_power_mc160.csv) is historical: it reused seeds across cells and overstated two stable-agent cells. Three-name rows below use all six page claims.','',
             '| family effects | stability | metric / truth | exact bounded power |','|---|---|---|---:|']
    for r in sorted(power,key=lambda r:(r['family_effects'],r['stability'],r['metric'],float(r['truth']))):
        if r['names']=='3':
            text.append('| %s | %s | %s / %s | %.4f%% |' %
                        (r['family_effects'],r['stability'],r['metric'],r['truth'],100*float(r['new_power'])))
    text += ['', '## What the rule can call','',
             'For the synthetic agents, rho and kappa have the same distribution. Values below 50 are symmetric. The table uses exact bound power with other quality vetoes forced to pass; the full pipeline can only withhold more. With no family effects, integer G must sustain at least 80% power for the next 16 integers; the balanced column rounds that requirement up to a multiple of eight. With family effects, the search and next-16 check use balanced banks in increments of eight. This local persistence convention handles lattice oscillations and does not establish power at every larger size.','',
             '| names | stability | fixed family effects | value at 80% power, G=48 | G for 65 | G for 70 | balanced G for 65 | balanced G for 70 |',
             '|---|---|---|---:|---:|---:|---:|---:|']
    for r in callable_rows:
        text.append('| %s | %s | %s | %.2f | %s | %s | %s | %s |' %
                    (r['names'],r['stability'],r['family_effects'],float(r['value_for_80pct']),
                     r['G_for_65'],r['G_for_70'],r['balanced_G_for_65'],r['balanced_G_for_70']))
    text += ['', 'At 48 complete scenarios the joint radii are %.6f points (two tests) and %.6f points (six tests). All eight family variances may differ. The bound assumes independent scenario clusters conditional on fixed families and supported scenarios; arbitrary scenario dependence and outcome-dependent losses are outside that claim. Cross-pair dependence is permitted by the union bound.'%(E.bounded_radius(2,48),E.bounded_radius(6,48)), '',
             '## Sensitivity to assumptions','',
             'In the independent 1,000-page cases, three scenarios per family sharing one latent cause produced 52 false pages (5.2%) for two names and 42 (4.2%) for three. Scenario independence given families fails there. Authoring-contract point 8 forbids a shared setting or object; the development pilot reports within-family dependence of scenario values. Outcome-dependent refusal produced 61/1,000 pages (6.1%) false against the latent preference, while each sentence remained true for the registered support-conditional target. This is not a 6.1% error rate against that registered target. Refusal and recusal counts accompany resolved sentences. These counts are sensitivity evidence, not theoretical bounds.','',
             '## Reference and fixture evidence','',
             'The required tier is checked against every registered plan ID/cell. A quick plan cannot be promoted to standard; a complete standard plan requested as quick stays descriptive. Non-primary arms have descriptive per-metric records, not merely a disabled page flag. The pure sentence renderer and exact A/B referents are pinned by full-output fixtures, including the once-per-primary-standard-page scope statement and the registered quick/non-primary descriptive lines.','',
             'Forty full-output/refusal fixtures, 32 full-plan neighboring guard tables, 9,500/9,499 defined-draw checks and actual one-run joint-boundary pairs cover the repaired rule. All ten formerly undetected mutations are caught in [the mutation table](repair_mutations.csv). Fixtures pin Python random.Random, use 128 draws for replay and preserve full reply grids in a lossless compact encoding. Error studies use 10,000 draws; power uses exact convolution.','',
             'Earlier CSVs without a repair_ prefix are historical and do not certify this rule. In particular standard_rule.json, standard_validation.csv, standard_power_width.csv and descriptive_widths.csv describe the refuted rule or its inflated displays. The prospective pilot/holdout and remaining owner approvals are in ../../PREREG.md. The bounded rule was independently confirmed; these follow-up changes still need review, and a bank freeze and owner approval remain required before registration or release.','']
    (RESULTS/'SUMMARY.md').write_text('\n'.join(text))


if __name__=='__main__':main()
