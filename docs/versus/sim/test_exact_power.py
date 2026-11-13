"""Independent small-distribution algebra and externally checked power anchors."""
import itertools
import math
import unittest

import exact_power as P


class ExactPowerTests(unittest.TestCase):
    """Check lattice integration independently of its convolution algorithm."""

    def test_stable_role_enumeration(self):
        # Enumerate four independent role coins for two scenarios. Each
        # scenario contributes the average of its two role-aligned choices.
        theta = .7
        expected = sum(theta**sum(bits)*(1-theta)**(4-sum(bits))
                       for bits in itertools.product((0,1), repeat=4)
                       if sum(bits) >= 3)
        dist = P.repeated(P.scenario_pmf('stable','rho',theta,1-theta),2)
        self.assertAlmostEqual(sum(dist[12:]),expected)
        self.assertAlmostEqual(sum(dist),1.)

    def test_noisy_independent_binomial(self):
        # Sixteen noisy scenarios contain 128 independent choices; their
        # role-aligned successes are Binomial(128, theta), regardless of rho/kappa.
        theta = .8
        radius = 100*math.sqrt(math.log(4/.05)/32)
        expected = sum(math.comb(128,k)*theta**k*(1-theta)**(128-k)
                       for k in range(129) if 100*k/128-radius > 50+1e-12)
        self.assertGreater(expected,0.)
        self.assertLess(expected,1.)
        for metric in ('rho','kappa'):
            self.assertAlmostEqual(P.power('noisy',metric,theta,16,2),expected)

    def test_independent_verifier_anchors(self):
        self.assertAlmostEqual(P.power('stable','kappa',.7,48,2),.391,places=3)
        self.assertAlmostEqual(P.power('stable','kappa',.7,48,6,True),.220,places=3)
        for m,stability,family,value in ((2,'stable',False,75.04),
                                       (2,'noisy',False,73.38),
                                       (6,'stable',True,76.80),
                                       (6,'noisy',True,75.55)):
            self.assertAlmostEqual(P.callable_value(stability,48,m,family),value,places=2)

    def test_family_plan_and_stdlib_path(self):
        with self.assertRaisesRegex(ValueError,'divisible by eight'):
            P.power('stable','rho',.7,49,2,True)
        original = P.np
        try:
            P.np = None
            self.assertEqual(P.convolve([.25,.75],[.5,.5]),[.125,.5,.375])
        finally:
            P.np = original
