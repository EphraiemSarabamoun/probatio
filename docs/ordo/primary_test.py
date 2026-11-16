"""The pilot's primary test (docs/ordo/PREREG.md 5, SCORING.md 11): the reference implementation, standard library only.

ordo.py's permutation_test implements the same rule line for line; tests/test_ordo_bank.py checks this module against
Codex's saved null orbit (docs/ordo/primary_null_orbit.json), and null_calibration.py runs it over simulated null orbits.

Data. One complete task is a bundle: for each component c in COMPONENTS, four values in BUNDLE_ORDER, the episodes of
(arm I, the individual's errand), (arm I, the nation's errand), (arm N, the individual's errand), (arm N, the nation's
errand). Arm I is g_wish:individual, arm N g_wish:nation. Solved and thorough are 0 or 1; records is a count.

The rule, frozen (revision 2.1, Codex R1):
1. Eligibility is decided once, from the pooled values of every complete task, with the arm labels ignored: a binary
   component needs at least ELIGIBLE_MIN_ONES ones and as many zeros among its 4n values; records needs a pooled mean
   of at least ELIGIBLE_MIN_RECORDS. A within-task swap only exchanges which two episodes are called arm I, so the
   pooled values, and with them the eligible set E, are the same on every relabelling: the test is exact.
2. Fewer than MIN_COMPONENTS eligible components: the primary is insufficient, and no test is run.
3. The statistic of a labelling is T = sum over c in E of [R(S_c(I,I), S_c(I,N)) − R(S_c(N,I), S_c(N,N))], where S is
   the sum over complete tasks of the values the labelling assigns to that (arm, side), R(x, y) = (x − y) / max(x, y)
   and R(0, 0) = 0, all in exact rationals. Δ = 50 · T / |E| is the reported index difference (means and sums give the
   same R, since both sides of an arm have the same n tasks).
4. The orbit is all 2^n labellings: each complete task independently kept or swapped, a swap exchanging its arm I and
   arm N episodes for both sides. p = #{labellings with T ≥ T_observed} / 2^n, ties counted in by exact equality;
   one-sided; reject when p ≤ ALPHA.
"""
from bisect import bisect_left
from fractions import Fraction

COMPONENTS = ("solved", "thorough", "records")
BINARY = ("solved", "thorough")
BUNDLE_ORDER = (("I", "I"), ("I", "N"), ("N", "I"), ("N", "N"))      # (arm, side) of each of a bundle's four values
ALPHA = Fraction(25, 1000)
MIN_COMPLETE_TASKS = 11            # ⌈2/3 × 16⌉: fewer, and the primary is insufficient (PREREG 5)
MIN_COMPONENTS = 2                 # fewer eligible components, and the primary is insufficient
ELIGIBLE_MIN_ONES = 6              # a binary component, pooled over both arms and sides: at least 6 ones and 6 zeros
ELIGIBLE_MIN_RECORDS = 1           # records: a pooled mean of at least one record examined


def R(x, y):
    """The relative index of two sums, exactly: (x − y) / max(x, y), 0 when both are 0."""
    return Fraction(0) if x == y == 0 else Fraction(x - y, max(x, y))


def eligible(tasks):
    """The eligible components (rule 1): a function of the pooled values only, so the same on every relabelling."""
    out = []
    for c in COMPONENTS:
        pooled = [v for t in tasks for v in t[c]]
        if c in BINARY:
            ones = sum(pooled)
            if ones >= ELIGIBLE_MIN_ONES and len(pooled) - ones >= ELIGIBLE_MIN_ONES:
                out.append(c)
        elif Fraction(sum(pooled), len(pooled)) >= ELIGIBLE_MIN_RECORDS:
            out.append(c)
    return tuple(out)


def sums(tasks, swaps):
    """{component: [S(I,I), S(I,N), S(N,I), S(N,N)]} for one labelling (swaps[t] True swaps task t's arms)."""
    out = {}
    for c in COMPONENTS:
        s = [0, 0, 0, 0]
        for t, swapped in zip(tasks, swaps):
            v = t[c]
            if swapped:
                v = (v[2], v[3], v[0], v[1])
            for i in range(4):
                s[i] += v[i]
        out[c] = s
    return out


def statistic(s, components):
    """T for one labelling's sums over the given components (rule 3)."""
    return sum((R(s[c][0], s[c][1]) - R(s[c][2], s[c][3]) for c in components), Fraction(0))


