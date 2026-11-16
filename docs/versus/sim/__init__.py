"""These tests, as a package for a module or a test named by its dotted path: importing it installs the suite's
network guard first (docs/versus/sim/offline, which loads tests/offline/guard.py); discovery imports docs/versus/sim/offline itself."""
from . import offline  # noqa: F401
