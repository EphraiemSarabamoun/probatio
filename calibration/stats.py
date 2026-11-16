"""Policy-neutral statistics for CALIBRATION.md section 9.

Callers select complete, eligible pairs for each class and enforce the study's
minimum-pair rules. These helpers never drop missing observations or replace an
undefined statistic with zero. ICC is the two-occasion absolute-agreement,
single-measurement ICC(A,1), not a correlation or a consistency ICC.
"""

import math
import random
from numbers import Real


def _numbers(values):
    """Materialize finite real observations; reject missing data and booleans."""
    result = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, Real):
            raise ValueError("observations must be finite real numbers")
        try:
            number = float(value)
        except (OverflowError, ValueError) as exc:
            raise ValueError("observation is outside finite float range") from exc
        if not math.isfinite(number):
            raise ValueError("observations must be finite real numbers")
        result.append(number)
    return result


def _pairs(pairs):
    """Validate two occasions per row without silently discarding either one."""
    result = []
    for pair in pairs:
        try:
            row = _numbers(pair)
        except TypeError as exc:
            raise ValueError("each retest pair must contain two numbers") from exc
        if len(row) != 2:
            raise ValueError("each retest pair must contain two numbers")
        result.append(tuple(row))
    return result


def icc_a1(pairs):
    """Return ICC(A,1) for original/retest pairs, or None if undefined.

At least two complete pairs are required. A common scale keeps sums of squares
finite without changing the ICC. Residuals are squared directly, avoiding the
cancellation of SST - SSR - SSC. Negative ICC estimates are retained.
"""
    pairs = _pairs(pairs)
    n = len(pairs)
    if n < 2:
        return None
    scale = max(abs(value) for pair in pairs for value in pair)
    if scale == 0:
        return None
    rows = [(a / scale, b / scale) for a, b in pairs]
    row_means = [math.fsum(row) / 2 for row in rows]
    column_means = [math.fsum(row[j] for row in rows) / n for j in range(2)]
    grand = math.fsum(column_means) / 2
    msr = 2 * math.fsum((mean - grand) ** 2 for mean in row_means) / (n - 1)
    msc = n * math.fsum((mean - grand) ** 2 for mean in column_means)
    mse = math.fsum(
        (row[j] - row_means[i] - column_means[j] + grand) ** 2
        for i, row in enumerate(rows) for j in range(2)
    ) / (n - 1)
    denominator = msr + mse + 2 * (msc - mse) / n
    if denominator <= 0:
        return None
    value = (msr - mse) / denominator
    return value if math.isfinite(value) else None


def _ranks(values):
    """Assign average one-based ranks to tied observations."""
    ranks = [0.0] * len(values)
    order = sorted(range(len(values)), key=values.__getitem__)
    start = 0
    while start < len(order):
        stop = start + 1
        while stop < len(order) and values[order[stop]] == values[order[start]]:
            stop += 1
        rank = (start + 1 + stop) / 2
        for position in range(start, stop):
            ranks[order[position]] = rank
        start = stop
    return ranks


def spearman(xs, ys):
    """Return Pearson correlation of average ranks, or None when undefined.

The no-ties shortcut is intentionally avoided. Length mismatches and nonfinite
or missing input are data errors, rather than permission to truncate a pairing.
"""
    xs, ys = _numbers(xs), _numbers(ys)
    if len(xs) != len(ys):
        raise ValueError("Spearman inputs must have equal lengths")
    if len(xs) < 2:
        return None
    rx, ry = _ranks(xs), _ranks(ys)
    middle = (len(xs) + 1) / 2
    dx, dy = [x - middle for x in rx], [y - middle for y in ry]
    xx, yy = math.fsum(x * x for x in dx), math.fsum(y * y for y in dy)
    if xx == 0 or yy == 0:
        return None
    return math.fsum(x * y for x, y in zip(dx, dy)) / math.sqrt(xx * yy)


def percentile(values, p):
    """Return the linear-interpolated quantile at p in [0, 1], or None if empty."""
    if isinstance(p, bool) or not isinstance(p, Real) or not math.isfinite(p) or not 0 <= p <= 1:
        raise ValueError("percentile probability must be finite and between 0 and 1")
    ordered = sorted(_numbers(values))
    if not ordered:
        return None
    index = (len(ordered) - 1) * p
    lower, upper = math.floor(index), math.ceil(index)
    fraction = index - lower
    return (1 - fraction) * ordered[lower] + fraction * ordered[upper]


def bootstrap_retest(pairs, B=2000, seed=20261109):
    """Summarize complete pairs and bootstrap ICC while keeping occasions paired.

The return dictionary includes n, icc, icc_ci, median_absolute_difference,
max_absolute_difference, and defined/undefined_bootstrap_replicates. All B draws
resample n complete rows with replacement from a private seeded stream. If any
replicate ICC is undefined, icc_ci is None: silently dropping it would report a
conditional interval instead of the requested percentile interval. n < 2 also
has no ICC interval. Differences remain descriptive even for one pair; callers
decide whether enough eligible pairs exist to evaluate their study criterion.

For pooled classes, callers must jointly resample shared pair identities; three
separate calls here are only marginal per-class summaries, never a pooled CI.
"""
    if isinstance(B, bool) or not isinstance(B, int) or B < 1:
        raise ValueError("B must be a positive integer")
    pairs = _pairs(pairs)
    differences = [abs(a - b) for a, b in pairs]
    if not all(math.isfinite(d) for d in differences):
        raise ValueError("absolute differences exceed finite float range")
    result = {
        "n": len(pairs),
        "icc": icc_a1(pairs),
        "icc_ci": None,
        "median_absolute_difference": percentile(differences, 0.5),
        "max_absolute_difference": max(differences) if differences else None,
        "defined_bootstrap_replicates": 0,
        "undefined_bootstrap_replicates": 0,
    }
    if len(pairs) < 2:
        return result
    rng = random.Random(seed)
    replicates = []
    for _ in range(B):
        statistic = icc_a1([pairs[rng.randrange(len(pairs))] for _ in pairs])
        if statistic is None:
            result["undefined_bootstrap_replicates"] += 1
        else:
            replicates.append(statistic)
    result["defined_bootstrap_replicates"] = len(replicates)
    if result["undefined_bootstrap_replicates"] == 0:
        result["icc_ci"] = [percentile(replicates, 0.025), percentile(replicates, 0.975)]
    return result
