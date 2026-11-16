"""Clear absent-configuration words in both forms, and damaged comparison launch records on rescore.

The real commands and form memory report share a synthetic extension. Every run and browser profile stays in the
test's scratch folder; provider processes and outbound network entry points are forbidden by the shared fixture.
"""
import json
import os
import re
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(REPO), str(REPO / "tests")]
from tests import test_agent_extension as helper
from tests import test_ui as TU
from tests.test_versus_fixture import full_bank
import agent_registry as AR
import probatio as P
import ui as U
import versus as VS
import versus_run as VR

CONFIG_WORDS = "config folder: none (this agent has none)"
EMPTY_WORDS = "none (this agent has no config folder)"
UNSET_VARIABLE = "SAMPLE_CONFIG_DIR"
UNSET_SOURCE = "SAMPLE_CONFIG_DIR is not set in this shell, and this agent has no default folder"
UNSET_EMPTY_WORDS = "none (this agent has no config folder in this run)"
VERSUS_FIXTURE = REPO / "tests" / "fixtures" / "sample_versus_extension.py"


class Fixture(unittest.TestCase):
    def setUp(self):
        self.fixture = helper.Extension(methodName="runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.tmp, self.work, self.config = self.fixture.tmp, self.fixture.work, self.fixture.config

    def comparison_fixture(self):
        """The public comparison fixture supports every class the quick tier actually plans."""
        source = VERSUS_FIXTURE.read_text(encoding="utf-8")
        self.fixture.path.write_text(source.replace('CLASSES = ("I", "III")', 'CLASSES = ("I", "II")'),
                                     encoding="utf-8")
        P._AGENT_REGISTRIES.pop(str(self.tmp), None)

    def arguments(self, versus=False, explicit=False, out=None):
        """Commands for the same missing default, with an optional explicit config proving the other branch."""
        if versus:
            self.comparison_fixture()
            words = ["--agent", "sample", "--versus", "individual=Jane Doe", "--versus", "organization=Acme Corp",
                     "--tier", "quick", "--memory", "on", "--jobs", "1", "--workdir", str(self.work)]
        else:
            words = self.fixture.arguments()
            start = words.index("--config-dir")
            del words[start:start + 2]
        if explicit:
            words += ["--config-dir", str(self.config)]
        return words + (["--out", str(out)] if out else [])

    def command(self, *words):
        """Keep every synthetic command's user-facing output free of tracebacks and missing-value jargon."""
        result = self.fixture.command(*words)
        self.assertNotIn("Traceback", result[1] + result[2])
        self.assertNotIn("None", result[1] + result[2])
        return result

    def clear_missing_words(self, text):
        for stale in ("not configured", "default, since", "none found in", "None"):
            self.assertNotIn(stale, text)

    def registry_with_variable(self, variable, forms=False):
        """Keep every adapter intact, changing only the synthetic config variable and optional form families."""
        registry = AR.Registry()
        for spec in P.agents().values():
            if spec.id == "sample":
                changes = {"config_variable": variable}
                if forms:
                    changes["families"] = ("v4", "versus", "rescore")
                spec = replace(spec, **changes)
            registry.register(spec)
        return registry


class ConfigurationWords(Fixture):
    def test_v4_preflight_and_form_describe_the_missing_config_folder(self):
        words = self.arguments()
        code, text, error = self.command(*words, "--dry-run")
        self.assertEqual(code, 0, error)
        self.assertIn(CONFIG_WORDS, text)
        self.assertIn(P.MEMORY_HEAD + " " + EMPTY_WORDS, text)
        answer = U.memory_answer(words)
        self.assertEqual((answer["report"]["folder"], answer["report"]["source"]), ("none", "this agent has none"))
        self.assertEqual(answer["empty"], EMPTY_WORDS)
        self.clear_missing_words(text + json.dumps(answer))
        self.assertEqual(self.fixture.lines("sample-calls.jsonl"), [])

    def test_versus_preflight_and_form_describe_the_missing_config_folder(self):
        words = self.arguments(versus=True)
        with mock.patch.object(VR, "load_bank", return_value=full_bank()):
            code, text, error = self.command(*words, "--dry-run")
        self.assertEqual(code, 0, error)
        self.assertIn(CONFIG_WORDS, text)
        self.assertIn(P.MEMORY_HEAD + " " + EMPTY_WORDS, text)
        answer = U.memory_answer(words)
        self.assertEqual((answer["report"]["folder"], answer["report"]["source"]), ("none", "this agent has none"))
        self.assertEqual(answer["empty"], EMPTY_WORDS)
        self.clear_missing_words(text + json.dumps(answer))
        self.assertEqual(self.fixture.lines("sample-calls.jsonl"), [])

    def unset_variable_preflight(self, versus):
        """Both commands and their form reports distinguish an unset variable from an undeclared folder."""
        words = self.arguments(versus=versus)
        registry = self.registry_with_variable(UNSET_VARIABLE)
        with mock.patch.dict(os.environ), mock.patch.object(P, "agents", return_value=registry):
            os.environ.pop(UNSET_VARIABLE, None)
            with mock.patch.object(VR, "load_bank", return_value=full_bank()):
                code, text, error = self.command(*words, "--dry-run")
            self.assertEqual(code, 0, error)
            self.assertIn("config folder: none (" + UNSET_SOURCE + ")", text)
            self.assertIn(P.MEMORY_HEAD + " " + UNSET_EMPTY_WORDS, text)
            answer = U.memory_answer(words)
            self.assertEqual((answer["report"]["folder"], answer["report"]["source"]), ("none", UNSET_SOURCE))
            self.assertEqual(answer["empty"], UNSET_EMPTY_WORDS)
            self.clear_missing_words(text + json.dumps(answer))
        self.assertEqual(self.fixture.lines("sample-calls.jsonl"), [])

    def test_v4_unset_variable_with_no_default_names_the_missing_folder_in_this_run(self):
        self.unset_variable_preflight(versus=False)

    def test_versus_unset_variable_with_no_default_names_the_missing_folder_in_this_run(self):
        self.unset_variable_preflight(versus=True)

    def test_an_explicit_folder_keeps_its_normal_words_in_both_forms(self):
        for versus in (False, True):
            with self.subTest(versus=versus):
                words = self.arguments(versus=versus, explicit=True)
                with mock.patch.object(VR, "load_bank", return_value=full_bank()):
                    code, text, error = self.command(*words, "--dry-run")
                self.assertEqual(code, 0, error)
                answer = U.memory_answer(words)
                self.assertEqual(answer["report"]["folder"], P.display(P.tilde(self.config)))
                self.assertEqual(answer["report"]["source"], "chosen with --config-dir")
                self.assertNotIn("this agent has none", text + json.dumps(answer))
                self.assertNotIn("this agent has no config folder", text + json.dumps(answer))

    def test_native_memory_lines_keep_their_existing_folder_source_and_off_words(self):
        for agent in ("claude", "codex"):
            with self.subTest(agent=agent):
                report = P.memory_report(agent, str(self.work), "off", "as-configured", self.config, "default")
                source = P.CONFIG_SOURCES["default"].format(P.CONFIG_VARIABLE[agent])
                self.assertEqual(P.memory_lines(report), [
                    P.MEMORY_FOLDER_LINE.format(folder=P.display(P.tilde(self.config)), source=source),
                    P.MEMORY_HEAD + " " + P.MEMORY_OFF])

    @unittest.skipUnless(TU.CHROME, "needs a headless Chrome on PATH")
    def test_the_real_memory_block_shows_the_missing_folder_in_both_forms(self):
        """Both missing-default cases render actual reports through the unchanged browser formatter."""
        seen = []

        def memory(argv, env):
            seen.append(U.is_versus(argv))
            return U.memory_answer(argv)

        cases = ((None, "this agent has none", EMPTY_WORDS), (UNSET_VARIABLE, UNSET_SOURCE, UNSET_EMPTY_WORDS))
        for index, (variable, origin, empty) in enumerate(cases):
            registry = self.registry_with_variable(variable, forms=True)
            with self.subTest(variable=variable), mock.patch.dict(os.environ), \
                    mock.patch.object(P, "agents", return_value=registry), \
                    mock.patch.object(U, "memory_view", side_effect=memory):
                os.environ.pop(UNSET_VARIABLE, None)
                U.defaults.cache_clear()
                server = TU.Served(U.Session())
                chrome = TU.Chrome(self.tmp / f"c{index}")
                try:
                    chrome.call("Page.navigate", url=f"http://127.0.0.1:{server.port}/?t={server.token}")
                    self.assertTrue(TU.wait_for(lambda: chrome.js("typeof F !== 'undefined' && F !== null"), 60))
                    for versus in (False, True):
                        with self.subTest(versus=versus):
                            requested = len(seen) + 1
                            chrome.js("F.agent='sample'; F.memory='on'; F.targets={individual:'Jane Doe', "
                                      "organization:'Acme Corp'}; F.types=" +
                                      ("['individual','organization']" if versus else "['individual']") +
                                      "; build(); changed()")
                            self.assertTrue(TU.wait_for(lambda: len(seen) >= requested and seen[-1] == versus, 60))
                            self.assertTrue(TU.wait_for(lambda: chrome.js(
                                "mem && mem.report && mem.report.folder === 'none' && mem.report.source === " +
                                json.dumps(origin) + " && "
                                "document.getElementById('cmd').textContent.includes('--versus') === " +
                                ("true" if versus else "false")), 60))
                            shown = re.sub(r"\s+", " ", chrome.js("document.getElementById('memory').innerText"))
                            self.assertIn("config folder none (" + origin + ")", shown)
                            self.assertIn("Memory files: " + empty + ".", shown)
                            self.clear_missing_words(shown)
                finally:
                    chrome.close()
                    server.close()
        self.assertIn(False, seen)
        self.assertIn(True, seen)


class ComparisonAgentRecord(Fixture):
    def finished(self):
        """A real finished synthetic comparison supplies all the valid fields the rescorer otherwise needs."""
        out = self.tmp / "finished-comparison"
        words = self.arguments(versus=True, out=out)
        with mock.patch.object(VR, "load_bank", return_value=full_bank()):
            code, text, error = self.command(*words)
        self.assertEqual(code, 0, error or text)
        self.assertTrue(P.file_there(out / "scorecard.json"))
        self.assertTrue(self.fixture.lines("sample-calls.jsonl"))
        return out, json.loads((out / VS.RUN_FILE).read_text(encoding="utf-8"))

    def refused(self, out, record):
        """Invalid launch fields stop before bank loading, lock creation or any changed result file."""
        (out / VS.RUN_FILE).write_text(json.dumps(record) + "\n", encoding="utf-8")
        before = {str(p.relative_to(out)): p.read_bytes() for p in out.rglob("*") if p.is_file()}
        calls = self.fixture.lines("sample-calls.jsonl")
        with mock.patch.object(VR, "bank_at", side_effect=AssertionError("invalid agent reached bank loading")):
            code, text, error = self.command("--rescore", str(out))
        self.assertEqual((code, text), (1, ""), error)
        self.assertEqual(len(error.splitlines()), 1, error)
        self.assertEqual({str(p.relative_to(out)): p.read_bytes() for p in out.rglob("*") if p.is_file()}, before)
        self.assertEqual(self.fixture.lines("sample-calls.jsonl"), calls)
        return error

    def test_nontext_comparison_agents_are_damaged_records(self):
        out, record = self.finished()
        for agent in (5, ["sample"], None, {}, True):
            with self.subTest(agent=agent):
                record["launch"]["agent"] = agent
                error = self.refused(out, record)
                self.assertEqual(error, f"{P.display(out)} is not a versus run folder: its {VS.RUN_FILE} is not a "
                                 "versus run record.\n")
                self.assertNotIn("extension is not installed", error)

    def test_unregistered_text_agents_name_the_missing_extension(self):
        out, record = self.finished()
        for agent in ("missing-sample", "Sample"):
            with self.subTest(agent=agent):
                record["launch"]["agent"] = agent
                error = self.refused(out, record)
                self.assertIn(agent, error)
                self.assertIn("is not registered; its extension is not installed", error)
                self.assertNotIn("not a versus run record", error)


if __name__ == "__main__":
    unittest.main()
