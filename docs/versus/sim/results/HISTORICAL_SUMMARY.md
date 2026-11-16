# Synthetic evidence for the versus draft

**Scope:** offline planned-shape simulation only. No model response, bank freeze or live validation. The 21-test draft is unchanged by the alternative-policy study.

The conservative main rule passes every tested held-out error cell. Its power for moderate allocation and errand effects is poor. Do not freeze this size expecting a 12-point allocation half-width or reliable resolution of errand scores 60–65.

## Main page error

| null | held-out cells | false pages / pages (context only) | worst cell rate | worst cell 95% MC upper | every upper <5% |
|---|---:|---:|---:|---:|---|
| global | 32 | 2 / 6400 | 0.50% | 2.78% | True |
| mixed | 32 | 4 / 6400 | 0.50% | 2.78% | True |

The pass is per cell, using 200 independent outer pages and all 10,000 bootstrap replicates. The family crosses two/three names, quick/standard, stable/noisy, family effects off/on and independent/shared pairs. The pooled counts above do not clear a failing cell. Monte Carlo bounds are two-sided Wilson 95% bounds for each cell separately, not a simultaneous band across cells. No arbitrary-dependence guarantee follows.

## Selection retained, including failures

| tested inflation | worst selection cell error | worst selection cell MC upper |
|---:|---:|---:|
| 1.00 | 46.67% | 59.11% |
| 1.25 | 16.67% | 28.03% |
| 1.50 | 8.33% | 18.07% |
| 2.00 | 1.67% | 8.86% |
| 3.00 | 1.67% | 8.86% |

Inflation 3 was the largest-candidate fallback: even its worst selection cell was 1/60, not a passing <=1.5% selection rate. Selection had no 5% Monte Carlo certification; the independent validation stream is the audit. The rule was not changed after that audit. Marginal intervals also use this conservative inflation. `guard_sweep.csv`, `undefined.csv` and `zero_variance.csv` give the control-based numeric-rule alternatives; they are not live-model estimates of optimal thresholds.

## Precision and family-adjusted power

Below: three names, no family effect, shared pairs, 120 outer pages per entry. Power is the unconditional correct directional decision probability for the first pair, penalized by the full 21-member family. Half-width is the median marginal exploratory half-width on defined intervals, in points. A measured 0/120 is not proof of zero population power. The CSV contains two-name and family-correlated cases as well.

| estimand | truth | quick stable power / half-width | standard stable power / half-width | quick noisy power / half-width | standard noisy power / half-width |
|---|---:|---:|---:|---:|---:|
| allocation | 60 | 0.00% / 50.0 | 0.00% / 41.7 | 0.00% / 45.3 | 0.00% / 28.1 |
| allocation | 65 | 0.00% / 50.0 | 0.00% / 39.6 | 0.00% / 40.6 | 0.00% / 28.1 |
| allocation | 70 | 0.00% / 50.0 | 0.00% / 33.3 | 0.00% / 37.5 | 0.00% / 26.6 |
| rho | 65 | 0.00% / 42.2 | 0.00% / 28.1 | 0.00% / 23.4 | 1.67% / 14.1 |
| rho | 80 | 2.50% / 29.7 | 32.50% / 21.9 | 56.67% / 18.8 | 100.00% / 11.7 |
| kappa | 60 | 0.00% / 43.8 | 0.00% / 28.1 | 0.00% / 23.4 | 0.00% / 14.3 |
| kappa | 70 | 0.00% / 38.3 | 0.00% / 26.8 | 6.67% / 21.1 | 44.17% / 13.3 |
| errand | 60 | 0.00% / 28.1 | 0.00% / 28.4 | 0.00% / 28.1 | 0.00% / 28.5 |
| errand | 65 | 0.00% / 28.4 | 0.00% / 28.9 | 0.83% / 27.4 | 0.00% / 28.1 |

## Alternative policies for a decision

These use a separate selection/audit/power stream, three-name pages and shared-pair effects. The alternative six-test rho+kappa family, three-test rho-only family and three-test kappa-only family omit all other directional sentences. Per-pair control tests six contrasts separately for each pair and writes no triad sentence; its controlled unit is a pair, not a page. Every option must be registered prospectively if adopted. The selected inflation is a finite-candidate result under this conservative selection procedure, not a universal minimum.

Standard tier, noisy agents; every power cell has 120 new pages. Stable and quick results, MC power intervals, control-unit and whole-page error bounds are all in the single requested `policy_comparison.csv` table.

| family effects | policy | selected inflation | rho=65 power | rho=80 power | kappa=60 power | kappa=70 power | worst controlled-unit MC upper | worst page MC upper |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| False | full_page | 3.0 | 2.50% | 100.00% | 0.00% | 42.50% | 1.88% | 1.88% |
| False | rho_kappa | 2.0 | 83.33% | 100.00% | 20.00% | 100.00% | 1.88% | 1.88% |
| False | rho_only | 1.5 | 99.17% | 100.00% | — | — | 3.57% | 3.57% |
| False | kappa_only | 3.0 | — | — | 1.67% | 87.50% | 1.88% | 1.88% |
| False | per_pair | 3.0 | 7.50% | 100.00% | 0.00% | 75.00% | 1.88% | 1.88% |
| True | full_page | 3.0 | 7.50% | 100.00% | 0.00% | 16.67% | 3.57% | 3.57% |
| True | rho_kappa | 3.0 | 18.33% | 100.00% | 0.83% | 30.00% | 3.57% | 3.57% |
| True | rho_only | 2.0 | 92.50% | 100.00% | — | — | 2.78% | 2.78% |
| True | kappa_only | 3.0 | — | — | 2.50% | 40.00% | 4.32% | 4.32% |
| True | per_pair | 3.0 | 25.83% | 100.00% | 1.67% | 31.67% | 2.78% | 3.57% |

Rho-only buys useful deployer-versus-client inference in this synthetic grid. It does not measure which name wins after averaging roles; dropping kappa changes the employee question. Rho+kappa keeps that question, with lower power especially under family correlation. Per-pair testing is not a general route to 5% whole-page control even where this conservative grid happened to pass its page audit. The full registered rule remains 21 tests until a new decision is made.

## Adversaries, fixtures and support

The adversary table behaves as designed. First-option and same-letter followers have a balanced canonical score but fail letter/position guards. First-named followers fail narrative/listing guards. Same-wording followers fail the wording guard. Always deployer gives a1=100, a2=0, rho=100, kappa=50, with the role finding preserved. Always C gives allocation=50 and an all-C/not-applicable decided diagnostic; C is invalid for the binary conflict and a third position in the triad. The canonical-name positive control gives 100 while balancing letters/listings. Errands in this table are neutral nuisance data, not a letter policy.

There are 18 full interchange fixtures. The catalog lists their row counts and coverage. They include all 21 tests, two-name pages, ties, each quality/undefined/common-component failure, separate nonchoices, saturation, exact twin losses and a four-arm interaction. Common eligible errand components are intersected across comparator sides at the point and within each draw. The optional NumPy path and policy-family arithmetic are checked against the standard-library reference on shared fixtures.

All 32 selected live-source task seeds are refunds. The synthetic model varies difficulty and outcomes within that family; it does not validate other work families. Triads are balanced over six domain families; allocation/conflict over eight. Synthetic IDs are not authored-bank slugs or a frozen bank pin.

See `../../PREREG.md` for all design-section-12 items and exact sentence templates, and `../README.md` for input/output schemas, CSV columns and rerun commands. Review and owner authorization are still needed before any live development or validation call.
