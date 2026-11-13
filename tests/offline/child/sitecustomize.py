"""The suite's network guard in a Python child a test starts with its environment (tests/offline/guard.py; not one
started with -I, -E or -S or with a cleaned environment, which skips this file): this folder is first on
the PYTHONPATH the guard leaves in the test process's environment, and holds nothing else, so a child's site imports
this file at startup. It installs the same hook, then runs the next sitecustomize on the path, if there is one (a
distribution's own), so a child starts as it would without the guard, but for the hook."""
import importlib.machinery
import importlib.util
import os
import sys
from pathlib import Path

_here = Path(__file__).resolve().parent
if "probatio_test_guard" not in sys.modules:
    _spec = importlib.util.spec_from_file_location("probatio_test_guard", _here.parent / "guard.py")
    sys.modules[_spec.name] = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(sys.modules[_spec.name])
sys.modules["probatio_test_guard"].install()

_rest = [p for p in sys.path if os.path.abspath(p or os.curdir) != str(_here)]
_next = importlib.machinery.PathFinder.find_spec("sitecustomize", _rest)
if _next is not None and _next.origin and Path(_next.origin).resolve() != Path(__file__).resolve():
    _module = importlib.util.module_from_spec(_next)
    sys.modules["sitecustomize"] = _module
    _next.loader.exec_module(_module)
