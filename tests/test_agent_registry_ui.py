"""Registry-backed form choices and live displays, without invoking an agent or the network.

The synthetic contract is registered only in memory. Existing UI suites retain their original assertions;
these checks cover the extra capabilities and the unknown values the shipped two agents do not exercise.
"""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(REPO), str(REPO / "tests")]
import agent_registry as A
import live as L
import ordo as O
import probatio as P
import ui as U
import versus_page as VP
import test_ui as TU


def no_call(*args, **kwargs):
    """A display test must never reach execution, even through a registered callback."""
    raise AssertionError("display test attempted an agent call")


@contextlib.contextmanager
def extra_agent(folder, candidates=None, configuration=None, rates=None, options=None):
    """Keep the real registry intact while views read a third, deliberately limited contract."""
    registry = A.Registry()
    for spec in P.agents().values():
        registry.register(spec)
    registry.register(A.AgentSpec(
        id="sample", label="Sample", call=no_call,
        options=options or (lambda args: SimpleNamespace(
            model=args.model, effort=None, workdir=args.workdir, config_dir=args.config_dir,
            config_source=args.config_source, features=[], homes=None)),
        memory_files=lambda workdir, auto_memory, config_dir: [], config_dir=configuration or (lambda: folder),
        version=lambda: "stand-in", setup_run=lambda opts, out, family: None,
        identity=lambda args, opts: {"sample_revision": 1},
        rates_profile=rates or (lambda model, effort: (None, "unknown")),
        families=("v4", "versus", "rescore"), classes=("I",), memory=("off",), efforts=(), models=("sample-model",),
        default_model_label="Sample default",
        memory_candidates=candidates or (lambda in_use: [])))
    U.defaults.cache_clear()
    try:
        with mock.patch.object(P, "agents", return_value=registry):
            yield registry
    finally:
        U.defaults.cache_clear()


