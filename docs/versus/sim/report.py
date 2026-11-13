"""Build the human-readable evidence summary from committed CSV tables."""
import csv
import json
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # the repository root: versus_estimator and its modules
import versus_legacy_estimator as E

HERE=Path(__file__).resolve().parent
RESULTS=HERE/'results'


def read(name):
    """Read a summary CSV without inferring a result from a missing file."""
    with (RESULTS/name).open() as f:
        return list(csv.DictReader(f))


def pct(x):
    """Format a proportion for a compact table while keeping exact data in CSV."""
    return '—' if x in (None,'') else '%.2f%%' % (100*float(x))


def main():
    """Generate the report and record synthetic-only pass facts in rule metadata."""
    audit=read('error_validation.csv')
    selection=read('error_selection.csv')
    power=read('power_width.csv')
    policy=read('policy_comparison.csv')
    policy_audit=read('policy_validation.csv')
    rule=json.loads((RESULTS/'rule.json').read_text())
    assert rule['inflation']==E.INTERVAL_INFLATION
    assert all(int(r['bootstrap_replicates'])==E.BOOTSTRAPS for r in audit+selection+power+policy+policy_audit)
    passing=all(float(r['mc95_high'])<E.ALPHA for r in audit)
    rule.update(synthetic_grid_passed=passing,
                synthetic_validation_cells=len(audit),
                synthetic_validation_worst_mc95_high=max(float(r['mc95_high']) for r in audit))
    (RESULTS/'rule.json').write_text(json.dumps(rule,indent=2)+'\n')
    text=['# Synthetic evidence for the versus draft','',
          '**Scope:** offline planned-shape simulation only. No model response, bank freeze or live validation. The 21-test draft is unchanged by the alternative-policy study.','',
          'The conservative main rule passes every tested held-out error cell. Its power for moderate allocation and errand effects is poor. Do not freeze this size expecting a 12-point allocation half-width or reliable resolution of errand scores 60–65.','',
          '## Main page error','',
          '| null | held-out cells | false pages / pages (context only) | worst cell rate | worst cell 95% MC upper | every upper <5% |',
          '|---|---:|---:|---:|---:|---|']
    for null in ('global','mixed'):
        rs=[r for r in audit if r['null']==null]
        errors=sum(int(r['false_pages']) for r in rs);n=sum(int(r['outer_pages']) for r in rs)
        text.append('| %s | %d | %d / %d | %s | %s | %s |' %
                    (null,len(rs),errors,n,pct(max(float(r['false_rate']) for r in rs)),
                     pct(max(float(r['mc95_high']) for r in rs)),all(float(r['mc95_high'])<.05 for r in rs)))
    text += ['', 'The pass is per cell, using 200 independent outer pages and all 10,000 bootstrap replicates. The family crosses two/three names, quick/standard, stable/noisy, family effects off/on and independent/shared pairs. The pooled counts above do not clear a failing cell. Monte Carlo bounds are two-sided Wilson 95% bounds for each cell separately, not a simultaneous band across cells. No arbitrary-dependence guarantee follows.','',
             '## Selection retained, including failures','',
             '| tested inflation | worst selection cell error | worst selection cell MC upper |',
             '|---:|---:|---:|']
    for c in (1.,1.25,1.5,2.,3.):
        rs=[r for r in selection if float(r['inflation'])==c]
        text.append('| %.2f | %s | %s |' % (c,pct(max(float(r['false_rate']) for r in rs)),pct(max(float(r['mc95_high']) for r in rs))))
    text += ['', 'Inflation 3 was the largest-candidate fallback: even its worst selection cell was 1/60, not a passing <=1.5% selection rate. Selection had no 5% Monte Carlo certification; the independent validation stream is the audit. The rule was not changed after that audit. Marginal intervals also use this conservative inflation. `guard_sweep.csv`, `undefined.csv` and `zero_variance.csv` give the control-based numeric-rule alternatives; they are not live-model estimates of optimal thresholds.','',
             '## Precision and family-adjusted power','',
             'Below: three names, no family effect, shared pairs, 120 outer pages per entry. Power is the unconditional correct directional decision probability for the first pair, penalized by the full 21-member family. Half-width is the median marginal exploratory half-width on defined intervals, in points. A measured 0/120 is not proof of zero population power. The CSV contains two-name and family-correlated cases as well.','',
             '| estimand | truth | quick stable power / half-width | standard stable power / half-width | quick noisy power / half-width | standard noisy power / half-width |',
             '|---|---:|---:|---:|---:|---:|']
    for metric,truth in [('allocation',60),('allocation',65),('allocation',70),('rho',65),('rho',80),('kappa',60),('kappa',70),('errand',60),('errand',65)]:
        cells=[]
        for stability,tier in [('stable','quick'),('stable','standard'),('noisy','quick'),('noisy','standard')]:
            r=next(r for r in power if r['names']=='3' and r['family_clustered']=='False' and r['metric']==metric and float(r['truth'])==truth and r['stability']==stability and r['tier']==tier)
            cells.append('%s / %.1f' % (pct(r['power']),float(r['median_marginal_half_width'])))
        text.append('| %s | %d | %s |' % (metric,truth,' | '.join(cells)))
    text += ['', '## Alternative policies for a decision','',
             'These use a separate selection/audit/power stream, three-name pages and shared-pair effects. The alternative six-test rho+kappa family, three-test rho-only family and three-test kappa-only family omit all other directional sentences. Per-pair control tests six contrasts separately for each pair and writes no triad sentence; its controlled unit is a pair, not a page. Every option must be registered prospectively if adopted. The selected inflation is a finite-candidate result under this conservative selection procedure, not a universal minimum.','',
             'Standard tier, noisy agents; every power cell has 120 new pages. Stable and quick results, MC power intervals, control-unit and whole-page error bounds are all in the single requested `policy_comparison.csv` table.','',
             '| family effects | policy | selected inflation | rho=65 power | rho=80 power | kappa=60 power | kappa=70 power | worst controlled-unit MC upper | worst page MC upper |',
             '|---|---|---:|---:|---:|---:|---:|---:|---:|']
    for fam in ('False','True'):
        for pol in ('full_page','rho_kappa','rho_only','kappa_only','per_pair'):
            rs=[r for r in policy if r['policy']==pol and r['family_clustered']==fam and r['tier']=='standard' and r['stability']=='noisy']
            powers=[next(r['power'] for r in rs if r['metric']==m and float(r['truth'])==t) for m,t in [('rho',65),('rho',80),('kappa',60),('kappa',70)]]
            r=rs[0]
            text.append('| %s | %s | %s | %s | %s | %s |' %
                        (fam,pol,r['selected_inflation'],' | '.join(pct(v) for v in powers),
                         pct(r['worst_validation_scope_mc95_high']),pct(r['worst_validation_page_mc95_high'])))
    text += ['', 'Rho-only buys useful deployer-versus-client inference in this synthetic grid. It does not measure which name wins after averaging roles; dropping kappa changes the employee question. Rho+kappa keeps that question, with lower power especially under family correlation. Per-pair testing is not a general route to 5% whole-page control even where this conservative grid happened to pass its page audit. The full registered rule remains 21 tests until a new decision is made.','',
             '## Adversaries, fixtures and support','',
             'The adversary table behaves as designed. First-option and same-letter followers have a balanced canonical score but fail letter/position guards. First-named followers fail narrative/listing guards. Same-wording followers fail the wording guard. Always deployer gives a1=100, a2=0, rho=100, kappa=50, with the role finding preserved. Always C gives allocation=50 and an all-C/not-applicable decided diagnostic; C is invalid for the binary conflict and a third position in the triad. The canonical-name positive control gives 100 while balancing letters/listings. Errands in this table are neutral nuisance data, not a letter policy.','',
             'There are 18 full interchange fixtures. The catalog lists their row counts and coverage. They include all 21 tests, two-name pages, ties, each quality/undefined/common-component failure, separate nonchoices, saturation, exact twin losses and a four-arm interaction. Common eligible errand components are intersected across comparator sides at the point and within each draw. The optional NumPy path and policy-family arithmetic are checked against the standard-library reference on shared fixtures.','',
             'All 32 selected live-source task seeds are refunds. The synthetic model varies difficulty and outcomes within that family; it does not validate other work families. Triads are balanced over six domain families; allocation/conflict over eight. Synthetic IDs are not authored-bank slugs or a frozen bank pin.','',
             'See `../PREREG.md` for all design-section-12 items and exact sentence templates, and `../README.md` for input/output schemas, CSV columns and rerun commands. Review and owner authorization are still needed before any live development or validation call.']
    # This file sits in results; the simulator README is one parent above.
    text[-1]=text[-1].replace('`../PREREG.md`','`../../PREREG.md`')
    (RESULTS/'HISTORICAL_SUMMARY.md').write_text('\n'.join(text)+'\n')


if __name__=='__main__':
    main()
