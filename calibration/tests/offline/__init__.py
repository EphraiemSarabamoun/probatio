"""The suite's setup for calibration/tests/: the network guard (tests/offline/guard.py, the one guard: no test reaches
the network beyond this machine) and the resolved temp folder (tests/offline/temp.py, the one home: every temporary
folder a test makes is a real path). unittest's discovery of calibration/tests imports this package before any test
runs, and calibration/tests/__init__.py imports it for a module or a test named by its dotted path. Each is loaded
once per process under one name."""
import importlib.util
import sys
from pathlib import Path

if "probatio_test_guard" not in sys.modules:
    _guard = Path(__file__).resolve().parents[3] / "tests" / "offline" / "guard.py"
    _spec = importlib.util.spec_from_file_location("probatio_test_guard", _guard)
    sys.modules[_spec.name] = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(sys.modules[_spec.name])
sys.modules["probatio_test_guard"].install()

if "probatio_test_temp" not in sys.modules:        # the one resolved temp folder (tests/offline/temp.py)
    _temp = Path(__file__).resolve().parents[3] / "tests" / "offline" / "temp.py"
    _spec = importlib.util.spec_from_file_location("probatio_test_temp", _temp)
    sys.modules[_spec.name] = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(sys.modules[_spec.name])
sys.modules["probatio_test_temp"].resolve_temp()
