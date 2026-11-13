# Repaired versus statistics, draft 5

Only the primary standard arm can conclude rho/kappa directions. The joint rule is a Hoeffding union bound on every complete-scenario mean; marginal bootstrap intervals are plain percentiles, labelled exploratory. Quick and other arms are descriptive. No live benchmark is frozen or validated.

The previous 1.7 max-T rule is refuted. See [the failure evidence](REFUTATION.md) and [its exact reproduction](refutation_exact.csv). The replacement does not estimate a tail from six erratic observations. Its cost is substantial moderate-effect power.

| stream / generator family | cells | false pages / pages | largest cell Wilson 95% upper |
|---|---:|---:|---:|
| selection / old | 40 | 0 / 4000 | 3.699% |
| selection / verifier | 18 | 0 / 1800 | 3.699% |
| selection / near | 480 | 0 / 48000 | 3.699% |
| validation / old | 40 | 0 / 16000 | 0.951% |
| validation / verifier | 18 | 0 / 7200 | 0.951% |
| validation / near | 480 | 0 / 48000 | 3.699% |

Every individual upper bound is below 5%. Bounds are per-cell Monte Carlo intervals, not a simultaneous band across the grid. Selection starts at 21000000 and validation at 22000000; each row records its exact seed. Of the 538 cells in each stream, 480 cannot produce a false direction by arithmetic: with up to three erratic families, the maximum null deviation is 18.75 points, below either radius. The guarantee is the theorem under its independence assumptions; the remaining 58 cells and the independent check’s 35 generators supply empirical evidence. The 480-cell search is a limited arithmetic stress check, not 480 nontrivial demonstrations of error control. [The bridge](repair_bridge.json) checks 32 coupled full-grid pages and 128 member vectors/decisions.

## Exact standard directional-bound power

Exact convolution of scenario means on the 12.5-point lattice replaces the 160-page Monte Carlo table. These are complete-support bound-crossing probabilities with other quality vetoes forced to pass. They are upper bounds on full-pipeline power for these generators, not probabilities integrating guard failures or refusals. The [old paired table](historical_repair_power_mc160.csv) is historical: it reused seeds across cells and overstated two stable-agent cells. Three-name rows below use all six page claims.

| family effects | stability | metric / truth | exact bounded power |
|---|---|---|---:|
| False | noisy | kappa / 60.0 | 0.0000% |
| False | noisy | kappa / 70.0 | 4.9407% |
| False | noisy | rho / 65.0 | 0.0105% |
| False | noisy | rho / 80.0 | 99.8324% |
| False | stable | kappa / 60.0 | 0.2979% |
| False | stable | kappa / 70.0 | 23.3366% |
| False | stable | rho / 65.0 | 3.9245% |
| False | stable | rho / 80.0 | 94.2237% |
| True | noisy | kappa / 60.0 | 0.0000% |
| True | noisy | kappa / 70.0 | 4.0428% |
| True | noisy | rho / 65.0 | 0.0049% |
| True | noisy | rho / 80.0 | 99.9279% |
| True | stable | kappa / 60.0 | 0.1969% |
| True | stable | kappa / 70.0 | 21.9676% |
| True | stable | rho / 65.0 | 3.2011% |
| True | stable | rho / 80.0 | 95.5840% |

## What the rule can call

For the synthetic agents, rho and kappa have the same distribution. Values below 50 are symmetric. The table uses exact bound power with other quality vetoes forced to pass; the full pipeline can only withhold more. With no family effects, integer G must sustain at least 80% power for the next 16 integers; the balanced column rounds that requirement up to a multiple of eight. With family effects, the search and next-16 check use balanced banks in increments of eight. This local persistence convention handles lattice oscillations and does not establish power at every larger size.

| names | stability | fixed family effects | value at 80% power, G=48 | G for 65 | G for 70 | balanced G for 65 | balanced G for 70 |
|---|---|---|---:|---:|---:|---:|---:|
| 2 | stable | False | 75.04 | 141 | 79 | 144 | 80 |
| 2 | stable | True | 74.83 | 136 | 80 | 136 | 80 |
| 2 | noisy | False | 73.38 | 118 | 66 | 120 | 72 |
| 2 | noisy | True | 73.27 | 120 | 72 | 120 | 72 |
| 3 | stable | False | 77.02 | 170 | 95 | 176 | 96 |
| 3 | stable | True | 76.80 | 168 | 96 | 168 | 96 |
| 3 | noisy | False | 75.66 | 144 | 81 | 144 | 88 |
| 3 | noisy | True | 75.55 | 144 | 80 | 144 | 80 |

At 48 complete scenarios the joint radii are 21.364950 points (two tests) and 23.893511 points (six tests). All eight family variances may differ. The bound assumes independent scenario clusters conditional on fixed families and supported scenarios; arbitrary scenario dependence and outcome-dependent losses are outside that claim. Cross-pair dependence is permitted by the union bound.

## Sensitivity to assumptions

In the independent 1,000-page cases, three scenarios per family sharing one latent cause produced 52 false pages (5.2%) for two names and 42 (4.2%) for three. Scenario independence given families fails there. Authoring-contract point 8 forbids a shared setting or object; the development pilot reports within-family dependence of scenario values. Outcome-dependent refusal produced 61/1,000 pages (6.1%) false against the latent preference, while each sentence remained true for the registered support-conditional target. This is not a 6.1% error rate against that registered target. Refusal and recusal counts accompany resolved sentences. These counts are sensitivity evidence, not theoretical bounds.

## Reference and fixture evidence

The required tier is checked against every registered plan ID/cell. A quick plan cannot be promoted to standard; a complete standard plan requested as quick stays descriptive. Non-primary arms have descriptive per-metric records, not merely a disabled page flag. The pure sentence renderer and exact A/B referents are pinned by full-output fixtures, including the once-per-primary-standard-page scope statement and the registered quick/non-primary descriptive lines.

Forty full-output/refusal fixtures, 32 full-plan neighboring guard tables, 9,500/9,499 defined-draw checks and actual one-run joint-boundary pairs cover the repaired rule. All ten formerly undetected mutations are caught in [the mutation table](repair_mutations.csv). Fixtures pin Python random.Random, use 128 draws for replay and preserve full reply grids in a lossless compact encoding. Error studies use 10,000 draws; power uses exact convolution.

Earlier CSVs without a repair_ prefix are historical and do not certify this rule. In particular standard_rule.json, standard_validation.csv, standard_power_width.csv and descriptive_widths.csv describe the refuted rule or its inflated displays. The prospective pilot/holdout and remaining owner approvals are in ../../PREREG.md. The bounded rule was independently confirmed; these follow-up changes still need review, and a bank freeze and owner approval remain required before registration or release.
