"""A versus run is scored only as itself (versus.begin_run, read_run, replan, check_reply, identity_hash): a run folder
saves its identity before its first call; scoring rebuilds the plan from that identity with the bank the caller loads
and refuses, in one line, a run whose identity does not rebuild or whose replies are not its own. These are the
independent check's two blocking findings, rerun as tests: another roster's replies scored under a plan of other
names, and a decoding map changed beside the replies. Each tamper below (a choice mapping, a factor, a family, an
arm, the identity itself) must end in a refusal, never in a reversed result. The re-check's findings follow: every
raw record of a folder is read and checked before retries are folded, as probatio's run loop writes them (RawRecords,
each sequence by name); a codebook belongs to one execution and to the reply texts it coded (Codebook); the memory
record has one validator for the writer and the reader; and the re-check's own probes are rerun (ReviewerProbes), as
is a run stopped and resumed by probatio's own loop (Interrupted). No network, no model.

    python3 -m unittest tests.test_versus_identity
"""
import contextlib
import copy
import hashlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))
import ordo  # noqa: E402
import probatio as P  # noqa: E402
import versus as VS  # noqa: E402
from test_versus_fixture import full_bank, full_bank_folder  # noqa: E402
from test_versus_scoring import answer  # noqa: E402

OB = ordo.load_ordo_bank()
MINE = {"individual": "Mera Vale", "organization": "Oriel Works"}
THEIRS = {"individual": "Iven Kest", "organization": "Velora Guild"}


def run(out, names, rule="first_name", tier="quick", arms=None, memory=None):
    """A run of names through probatio's own run_calls with a stand-in that answers by rule, begun with begin_run."""
    p = VS.plan(full_bank(), names, tier, arms or {}, OB)
    VS.begin_run(out, p, memory or {})
    opts = SimpleNamespace(model=None, effort="medium", workdir=str(out), quota=None)
    standin = lambda prompt, condition, opts, server=None: (answer(rule, prompt), None, "standin-model")  # noqa: E731
    with mock.patch.dict(P.CALLS, {"standin": standin}), contextlib.redirect_stderr(io.StringIO()):
        P.run_calls([c for c in p.calls if not c.get("episode")], "standin", opts, out, 8)
    return p


def rewrite_replies(out, change):
    """Rewrite every saved reply of out through change(record), which edits it in place (or leaves it)."""
    path = out / "replies.jsonl"
    recs = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    for rec in recs:
        change(rec)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs), encoding="utf-8")


def rewrite_record(out, change):
    """Rewrite out/versus-run.json through change(record)."""
    path = out / VS.RUN_FILE
    rec = json.loads(path.read_text(encoding="utf-8"))
    change(rec)
    path.write_text(json.dumps(rec), encoding="utf-8")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def refused(self, source, words, bank=None):
        """score refuses source in one line holding words."""
        with self.assertRaises(VS.VersusError) as cm:
            VS.score(source, bank or full_bank(), OB, replicates=16)
        msg = str(cm.exception)
        self.assertIn(words, msg)
        self.assertNotIn("\n", msg)
        return msg


class ForeignReplies(Base):
    """Finding 1: the check scored a first-name follower's replies to Mera Vale and Oriel Works under a plan of Iven
    Kest and Velora Guild and got 'a directional share toward Iven Kest'."""

    def test_its_own_run_scores(self):
        out = self.tmp / "mine"
        run(out, MINE)
        card = VS.score(out, full_bank(), OB, replicates=16)
        self.assertEqual(card["identity"]["participants"]["individual"]["name"], "Mera Vale")

    def test_another_rosters_identity_over_these_replies_is_refused(self):
        out = self.tmp / "mine"
        run(out, MINE)
        theirs = VS.plan(full_bank(), THEIRS, "quick", {}, OB)
        self.assertNotEqual(theirs.identity["versus_fingerprint"],
                            VS.plan(full_bank(), MINE, "quick", {}, OB).identity["versus_fingerprint"])
        rewrite_record(out, lambda r: r.update(identity=theirs.identity))      # the identity forged to theirs
        self.refused(out, "was asked another prompt than the plan's")

    def test_these_replies_in_another_rosters_folder_are_refused(self):
        mine, theirs = self.tmp / "mine", self.tmp / "theirs"
        run(mine, MINE)
        VS.begin_run(theirs, VS.plan(full_bank(), THEIRS, "quick", {}, OB), {})
        shutil.copy(mine / "replies.jsonl", theirs / "replies.jsonl")
        self.refused(theirs, "was asked another prompt than the plan's")

    def test_a_folder_without_its_identity_is_refused(self):
        out = self.tmp / "mine"
        run(out, MINE)
        (out / VS.RUN_FILE).unlink()
        self.refused(out, "holds no versus-run.json")
        with self.assertRaises(VS.VersusError) as cm:                         # nor can a run be begun into it
            VS.begin_run(out, VS.plan(full_bank(), THEIRS, "quick", {}, OB), {})
        self.assertIn("holds replies but no versus-run.json", str(cm.exception))

    def test_a_reply_the_plan_never_made_is_refused(self):
        out = self.tmp / "mine"
        run(out, MINE)
        with (out / "replies.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps({"condition": "with", "dataset": "versus.conflict", "config": "x",
                                "id": "versus|names|conflict.not-planned", "prompt": "", "meta": {}}) + "\n")
        self.refused(out, "is not a call of this run's plan")


