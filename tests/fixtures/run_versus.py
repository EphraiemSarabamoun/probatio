"""The versus command for the tests, exactly as a user runs it (probatio.py's own __main__, its exit codes and its
refusal lines), with tests/fixtures/versus_page_standin.py standing in for versus_page.py, so a test controls
the page (tests/test_versus_integration.py runs probatio.py with the real page):

    python3 tests/fixtures/run_versus.py --agent claude --versus individual="Jane Doe" ...

It puts the stand-in page in sys.modules as versus_page, then runs probatio.py as __main__ with these arguments."""
import runpy
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(HERE))
import versus_page_standin  # noqa: E402

sys.modules["versus_page"] = versus_page_standin
sys.argv = [str(REPO / "probatio.py")] + sys.argv[1:]
runpy.run_path(str(REPO / "probatio.py"), run_name="__main__")
