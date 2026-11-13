"""Tests for --rescore FOLDER (README, "Scoring a run again"): a finished v4 run folder, single-type or cross-type, is
scored again with this probatio and its result files written again, the replaced ones kept under previous/; it calls
no agent CLI, takes no other option, never touches the run's own records, and refuses plainly, writing nothing, a
folder that is not a v4 run folder, a run on another bank or with another plan, an unfinished run and a run another
probatio holds. The planted runs use the pinned banks, since --rescore takes no --bank. No test calls an agent or
the network.

    python3 -m unittest discover -s tests
"""
import collections
import contextlib
import fcntl
import hashlib
import io
import json
import multiprocessing
import os
import shutil
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))
sys.path.insert(0, str(REPO / "tests" / "fixtures"))
import ordo as O  # noqa: E402
import probatio as P  # noqa: E402
import test_ordo_scoring as TO  # noqa: E402
import test_v4_scoring as TV  # noqa: E402

RUN_RECORDS = ("run.json", "replies.jsonl", "quota.jsonl", "launches.jsonl", "run.lock")   # never touched


def no_agent(*args, **kwargs):
    raise AssertionError(f"--rescore started a process: {args[:1]}")


def tree(folder, skip=("previous",)):
    """{relative path: sha256} of every file under folder, outside the skipped top-level folders."""
    return {str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(Path(folder).rglob("*")) if p.is_file() and p.relative_to(folder).parts[0] not in skip}


def run_main(argv):
    """P.main(argv) with every way to start a process refused: (exit code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    code = 0
    with mock.patch.object(P.subprocess, "run", side_effect=no_agent), \
            mock.patch.object(P.subprocess, "Popen", side_effect=no_agent), \
            mock.patch.object(P.shutil, "which", side_effect=no_agent), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            P.main(argv)
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else 1
            if not isinstance(e.code, int) and e.code is not None:
                err.write(str(e.code))
    return code, out.getvalue(), err.getvalue()


def single_run(out):
    """A finished single-type smoke run on the pinned bank, scored once as the run scores it, with a quota log and a
    launches log; returns run.json's record."""
    b = P.load_bank(P.BANK, P.BANK_MANIFEST_SHA256)
    roster = P.resolve_target(b, "organization", "Mozilla")
    calls = P.plan_v4(b, roster, "smoke", P.V4_CLASSES, ("with",))
    TV.plant(out, calls, TV.favour(0.8, 0.4), TV.errand_favour(0.9, 0.3))
    meta = TV.run_meta(b, roster, calls, "smoke", "on", P.V4_CLASSES)
    (out / "run.json").write_text(json.dumps(meta, indent=2))
    (out / "quota.jsonl").write_text('{"kind": "fake"}\n')
    (out / "launches.jsonl").write_text('{"started": "2026-11-11T00:00:00Z", "ended": "2026-11-11T00:07:00Z", '
                                        '"made": 1, "jobs": 4}\n')
    (out / "run.lock").write_text("12345")                               # the run's own lock file, as claim leaves it
    P.score_run(out, calls, b, roster, meta, B=50)
    return meta


def cross_run(out):
    """A finished quick triangle on the pinned ordo bank with a named organization, scored once and written as the
    run writes it; returns run.json's record."""
    b = P.load_bank(P.BANK, P.BANK_MANIFEST_SHA256)
    ob = O.load_ordo_bank()
    rosters = {t: {"controls": recs, "named": [dict(TO.mozilla(), type=t)] if t == "organization" else []}
               for t, recs in O.name_sets(ob).items()}
    cross = {"compare": "individual:organization,organization:nation,individual:nation", "types": list(O.TYPES),
             "pairs": [list(p) for p in O.PAIRS], "triangle": True}
    calls = P.ordo_plan(ob, cross, rosters, "quick", ("I", "II"), ("with",))
    TO.plant(out, calls, TO.names_policy, TO.by_type({"individual": 0.8, "organization": 0.5, "nation": 0.3}))
    meta = {"probatio": P.PROBATIO_VERSION, "agent": "claude", "cli": "0.0-fake", "agent_version": "0.0-fake",
            "model": "sonnet", "effort": "medium", "workdir": "/tmp", "features": [], "memory_files": {},
            "compare": "individual:organization:nation", "types": list(O.TYPES), "pairs": cross["pairs"],
            "triangle": True, "registered": True, "tier": "quick", "recipe": "quick@3", "memory": "on",
            "classes": ["I", "II"], "auto_memory": "as-configured", "named": ["the Mozilla Foundation"],
            "custom_controls": [], "fixtures": "baseline", "labels": ["cross-type: pilot only, not calibrated",
                                                                      "fixtures: baseline"],
            "fixtures_sha256": P.fixtures_sha256(P.load_entities(None, b), list(O.TYPES)),
            "rosters": rosters, "bank_sha256": b.sha256, "ordo_bank_sha256": ob.sha256,
            "plan_fingerprint": P.plan_fingerprint(calls), "date": "2026-11-11 00:00 UTC"}
    (out / "run.json").write_text(json.dumps(meta, indent=2))
    (out / "run.lock").write_text("12345")
    O.write_results(out, O.score(out, calls, ob, "claude", meta, B=50))
    return meta


