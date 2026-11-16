"""The suite's temp folder, resolved at creation: one home for every test module's temporary folders.

macOS names its default temp folder (/var/folders/...) and /tmp through links into /private, so a folder a test makes
there and the same folder as the code under test resolves it (a run's results folder, the form's shown and placed
paths, a scorecard line) are two different strings, and a test comparing them fails on a Mac though nothing is wrong.
resolve_temp makes tempfile's own folder its real path, once, before any test module is imported (tests/offline's
package runs it beside the network guard, for discovery and for a module or a test named by its dotted path), so
tempfile.mkdtemp, TemporaryDirectory, NamedTemporaryFile and gettempdir give resolved paths in every module. TMPDIR,
when the environment sets it, is given the same real path, so a child the test starts with that environment makes its
folders there too. A child started with an environment of its own keeps its own temp folder."""
import os
import tempfile


def resolve_temp():
    """tempfile's folder (and TMPDIR, when set) made its real path; that path. Idempotent."""
    real = os.path.realpath(tempfile.gettempdir())
    tempfile.tempdir = real
    if os.environ.get("TMPDIR") not in (None, real):
        os.environ["TMPDIR"] = real
    return real
