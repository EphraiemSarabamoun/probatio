"""The suite's network guard (guard.py beside this file: no test reaches the network beyond this machine). unittest's
discovery of tests/ imports this package before any test runs, and tests/__init__.py imports it for a module or a test
named by its dotted path. Loaded once per process under one name, so every entry point shares one hook."""
import importlib.util
import sys
from pathlib import Path

if "probatio_test_guard" not in sys.modules:
    _spec = importlib.util.spec_from_file_location("probatio_test_guard", Path(__file__).resolve().parent / "guard.py")
    sys.modules[_spec.name] = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(sys.modules[_spec.name])
sys.modules["probatio_test_guard"].install()
