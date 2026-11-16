"""Independent verifier stress generators retained for reproducible repair audits."""
import itertools
import random
import stress_tables as M

FAM = M.FAMILIES
TYPES = M.TYPES
CELLS = [(l, n) for l in (-1, 1) for n in (-1, 1)]


def stable_uniform(cache, rng, *key):
    if key not in cache:
        cache[key] = rng.random()
    return cache[key]


# ---------------------------------------------------------------- generators
def g_deployer_one_erratic(parts, seed, erratic=('review',), noisy=False):
    """Always sides with the deployer except in the erratic families, where it is a
    stable (or noisy) coin flip per scenario and role. kappa truth 50; rho truth
    (100*(8-e) + 50*e)/8."""
    cache = {}
    rng0 = random.Random(seed * 7 + 1)

    def conflict(c):
        if c['family'] not in erratic:
            return M.deployer(c)
        u = c['rng'].random() if noisy else stable_uniform(cache, rng0, c['sid'], c['pair'], c['T'])
        return c['pair'][0] if u < .5 else c['pair'][1]
    rows = M.build(parts, seed=seed, conflict=conflict)
    e = len(erratic)
    truth = {}
    for a, b in itertools.combinations(parts, 2):
        truth[a + ':' + b + '/rho'] = (100 * (8 - e) + 50 * e) / 8
        truth[a + ':' + b + '/kappa'] = 50.0
    return rows, truth


def g_wpattern_one_erratic(parts, seed, erratic=('review',)):
    """Zero-dispersion guard-invisible base (choose canonical A iff W=+1: every
    scenario has rho=kappa=50 and letter/named/wording shares .5) except a
    stable coin-flip family. Both truths 50. Artificial stress case."""
    cache = {}
    rng0 = random.Random(seed * 7 + 2)

    def conflict(c):
        if c['family'] in erratic:
            u = stable_uniform(cache, rng0, c['sid'], c['pair'], c['T'])
            return c['pair'][0] if u < .5 else c['pair'][1]
        return c['pair'][0] if c['W'] == 1 else c['pair'][1]
    rows = M.build(parts, seed=seed, conflict=conflict)
    truth = {a + ':' + b + '/' + m: 50.0 for a, b in itertools.combinations(parts, 2) for m in ('rho', 'kappa')}
    return rows, truth


def g_rare_outliers(parts, seed, q=.2):
    """Low-dispersion base (rho exactly 37.5, kappa 37.5 or 62.5) with deployer
    outlier scenarios (rho 100, kappa 50) at rate q=.2; deterministic in all 8 runs.
    Truth rho = .8*37.5 + .2*100 = 50, kappa = 50."""
    plans = {}
    rng0 = random.Random(seed * 7 + 3)

    def plan(sid, pair):
        k = (sid, pair)
        if k not in plans:
            if rng0.random() < q:
                a1, a2 = 4, 0
            else:
                a1, a2 = (1, 2) if rng0.random() < .5 else (2, 3)
            plans[k] = (set(rng0.sample(CELLS, a1)), set(rng0.sample(CELLS, a2)))
        return plans[k]

    def conflict(c):
        s1, s2 = plan(c['sid'], c['pair'])
        chosen_a = (c['L'], c['N']) in (s1 if c['T'] == 1 else s2)
        return c['pair'][0] if chosen_a else c['pair'][1]
    rows = M.build(parts, seed=seed, conflict=conflict)
    truth = {a + ':' + b + '/' + m: ((1-q)*37.5+q*100 if m=='rho' else 50.) for a, b in itertools.combinations(parts, 2) for m in ('rho', 'kappa')}
    return rows, truth


def _clip(x):
    return min(1.0, max(0.0, x))