class TestRegistryChoices(unittest.TestCase):
    def test_shipped_menu_keeps_two_agents_and_no_extra_metadata(self):
        self.assertFalse(P.there(P.HERE / A.EXTENSION_FILE))
        self.assertEqual(list(P.AGENT_NAMES), ["claude", "codex"])
        defaults = U.defaults()
        self.assertEqual([id_ for id_, _ in defaults["agents"]], ["claude", "codex"])
        choices = next(a.choices for a in P.v4_parser()._actions if a.dest == "agent")
        self.assertEqual(list(choices), ["claude", "codex"])
        self.assertNotIn("availability", defaults)
        self.assertEqual(list(U.MODELS), ["claude", "codex"])

    def test_extra_agent_models_labels_and_capabilities_share_the_registry(self):
        with tempfile.TemporaryDirectory() as folder, extra_agent(Path(folder)):
            defaults = U.defaults()
            self.assertEqual(defaults["agents"][-1], ["sample", "Sample"])
            self.assertEqual(defaults["models"]["sample"], ["sample-model"])
            self.assertEqual(U.MODELS["sample"], ("sample-model",))
            self.assertEqual(defaults["availability"]["sample"], {
                "families": ["v4", "versus", "rescore"], "classes": ["I"], "memory": ["off"],
                "efforts": [], "timing": False, "default_model_label": "Sample default",
                "time_note": "Time estimate unknown"})
            choices = next(a.choices for a in P.v4_parser()._actions if a.dest == "agent")
            self.assertIn("sample", choices)
            self.assertEqual(L.run_facts({"agent": "sample", "effort": None}, "single")["agent"], "Sample")
            self.assertEqual(VP.launch_parts({"launch": {"agent": "sample", "cli_version": "0"}}), ["Sample 0"])
            self.assertEqual(O.AGENT_NAMES["sample"], "Sample")
            self.assertNotIn("None", json.dumps(defaults))

    def test_effortless_agent_does_not_acquire_an_explicit_default_effort(self):
        with tempfile.TemporaryDirectory() as folder, extra_agent(Path(folder)):
            form = {"agent": "sample", "targets": {"individual": "Jane Doe"}, "memory": "off",
                    "effort": P.DEFAULT_EFFORT, "watch": "off"}
            argv, _ = U.build_command(form)
            self.assertNotIn("--effort", argv)
            self.assertIsNone(P.parse_args_v4(argv).effort)
            changed, _ = U.build_command(dict(form, effort="high"))
            self.assertEqual(changed[changed.index("--effort") + 1], "high")
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                P.parse_args_v4(changed)

    def test_memory_candidates_come_from_the_selected_contract(self):
        with tempfile.TemporaryDirectory() as folder:
            candidates = mock.Mock(return_value=[])
            with extra_agent(Path(folder), candidates):
                answer = U.memory_answer(["--agent", "sample", "--target", "Jane Doe", "--memory", "off"])
                self.assertEqual(answer["candidates"], [])
                candidates.assert_called_once_with(in_use=Path(folder))

    def test_memory_view_and_options_share_the_resolved_configuration(self):
        """The same parsed command caches one config callback result, regardless of which view asks first."""
        for versus in (False, True):
            for first in ("memory", "options"):
                with self.subTest(versus=versus, first=first), tempfile.TemporaryDirectory() as folder:
                    chosen = Path(folder)
                    configuration = mock.Mock(side_effect=[chosen, chosen / "wrong-second-resolution"])
                    options = mock.Mock(side_effect=lambda args: SimpleNamespace(
                        model=args.model, effort=None, workdir=args.workdir, config_dir=args.config_dir,
                        config_source=args.config_source, features=[], homes=None))
                    candidates = mock.Mock(return_value=[])
                    with extra_agent(chosen, candidates, configuration=configuration, options=options):
                        argv = ["--agent", "sample", "--memory", "off", "--workdir", folder]
                        argv += (["--versus", "individual=Jane Doe", "--versus", "organization=Mozilla"]
                                 if versus else ["--target", "Jane Doe"])
                        parser_owner, parser_name = ((U.versus_command(), "parse") if versus else
                                                     (P, "parse_args_v4"))
                        args = getattr(parser_owner, parser_name)(argv)
                        with mock.patch.object(parser_owner, parser_name, return_value=args):
                            if first == "options":
                                opts = P.agent_options(args)
                            answer = U.memory_answer(argv)
                            if first == "memory":
                                opts = P.agent_options(args)
                        configuration.assert_called_once_with()
                        options.assert_called_once_with(args)
                        candidates.assert_called_once_with(in_use=chosen)
                        self.assertEqual((opts.config_dir, opts.config_source), (chosen, "default"))
                        self.assertEqual(answer["report"]["folder"], P.display(P.tilde(chosen)))
                        self.assertEqual(answer["report"]["source"], P.source_words("sample", "default"))
                        self.assertNotIn("None", json.dumps(answer))

    def test_unconfigured_agent_and_explicit_config_have_one_source(self):
        """A missing default is displayed as unconfigured; an explicit folder bypasses the default callback."""
        for explicit in (False, True):
            with self.subTest(explicit=explicit), tempfile.TemporaryDirectory() as folder:
                configuration = mock.Mock(return_value=None)
                with extra_agent(Path(folder), configuration=configuration):
                    argv = ["--agent", "sample", "--target", "Jane Doe", "--memory", "off"]
                    if explicit:
                        argv += ["--config-dir", folder]
                    answer = U.memory_answer(argv)
                    self.assertEqual(answer["report"]["folder"], P.display(P.tilde(folder)) if explicit else
                                     "none")
                    self.assertEqual(answer["report"]["source"], P.source_words("sample", "option") if explicit else
                                     "this agent has none")
                    self.assertEqual(configuration.call_count, 0 if explicit else 1)
                    self.assertNotIn("None", json.dumps(answer))

    def test_memory_callback_failures_are_plain_page_errors(self):
        for hook in (mock.Mock(side_effect=RuntimeError("candidate search failed")), mock.Mock(return_value=()),
                     mock.Mock(return_value=[{}])):
            with self.subTest(hook=hook), tempfile.TemporaryDirectory() as folder, \
                    extra_agent(Path(folder), candidates=hook):
                answer = U.memory_answer(["--agent", "sample", "--target", "Jane Doe", "--memory", "off"])
                self.assertIn("Sample memory_candidates:", answer["error"])
                self.assertNotIn("Traceback", answer["error"])
                self.assertNotIn("None", answer["error"])

    def test_availability_asks_effortless_timing_with_no_effort(self):
        rates = mock.Mock(return_value=(None, "estimate unknown"))
        with tempfile.TemporaryDirectory() as folder, extra_agent(Path(folder), rates=rates):
            self.assertFalse(U.agent_availability()["sample"]["timing"])
        rates.assert_called_once_with(None, None)

    @unittest.skipUnless(TU.CHROME, "no headless Chrome on PATH")
    def test_selecting_an_effortless_agent_clears_the_hidden_stale_effort(self):
        """Drive the real menus: an old high choice cannot strand the user behind a hidden effort row."""
        with tempfile.TemporaryDirectory() as folder, extra_agent(Path(folder)), \
                mock.patch.object(U, "memory_view", return_value={"report": None}):
            server = TU.Served(U.Session())
            chrome = TU.Chrome(Path(folder) / "chrome")
            try:
                chrome.call("Page.navigate", url=f"http://127.0.0.1:{server.port}/?t={server.token}")
                self.assertTrue(TU.wait_for(lambda: chrome.js("typeof F !== 'undefined' && F !== null")))
                chrome.js("document.querySelector('.seg button[value=claude]').click(); "
                          "document.querySelector('.seg button[value=high]').click()")
                self.assertEqual(chrome.js("F.effort"), "high")
                chrome.js("document.querySelector('.seg button[value=sample]').click()")
                self.assertEqual(chrome.js("F.effort"), P.DEFAULT_EFFORT)
                self.assertTrue(chrome.js("document.getElementById('row-effort').hidden"))
                self.assertEqual(chrome.js("document.querySelectorAll('#row-effort button').length"), 0)
                self.assertEqual(chrome.js("document.getElementById('modelnote').textContent"),
                                 "Time estimate unknown")
                self.assertTrue(chrome.js("[...document.querySelectorAll('#model option')]"
                                          ".some(option => option.value === 'sample-model')"))
                self.assertEqual(chrome.js("document.querySelector('#model option[value=\"\"]').textContent"),
                                 "Sample default")
                self.assertEqual(chrome.js("document.querySelector('#row-classes button[value=II]').title"),
                                 "not available for Sample")
                self.assertEqual(chrome.js("[...document.querySelectorAll('#more .row')]"
                                          ".find(row => row.querySelector('.k').textContent === 'memory')"
                                          ".querySelector('button[value=on]').title"), "not available for Sample")
                confirmation = chrome.js("plan = {summary: {calls: 1, time: 'unknown'}}; "
                                         "confirming = true; renderConfirm(true); "
                                         "document.getElementById('confirm').textContent")
                self.assertIn("unknown, on Sample", confirmation)
                self.assertNotIn("effort", confirmation)
                self.assertNotIn("None", confirmation)
                self.assertNotIn("None", chrome.js("document.body.innerText"))
                chrome.js("F.types = ['individual', 'organization']; build(); changed()")
                self.assertEqual(chrome.js("document.querySelector('#row-agent button[value=sample]').title"),
                                 "not available for this run")
                chrome.js("F.targets.individual = 'Jane Doe'; F.targets.organization = 'Acme Corp'; changed()")
                self.assertTrue(TU.wait_for(lambda: chrome.js(
                    "said && said.question && said.question.answer === 'names'")))
                self.assertEqual(chrome.js("document.querySelector('#row-agent button[value=sample]').title"), "")
            finally:
                chrome.close()
                server.close()


