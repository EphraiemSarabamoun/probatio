"""The versus command for the form's and the live page's tests, as the form starts it: probatio.py's own __main__ with
the real page (versus_page.py, found on the repository's path), with probatio's folder (probatio.HERE, under which the
default results folder lies) read as the working directory. The form's tests make a scratch folder probatio's folder
(they patch ui's probatio.HERE) and the form starts every dry run and run there, so the run's folder is the one the
page prepared, and nothing is written under the repository's results/. Everything else (the banks, world.py, the
modules) is the repository's.

    python3 tests/fixtures/run_versus_here.py --agent claude --versus individual="Jane Doe" ...

VERSUS_PLANT=claims plants a card whose claims do not match its estimates (its claims record dropped from the card
versus.score made, as a hand-edited folder could hold), so a test can see the page's refusal reach the form and the
live page. It ends as probatio.py's own __main__ does (install_terminal_filter, run_command): a refusal's line
displayed on stderr, exit 1. No model, no network."""
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path[:0] = [str(REPO), str(HERE)]
import probatio as P  # noqa: E402

P.HERE = Path.cwd().resolve()
if os.environ.get("VERSUS_PLANT") == "claims":
    import versus_run as VR  # noqa: E402
    made = VR.result_card

    def planted(*args):
        """versus_run.result_card's card with its claims dropped: the page must refuse it (ClaimsMismatch)."""
        card, rows = made(*args)
        card["claims"] = None
        return card, rows
    VR.result_card = planted
sys.argv = [str(REPO / "probatio.py")] + sys.argv[1:]
P.install_terminal_filter()         # as probatio.py's __main__: the terminal's backstop, then its run and refusals
P.run_command()