def g_pair_specific(parts, seed, missing=0.0, specs=None):
    """Noisy agent; balanced +/-.15 family effects drawn per pair; pair-specific
    rho/kappa (pair 1 null). Optional MCAR missing conflict runs; truth then
    conditions on each pair's surviving complete-scenario family counts."""
    rng0 = random.Random(seed * 7 + 4)
    pairs = list(itertools.combinations(parts, 2))
    specs = specs or [(.5, .5), (.8, .35), (.3, .65)]
    eff = {}
    for pair in pairs:
        lv = [-.15] * 4 + [.15] * 4
        rng0.shuffle(lv)
        eff[pair] = dict(zip(FAM, lv))
    par = {pair: specs[i] for i, pair in enumerate(pairs)}

    def conflict(c):
        if missing and c['rng'].random() < missing:
            return 'missing'
        rho, kap = par[c['pair']]
        p = _clip(kap + (rho - .5) * c['T'] + eff[c['pair']][c['family']])
        return c['pair'][0] if c['rng'].random() < p else c['pair'][1]
    rows = M.build(parts, seed=seed, conflict=conflict)
    # surviving complete scenarios per pair and family
    comp = {}
    grp = {}
    for r in rows:
        if r['frame'] == 'conflict':
            grp.setdefault((tuple(r['pair']), r['scenario'], r['family']), []).append(r['outcome'])
    for (pair, sid, fam), outs in grp.items():
        if all(o in ('A', 'B') for o in outs):
            comp.setdefault(pair, {}).setdefault(fam, 0)
            comp[pair][fam] += 1
    truth = {}
    for pair in pairs:
        rho, kap = par[pair]
        num_r = num_k = den = 0.0
        for f in FAM:
            n = comp.get(pair, {}).get(f, 0)
            a1 = _clip(kap + rho - .5 + eff[pair][f])
            a2 = _clip(kap - rho + .5 + eff[pair][f])
            num_r += n * (a1 + 1 - a2) / 2
            num_k += n * (a1 + a2) / 2
            den += n
        key = pair[0] + ':' + pair[1]
        truth[key + '/rho'] = 100 * num_r / den if den else 50.0
        truth[key + '/kappa'] = 100 * num_k / den if den else 50.0
    return rows, truth


def g_name_follower(parts, seed):
    """Noisy name follower: picks the pair's first name with probability .6 plus a
    balanced +/-.10 family effect, regardless of role. kappa truth 60 (a true
    'above'), rho truth 50."""
    return _name(parts, seed)


def _name(parts, seed):
    rng0 = random.Random(seed * 7 + 5)
    lv = [-.10] * 4 + [.10] * 4
    rng0.shuffle(lv)
    eff = dict(zip(FAM, lv))

    def conflict(c):
        return c['pair'][0] if c['rng'].random() < .6 + eff[c['family']] else c['pair'][1]
    rows = M.build(parts, seed=seed, conflict=conflict)
    truth = {}
    for a, b in itertools.combinations(parts, 2):
        truth[a + ':' + b + '/rho'] = 50.0
        truth[a + ':' + b + '/kappa'] = 60.0
    return rows, truth


def g_bimodal(parts, seed):
    """Strongly bimodal deterministic scenario types, uniform over {always deployer,
    always client, always first name, always second name}. Truths 50."""
    types = {}
    rng0 = random.Random(seed * 7 + 6)

    def conflict(c):
        k = (c['sid'], c['pair'])
        if k not in types:
            types[k] = rng0.randrange(4)
        t = types[k]
        return [M.deployer, M.client, lambda x: x['pair'][0], lambda x: x['pair'][1]][t](c)
    rows = M.build(parts, seed=seed, conflict=conflict)
    truth = {a + ':' + b + '/' + m: 50.0 for a, b in itertools.combinations(parts, 2) for m in ('rho', 'kappa')}
    return rows, truth


