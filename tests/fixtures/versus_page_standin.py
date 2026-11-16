"""A stand-in for versus_page.py in the versus command's tests, which control the page through it: the two functions
the command calls, files(card, rows) and answer_lines(card), pure and small, so the command's tests exercise the
command and not the page. It writes the card itself (versus.card_json) and the rows, and prints a few lines whose
names pass through probatio's display(). run_versus.py and the in-process tests install it under the module name
versus_page (sys.modules), never on a path where it could shadow the real one. No model, no network."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import probatio as P  # noqa: E402
import versus as VS  # noqa: E402

STANDIN = "stand-in page"


def answer_lines(card):
    """A few lines naming the run: the names as rendered, the tier, and whether the estimator gave a card."""
    parties = card["identity"]["participants"]
    names = ", ".join(P.display(parties[t]["rendered"]) for t in VS.TYPES if t in parties)
    state = "refused: " + P.display(card["refused"]) if card.get("refused") else "estimated"
    return [f"versus ({STANDIN}): {names}", f"  {card['tier']} tier, {state}"]


def files(card, rows):
    """scorecard.json (the card), scorecard.md (the answer lines) and rows.jsonl (the estimator's rows)."""
    return {"scorecard.json": VS.card_json(card), "scorecard.md": "\n".join(answer_lines(card)) + "\n",
            "rows.jsonl": "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows)}
