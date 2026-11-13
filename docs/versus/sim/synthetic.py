"""Synthetic planned reply tables, never an authored or validated scenario bank."""
import itertools
import random

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # the repository root: versus_estimator and its modules
from versus_estimator import COMPONENTS, TYPES

from versus_axes import FAMILIES, ERRAND_SEEDS, plan_ids


def letter_for(row, party):
    """Encode a canonical winner using the same menu algebra as the reference."""
    if row['frame'] != 'conflict':
        return chr(65 + row['listing'].index(party))
    first = row['pair'][0] if row['T'] * row['L'] == 1 else row['pair'][1]
    return 'A' if party == first else 'B'


def generate(participants=TYPES, tier='standard', seed=1, stability='stable',
             family_effect=False, shared_pairs=False, allocation=0.5, rho=0.5,
             kappa=0.5, errand=0.5, tie=0.0, c_order=0.0, invalid=0.0,
             refusal=0.0, recusal=0.0, recognition=False, adversary=None,
             arms=(('names', 'on'),), unequal_difficulty=True, mixed=False,
             balanced_families=False, return_effects=False):
    """Construct full grids with controlled truths and optional quality failures.

A stable choice uses one uniform variate per scenario and role, reused across
listing/letter variants. Noisy choices draw per reply. A shared family offset
of +/-0.15 makes rows within a family dependent; this intentionally challenges
a scenario-only bootstrap. Shared-pair draws preserve cross-pair correlation.
Errands have task-shared difficulty but party-specific outcomes and records;
record effort is calibrated to the requested population relative index, while
binary components at the same relative means add finite-sample ratio bias.
"""
    rng = random.Random(seed)
    pairs = list(itertools.combinations(participants, 2))
    rows = []
    family_offsets = {(frame, f): rng.choice((-0.15, 0.15)) if family_effect else 0.0 for frame in ('allocation', 'conflict', 'triad') for f in FAMILIES}
    if family_effect and balanced_families:
        for frame in ('allocation', 'conflict', 'triad'):
            levels = [-0.15]*4 + [0.15]*4
            rng.shuffle(levels)
            for f, effect in zip(FAMILIES, levels):
                family_offsets[(frame, f)] = effect
    caches = {}

    def draw(frame, scenario, pair, role, stable=True):
        """Reuse latent policy draws only across the specified design dependencies."""
        key = (frame, scenario, () if shared_pairs else pair, role)
        if stable and stability == 'stable':
            if key not in caches:
                caches[key] = rng.random()
            return caches[key]
        return rng.random()

    for roster, memory in arms:
        for frame in ('allocation', 'conflict'):
            for sid, fam in plan_ids(frame, tier):
                for pair in pairs:
                    for factors in ([None, None] if frame == 'allocation' else itertools.product((-1, 1), repeat=3)):
                        row = {'scenario': sid, 'family': fam, 'frame': frame,
                               'pair': list(pair), 'roster': roster, 'memory': memory}
                        if frame == 'allocation':
                            # Exactly one row per listing, independent of chosen party.
                            prior = sum(r['scenario'] == sid and r['frame'] == frame and r['pair'] == list(pair) and r['roster'] == roster and r['memory'] == memory for r in rows[-6:])
                            row['listing'] = list(pair if prior == 0 else pair[::-1])
                            p = allocation + family_offsets[(frame, fam)]
                            if mixed and pair == pairs[0]:
                                p = 0.70 + family_offsets[(frame, fam)]
                            u = draw(frame, sid, pair, 0)
                            chosen = pair[0] if u < p else pair[1]
                            cp = min(1, max(0, tie + (c_order if row['listing'][0] == pair[0] else -c_order)))
                            if rng.random() < cp:
                                row['outcome'] = 'C'
                            else:
                                row['outcome'] = letter_for(row, chosen)
                        else:
                            t, l, n = factors
                            row.update(T=t, L=l, N=n, W=t*l*n)
                            rr = 0.8 if mixed and pair == pairs[0] else rho
                            p = kappa + (rr - 0.5) * t + family_offsets[(frame, fam)]
                            chosen = pair[0] if draw(frame, sid, pair, t) < p else pair[1]
                            row['outcome'] = letter_for(row, chosen)
                        if adversary:
                            if adversary in ('first_option', 'same_letter'):
                                row['outcome'] = 'A'
                            elif adversary == 'first_named':
                                chosen = row['listing'][0] if frame == 'allocation' else (pair[0] if row['T'] * row['N'] == 1 else pair[1])
                                row['outcome'] = letter_for(row, chosen)
                            elif adversary == 'deployer' and frame == 'conflict':
                                row['outcome'] = letter_for(row, pair[0] if row['T'] == 1 else pair[1])
                            elif adversary == 'all_C':
                                row['outcome'] = 'C' if frame == 'allocation' else 'invalid'
                            elif adversary == 'same_wording' and frame == 'conflict':
                                row['outcome'] = letter_for(row, pair[0] if row['T'] * row['W'] == 1 else pair[1])
                            elif adversary == 'same_name':
                                row['outcome'] = letter_for(row, pair[0])
                        z = rng.random()
                        if z < invalid:
                            row['outcome'] = 'invalid'
                        elif z < invalid + refusal:
                            row['outcome'] = 'refused'
                        elif z < invalid + refusal + recusal and (not recognition or memory == 'on'):
                            row['outcome'] = 'recused'
                        rows.append(row)
        if len(participants) == 3:
            for sid, fam in plan_ids('triad', tier):
                u = draw('triad', sid, (), 0)
                for listing in itertools.permutations(participants):
                    row = {'scenario': sid, 'family': fam, 'frame': 'triad', 'listing': list(listing),
                           'roster': roster, 'memory': memory}
                    z = u if stability == 'stable' else rng.random()
                    winner = participants[min(2, int(z * 3))]
                    row['outcome'] = letter_for(row, winner)
                    if adversary in ('first_option', 'first_named', 'same_letter'):
                        row['outcome'] = 'A'
                    elif adversary == 'same_name':
                        row['outcome'] = letter_for(row, participants[0])
                    elif adversary == 'all_C':
                        row['outcome'] = 'C'  # C is a third choice here, never a tie.
                    if rng.random() < invalid + refusal:
                        row['outcome'] = 'invalid'
                    rows.append(row)
        # Antisymmetric log effort contrasts put each requested pair at the
        # target when it is a two-party page. Three-party effects are ordered,
        # so the outer-pair truth differs and is not used for power reporting.
        relative = 2 * errand - 1
        ratio = 1 - abs(relative)
        levels = [ratio ** j for j in range(len(participants))]
        if relative < 0:
            levels.reverse()
        for i, (sid, fam) in enumerate(plan_ids('errand', tier)):
            difficulty = rng.uniform(0.25, 0.75) if unequal_difficulty else 0.55
            for j, party in enumerate(participants):
                level = levels[j]
                values = {'solved': int(rng.random() < difficulty * level),
                          'thorough': int(rng.random() < difficulty * level),
                          'records': max(0, round(12 * difficulty * level + rng.uniform(-2, 2)))}
                row = {'scenario': sid, 'seed': ERRAND_SEEDS[i], 'family': fam,
                       'frame': 'errand', 'beneficiary': party, 'values': values,
                       'outcome': 'valid', 'roster': roster, 'memory': memory}
                if rng.random() < invalid + refusal:
                    row['outcome'] = 'invalid'
                rows.append(row)
    return (rows, family_offsets) if return_effects else rows
