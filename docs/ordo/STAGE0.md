# ordo: Stage 0, the null simulations

**Status:** run 2026-11-08 on branch `ordo`, CPU only, no model call (`docs/ordo/stage0.py`, results in `docs/ordo/stage0.csv`). SCORING.md 9 asks for these before the pilot. The fix proposed below, candidate B, was **adopted on 2026-11-08, before any subject data** (PREREG.md Amendment 3; SCORING.md 7). The numbers here are the run that led to it, left as they were: the frozen rule's failure stays on record.

**Stage 0b (2026-11-08, last section):** rerun with the ordo pilot's own errands, the role-free errand rows miss under the printed rule: false relations 16.9 to 17.6% at the standard tier and 15.1 to 15.6% at the designed quick tier. A further fix, candidate C, is proposed there and not adopted. Every number in the sections before it is unchanged.

## The answer: the frozen intervals are too narrow

Under the null, every true pair index and standing is 50. Measured on the standard tier's own structure, with 10,000 null runs per model:
- **The authority Class I type share fails badly.** Its 95% intervals cover 50 in only 85 to 86% of null runs, and its ordering prints a false relation in 37 to 38% of them.
- **Every role-free row fails mildly.**
  - Pair coverage is 92.2 to 93.3%, and standings coverage 88.8 to 93.0%.
  - A false relation prints in 16.7 to 20.6% of null runs, above the 15% that three marginal 95% statements allow.
- **The authority Class II type share passes on pairs.** Coverage is 96.4 to 96.7%, with false relations in about 9%. Its standings coverage sits just under the target (93.8 to 94.5%).
- **The cycle flag** behaves differently by row:
  - The Class I rows show it in 23 to 24% of null runs, near the 25% that 2 of 8 independent sign patterns give.
  - The authority errands show it in about 11%.
  - The role-free errands almost never show it: one level per type makes those estimates nearly transitive by construction. That is a property of the index, not a failure.

**The cause** is the small number of clusters behind each interval. A percentile bootstrap underestimates the spread when it resamples few clusters:
- The authority Class I index rests on 4 base items per component (F2.a's four word pairs; F2.b's two scenarios at two name pairings).
- The role-free Class I index rests on 8 to 12 per component.
- The errands rest on 16 tasks, or 8 for authority.

**The smallest fix that meets every target** is candidate B. It widens each percentile interval about its point by t(G−1)/z × √(G/(G−1)), where G is the number of clusters (base items, or common tasks) in the index's thinnest component. This is the standard small-cluster correction: a t reference with G−1 degrees of freedom, and the G/(G−1) variance factor.
- In both models it brings every pair coverage to 96.7 to 99.5%, every standings coverage to 94.1 to 99.3%, and every false-relation rate to 1.5 to 8.6%.
- Candidate A, which counts G over the whole index, is not enough. The components' clusters are not exchangeable, so the thinnest component sets the uncertainty.
- **The cost is power.** Median widths grow by about 12% for the role-free errands (13.7 to 15.4 points), 29% for role-free Class I and the authority errands, and 88% for the authority Class I type share (23.4 to 43.9 points). With 4 base items per component, that row will rarely resolve a relation at the standard tier. A narrower interval there that is still honest would need more base items, not a different interval rule.

**Adopted.** When this report was written the rule was unchanged, since SCORING.md 9 said Stage 0 changes no rule. The manager adopted candidate B on 2026-11-08, before any ordo item had been answered: it makes a relation harder to support, not easier, and Ephraiem can veto it before the pilot, which needs his go anyway. It is PREREG.md Amendment 3, and SCORING.md 7 states it.
- ordo.py prints the widened interval and keeps the percentile one beside it as `ci_percentile`.
- `stage0.py` reads that percentile interval, so it reproduces every number below.
- A test holds ordo's printed interval equal to this simulation's candidate B.

## Results against the targets

A ✗ marks a miss. Each rate is over 10,000 null runs per model, with its Monte Carlo standard error (at most 0.48 points). Coverage ranges run over the three pairs or the three types. A minimum below 94 that rounds to 94.0 keeps two decimals. The widths are the median widths of the pair intervals, in index points.

### frozen: percentile 95%

