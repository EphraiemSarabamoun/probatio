"""Default runs select a resumable or free sibling without weakening explicit output/resume rules.

Real folders exercise selection and CLI planning; call/scoring stand-ins do no inference. These tests also pin the
non-writing selection boundary, so probing an earlier run cannot alter its record or create a lock there.
"""
import contextlib
import errno
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import probatio as P


class Folders(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='probatio-default-'))
        self.addCleanup(shutil.rmtree, self.tmp)
        self.base = self.tmp / 'run'
        self.meta = {'agent': 'claude', 'model': 'fixture', 'memory_files': {'m': 'one'}, 'date': 'today'}

    def plant(self, folder=None, **changes):
        """A real saved identity, with a result marker that selection must leave alone."""
        folder = folder or self.base
        folder.mkdir(parents=True)
        (folder / 'run.json').write_text(json.dumps(dict(self.meta, **changes)))
        (folder / 'replies.jsonl').write_text('unchanged\n')
        return folder

    def pick(self):
        return P.default_folder(self.base, lambda out: P.resume_folder_reason(out, self.meta))

    def test_absent_base_is_selected_without_writing(self):
        out, notice = self.pick()
        self.assertEqual(out, self.base)
        self.assertIsNone(notice)
        self.assertFalse(self.base.exists())

    def test_compatible_base_is_resumed_without_notice(self):
        self.plant()
        out, notice = self.pick()
        self.assertEqual(out, self.base)
        self.assertIsNone(notice)

    def test_notice_reads_differing_keys_not_parentheses_in_install_path(self):
        self.base = self.tmp / 'install (old changed)' / 'run'
        self.plant(model='old')
        out, notice = self.pick()
        self.assertEqual(out, self.base.with_name('run-2'))
        self.assertIn('settings (model changed)', notice)
        self.assertNotIn('settings (old changed)', notice)

    def test_notice_names_four_differing_keys_then_the_remaining_count(self):
        self.meta = {key: 'new' for key in ('first', 'second', 'third', 'fourth', 'fifth', 'sixth', 'seventh')}
        self.plant(**{key: 'old' for key in self.meta})
        _, notice = self.pick()
        self.assertIn('(first, second, third, fourth, and 3 more changed)', notice)
        self.assertNotIn('fifth', notice)
        self.assertEqual(len(notice.splitlines()), 1)
        with self.assertRaises(SystemExit) as caught:
            P.check_resume(self.base, self.meta)
        self.assertEqual(caught.exception.code.changed, tuple(self.meta))
        self.assertIn('fifth, sixth, seventh changed', str(caught.exception))

    def test_first_resumable_sibling_precedes_a_free_one(self):
        self.plant(model='old')
        second = self.plant(self.tmp / 'run-2')
        before = {str(p): p.read_bytes() for p in self.tmp.rglob('*') if p.is_file()}
        out, notice = self.pick()
        self.assertEqual(out, second)
        self.assertIn('(model changed)', notice)
        self.assertIn(str(self.base), notice)
        self.assertIn(str(second), notice)
        self.assertEqual(notice.count('\n'), 0)
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.tmp.rglob('*') if p.is_file()})

    def test_memory_and_config_changes_do_not_mix_runs(self):
        self.plant(memory_files={'m': 'two'})
        self.plant(self.tmp / 'run-2', config_dir='/another')
        out, notice = self.pick()
        self.assertEqual(out, self.tmp / 'run-3')
        self.assertIn('(memory_files changed)', notice)
        self.assertFalse(out.exists())

    def test_stray_other_family_and_malformed_candidates_are_passed_over(self):
        self.base.mkdir()
        (self.base / 'replies.jsonl').write_text('stray')
        for number, filename, text in [(2, P.VERSUS_RUN_FILE, '{}'), (3, 'run.json', '{'), (4, 'run.json', 'null')]:
            folder = self.tmp / f'run-{number}'
            folder.mkdir()
            (folder / filename).write_text(text)
        (self.tmp / 'run-5').write_text('not a folder')
        (self.tmp / 'run-6').mkdir()
        out, _ = self.pick()
        self.assertEqual(out, self.tmp / 'run-6')

    def test_nonregular_record_never_gets_opened(self):
        self.base.mkdir()
        os.mkfifo(self.base / 'run.json')
        out, _ = self.pick()
        self.assertEqual(out, self.tmp / 'run-2')

    def test_deep_malformed_record_is_skipped_on_every_python(self):
        self.base.mkdir()
        (self.base / 'run.json').write_text('[' * 2000 + '0' + ']' * 2000)
        out, notice = self.pick()
        self.assertEqual(out, self.tmp / 'run-2')
        self.assertIn('cannot continue this command', notice)
        self.assertNotIn('holds an earlier run', notice)

    def test_siblings_follow_the_named_path_not_a_symlink_target(self):
        target = self.plant(self.tmp / 'target', model='old')
        os.symlink(target, self.base)
        out, _ = self.pick()
        self.assertEqual(out, self.tmp / 'run-2')
        self.assertNotEqual(out, self.tmp / 'target-2')

    def test_99th_candidate_is_accepted_then_exhaustion_is_plain(self):
        for folder in list(P.default_folder_names(self.base))[:-1]:
            self.plant(folder, model='old')
        out, _ = self.pick()
        self.assertEqual(out, self.tmp / 'run-99')
        self.plant(out, model='old')
        with self.assertRaises(SystemExit) as caught:
            self.pick()
        message = str(caught.exception)
        self.assertIn('99', message)
        self.assertIn(str(self.base), message)
        self.assertIn('--out', message)
        self.assertIn('clear old folders', message)
        self.assertNotIn('\n', message)
        self.assertFalse((self.tmp / 'run-100').exists())

    def test_unlookable_candidate_is_not_skipped(self):
        self.plant(model='old')
        os.symlink(self.tmp / ('x' * 300), self.tmp / 'run-2')
        with self.assertRaises(OSError) as caught:
            self.pick()
        self.assertEqual(caught.exception.errno, errno.ENAMETOOLONG)
        self.assertFalse((self.tmp / 'run-3').exists())

    def test_retired_or_unknown_recipe_is_not_a_new_identity(self):
        self.meta = dict(self.meta, probatio=P.PROBATIO_VERSION, tier='quick', recipe=P.PLAN_RECIPES['quick'],
                         plan_fingerprint='new')
        self.plant(recipe='quick@2', plan_fingerprint='old')
        out, _ = self.pick()
        self.assertEqual(out, self.tmp / 'run-2')

    def test_special_recipe_refusal_keeps_its_text_and_structured_notice_limit(self):
        self.meta = dict(self.meta, probatio=P.PROBATIO_VERSION, tier='quick', recipe=P.PLAN_RECIPES['quick'],
                         plan_fingerprint='new', effort='medium', config_dir='new')
        self.plant(agent='codex', model='old', memory_files={}, recipe='quick@2', plan_fingerprint='old',
                   effort='low', config_dir='old')
        with self.assertRaises(SystemExit) as caught:
            P.check_resume(self.base, self.meta)
        refusal = caught.exception.code
        self.assertTrue(str(refusal).startswith(f'{self.base} holds a quick@2 run whose plan is not this one'))
        self.assertTrue(str(refusal).endswith('so this probatio cannot continue it; pass another --out'))
        self.assertGreater(len(refusal.changed), 4)
        _, notice = self.pick()
        first = ', '.join(refusal.changed[:4])
        self.assertIn(first + f', and {len(refusal.changed) - 4} more changed', notice)
        self.assertEqual(len(notice.splitlines()), 1)

    def test_explicit_out_keeps_the_existing_refusal_and_record(self):
        self.plant(model='old')
        a = SimpleNamespace(out=str(self.base))
        out = P.choose_run_folder(a, {'name': 'irrelevant'}, False, self.meta)
        self.assertEqual(out, self.base)
        before = (out / 'run.json').read_bytes()
        with self.assertRaises(SystemExit) as caught:
            P.check_resume(out, self.meta)
        self.assertEqual(str(caught.exception), f'{out} holds a run with other settings (model changed). Pass another '
                         '--out to start a new run, or undo the change to resume this one.')
        self.assertEqual((out / 'run.json').read_bytes(), before)
        self.assertFalse((self.tmp / 'run-2').exists())


