"""The test suite's network contract (README, "Testing"): no test is to reach the network beyond this machine. This
file is its audit aid, not a network sandbox.

One mechanism, Python's own audit hook (sys.addaudithook), sees the name lookups and connections that Python's socket
module reports as audit events (socket.getaddrinfo, gethostbyname, gethostbyaddr, getnameinfo, connect, sendto),
whichever Python library calls them; networking done natively inside the process, outside those audited calls, is
not seen. install() adds that hook to this process and makes a Python child that inherits this process's
environment, with site enabled, carry it too: it puts tests/offline/child, which holds only a sitecustomize.py that
installs the same hook, first on PYTHONPATH in this process's environment, which subprocess passes on. An audited
lookup of, or connection to, anything that is not this machine raises NetworkRefused, naming the host, inside the call that tried it; loopback (127.0.0.0/8, ::1,
localhost), the unspecified address of a passive lookup and local (AF_UNIX) sockets pass, so the form's and the live
page's tests still serve and fetch on 127.0.0.1 by design. NetworkRefused is a BaseException, so no `except
Exception` or `except OSError` in the code under test can swallow it: in the test's own process the test errors with
the host named, and a child process stops with it (its traceback naming the host, exit 1), which fails the test that
started it.

How it is reached, however the tests are run: unittest's discovery (python3 -m unittest discover -s DIR, even
narrowed by -p to one module) imports every package folder inside DIR before any test runs, so each suite folder
(tests, calibration/tests, docs/versus/sim) holds a package, offline/, whose __init__.py loads this file; a module or
a single test named by its dotted path (python3 -m unittest tests.test_x.Case.test_y) imports the suite folder's own
__init__.py first, which imports the same package. This file is loaded once per process under one name
(probatio_test_guard), and installs once. What it does not see: a child started with -I, -E or -S (they ignore
PYTHONPATH or skip site, as world.py's episodes are), a child given an environment built without this one's, a
process that is not Python (a browser, the agent CLI stand-ins' shells), and a test module run as a script
(python3 tests/test_x.py), which imports none of the suite's packages. The test of it is tests/test_network_guard.py,
whose ChildFlags is a static tripwire over the test files of the three suites: it fails on an argv literal, or a
shell command line in a script's text, holding -I, -E or -S as an unquoted option word next to a Python interpreter,
but for the ones UNGUARDED_CHILDREN names with their reasons, and misses a quoted flag, an interpreter alias, a
starred or variable option list, a cleaned environment and a child that probatio's own code starts (world.py's
episodes).
"""
import ipaddress
import os
import sys
from pathlib import Path

NAME = "probatio_test_guard"                             # the one module name every entry point loads this file as
CHILD = Path(__file__).resolve().parent / "child"        # PYTHONPATH's first entry for children: their sitecustomize
LOOPBACK_NAMES = frozenset({"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"})
HOST_EVENTS = frozenset({"socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyname_ex",
                         "socket.gethostbyaddr"})       # their first argument is the host
ADDRESS_EVENTS = frozenset({"socket.connect", "socket.connect_ex", "socket.sendto"})   # (socket, address, ...)
NAMEINFO_EVENT = "socket.getnameinfo"                    # its first argument is the address tuple
REFUSED = ("probatio's tests reach no network beyond this machine (README, Testing): {event} to {host!r} was refused; "
           "a test that needs the network is not a test of this suite")


class NetworkRefused(BaseException):
    """A lookup or a connection beyond this machine, refused inside the call that tried it. A BaseException, so the
    code under test cannot catch it as an ordinary error and carry on."""


def is_local(host):
    """Whether host names this machine: a loopback name or address, or the unspecified address (a passive lookup), or
    no host at all. An address tuple's host is its first item; a path (AF_UNIX) is local."""
    if host is None or host == "" or host == b"":
        return True
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    if not isinstance(host, str):
        return False
    name = host.strip("[]").split("%", 1)[0].lower().rstrip(".")
    if name in LOOPBACK_NAMES:
        return True
    try:
        address = ipaddress.ip_address(name)
    except ValueError:
        return False
    return address.is_loopback or address.is_unspecified


def audit(event, args):
    """The hook: refuse a lookup or a connection beyond this machine (REFUSED), let every other event pass."""
    if event in HOST_EVENTS:
        host = args[0] if args else None
    elif event in ADDRESS_EVENTS:
        address = args[1] if len(args) > 1 else None
        if not isinstance(address, tuple):              # an AF_UNIX path, or no address: this machine
            return
        host = address[0] if address else None
    elif event == NAMEINFO_EVENT:
        address = args[0] if args else None
        host = address[0] if isinstance(address, tuple) and address else address
    else:
        return
    if not is_local(host):
        raise NetworkRefused(REFUSED.format(event=event, host=host))


def install():
    """Add the hook to this process, once, and make a Python child that inherits this environment with site enabled
    carry it (CHILD first on PYTHONPATH in the environment it inherits). Returns True."""
    if getattr(sys, "_probatio_test_guard", False):
        return True
    sys.addaudithook(audit)
    sys._probatio_test_guard = True
    paths = [p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p]
    if str(CHILD) not in paths:
        os.environ["PYTHONPATH"] = os.pathsep.join([str(CHILD)] + paths)
    return True

