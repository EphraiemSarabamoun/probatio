# Why the first joint rule was replaced

The original standard rule used a family-stratified centered max-|T| bootstrap
with inflation 1.7. Only zero-variance members used a bounded-mean radius. Its
40 tested standard cells passed, but an independent synthetic check found the
near-saturated, unequal-family-variance failure below. No versus model response
was used to select or repair the rule.

Seven families always choose the deployer, giving scenario rho=100, kappa=50.
In the eighth, each scenario is always canonical A with probability a, always
canonical B with probability a, or balanced with probability 1-2a. Balanced
letter-A and W versions both give rho=kappa=50; a deployer-balanced version
has a different, explicitly computed rho truth. Kappa truth remains exactly 50.
Only six scenarios estimate its stochastic tail, and the stratified bootstrap
understates that tail. These are different policies from homogeneous-family
nulls, not a reinterpretation of their successful results.

| evidence | names / a | false pages or configurations | rate / 95% upper |
|---|---|---:|---:|
| exact registered-draw enumeration | 2 / 1/3 | 42/729 | 5.7613% exact |
| independent full-grid check | 2 / 1/3 | 216/4,000 | 5.40% / 6.14% |
| independent fast-rule check, validated against full grids | 3 / 5/12 | 1,507/20,000 | 7.535% / 7.91% |

`refutation_check.py` reproduces the 42/729 failure through the historical
reference and checks the repaired reference on the same 729 configurations.
[refutation_exact.csv](refutation_exact.csv) includes ten a values. The new
rule produces zero false directions in that enumeration. Its full fresh
selection, validation, power and sufficient-statistic/full-grid bridge evidence
are the repair-prefixed files. The independent historical counts above remain
reported as received evidence, not as a claimed rerun of those 24,000 pages.