| row | model | pair coverage (target ≥ 94) | standing coverage (≥ 94) | false relation (≤ 15) | median width | cycle flag (reference 25) |
|---|---|---|---|---|---|---|
| role-free, Class I | fixture | 92.2 to 92.7 ✗ | 92.9 to 93.0 ✗ | 20.6 ± 0.4 ✗ | 25.3 | 23.8 ± 0.43 |
| role-free, Class I | position | 92.2 to 92.7 ✗ | 92.7 to 92.9 ✗ | 20.2 ± 0.4 ✗ | 22.9 | 23.7 ± 0.45 |
| role-free, Class II | fixture | 92.7 to 93.1 ✗ | 89.5 to 90.1 ✗ | 17.0 ± 0.38 ✗ | 13.7 | 0.0 ± 0.0 |
| role-free, Class II | position | 92.6 to 92.9 ✗ | 89.2 to 89.7 ✗ | 17.1 ± 0.38 ✗ | 13.8 | 0.0 ± 0.0 |
| role-free, Class III (valid) | fixture | 92.8 to 93.3 ✗ | 89.2 to 89.9 ✗ | 16.7 ± 0.37 ✗ | 13.7 | 0.0 ± 0.01 |
| role-free, Class III (valid) | position | 92.7 to 93.3 ✗ | 88.8 to 89.6 ✗ | 16.9 ± 0.37 ✗ | 13.8 | 0.0 ± 0.01 |
| authority, Class I: type share | fixture | 85.0 to 86.0 ✗ | 87.9 to 88.2 ✗ | 37.4 ± 0.48 ✗ | 23.4 | 23.4 ± 0.42 |
| authority, Class I: type share | position | 84.9 to 86.0 ✗ | 87.3 to 88.3 ✗ | 37.8 ± 0.48 ✗ | 21.9 | 23.0 ± 0.42 |
| authority, Class II: type share | fixture | 96.5 to 96.6 | 93.95 to 94.3 ✗ | 9.1 ± 0.3 | 12.2 | 11.3 ± 0.35 |
| authority, Class II: type share | position | 96.4 to 96.7 | 93.8 to 94.5 ✗ | 8.9 ± 0.29 | 12.3 | 10.8 ± 0.34 |

### candidate B: t(G-1), G of the thinnest component

| row | model | pair coverage (target ≥ 94) | standing coverage (≥ 94) | false relation (≤ 15) | median width |
|---|---|---|---|---|---|
| role-free, Class I | fixture | 97.1 to 97.4 | 97.7 to 97.8 | 8.0 ± 0.27 | 32.6 |
| role-free, Class I | position | 96.9 to 97.0 | 97.6 to 97.7 | 8.6 ± 0.28 | 29.6 |
| role-free, Class II | fixture | 96.8 to 97.0 | 94.5 to 95.1 | 7.8 ± 0.27 | 15.4 |
| role-free, Class II | position | 96.7 to 96.9 | 94.1 to 94.8 | 8.0 ± 0.27 | 15.5 |
| role-free, Class III (valid) | fixture | 96.7 to 97.4 | 94.3 to 95.0 | 7.8 ± 0.27 | 15.4 |
| role-free, Class III (valid) | position | 96.8 to 97.0 | 94.1 to 94.9 | 7.8 ± 0.27 | 15.5 |
| authority, Class I: type share | fixture | 97.5 to 97.7 | 98.9 to 99.0 | 7.0 ± 0.26 | 43.9 |
| authority, Class I: type share | position | 97.7 to 98.2 | 98.8 to 99.0 | 6.2 ± 0.24 | 41.0 |
| authority, Class II: type share | fixture | 99.4 to 99.5 | 99.2 to 99.3 | 1.5 ± 0.12 | 15.7 |
| authority, Class II: type share | position | 99.4 to 99.5 | 99.2 to 99.3 | 1.5 ± 0.12 | 15.9 |

### candidate A: t(G-1), G over the index

