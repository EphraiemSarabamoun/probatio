"""Independent planned-grid builder in the reference row format (independent of synthetic.py).

Policies are functions of the design factors and return the CHOSEN PARTY (or a
nonchoice code); this module converts parties to menu letters with the design's
own algebra: in a conflict the deployer's option is letter A iff L=+1, the
deployer is pair[0] iff T=+1; in allocation and triad the letter is the listing
position. Nothing here imports the reference arithmetic.
"""
import itertools
import random

FAMILIES = ('scheduling', 'queues', 'budget', 'quotas', 'review', 'documents', 'travel', 'access')
TRIAD_FAMILIES = FAMILIES[:6]
TYPES = ('individual', 'organization', 'nation')
NONCHOICE = ('refused', 'recused', 'invalid', 'missing')


def ids(frame, tier, per_family=None):
    """Stable ids; standard 6 per family, quick 2 per family (first two)."""
    if frame == 'triad':
        return [('vt-%02d-triad' % (i + 1), TRIAD_FAMILIES[i % 6]) for i in range(12)]
    if frame == 'errand':
        return [('ve-%02d' % (i + 1), 'refunds') for i in range(32)]
    k = per_family or (6 if tier == 'standard' else 2)
    out = []
    for f_i, fam in enumerate(FAMILIES):
        for j in range(k):
            out.append(('%s-%02d' % ('va' if frame == 'allocation' else 'vc', f_i * 6 + j + 1), fam))
    return out


def conflict_letter(pair, T, L, party):
    deployer = pair[0] if T == 1 else pair[1]
    deployer_letter = 'A' if L == 1 else 'B'
    if party == deployer:
        return deployer_letter
    return 'B' if deployer_letter == 'A' else 'A'


def build(participants, tier='standard', alloc=None, conflict=None, triad=None, errand=None,
          seed=0, arm=('names', 'on'), per_family=None, triad_on=True):
    """alloc(ctx)->party|'C'|nonchoice; conflict(ctx)->party|nonchoice; triad(ctx)->party|nonchoice;
    errand(ctx)->dict|nonchoice. ctx carries rng, scenario index, family, pair, factors."""
    rng = random.Random(seed)
    roster, memory = arm
    pairs = list(itertools.combinations(participants, 2))
    rows = []
    for si, (sid, fam) in enumerate(ids('allocation', tier, per_family)):
        for pair in pairs:
            for listing in (list(pair), list(pair[::-1])):
                ctx = dict(rng=rng, s=si, sid=sid, family=fam, pair=pair, listing=listing)
                who = alloc(ctx) if alloc else rng.choice(list(pair) + ['C'])
                if who in NONCHOICE:
                    out = who
                elif who == 'C':
                    out = 'C'
                else:
                    out = 'AB'[listing.index(who)]
                rows.append(dict(scenario=sid, family=fam, frame='allocation', pair=list(pair), roster=roster,
                                 memory=memory, listing=listing, outcome=out))
    for si, (sid, fam) in enumerate(ids('conflict', tier, per_family)):
        for pair in pairs:
            for T, L, N in itertools.product((-1, 1), repeat=3):
                W = T * L * N
                ctx = dict(rng=rng, s=si, sid=sid, family=fam, pair=pair, T=T, L=L, N=N, W=W)
                who = conflict(ctx) if conflict else rng.choice(pair)
                out = who if who in NONCHOICE else conflict_letter(pair, T, L, who)
                rows.append(dict(scenario=sid, family=fam, frame='conflict', pair=list(pair), roster=roster,
                                 memory=memory, T=T, L=L, N=N, W=W, outcome=out))
    if len(participants) == 3 and triad_on:
        for si, (sid, fam) in enumerate(ids('triad', tier)):
            for listing in itertools.permutations(participants):
                ctx = dict(rng=rng, s=si, sid=sid, family=fam, listing=list(listing))
                who = triad(ctx) if triad else rng.choice(participants)
                out = who if who in NONCHOICE else 'ABC'[list(listing).index(who)]
                rows.append(dict(scenario=sid, family=fam, frame='triad', listing=list(listing), roster=roster,
                                 memory=memory, outcome=out))
    for si, (sid, fam) in enumerate(ids('errand', tier)):
        for party in participants:
            ctx = dict(rng=rng, s=si, sid=sid, party=party)
            v = errand(ctx) if errand else dict(solved=int(rng.random() < .5), thorough=int(rng.random() < .5),
                                                 records=rng.randint(0, 10))
            row = dict(scenario=sid, seed=(list(range(625, 649)) + list(range(301, 309)))[si], family='refunds',
                       frame='errand', beneficiary=party, roster=roster, memory=memory)
            if isinstance(v, str):
                row.update(outcome=v)
            else:
                row.update(outcome='valid', values=v)
            rows.append(row)
    return rows


# Convenience policies -------------------------------------------------------

def deployer(ctx):
    return ctx['pair'][0] if ctx['T'] == 1 else ctx['pair'][1]


def client(ctx):
    return ctx['pair'][1] if ctx['T'] == 1 else ctx['pair'][0]


def first_letter_conflict(ctx):
    """Always letter A: party on letter A."""
    pair, T, L = ctx['pair'], ctx['T'], ctx['L']
    return pair[0] if T * L == 1 else pair[1]


def first_named_conflict(ctx):
    pair, T, N = ctx['pair'], ctx['T'], ctx['N']
    dep = pair[0] if T == 1 else pair[1]
    cli = pair[1] if T == 1 else pair[0]
    return dep if N == 1 else cli


def first_wording_conflict(ctx):
    pair, T, W = ctx['pair'], ctx['T'], ctx['W']
    dep = pair[0] if T == 1 else pair[1]
    cli = pair[1] if T == 1 else pair[0]
    return dep if W == 1 else cli


def pair_first_name(ctx):
    return ctx['pair'][0]


def first_listed(ctx):
    return ctx['listing'][0]
