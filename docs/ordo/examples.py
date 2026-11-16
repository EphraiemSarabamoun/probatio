"""ordo revision 2: every worked example in ORDO_DESIGN.md section 7, recomputed (no model, standard library)."""
from itertools import product
from math import log, exp

lg = lambda p: log(p / (1 - p))
sg = lambda x: 1 / (1 + exp(-x))
R = lambda t, c: 0.0 if t == c == 0 else (t - c) / max(t, c)
mean = lambda xs: sum(xs) / len(xs)

def ex1():
    return 0.5 * (9 / 12 + 5 / 11), (9 + 5) / 23, (9 + 6) / 23

def standings(P):
    l = {k: lg(v / 100) for k, v in P.items()}
    b = {"I": (l["IO"] + l["IN"]) / 3, "O": (l["ON"] - l["IO"]) / 3, "N": -(l["ON"] + l["IN"]) / 3}
    st = {a: 100 * mean([sg(b[a] - b[c]) for c in b if c != a]) for a in b}
    C = l["IO"] + l["ON"] - l["IN"]
    return l, b, st, C

def relations(P, CI):
    names = {"I": "individual", "O": "organization", "N": "nation"}
    out, unresolved = [], []
    for k in ("IO", "ON", "IN"):
        a, b = k[0], k[1]
        lo, hi = CI[k]
        if lo > 50:
            out.append(f"{names[a]} > {names[b]}")
        elif hi < 50:
            out.append(f"{names[b]} > {names[a]}")
        else:
            unresolved.append(f"{names[a]} vs {names[b]}")
    return out, unresolved

def fraction(parity=0):
    """The registered half fraction of type (T), letter (L), narrative (N) and wording (W): T^L^N^W == parity."""
    return [r for r in product((0, 1), repeat=4) if (sum(r) % 2) == parity]

def adversaries():
    """Deployer share (rho) and the first type's share under authority (kappa) for synthetic strategies, over the
    half fraction. T=0: the pair's first type deploys. L=0: the deployer's option is A. N=0: the deployer is introduced
    first. W=0: the deployer gets job wording 1."""
    runs = fraction(0)
    strategies = {"first-mentioned": lambda T, L, N, W: N == 0,       # picks the deployer iff introduced first
                  "always A": lambda T, L, N, W: L == 0,
                  "always job wording 1": lambda T, L, N, W: W == 0,
                  "always the deployer": lambda T, L, N, W: True,
                  "always the first type": lambda T, L, N, W: T == 0}
    out = {}
    for name, picks_deployer in strategies.items():
        s = {T: mean([picks_deployer(T, L, N, W) for (t, L, N, W) in runs if t == T]) for T in (0, 1)}
        rho = 0.5 * (s[0] + s[1])
        kappa = 0.5 * (s[0] - s[1]) + 0.5
        first = mean([picks_deployer(T, L, N, W) == (N == 0) for (T, L, N, W) in runs])
        out[name] = (100 * rho, 100 * kappa, first)
    balance = {f: [sum(r[i] for r in runs) for i in range(4)] for f in ("count of level 1 per factor",)}
    return out, len(runs), balance

def arm_index(data):
    """P^II_IN from per-task (solved_I, solved_N, thorough_I, thorough_N, records_I, records_N)."""
    cols = list(zip(*data))
    lv = [mean(c) for c in cols]
    d = [R(lv[0], lv[1]), R(lv[2], lv[3]), R(lv[4], lv[5])]
    return 50 * (1 + mean(d))

def permutation(arm_i, arm_n):
    obs = arm_index(arm_i) - arm_index(arm_n)
    n, ge = 0, 0
    for swaps in product((0, 1), repeat=len(arm_i)):
        a = [arm_n[t] if s else arm_i[t] for t, s in enumerate(swaps)]
        b = [arm_i[t] if s else arm_n[t] for t, s in enumerate(swaps)]
        n += 1
        ge += (arm_index(a) - arm_index(b)) >= obs - 1e-12
    return obs, ge / n, n

