"""The bank builder's adapters on the source slices (tests/fixtures/sources/): every sliced row comes back from its
template, the reference worlds reproduce their published rows, the cells and the organization worlds derive as in a
full build, and a whole bank builds from the slices. No .cache, no network, no model: these run anywhere.

    python3 -m unittest tests.test_bank_adapters
"""
import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(REPO / "tests" / "fixtures"))
import build_bank as B  # noqa: E402
import make_sources as M  # noqa: E402

DATA = M.read()
FILES, SUMMARY = B.build(DATA, slices=True)
PUBLISHED = B.published_class1(DATA)


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def rows(name):
    return [json.loads(line) for line in FILES[name].splitlines()]


def errands():
    doc = json.loads(FILES["cells.json"])
    n0 = rows("errands.jsonl")
    return {(t["type"], t["seed"], t["cell"]): t for t in n0 + [B.derive_cell(r, doc, c) for r in n0 for c in ("U", "O")]}


class TestSlices(unittest.TestCase):
    def test_every_pinned_config_is_sliced_and_small(self):
        manifest = json.loads((M.OUT / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(set(manifest["configs"]), {f"{d}/{c}" for d, (_, cs) in B.SOURCES.items() for c in cs})
        for k, m in manifest["configs"].items():
            self.assertEqual(m["lines"], sorted(set(m["lines"])), k)
            self.assertLess(len(m["lines"]), m["file_rows"], k)
        size = sum(p.stat().st_size for p in M.OUT.rglob("*.jsonl"))
        self.assertLess(size, 2_000_000)

    def test_a_changed_slice_is_refused(self):
        root = Path(tempfile.mkdtemp())
        try:
            shutil.copytree(M.OUT, root / "sources")
            p = root / "sources" / "devotio" / "full.jsonl"
            p.write_bytes(p.read_bytes().replace(b"credits", b"credit", 1))
            with self.assertRaises(SystemExit):
                M.read(root / "sources")
        finally:
            shutil.rmtree(root)


class TestSliceGuards(unittest.TestCase):
    """The W2 cleanup's verification lows (in the review record), without .cache: what read()
    checks, the selection contract, and the full build's whole-pair guard."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        shutil.copytree(M.OUT, self.root / "sources")
        self.folder = self.root / "sources"

    def manifest(self):
        return json.loads((self.folder / "manifest.json").read_text(encoding="utf-8"))

    def write_manifest(self, manifest):
        (self.folder / "manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")

    def refused(self, pinned=M.MANIFEST_SHA256):
        with self.assertRaises(SystemExit) as e:
            M.read(self.folder, pinned)
        return str(e.exception)

    def test_eleven_configs_from_seven_datasets(self):
        self.assertEqual((len(B.SOURCES), sum(len(cs) for _, cs in B.SOURCES.values())), (7, 11))
        self.assertEqual(len(self.manifest()["configs"]), 11)

    def test_the_manifest_is_pinned(self):
        self.assertEqual(hashlib.sha256((M.OUT / "manifest.json").read_bytes()).hexdigest(), M.MANIFEST_SHA256)
        M.read(self.folder)                                                      # the copy reads as the original

    def test_a_dropped_row_resealed_is_refused(self):
        # devotio's first sliced row out, the manifest's lines, line hashes and slice hash rewritten to match: every
        # hash read() can recompute agrees, so only the pin catches it
        p = self.folder / "devotio" / "full.jsonl"
        lines = p.read_bytes().split(b"\n")[:-1]
        p.write_bytes(b"\n".join(lines[1:]) + b"\n")
        m = self.manifest()
        c = m["configs"]["devotio/full"]
        c["lines"], c["line_sha256"] = c["lines"][1:], c["line_sha256"][1:]
        c["slice_sha256"] = hashlib.sha256(p.read_bytes()).hexdigest()
        self.write_manifest(m)
        M.read(self.folder, None)                                                # consistent without the pin
        self.assertIn("make_sources.py pins", self.refused())

    def test_a_moved_line_number_is_refused(self):
        m = self.manifest()
        m["configs"]["devotio/full"]["lines"][0] += 1
        self.write_manifest(m)
        self.assertIn("make_sources.py pins", self.refused())

    def test_the_full_files_pins_are_checked(self):
        for change in ({"file_sha256": "0" * 64}, {"file_rows": 1}):
            with self.subTest(change):
                m = self.manifest()
                m["configs"]["anulus/v0.5"].update(change)
                self.write_manifest(m)
                self.assertIn("is not the one the builder pins", self.refused(None))
                self.assertIn("make_sources.py pins", self.refused())
        m = json.loads((M.OUT / "manifest.json").read_text(encoding="utf-8"))
        m["configs"]["anulus/v0.5"]["lines"][-1] = 10 ** 6                       # beyond the full file
        self.write_manifest(m)
        self.assertIn("not increasing within 1 to 768", self.refused(None))

    def test_every_template_keeps_its_defining_row_and_a_row_naming_each_side(self):
        """make_sources.py's selection contract: for every Class I template, a row it can be read off and a row
        naming the user and the control (for nations, the United States and Arvenia)."""
        for items, sides in ((B.person_items(DATA), M.PERSON_SIDES),
                             ([it for it, _, _ in B.nation_items(DATA)], M.NATION_SIDES)):
            named, defined = {}, set()
            for tid, _, _, _, row, who, derive in items:
                named.setdefault(tid, set()).update(who.values())
                if derive:
                    defined.add(tid)
            self.assertEqual(defined, set(named))
            for tid, got in named.items():
                self.assertTrue(set(sides) <= got, f"{tid}: no sliced row names {set(sides) - got}")

    def test_only_a_slice_build_may_meet_a_task_without_a_patria_pair(self):
        with self.assertRaises(ValueError) as e:
            B.build(DATA)                                     # a full build of the same rows: each task lacks a pair
        self.assertRegex(str(e.exception), r"patria s\d+: the (real|fictional) pair is missing")
        self.assertEqual(SUMMARY["class1"]["nation"], 61)     # built with slices=True at import


class TestClassIAdapters(unittest.TestCase):
    def test_every_sliced_person_row_comes_back(self):
        """Each sliced person row, rendered from its template for its row's names (the anonymous requester for
        devotio's twins), gives its prompt_sha256_public; every person template is covered."""
        covered = set()
        for tid, _, _, _, row, who, _ in B.person_items(DATA):
            t = PUBLISHED[tid + ".individual"]
            got = B.render(t["text"], {r: B.person_record(k) for r, k in who.items()})
            self.assertEqual(sha(got), row["prompt_sha256_public"], row["id"])
            covered.add(tid)
        self.assertEqual(len(covered), 61)

    def test_every_sliced_nation_row_comes_back(self):
        """Each sliced patria class1 row comes back from its template; I.1 as patria's strict prompt once the forced
        tail is swapped back. Every nation template patria's rows give is covered."""
        tail, covered = B.forced_tail(DATA), set()
        for (tid, comp, _, _, row, who, _), _, _ in B.nation_items(DATA):
            text = PUBLISHED[tid + ".nation"]["text"]
            if comp == "I.1":
                text = text[:-len(tail)] + B.STRICT_PAIR_TAIL
            self.assertEqual(B.render(text, {r: B.nation_record(k) for r, k in who.items()}), row["prompt"])
            covered.add(tid)
        self.assertEqual(len(covered), 29)                   # 37 nation templates less I.2's 8 (par-potestas)

    def test_the_slice_build_has_every_class1_template(self):
        self.assertEqual(SUMMARY["class1"], {"individual": 61, "nation": 61, "organization": 61})
        self.assertEqual(len(rows("class1.jsonl")), 183)


class TestErrandAdapters(unittest.TestCase):
    def test_reference_worlds_reproduce_anulus_v05_and_labores(self):
        tpls, extra = errands(), B.cells(B.SRC, DATA)[1]
        for r in DATA["anulus/v0.5"] + DATA["labores/refunds"]:
            if r["arm"] != "neutral":
                continue
            n0 = tpls[("individual", r["seed"], "N0")]
            x = B.mate_record(r)
            rec = {"T": B.you_record(), "X": x, "B": B.you_record() if r["beneficiary"] == "principal" else x}
            self.assertEqual(sha(B.render(n0["prompt"], rec)), r["prompt_sha256_public"], r["id"])
            if r["id"] in {x["id"] for x in DATA["anulus/v0.5"]}:
                self.assertEqual(sha(B.render_world(B.strip_cell(n0, extra), rec)), r["world_sha256"], r["id"])

    def test_reference_worlds_reproduce_patria_class23(self):
        tpls, extra = errands(), B.cells(B.SRC, DATA)[1]
        checked = 0
        for r in DATA["patria/class23"]:
            if r["condition"] != "neutral":
                continue
            n0 = tpls[("nation", r["seed"], "N0")]
            pair = B.NATION_PAIRS[r["pair"]]
            d = r["deploying_nation"]
            o = pair[1] if d == pair[0] else pair[0]
            first, second = pair if r["seed"] % 4 < 2 else pair[::-1]
            rec = {"T": B.NATIONS[d], "X": B.NATIONS[o], "B": B.NATIONS[r["beneficiary_nation"]],
                   "P1": B.NATIONS[first], "P2": B.NATIONS[second]}
            self.assertEqual(B.render(n0["prompt"], rec), r["system_text"] + "\n\n" + r["user"], r["episode_id"])
            w = json.loads(B.render_world(B.strip_cell(n0, extra), rec))
            for k, f in (("tools", "tools"), ("state", "initial_state"), ("rules", "rules"), ("end", "end")):
                self.assertEqual(w[k], json.loads(r[f]), (r["episode_id"], k))
            checked += 1
        self.assertEqual(checked, 12)

    def test_the_cells_and_the_organization_worlds(self):
        tpls, extra = errands(), B.cells(B.SRC, DATA)[1]
        org = B.source(B.SRC, "organization/errands.json")
        kinds = set()
        for ty in B.TYPES:
            for seed in (301, 625, 626):
                trio = {c: tpls[(ty, seed, c)] for c in B.CELLS}
                B.check_cells(trio, dict(trio["N0"], world=B.strip_cell(trio["N0"], extra)), extra)
        for seed in (301, 625, 626):
            n, o = tpls[("nation", seed, "N0")], tpls[("organization", seed, "N0")]
            kind = "studio" if "programme" in n["prompt"].split("\n\n")[0] else "lab"
            kinds.add(kind)
            self.assertEqual(B.with_surface(n, B.surface(org, kind)), (o["world"], o["prompt"]))
        self.assertEqual(kinds, {"lab", "studio"})

    def test_the_overlay_evidence_is_in_the_slice(self):
        ov = B.overlay(DATA)
        self.assertEqual(B.cells(B.SRC, DATA)[1]["tools_added"], [ov["U"]["tool"]])

    def test_arms_and_reports_for_every_type(self):
        self.assertEqual(set(json.loads(FILES["arms.json"])), set(B.TYPES))
        self.assertEqual(set(json.loads(FILES["followup.json"])), set(B.TYPES))


if __name__ == "__main__":
    unittest.main()
