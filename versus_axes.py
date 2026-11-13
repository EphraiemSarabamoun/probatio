"""Registered reference plan axes; production must map its frozen IDs to these axes."""

FAMILIES = ('scheduling', 'queues', 'budget', 'quotas', 'review', 'documents', 'travel', 'access')
ERRAND_SEEDS = tuple(range(625, 649)) + tuple(range(301, 309))


def plan_ids(frame, tier):
    """Use stable family-major IDs; quick is two of six in each of eight families."""
    if tier not in ('quick', 'standard'):
        raise ValueError('registered quick or standard tier required')
    n = 12 if frame == 'triad' else (32 if frame == 'errand' else 48)
    prefix = {'allocation': 'va', 'conflict': 'vc', 'triad': 'vt', 'errand': 've'}[frame]
    ids = []
    for i in range(n):
        if frame in ('allocation', 'conflict'):
            family = FAMILIES[i // 6]
        elif frame == 'triad':
            family = FAMILIES[i % 6]  # Six domains, two scenarios in each.
        else:
            family = 'refunds'  # All 32 selected source worlds have this family.
        ids.append((prefix + '-%02d' % (i + 1) + ('-triad' if frame == 'triad' else ''), family))
    return [(s, f) for i, (s, f) in enumerate(ids) if tier != 'quick' or frame not in ('allocation', 'conflict') or i % 6 < 2]