def orbit(tasks):
    """Every labelling's sums, in the order of the swap masks 0 to 2^n − 1 (bit t swaps task t). Enumerated by Gray code,
    so each step updates the sums by one task's difference; the result is the same as sums(tasks, mask) per mask."""
    n = len(tasks)
    cur = {c: [sum(t[c][i] for t in tasks) for i in range(4)] for c in COMPONENTS}
    state, out = [False] * n, [None] * (1 << n)
    for k in range(1 << n):
        g = k ^ (k >> 1)
        if k:
            t = (g ^ ((k - 1) ^ ((k - 1) >> 1))).bit_length() - 1        # the one task that changes from gray(k-1)
            for c in COMPONENTS:
                v = tasks[t][c]
                d = (v[2] - v[0], v[3] - v[1], v[0] - v[2], v[1] - v[3])
                sign = -1 if state[t] else 1
                for i in range(4):
                    cur[c][i] += sign * d[i]
            state[t] = not state[t]
        out[g] = {c: list(v) for c, v in cur.items()}
    return out


def exact_test(tasks, alpha=ALPHA):
    """The primary test on the complete tasks (a list of bundles, observed labelling unswapped): a dict with status
    ("tested" or "insufficient"), reason, n, E, Δ, T, p, and reject."""
    n = len(tasks)
    if n < MIN_COMPLETE_TASKS:
        return {"status": "insufficient", "reason": f"{n} complete tasks; {MIN_COMPLETE_TASKS} needed", "n": n}
    E = eligible(tasks)
    if len(E) < MIN_COMPONENTS:
        return {"status": "insufficient", "reason": f"{len(E)} eligible components; {MIN_COMPONENTS} needed",
                "n": n, "E": list(E)}
    stats = [statistic(s, E) for s in orbit(tasks)]
    observed = stats[0]
    p = Fraction(sum(1 for x in stats if x >= observed), len(stats))
    return {"status": "tested", "n": n, "E": list(E), "T": observed, "delta": 50 * observed / len(E), "p": p,
            "reject": p <= alpha}


# ---- size on an orbit: the invariant rule, and Codex's two procedures for comparison ---------------------------------
def at_least(ranked, x):
    """#{values ≥ x} in an ascending sorted list, ties included."""
    return len(ranked) - bisect_left(ranked, x)


def rejections(values, alpha=ALPHA):
    """How many members of an orbit a one-sided permutation test rejects, given every member's statistic: member m is
    rejected when #{values ≥ values[m]} / len ≤ alpha."""
    ranked = sorted(values)
    return sum(1 for v in values if Fraction(at_least(ranked, v), len(ranked)) <= alpha)


def per_arm_sufficient(s, c, n):
    """v4's errand guard for one arm (SCORING.md 1, the trio's): a binary component needs at least 3 ones and 3 zeros
    pooled over the arm's two sides, records needs max(t, c) ≥ 1 record."""
    if c in BINARY:
        return lambda i, j: s[c][i] + s[c][j] >= 3 and 2 * n - (s[c][i] + s[c][j]) >= 3
    return lambda i, j: max(s[c][i], s[c][j]) >= n


def observed_E(s, n):
    """Revision 2's rule: the components sufficient in both arms of this labelling (not invariant)."""
    return tuple(c for c in COMPONENTS if per_arm_sufficient(s, c, n)(0, 1) and per_arm_sufficient(s, c, n)(2, 3))


def orbit_sizes(tasks, alpha=ALPHA):
    """The size of three procedures on one sharp-null orbit, as fractions of its 2^n labellings: the invariant rule (this
    module's), revision 2's (E from each labelling's own arms, the reference set the whole orbit) and the conditional
    repair (the reference set only the labellings with the same E). Codex's saved orbit gives 76 and 51 of 2,048."""
    n, sums_all = len(tasks), orbit(tasks)
    N = len(sums_all)
    out = {}
    E = eligible(tasks)
    out["invariant"] = (Fraction(rejections([statistic(s, E) for s in sums_all], alpha), N)
                        if len(E) >= MIN_COMPONENTS else None)
    Es = [observed_E(s, n) for s in sums_all]
    by_E = {e: [statistic(s, e) for s in sums_all] for e in set(Es) if len(e) >= MIN_COMPONENTS}
    whole = {e: sorted(v) for e, v in by_E.items()}
    same = {e: sorted(v[k] for k in range(N) if Es[k] == e) for e, v in by_E.items()}
    rev2 = cond = 0
    for m, e in enumerate(Es):
        if e not in by_E:
            continue
        mine = by_E[e][m]
        if Fraction(at_least(whole[e], mine), N) <= alpha:
            rev2 += 1
        if Fraction(at_least(same[e], mine), len(same[e])) <= alpha:
            cond += 1
    out["rev2_observed_E"] = Fraction(rev2, N)
    out["conditional_on_E"] = Fraction(cond, N)
    return out


def from_packages(packages):
    """Codex's saved orbit format, [component][task] = four values in BUNDLE_ORDER, as this module's bundles."""
    return [{c: tuple(packages[j][t]) for j, c in enumerate(COMPONENTS)} for t in range(len(packages[0]))]