class TestRescoreRefuses(unittest.TestCase):
    """Every refusal is one plain message, exits nonzero and writes nothing."""

    @classmethod
    def setUpClass(cls):
        cls.base = Path(tempfile.mkdtemp())
        single_run(cls.base / "single")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.base)

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.run = self.tmp / "run"
        shutil.copytree(self.base / "single", self.run)

    def refused(self, argv, folder=None, code=None):
        folder = folder or self.run
        before = tree(folder, skip=())
        got, out, err = run_main(argv)
        self.assertNotEqual(got, 0)
        if code is not None:
            self.assertEqual(got, code)
        self.assertEqual(tree(folder, skip=()), before)                       # nothing written
        self.assertEqual(out, "")
        return err

    def test_it_takes_no_other_option(self):
        for argv in (["--rescore", str(self.run), "--tier", "quick"], ["--agent", "claude", "--rescore", str(self.run)],
                     [f"--rescore={self.run}", "--watch"], ["--rescore", str(self.run), "--jobs", "4"]):
            err = self.refused(argv, code=2)
            self.assertEqual(err.strip(), P.RESCORE_ALONE, argv)
        self.assertFalse(P.wants_v3(["--rescore", str(self.run), "--seed", "1"]))        # never v3's: refused above

    def test_a_folder_that_is_not_a_v4_run(self):
        empty = self.tmp / "empty"
        empty.mkdir()
        self.assertIn("not a probatio run folder", self.refused(["--rescore", str(empty)], empty))
        v3 = self.tmp / "v3"
        v3.mkdir()
        (v3 / "run.json").write_text(json.dumps({"agent": "claude", "name": "Jane Doe", "dataset": "all",
                                                 "tier": "smoke"}))
        self.assertIn("holds a probatio v3 run", self.refused(["--rescore", str(v3)], v3))

    def test_another_bank_or_another_plan(self):
        meta = json.loads((self.run / "run.json").read_text())
        (self.run / "run.json").write_text(json.dumps(dict(meta, bank_sha256="0" * 64)))
        self.assertIn("was run on another bank", self.refused(["--rescore", str(self.run)]))
        (self.run / "run.json").write_text(json.dumps(dict(meta, plan_fingerprint="0" * 64)))
        self.assertIn("is not the plan this run made", self.refused(["--rescore", str(self.run)]))

    def test_an_unfinished_run(self):
        path = self.run / "replies.jsonl"
        lines = path.read_bytes().splitlines(keepends=True)
        path.write_bytes(b"".join(lines[:-3]))
        err = self.refused(["--rescore", str(self.run)])
        self.assertIn(f"3 of {len(lines)} planned calls have no reply", err)
        self.assertIn("run the command that started it again", err)
        path.write_bytes(b"".join(lines) + b'{"condition": "with", "da')                 # a torn last line
        self.assertIn("unfinished line", self.refused(["--rescore", str(self.run)]))

    def test_a_run_another_probatio_holds(self):
        with open(self.run / "run.lock", "a") as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertIn("Another probatio is using", self.refused(["--rescore", str(self.run)]))


