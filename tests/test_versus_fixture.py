"""A complete synthetic versus source for the tests: the registered counts (six allocation and six conflict scenarios
in each of the eight families, twelve triads, four ties), made by copying the real scenarios under new ids, with
review records that pass the builder's gate. It is a TEST FIXTURE, never a bank: its scenarios repeat one another's
text, and its review records are invented ("synthetic test fixture: not a review"). It exists so the registered
build, the standard tier and the claim rule can be exercised before the real bank is written. No network, no model.

    python3 -m unittest tests.test_versus_fixture
"""
import atexit
import copy
import functools
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools"))
import build_versus_bank as BV  # noqa: E402
import ordo  # noqa: E402
import versus as VS  # noqa: E402
import versus_render as V  # noqa: E402

SRC = REPO / "bank" / "versus" / "src"
FIXTURE_NOTE = "synthetic test fixture: not a review"


def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def review_record(lst, item, common):
    """A review record that passes the gate (format probatio-versus-review-3): tied to the current rendering, with its
    own setting and object, a loss kind and an empty near list, read by a second reader whose structured vendor is
    not the first's, with the verdict keep."""
    current = V.render_hash(V.render(lst, item, V.ROSTERS["default"], common))
    return {"format": BV.REVIEW_FORMAT, "id": item["id"], "list": lst, "family": item["family"],
            "setting": f"fixture setting of {item['id']}", "object": f"fixture object of {item['id']}",
            "loss_kind": "time", "near": [], "render_sha256": current, "status": FIXTURE_NOTE, "points": {},
            "first_reader": {"reader": "a fixture author", "vendor": "anthropic"},
            "second_reader": {"reader": "a fixture reader", "vendor": "openai", "verdict": "keep",
                              "render_sha256_read": current}}


def fixed_text_record(common):
    """The fixed text's own review record (ruling 4) that passes the gate: tied to the fixed-text version the builder
    computes from the fixed text, the role sentences and the pinned system message, with a second reader of another
    vendor."""
    version = BV.fixed_version(common, BV.system_message(ordo.load_ordo_bank()))
    return {"format": BV.REVIEW_FORMAT, "id": BV.FIXED_TEXT_ID, "list": BV.FIXED_TEXT_ID,
            "fixed_text_version": version, "status": FIXTURE_NOTE,
            "first_reader": {"reader": "a fixture author", "vendor": "anthropic"},
            "second_reader": {"reader": "a fixture reader", "vendor": "openai", "verdict": "pass",
                              "fixed_text_version_read": version}}


def clone(item, sid, family):
    out = copy.deepcopy(item)
    out.update(id=sid, family=family)
    return out


def write_full_source(dst, src=SRC):
    """Write the complete synthetic source into dst and return dst. Allocation scenarios copy each family's own
    allocation item; conflicts copy each family's own conflict, or the data-access conflict where a family has none;
    the twelve triads copy the scheduling triad into the first six families, two each; the ties are the real four."""
    dst = Path(dst)
    source = V.load_source(src)
    common = source["common"]
    families = list(common["families"])
    write_json(dst / "common.json", common)
    donor_conflict = next(i for i in source["conflict"] if i["family"] == "data-access")
    for fam in families:
        alloc = next(i for i in source["allocation"] if i["family"] == fam)
        conf = next((i for i in source["conflict"] if i["family"] == fam), donor_conflict)
        items = {"family": fam,
                 "allocation_items": [clone(alloc, f"{fam}-a{k}", fam) for k in range(1, 7)],
                 "conflict_items": [clone(conf, f"{fam}-c{k}", fam) for k in range(1, 7)]}
        write_json(dst / "families" / f"{fam}.json", items)
    triad = source["triad"][0]
    triads = [clone(triad, f"{fam}-t{k}-triad", fam) for fam in families[:6] for k in (1, 2)]
    write_json(dst / "triads.json", {"triad_items": triads})
    write_json(dst / "ties.json", {"tie_controls": source["tie"]})
    for lst, item in V.scenarios(V.load_source(dst)):
        write_json(dst / "reviews" / f"{item['id']}.json", review_record(lst, item, common))
    write_json(dst / "reviews" / f"{BV.FIXED_TEXT_ID}.json", fixed_text_record(common))
    return dst


_TEMP = []


def _cleanup():
    for d in _TEMP:
        shutil.rmtree(d, True)


atexit.register(_cleanup)


def scratch():
    """A temporary folder removed when the test process exits."""
    d = Path(tempfile.mkdtemp(prefix="versus-fixture-"))
    _TEMP.append(d)
    return d


@functools.lru_cache(maxsize=None)
def full_bank_folder():
    """The complete synthetic source built as a registered bank, once per test process: (source folder, bank folder).
    The source sits beside the bank (bank/src), as bank/versus/src does, because load_bank checks the bank against
    it."""
    out = scratch() / "bank"
    src = write_full_source(out / "src")
    for name, data in BV.build(src, development=False).items():
        (out / name).write_bytes(data)
    return src, out


def full_bank():
    """The complete synthetic bank, loaded and checked by versus.load_bank (unpinned: the pin is the shipped bank's)."""
    return VS.load_bank(full_bank_folder()[1], None)


@functools.lru_cache(maxsize=None)
def development_bank_folder():
    """A development bank, built once per test process from a copy of the shipped source in which one allocation
    scenario fails a mechanical check (its id is not a slug), so the builder leaves it out: 47 allocation scenarios,
    which the estimator's registered grid does not hold. The shipped bank is registered; this is the bank the
    development paths (the loader's flag, the estimator's refusal, the page's bank line) are tested on."""
    out = scratch() / "bank"
    src = out / "src"
    shutil.copytree(SRC, src)
    fam = next(iter(json.loads((src / "common.json").read_text(encoding="utf-8"))["families"]))
    path = src / "families" / f"{fam}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    item = next(i for i in data["allocation_items"] if i["id"] not in set(data.get("spares", [])))
    item["id"] = "Left_Out"
    write_json(path, data)
    for name, built in BV.build(src, development=True).items():
        (out / name).write_bytes(built)
    return out


def development_bank():
    """The development bank of development_bank_folder, loaded as one (unpinned, development=True)."""
    return VS.load_bank(development_bank_folder(), None, development=True)


class FullSource(unittest.TestCase):
    def test_it_builds_as_a_registered_bank_with_the_registered_counts(self):
        bank = full_bank()
        m = bank.manifest
        self.assertTrue(m["registered"])
        self.assertIsNone(m["development"])
        self.assertEqual(m["counts"]["scenarios"], {"allocation": 48, "conflict": 48, "tie": 4, "triad": 12})
        for tier, (n, tri) in (("standard", (48, 12)), ("quick", (16, 12))):
            self.assertEqual([len(m["tiers"][tier][f]) for f in ("allocation", "conflict", "triad", "tie")],
                             [n, n, tri, 4])
        for rec in json.loads((full_bank_folder()[0] / "reviews" / "quotas-a1.json").read_text())["status"],:
            self.assertEqual(rec, FIXTURE_NOTE)


if __name__ == "__main__":
    unittest.main()
