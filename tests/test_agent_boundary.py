"""Checked callback returns become runner-owned state, with plain setup refusals and one malformed-call stop."""
import contextlib
import io
import json
import unittest
from collections import namedtuple
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tests import test_agent_extension as helper
from tests.test_versus_fixture import full_bank
import agent_registry as AR
import probatio as P
import versus_run as VR


class Boundary(unittest.TestCase):
    """Reuse the isolated synthetic installation without inheriting its existing test methods."""
    def setUp(self):
        self.fixture = helper.Extension(methodName="runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def registry(self, **changes):
        original = P.agents()
        registry = AR.Registry()
        for spec in original.values():
            registry.register(replace(spec, **changes) if spec.id == "sample" else spec)
        return registry

    def comparison_words(self):
        return ["--agent", "sample", "--versus", "individual=Jane Doe", "--versus", "organization=Acme Corp",
                "--tier", "quick", "--memory", "on", "--jobs", "1", "--workdir", str(self.fixture.work),
                "--config-dir", str(self.fixture.config), "--dry-run"]

    def test_unknown_rates_profiles_refuse_before_either_runner_computes_an_estimate(self):
        bank = full_bank()
        for profile in ("bogus", ""):
            registry = self.registry(families=("v4", "versus", "rescore"), classes=("I", "II"),
                                     rates_profile=lambda model, effort, value=profile: (value, "fixture rates"))
            commands = (self.fixture.arguments() + ["--dry-run"], self.comparison_words())
            for words in commands:
                with self.subTest(profile=profile, comparison="--versus" in words), \
                        mock.patch.object(P, "agents", return_value=registry), \
                        mock.patch.object(VR, "load_bank", return_value=bank), \
                        mock.patch.object(P, "expected_seconds", side_effect=AssertionError("estimated bad rates")) \
                        as estimate:
                    code, _, error = self.fixture.command(*words)
                self.assertEqual(code, 2, error)
                self.assertEqual(len(error.splitlines()), 1)
                self.assertIn("Sample rates_profile:", error)
                self.assertIn("unknown timing profile", error)
                estimate.assert_not_called()
        self.assertEqual(self.fixture.lines("sample-calls.jsonl"), [])

    def test_rates_profile_string_subclasses_are_plain_shape_refusals_on_both_runners(self):
        """Only a plain string reaches the pricing lookup, including when a subclass cannot be hashed."""
        class Profile(str):
            pass

        class UnhashableProfile(str):
            __hash__ = None

        for profile in (Profile("sonnet"), UnhashableProfile("sonnet")):
            registry = self.registry(families=("v4", "versus", "rescore"), classes=("I", "II"),
                                     rates_profile=lambda model, effort, value=profile: (value, "fixture rates"))
            for words in (self.fixture.arguments() + ["--dry-run"], self.comparison_words()):
                with self.subTest(profile_type=type(profile).__name__, versus="--versus" in words), \
                        mock.patch.object(P, "agents", return_value=registry), \
                        mock.patch.object(VR, "load_bank", return_value=full_bank()), \
                        mock.patch.object(P, "expected_seconds", side_effect=AssertionError("estimated bad rates")) \
                        as estimate:
                    code, _, error = self.fixture.command(*words)
                self.assertEqual(code, 2, error)
                self.assertEqual(error.splitlines(),
                                 ["Sample rates_profile: ValueError: invalid rates_profile return value"])
                estimate.assert_not_called()
        self.assertEqual(self.fixture.lines("sample-calls.jsonl"), [])

    def test_installed_rates_profiles_and_unknown_are_accepted_at_the_boundary(self):
        for profile in [None, *P.pricing()["profiles"]]:
            registry = self.registry(rates_profile=lambda model, effort, value=profile: [value, "fixture rates"])
            with self.subTest(profile=profile), mock.patch.object(P, "agents", return_value=registry):
                self.assertEqual(P.agent_callback("sample", "rates_profile", None, None), (profile, "fixture rates"))

    def test_immutable_options_are_copied_before_config_and_setup_changes(self):
        fields = (*AR.OPTION_FIELDS, "revision", "adapter_state")
        TupleOptions = namedtuple("TupleOptions", fields)

        class FrozenOptions:
            def __init__(self, values):
                for name, value in values.items():
                    object.__setattr__(self, name, value)

            def __setattr__(self, name, value):
                raise TypeError("immutable fixture options")

        wrong = self.fixture.tmp / "callback-config"
        wrong.mkdir()
        for kind in ("namedtuple", "frozen"):
            state, features, seen = {"nested": ["adapter-owned"]}, ["declared"], []
            values = dict(model="sample-model", effort=None, workdir=str(self.fixture.work), config_dir=wrong,
                          config_source="default", features=features, revision="retained", adapter_state=state)
            original = TupleOptions(**values) if kind == "namedtuple" else FrozenOptions(values)

            def options(args):
                return original

            def setup(opts, out, family):
                self.assertIsInstance(opts, SimpleNamespace)
                self.assertIsNot(opts, original)
                self.assertIs(opts.adapter_state, state)
                self.assertEqual(opts.revision, "retained")
                self.assertEqual((opts.config_dir, opts.config_source), (self.fixture.config, "option"))
                opts.features.append("setup-owned")
                opts.setup_marker = family
                seen.append(opts)

            def call(prompt, condition, opts, server=None):
                self.assertEqual((opts.revision, opts.setup_marker), ("retained", "v4"))
                self.assertIs(opts.adapter_state, state)
                self.assertEqual(opts.features, ["declared", "setup-owned"])
                return "A", None, "sample-model"

            registry = self.registry(options=options, setup_run=setup, call=call)
            with self.subTest(kind=kind), mock.patch.object(P, "agents", return_value=registry):
                code, _, error = self.fixture.command(*self.fixture.arguments(self.fixture.tmp / kind))
            self.assertEqual(code, 0, error)
            self.assertEqual(len(seen), 1)
            self.assertEqual({name: getattr(original, name) for name in fields}, values)
            self.assertIs(original.features, features)
            self.assertEqual(features, ["declared"])
            self.assertEqual(state, {"nested": ["adapter-owned"]})
            self.assertEqual((original.config_dir, original.config_source), (wrong, "default"))

    def test_missing_or_raising_required_option_attributes_refuse_plainly(self):
        good = dict(model="sample-model", effort=None, workdir=str(self.fixture.work),
                    config_dir=self.fixture.config, config_source="option", features=[])

        class BadDescriptor:
            @property
            def workdir(self):
                raise RuntimeError("unreadable fixture attribute")

        values = []
        for field in AR.OPTION_FIELDS:
            fields = dict(good)
            del fields[field]
            values.append((field, SimpleNamespace(**fields)))
        descriptor = BadDescriptor()
        for name, value in good.items():
            if name != "workdir":
                setattr(descriptor, name, value)
        values.append(("descriptor", descriptor))
        for label, value in values:
            registry = self.registry(options=lambda args, result=value: result)
            with self.subTest(label=label), mock.patch.object(P, "agents", return_value=registry):
                code, text, error = self.fixture.command(*self.fixture.arguments(), "--dry-run")
            self.assertEqual(code, 2)
            self.assertEqual(text, "")
            self.assertEqual(len(error.splitlines()), 1)
            self.assertIn("Sample options:", error)
        self.assertEqual(self.fixture.lines("sample-calls.jsonl"), [])

    def test_normalized_containers_are_owned_while_adapter_extra_state_is_retained(self):
        spec = P.agent_spec("sample")
        identity = {"nested": [{"revision": "one"}]}
        result = AR.invoke(spec, "identity", function=lambda: identity)
        result["nested"][0]["revision"] = "changed"
        self.assertEqual(identity, {"nested": [{"revision": "one"}]})
        extra = {"adapter": ["untouched"]}
        candidates = [{"path": "fixture", "claude_md": {"bytes": 4, "title": "Title", "extra": extra},
                       "extra": extra}]
        copied = AR.invoke(spec, "memory_candidates", function=lambda: candidates)
        self.assertIsNot(copied, candidates)
        self.assertIsNot(copied[0], candidates[0])
        self.assertIsNot(copied[0]["claude_md"], candidates[0]["claude_md"])
        self.assertIs(copied[0]["extra"], extra)
        self.assertIs(copied[0]["claude_md"]["extra"], extra)
        copied[0]["path"], copied[0]["claude_md"]["title"] = "changed", "Changed"
        self.assertEqual((candidates[0]["path"], candidates[0]["claude_md"]["title"]), ("fixture", "Title"))
        for hook in ("preflight_lines", "report_lines"):
            lines = ["original"]
            result = AR.invoke(spec, hook, function=lambda: lines)
            result.append("owned")
            self.assertEqual(lines, ["original"])
        for hook, value in (("call", ["A", None, "sample-model"]), ("rates_profile", [None, "unknown"])):
            result = AR.invoke(spec, hook, function=lambda: value)
            self.assertIsInstance(result, tuple)
            before = tuple(value)
            value[0] = "changed"
            self.assertEqual(result, before)
        paths = ["fixture"]
        result = AR.invoke(spec, "memory_files", function=lambda: paths)
        result.append(Path("owned"))
        self.assertEqual(paths, ["fixture"])
        original = SimpleNamespace(model=None, effort=None, workdir="fixture", config_dir=None,
                                   config_source="default", features=["declared"], adapter_state=extra)
        owned = AR.invoke(spec, "options", function=lambda: original)
        self.assertIsNot(owned, original)
        self.assertIs(owned.adapter_state, extra)
        owned.features.append("owned")
        self.assertEqual(original.features, ["declared"])
        self.assertEqual(extra, {"adapter": ["untouched"]})

    def test_both_builtin_ids_stop_after_first_nontext_reply_and_keep_earlier_records(self):
        roster = P.resolve_target(self.fixture.bank, "organization", "Mozilla")
        calls = P.plan_v4(self.fixture.bank, roster, "smoke", ("I",), ("with",))[:2]
        self.assertEqual(len(calls), 2)
        cases = (("integer", 42), ("object", {"reply": "A"}), ("list", ["A"]), ("boolean", True))
        for agent, label in (("claude", "Claude Code"), ("codex", "Codex")):
            for kind, reply in cases:
                out = self.fixture.tmp / (agent + "-" + kind)
                out.mkdir()
                prior = dict(condition="with", dataset="fixture", config="fixture", id="earlier-record",
                             reply="A", error=None, model=None, seconds=0)
                saved = (json.dumps(prior) + "\n").encode()
                (out / "replies.jsonl").write_bytes(saved)
                attempts = []

                def malformed(*args, **kwargs):
                    attempts.append(1)
                    return reply, None, None

                stdout, stderr = io.StringIO(), io.StringIO()
                with self.subTest(agent=agent, kind=kind), mock.patch.dict(P.CALLS, {agent: malformed}), \
                        contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr), \
                        self.assertRaises(SystemExit) as stopped:
                    P.run_calls(calls, agent, None, out, 1)
                self.assertEqual(stopped.exception.code, 2)
                self.assertEqual(len(attempts), 1)
                self.assertEqual(stdout.getvalue(), "")
                self.assertEqual(stderr.getvalue().splitlines(),
                                 [label + " call: ValueError: invalid call return value"])
                self.assertEqual((out / "replies.jsonl").read_bytes(), saved)


if __name__ == "__main__":
    unittest.main()