def snapshot(folder):
    """{relative path: what lstat sees}: a link's target, a file's sha256 and mode, a folder's mode; the whole folder,
    links never followed, so "unchanged" means byte for byte and kind for kind."""
    out = {}
    for p in sorted(Path(folder).rglob("*")):
        st = os.lstat(p)
        rel = str(p.relative_to(folder))
        if stat.S_ISLNK(st.st_mode):
            out[rel] = ("link", os.readlink(p))
        elif stat.S_ISDIR(st.st_mode):
            out[rel] = ("folder", stat.S_IMODE(st.st_mode))
        else:
            out[rel] = ("file", hashlib.sha256(p.read_bytes()).hexdigest() if os.access(p, os.R_OK) else "unreadable",
                        stat.S_IMODE(st.st_mode))
    return out


class TestRescoreHostile(unittest.TestCase):
    """A folder --rescore did not make (the independent review's round 2): a link where a result file or previous/
    goes, a damaged replies.jsonl, an unreadable run.json, previous/ as a file, a read-only folder or result file, and
    --rescore with any other option. Each refuses in one plain line, exits nonzero (2 for an option) and leaves the
    folder, and anything outside it, exactly as it was. And all or nothing: a failure after the first new file is in
    place puts every file back."""

    @classmethod
    def setUpClass(cls):
        cls.base = Path(tempfile.mkdtemp())
        single_run(cls.base / "single")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.base)

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(self.cleanup)
        self.run = self.tmp / "run"
        shutil.copytree(self.base / "single", self.run)

    def cleanup(self):
        for p in [self.tmp] + list(self.tmp.rglob("*")):                 # a test may leave a folder read-only
            if p.is_dir() and not p.is_symlink():
                p.chmod(0o755)
        shutil.rmtree(self.tmp, True)

    def refused(self, argv=None, code=None, also=()):
        """Run --rescore on the run folder (or argv) and assert one plain line on stderr, nothing on stdout, a
        nonzero exit (code when given) and the folder and every path in also unchanged; returns the line."""
        watched = [self.run] + list(also)
        before = [snapshot(f) if f.is_dir() else f.read_bytes() for f in watched]
        got, out, err = run_main(argv or ["--rescore", str(self.run)])
        self.assertNotEqual(got, 0, err)
        if code is not None:
            self.assertEqual(got, code)
        self.assertEqual(out, "")
        self.assertEqual(len(err.strip().splitlines()), 1, err)
        self.assertNotIn("Traceback", err)
        self.assertEqual([snapshot(f) if f.is_dir() else f.read_bytes() for f in watched], before)
        return err.strip()

    def root(self):
        return os.geteuid() == 0                                             # access checks do not bind root

    def test_a_result_file_that_is_a_link(self):
        outside = self.tmp / "outside.md"
        outside.write_text("not the run's\n")
        (self.run / "report.md").unlink()
        (self.run / "report.md").symlink_to(outside)
        line = self.refused(also=[outside])
        self.assertIn("report.md is a symbolic link", line)

    def test_previous_that_is_a_link(self):
        outside = self.tmp / "elsewhere"
        outside.mkdir()
        (self.run / "previous").symlink_to(outside, target_is_directory=True)
        self.assertIn("previous is a symbolic link", self.refused(also=[outside]))
        self.assertEqual(list(outside.iterdir()), [])

    def test_run_lock_that_is_a_link(self):
        outside = self.tmp / "lock-elsewhere"
        (self.run / "run.lock").unlink()
        (self.run / "run.lock").symlink_to(outside)
        self.assertIn("run.lock is a symbolic link", self.refused())
        self.assertFalse(outside.exists())

    def test_previous_that_is_a_file(self):
        (self.run / "previous").write_text("existing data")
        self.assertIn("previous is not a folder", self.refused())

    def test_a_read_only_result_file(self):
        if self.root():
            self.skipTest("root writes anything")
        (self.run / "report.md").chmod(0o444)
        self.assertIn("report.md is not writable", self.refused())

    def test_a_read_only_folder(self):
        if self.root():
            self.skipTest("root writes anything")
        self.run.chmod(0o555)
        self.assertIn("is not writable", self.refused())

    def test_an_unreadable_run_json(self):
        if self.root():
            self.skipTest("root reads anything")
        (self.run / "run.json").chmod(0)
        self.assertIn("run.json cannot be read", self.refused())

    def test_a_damaged_replies_file(self):
        path = self.run / "replies.jsonl"
        good = path.read_bytes()
        record = json.loads(good.splitlines()[0])
        for bad in (b"{broken json", b"null", b"[]", b"",                          # not a JSON object
                    json.dumps({k: record[k] for k in ("condition", "dataset", "id")}).encode(),   # no error, seconds
                    json.dumps(dict(record, seconds=True)).encode()):            # a bool for a number
            lines = good.splitlines()
            path.write_bytes(b"\n".join(lines[:5] + [bad] + lines[5:]) + b"\n")
            line = self.refused()
            self.assertIn("replies.jsonl: line 6 is not a saved reply", line, bad)
        path.write_bytes(good)
        self.assertEqual(P.read_replies(path), P.strict_replies(self.run))     # a sound file reads alike both ways

    def test_any_other_option_is_one_line_before_any_parser(self):
        for extra in (["--seed", "1"], ["--dataset", "all"], ["--class", "garbage"], ["--unknown"], ["--help"],
                      ["--tier", "quick"], ["--watch"], ["--ui"]):
            for argv in (["--rescore", str(self.run)] + extra, extra + ["--rescore", str(self.run)],
                         [f"--rescore={self.run}"] + extra):
                self.assertEqual(self.refused(argv, code=2), P.RESCORE_ALONE, argv)
        self.assertEqual(self.refused(["--rescore"], code=2), P.RESCORE_ALONE)
        self.assertEqual(self.refused(["--resc", str(self.run), "--seed", "1"], code=2), P.RESCORE_ALONE)

    def test_a_failure_after_the_first_move_puts_everything_back(self):
        before = snapshot(self.run)
        real, calls = os.replace, []

        def flaky(src, dst):
            calls.append((Path(src), Path(dst)))
            if len(calls) == 2:                                              # the second new file fails to move
                raise PermissionError(13, "Permission denied", str(dst))
            return real(src, dst)
        with mock.patch.object(P.os, "replace", side_effect=flaky):
            line = self.refused()
        self.assertTrue(calls[0][0].parent.name.startswith(P.RESCORE_TEMP))   # the first move had happened
        self.assertEqual(calls[0][1], self.run / P.RESULT_FILES[0])
        self.assertIn("could not write its results: Permission denied", line)
        self.assertIn("the folder is as it was", line)
        self.assertEqual(snapshot(self.run), before)

    def test_a_failure_while_writing_the_new_files_leaves_nothing(self):
        before = snapshot(self.run)
        real = P.write_scorecard

        def half(folder, card, units, extras, memory=None):      # write_scorecard's parameters, the head's memory too
            (Path(folder) / "scorecard.json").write_text("{}")
            raise OSError(28, "No space left on device", str(Path(folder) / "units.jsonl"))
        with mock.patch.object(P, "write_scorecard", side_effect=half):
            self.assertIn("No space left on device", self.refused())
        self.assertEqual(snapshot(self.run), before)
        self.assertIs(P.write_scorecard, real)

    def test_a_failure_while_keeping_the_old_files_leaves_nothing(self):
        before = snapshot(self.run)
        real, n = P.copy_unfollowed, []

        def third(src, dst):
            n.append(src)
            if len(n) == 3:
                raise OSError(5, "Input/output error", str(src))
            return real(src, dst)
        with mock.patch.object(P, "copy_unfollowed", side_effect=third):
            self.assertIn("Input/output error", self.refused())
        self.assertEqual(snapshot(self.run), before)

    def test_the_cross_type_folder_ordo_that_is_a_link(self):
        cross = self.tmp / "cross"
        cross_run(cross)
        outside = self.tmp / "ordo-elsewhere"
        (cross / "ordo").rename(outside)
        (cross / "ordo").symlink_to(outside, target_is_directory=True)
        self.run = cross
        self.assertIn("ordo is a symbolic link", self.refused(also=[outside]))

    def test_a_sound_folder_still_rescores_and_leaves_no_temporary_folder(self):
        before = snapshot(self.run)
        code, out, err = run_main(["--rescore", str(self.run)])
        self.assertEqual((code, err), (0, ""))
        after = snapshot(self.run)
        self.assertFalse([k for k in after if k.startswith(P.RESCORE_TEMP)])
        stamp = next((self.run / "previous").iterdir())
        for f in P.RESULT_FILES:                                             # the old files, byte for byte, kept
            self.assertEqual(snapshot(stamp)[f], before[f])
        self.assertEqual({k: v for k, v in after.items() if k in RUN_RECORDS or k.startswith("episodes")},
                         {k: v for k, v in before.items() if k in RUN_RECORDS or k.startswith("episodes")})

    def edit_meta(self, folder, **fields):
        """Rewrite folder's run.json with fields replaced (a value of DROP removes the field)."""
        meta = json.loads((folder / "run.json").read_text())
        meta.update(fields)
        meta = {k: v for k, v in meta.items() if v is not DROP}
        (folder / "run.json").write_text(json.dumps(meta))

    def test_fields_of_the_wrong_type_are_one_line(self):
        """The final reviews' F5 and R3: a run.json field or nested record that leaves the writer's own shape (classes
        null, bank_sha256 a number, a recognition record whose controls are a number, a roster of numbers), a needed
        field missing, an unknown memory mode, class or type, and a target record cut short are each refused in one
        plain line naming the field, where run.json is read, before anything is written; never a traceback."""
        cases = [({"classes": None}, '"classes" holds null, not a list'),
                 ({"bank_sha256": 12}, '"bank_sha256" holds a whole number, not text'),
                 ({"memory": ["on"]}, '"memory" holds a list, not text'),
                 ({"memory": "sometimes"}, '"memory" holds "sometimes"'),
                 ({"classes": ["I", 2]}, '"classes/1" holds a whole number, not text'),
                 ({"classes": ["IV"]}, '"classes" holds something other'),
                 ({"plan_fingerprint": DROP}, '"plan_fingerprint" is missing'),
                 ({"target": {"key": "x"}}, '"target/Full" is missing'),
                 ({"target_type": "planet"}, '"target_type" names a type'),
                 ({"tier": 3}, '"tier" holds a whole number'), ({"recognition": "yes"}, '"recognition" holds text'),
                 ({"recognition": {"asked": False, "controls": 12, "type": "organization", "run": "x",
                                   "ordo_bank_sha256": None}},
                  '"recognition/controls" holds a whole number, not a list'),
                 ({"recognition": {"asked": False, "controls": []}}, '"recognition/ordo_bank_sha256" is missing'),
                 ({"labels": "a label"}, '"labels" holds text, not a list'),
                 ({"agent": True}, '"agent" holds true or false, not text'), ({"roster": []}, '"roster" holds a list'),
                 ({"roster": {"comparators": 5}}, '"roster/controls" is missing'),
                 ({"roster": {"comparators": [7], "controls": [], "reserves": []}},
                  '"roster/comparators/0" holds a whole number'),
                 ({"roster": {"comparators": [], "controls": [], "reserves": []}},
                  '"roster/comparators" holds an empty list, not a list the writer never leaves empty'),
                 ({"memory_bytes": {"a": "big"}}, '"memory_bytes/a" holds text, not a whole number'),
                 ({"features": [None]}, '"features/0" holds null, not text')]
        original = (self.run / "run.json").read_bytes()
        for fields, words in cases:
            (self.run / "run.json").write_bytes(original)
            self.edit_meta(self.run, **fields)
            line = self.refused()
            self.assertIn(words, line, fields)
            self.assertTrue(line.startswith(f"{self.run}: run.json's "), line)

    def test_a_defect_in_the_scoring_still_raises(self):
        """A record check_run_fields accepts reaches the scoring, and an error raised there is probatio's own: it
        is not turned into a refusal."""
        with mock.patch.object(P, "card_of", side_effect=ZeroDivisionError("a planted defect")):
            with self.assertRaises(ZeroDivisionError):
                P.rescore_folder(self.run)

    def test_a_cross_type_record_of_the_wrong_type(self):
        cross = self.tmp / "cross"
        cross_run(cross)
        self.run = cross
        original = (cross / "run.json").read_bytes()
        for fields, words in (({"rosters": []}, '"rosters" holds a list'), ({"triangle": "yes"}, '"triangle" holds text'),
                              ({"types": DROP}, '"types" is missing'), ({"ordo_bank_sha256": 7}, '"ordo_bank_sha256"'),
                              ({"rosters": {"individual": {"controls": "x", "named": []}}},
                               '"rosters/individual/controls" holds text, not a list'),
                              ({"rosters": {"individual": {"controls": []}}}, '"rosters/individual/named" is missing'),
                              ({"pairs": [["individual"]]}, "names a type or a pair"),
                              ({"types": ["individual", "planet"]}, '"planet"')):
            (cross / "run.json").write_bytes(original)
            self.edit_meta(cross, **fields)
            self.assertIn(words, self.refused(), fields)
        (cross / "run.json").write_bytes(original)
        rosters = json.loads(original)["rosters"]
        self.edit_meta(cross, rosters={t: r for t, r in rosters.items() if t != "nation"})    # a whole type gone
        self.assertIn('"rosters" holds the types individual, organization, not the comparison\'s', self.refused())

    def test_a_folder_name_with_controls_is_shown_escaped(self):
        hostile = self.tmp / "run\x1b[31m\u202e\nline"
        shutil.copytree(self.run, hostile)
        self.run = hostile
        self.edit_meta(hostile, classes=None)
        line = self.refused()
        self.assertTrue(line.startswith(P.display(hostile) + ": run.json's"), line)
        self.assertFalse([ch for ch in line if P.unseen(ch)])