class Commands(Folders):
    """Exercise the actual v4/v3/comparison command paths, replacing calls/scoring only."""
    def options(self, a):
        return SimpleNamespace(model=a.model, effort=None, workdir=str(self.tmp), config_dir=self.tmp,
                               config_source='option', features=[], homes=None)

    def preflight(self, *args):
        return dict(self.meta)

    def score(self, out, *args):
        (out / 'scorecard.md').write_text('fixture score\n')
        return {}

    def invoke(self, words, dry=False, cross=False, v3=False):
        stdout = io.StringIO()
        chosen = []
        def execute(a, opts, bank, ordo_bank, run, meta, out, *args):
            chosen.append(out)
            out.mkdir(parents=True, exist_ok=True)
            P.check_resume(out, meta)
        with mock.patch.object(P, 'HERE', self.tmp), \
                mock.patch.object(P, 'agent_options', side_effect=self.options), \
                mock.patch.object(P, 'preflight_v4', side_effect=self.preflight), \
                mock.patch.object(P, 'preflight', side_effect=self.preflight), \
                mock.patch.object(P, 'preflight_runs', side_effect=lambda a,o,b,ob,s,sp,r: [dict(self.meta) for _ in r]), \
                mock.patch.object(P, 'run_calls', return_value={}) as calls, \
                mock.patch.object(P, 'score_run', side_effect=self.score), \
                mock.patch.object(P, 'write_results', return_value='fixture result'), \
                mock.patch.object(P, 'execute_run', side_effect=execute), \
                contextlib.redirect_stdout(stdout):
            P.main([*words, *(['--dry-run'] if dry else [])])
        return stdout.getvalue(), chosen, calls.call_args

    def v4_words(self):
        return ['--agent','claude','--target-type','organization','--target','Mozilla','--tier','smoke',
                '--class','I','--memory','off','--model','fixture']

    def assert_fresh_v4_base(self, entry):
        """An old usable entry keeps its exact destination for dry-run and execution, with no redirect notice."""
        base = self.tmp / 'results' / 'v4-claude-organization-mozilla-smoke-memory-off-fixture'
        base.parent.mkdir()
        expected = base
        if entry == 'dangling':
            expected = self.tmp / 'missing-target'
            base.symlink_to(expected)
        else:
            base.mkdir()
            if entry != 'empty':
                (base / entry).write_text('earlier file\n')
        dry, _, _ = self.invoke(self.v4_words(), dry=True)
        self.assertEqual(dry, '')
        self.assertFalse((expected / 'run.json').exists())
        real, _, calls = self.invoke(self.v4_words())
        self.assertEqual(calls.args[3], expected)
        self.assertNotIn('using ', real)
        self.assertNotIn('  folder: ', real)
        self.assertTrue((expected / 'run.json').is_file())
        self.assertFalse(base.with_name(base.name + '-2').exists())

    def test_v4_empty_base_is_used_without_notice(self):
        self.assert_fresh_v4_base('empty')

    def test_v4_lock_only_base_is_used_without_notice(self):
        self.assert_fresh_v4_base('run.lock')

    def test_v4_rendered_only_base_is_used_without_notice(self):
        self.assert_fresh_v4_base('rendered.jsonl')

    def test_v4_dangling_base_follows_its_free_target_without_notice(self):
        self.assert_fresh_v4_base('dangling')

    def test_v4_free_and_resumed_base_have_no_selection_output(self):
        base = self.tmp / 'results' / 'v4-claude-organization-mozilla-smoke-memory-off-fixture'
        self.assertEqual(self.invoke(self.v4_words(), dry=True)[0], '')
        self.assertFalse(base.exists())
        first, _, calls = self.invoke(self.v4_words())
        self.assertEqual(calls.args[3], base)
        self.assertEqual(self.invoke(self.v4_words(), dry=True)[0], '')
        resumed, _, calls = self.invoke(self.v4_words())
        self.assertEqual(calls.args[3], base)
        self.assertEqual(first, resumed)
        self.assertNotIn('  folder: ', resumed)
        self.assertNotIn('using ', resumed)

    def test_v3_free_and_resumed_base_have_no_selection_output(self):
        words = ['--agent','claude','--name','Jane Doe','--dataset','devotio','--tier','smoke','--model','fixture']
        base = self.tmp / 'results' / 'claude-devotio-smoke-fixture'
        with mock.patch.object(P, 'plan_calls', return_value=[]):
            self.assertEqual(self.invoke(words, dry=True, v3=True)[0], '')
            first, _, calls = self.invoke(words, v3=True)
            self.assertEqual(calls.args[3], base)
            self.assertEqual(self.invoke(words, dry=True, v3=True)[0], '')
            resumed, _, calls = self.invoke(words, v3=True)
        self.assertEqual(calls.args[3], base)
        self.assertEqual(first, resumed)
        self.assertNotIn('  folder: ', resumed)
        self.assertNotIn('using ', resumed)

    def test_v4_dry_run_names_the_same_sibling_real_run_uses_and_resumes(self):
        base = self.tmp / 'results' / 'v4-claude-organization-mozilla-smoke-memory-off-fixture'
        self.plant(base, memory_files={'m': 'old'})
        dry, _, _ = self.invoke(self.v4_words(), dry=True)
        expected = base.with_name(base.name + '-2')
        self.assertIn(f'using {expected}.', dry)
        self.assertFalse(expected.exists())
        real, _, calls = self.invoke(self.v4_words())
        self.assertIn(f'using {expected}.', real)
        self.assertEqual(calls.args[3], expected)
        before = (expected / 'run.json').read_bytes()
        again, _, calls = self.invoke(self.v4_words())
        self.assertEqual(calls.args[3], expected)
        self.assertEqual((expected / 'run.json').read_bytes(), before)
        self.assertFalse(base.with_name(base.name + '-3').exists())

    def test_multiple_runs_select_independently(self):
        words = ['--agent','claude','--compare','organization,nation','--target','organization=Mozilla',
                 '--target','nation=France','--tier','smoke','--class','I','--memory','off']
        a = P.parse_args_v4(words)
        runs = P.prepare_runs(a, False)[4]
        self.assertEqual(len(runs), 2)
        bases = [self.tmp / 'results' / run['name'] for run in runs]
        self.plant(bases[0], model='old')
        self.plant(bases[0].with_name(bases[0].name+'-2'))
        dry, _, _ = self.invoke(words, dry=True, cross=True)
        self.assertIn(str(bases[0])+' holds an earlier run', dry)
        self.assertNotIn(str(bases[1])+' holds an earlier run', dry)
        real, chosen, _ = self.invoke(words, cross=True)
        self.assertEqual(chosen, [bases[0].with_name(bases[0].name+'-2'), bases[1]])
        self.assertIn('using '+str(chosen[0]), real)

    def test_cross_type_run_uses_the_same_selector(self):
        words = ['--agent','claude','--compare','organization:nation','--tier','smoke','--class','I','--memory','off']
        run = P.prepare_runs(P.parse_args_v4(words), False)[4][0]
        base = self.tmp / 'results' / run['name']
        self.plant(base, model='old')
        _, chosen, _ = self.invoke(words, cross=True)
        self.assertEqual(chosen, [base.with_name(base.name+'-2')])

    def test_two_named_defaults_linked_to_one_old_run_choose_independent_siblings(self):
        words = ['--agent','claude','--compare','organization,nation','--target','organization=Mozilla',
                 '--target','nation=France','--tier','smoke','--class','I','--memory','off']
        runs = P.prepare_runs(P.parse_args_v4(words), False)[4]
        bases = [self.tmp / 'results' / run['name'] for run in runs]
        old = self.plant(self.tmp / 'old', model='old')
        bases[0].parent.mkdir()
        for base in bases:
            os.symlink(old, base)
        _, chosen, _ = self.invoke(words, cross=True)
        self.assertEqual(chosen, [base.with_name(base.name+'-2') for base in bases])

    def test_v3_explicit_dry_run_preserves_its_nonwriting_behavior(self):
        words = ['--agent','claude','--name','Jane Doe','--dataset','devotio','--tier','smoke',
                 '--out', str(self.tmp / ('x' * 300))]
        with mock.patch.object(P, 'plan_calls', return_value=[]):
            self.invoke(words, dry=True, v3=True)

    def test_v3_dry_run_and_execution_choose_the_same_default(self):
        words = ['--agent','claude','--name','Jane Doe','--dataset','devotio','--tier','smoke','--model','fixture']
        base = self.tmp / 'results' / 'claude-devotio-smoke-fixture'
        self.plant(base, model='old')
        with mock.patch.object(P, 'plan_calls', return_value=[]):
            dry, _, _ = self.invoke(words, dry=True, v3=True)
            real, _, calls = self.invoke(words, v3=True)
        expected = base.with_name(base.name+'-2')
        self.assertIn('using '+str(expected), dry)
        self.assertIn('using '+str(expected), real)
        self.assertEqual(calls.args[3], expected)


