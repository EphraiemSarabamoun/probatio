"""Directional-bound power for the synthetic agents, by lattice convolution.

This integrates scenario means, not a Monte Carlo sample. Support is complete;
the probabilities force the other quality guards to pass. It does not
model refusals, guard failures, or the refuted bootstrap rule. NumPy speeds up
table generation; the standard-library path supports independent small checks.
"""
import csv
import itertools
import math
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # the repository root: versus_estimator and its modules
from versus_bounds import bounded_mean_radius

try:
    import numpy as np
except ImportError:
    np = None


def convolve(left, right):
    """Add two independent lattice-valued variables without sampling."""
    if np is not None:
        return np.convolve(left, right).tolist()
    out = [0.] * (len(left) + len(right) - 1)
    for i, a in enumerate(left):
        if a:
            for j, b in enumerate(right):
                out[i+j] += a*b
    return out


def scenario_pmf(stability, metric, p1, p2):
    """Mirror synthetic.generate's independent role draws on 12.5-point units."""
    if stability not in ('stable', 'noisy') or metric not in ('rho', 'kappa'):
        raise ValueError('registered stability and conflict metric required')
    p1, p2 = [min(1., max(0., p)) for p in (p1, p2)]
    n = 1 if stability == 'stable' else 4
    role1 = [math.comb(n,k)*p1**k*(1-p1)**(n-k) for k in range(n+1)]
    role2 = [math.comb(n,k)*p2**k*(1-p2)**(n-k) for k in range(n+1)]
    out = [0.] * 9
    for a, b in itertools.product(range(n+1), repeat=2):
        value = a+n-b if metric == 'rho' else a+b
        out[value*(4//n)] += role1[a]*role2[b]
    return out


def repeated(pmf, count):
    """Sum identically distributed scenarios with binary convolution powers."""
    out = [1.]
    while count:
        if count & 1:
            out = convolve(out, pmf)
        count >>= 1
        if count:
            pmf = convolve(pmf, pmf)
    return out


def power(stability, metric, theta, G, m, family=False):
    """Integrate the strict registered upper-direction event, including EPS.

Balanced fixed effects are +.15 for half the scenarios and -.15 for half.
Whole eight-family banks therefore require G to be divisible by eight.
"""
    radius = bounded_mean_radius(m, G, .05)
    if family and G % 8:
        raise ValueError('balanced eight-family banks require G divisible by eight')
    dist = [1.]
    for offset in ((-.15, .15) if family else (0.,)):
        p1 = theta + offset
        p2 = (1-theta if metric == 'rho' else theta) + offset
        dist = convolve(dist, repeated(scenario_pmf(stability, metric, p1, p2),
                                       G//2 if family else G))
    return math.fsum(p for i,p in enumerate(dist) if 12.5*i/G-radius > 50+1e-12)


def callable_value(stability, G, m, family=False):
    """Find the smallest upper true value with at least 80% bound power."""
    lo, hi = .5, 1.
    for _ in range(40):
        mid = (lo+hi)/2
        if power(stability, 'rho', mid, G, m, family) >= .8:
            hi = mid
        else:
            lo = mid
    return hi*100


def required_scenarios(stability, theta, m, family=False, balanced=False):
    """Require 80% power at G and the next sixteen permitted sizes.

The persistence check handles discrete threshold oscillations. This is a
declared local persistence criterion, not a proof for every larger size.
"""
    step = 8 if balanced or family else 1
    if balanced and not family:
        # Report the verifier's convention: round the persistent integer-size
        # requirement upward, rather than searching only every eighth size.
        return 8*math.ceil(required_scenarios(stability, theta, m)/8)
    for G in range(16 if step == 8 else 12, 2001, step):
        if all(power(stability, 'rho', theta, G+step*j, m, family) >= .8
               for j in range(17)):
            return G
    raise ValueError('80% power not reached within search range')


def write_csv(path, rows):
    """Store all numerical evidence with portable newlines and explicit units."""
    with path.open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)


def main():
    """Regenerate exact power and the owner's callable-effect table offline."""
    results = Path(__file__).parent/'results'
    rows = []
    callable_rows = []
    for names, stability, family in itertools.product((2,3), ('stable','noisy'), (False,True)):
        m = 2 if names == 2 else 6
        for metric, theta in (('rho',.65), ('rho',.8), ('kappa',.6), ('kappa',.7)):
            rows.append(dict(names=names, stability=stability, family_effects=family,
                             metric=metric, truth=100*theta, complete_scenarios=48,
                             new_power=power(stability,metric,theta,48,m,family),
                             new_radius=bounded_mean_radius(m,48,.05),
                             method='exact_lattice_convolution',
                             conditioning='complete_support_other_guards_forced_pass'))
        callable_rows.append(dict(names=names, stability=stability, family_effects=family,
                                  G=48, value_for_80pct=callable_value(stability,48,m,family),
                                  G_for_65=required_scenarios(stability,.65,m,family),
                                  G_for_70=required_scenarios(stability,.70,m,family),
                                  balanced_G_for_65=required_scenarios(stability,.65,m,family,True),
                                  balanced_G_for_70=required_scenarios(stability,.70,m,family,True)))
    write_csv(results/'repair_power.csv',rows)
    write_csv(results/'callable_effects.csv',callable_rows)


if __name__ == '__main__':
    main()
