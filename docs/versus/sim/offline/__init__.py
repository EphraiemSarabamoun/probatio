"""The suite's network guard for docs/versus/sim/ (tests/offline/guard.py, the one guard: no test reaches the network beyond this
machine). unittest's discovery of docs/versus/sim imports this package before any test runs, and docs/versus/sim/__init__.py
imports it for a module or a test named by its dotted path. Loaded once per process under one name."""
import importlib.util
import sys
from pathlib import Path

if "probatio_test_guard" not in sys.modules:
    _guard = Path(__file__).resolve().parents[4] / "tests" / "offline" / "guard.py"
    _spec = importlib.util.spec_from_file_location("probatio_test_guard", _guard)
    sys.modules[_spec.name] = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(sys.modules[_spec.name])
sys.modules["probatio_test_guard"].install()
