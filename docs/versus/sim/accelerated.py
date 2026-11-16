"""Optional NumPy replicate arithmetic on the reference estimator's exact draws."""
import numpy as np

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # the repository root: versus_estimator and its modules
import versus_legacy_estimator as E


def bootstrap_panels(panels, draws):
    """Vectorize weighted cluster means and nonlinear errand component selection."""
    output = {}
    for arm, records in panels.items():
        output[arm] = {}
        cache = {}
        for key, p in records.items():
            ids = sorted(p['values'])
            axis = (p['frame'], tuple(ids))
            if axis not in cache:
                cache[axis] = np.array([[d[p['frame']].get(s, 0) for s in ids] for d in draws], dtype=float).reshape(len(draws), len(ids))
            w = cache[axis]
            n = w.sum(axis=1)
            safe_n = np.maximum(n, 1)
            if p['frame'] != 'errand':
                values = w @ np.array([p['values'][s] for s in ids], dtype=float) / safe_n
                values[n == 0] = np.nan
            else:
                obs = np.array([p['values'][s] for s in ids], dtype=float).reshape(len(ids), 6)
                levels = (w @ obs) / safe_n[:, None]
                a, b = levels[:, :3], levels[:, 3:]
                top = np.maximum(a, b)
                ratio = np.divide(a - b, top, out=np.zeros_like(a), where=top != 0)
                events = (a[:, :2] + b[:, :2]) * n[:, None]
                ok = np.column_stack(((events + E.EPS >= E.MIN_BINARY_EVENTS) & (2*n[:, None] - events + E.EPS >= E.MIN_BINARY_EVENTS), top[:, 2] + E.EPS >= E.MIN_RECORD_LEVEL))
                used = ok.sum(axis=1)
                values = 50 * (1 + (ratio * ok).sum(axis=1) / np.maximum(used, 1))
                values[(used < E.MIN_ERRAND_COMPONENTS) | (n == 0)] = np.nan
            output[arm][key] = [None if not np.isfinite(v) else float(v) for v in values]
    return output


def make_engine(draws):
    """Cache fixed draw matrices across independent outer simulation datasets."""
    matrices = {}

    def evaluate(panels, unused_draws):
        """Return numeric replicate arrays, sharing full stable-ID weight axes."""
        output = {}
        for arm, records in panels.items():
            output[arm] = {}
            for key, p in records.items():
                ids = sorted(p['values'])
                axis = (p['frame'], tuple(ids))
                if axis not in matrices:
                    matrices[axis] = np.array([[d[p['frame']].get(s, 0) for s in ids] for d in draws], dtype=float).reshape(len(draws), len(ids))
                w = matrices[axis]
                n = w.sum(axis=1)
                safe_n = np.maximum(n, 1)
                if p['frame'] != 'errand':
                    values = w @ np.array([p['values'][s] for s in ids], dtype=float) / safe_n
                    values[n == 0] = np.nan
                else:
                    obs = np.array([p['values'][s] for s in ids], dtype=float).reshape(len(ids), 6)
                    levels = (w @ obs) / safe_n[:, None]
                    a, b = levels[:, :3], levels[:, 3:]
                    top = np.maximum(a, b)
                    ratio = np.divide(a - b, top, out=np.zeros_like(a), where=top != 0)
                    events = (a[:, :2] + b[:, :2]) * n[:, None]
                    ok = np.column_stack(((events + E.EPS >= E.MIN_BINARY_EVENTS) & (2*n[:, None] - events + E.EPS >= E.MIN_BINARY_EVENTS), top[:, 2] + E.EPS >= E.MIN_RECORD_LEVEL))
                    used = ok.sum(axis=1)
                    values = 50 * (1 + (ratio * ok).sum(axis=1) / np.maximum(used, 1))
                    values[(used < E.MIN_ERRAND_COMPONENTS) | (n == 0)] = np.nan
                output[arm][key] = values
        return output
    return evaluate


def summarize(records, boot, inflation=E.INTERVAL_INFLATION):
    """Historical summary delegates to the frozen historical reference rule."""
    bb={k:[None if not np.isfinite(v) else float(v) for v in vs] for k,vs in boot.items()}
    page=E.simultaneous(records,bb,len(records),inflation)
    out={}
    for k,r in page['metrics'].items():
        ci=r['marginal']
        out[k]={'estimate':r['estimate'], 'decision':r['decision'],
                'half_width':None if ci is None else (ci[1]-ci[0])/2,
                'eligible':not r['withheld_reasons'], 'se':r['se']}
    return out