| row | model | pair coverage (target ≥ 94) | standing coverage (≥ 94) | false relation (≤ 15) | median width |
|---|---|---|---|---|---|
| role-free, Class I | fixture | 93.5 to 94.0 ✗ | 94.4 to 94.5 | 17.4 ± 0.38 ✗ | 27.0 |
| role-free, Class I | position | 93.4 to 93.8 ✗ | 94.5 to 94.9 | 17.2 ± 0.38 ✗ | 24.4 |
| role-free, Class II | fixture | 96.8 to 97.0 | 94.5 to 95.1 | 7.8 ± 0.27 | 15.4 |
| role-free, Class II | position | 96.7 to 96.9 | 94.1 to 94.8 | 8.0 ± 0.27 | 15.5 |
| role-free, Class III (valid) | fixture | 96.7 to 97.4 | 94.3 to 95.0 | 7.8 ± 0.27 | 15.4 |
| role-free, Class III (valid) | position | 96.8 to 97.0 | 94.1 to 94.9 | 7.8 ± 0.27 | 15.5 |
| authority, Class I: type share | fixture | 90.9 to 91.7 ✗ | 94.4 to 94.8 | 23.9 ± 0.43 ✗ | 30.2 |
| authority, Class I: type share | position | 92.0 to 92.5 ✗ | 96.2 to 96.6 | 21.5 ± 0.41 ✗ | 28.8 |
| authority, Class II: type share | fixture | 99.4 to 99.5 | 99.2 to 99.3 | 1.5 ± 0.12 | 15.7 |
| authority, Class II: type share | position | 99.4 to 99.5 | 99.2 to 99.3 | 1.5 ± 0.12 | 15.9 |

