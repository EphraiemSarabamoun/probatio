"""probatio's tests. Importing this package (a module or a test named by its dotted path, tests.test_x) installs the
suite's network guard first (tests/offline/guard.py); discovery imports tests/offline itself."""
from . import offline  # noqa: F401
