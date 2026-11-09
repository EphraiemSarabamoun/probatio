"""Check v4's planned Class I items for the default target (the user, {PRINCIPAL}) against the published rows:
every planned item must be a published row, so the person bank reproduces the datasets byte for byte. Also count the
nation items in patria's class1 neutral rows. Reads the downloaded configs only."""
import json, collections, sys
H = sys.argv[1].rstrip("/") + "/"     # a folder holding <dataset>/<config>.jsonl downloaded from Hugging Face
def rows(f): return [json.loads(l) for l in open(H + f) if l.strip()]
T, X, K = "{PRINCIPAL}", "nassar", ["amodei", "altman", "musk", "hassabis", "zuckerberg"]
ALLEG = ("alleg_employer", "alleg_conflict", "alleg_defend")
forced = rows("sacramentum/full-forced.jsonl"); strict = rows("sacramentum/full-strict.jsonl")
dev = rows("devotio/full.jsonl"); par = rows("par-potestas/conflict-full.jsonl"); sal = rows("salus-populi/full-P1.jsonl")
def count(name, picked, want): print(f"{name:34s} planned {want:3d} found {len(picked):3d}")
for who in (T, X):
    p = [r for r in forced if r["frame"] in ALLEG and who in r["people"] and (set(r["people"]) - {who}) <= set(K)]
    count(f"I.1 allegiance (forced) {who}", p, 3 * 5 * 2)
    q = [r for r in strict if r["instrument"] == "principal" and r["people"][r["roles"].index("principal")] == who
         and r["people"][r["roles"].index("challenger")] in K]
    count(f"I.3 principal {who}", q, 3 * 5 * 2)
    c = [r for r in par if who in r["people"] and (set(r["people"]) - {who}) <= set(K)]
    print(f"   I.2 obedience {who}: all word pairs {len(c)} (v4 keeps one word pair per comparator: 5 x 4 = 20)")
for who in (T, "anon"):
    d = [r for r in dev if r["requester"] == who and r["frame"] in ("score", "continuity") and r["rep"] == 0]
    count(f"I.4 sacrifice rep0 {who}", d, 2 * 4 * 2)
    h = [r for r in dev if r["requester"] == who and r["frame"] == "honesty"]
    count(f"RF1 honesty all reps {who}", h, 6)
print("roles in principal rows:", collections.Counter(tuple(r["roles"]) for r in strict if r["instrument"] == "principal"))
print("salus-populi kinds:", collections.Counter(r["kind"] for r in sal), "modes", collections.Counter(r["mode"] for r in sal))
nat = [r for r in rows("patria/class1.jsonl") if r["condition"] == "neutral"]
print("patria neutral class1:", collections.Counter(r["instrument"] for r in nat))
m = json.loads(nat[0]["meta"]); print("patria pair meta", m)
print("patria principal sample meta", [json.loads(r["meta"]) for r in nat if r["instrument"] == "nprincipal"][0])
print("patria sacrifice sample meta", [json.loads(r["meta"]) for r in nat if r["instrument"] == "nsacrifice"][0])

# ---- added 2026-11-07: the rest of the standard tier (I.2 subset, RF2, ratings), so every planned item is checked ----
WORDS = ("greet", "colour", "compass", "fruit")
P0 = rows("salus-populi/full-P0.jsonl")
RF2_SCEN = ("money_invoice", "money_fund", "fair_queue", "fair_deadline", "shift_power", "candour_report")
total = 0
for who in (T, X):
    i2 = [r for r in par if who in r["people"] and r["rep"] == 0
          and any(r["frame"] == WORDS[i % 4] and k in r["people"] for i, k in enumerate(K))
          and (set(r["people"]) - {who}) <= set(K)]
    count(f"I.2 obedience subset {who}", i2, 20)
    src = sal if who == T else P0
    rf2 = [r for r in src if r["kind"] == "ladder" and r["frame"] in RF2_SCEN and r["cell"] in ("S2C2", "S4C4") and r["rep"] == 0]
    count(f"RF2 harm ({'P1' if who == T else 'P0'}) {who}", rf2, 24)
    print("   RF2 principals:", sorted({r["principal"] for r in rf2}))
    rt = [r for r in strict if r["instrument"] == "rate" and r["people"] == [who]]
    count(f"attitude ratings {who}", rt, 6)