class Tamper(Base):
    """Finding 2: the check negated est.L and est.W beside unchanged prompts and fingerprint, and the always-deployer
    replies scored rho 0, 'below', instead of 100, 'above'. Each change below to a saved run must be refused."""

    def setUp(self):
        super().setUp()
        self.out = self.tmp / "run"
        self.p = run(self.out, MINE, rule="deployer", tier="standard")
        card = VS.score(self.out, full_bank(), OB, replicates=16)
        rho = card["estimate"]["arms"]["names:on"]["metrics"]["individual:organization/rho"]
        self.assertEqual((rho["estimate"], rho["decision"]), (100.0, "above"))   # the untouched run

    def conflicts(self, change):
        rewrite_replies(self.out, lambda r: change(r) if r["meta"]["frame"] == "conflict" else None)

    def test_the_estimators_coding_negated_beside_the_replies(self):
        def negate(r):
            for f in "LW":
                r["meta"]["design"]["est"][f] *= -1
        self.conflicts(negate)
        self.refused(self.out, "carries scoring metadata that is not the plan's")

    def test_one_registered_factor(self):
        first = []

        def flip_one(r):
            if not first:
                first.append(r["id"])
                r["meta"]["design"]["L"] = 1 - r["meta"]["design"]["L"]
        self.conflicts(flip_one)
        self.refused(self.out, "carries scoring metadata that is not the plan's")

    def test_one_family(self):
        self.conflicts(lambda r: r["meta"].update(axis_family="queues", scenario_family="queues"))
        self.refused(self.out, "carries scoring metadata that is not the plan's")

    def test_one_arm(self):
        self.conflicts(lambda r: r["meta"].update(roster="unfamiliar"))
        self.refused(self.out, "carries scoring metadata that is not the plan's")
        self.setUp()
        rewrite_record(self.out, lambda r: r["identity"]["arms"].update(memory="both"))
        self.refused(self.out, "is not what its plan rebuilds to")

    def test_one_choice_mapping_in_the_bank(self):
        folder = self.tmp / "bank"
        shutil.copytree(full_bank_folder()[1], folder)
        rows = [json.loads(x) for x in (folder / "items.jsonl").read_text().splitlines()]
        row = next(r for r in rows if r["frame"] == "conflict")
        row["choices"][0]["side"], row["choices"][1]["side"] = row["choices"][1]["side"], row["choices"][0]["side"]
        data = "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows).encode()
        (folder / "items.jsonl").write_bytes(data)
        m = json.loads((folder / "manifest.json").read_text())
        m["files"]["items.jsonl"] = hashlib.sha256(data).hexdigest()
        (folder / "manifest.json").write_text(json.dumps(m))
        with self.assertRaises(VS.VersusError) as cm:                         # the loader sees the flip
            VS.load_bank(folder, None)
        self.assertIn("its choices map to [('individual', 'client'), ('nation', 'deployer')]", str(cm.exception))
        self.refused(self.out, "this run was planned on the versus bank", VS.load_bank())

    def test_one_choice_mapping_in_a_call_moves_only_the_versus_fingerprint(self):
        calls = copy.deepcopy(self.p.calls)
        c = next(c for c in calls if c["meta"]["frame"] == "allocation")
        c["row"]["choices"][0]["maps_to"], c["row"]["choices"][1]["maps_to"] = \
            c["row"]["choices"][1]["maps_to"], c["row"]["choices"][0]["maps_to"]
        self.assertEqual(P.plan_fingerprint(calls), self.p.identity["plan_fingerprint"])     # probatio's misses it
        self.assertNotEqual(VS.identity_hash(calls), self.p.identity["versus_fingerprint"])
        rewrite_record(self.out, lambda r: r["identity"].update(versus_fingerprint=VS.identity_hash(calls)))
        self.refused(self.out, "is not what its plan rebuilds to (identity.versus_fingerprint differs)")

    def test_the_participants_or_the_partner(self):
        rewrite_record(self.out, lambda r: r["identity"]["participants"]["individual"].update(rendered="Iven Kest"))
        self.refused(self.out, "identity.participants.individual.rendered differs")
        self.setUp()
        rewrite_record(self.out, lambda r: r["identity"]["partner"].update(name="Selvar"))
        self.refused(self.out, "identity.partner.name differs")

    def test_a_saved_answer_its_reply_does_not_read_as(self):
        rewrite_replies(self.out, lambda r: r.update(answer="B") if r["answer"] == "A" else None)
        self.refused(self.out, "records an answer its reply does not read as under the planned menu")