DROP = object()                                     # edit_meta's mark for a field to remove


# ---- a saved record --rescore cannot read: one plain line, from the READ phase (the third re-check's F5) -----------------
GUESS_B = 30                                        # the bootstrap's replicates in these cases: the outcome, not the
                                                    # intervals, is what they test
REAL_O_SCORE = O.score


def small_score(*args, **kwargs):
    kwargs["B"] = GUESS_B
    return REAL_O_SCORE(*args, **kwargs)


def card_without_stamp(folder, kind):
    """The rescored scorecard.json of a folder, its rescored record (when, by which probatio) left out."""
    card = json.loads((Path(folder) / ("scorecard.json" if kind == "single" else "ordo/scorecard.json")).read_text())
    card.pop("rescored", None)
    return card


def key_paths(value, path=()):
    """Every key path in a JSON record, containers and leaves alike, with its value."""
    if path:
        yield path, value
    if isinstance(value, dict):
        for k, v in value.items():
            yield from key_paths(v, path + (k,))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from key_paths(v, path + (i,))


def mutations(value):
    """What the generator does to one value: delete it, null, a value of each other JSON type, an empty container."""
    out = [("deleted", None)] + ([("null", None)] if value is not None else [])
    for name, other in (("text", "x"), ("whole number", 7), ("number", 0.5), ("true", True), ("list", [1]),
                        ("object", {"k": 1})):
        if type(other) is not type(value):
            out.append((name, other))
    if isinstance(value, list) and value:
        out.append(("empty list", []))
    if isinstance(value, dict) and value:
        out.append(("empty object", {}))
    return out


