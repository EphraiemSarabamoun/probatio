"""The suite's network contract holds in docs/versus/sim too (README, "Testing"; tests/offline/guard.py, the one guard): the
guard is installed however these tests are run, and a lookup of a public name (RFC 2606's documentation name) is
refused naming the host. tests/test_network_guard.py tests the guard itself. Nothing is looked up unless the guard is
installed first.

    python3 -m unittest discover -s docs/versus/sim -p test_network_guard.py
"""
import socket
import sys
import unittest


class Guard(unittest.TestCase):
    def test_installed_and_a_public_name_refused(self):
        guard = sys.modules.get("probatio_test_guard")
        if guard is None or not getattr(sys, "_probatio_test_guard", False):
            self.fail("the suite's network guard is not installed in this process, so nothing here looks anything up")
        with self.assertRaises(guard.NetworkRefused) as cm:
            socket.getaddrinfo("example.com", 443)
        self.assertIn("'example.com' was refused", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