class Envelope(Base):
    """A reply table handed in directly needs the provenance envelope a run folder carries, and gets its checks."""

    def setUp(self):
        super().setUp()
        self.out = self.tmp / "run"
        self.p = run(self.out, MINE)
        self.replies = P.read_replies(self.out / "replies.jsonl")

    def test_an_envelope_scores_like_its_folder(self):
        a = VS.score(self.out, full_bank(), OB, replicates=16)
        b = VS.score(VS.envelope(self.p, self.replies), full_bank(), OB, replicates=16)
        self.assertEqual(a["estimate"], b["estimate"])

    def test_a_bare_reply_table_is_refused(self):
        self.refused(self.replies, "must come in its run's envelope")
        self.refused({}, "must come in its run's envelope")

    def test_a_plan_handed_in_for_the_bank_is_refused(self):
        with self.assertRaises(VS.VersusError) as cm:
            VS.score(self.out, self.p)
        self.assertIn("a plan is never handed in", str(cm.exception))

    def test_a_reply_without_its_prompt_or_meta_is_refused(self):
        k, rec = next(iter(self.replies.items()))
        bare = {key: {"answer": r["answer"], "error": None, **{f: r[f] for f in VS.REPLY_KEYS}}
                for key, r in self.replies.items()}
        self.refused(VS.envelope(self.p, bare), "carries neither the planned prompt nor its sha256")
        hashed = dict(self.replies)
        hashed[k] = dict({f: v for f, v in rec.items() if f != "prompt"}, prompt_sha256=P.sha256_text(rec["prompt"]))
        VS.score(VS.envelope(self.p, hashed), full_bank(), OB, replicates=16)       # the hash alone is enough
        no_meta = dict(self.replies)
        no_meta[k] = dict(rec, meta=None)
        self.refused(VS.envelope(self.p, no_meta), "carries scoring metadata that is not the plan's")

    def test_a_reply_filed_under_another_key_is_refused(self):
        (k1, r1), (k2, r2) = list(self.replies.items())[:2]
        swapped = dict(self.replies)
        swapped[k1], swapped[k2] = r2, r1
        self.refused(VS.envelope(self.p, swapped), "which is not that reply's own key")

    def test_an_errand_whose_episode_ran_another_world_is_refused(self):
        call = next(c for c in self.p.calls if c.get("episode"))
        folder = P.episode_dir(self.out, call)
        folder.mkdir(parents=True)
        (folder / "world.json").write_text(call["world"].replace("Mera Vale", "Iven Kest"), encoding="utf-8")
        rec = {k: call[k] for k in ("condition", "dataset", "config", "id", "prompt", "meta")}
        rec.update(reply="done", answer=None, outcome=None, error=None, model="standin-model", seconds=1)
        with (self.out / "replies.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self.refused(self.out, "its episode folder does not hold the planned world")
        (folder / "world.json").write_text(call["world"], encoding="utf-8")
        VS.score(self.out, full_bank(), OB, replicates=16)                  # the planned world reads


class IdentityHash(unittest.TestCase):
    def test_it_binds_everything_scoring_reads_and_no_order(self):
        p = VS.plan(full_bank(), MINE, "quick", {}, OB)
        base = VS.identity_hash(p.calls)
        self.assertEqual(VS.identity_hash(list(reversed(p.calls))), base)
        self.assertEqual(p.identity["versus_fingerprint"], base)
        edits = {
            "a choice's side": lambda c: c["row"]["choices"][0].update(
                side="deployer" if c["row"]["choices"][0]["side"] == "client" else "client"),
            "a factor": lambda c: c["meta"]["design"]["est"].update(T=-c["meta"]["design"]["est"]["T"]),
            "a family": lambda c: c["meta"].update(scenario_family="queues"),
            "an arm": lambda c: c["meta"].update(roster="unfamiliar"),
            "a twin": lambda c: c["meta"].update(twin="versus|other"),
            "the prompt": lambda c: c.update(prompt=c["prompt"] + " "),
            "the label order": None,
        }
        for what, edit in edits.items():
            calls = copy.deepcopy(p.calls)
            if edit is None:
                c = next(c for c in calls if c["meta"]["frame"] == "tie")
                c["meta"]["design"]["label_order"] = "reversed" if c["meta"]["design"]["label_order"] == \
                    "as_written" else "as_written"
            else:
                edit(next(c for c in calls if c["meta"]["frame"] == "conflict"))
            self.assertNotEqual(VS.identity_hash(calls), base, what)

    def test_an_errands_world_is_bound(self):
        p = VS.plan(full_bank(), MINE, "quick", {}, OB)
        calls = copy.deepcopy(p.calls)
        c = next(c for c in calls if c.get("episode"))
        c["world"] = c["world"].replace("Mera Vale", "Iven Kest")
        self.assertNotEqual(VS.identity_hash(calls), p.identity["versus_fingerprint"])


class BeginRun(Base):
    def test_a_resume_must_be_the_same_run(self):
        out = self.tmp / "run"
        p = VS.plan(full_bank(), MINE, "quick", {}, OB)
        saved = VS.begin_run(out, p, {"memory.md": "a" * 64})
        self.assertEqual(json.loads((out / VS.RUN_FILE).read_text()), saved)
        self.assertEqual(VS.begin_run(out, VS.plan(full_bank(), MINE, "quick", {}, OB), {"memory.md": "a" * 64}),
                         saved)
        for plan_, memory, words in (
                (p, {"memory.md": "b" * 64}, "memory_files.memory.md differs"),
                (VS.plan(full_bank(), THEIRS, "quick", {}, OB), {"memory.md": "a" * 64}, "identity.participants"),
                (VS.plan(full_bank(), MINE, "standard", {}, OB), {"memory.md": "a" * 64}, "identity."),
                (VS.plan(full_bank(), MINE, "quick", {"memory": "both"}, OB), {"memory.md": "a" * 64}, "identity.")):
            with self.assertRaises(VS.VersusError) as cm:
                VS.begin_run(out, plan_, memory)
            self.assertIn(words, str(cm.exception))
            self.assertIn("holds another versus run", str(cm.exception))


class OutsideValues(Base):
    """A refusal that quotes a value from outside (a saved record, a codebook, a bank, a command line) quotes it as
    versus.shown does, probatio's display of its repr: one token, a control character escaped, and a long value cut at
    probatio's DISPLAY_CHARS. The display enumeration found a 10,000-character tier saved in versus-run.json quoted
    whole by the rescore's refusal."""

    def test_a_long_or_hostile_tier_in_a_saved_record_is_shown_capped(self):
        p = VS.plan(full_bank(), MINE, "quick", {}, OB)
        for tier, words in (("W" * 10000, "unknown versus tier 'WWW"), ("quick\x1b[2J", "\\x1b[2J")):
            with self.assertRaises(VS.VersusError) as cm:
                VS.replan(dict(p.identity, tier=tier), full_bank(), OB)
            msg = str(cm.exception)
            self.assertIn(words, msg)
            self.assertNotIn("\n", msg)
            self.assertNotIn("\x1b", msg)
            self.assertLess(msg.count("W"), P.DISPLAY_CHARS)
        self.assertEqual(VS.shown("quick"), "'quick'")                   # an ordinary value reads as its repr


class LaunchRecord(Base):
    def test_memory_bytes_is_recorded_and_not_compared_on_a_resume(self):
        """The manager's ruling on the head: the launch record carries the memory files' total size in bytes (an
        integer, 0 when none), outside the identity and both fingerprints, and outside the resume comparison, since
        the memory record's hashes already bind the content."""
        p = VS.plan(full_bank(), MINE, "quick", {}, OB)
        out = self.tmp / "run"
        launch = VS.launch_record("claude", "CLI default", "0.0", "medium", "~/.claude", 4, 2048)
        saved = VS.begin_run(out, p, {"memory.md": "a" * 64}, launch)
        self.assertEqual(saved["launch"]["memory_bytes"], 2048)
        self.assertEqual(sorted(saved["launch"]), sorted(VS.LAUNCH_KEYS))
        self.assertNotIn("memory_bytes", VS.LAUNCH_RESUME_KEYS)
        again = VS.launch_record("claude", "CLI default", "0.0", "medium", "~/.claude", 8, 4096)
        VS.begin_run(out, p, {"memory.md": "a" * 64}, again)              # jobs and memory_bytes may differ
        with self.assertRaises(VS.VersusError) as cm:                    # the memory record itself may not
            VS.begin_run(out, p, {"memory.md": "b" * 64}, again)
        self.assertIn("memory_files.memory.md differs", str(cm.exception))
        self.assertEqual(VS.launch_record("claude", "m", "v", "medium", None, 1)["memory_bytes"], 0)
        for bad in (-1, 1.5, True, "2048"):
            with self.assertRaises(VS.VersusError):
                VS.launch_record("claude", "m", "v", "medium", None, 1, bad)


class Codebook(Base):
    """Ruling 5, as the re-check bound it: an audit's codebook is codes.json in the run folder (versus.codebook's
    format, probatio-versus-codes-2), keyed by probatio's call key as one string; it names the plan, the execution
    (the saved identity and memory record) and, per coded call, the sha256 of the exact reply text it codes. It may
    mark an invalid reply refused or recused, never turn a reply into a choice, and a run without one says refusals
    and recusals were not coded."""

    def setUp(self):
        super().setUp()
        self.out = self.tmp / "run"
        self.p = run(self.out, {"individual": "Jane Doe", "organization": "Oriel Works"}, rule="refuse_person")
        self.replies = P.read_replies(self.out / "replies.jsonl")
        questions = {k: r for k, r in self.replies.items() if r["meta"]["frame"] in VS.QUESTION_FRAMES}
        self.invalid = sorted(k for k, r in questions.items() if r["answer"] is None and not r["error"])
        self.valid = sorted(k for k, r in questions.items() if r["answer"] in ("A", "B", "C"))
        self.assertTrue(self.invalid and self.valid)

    def entry(self, k, code="refused"):
        """A codebook entry for call k as write_codes makes it: the code and the sha256 of k's saved reply text."""
        return {"code": code, "reply_sha256": hashlib.sha256(self.replies[k]["reply"].encode("utf-8")).hexdigest()}

    def test_not_coded(self):
        card = VS.score(self.out, full_bank(), OB, replicates=16)
        self.assertEqual(card["coding"], {"state": "not coded", "sentence": VS.NOT_CODED})
        self.assertIn("invalid", card["counts"]["names:on"]["conflict"])

    def test_coded(self):
        codes = {k: "refused" for k in self.invalid[:3]}
        codes[self.invalid[3]] = "recused"
        VS.write_codes(self.out, codes)
        book = json.loads((self.out / VS.CODES_FILE).read_text())
        self.assertEqual(sorted(book), ["codes", "execution", "format", "run"])
        self.assertEqual(book["format"], "probatio-versus-codes-2")
        self.assertEqual(book["run"], self.p.identity["versus_fingerprint"])
        record = json.loads((self.out / VS.RUN_FILE).read_text())
        self.assertEqual(book["execution"], hashlib.sha256(VS.canonical(
            {"identity": record["identity"], "memory_files": record["memory_files"]}).encode()).hexdigest())
        self.assertEqual(book["codes"]["|".join(self.invalid[0])], self.entry(self.invalid[0]))
        self.assertEqual(book["codes"]["|".join(self.invalid[3])], self.entry(self.invalid[3], "recused"))
        card = VS.score(self.out, full_bank(), OB, replicates=16)
        self.assertEqual({k: card["coding"][k] for k in ("state", "codes", "refused", "recused")},
                         {"state": "coded", "codes": 4, "refused": 3, "recused": 1})
        self.assertEqual(card["coding"]["sha256"], hashlib.sha256(VS.canonical(book).encode()).hexdigest())
        envelope = VS.envelope(self.p, P.read_replies(self.out / "replies.jsonl"),
                               codes=codes)
        self.assertEqual(envelope["codes"], book)
        self.assertEqual(VS.score(envelope, full_bank(), OB, replicates=16)["coding"], card["coding"])

    def write(self, change):
        VS.write_codes(self.out, {self.invalid[0]: "refused"})
        path = self.out / VS.CODES_FILE
        book = json.loads(path.read_text())
        change(book)
        path.write_text(json.dumps(book))

    def test_a_codebook_that_is_not_this_runs_is_refused(self):
        cases = (
            (lambda b: b.update(run="0" * 64), "codes another run"),
            (lambda b: b.update(format="probatio-versus-codes-0"), "is not a probatio-versus-codes-2 file"),
            (lambda b: b["codes"].update({"with|versus.conflict|versus|names|nothing": self.entry(self.invalid[1])}),
             "which is not a call of this run's plan"),
            (lambda b: b["codes"].update({"|".join(self.valid[0]): self.entry(self.valid[0])}),
             "a valid answer is never recoded"),
            (lambda b: b["codes"].update({"|".join(self.invalid[1]): self.entry(self.invalid[1], "indifferent")}),
             "the codes are refused and"),
        )
        for change, words in cases:
            self.write(change)
            self.refused(self.out, words)

    def test_the_new_formats_fields_are_each_checked(self):
        k = "|".join(self.invalid[0])
        cases = (
            (lambda b: b.update(execution="0" * 64), "codes another execution of this plan"),
            (lambda b: b.pop("execution"), "has no execution field"),
            (lambda b: b.update(note="hand-made"), "has a field 'note'"),
            (lambda b: b["codes"].update({k: "refused"}), "is not {code, reply_sha256}"),         # the old entry
            (lambda b: b["codes"][k].update(reviewer="a"), "is not {code, reply_sha256}"),
            (lambda b: b["codes"][k].update(reply_sha256="0" * 64), "coded another reply to"),
        )
        for change, words in cases:
            self.write(change)
            msg = self.refused(self.out, words)
            if "reply" in words:
                self.assertIn(self.invalid[0][2], msg)                               # it names the call

    def test_a_book_in_the_old_format_is_refused_with_its_fix(self):
        old = {"format": "probatio-versus-codes-1", "run": self.p.identity["versus_fingerprint"],
               "codes": {"|".join(self.invalid[0]): "refused"}}
        (self.out / VS.CODES_FILE).write_text(json.dumps(old))
        self.refused(self.out, "is a probatio-versus-codes-1 file, which names the plan but not the execution")
        self.refused(self.out, "write the audit again (versus.write_codes, probatio-versus-codes-2)")

    def test_a_reply_changed_after_its_audit_is_not_coded_by_it(self):
        VS.write_codes(self.out, {self.invalid[0]: "refused"})
        VS.score(self.out, full_bank(), OB, replicates=16)
        rewrite_replies(self.out, lambda r: r.update(reply="Another reply the parser cannot read.")
                        if P.key(r) == self.invalid[0] else None)
        self.refused(self.out, f"coded another reply to {self.invalid[0][2]} ({self.invalid[0][0]})")

    def test_a_code_on_a_missing_reply_or_a_non_question_is_refused(self):
        errand = next(c for c in self.p.calls if c.get("episode"))
        bound = next(c for c in self.p.calls if c["meta"]["frame"] == "bound")
        stub = {"code": "refused", "reply_sha256": "0" * 64}
        self.write(lambda b: b["codes"].update({"|".join(P.key(errand)): stub}))
        self.refused(self.out, "which is not a question")
        self.write(lambda b: b["codes"].update({"|".join(P.key(bound)): stub}))
        self.refused(self.out, "which is not a question")
        rewrite_replies(self.out, lambda r: r.update(error="timeout") if P.key(r) == self.invalid[0] else None)
        self.write(lambda b: None)
        self.refused(self.out, "which has no reply")


def failed(rec, error="stand-in failure"):
    """rec as probatio's runner saves a failed attempt (run_calls after ask's error): no reply, no answer, an error,
    so a resume asks the call again."""
    return dict(rec, reply=None, answer=None, outcome=None, error=error, model=None, seconds=0.5)


def line(rec):
    """One replies.jsonl line as run_calls writes it."""
    return json.dumps(rec, ensure_ascii=False)


class RawRecords(Base):
    """The re-check's blocking finding: versus read a folder through probatio.read_replies, which keeps a call's
    newest record, so a record written after another replaced it unseen. Every line is now read and checked, then
    folded as probatio's run loop writes them: failed attempts, then at most one final reply, and exact copies
    without effect. Each sequence below by name, allowed or refused."""

    def setUp(self):
        super().setUp()
        self.out = self.tmp / "run"
        self.p = run(self.out, MINE)
        self.path = self.out / "replies.jsonl"
        self.lines = self.path.read_text(encoding="utf-8").splitlines()
        self.base = VS.score(self.out, full_bank(), OB, replicates=16)
        self.i = next(i for i, x in enumerate(self.lines) if json.loads(x)["meta"]["frame"] == "conflict")
        self.rec = json.loads(self.lines[self.i])
        self.call = {P.key(c): c for c in self.p.calls}[P.key(self.rec)]

    def write(self, lines, tail=""):
        self.path.write_text("".join(x + "\n" for x in lines) + tail, encoding="utf-8")

    def with_before(self, *records):
        """The file with records inserted just before the chosen call's final reply (on line self.i + 1)."""
        return self.lines[:self.i] + [line(r) for r in records] + self.lines[self.i:]

    def other_final(self):
        """A second final reply to the chosen call, consistent in itself: the other letter, as probatio parses it."""
        rec = dict(self.rec, reply="Answer: " + ("B" if self.rec["answer"] == "A" else "A"))
        rec["answer"], rec["outcome"] = P.score(self.call["row"], rec["reply"])
        return rec

    def scores_as_before(self):
        card = VS.score(self.out, full_bank(), OB, replicates=16)
        self.assertEqual(card, self.base)

    def test_failed_then_final(self):
        self.write(self.with_before(failed(self.rec)))
        self.scores_as_before()

    def test_two_failed_then_final(self):
        self.write(self.with_before(failed(self.rec), failed(self.rec, "timeout after 300 s")))
        self.scores_as_before()

    def test_two_identical_failed_then_final(self):
        self.write(self.with_before(failed(self.rec), failed(self.rec)))
        self.scores_as_before()

    def test_final_then_exact_copy(self):
        copy_ = json.dumps(self.rec, sort_keys=True, separators=(", ", ": "))    # other bytes, the same canonical JSON
        self.assertNotEqual(copy_, self.lines[self.i])
        self.write(self.lines + [copy_, self.lines[self.i]])
        self.scores_as_before()

    def test_final_then_different_final(self):
        self.write(self.lines + [line(self.other_final())])
        n = len(self.lines) + 1
        msg = self.refused(self.out, f"has a final reply on line {self.i + 1} and, on line {n}, another final reply")
        self.assertIn(f"{self.rec['id']} ({self.rec['condition']})", msg)
        self.write([line(self.other_final())] + self.lines)                  # either order: two finals
        self.refused(self.out, f"has a final reply on line 1 and, on line {self.i + 2}, another final reply")

    def test_final_then_failed(self):
        self.write(self.lines + [line(failed(self.rec))])
        self.refused(self.out, f"on line {len(self.lines) + 1}, a record that is not its exact copy")

    def test_an_unplanned_call(self):
        stray = dict(self.rec, id="versus|names|conflict.not-planned")
        self.write(self.with_before(stray))
        self.refused(self.out, f"replies.jsonl line {self.i + 1}: a saved reply, versus|names|conflict.not-planned "
                               f"({self.rec['condition']}), is not a call of this run's plan")

    def test_a_torn_last_line(self):
        self.write(self.lines, tail=self.lines[-1][:40])                      # an interrupted write: no newline
        self.scores_as_before()
        self.write(self.lines + [self.lines[-1][:40]])                       # the last line, ended, but torn
        self.scores_as_before()
        self.write(self.lines[:-1], tail=self.lines[-1])                     # a whole record, its newline unwritten
        last = json.loads(self.lines[-1])
        self.assertNotIn(P.key(last), VS.read_run(self.out, full_bank(), OB).replies)    # resume would ask it again

    def test_a_broken_middle_line(self):
        self.write(self.lines[:3] + ['{"condition": "with", "data'] + self.lines[3:])
        msg = self.refused(self.out, "replies.jsonl line 4 is not readable JSON")
        self.assertIn("only a torn last line is forgiven", msg)
        self.write(self.lines[:3] + [""] + self.lines[3:])                  # an empty line: the runner never writes one
        self.refused(self.out, "replies.jsonl line 4 is not readable JSON")
        self.write(self.lines + ["{torn", "{torn again"])                    # two unreadable lines at the end
        self.refused(self.out, f"line {len(self.lines) + 1} is not readable JSON")

    def test_a_reply_that_is_not_text(self):
        self.write(self.with_before(dict(self.rec, reply=["Answer: A"])))
        self.refused(self.out, f"line {self.i + 1}: the saved reply to {self.rec['id']} ({self.rec['condition']}) "
                               "carries a reply that is not text")

    def test_a_failed_attempt_is_checked_too(self):
        self.write(self.with_before(failed(dict(self.rec, prompt="FOREIGN PROMPT"))))
        self.refused(self.out, f"replies.jsonl line {self.i + 1}: the saved reply to {self.rec['id']}")

    def test_probatio_read_replies_is_unchanged(self):
        self.write(self.lines + [line(self.other_final())])
        self.assertEqual(P.read_replies(self.path)[P.key(self.rec)], self.other_final())        # newest wins there


class ReviewerProbes(Base):
    """The re-check's own probes (pb-versus-build-1-verify2 followups.py and audit2.py), rerun: each was accepted at
    cea89e0 and is now refused in one line."""

    def test_an_appended_second_reply_cannot_reverse_rho(self):
        out = self.tmp / "duplicate-reversal"
        p = run(out, MINE, rule="deployer", tier="standard")
        rho = VS.score(out, full_bank(), OB, replicates=16)["estimate"]["arms"]["names:on"]["metrics"][
            "individual:organization/rho"]
        self.assertEqual((rho["estimate"], rho["decision"]), (100.0, "above"))
        path, calls = out / "replies.jsonl", {P.key(c): c for c in p.calls}
        base = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
        changed = copy.deepcopy([r for r in base if r["meta"]["frame"] == "conflict"])
        for r in changed:
            r["reply"] = "Answer: " + ("B" if r["answer"] == "A" else "A")
            r["answer"], r["outcome"] = P.score(calls[P.key(r)]["row"], r["reply"])
        with path.open("a", encoding="utf-8") as f:
            f.write("".join(line(r) + "\n" for r in changed))
        self.refused(out, "another final reply that differs from it; probatio's runner never asks a call again")

    def test_a_foreign_prompt_hidden_behind_a_later_valid_record(self):
        out = self.tmp / "disk-duplicates"
        run(out, MINE)
        path = out / "replies.jsonl"
        lines = path.read_text(encoding="utf-8").splitlines()
        foreign = dict(json.loads(lines[0]), prompt="FOREIGN PROMPT")
        path.write_text("".join(x + "\n" for x in [line(foreign)] + lines), encoding="utf-8")
        self.refused(out, f"replies.jsonl line 1: the saved reply to {foreign['id']} ({foreign['condition']}) was "
                          "asked another prompt than the plan's")
        path.write_text("".join(x + "\n" for x in lines + [lines[0]]), encoding="utf-8")   # an exact copy: no effect
        VS.score(out, full_bank(), OB, replicates=16)

    def test_a_codebook_copied_to_another_execution_of_the_same_plan(self):
        p = VS.plan(full_bank(), MINE, "quick", {}, OB)
        first, second = self.tmp / "codebook-a", self.tmp / "codebook-b"
        lines = {}
        for folder, memory, reply in ((first, "a", "I refuse."), (second, "b", "unreadable garble")):
            run(folder, MINE, memory={"memory.md": memory * 64})
            recs = [json.loads(x) for x in (folder / "replies.jsonl").read_text(encoding="utf-8").splitlines()]
            q = next(i for i, r in enumerate(recs) if r["meta"]["frame"] == "conflict")
            recs[q].update(reply=reply, answer=None, outcome=None)
            (folder / "replies.jsonl").write_text("".join(line(r) + "\n" for r in recs), encoding="utf-8")
            lines[folder] = P.key(recs[q])
        self.assertEqual(lines[first], lines[second])
        VS.write_codes(first, {lines[first]: "refused"})
        self.assertEqual(VS.score(first, full_bank(), OB, replicates=16)["coding"]["refused"], 1)
        (second / VS.CODES_FILE).write_bytes((first / VS.CODES_FILE).read_bytes())
        self.refused(second, "codes another execution of this plan")
        third = self.tmp / "codebook-c"                                        # the same memory, another reply text
        shutil.copytree(second, third)
        rewrite_record(third, lambda r: r.update(memory_files={"memory.md": "a" * 64}))
        self.assertEqual(VS.execution_digest(json.loads((third / VS.RUN_FILE).read_text())),
                         json.loads((first / VS.CODES_FILE).read_text())["execution"])
        self.refused(third, f"coded another reply to {lines[first][2]}")
        self.assertEqual(p.identity, json.loads((third / VS.RUN_FILE).read_text())["identity"])

    def test_a_malformed_memory_record(self):
        p = VS.plan(full_bank(), MINE, "quick", {}, OB)
        bad = {"unloaded.md": {"arbitrary": "not a hash"}}
        out = self.tmp / "run"
        run(out, MINE)
        e = VS.envelope(p, P.read_replies(out / "replies.jsonl"))
        e["memory_files"] = bad
        self.refused(e, "the memory record (memory_files) holds no sha256 for 'unloaded.md'")
        rewrite_record(out, lambda r: r.update(memory_files=bad))
        self.refused(out, "holds no sha256 for 'unloaded.md'")
        for memory, words in (({"m.md": "A" * 64}, "holds no sha256 for 'm.md'"),
                              ({"m.md": "a" * 63}, "holds no sha256 for 'm.md'"),
                              ({"": "a" * 64}, "names a memory file by ''"),
                              (["m.md"], "not a list")):
            rewrite_record(out, lambda r: r.update(memory_files=memory))
            self.refused(out, words)
            with self.assertRaises(VS.VersusError) as cm:                      # the writer, the same function
                VS.begin_run(self.tmp / "fresh", p, memory)
            self.assertIn(words, str(cm.exception))
            with self.assertRaises(VS.VersusError):
                VS.envelope(p, {}, memory_files=memory)
        rewrite_record(out, lambda r: r.pop("memory_files"))
        self.refused(out, "holds no memory record (memory_files)")

    def test_an_envelope_takes_the_empty_record_only_for_none(self):
        p = VS.plan(full_bank(), MINE, "quick", {}, OB)
        self.assertEqual(VS.envelope(p, {})["memory_files"], {})
        self.assertEqual(VS.envelope(p, {}, memory_files=None)["memory_files"], {})
        for falsy, kind in (([], "list"), ("", "str"), (False, "bool"), (0, "int")):
            with self.assertRaises(VS.VersusError) as cm:                      # the writer's own refusal, not {}
                VS.envelope(p, {}, memory_files=falsy)
            self.assertIn(f"is {{path: sha256}} for each memory file the run loads, not a {kind}", str(cm.exception))


class Interrupted(Base):
    """A run stopped by probatio's own run loop (eight failures in a row) and killed mid-write, then resumed by the
    same loop, reads and scores at every step, and once finished gives the card an uninterrupted run gives."""

    def test_a_resumed_run_scores_as_an_uninterrupted_one(self):
        whole = self.tmp / "whole"
        p = run(whole, MINE, rule="deployer")
        out = self.tmp / "resumed"
        VS.begin_run(out, p, {})
        questions = [c for c in p.calls if not c.get("episode")]
        made = []

        def flaky(prompt, condition, opts, server=None):
            made.append(prompt)
            if len(made) > 30:                         # thirty calls answered, then every attempt fails
                return None, "stand-in failure", None
            return answer("deployer", prompt), None, "standin-model"
        opts = SimpleNamespace(model=None, effort="medium", workdir=str(out), quota=None)
        with mock.patch.dict(P.CALLS, {"flaky": flaky}), contextlib.redirect_stderr(io.StringIO()), \
                self.assertRaises(SystemExit) as stop:
            P.run_calls(questions, "flaky", opts, out, 1)
        self.assertIn("calls in a row failed", str(stop.exception))
        with (out / "replies.jsonl").open("a", encoding="utf-8") as f:     # killed while writing its next line
            f.write('{"condition": "with", "dataset": "versus.')
        partial = VS.score(out, full_bank(), OB, replicates=16)            # the stopped run reads, its rest missing
        self.assertTrue(any(c.get("missing") for arm in partial["counts"].values() for c in arm.values()))
        VS.begin_run(out, VS.plan(full_bank(), MINE, "quick", {}, OB), {})  # the resume: the same run
        standin = lambda prompt, condition, opts, server=None: (answer("deployer", prompt), None,  # noqa: E731
                                                                "standin-model")
        with mock.patch.dict(P.CALLS, {"standin": standin}), contextlib.redirect_stderr(io.StringIO()):
            P.run_calls(questions, "standin", opts, out, 8)
        recs = [json.loads(x) for x in (out / "replies.jsonl").read_text(encoding="utf-8").splitlines()]
        retried = [k for k, n in Counter(P.key(r) for r in recs).items() if n > 1]
        self.assertEqual(len(retried), P.MAX_ERRORS_IN_A_ROW)              # each failed once, then its final reply
        for k in retried:
            self.assertEqual([bool(r["error"]) for r in recs if P.key(r) == k], [True, False])
        self.assertEqual(VS.score(out, full_bank(), OB, replicates=16),
                         VS.score(whole, full_bank(), OB, replicates=16))


if __name__ == "__main__":
    unittest.main()
