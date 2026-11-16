"""Default versus folders choose resumable siblings with each candidate's own recipe, without model calls.

Real scratch folders hold records made by the real planner. A patched version probe and execute boundary exercise
main's dry-run and execution selection without launching an agent or writing a results page.
"""
import contextlib
import errno
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import ordo
import probatio as P
import versus as VS
import versus_run as VR
from tests.test_versus_fixture import full_bank


class DefaultVersus(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="default-versus-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.work, self.config = self.root / "work", self.root / "config"
        self.work.mkdir()
        self.config.mkdir()
        self.patch_here = mock.patch.object(P, "HERE", self.root)
        self.patch_here.start()
        self.addCleanup(self.patch_here.stop)
        self.args = ["--agent", "claude", "--versus", "individual=Jane Doe", "--versus",
                     "organization=Acme Corp", "--tier", "quick", "--workdir", str(self.work),
                     "--config-dir", str(self.config)]
        self.a = VR.parse(self.args)
        self.names = VR.versus_names(self.a.versus)
        self.bank, self.ordo = full_bank(), ordo.load_ordo_bank()
        self.opts = SimpleNamespace(workdir=self.work, config_dir=self.config, config_source="option",
                                    model=None, effort=self.a.effort)
        self.cli = "fixture 1.0"
        self.report = P.memory_report("claude", self.work, self.a.memory, "as-configured", self.config,
                                      "option", VR.memory_people(VS.resolve_names(self.names)))
        self.base = self.root / "results" / VR.run_name(self.a, self.names)

    def select(self):
        return VR.select_run(self.a, self.opts, self.bank, self.ordo, self.names, self.cli, self.report)

    def write_record(self, out, recipe=None, change=None):
        """Save a real plan's launch record, optionally altering the identity that should prevent a resume."""
        plan = VS.plan(self.bank, self.names, self.a.tier, VR.arms_of(self.a), self.ordo, recipe=recipe)
        record = VS.run_record(plan, P.memory_record(self.report)["memory_files"],
                               VR.launch(self.a, self.opts, self.cli, self.report))
        if change:
            change(record)
        out.mkdir(parents=True)
        (out / VS.RUN_FILE).write_text(json.dumps(record), encoding="utf-8")
        return record

    def conflict(self, out):
        self.write_record(out, change=lambda rec: rec["launch"].update(cli_version="earlier CLI"))

    def test_absent_base_and_compatible_base_are_selected_without_notice(self):
        out, _, record, notice = self.select()
        self.assertEqual(out, self.base)
        self.assertIsNone(notice)
        self.assertFalse(self.base.exists())
        self.write_record(self.base)
        out, _, resumed, notice = self.select()
        self.assertEqual(out, self.base)
        self.assertEqual(record, resumed)
        self.assertIsNone(notice)

    def assert_fresh_base(self, expected=None):
        """A pre-existing usable path must keep the same folder, without taking a sibling or writing a record."""
        out, _, record, notice = self.select()
        self.assertEqual(out, expected or self.base)
        self.assertIsNone(notice)
        self.assertEqual(VR.folder_state(out, record), ("fresh", None))
        self.assertFalse(self.base.with_name(self.base.name + "-2").exists())
        return out

    def test_empty_default_folder_is_usable_without_notice(self):
        self.base.mkdir(parents=True)
        self.assert_fresh_base()

    def test_default_folder_holding_only_run_lock_is_usable_without_notice(self):
        self.base.mkdir(parents=True)
        lock = self.base / "run.lock"
        lock.write_text("earlier lock file\n")
        self.assert_fresh_base()
        self.assertEqual(lock.read_text(), "earlier lock file\n")

    def test_default_folder_holding_only_rendered_items_is_usable_without_notice(self):
        self.base.mkdir(parents=True)
        rendered = self.base / "rendered.jsonl"
        rendered.write_text("rendered by a dry run\n")
        self.assert_fresh_base()
        self.assertEqual(rendered.read_text(), "rendered by a dry run\n")

    def test_dangling_default_link_is_followed_without_notice(self):
        target = self.root / "elsewhere" / "new-run"
        self.base.parent.mkdir(parents=True)
        self.base.symlink_to(target, target_is_directory=True)
        self.assert_fresh_base(target)
        self.assertFalse(target.exists())
        self.assertTrue(self.base.is_symlink())

    def test_fresh_and_resumed_preflights_add_no_notice(self):
        with mock.patch.object(VR, "load_bank", return_value=self.bank), \
                mock.patch.object(VR, "check_world"), mock.patch.object(P, "agent_options", return_value=self.opts), \
                mock.patch.object(P.subprocess, "run", return_value=SimpleNamespace(stdout=self.cli)):
            for expected in ("starts fresh", "resumes the run it holds"):
                with contextlib.redirect_stdout(io.StringIO()) as stream:
                    VR.main(self.args + ["--dry-run"])
                text = stream.getvalue()
                self.assertIn(expected, text)
                self.assertNotIn("; using ", text)
                self.assertNotIn("  folder: ", text)
                if not self.base.exists():
                    self.write_record(self.base)

    def test_incompatible_base_uses_absent_sibling_with_one_notice(self):
        self.conflict(self.base)
        before = (self.base / VS.RUN_FILE).read_bytes()
        out, _, _, notice = self.select()
        self.assertEqual(out, self.base.with_name(self.base.name + "-2"))
        self.assertIn(P.display(self.base), notice)
        self.assertIn(P.display(out), notice)
        self.assertIn("CLI version", notice)
        self.assertNotIn("pass --out", notice)
        self.assertNotIn("To start a new run", notice)
        self.assertNotIn("run the command that started it", notice)
        self.assertEqual(len(notice.splitlines()), 1)
        self.assertEqual((self.base / VS.RUN_FILE).read_bytes(), before)
        self.assertFalse(out.exists())

    def test_notice_uses_structured_reason_with_changed_words_in_install_path(self):
        install = self.root / "install (old changed)"
        base = install / "results" / VR.run_name(self.a, self.names)
        self.conflict(base)
        with mock.patch.object(P, "HERE", install):
            out, _, _, notice = self.select()
        self.assertEqual(out, base.with_name(base.name + "-2"))
        self.assertIn("(the CLI version changed)", notice)
        self.assertNotIn("other settings (old changed)", notice)

    def test_notice_lists_first_four_structured_changes_and_counts_the_rest(self):
        def different(record):
            record["launch"].update(agent="codex", model="other", cli_version="earlier", effort="other",
                                    config_dir="other config")
            record["memory_files"] = {"other memory": "0" * 64}

        saved = self.write_record(self.base, change=different)
        out, _, record, notice = self.select()
        reason = VR.resume_refusal(self.base, saved, record)
        names = ("the agent", "the model", "the CLI version", "the effort", "the config folder",
                 "the memory record (each memory file's sha256)")
        self.assertEqual(reason.changed, names)
        self.assertEqual(str(reason), VR.RESUME_LINE.format(folder=P.display(self.base), what="the agent",
                         theirs="codex", mine="claude"))
        self.assertEqual(out, self.base.with_name(self.base.name + "-2"))
        self.assertIn(", ".join(names[:4]), notice)
        self.assertIn("and 2 more", notice)
        self.assertNotIn(names[4], notice)
        self.assertNotIn(names[5], notice)
        self.assertEqual(len(notice.splitlines()), 1)

    def test_deep_unlisted_fields_cannot_replace_the_original_plain_refusal(self):
        left, right = {}, {}
        for _ in range(1500):
            left, right = {"nested": left}, {"nested": right}
        saved = {"launch": {"agent": "codex"}, "unlisted": left}
        record = {"launch": {"agent": "claude"}, "unlisted": right}
        reason = VR.resume_refusal(self.base, saved, record)
        self.assertEqual(str(reason), VR.RESUME_LINE.format(folder=P.display(self.base), what="the agent",
                         theirs="codex", mine="claude"))
        self.assertEqual(reason.changed, ("the agent",))

    def test_changed_memory_skips_old_record_and_can_resume_a_later_match(self):
        memory = self.config / "CLAUDE.md"
        memory.write_text("Jane Doe is the user.\n")
        self.report = P.memory_report("claude", self.work, self.a.memory, "as-configured", self.config,
                                      "option", VR.memory_people(VS.resolve_names(self.names)))
        self.write_record(self.base)
        memory.write_text("Jane Doe is the user, with revised notes.\n")
        self.report = P.memory_report("claude", self.work, self.a.memory, "as-configured", self.config,
                                      "option", VR.memory_people(VS.resolve_names(self.names)))
        second = self.base.with_name(self.base.name + "-2")
        self.conflict(second)
        third = self.base.with_name(self.base.name + "-3")
        self.write_record(third)
        out, _, _, notice = self.select()
        self.assertEqual(out, third)
        self.assertIn("memory record", notice)

    def test_resumable_legacy_sibling_keeps_its_own_recipe(self):
        self.conflict(self.base)
        sibling = self.base.with_name(self.base.name + "-2")
        self.write_record(sibling, recipe=VS.RECIPES[self.a.tier])
        out, plan, record, _ = self.select()
        self.assertEqual(out, sibling)
        self.assertEqual(plan.identity["recipe"], VS.RECIPES[self.a.tier])
        self.assertEqual(record, json.loads((sibling / VS.RUN_FILE).read_text()))

    def test_foreign_stray_retired_and_malformed_folders_are_skipped(self):
        choices = [self.base] + [self.base.with_name(self.base.name + "-" + str(i)) for i in range(2, 5)]
        choices[0].mkdir(parents=True)
        (choices[0] / "run.json").write_text("{}")
        choices[1].mkdir()
        (choices[1] / "replies.jsonl").write_text("{}\n")
        self.write_record(choices[2], change=lambda rec: rec["identity"].update(recipe="retired"))
        choices[3].mkdir()
        (choices[3] / VS.RUN_FILE).write_text("{")
        out, _, _, _ = self.select()
        self.assertEqual(out, self.base.with_name(self.base.name + "-5"))

    def test_deeply_nested_record_is_skipped_without_changing_it(self):
        self.base.mkdir(parents=True)
        record = self.base / VS.RUN_FILE
        body = "[" * 10000 + "0" + "]" * 10000
        record.write_text(body)
        out, _, _, notice = self.select()
        self.assertEqual(out, self.base.with_name(self.base.name + "-2"))
        self.assertTrue(notice and notice.strip())
        self.assertIn(str(self.base), notice)
        self.assertIn(str(out), notice)
        self.assertEqual(record.read_text(), body)

    def test_nonregular_record_is_skipped_without_opening_it(self):
        self.base.mkdir(parents=True)
        os.mkfifo(self.base / VS.RUN_FILE)
        out, _, _, notice = self.select()
        self.assertEqual(out, self.base.with_name(self.base.name + "-2"))
        self.assertIn("not a regular versus run record", notice)

    def test_non_directory_and_linked_conflict_keep_the_logical_sibling_name(self):
        self.base.parent.mkdir()
        self.base.write_text("an ordinary file")
        out, _, _, _ = self.select()
        self.assertEqual(out, self.base.with_name(self.base.name + "-2"))
        self.base.unlink()
        target = self.root / "elsewhere"
        self.conflict(target)
        self.base.symlink_to(target, target_is_directory=True)
        out, _, _, _ = self.select()
        self.assertEqual(out, self.base.with_name(self.base.name + "-2"))
        self.assertFalse(target.with_name(target.name + "-2").exists())

    def test_unlookable_candidate_refuses_without_skipping(self):
        self.conflict(self.base)
        sibling = self.base.with_name(self.base.name + "-2")
        original = P.there

        def look(path):
            if Path(path) == sibling:
                raise OSError(errno.EACCES, "Permission denied", str(path))
            return original(path)

        with mock.patch.object(P, "there", side_effect=look), contextlib.redirect_stderr(io.StringIO()) as err:
            with self.assertRaises(SystemExit) as stop:
                self.select()
        self.assertEqual(stop.exception.code, 2)
        self.assertIn("Permission denied", err.getvalue())
        self.assertIn(str(sibling), err.getvalue())
        self.assertFalse(self.base.with_name(self.base.name + "-3").exists())

    def test_all_99_taken_refuses(self):
        self.conflict(self.base)
        incompatible = (self.base / VS.RUN_FILE).read_bytes()
        for n in range(2, 100):
            out = self.base.with_name(self.base.name + "-" + str(n))
            out.mkdir(parents=True)
            (out / VS.RUN_FILE).write_bytes(incompatible)
        with self.assertRaises(SystemExit) as stop:
            self.select()
        self.assertIn(str(self.base), str(stop.exception))
        self.assertIn("--out", str(stop.exception))
        self.assertFalse(self.base.with_name(self.base.name + "-100").exists())

    def test_explicit_out_keeps_original_resume_refusal(self):
        self.conflict(self.base)
        self.a.out = str(self.base)
        out, _, record, notice = self.select()
        self.assertEqual(out, self.base)
        self.assertIsNone(notice)
        state, reason = VR.folder_state(out, record)
        self.assertEqual(state, "refused")
        with self.assertRaises(SystemExit) as stop:
            VR.execute(self.a, self.opts, self.bank, self.ordo, None, out, record)
        self.assertEqual(stop.exception.code, reason)

    def test_dry_run_and_execution_select_same_folder_and_print_notice_once(self):
        self.conflict(self.base)
        chosen = self.base.with_name(self.base.name + "-2")
        outputs = []
        with mock.patch.object(VR, "load_bank", return_value=self.bank), \
                mock.patch.object(VR, "check_world"), mock.patch.object(P, "agent_options", return_value=self.opts), \
                mock.patch.object(P.subprocess, "run", return_value=SimpleNamespace(stdout=self.cli)), \
                mock.patch.object(VR, "page"), mock.patch.object(P, "run_calls") as calls, \
                mock.patch.object(VR, "result_card", return_value=({}, [])), \
                mock.patch.object(VR, "page_files", return_value={"fixture.txt": b"fixture\n"}), \
                mock.patch.object(VR, "answer_lines", side_effect=lambda card, out: ["Results: " + str(out)]):
            for extra in (["--dry-run"], []):
                with contextlib.redirect_stdout(io.StringIO()) as stream:
                    VR.main(self.args + extra)
                outputs.append(stream.getvalue())
                if extra:
                    self.assertFalse(chosen.exists())
        self.assertEqual(calls.call_count, 1)
        self.assertEqual(calls.call_args.args[3], chosen)
        for text in outputs:
            self.assertIn(VR.FOLDER_FRESH.format(folder=P.display(chosen)), text)
            self.assertEqual(sum("CLI version" in line for line in text.splitlines()), 1)
        self.assertTrue(outputs[1].startswith(outputs[0]))
        self.assertTrue((chosen / VS.RUN_FILE).is_file())
        self.assertEqual((chosen / "fixture.txt").read_bytes(), b"fixture\n")


if __name__ == "__main__":
    unittest.main()
