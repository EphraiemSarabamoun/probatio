"""Command-level wiring checks for capability notices, comparison refusal and the resolved config folder."""
import json
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from tests import test_agent_extension as helper
from tests.test_versus_fixture import full_bank
import agent_registry as AR
import probatio as P
import versus_run as VR


class MutationGuards(unittest.TestCase):
    """Compose the isolated fixture harness; each test reaches the public command entry point."""
    def setUp(self):
        self.fixture = helper.Extension(methodName="runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def dry_run(self, tier):
        words = self.fixture.arguments()
        words[words.index("--tier") + 1] = tier
        code, text, error = self.fixture.command(*words, "--dry-run")
        self.assertEqual((code, error), (0, ""), text)
        return text.splitlines()

    def test_comparison_command_refuses_undeclared_standard_class_before_calls(self):
        source = (helper.REPO / "tests" / "fixtures" / "sample_versus_extension.py").read_text()
        self.fixture.path.write_text(source.replace('CLASSES = ("I", "III")', 'CLASSES = ("I", "II")'))
        P._AGENT_REGISTRIES.pop(str(self.fixture.tmp), None)
        words = ["--agent", "sample", "--versus", "individual=Jane Doe", "--versus", "organization=Acme Corp",
                 "--tier", "standard", "--memory", "on", "--jobs", "1", "--workdir", str(self.fixture.work),
                 "--config-dir", str(self.fixture.config)]
        # This is the real parser, planner and folder selector. The sentinel marks the first possible model-call
        # boundary, so a missing selector guard is caught without executing an unsupported standard comparison.
        with mock.patch.object(VR, "load_bank", return_value=full_bank()), \
                mock.patch.object(P, "run_calls", side_effect=AssertionError("unsupported comparison reached calls")) \
                as calls:
            code, text, error = self.fixture.command(*words)
        self.assertEqual(code, 1, error or text)
        self.assertEqual(text, "")
        self.assertEqual(error.splitlines(), ["Sample does not support Class III in this run."])
        calls.assert_not_called()
        self.assertEqual(self.fixture.lines("sample-calls.jsonl"), [])
        self.assertEqual(self.fixture.lines("sample-setup.jsonl"), [])

    def test_capability_notice_follows_the_command_header(self):
        lines = self.dry_run("smoke")
        header = [i for i, line in enumerate(lines) if line.startswith("probatio v4:")]
        notice = [i for i, line in enumerate(lines) if line.startswith("  not run:")]
        self.assertEqual(len(header), 1)
        self.assertEqual(len(notice), 1)
        self.assertLess(header[0], notice[0])
        self.assertIn("Class II", lines[notice[0]])
        self.assertIn("Class III", lines[notice[0]])

    def test_quick_capability_notice_names_only_the_classes_that_tier_would_plan(self):
        notices = [line for line in self.dry_run("quick") if line.startswith("  not run:")]
        self.assertEqual(len(notices), 1)
        self.assertIn("Sample does not support Class II", notices[0])
        self.assertNotIn("Class III", notices[0])

    def test_options_cannot_replace_the_resolved_config_used_by_the_command(self):
        chosen = self.fixture.config
        other = self.fixture.tmp / "other-config"
        other.mkdir()
        (chosen / "revision.txt").write_text("chosen\n")
        (other / "revision.txt").write_text("other\n")
        original = P.agents()
        spec = original["sample"]
        observed = []

        def options(args):
            result = spec.options(args)
            result.config_dir, result.config_source = other, "default"
            return result

        def call(prompt, condition, opts, server=None):
            folder = Path(opts.config_dir)
            observed.append((folder, opts.config_source, (folder / "revision.txt").read_text().strip()))
            return "A", None, "sample-model"

        registry = AR.Registry()
        for item in original.values():
            registry.register(replace(item, options=options, call=call) if item.id == "sample" else item)
        out = self.fixture.tmp / "resolved-config"
        with mock.patch.object(P, "agents", return_value=registry):
            code, text, error = self.fixture.command(*self.fixture.arguments(out))
        self.assertEqual(code, 0, error or text)
        self.assertTrue(observed)
        self.assertTrue(all(value == (chosen, "option", "chosen") for value in observed), observed[:1])
        self.assertEqual(json.loads((out / "run.json").read_text())["config_dir"], str(chosen))
        self.assertIn("config folder: " + P.display(P.tilde(chosen)) + " (chosen with --config-dir)", text)
        self.assertNotIn(str(other), text)


if __name__ == "__main__":
    unittest.main()
