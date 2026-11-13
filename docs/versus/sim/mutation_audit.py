"""Apply each formerly invisible mutation alone and replay the fixture suite.

The estimator lives at the repository root (versus_estimator.py, versus_legacy_estimator.py, versus_bounds.py,
versus_axes.py) and docs/versus/sim/ imports it from there, so each mutant tree copies those root modules and the sim
folder into the same layout (<tree>/versus_*.py, <tree>/docs/versus/sim/), mutates one root module and runs the sim
folder's test_estimator.py, which then imports the mutant. A mutation that is not applied exactly once, or that no
test catches, stops the audit."""
import argparse
import csv
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

MUTATIONS={
 'Hoeffding m fixed at 2':('versus_bounds.py','math.log(2 * family_size / alpha)','math.log(2 * 2 / alpha)'),
 'position band exclusive':('versus_legacy_estimator.py','not POSITION_BAND[0] <= pfirst <= POSITION_BAND[1]',
                            'not POSITION_BAND[0] < pfirst < POSITION_BAND[1]'),
 'C gap >= fails':('versus_legacy_estimator.py','abs(cs[0]*ns[1]-cs[1]*ns[0])*gap_den > gap_num*ns[0]*ns[1]',
                  'abs(cs[0]*ns[1]-cs[1]*ns[0])*gap_den >= gap_num*ns[0]*ns[1]'),
 'MIN_SCENARIOS 11':('versus_legacy_estimator.py','MIN_SCENARIOS = 12','MIN_SCENARIOS = 11'),
 'cell rule round not ceil':('versus_legacy_estimator.py',"math.ceil(VALID_FRACTION * v['planned'])","round(VALID_FRACTION * v['planned'])"),
 'MIN_DECIDED 7':('versus_legacy_estimator.py','MIN_DECIDED = 8','MIN_DECIDED = 7'),
 'TRIAD_POSITION_MAX .75':('versus_legacy_estimator.py','TRIAD_POSITION_MAX = 0.70','TRIAD_POSITION_MAX = 0.75'),
 'MAX_UNDEFINED .06':('versus_legacy_estimator.py','MAX_UNDEFINED = 0.05','MAX_UNDEFINED = 0.06'),
 'MIN_RECORD_LEVEL .9':('versus_legacy_estimator.py','MIN_RECORD_LEVEL = 1.0','MIN_RECORD_LEVEL = 0.9'),
 'MIN_ERRAND_TASKS 21':('versus_legacy_estimator.py','MIN_ERRAND_TASKS = 22','MIN_ERRAND_TASKS = 21'),
}
ROOT_MODULES=('versus_estimator.py','versus_legacy_estimator.py','versus_bounds.py','versus_axes.py')


def main():
    """Keep mutant trees/logs in caller-selected scratch, portable CSV in results."""
    parser=argparse.ArgumentParser();parser.add_argument('--scratch',type=Path,required=True)
    args=parser.parse_args();src=Path(__file__).resolve().parent;root=src.parents[2];output=[]
    args.scratch.mkdir(parents=True,exist_ok=True)
    baseline=subprocess.run([sys.executable,'-m','unittest','test_estimator'],cwd=src,capture_output=True,text=True)
    if baseline.returncode:
        raise RuntimeError('baseline must pass before mutation: '+baseline.stderr)
    for index,(name,(file,old,new)) in enumerate(MUTATIONS.items()):
        with tempfile.TemporaryDirectory(prefix='mutation-',dir=args.scratch) as temp:
            tree=Path(temp)/'repo';dst=tree/'docs'/'versus'/'sim'
            shutil.copytree(src,dst,ignore=shutil.ignore_patterns('__pycache__'))
            for module in ROOT_MODULES:shutil.copy2(root/module,tree/module)
            p=tree/file;s=p.read_text()
            if s.count(old)!=1:raise RuntimeError('mutation not applied exactly once: '+name)
            p.write_text(s.replace(old,new))
            run=subprocess.run([sys.executable,'-m','unittest','-v','test_estimator'],cwd=dst,capture_output=True,text=True)
            (args.scratch/('mutation-%02d.log'%index)).write_text(run.stdout+run.stderr)
            failures=[line for line in run.stderr.splitlines() if line.startswith(('FAIL:','ERROR:'))]
            if not failures:raise RuntimeError('mutation escaped or infrastructure failed: '+name)
            row={'mutation':name,'caught':run.returncode!=0,'caught_by':' | '.join(failures[:3])}
            output.append(row);print(name,'caught',flush=True)
    with (src/'results'/'repair_mutations.csv').open('w') as f:
        w=csv.DictWriter(f,fieldnames=list(output[0]),lineterminator="\n");w.writeheader();w.writerows(output)


if __name__=='__main__':
    main()
