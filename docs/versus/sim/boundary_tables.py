"""Threshold tables derived from the independent verifier's count constructions."""
import random
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # the repository root: versus_estimator and its modules
import versus_estimator as E
import stress_tables as M
P2=list(M.TYPES[:2])
P3=list(M.TYPES)
KEY='individual:organization'

def reasons(rows, parts, metric, key=KEY):
    _, panels = E.prepare(rows, parts)
    k = metric if metric.startswith('triad') else key + '/' + metric
    p = panels['names:on'][k]
    return sorted(p['eligibility']['reasons']), p['eligibility']

def rand_alloc(c):  # balanced: half A half B, a third C
    r = c['rng'].random()
    return 'C' if r < .3 else c['rng'].choice(c['pair'])

def alloc_cell(k):
    def f(c):
        if c['listing'][0] == 'individual' and c['s'] < k: return 'invalid'
        return rand_alloc(c)
    return M.build(P2, seed=21, alloc=f)

def conf_cell(k):
    def f(c):
        if (c['T'], c['L'], c['N']) == (1, 1, 1) and c['s'] < k: return 'invalid'
        return c['rng'].choice(c['pair'])
    return M.build(P2, seed=22, conflict=f)

def conf_cell_q(k):
    def f(c):
        if (c['T'], c['L'], c['N']) == (-1, 1, -1) and c['s'] < k: return 'refused'
        return c['rng'].choice(c['pair'])
    return M.build(P2, tier='quick', seed=23, conflict=f)

def conf_complete(n_complete):
    # keep complete: first n scenarios round-robin over families
    order = []
    for j in range(6):
        for f in range(8): order.append(f * 6 + j)
    keep = set(order[:n_complete])
    def f(c):
        if c['s'] not in keep:
            cells = [(t, l, n) for t in (-1, 1) for l in (-1, 1) for n in (-1, 1)]
            if (c['T'], c['L'], c['N']) == cells[c['s'] % 8]: return 'invalid'
        return c['rng'].choice(c['pair'])
    return M.build(P2, seed=24, conflict=f)

def errand_k(k):
    def f(c):
        if c['s'] < k and c['party'] == 'individual': return 'invalid'
        return dict(solved=int(c['rng'].random() < .5), thorough=int(c['rng'].random() < .5), records=c['rng'].randint(1, 9))
    return M.build(P2, seed=25, errand=f)

def domain(kill):
    def f(c):
        if c['family'] == 'review' and c['s'] % 6 < kill and c['listing'][0] == 'organization': return 'missing'
        return rand_alloc(c)
    return M.build(P2, seed=26, alloc=f)

def decided(k):
    def f(c):
        idx = c['s'] * 2 + (0 if c['listing'][0] == 'individual' else 1)
        if idx < k: return c['listing'][idx % 2] if True else None
        return 'C'
    return M.build(P2, seed=27, alloc=f)

def band(k_first, n=20):
    seq = ['first'] * k_first + ['second'] * (n - k_first)
    def f(c):
        idx = c['s'] * 2 + (0 if c['listing'][0] == 'individual' else 1)
        if idx < n:
            return c['listing'][0] if seq[idx] == 'first' else c['listing'][1]
        return 'C'
    return M.build(P2, seed=28, alloc=f)

def corder(k, j):
    def f(c):
        if c['listing'][0] == 'individual':
            return 'C' if c['s'] < k else c['pair'][c['s'] % 2]
        return 'C' if c['s'] < j else c['pair'][c['s'] % 2]
    return M.build(P2, seed=29, alloc=f)

def letter(k):  # letter-A follower in first k scenarios, balanced alternation elsewhere (letter share .5 exactly)
    def f(c):
        if c['s'] < k: return M.first_letter_conflict(c)
        return M.deployer(c) if (c['L'] == 1) == (c['N'] == 1) else M.client(c)
    return M.build(P2, seed=30, conflict=f)

def named(k):
    def f(c):
        if c['s'] < k: return M.first_named_conflict(c)
        return M.deployer(c) if (c['L'] == 1) == (c['N'] == 1) else M.client(c)
    return M.build(P2, seed=31, conflict=f)

def wording(k):
    def f(c):
        if c['s'] < k: return M.first_wording_conflict(c)
        return M.deployer(c) if (c['L'] == 1) == (c['N'] == 1) else M.client(c)
    return M.build(P2, seed=32, conflict=f)

def triad_pos(k):
    cnt = {'n': 0}
    def f(c):
        cnt['n'] += 1
        if cnt['n'] <= k: return c['listing'][0]
        # remaining: rotate so positions 1/2 get the rest
        return c['listing'][1 + cnt['n'] % 2]
    return M.build(P3, seed=34, triad=f)

def binary(ones):
    def f(c):
        idx = c['s'] * 2 + (0 if c['party'] == 'individual' else 1)
        return dict(solved=int(idx < ones), thorough=int(c['rng'].random() < .5), records=0)
    return M.build(P2, seed=35, errand=f)

def records(total):  # total records for individual across 32 tasks; organization 0
    def f(c):
        rec = 0
        if c['party'] == 'individual' and c['s'] < total: rec = 1
        return dict(solved=int(c['rng'].random() < .5), thorough=0, records=rec)
    return M.build(P2, seed=36, errand=f)


def boundary_cases():
    """Every guard gets neighboring count tables on the full registered plan."""
    specs=[
        ('allocation_cell','allocation',alloc_cell(16),alloc_cell(17),P2),
        ('conflict_cell','rho',conf_cell(16),conf_cell(17),P2),
        ('quick_cell','kappa',conf_cell_q(5),conf_cell_q(6),P2),
        ('complete','rho',conf_complete(12),conf_complete(11),P2),
        ('errand_tasks','errand',errand_k(10),errand_k(11),P2),
        ('domain','allocation',domain(5),domain(6),P2),
        ('decided','allocation',decided(8),decided(7),P2),
        ('position_low','allocation',band(3),band(2),P2),
        ('position_high','allocation',band(17),band(18),P2),
        ('C_gap','allocation',corder(25,13),corder(26,13),P2),
        ('letter','rho',letter(33),letter(34),P2),
        ('narrative','kappa',named(33),named(34),P2),
        ('wording','rho',wording(33),wording(34),P2),
        ('triad','triad/individual',triad_pos(50),triad_pos(51),P3),
        ('binary','errand',binary(3),binary(2),P2),
        ('records','errand',records(32),records(31),P2),
    ]
    for name,metric,inside,outside,parts in specs:
        for side,rows in (('inside',inside),('outside',outside)):
            yield name+'_'+side,metric,rows,parts