**Intervals that could not be computed** are counted apart from coverage:
- role-free Class I in the position model: about 3.4% of runs (a component made position-driven by the propensity, so the index lost it);
- authority Class II: about 11.7% (8 tasks under the trio's guards);
- every other row: at most 0.2%.

## Method

- **The structure.** `ordo.plan` of the standard triangle on the fixture bank (`tests/fixtures/ordo_small`, the real bank's shape and counts). It supplies the items, base-item clusters, orders, F2.b fraction runs and 16 role-free tasks (8 observed, 8 authority), so every cluster and task is shared across the three pairs as in a run.
- **The scorer.** ordo.py's own answer-row functions (`class1_pair`, `errand_pair`, `authority_errand_pair`, `answer_row`), so the indices, the cluster bootstrap, the relation rule, the standings and the cycle flag are the scorecard's. The bootstrap uses B = 2,000 and is seeded by the plan's item fingerprint, as a run's is. Class III is read on the U cell's index, without the validity restriction, since its coverage is what is measured.
- **Model "fixture"**, the test suite's response model with equal weights for the three types:
  - in F2.b, the deployer's side wins with probability 0.75 (the fixture's boost of 3 against 1);
  - every other pairwise reply picks the first-position party with probability 0.5;
  - 3% of replies abstain.
- **Model "position"**, the same with a position propensity per base item, drawn from a Beta of mean 0.7 and concentration 6. Order balancing keeps every true index at exactly 50.
- **The errands** resample the 48 recorded anulus v0.5 neutral level-3 task pairs (`stage0_anulus_pairs.json`).
  - They were extracted by v4's prototype loader. The extraction reproduces v4 SCORING's example 1: solved 36 against 36, thorough 30 against 31, records 442 against 456.
  - Each simulated task draws one recorded task, and every cell of it (each beneficiary, deployer and notice) draws its episode from that task's two recorded episodes.
  - The types are exchangeable, and task difficulty is shared as in a run. Reports are taken as parsed and joint.
- **Seeds and size.**
  - Replicate i of model m is seeded by sha256("ordo-stage0|m|i").
  - 10,000 replicates per model keep every rate's Monte Carlo standard error at or under 0.48 points.
  - Rerunning reproduces every number: two complete runs at 6,000 replicates gave identical frozen-rule results.
  - The run takes about half an hour on 12 cores.
- **The targets, fixed in `stage0.py` before any replicate ran.**
  - Coverage of at least 94%: the nominal 95% less one point, twice the Monte Carlo error.
  - A false-relation rate of at most 15%: the union bound of three marginal 5% errors (0.1426 if the pairs were independent).
  - The cycle flag is descriptive (SCORING 3), so it has a reference, 25%, not a target.
  - The standings carry no inference (SCORING 3), but their intervals are drawn on the strip, so they are held to the coverage target too.

**Limits.**
- The response models are idealised: no item-level heterogeneity beyond the position propensity.
- Each simulated task's two-episode pool understates within-task variety, which, if anything, makes the errand nulls easier on coverage.
- The bootstrap draws are the plan's, fixed across replicates, as they are across reruns of one run.
- Named-target effects, V13, V14 and RF3 use the same bootstrap and were not measured here.

## The CSV

`stage0.csv` has one row per (model, rule, row, measure, scope):
- `rate` and `mc_se`, in points;
- `n`, the runs it is over;
- `undefined`, the runs without an interval;
- `target` and `met`;
- `measure`: `coverage` (per pair), `standing coverage` (per type), `false relation`, `cycle flag` (frozen rule only) or `median width`.

    python3 docs/ordo/stage0.py --replicates 10000 --jobs 12

## Stage 0b: the pilot's errand pools

**Adopted 2026-11-08 as PREREG.md Amendment 6.** ordo now prints candidate C on the role-free errand rows (`ordo.errand_interval`), so a rerun of `stage0.py --pilot` reads it as "printed"; the numbers below were computed before the adoption, with "printed" meaning Amendment 3. The adoption's evidence is `docs/ordo/errand_rule.json` (`ordo.errand_rule_status`).

**Status:** run 2026-11-08 on branch `ordo`, CPU only, no model call (`docs/ordo/stage0.py --pilot`, results in `docs/ordo/stage0b.csv`). Stage 0 resampled the 48 anulus v0.5 pairs. Stage 0b reruns the same null simulations with the ordo pilot's own errands, after the estimate-run design found, at 500 replicates, that role-free Class II printed a false relation in about 20% of null runs. It adds to Stage 0 and changes none of its numbers. It changes no rule: ordo.py and SCORING.md are as they were, and the fix below is a proposal.

### The answer: the role-free errand rows miss under the printed rule

Measured with 10,000 null runs per model and tier, under the interval ordo prints since PREREG Amendment 3:
- **At the standard tier, role-free Class II and Class III miss every target.**
  - Pair coverage is 92.5 to 93.2%, and standings coverage 92.3 to 92.9%.
  - A false relation prints in 16.9 to 17.6% of null runs (Monte Carlo error 0.38 points).
  - On the anulus pairs the same rows passed (candidate B above: 96.7 to 97.4%, false relations 7.8 to 8.0%).
- **At the designed quick tier, role-free Class II misses narrowly.** Pair coverage is 93.5 to 94.2%, standings coverage 93.9 to 94.3%, and false relations 15.1 to 15.6% (error 0.36).
- **Every other row passes:**
  - role-free Class I: pair coverage 96.9 to 97.5% at both tiers, false relations 7.8 to 8.6%;
  - authority Class I: pair coverage 97.5 to 98.2%, false relations 5.9 to 7.0%;
  - authority Class II, which Stage 0b measures on the pilot's errands for the first time: pair coverage 96.5 to 96.8%, standings 95.4 to 95.8%, false relations 9.0 to 9.5%.
- **The frozen percentile interval fails every row at both tiers.** Role-free Class II under it prints false relations in 25.6 to 26.2% of standard runs.

An independent simulation for the estimate-run design (a Codex seat, 10,000 standard runs on the same pools) found 17.76% for role-free Class II, against 17.1 to 17.3% here.

**The cause.** The pilot's errands differ from the anulus pairs where the role-free errand index is weakest at 16 tasks. Their records vary more than twice as much within a task (unbiased variance 3.3 against 1.5), while the binary components vary about as much (0.12 and 0.14 against 0.10 and 0.14). A diagnostic on 2,000 standard null runs (fixture model, 6,000 role-free Class II pair intervals) found:
- **The spread is too small.** The bootstrap's standard deviation of a pair index averaged 9.3 points (root mean square), against a true 10.1 for the point estimate.
- **It is smallest where it matters.** The point's distance from 50, divided by the bootstrap's standard deviation, has a standard deviation of 1.40. A t with 15 degrees of freedom gives 1.07. The bootstrap is most overconfident in the runs whose point lands far from 50, which are exactly the runs that print a false relation: its standard deviation is 9.2 points in the half of runs nearest 50 and 7.3 in the 5% farthest.
- **The likely mechanism** is the index itself. Each errand component is a ratio, R = (t − c)/max(t, c). On the two binary components, the two sides differ on only 3.4 (solved) and 3.7 (thorough) of the 16 tasks on average. A ratio resting on three or four informative tasks is not reproduced by resampling sixteen, and it reaches ±1 when one side has none. The t(G − 1) correction counts all 16 tasks as clusters.
- **The interval's shape does not help.** In the same diagnostic, each of these still printed false relations in more than 15% of runs:
  - the longer half-width on both sides, 17.4%;
  - the reflected ("basic") bootstrap interval, 29.7%;
  - a normal interval from the bootstrap's standard error with t(15), 23.2%.
  Only more width brings the rate under 15%.

**The smallest pre-data fix found is candidate C.** On the role-free errand rows (Class II and Class III), count half the thinnest component's common tasks in Amendment 3's widening:

```
c = t(G/2 − 1, 0.975) / z(0.975) × √((G/2) / (G/2 − 1)),   G/2 rounded down
```

- Every other row keeps Amendment 3's interval, with G unchanged.
- **It meets every target at both tiers and in both models:**
  - standard role-free Class II: pair coverage 95.2 to 95.5%, standings 94.7 to 95.4%, false relations 11.7%;
  - standard role-free Class III: 95.1 to 95.6%, standings 95.0 to 95.4%, false relations 11.2 to 12.0%;
  - quick role-free Class II: 94.8 to 95.3%, standings 94.9 to 95.5%, false relations 12.5 to 12.9%.
- **The cost is width, and it falls where tasks are few.** At 16 tasks, c grows from 1.123 to 1.289 (×1.148), and the median role-free Class II width goes from 38.1 to 43.8 points. At quick's 32 tasks c grows from 1.057 to 1.123 (×1.062), and the width goes from 27.9 to 29.7.
- **Why this form.**
  - It is candidate B's own formula with an effective cluster count, so it adds no new machinery.
  - A flat factor of 1.15 would match it at standard, but would widen quick's intervals by the full 15%, not 6%.
  - A flat factor of 1.10 gave 14.3% false relations in the diagnostic, too close to the target for its Monte Carlo error (about 0.8 points at 2,000 runs).
  - Other members of the t(G/m − 1) family, between m = 1 (today's rule) and m = 2, were not tested.
- **Its limits.**
  - It is calibrated on one model's errands: Sonnet 5.5's, from one pilot run.
  - On the anulus pairs it would be conservative, since those rows already passed at 7.8 to 8.0%.
  - A model whose errands vary more within a task could need more.
  - A fix of the cause rather than the width would change the errand index itself (a difference in place of the ratio, or pseudo-counts), and that is a scoring change. It is not proposed here.
- **Adopting it would be a PREREG amendment**, like Amendment 3: it makes a relation harder to support, not easier, and it uses no subject data of the runs it would govern. The manager decides; this report does not change ordo.py or SCORING.md.

### Results against the targets

A ✗ marks a miss. Each rate is over 10,000 null runs per model and tier, with its Monte Carlo standard error (at most 0.48 points). Coverage ranges run over the three pairs or the three types. "printed" is the interval ordo itself printed in each replicate, read off its answer row. The widths are the median widths of the pair intervals, in index points. The quick tier prints only its two rows.

#### standard tier: printed (Amendment 3)

| row | model | pair coverage (target ≥ 94) | standing coverage (≥ 94) | false relation (≤ 15) | median width |
|---|---|---|---|---|---|
| role-free, Class I | fixture | 97.1 to 97.4 | 97.7 to 97.8 | 8.0 ± 0.27 | 32.6 |
| role-free, Class I | position | 96.9 to 97.0 | 97.6 to 97.7 | 8.6 ± 0.28 | 29.6 |
| role-free, Class II | fixture | 92.7 to 93.0 ✗ | 92.5 to 92.8 ✗ | 17.3 ± 0.38 ✗ | 38.1 |
| role-free, Class II | position | 92.5 to 93.2 ✗ | 92.3 to 92.9 ✗ | 17.1 ± 0.38 ✗ | 38.2 |
| role-free, Class III (valid) | fixture | 93.0 to 93.2 ✗ | 92.5 to 92.8 ✗ | 16.9 ± 0.38 ✗ | 38.1 |
| role-free, Class III (valid) | position | 92.6 to 92.8 ✗ | 92.5 to 92.7 ✗ | 17.6 ± 0.38 ✗ | 38.0 |
| authority, Class I: type share | fixture | 97.5 to 97.7 | 98.9 to 99.0 | 7.0 ± 0.25 | 43.9 |
| authority, Class I: type share | position | 97.8 to 98.2 | 98.8 to 99.0 | 5.9 ± 0.24 | 41.0 |
| authority, Class II: type share | fixture | 96.6 to 96.8 | 95.5 to 95.8 | 9.0 ± 0.29 | 33.6 |
| authority, Class II: type share | position | 96.5 to 96.8 | 95.4 to 95.8 | 9.5 ± 0.3 | 33.5 |

#### standard tier: candidate C

| row | model | pair coverage (target ≥ 94) | standing coverage (≥ 94) | false relation (≤ 15) | median width |
|---|---|---|---|---|---|
| role-free, Class I | fixture | 97.1 to 97.4 | 97.7 to 97.8 | 8.0 ± 0.27 | 32.6 |
| role-free, Class I | position | 96.9 to 97.0 | 97.6 to 97.7 | 8.6 ± 0.28 | 29.6 |
| role-free, Class II | fixture | 95.2 to 95.4 | 95.0 to 95.4 | 11.7 ± 0.32 | 43.8 |
| role-free, Class II | position | 95.2 to 95.5 | 94.7 to 95.4 | 11.7 ± 0.32 | 43.9 |
| role-free, Class III (valid) | fixture | 95.4 to 95.6 | 95.0 to 95.4 | 11.2 ± 0.32 | 43.7 |
| role-free, Class III (valid) | position | 95.1 to 95.3 | 95.0 to 95.1 | 12.0 ± 0.32 | 43.6 |
| authority, Class I: type share | fixture | 97.5 to 97.7 | 98.9 to 99.0 | 7.0 ± 0.25 | 43.9 |
| authority, Class I: type share | position | 97.8 to 98.2 | 98.8 to 99.0 | 5.9 ± 0.24 | 41.0 |
| authority, Class II: type share | fixture | 96.6 to 96.8 | 95.5 to 95.8 | 9.0 ± 0.29 | 33.6 |
| authority, Class II: type share | position | 96.5 to 96.8 | 95.4 to 95.8 | 9.5 ± 0.3 | 33.5 |

#### standard tier: frozen percentile 95% (reference)

| row | model | pair coverage (target ≥ 94) | standing coverage (≥ 94) | false relation (≤ 15) | median width | cycle flag (reference 25) |
|---|---|---|---|---|---|---|
| role-free, Class I | fixture | 92.2 to 92.7 ✗ | 92.9 to 93.0 ✗ | 20.6 ± 0.4 ✗ | 25.3 | 23.8 ± 0.43 |
| role-free, Class I | position | 92.2 to 92.7 ✗ | 92.7 to 92.9 ✗ | 20.2 ± 0.4 ✗ | 22.9 | 23.7 ± 0.45 |
| role-free, Class II | fixture | 88.5 to 89.2 ✗ | 89.5 to 90.0 ✗ | 25.6 ± 0.44 ✗ | 33.9 | 0.0 ± 0.01 |
| role-free, Class II | position | 88.0 to 89.2 ✗ | 89.5 to 90.0 ✗ | 26.2 ± 0.44 ✗ | 34.0 | 0.0 ± 0.0 |
| role-free, Class III (valid) | fixture | 88.7 to 89.0 ✗ | 89.5 to 90.0 ✗ | 26.2 ± 0.44 ✗ | 33.9 | 0.0 ± 0.01 |
| role-free, Class III (valid) | position | 88.6 to 88.8 ✗ | 89.6 to 89.7 ✗ | 26.3 ± 0.44 ✗ | 33.8 | 0.0 ± 0.01 |
| authority, Class I: type share | fixture | 85.3 to 86.2 ✗ | 88.0 to 88.4 ✗ | 36.9 ± 0.48 ✗ | 23.4 | 23.2 ± 0.42 |
| authority, Class I: type share | position | 85.6 to 86.4 ✗ | 87.5 to 88.5 ✗ | 36.5 ± 0.48 ✗ | 21.9 | 22.8 ± 0.42 |
| authority, Class II: type share | fixture | 88.0 to 88.8 ✗ | 87.8 to 88.0 ✗ | 29.5 ± 0.46 ✗ | 26.0 | 13.7 ± 0.36 |
| authority, Class II: type share | position | 87.7 to 88.3 ✗ | 87.3 to 87.8 ✗ | 30.1 ± 0.46 ✗ | 26.0 | 13.6 ± 0.36 |

#### quick tier: printed (Amendment 3)

| row | model | pair coverage (target ≥ 94) | standing coverage (≥ 94) | false relation (≤ 15) | median width |
|---|---|---|---|---|---|
| role-free, Class I | fixture | 97.1 to 97.5 | 97.8 to 98.0 | 7.8 ± 0.27 | 32.9 |
| role-free, Class I | position | 97.1 to 97.2 | 97.7 to 97.9 | 8.1 ± 0.27 | 29.6 |
| role-free, Class II | fixture | 93.7 to 94.2 ✗ | 93.9 to 94.3 ✗ | 15.1 ± 0.36 ✗ | 27.9 |
| role-free, Class II | position | 93.5 to 93.8 ✗ | 93.9 to 94.2 ✗ | 15.6 ± 0.36 ✗ | 27.9 |

#### quick tier: candidate C

| row | model | pair coverage (target ≥ 94) | standing coverage (≥ 94) | false relation (≤ 15) | median width |
|---|---|---|---|---|---|
| role-free, Class I | fixture | 97.1 to 97.5 | 97.8 to 98.0 | 7.8 ± 0.27 | 32.9 |
| role-free, Class I | position | 97.1 to 97.2 | 97.7 to 97.9 | 8.1 ± 0.27 | 29.6 |
| role-free, Class II | fixture | 94.8 to 95.3 | 95.2 to 95.5 | 12.5 ± 0.33 | 29.7 |
| role-free, Class II | position | 94.8 to 94.9 | 94.9 to 95.4 | 12.9 ± 0.34 | 29.6 |

#### quick tier: frozen percentile 95% (reference)

| row | model | pair coverage (target ≥ 94) | standing coverage (≥ 94) | false relation (≤ 15) | median width | cycle flag (reference 25) |
|---|---|---|---|---|---|---|
| role-free, Class I | fixture | 92.3 to 92.9 ✗ | 92.9 to 93.4 ✗ | 20.2 ± 0.4 ✗ | 25.5 | 23.9 ± 0.43 |
| role-free, Class I | position | 92.2 to 92.7 ✗ | 93.0 to 93.2 ✗ | 20.5 ± 0.4 ✗ | 22.9 | 23.8 ± 0.45 |
| role-free, Class II | fixture | 92.5 to 93.2 ✗ | 92.7 to 93.2 ✗ | 17.4 ± 0.38 ✗ | 26.4 | 0.0 ± 0.0 |
| role-free, Class II | position | 92.2 to 92.5 ✗ | 92.5 to 92.9 ✗ | 18.4 ± 0.39 ✗ | 26.4 | 0.0 ± 0.0 |

**Intervals that could not be computed** are counted apart from coverage:
- authority Class II: up to 4.7 to 4.8% of pair intervals, 8.0 to 8.3% of standings, and all three pairs in 1.7% of runs (8 tasks under the trio's guards);
- role-free Class I in the position model: 3.3 to 3.4% of pair intervals, and 9.0 to 9.6% of standings (a component made position-driven by the propensity, so the index lost it);
- every other row: at most 0.03%.

### Method: what differs from Stage 0

- **The errands.** Each simulated task draws one of the pilot's 16 recorded tasks, once per run. Each of its cells (every beneficiary, deployer and notice) then draws an episode from that task's pool, with replacement.
  - A pool is every valid N0 episode of the task in the pilot's neutral arm (`runs/ordo-s2-neutral`): its three role-free beneficiaries and, on the 8 authority tasks, its nine authority cells. So pools hold 3 or 12 episodes, 120 in all.
  - Each episode is read as (solved, thorough, records) by ordo's own `errand_unit`, so v4's episode outcome and credit rule apply. This is how the estimate-run design's `work/pilot_pools.py` builds them.
  - As in Stage 0, the U cell and the authority cells draw from the same N0 pools, so the types are exchangeable and task difficulty is shared.
- **The pools stay out of the repository.** `stage0.py --pilot` reads the run folder each time, only reading it. It rebuilds the plan on the pinned bank from `run.json` and refuses a run whose plan fingerprint differs. The pools' sha256 is `bdb8695fa587b4b8197db5ed3c9964e92128befa4b6958e97f7efb0d1e4901bc`, and a rerun prints it.
- **Class I** is Stage 0's, unchanged: the same two response models and the same fixture-bank structure.
  - The standard tier keeps Stage 0's replicate seeds, sha256("ordo-stage0|m|i"), so each replicate's Class I replies are Stage 0's.
  - The quick tier's are sha256("ordo-stage0b-quick|m|i").
- **The quick tier** is the estimate-run design's quick triangle, 372 calls:
  - standard's role-free Class I items (F1.a, F1.b, F1.c, F1.tri) and checks, 276 questions;
  - role-free N0 errands on 32 tasks spread over the full pool, 96 errands.
  - It is planned by `ordo.plan` on an in-memory copy of the bank whose standard tier draws those tasks, with the authority frame's questions dropped. The bank is not changed.
  - It prints two rows, role-free Class I and Class II. Its 32 simulated tasks resample the 16 recorded ones, so it holds less task variety than 32 real tasks would.
- **The rules.**
  - "printed" reads the interval ordo's answer row printed: Amendment 3's widening, clamped to 0 to 100. Clamping never changes coverage of 50.
  - The frozen percentile interval is kept for reference.
  - Candidate C is computed from the same replicates' percentile intervals and cluster counts.
- **Size and time.** 10,000 replicates per model and tier, 40,000 null runs in all. They took 26.5 minutes on 16 cores.

**Checks.**
- **The pools** equal the estimate-run design's `work/pilot_pools.json`, task by task.
- **The harness.** On the anulus pairs, today's `stage0.py` gives the same replicates as the version that produced Stage 0's CSV. Compared on 80 replicates and their 250 summary rows: identical.
- **The role-free Class I rows** of the standard tier equal Stage 0's exactly, on every measure and in both models.
- **The authority Class I rows do not, and the cause is known.**
  - Commit a69a9e3 (Codex's final verification, finding 1) came after Stage 0 ran. It made F2.b's deployer and type shares order-balanced within each deployer assignment, and made an empty order cell insufficient.
  - With the same seeds, ordo before and after that commit agrees on role-free Class I in 120 of 120 replicates and on authority Class I in 7 of 120.
  - So Stage 0's authority Class I numbers belong to the earlier F2.b index, and Stage 0b's to today's. The conclusions are the same: the frozen interval fails (false relations 36.5 to 36.9%, against Stage 0's 37.4 to 37.8%), and the printed one passes (5.9 to 7.0%, against 6.2 to 7.0%).
  - No other scoring function the simulation reads has changed since Stage 0, apart from the cluster counts and the printed interval that Amendment 3 added.

**Limits.**
- One model's errands, from one pilot run of 16 tasks.
- The pools mix role-free and authority episodes of a task. That is exchangeable only if, under the null, the frame does not change a task's outcomes.
- Stage 0b measures the null. It says nothing about the power of any rule against a real preference, or about the policy arms, which are not null.
- Stage 0's other limits stand: idealised Class I response models, and bootstrap draws fixed across replicates.

### The CSV

`stage0b.csv` has Stage 0's columns, preceded by:
- `tier`: standard or quick;
- `pools`: pilot.

Its rules are "printed: t(G-1), G of the thinnest component (Amendment 3)", "frozen: percentile 95%" and "candidate C: role-free errands t(G/2-1), else printed". The cycle flag is under the frozen rule only, as in Stage 0.

    python3 docs/ordo/stage0.py --pilot <the pilot's runs/ordo-s2-neutral folder> --replicates 10000 --jobs 16
