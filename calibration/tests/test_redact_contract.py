"""The harness's Redactor against the redaction cases it shares with probatio's --watch page (tests/fixtures/
redact_cases.json; live.py's ViewRedactor is held to the same cases by tests/test_watch_v4.py), so the one contract
cannot drift on either side. The fixture's secret values are built here at run time.

    python3 -m unittest discover -s calibration/tests
"""
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import redact  # noqa: E402

CASES = HERE.parent.parent / "tests" / "fixtures" / "redact_cases.json"


def values():
    return {"ANT": "sk-ant-" + "FAKE" * 6, "HF": "hf_" + "FAKE" * 8, "SECRET1": "value-" + "x" * 12,
            "SECRET2": "value-" + "x" * 30, "SHORT": "abc"}


def fill(x, v):
    if isinstance(x, str):
        for k, val in v.items():
            x = x.replace("{" + k + "}", val)
        return x
    if isinstance(x, dict):
        return {k: fill(val, v) for k, val in x.items()}
    if isinstance(x, list):
        return [fill(val, v) for val in x]
    return x


class TestSharedCases(unittest.TestCase):
    def test_every_shared_case(self):
        for case in json.loads(CASES.read_text(encoding="utf-8"))["cases"]:
            c = fill(case, values())
            r = redact.Redactor(c["environ"], c["extra"])
            got = r.clip(c["text"], c["clip"]) if "clip" in c else r(c["text"])
            self.assertEqual(got, c["out"], case["name"])


if __name__ == "__main__":
    unittest.main()
