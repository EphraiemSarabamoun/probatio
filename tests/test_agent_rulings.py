"""Second-round extension refusals and comparison capabilities, entirely in process on owned fixture data."""
import json
import unittest
from dataclasses import replace
from unittest import mock

from tests import test_agent_extension as helper
from tests.test_versus_fixture import full_bank
import agent_registry as AR
import ordo
import probatio as P
import versus as VS
import versus_run as VR


VERSUS_FIXTURE = helper.REPO / "tests" / "fixtures" / "sample_versus_extension.py"


class Rulings(unittest.TestCase):
    """Compose the existing isolated extension harness without inheriting or duplicating its test methods."""
    def setUp(self):
        self.fixture = helper.Extension(methodName="runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.tmp, self.work, self.config = self.fixture.tmp, self.fixture.work, self.fixture.config

    def command(self, *words):
        result = self.fixture.command(*words)
        for text in result[1:]:
            self.assertNotIn("None", text)
            self.assertNotIn("Traceback", text)
        return result

    def registry(self, **changes):
        """Keep builtin objects intact while varying one synthetic declaration or callback."""
        original = P.agents()
        registry = AR.Registry()
        for spec in original.values():
            registry.register(replace(spec, **changes) if spec.id == "sample" else spec)
        return registry

    def test_every_setup_callback_exception_and_bad_return_refuse_before_calls(self):
        class Stop(BaseException):
            pass
        def broken(*args, **kwargs):
            raise Stop("fixture callback failed")
        bad = {"options": None, "version": None, "memory_files": None, "identity": [],
               "setup_run": "unexpected", "rates_profile": None, "config_dir": 3}
        for name, value in bad.items():
            for kind in ("exception", "shape"):
                function = broken if kind == "exception" else lambda *args, value=value, **kwargs: value
                registry = self.registry(**{name: function})
                out = self.tmp / (name + "-" + kind)
                words = self.fixture.arguments(out)
                if name == "config_dir":
                    start = words.index("--config-dir")
                    del words[start:start + 2]
                with self.subTest(callback=name, kind=kind), mock.patch.object(P, "agents", return_value=registry):
                    code, _, error = self.command(*words)
                    self.assertEqual(code, 2, error)
                    self.assertEqual(len(error.splitlines()), 1, error)
                    self.assertIn("Sample " + name + ":", error)
                    self.assertEqual(self.fixture.lines("sample-calls.jsonl"), [])

    def test_call_exception_is_retried_and_recorded_as_a_failed_call(self):
        attempts = []
        class Stop(BaseException):
            pass
        def broken(*args, **kwargs):
            attempts.append(1)
            raise Stop("fixture call failed")
        out = self.tmp / "failed-calls"
        with mock.patch.object(P, "agents", return_value=self.registry(call=broken)):
            code, _, error = self.command(*self.fixture.arguments(out))
        self.assertEqual(code, 1, error)
        self.assertIn("calls in a row failed", error)
        records = [json.loads(line) for line in (out / "replies.jsonl").read_text().splitlines()]
        self.assertEqual(len(records), P.MAX_ERRORS_IN_A_ROW)
        self.assertEqual(len(attempts), 2 * len(records))
        self.assertTrue(all("Sample call:" in row["error"] and "fixture call failed" in row["error"]
                            and row["reply"] is None for row in records))

    def test_bad_call_return_stops_plainly_and_keeps_written_records(self):
        out, attempts, saved = self.tmp / "bad-call-return", [], []
        def wrong(*args, **kwargs):
            attempts.append(1)
            if len(attempts) == 1:
                saved.append((out / "run.json").read_bytes())
                return "A", None, "sample-model"
            return ["bad return"]
        with mock.patch.object(P, "agents", return_value=self.registry(call=wrong)):
            code, _, error = self.command(*self.fixture.arguments(out))
        self.assertEqual(code, 2, error)
        self.assertEqual(sum("Sample call:" in line for line in error.splitlines()), 1, error)
        self.assertEqual(len(attempts), 2)
        self.assertEqual((out / "run.json").read_bytes(), saved[0])
        records = [json.loads(line) for line in (out / "replies.jsonl").read_text().splitlines()]
        self.assertEqual(len(records), 1)
        self.assertIsNone(records[0]["error"])
        self.assertTrue(P.file_there(out / "launches.jsonl"))

    def test_removed_extension_rescore_names_the_missing_installation_and_writes_nothing(self):
        out = self.tmp / "removed-extension"
        code, _, error = self.command(*self.fixture.arguments(out))
        self.assertEqual(code, 0, error)
        before = {path.name: path.read_bytes() for path in out.iterdir()}
        self.fixture.path.unlink()
        P._AGENT_REGISTRIES.pop(str(self.tmp), None)
        code, _, error = self.command("--rescore", str(out))
        self.assertEqual(code, 1)
        self.assertEqual(len(error.splitlines()), 1)
        self.assertIn("sample", error)
        self.assertIn("extension is not installed", error)
        self.assertEqual({path.name: path.read_bytes() for path in out.iterdir()}, before)

    def test_no_effort_header_and_missing_config_refusal_never_name_an_unset_value(self):
        code, text, error = self.command(*self.fixture.arguments(), "--dry-run")
        self.assertEqual(code, 0, error)
        self.assertNotIn("effort", text.splitlines()[0])
        words = self.fixture.arguments()
        words[words.index("--config-dir") + 1] = str(self.tmp / "missing-config")
        code, text, error = self.command(*words, "--dry-run")
        self.assertNotEqual(code, 0)
        self.assertEqual(text, "")
        self.assertEqual(len(error.splitlines()), 1)
        self.assertIn("--config-dir", error)
        self.assertNotIn("CLAUDE_CONFIG_DIR", error)

    def comparison_fixture(self, classes=("I", "II")):
        source = VERSUS_FIXTURE.read_text()
        self.fixture.path.write_text(source.replace('CLASSES = ("I", "III")', "CLASSES = " + repr(classes)))
        P._AGENT_REGISTRIES.pop(str(self.tmp), None)

    def comparison_words(self, tier="quick", out=None):
        words = ["--agent", "sample", "--versus", "individual=Jane Doe", "--versus", "organization=Acme Corp",
                 "--tier", tier, "--memory", "on", "--jobs", "1", "--workdir", str(self.work),
                 "--config-dir", str(self.config)]
        return words + (["--out", str(out)] if out else [])

    def test_real_comparison_plans_use_their_true_classes_at_both_tiers(self):
        self.comparison_fixture()
        bank, ob = full_bank(), ordo.load_ordo_bank()
        names = {"individual": "Jane Doe", "organization": "Acme Corp"}
        plans = {tier: VS.plan(bank, names, tier, {"memory": "on"}, ob) for tier in ("quick", "standard")}
        self.assertNotIn("III", {P.call_class(call) for call in plans["quick"].calls})
        class3 = [call for call in plans["standard"].calls if call.get("meta", {}).get("frame") == "class3"]
        self.assertTrue(class3)
        self.assertTrue(all(P.call_class(call) == "III" for call in class3))
        for classes in (("I", "III"), ("I", "II")):
            with mock.patch.object(P, "agents", return_value=self.registry(classes=classes)):
                for tier, plan in plans.items():
                    with self.subTest(classes=classes, tier=tier):
                        if classes == ("I", "II") and tier == "quick":
                            P.check_agent_calls("sample", plan.calls)
                        else:
                            missing = "II" if classes == ("I", "III") else "III"
                            with self.assertRaises(SystemExit) as stop:
                                P.check_agent_calls("sample", plan.calls)
                            self.assertIn("Class " + missing, str(stop.exception))
        self.assertEqual(self.fixture.lines("sample-calls.jsonl"), [])

    def test_unknown_comparison_time_has_no_hint_and_runs_the_callable(self):
        self.comparison_fixture()
        bank = full_bank()
        out = self.tmp / "comparison"
        with mock.patch.object(VR, "load_bank", return_value=bank):
            code, text, error = self.command(*self.comparison_words(), "--dry-run")
            self.assertEqual(code, 0, error)
            self.assertIn("unknown (estimate unknown)", text)
            self.assertNotIn("to shorten:", text)
            self.assertNotIn("Sonnet", text)
            self.assertEqual(self.fixture.lines("sample-calls.jsonl"), [])
            code, text, error = self.command(*self.comparison_words(out=out))
            self.assertEqual(code, 0, error)
        self.assertTrue(P.file_there(out / VS.RUN_FILE))
        self.assertTrue(P.file_there(out / "scorecard.json"))
        calls = self.fixture.lines("sample-calls.jsonl")
        self.assertTrue(calls)
        self.assertTrue(any(call["episode"] for call in calls))
        replies = [json.loads(line) for line in (out / "replies.jsonl").read_text().splitlines()]
        self.assertEqual(len(calls), len(replies))
        self.assertTrue(all(row["error"] is None for row in replies))


if __name__ == "__main__":
    unittest.main()