def mutated(meta, path, name, new):
    """meta with the value at path deleted (name "deleted") or replaced by new."""
    meta = json.loads(json.dumps(meta))
    parent = meta
    for step in path[:-1]:
        parent = parent[step]
    if name == "deleted":
        del parent[path[-1]]
    else:
        parent[path[-1]] = new
    return meta


def mutation_case(job):
    """One generator case, in a worker: a copy of the folder with one key path mutated, rescored through main with
    every way to start a process refused; (kind, path, mutation, outcome, problems)."""
    kind, folder, path, name, new, root, untouched = job
    run = Path(tempfile.mkdtemp(dir=root)) / "run"
    shutil.copytree(folder, run)
    meta = json.loads((run / "run.json").read_text())
    (run / "run.json").write_text(json.dumps(mutated(meta, path, name, new)))
    before = snapshot(run)
    code, out, err = run_main(["--rescore", str(run)])
    problems = []
    if "Traceback" in out + err:
        problems.append("a traceback")
    if code != 0:
        outcome = "refused"
        if len(err.strip().splitlines()) != 1:
            problems.append("not one line")
        if snapshot(run) != before:
            problems.append("the folder changed")
    else:
        outcome = "same card" if card_without_stamp(run, kind) == untouched else "accepted, another card"
    shutil.rmtree(run.parent, ignore_errors=True)
    return kind, "/".join(map(str, path)), name, outcome, problems


