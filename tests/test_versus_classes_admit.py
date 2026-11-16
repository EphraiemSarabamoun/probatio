"""tools/versus_classes_admit.py on planted inputs: a packet whose sheets are made from the shipped class source (each
sheet's head the template's rendering hash, its body the six unfamiliar-roster renderings) and reading folders written
here. The admission proves each hash three ways (today's source, the sheet's head, the sheet's own renderings), admits
a template only on a passing reading of today's rendering, names the reader by vendor alone, keeps an earlier failed
reading as history with what changed and under which ruling, writes nothing when one template is not admitted, and
changes nothing when run again. No network, no model.

    python3 -m unittest tests.test_versus_classes_admit
"""
import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from itertools import permutations
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools"))
import build_versus_classes as BC  # noqa: E402
import versus as VS  # noqa: E402
import versus_classes as VC  # noqa: E402
import versus_classes_admit as A  # noqa: E402


def write_sheet(folder, t, fixed, common, renderings=None):
    """A sheet as a reading packet prints it: a head line with the rendering hash, then the six complete renderings
    on the unfamiliar roster (system message, user message) and one on the long roster."""
    texts = renderings or BC.renderings(t, fixed, common)
    lines = ["CHANGED under K8 only", f"{t['id']} | {t['component']}",
             f"rendering hash (unfamiliar roster, the six assignments in order): {BC.render_hash(t, fixed, common)}",
             "", "---- the template, slots visible", t["body"]]
    for n, (types, text) in enumerate(zip(permutations(VS.TYPES, 2), texts), 1):
        lines += ["", f"---- complete rendering {n} of 6: unfamiliar roster, A={types[0]} B={types[1]}",
                  "--- system message (recorded in the bank; not sent in an agent run)", "You are a helpful assistant.",
                  "--- user message", text]
    lines += ["", "---- complete rendering on the long roster: A=nation B=organization", "--- user message", "(long)"]
    path = folder / "sheets" / f"{t['id']}.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def reading(t, h, overall="pass", failed=(), reviewer="Codex reader 7 (a free-text label)"):
    """One reading entry of a template: every point passed but those in failed, the hash read recorded."""
    points = {str(i): {"verdict": "fail" if str(i) in failed else "pass", "why": f"point {i} read"}
              for i in range(1, 7)}
    return {"id": t["id"], "set": t["id"].rsplit("-", 1)[0], "points": points, "overall": overall, "fix": None,
            "quotes": ["a quoted line"], "type_argument": "a type argument", "render_sha256": h, "reviewer": reviewer}


class Admit(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.src = self.tmp / "src"
        shutil.copytree(VC.CLASS_SRC, self.src)
        shutil.rmtree(self.src / "reviews")
        self.templates, self.fixed, self.common, _ = BC.read_source(self.src)
        self.packet = self.tmp / "packet"
        self.hash = {}
        for t in self.templates:
            write_sheet(self.packet, t, self.fixed, self.common)
            self.hash[t["id"]] = BC.render_hash(t, self.fixed, self.common)
        self.first, self.second = self.tmp / "read-1", self.tmp / "read-2"
        old = [reading(t, "1" * 64, overall="fail", failed=("2",)) for t in self.templates[:3]]
        self.write(self.first, old)
        self.write(self.second, [reading(t, self.hash[t["id"]]) for t in self.templates])
        self.changes = self.tmp / "changes.json"
        self.changes.write_text(json.dumps([{"id": self.templates[0]["id"], "ruling": "K7", "before": "b",
                                             "after": "a"}]))

    @staticmethod
    def write(folder, entries):
        folder.mkdir(exist_ok=True)
        (folder / "reading.json").write_text(json.dumps(entries), encoding="utf-8")

    def run_tool(self, *extra):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            A.main(["--readings", str(self.first), str(self.second), "--packets", str(self.packet),
                    "--changes", str(self.changes), "--src", str(self.src), *extra])
        return out.getvalue()

    def refused(self, words):
        with self.assertRaises(SystemExit) as cm:
            self.run_tool()
        self.assertIn(words, str(cm.exception))
        self.assertFalse((self.src / "reviews").exists())          # nothing written

    def test_every_template_admitted_and_recorded_once(self):
        self.assertIn("36 templates admitted, 36 records written", self.run_tool())
        self.assertIn("36 records, none would change", self.run_tool("--check"))
        files = BC.build(self.src, development=False)               # the registered gate takes the records
        self.assertTrue(json.loads(files["manifest.json"])["registered"])

    def test_a_record_names_the_vendor_and_keeps_the_earlier_failure(self):
        self.run_tool()
        t = self.templates[0]
        rec = json.loads((self.src / "reviews" / f"{t['id']}.json").read_text(encoding="utf-8"))
        self.assertEqual(rec["second_reader"]["reader"], "an independent reader, vendor openai")
        self.assertNotIn("free-text label", json.dumps(rec))
        self.assertEqual(rec["render_sha256"], self.hash[t["id"]])
        self.assertEqual(rec["earlier_second_readings"], [{
            "overall": "fail", "render_sha256_read": None, "failed_points": {"2": "point 2 read"}, "fix": None,
            "changed": {"ruling": "K7", "before": "b", "after": "a"}}])
        later = json.loads((self.src / "reviews" / f"{self.templates[5]['id']}.json").read_text(encoding="utf-8"))
        self.assertEqual(later["earlier_second_readings"], [])

    def test_a_reading_of_another_rendering_admits_nothing(self):
        t = self.templates[4]
        entries = [reading(x, self.hash[x["id"]] if x is not t else "2" * 64) for x in self.templates]
        self.write(self.second, entries)
        self.refused(f"{t['id']}: no reading of today's rendering")

    def test_a_sheet_whose_renderings_are_not_its_head_admits_nothing(self):
        t = self.templates[7]
        texts = BC.renderings(t, self.fixed, self.common)
        write_sheet(self.packet, t, self.fixed, self.common, renderings=[texts[0] + " ", *texts[1:]])
        self.refused(f"{t['id']}: the hash proof fails")

    def test_a_failed_point_on_todays_rendering_admits_nothing(self):
        t = self.templates[9]
        entries = [reading(x, self.hash[x["id"]], failed=("4",) if x is t else ()) for x in self.templates]
        self.write(self.second, entries)
        self.refused(f"{t['id']}: the reading of today's rendering does not pass")

    def test_a_reader_of_the_first_readers_vendor_admits_nothing(self):
        t = self.templates[2]
        entries = [reading(x, self.hash[x["id"]], reviewer="Claude, a reader" if x is t else "Codex")
                   for x in self.templates]
        self.write(self.second, entries)
        with self.assertRaises(SystemExit) as cm:
            with contextlib.redirect_stdout(io.StringIO()):
                A.main(["--readings", str(self.first), str(self.second), "--packets", str(self.packet), "--src",
                        str(self.src), "--vendor", "anthropic"])
        self.assertIn(f"{t['id']}: the deciding reader is of the first reader's vendor", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