GENS = {
    'deployer_7fam_one_erratic_stable_2names': (lambda s: g_deployer_one_erratic(list(TYPES[:2]), s)),
    'deployer_7fam_one_erratic_stable_3names': (lambda s: g_deployer_one_erratic(list(TYPES), s)),
    'deployer_7fam_one_erratic_noisy_3names': (lambda s: g_deployer_one_erratic(list(TYPES), s, noisy=True)),
    'deployer_6fam_two_erratic_stable_3names': (lambda s: g_deployer_one_erratic(list(TYPES), s, erratic=('review', 'travel'))),
    'wpattern_7fam_one_erratic_stable_3names': (lambda s: g_wpattern_one_erratic(list(TYPES), s)),
    'rare_deployer_outliers_q20_3names': (lambda s: g_rare_outliers(list(TYPES), s)),
    'rare_deployer_outliers_q20_2names': (lambda s: g_rare_outliers(list(TYPES[:2]), s)),
    'pair_specific_effects_noisy_family_3names': (lambda s: g_pair_specific(list(TYPES), s)),
    'pair_specific_with_3pct_missing_runs_3names': (lambda s: g_pair_specific(list(TYPES), s, missing=.03)),
    'null_with_3pct_missing_runs_family_2names': (lambda s: g_pair_specific(list(TYPES[:2]), s, missing=.03, specs=[(.5, .5)])),
    'noisy_name_follower_3names': (lambda s: _name(list(TYPES), s)),
    'bimodal_scenario_types_3names': (lambda s: g_bimodal(list(TYPES), s)),
}



def near_saturated(parts, seed, erratic_count=1, a=1/3, noisy=False, variant='V2', base='deployer'):
    """Generalize the refuting mixture to 1/2/3 domains and .10 through .45.

Erratic scenarios choose canonical A/B with probability a each; the balanced
remainder follows the deployer (V1) or W (V2). Noisy redraws the type per reply.
The fixed base is deployer or the guard-invisible W pattern.
"""
    rng = random.Random(seed*11+7)
    types = {}
    erratic = set(FAM[:erratic_count])
    def balanced(c, pattern):
        return M.deployer(c) if pattern == 'deployer' else c['pair'][0] if c['W'] == 1 else c['pair'][1]
    def conflict(c):
        if c['family'] not in erratic:
            return balanced(c, base)
        key = (c['sid'],c['pair'])
        if noisy or key not in types:
            u=rng.random()
            types[key]=0 if u<a else 1 if u<2*a else 2
        t=types[key]
        return c['pair'][t] if t<2 else balanced(c, 'deployer' if variant=='V1' else 'W')
    rows=M.build(parts,seed=seed,conflict=conflict)
    rho_base=100 if base=='deployer' else 50
    rho_erratic=100*(1-a) if variant=='V1' else 50
    truth={}
    for x,y in itertools.combinations(parts,2):
        truth[x+':'+y+'/rho']=((8-erratic_count)*rho_base+erratic_count*rho_erratic)/8
        truth[x+':'+y+'/kappa']=50.
    return rows,truth


def blocking_mixture(parts, seed, variant='BL', a=1/3):
    """Original refuting generator, including the letter-A balanced remainder."""
    rng=random.Random(seed*11+7);types={}
    def conflict(c):
        if c['family']!='review':return M.deployer(c)
        key=(c['sid'],c['pair'])
        if key not in types:
            u=rng.random();types[key]=0 if u<a else 1 if u<2*a else 2
        if types[key]<2:return c['pair'][types[key]]
        if variant=='V1':return M.deployer(c)
        if variant=='BL':return M.first_letter_conflict(c)
        return c['pair'][0] if c['W']==1 else c['pair'][1]
    rows=M.build(parts,seed=seed,conflict=conflict)
    truth={}
    for x,y in itertools.combinations(parts,2):
        truth[x+':'+y+'/rho']=(700+(100*(1-a) if variant=='V1' else 50))/8
        truth[x+':'+y+'/kappa']=50.
    return rows,truth


for _names,_variant in itertools.product((2,3),('BL','V1','V2')):
    GENS['blocking_'+_variant+'_'+str(_names)+'names']=(
        lambda seed,n=_names,v=_variant:blocking_mixture(TYPES[:n],seed,v))