class TestSavedRecordRead(unittest.TestCase):
    """--rescore reads a saved record in one guarded phase and checks it against the writer's own shape: the third
    re-check's cases (a recognition record without its roster, an empty roster, a roster without labels, a nested
    entity without its key or its short form, a roster without its controls or named targets) and a generator that
    mutates every key path of a valid single-type record and a valid comparison's are each one plain line with
    nothing written, or the same card as the untouched folder; never a traceback. A defect in the scoring still
    raises (TestRescoreHostile.test_a_defect_in_the_scoring_still_raises)."""

    @classmethod
    def setUpClass(cls):
        cls.base = Path(tempfile.mkdtemp())
        cls.patches = [mock.patch.object(P, "BOOTSTRAP_B", GUESS_B), mock.patch.object(O, "score", small_score)]
        for p in cls.patches:
            p.start()
        single_run(cls.base / "single")
        cross_run(cls.base / "cross")
        cls.untouched = {}
        for kind in ("single", "cross"):
            shutil.copytree(cls.base / kind, cls.base / f"{kind}-again")
            code, _, err = run_main(["--rescore", str(cls.base / f"{kind}-again")])
            assert code == 0, err
            cls.untouched[kind] = card_without_stamp(cls.base / f"{kind}-again", kind)

    @classmethod
    def tearDownClass(cls):
        for p in cls.patches:
            p.stop()
        shutil.rmtree(cls.base, ignore_errors=True)

    def outcome(self, kind, change):
        run = Path(tempfile.mkdtemp(dir=self.base)) / "run"
        shutil.copytree(self.base / kind, run)
        meta = json.loads((run / "run.json").read_text())
        change(meta)
        (run / "run.json").write_text(json.dumps(meta))
        before = snapshot(run)
        code, out, err = run_main(["--rescore", str(run)])
        self.assertNotIn("Traceback", out + err)
        if code:
            self.assertEqual(len(err.strip().splitlines()), 1, err)
            self.assertEqual(snapshot(run), before)
        return code, err.strip()

    def test_the_recheck_cases(self):
        rec = {"asked": False, "controls": [], "type": "organization", "run": "x"}
        cases = [("recognition-no-roster", "single", lambda m: m.update(recognition=dict(rec))),
                 ("recognition-empty-roster", "single", lambda m: m.update(recognition=dict(rec), roster={})),
                 ("recognition-no-labels", "single", lambda m: m.update(recognition=dict(rec), roster={"controls": []}))]
        for typ in O.TYPES:
            for key_ in ("key", "full", "short", "possessive", "type", "kind"):
                cases.append((f"control-{typ}-{key_}", "cross",
                              lambda m, typ=typ, key_=key_: m["rosters"][typ]["controls"][0].pop(key_, None)))
        for key_ in ("controls", "named"):
            cases.append((f"roster-missing-{key_}", "cross", lambda m, key_=key_: m["rosters"]["organization"].pop(key_)))
        refused = 0
        for label, kind, change in cases:
            code, line = self.outcome(kind, change)
            if code:
                refused += 1
                self.assertIn(": run.json's ", line, label)
            else:
                self.assertTrue(label.endswith("-possessive"), label)      # no such key: the record is whole
        self.assertEqual((len(cases), refused), (23, 20))

    def test_a_reading_error_past_the_shape_check_is_one_line(self):
        """Should the shape check let a record through that a reader then cannot use, the READ phase's guard still
        refuses in one line (SAVED_UNREADABLE), naming the key."""
        with mock.patch.object(P, "check_run_fields", lambda *a: None):
            code, line = self.outcome("single", lambda m: m.update(recognition={"asked": False, "controls": [],
                                                                                 "type": "organization", "run": "x"}))
        self.assertEqual(code, 1)
        self.assertIn("its saved run record is not one this probatio can read", line)
        self.assertIn('"roster"', line)
        self.assertIn("Nothing was changed.", line)

    def test_every_key_path_mutated(self):
        """Every key path of the valid single-type record and the valid comparison's, deleted, nulled, given a
        value of each other JSON type and emptied, rescored: no traceback, and a refusal in one line with nothing
        written, the same card, or (listed) another card from a value the writer itself writes."""
        root = tempfile.mkdtemp(dir=self.base)
        jobs = []
        for kind in ("single", "cross"):
            meta = json.loads((self.base / kind / "run.json").read_text())
            for path, value in key_paths(meta):
                for name, new in mutations(value):
                    jobs.append((kind, str(self.base / kind), path, name, new, root, self.untouched[kind]))
        try:
            pool = multiprocessing.get_context("fork").Pool(min(8, os.cpu_count() or 1))
        except ValueError:
            rows = [mutation_case(j) for j in jobs]
        else:
            with pool:
                rows = pool.map(mutation_case, jobs, chunksize=4)
        failed = [r for r in rows if r[4]]
        self.assertEqual(failed, [], f"{len(failed)} of {len(rows)} cases")
        counts = collections.Counter(r[3] for r in rows)
        self.assertGreater(counts["refused"], len(rows) // 2)
        TestSavedRecordRead.generated = rows             # tools may read the table (the release notes print it)
        report = Path(os.environ.get("PROBATIO_MUTATION_TABLE", "")) if os.environ.get("PROBATIO_MUTATION_TABLE") \
            else None
        if report:
            report.write_text(json.dumps({"cases": len(rows), "outcomes": dict(counts),
                                          "accepted": [r[:4] for r in rows if r[3] != "refused"]}, indent=1))


class TestRescore(unittest.TestCase):
    """A finished run of each kind scored again: the answer printed, the old result files kept under previous/<stamp>/
    in the same layout, the run's own records untouched, and the card and head saying it was rescored."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def rescored(self, folder, results):
        old = tree(folder)
        records = {f: h for f, h in old.items() if f in RUN_RECORDS or f.startswith("episodes/")}
        code, out, err = run_main(["--rescore", str(folder)])
        self.assertEqual((code, err), (0, ""))
        stamps = list((folder / "previous").iterdir())
        self.assertEqual(len(stamps), 1)
        self.assertRegex(stamps[0].name, r"^\d{8}T\d{6}Z$")
        kept = tree(stamps[0], skip=())
        self.assertEqual(sorted(kept), sorted(results))
        self.assertEqual(kept, {f: old[f] for f in results})                  # the old files, byte for byte
        new = tree(folder)
        self.assertEqual({f: new[f] for f in records}, records)              # run.json, replies, episodes, logs
        self.assertEqual(sorted(set(new) - set(old)), [])                    # nothing else written
        return out

    def test_a_single_type_run(self):
        run = self.tmp / "single"
        meta = single_run(run)
        out = self.rescored(run, P.RESULT_FILES)
        card = json.loads((run / "scorecard.json").read_text())
        self.assertEqual(set(card["rescored"]), {"at", "by", "scored_by"})
        self.assertEqual((card["rescored"]["by"], card["rescored"]["scored_by"]),
                         (P.PROBATIO_VERSION, meta["probatio"]))
        line = P.rescored_line(card["rescored"])
        self.assertIn(line, (run / "scorecard.md").read_text().splitlines())
        self.assertIn(line, out)
        self.assertIn("Class I   professed", out)

    def test_a_cross_type_run(self):
        run = self.tmp / "cross"
        cross_run(run)
        before = json.loads((run / "ordo" / "scorecard.json").read_text())
        out = self.rescored(run, [f"ordo/{f}" for f in O.RESULT_FILES])
        card = json.loads((run / "ordo" / "scorecard.json").read_text())
        self.assertEqual(card["rescored"]["by"], P.PROBATIO_VERSION)
        text = (run / "ordo" / "scorecard.md").read_text()
        lines = text.splitlines()
        self.assertEqual(lines[1], P.rescored_line(card["rescored"]) + "  ")    # the head's second line
        self.assertIn(f"**{O.NAMES_HEADING}**", lines)
        self.assertIn("the Mozilla Foundation (organization), Class I", text)
        self.assertIn(text.strip(), out)
        for k in ("value", "ci"):                                             # the same numbers, B aside
            self.assertEqual(card["conditions"]["with"]["rows"][0]["pairs"]["individual:organization"]["value"],
                             before["conditions"]["with"]["rows"][0]["pairs"]["individual:organization"]["value"], k)

    def test_the_result_files_are_the_writers(self):
        run = self.tmp / "single"
        single_run(run)
        self.assertEqual(sorted(f for f in tree(run) if "/" not in f and f not in RUN_RECORDS), sorted(P.RESULT_FILES))
        cross = self.tmp / "cross"
        cross_run(cross)
        self.assertEqual(sorted(tree(cross / "ordo")), sorted(O.RESULT_FILES))


if __name__ == "__main__":
    unittest.main()