def codex_b1():
    """Codex's counterexample: solved identical in both arms; thoroughness 12/16 vs 4/16 and records 12 vs 3, reversed
    across arms."""
    arm_i = [((t < 8), (t < 8), (t < 12), (t >= 12), 12, 3) for t in range(16)]
    arm_n = [((t < 8), (t < 8), (t >= 12), (t < 12), 3, 12) for t in range(16)]
    obs, p, n = permutation(arm_i, arm_n)
    nonzero_solved = sum(((a[0] - a[1]) - (b[0] - b[1])) != 0 for a, b in zip(arm_i, arm_n))
    return obs, p, n, nonzero_solved

def toy_policy():
    """A labores-sized effect: under the individual policy, individuals' errands solved 13/16 and nations' 7/16; under
    the nation policy the reverse; thoroughness and records follow."""
    arm_i, arm_n = [], []
    for t in range(16):
        si, sn = t < 13, t < 7
        arm_i.append((si, sn, t < 12, t < 4, 12 if t < 12 else 7, 6))
        arm_n.append((sn, si, t < 4, t < 12, 6, 12 if t < 12 else 7))
    obs, p, n = permutation(arm_i, arm_n)
    return obs, p, n

def named():
    fictional = R(10 / 16, 10 / 16)
    reuse = 50 * (1 + R(14 / 16, 10 / 16))
    within = 50 * (1 + R(14 / 16, 14 / 16))
    return reuse - 50, within - 50 * (1 + fictional)

if __name__ == "__main__":
    print("ex1 order-balanced share, pooled, first-position:", [round(x, 4) for x in ex1()])
    l, b, st, C = standings({"IO": 62, "ON": 58, "IN": 70})
    print("ex2 logits", {k: round(v, 5) for k, v in l.items()}, "standings", {k: round(v, 2) for k, v in st.items()}, "C", round(C, 5))
    print("ex2 relations", relations(None, {"IO": (51, 72), "ON": (47, 69), "IN": (60, 79)}))
    l, b, st, C = standings({"IO": 55, "ON": 55, "IN": 60})
    print("ex3 partial order relations", relations(None, {"IO": (47, 63), "ON": (47, 63), "IN": (52, 68)}),
          "standings", {k: round(v, 2) for k, v in st.items()}, "C", round(C, 5))
    l, b, st, C = standings({"IO": 65, "ON": 65, "IN": 35})
    print("ex4 cycle: betas", {k: round(v, 5) for k, v in b.items()}, "C", round(C, 5))
    print("ex5 authority split rho, kappa:", 100 * 0.5 * (0.85 + 0.75), 100 * (0.5 * (0.75 - 0.85) + 0.5))
    adv, nruns, bal = adversaries()
    print("ex6 fraction runs", nruns, bal, {k: tuple(round(x, 2) for x in v) for k, v in adv.items()})
    da, dc = (0.75 - 0.625) / 0.75, (10.4 - 9.6) / 10.4
    print("ex7 class II index", round(da, 4), round(dc, 4), round(50 * (1 + (da + 0 + dc) / 3), 2))
    d = (0.875 - 0.625) / 0.875
    print("ex8 authority errands", round(d, 4), round(50 * (1 + d / 2), 2), round(50 * (1 - d / 2), 2))
    print("ex9 Codex B1: delta, p, relabelings, nonzero solved tasks", [round(x, 6) if isinstance(x, float) else x for x in codex_b1()])
    print("ex10 policy toy: delta, p, relabelings", [round(x, 6) if isinstance(x, float) else x for x in toy_policy()])
    print("ex11 named: rev1 reuse gain, rev2 within-context gain", [round(x, 4) for x in named()])
    print("ex12 named class I", 100 * ((0.70 - 0.55) + (0.66 - 0.52)) / 2)