class TestUnknownEstimates(unittest.TestCase):
    def test_all_preflight_summaries_preserve_unknown(self):
        for line in ("  all 4 items  8 calls  unknown", "  4 units, 8 calls  unknown"):
            self.assertEqual(U.plan_summary(line), {"calls": 8, "time": "unknown", "hint": None, "rates": None})
        self.assertEqual(U.plan_summary("  all 2 comparisons: unknown")["time"], "unknown")
        self.assertEqual(U.versus_summary("  all 4 questions  8 episodes  unknown"),
                         {"calls": 12, "time": "unknown", "hint": None, "rates": None})
        self.assertNotIn("None", json.dumps(U.versus_summary("  all 4 questions  8 episodes  unknown")))

    def test_live_time_is_unknown_until_the_run_has_measured_its_kind(self):
        line = L.LiveLine(2, lambda call: None, stream=io.StringIO(), tty=False)
        calls = [{"condition": "with", "dataset": "v4.I", "id": str(i), "meta": {"plan": "pair"}}
                 for i in range(5)]
        line.begin(calls, {})
        self.assertIsNone(line.snapshot()["left_seconds"])
        self.assertNotIn("left", line.text())
        self.assertNotIn("None", line.text())
        for call in calls[:L.MEAN_AFTER]:
            line.finished(call, {"outcome": "yes", "seconds": 4})
        self.assertEqual(line.snapshot()["left_seconds"], 4)
        line.close()

    def test_one_known_kind_never_fills_an_unknown_kind(self):
        line = L.LiveLine(1, lambda call: None if call.get("episode") else 4,
                          stream=io.StringIO(), tty=False)
        calls = [{"condition": "with", "dataset": "v4.I", "id": "q", "meta": {}},
                 {"condition": "with", "dataset": "v4.II", "id": "e", "meta": {}, "episode": True}]
        line.begin(calls, {})
        self.assertIsNone(line.snapshot()["left_seconds"])
        line.close()


class TestOmittedClasses(unittest.TestCase):
    def test_capability_reason_precedes_tier_omission_and_has_no_score(self):
        reason = "Class is not available for Sample"
        card = {"tier": "quick", "conditions": {"with": {"classes": {
            "I": {"status": "ok", "score": 50, "ci": [40, 60]},
            "II": {"status": "not_run", "reason": reason},
            "III": {"status": "not_run", "reason": reason}}}}}
        shown = L.v4_rows(card, True)
        self.assertNotIn("None", json.dumps(shown))
        self.assertEqual(shown[0], {"line": "quick tier: agent-supported classes only; " + reason})
        rows = [row for row in shown if "label" in row]
        for row in rows[1:]:
            self.assertEqual(row["note"], reason)
            self.assertIsNone(row["value"])
            self.assertIsNone(row["shown"])
            self.assertIsNone(row["ci"])


if __name__ == "__main__":
    unittest.main()
