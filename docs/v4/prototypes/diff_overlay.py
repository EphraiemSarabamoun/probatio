"""Compare one task's anulus v0.5 realistic row with its v0.4 unaudited-realistic and audited-disclosed rows."""
import json, sys, difflib
v4 = [json.loads(l) for l in open(sys.argv[1]) if l.strip()]
v5 = [json.loads(l) for l in open(sys.argv[2]) if l.strip()]
print("v0.5 keys", sorted(v5[0].keys()))
from collections import Counter
print(Counter((r['principal_slot'], r['arm'], r['realism'], r['level']) for r in v5))
def pick(rows, **kw):
    return [r for r in rows if all(r.get(k) == v for k, v in kw.items())][0]
seed = 625
a = pick(v5, seed=seed, beneficiary='principal', principal_slot='you', arm='neutral', realism='realistic')
b = pick(v4, seed=seed, beneficiary='principal', principal_slot='you', observation='unaudited', realism='realistic')
c = pick(v4, seed=seed, beneficiary='principal', principal_slot='you', observation='audited', realism='disclosed')
for x, y, lab in ((a, b, 'v05 realistic -> v04 unaudited'), (b, c, 'v04 unaudited -> v04 audited+disclosed')):
    print('=====', lab)
    for line in difflib.unified_diff(x['prompt'].split('\n'), y['prompt'].split('\n'), lineterm='', n=0):
        print(line[:400])
    wx, wy = json.loads(x['world']), json.loads(y['world'])
    for k in sorted(set(wx) | set(wy)):
        if wx.get(k) != wy.get(k):
            print('world key differs:', k, json.dumps(wx.get(k))[:300], '||', json.dumps(wy.get(k))[:300])
    for k in x:
        if k not in ('prompt', 'world', 'id', 'prompt_sha256_public', 'world_sha256') and x[k] != y.get(k):
            print('field differs:', k, repr(x[k])[:200], '||', repr(y.get(k))[:200])
print('teammates:', Counter(json.loads(r['world'])['roles']['teammate']['name'] for r in v5))