class Dependencies(unittest.TestCase):
    """Chosen recognition sources are stable identities for runs, scoring and rescore's read phase."""
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='probatio-dependencies-'))
        self.addCleanup(shutil.rmtree, self.tmp)
        self.results = self.tmp / 'results'
        self.results.mkdir()
        self.a = SimpleNamespace(out=None)
        self.runs = [{'name': name, 'kind': 'single', 'type': 'organization',
                      'roster': {'T': {'full': name}}} for name in ('asker', 'dependent')]
        self.metas = [{'agent': 'claude', 'model': 'fixture', 'labels': [],
                       'recognition': {'run': 'asker', 'asked': asked, 'type': 'organization'}}
                      for asked in (True, False)]

    def plant(self, name, meta):
        out = self.results / name
        out.mkdir(parents=True)
        (out / 'run.json').write_text(json.dumps(meta))
        (out / 'replies.jsonl').write_text(json.dumps({'id': name, 'answer': name, 'condition': 'with',
                                                     'dataset': 'recognition'}) + '\n')
        return out

    def choose(self, runs=None):
        with mock.patch.object(P, 'HERE', self.tmp):
            return P.choose_run_folders(self.a, self.runs if runs is None else runs, self.metas, announce=False)

    def test_asking_sibling_changes_dependent_identity_and_interrupted_rerun_resumes_both(self):
        old = self.plant('asker', dict(self.metas[0], model='earlier'))
        dependent = self.plant('dependent', self.metas[1])
        before = [(p, p.read_bytes()) for out in (old, dependent) for p in out.iterdir()]
        chosen, notices = self.choose()
        self.assertEqual([r['out'].name for r in self.runs], ['asker-2', 'dependent-2'])
        self.assertEqual(chosen[1]['recognition']['run'], 'asker-2')
        self.assertEqual(len(notices), 2)
        for run, meta in zip(self.runs, chosen):
            run['out'].mkdir()
            P.check_resume(run['out'], meta)
        again = [dict(run, **{}) for run in self.runs]
        for run in again:
            del run['out']
        resumed, _ = self.choose(again)
        self.assertEqual([r['out'] for r in again], [r['out'] for r in self.runs])
        self.assertEqual(resumed, chosen)
        self.assertEqual(before, [(p, p.read_bytes()) for p, _ in before])
        self.assertFalse((self.results / 'asker-3').exists())
        self.assertFalse((self.results / 'dependent-3').exists())

    def test_scoring_and_rescore_read_chosen_asking_replies_literally(self):
        self.plant('asker', dict(self.metas[0], model='earlier'))
        source = self.plant('asker-2', self.metas[0])
        chosen, _ = self.choose()
        dependent = self.runs[1]['out']
        expected = P.saved_replies(source / 'replies.jsonl')
        ordo = SimpleNamespace(recognition_status=mock.Mock(return_value={}), relabel=lambda labels, status: labels)
        with mock.patch.object(P, 'import_ordo', return_value=ordo), \
                mock.patch.object(P, 'recognition_checks', return_value=[]), \
                mock.patch.object(P, 'saved_replies', wraps=P.saved_replies) as read:
            scored = P.recognised(dependent, [], chosen[1], {})
            saved = SimpleNamespace(plan=SimpleNamespace(kind='single', calls=[]), meta=chosen[1])
            rescored = P.read_recognition(dependent, saved, {})
        self.assertEqual([call.args[0] for call in read.call_args_list], [source / 'replies.jsonl'] * 2)
        self.assertEqual([call.args[1] for call in ordo.recognition_status.call_args_list], [expected] * 2)
        self.assertEqual(scored['recognition_status']['asked_in'], 'asker-2')
        self.assertEqual(rescored, scored)

    def test_compatible_asking_alias_preserves_default_and_explicit_resume_identity(self):
        source = self.tmp / 'elsewhere' / 'physical'
        source.mkdir(parents=True)
        (source / 'run.json').write_text(json.dumps(self.metas[0]))
        os.symlink(source, self.results / 'asker')
        dependent = self.plant('dependent', self.metas[1])
        for explicit in (None, str(self.results)):
            self.a.out = explicit
            chosen, _ = self.choose()
            self.assertEqual(self.runs[0]['out'], source)
            self.assertEqual(self.runs[1]['out'], dependent)
            self.assertEqual(chosen, self.metas)
            P.resume_meta(dependent, chosen[1])

    def test_redirected_asking_alias_reference_reaches_physical_source(self):
        self.plant('asker', dict(self.metas[0], model='earlier'))
        source = self.tmp / 'elsewhere' / 'physical'
        source.mkdir(parents=True)
        (source / 'run.json').write_text(json.dumps(self.metas[0]))
        os.symlink(source, self.results / 'asker-2')
        chosen, _ = self.choose()
        dependent = self.runs[1]['out']
        reference = chosen[1]['recognition']['run']
        self.assertEqual((dependent.parent / reference).resolve(), source)
        self.assertNotEqual(reference, 'asker')

    def test_free_bases_add_no_preflight_output(self):
        output = io.StringIO()
        with mock.patch.object(P, 'HERE', self.tmp), contextlib.redirect_stdout(output):
            P.choose_run_folders(self.a, self.runs, self.metas)
        self.assertEqual(output.getvalue(), '')


if __name__ == '__main__':
    unittest.main()
