"""Common bounded-mean radius for current and preserved historical rules."""
import math


def bounded_mean_radius(family_size, complete_clusters, alpha):
    """Union-bound independent [0,100] means without estimating their variance."""
    if family_size < 1 or complete_clusters <= 0 or not 0 < alpha < 1:
        raise ValueError('positive family size/support and alpha in (0,1) required')
    return 100 * math.sqrt(math.log(2 * family_size / alpha) / (2 * complete_clusters))
