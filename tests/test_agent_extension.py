"""A scratch extension through the public command, planning, scoring and resume paths; no external agent runs."""
import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import probatio as P  # noqa: E402
import agent_registry as AR  # noqa: E402
import ui as U  # noqa: E402
import versus as VS  # noqa: E402
import versus_run as VR  # noqa: E402

BANK = REPO / "tests" / "fixtures" / "bank_small"
FIXTURE = REPO / "tests" / "fixtures" / "sample_extension.py"


class Extension(unittest.TestCase):
    """One installation and workdir per test, with all process and network entry points forbidden."""
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="extension-"))
        self.work, self.config = self.tmp / "work", self.tmp / "config"
        self.work.mkdir()
        self.config.mkdir()
        self.path = self.tmp / "probatio_ext.py"
        shutil.copyfile(FIXTURE, self.path)
        self.bank = P.load_bank(BANK, None)
        self.before_path = list(sys.path)
        self.addCleanup(self.clean)
        for patch in (mock.patch.object(P, "HERE", self.tmp), mock.patch.object(P, "BANK", BANK),
                      mock.patch.object(P, "BANK_MANIFEST_SHA256", self.bank.sha256),
                      mock.patch.object(U, "bank", return_value=self.bank),
                      mock.patch.object(P.subprocess, "run", side_effect=AssertionError("unexpected CLI call")),
                      mock.patch.object(P.shutil, "which", side_effect=AssertionError("unexpected CLI lookup")),
                      mock.patch.object(P.urllib.request, "urlopen", side_effect=AssertionError("unexpected network"))):
            patch.start()
            self.addCleanup(patch.stop)
        U.defaults.cache_clear()

    def clean(self):
        P._AGENT_REGISTRIES.pop(str(self.tmp), None)
        U.defaults.cache_clear()
        sys.path[:] = self.before_path
        shutil.rmtree(self.tmp)

    def command(self, *args):
        """The real console wrapper, captured so a plain refusal's actual code and lines can be asserted."""
        out, err = io.StringIO(), io.StringIO()
        code = 0
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                P.run_command(list(args))
            except SystemExit as stop:
                code = stop.code
        self.assertNotIn("None", out.getvalue())
        self.assertNotIn("None", err.getvalue())
        return code, out.getvalue(), err.getvalue()

    def arguments(self, out=None):
        words = ["--agent", "sample", "--target-type", "organization", "--target", "Mozilla", "--tier", "smoke",
                 "--bank", str(BANK), "--memory", "on", "--workdir", str(self.work), "--config-dir", str(self.config),
                 "--jobs", "1"]
        return words + (["--out", str(out)] if out else [])

    def lines(self, name):
        path = self.work / name
        return [json.loads(line) for line in path.read_text().splitlines()] if P.there(path) else []

    def run_sample(self, out=None):
        out = out or self.tmp / "run"
        text = self.success(*self.arguments(out))
        return out, text

    def success(self, *words):
        """A real run keeps its normal progress on stderr; anything else there is unexpected."""
        code, text, error = self.command(*words)
        self.assertEqual(code, 0, error or text)
        self.assertTrue(all(line.startswith(("probatio: ", "resuming: ")) for line in error.splitlines() if line), error)
        return text

    def test_shipped_tree_has_no_extension_and_exactly_two_agents(self):
        self.assertFalse(P.there(REPO / "probatio_ext.py"))
        with mock.patch.object(P, "HERE", REPO):
            self.assertEqual(list(P.agents()), ["claude", "codex"])
            U.defaults.cache_clear()
            self.assertEqual([name for name, _ in U.defaults()["agents"]], ["claude", "codex"])
            code, text, error = self.command("--help")
        self.assertEqual((code, error), (0, ""))
        self.assertIn("{claude,codex}", text)
        self.assertNotIn("sample", text)

    def test_extension_is_offered_by_parser_and_form(self):
        self.assertEqual(list(P.agents()), ["claude", "codex", "sample"])
        a = P.parse_args_v4(self.arguments() + ["--dry-run"])
        self.assertEqual(a.agent, "sample")
        defaults = U.defaults()
        self.assertEqual(defaults["agents"], [["claude", "Claude Code"], ["codex", "Codex"], ["sample", "Sample"]])
        self.assertEqual(defaults["models"]["sample"], ["sample-model"])
        available = defaults["availability"]["sample"]
        self.assertEqual(available["classes"], ["I"])
        self.assertFalse(available["timing"])

    def test_default_classes_run_only_class_i_and_explain_omissions_in_every_result(self):
        out, text = self.run_sample()
        meta = json.loads((out / "run.json").read_text())
        self.assertEqual(meta["agent"], "sample")
        self.assertEqual(meta["agent_identity"], {"revision": "one"})
        self.assertEqual(meta["classes"], ["I"])
        self.assertEqual(meta["effort"], None)
        self.assert_omissions(out, text)
        replies = [json.loads(line) for line in (out / "replies.jsonl").read_text().splitlines()]
        self.assertGreater(len(replies), 0)
        self.assertEqual(len(replies), len(self.lines("sample-calls.jsonl")))
        self.assertTrue(all(reply["error"] is None for reply in replies))
        units = [json.loads(line) for line in (out / "units.jsonl").read_text().splitlines()]
        self.assertEqual({unit["class"] for unit in units}, {"I"})
        self.assertEqual(self.lines("sample-setup.jsonl"), [{"family": "v4", "out": str(out)}])
        self.assertFalse(P.there(out / "episodes"))

    def assert_omissions(self, out, text):
        """Each result surface must carry the same reason and leave an omitted class without a score."""
        meta = json.loads((out / "run.json").read_text())
        omissions = meta["capability_omissions"]
        self.assertEqual(set(omissions), {"II", "III"})
        card = json.loads((out / "scorecard.json").read_text())
        for klass, reason in omissions.items():
            self.assertTrue(reason)
            self.assertIn(reason, text)
            cls = card["conditions"]["with"]["classes"][klass]
            self.assertEqual(cls["status"], "not_run")
            self.assertIsNone(cls["score"])
            self.assertEqual(cls["reason"], reason)
            for name in ("scorecard.md", "results.html"):
                self.assertIn(reason, (out / name).read_text(), name)

    def test_quick_tier_does_not_claim_an_unsupported_class_is_available_at_another_tier(self):
        out = self.tmp / "quick"
        words = self.arguments(out)
        words[words.index("--tier") + 1] = "quick"
        text = self.success(*words)
        self.assert_omissions(out, text)
        for shown in (text, (out / "scorecard.md").read_text(), (out / "results.html").read_text()):
            for false_claim in ("Classes I and II only", "Classes I and II; Class III", "--tier standard runs it"):
                self.assertNotIn(false_claim, shown)

    def test_versus_resume_view_keeps_adapter_identity_but_ignores_job_count(self):
        launch = VS.launch_record("sample", "sample-model", "0.0 sample", None, str(self.config), 1)
        original = {"launch": dict(launch, agent_identity={"revision": "one"})}
        changed = {"launch": dict(launch, agent_identity={"revision": "two"})}
        rescheduled = {"launch": dict(original["launch"], jobs=2)}
        self.assertNotEqual(VS.resume_view(original), VS.resume_view(changed))
        self.assertEqual(VS.resume_view(original), VS.resume_view(rescheduled))

    def test_unknown_estimate_is_not_borrowed_from_builtin_rates(self):
        code, text, error = self.command(*self.arguments(), "--dry-run")
        self.assertEqual((code, error), (0, ""))
        self.assertIn("unknown", text.lower())
        self.assertNotIn("Sonnet", text)
        self.assertNotIn("assum", text.lower())
        self.assertEqual(self.lines("sample-calls.jsonl"), [])

    def test_nonselectable_call_plan_refuses_unsupported_class_before_any_call(self):
        supported = [{"dataset": "versus", "id": "question", "meta": {"class": "I"}}]
        unsupported = [{"dataset": "versus", "id": "errand", "episode": True, "meta": {"class": "II"}}]
        self.assertIsNone(P.check_agent_calls("sample", supported))
        with self.assertRaises(SystemExit) as caught:
            P.check_agent_calls("sample", unsupported)
        message = str(caught.exception)
        self.assertEqual(message, "Sample does not support Class II in this run.")
        self.assertEqual(len(message.splitlines()), 1)
        self.assertEqual(self.lines("sample-calls.jsonl"), [])

    def test_v3_estimate_and_header_keep_missing_legacy_timing_unknown(self):
        # These pure/preflight helpers can be checked without declaring or running the unsupported v3 family.
        calls = [{"dataset": "sacramentum", "id": "question"}]
        self.assertIsNone(P.agent_spec("sample").legacy_seconds)
        lines = P.estimate("sample", calls, 1)
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].endswith("unknown"), lines)
        args = SimpleNamespace(agent="sample", model=None, effort=None, workdir=str(self.work),
                               config_dir=str(self.config), auto_memory="as-configured", tier="smoke", jobs=1,
                               dataset="sacramentum")
        opts = P.agent_options(args)
        shown = io.StringIO()
        with contextlib.redirect_stdout(shown):
            P.preflight(args, opts, "Example", calls)
        text = shown.getvalue()
        self.assertIn("time estimate unknown; no timing data for this agent", text)
        self.assertNotIn("times measured", text)
        self.assertNotIn("Sonnet", text)
        self.assertEqual(self.lines("sample-calls.jsonl"), [])

    def test_unsupported_requests_refuse_on_one_line_before_planning_or_calls(self):
        cases = (
            (self.arguments() + ["--memory", "both"], "memory"),
            (self.arguments() + ["--effort", "high"], "effort"),
            (self.arguments() + ["--class", "II"], "Class"),
            (["--agent", "sample", "--name", "Example", "--dataset", "sacramentum", "--tier", "smoke"], "v3"),
            (["--agent", "sample", "--name", "Example", "--dataset", "labores", "--tier", "smoke", "--demo"],
             "demo"),
            (["--agent", "sample", "--compare", "individual:organization", "--target", "organization=Mozilla"],
             "compare"),
            (["--agent", "sample", "--versus", "individual:organization", "--dry-run"], "versus"),
        )
        for words, concept in cases:
            with self.subTest(words=words), \
                    mock.patch.object(P, "plan_v4", side_effect=AssertionError("planned unsupported request")), \
                    mock.patch.object(P, "plan_calls", side_effect=AssertionError("planned unsupported request")):
                code, text, error = self.command(*words)
            self.assertNotEqual(code, 0)
            self.assertEqual(text, "")
            self.assertEqual(len(error.splitlines()), 1, error)
            self.assertIn("sample", error.lower())
            self.assertIn(concept.lower(), error.lower())
        self.assertEqual(self.lines("sample-calls.jsonl"), [])
        self.assertEqual(self.lines("sample-setup.jsonl"), [])

    def test_resume_keeps_its_identity_and_explicit_change_refuses_without_more_calls(self):
        out, _ = self.run_sample()
        record = (out / "run.json").read_bytes()
        count = len(self.lines("sample-calls.jsonl"))
        self.run_sample(out)
        self.assertEqual(len(self.lines("sample-calls.jsonl")), count)
        self.assertEqual((out / "run.json").read_bytes(), record)
        (self.config / "revision.txt").write_text("two\n")
        code, _, error = self.command(*self.arguments(out))
        self.assertEqual(code, 1)
        self.assertEqual(len(error.splitlines()), 1)
        self.assertIn("agent_identity", error)
        self.assertEqual(len(self.lines("sample-calls.jsonl")), count)
        self.assertEqual((out / "run.json").read_bytes(), record)

    def test_identity_change_selects_a_default_sibling_and_then_resumes_it(self):
        self.success(*self.arguments())
        folder = self.tmp / "results" / "v4-sample-organization-mozilla-smoke-memory-on"
        original = (folder / "run.json").read_bytes()
        (self.config / "revision.txt").write_text("two\n")
        text = self.success(*self.arguments())
        sibling = folder.with_name(folder.name + "-2")
        self.assertIn(str(sibling), text)
        self.assertEqual(json.loads((sibling / "run.json").read_text())["agent_identity"], {"revision": "two"})
        self.assertEqual((folder / "run.json").read_bytes(), original)
        count = len(self.lines("sample-calls.jsonl"))
        self.success(*self.arguments())
        self.assertEqual(len(self.lines("sample-calls.jsonl")), count)
        self.assertFalse(P.there(folder.with_name(folder.name + "-3")))

    def test_rescore_uses_saved_identity_without_calling_the_agent(self):
        out, _ = self.run_sample()
        record = (out / "run.json").read_bytes()
        replies = (out / "replies.jsonl").read_bytes()
        count = len(self.lines("sample-calls.jsonl"))
        (self.config / "revision.txt").write_text("two\n")
        code, text, error = self.command("--rescore", str(out))
        self.assertEqual((code, error), (0, ""), text)
        self.assertEqual((out / "run.json").read_bytes(), record)
        self.assertEqual((out / "replies.jsonl").read_bytes(), replies)
        self.assertEqual(len(self.lines("sample-calls.jsonl")), count)
        card = json.loads((out / "scorecard.json").read_text())
        self.assertIn("rescored", card)
        for klass in ("II", "III"):
            self.assertEqual(card["conditions"]["with"]["classes"][klass]["status"], "not_run")

    def test_rescore_refuses_missing_effort_without_writing_any_file(self):
        out, _ = self.run_sample()
        meta = json.loads((out / "run.json").read_text())
        del meta["effort"]
        (out / "run.json").write_text(json.dumps(meta) + "\n")
        self.assert_rescore_refused_without_writes(out, "effort")

    def assert_rescore_refused_without_writes(self, out, field):
        """A damaged record must be refused before changing any result, lock or directory entry."""
        before = {path.name: path.read_bytes() for path in out.iterdir() if P.file_there(path)}
        code, text, error = self.command("--rescore", str(out))
        self.assertEqual(code, 1)
        self.assertEqual(text, "")
        self.assertEqual(len(error.splitlines()), 1)
        self.assertIn(field, error)
        self.assertEqual({path.name for path in out.iterdir()}, set(before))
        self.assertEqual({path.name: path.read_bytes() for path in out.iterdir()}, before)

    def test_rescore_refuses_missing_omissions_and_nonfinite_identity_without_writes(self):
        out, _ = self.run_sample()
        original = json.loads((out / "run.json").read_text())
        for field in ("capability_omissions", "agent_identity"):
            with self.subTest(field=field):
                meta = dict(original)
                if field == "capability_omissions":
                    del meta[field]
                else:
                    # json.loads accepts this spelling, though the identity writer correctly cannot emit it.
                    meta[field] = {"revision": float("nan")}
                (out / "run.json").write_text(json.dumps(meta) + "\n")
                self.assert_rescore_refused_without_writes(out, field)

    def test_optional_config_folder_can_be_null_and_rescored(self):
        original = P.agents()
        spec = original["sample"]

        def options(args):
            opts = spec.options(args)
            opts.config_dir = None
            return opts

        replacement = replace(spec, options=options)
        registry = AR.Registry()
        for item in original.values():
            registry.register(replacement if item.id == "sample" else item)
        out = self.tmp / "no-config"
        words = self.arguments(out)
        start = words.index("--config-dir")
        del words[start:start + 2]
        with mock.patch.object(P, "agents", return_value=registry):
            self.assertFalse(P.agent_spec("sample").config_required)
            self.success(*words)
            record = (out / "run.json").read_bytes()
            self.assertIsNone(json.loads(record)["config_dir"])
            count = len(self.lines("sample-calls.jsonl"))
            code, text, error = self.command("--rescore", str(out))
            self.assertEqual((code, error), (0, ""), text)
            self.assertEqual((out / "run.json").read_bytes(), record)
            self.assertEqual(len(self.lines("sample-calls.jsonl")), count)

    def test_builtin_calls_forward_the_contract_server_keyword(self):
        """Exercise only wrappers, replacing their process adapters before either can run."""
        marker, opts = object(), object()
        reply = ("A", None, "fixture-model")
        for spec in P.builtin_agents():
            with self.subTest(agent=spec.id), \
                    mock.patch.object(P, spec.id + "_call", return_value=reply) as call:
                self.assertEqual(spec.call("prompt", "with", opts, server=marker), reply)
                call.assert_called_once_with("prompt", "with", opts, server=marker)

    def test_both_versus_rescore_reads_guard_family_before_replanning(self):
        """An in-memory saved launch reaches each reader, without opening a bank or creating a run."""
        registry = AR.Registry()
        registry.register(replace(P.agent_spec("sample"), families=("versus",)))
        record = {"launch": {"agent": "sample"}}
        with mock.patch.object(P, "agents", return_value=registry), \
                mock.patch.object(VR, "saved_record", return_value=record), \
                mock.patch.object(VR, "bank_at", side_effect=AssertionError("opened a bank")), \
                mock.patch.object(VS, "replan", side_effect=AssertionError("rebuilt an unsupported plan")), \
                mock.patch.object(P, "lock_folder", side_effect=AssertionError("opened a lock")):
            readers = (lambda: VR.rescore(self.tmp / "missing"),
                       lambda: VR.scored_snapshot(self.tmp / "missing", None, None, None))
            for read in readers:
                with self.subTest(reader=read), self.assertRaises(SystemExit) as stop:
                    read()
                self.assertEqual(str(stop.exception), "Sample does not support rescore runs.")
                self.assertEqual(len(str(stop.exception).splitlines()), 1)
        self.assertEqual(self.lines("sample-calls.jsonl"), [])
        self.assertFalse(P.there(self.tmp / "missing"))

    def test_broken_extension_refuses_every_command_before_cli_or_generation(self):
        original = FIXTURE.read_text()
        broken = {
            "import": 'print("hidden output")\nraise ValueError("broken import")\n',
            "register": 'def register(registry):\n    print("hidden output")\n    raise ValueError("broken setup")\n',
            "no function": "value = 1\n",
            "invalid spec": original.replace('classes=("I",)', 'classes=("IV",)'),
            "duplicate builtin": original.replace('id="sample"', 'id="claude"'),
        }
        commands = (("--help",), ("--agent", "sample", "--dry-run"),
                    ("--agent", "sample", "--name", "Example", "--dataset", "sacramentum", "--tier", "smoke"),
                    ("--agent", "sample", "--target", "Example"),
                    ("--agent", "sample", "--compare", "individual:organization"),
                    ("--agent", "sample", "--versus", "individual:organization"),
                    ("--rescore", str(self.tmp / "missing")), ("--print-entities",), ("--ui",))
        for name, source in broken.items():
            self.path.write_text(source)
            for words in commands:
                with self.subTest(broken=name, command=words), \
                        mock.patch.object(P, "plan_v4", side_effect=AssertionError("generated a plan")), \
                        mock.patch.object(P, "plan_calls", side_effect=AssertionError("generated a plan")), \
                        mock.patch.object(P, "import_ui", side_effect=AssertionError("started a server")):
                    P._AGENT_REGISTRIES.pop(str(self.tmp), None)
                    code, text, error = self.command(*words)
                    self.assertEqual(code, 2)
                    self.assertEqual(text, "")
                    self.assertEqual(len(error.splitlines()), 1, error)
                    self.assertTrue(error.startswith(str(self.path) + ": "), error)
                    self.assertNotIn("hidden output", error)
                    self.assertNotIn("Traceback", error)
        self.assertEqual(self.lines("sample-calls.jsonl"), [])
        self.assertEqual(self.lines("sample-setup.jsonl"), [])


if __name__ == "__main__":
    unittest.main()
