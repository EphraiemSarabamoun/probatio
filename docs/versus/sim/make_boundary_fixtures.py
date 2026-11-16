"""Freeze full-grid boundary tables and expected guard/component records."""
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # the repository root: versus_estimator and its modules
import versus_estimator as E
import boundary_tables as B
from fixture_io import pack_rows
from synthetic import generate


def main():
    """Write count-boundary fixtures without redundant bootstrap output."""
    cases=[]
    for name,metric,rows,parts in B.boundary_cases():
        _,ps=E.prepare(rows,parts)
        key=metric if metric.startswith('triad/') else 'individual:organization/'+metric
        cases.append({'name':name,'key':key,'participants':parts,'row_table':pack_rows(rows),
                      'eligibility':ps['names:on'][key]['eligibility']})
    (Path(__file__).parent/'fixtures'/'boundaries.json').write_text(json.dumps(cases,separators=(',',':'))+'\n')
    save_replicate_boundaries()


def save_replicate_boundaries():
    """Pin adjacent defined-replicate counts on one complete standard table."""
    parts=E.TYPES[:2];rows=generate(parts,tier='standard',adversary='deployer')
    _,ps=E.prepare(rows,parts)
    claims={k:v for k,v in ps['names:on'].items() if v['metric'] in ('rho','kappa')}
    output=[]
    for defined in (9500,9499):
        boot={k:[p['estimate']]*defined+[None]*(10000-defined) for k,p in claims.items()}
        output.append({'name':'defined_%d'%defined,'row_table':pack_rows(rows),'participants':parts,
                       'defined':defined,'replicates':10000,'expected':E.decide_claims(claims,boot,tier='standard')})
    (Path(__file__).parent/'fixtures'/'replicate_boundaries.json').write_text(json.dumps(output,separators=(',',':'))+'\n')


if __name__=='__main__':
    main()
