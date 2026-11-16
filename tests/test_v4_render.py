"""Tests for probatio v4's step 1: entity records, the target-name guard, and slot rendering of texts and worlds
(docs/v4/DESIGN.md 5.1, 6.4 and 9). The cases live in tests/fixtures/render_cases.json, which the bank builder's
tests read too, so the two implementations of "what a slot means" can never drift apart.

    python3 -m unittest discover -s tests
"""
import hashlib
import json
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import probatio as P  # noqa: E402

CASES = json.loads((REPO / "tests" / "fixtures" / "render_cases.json").read_text(encoding="utf-8"))


class TestEntity(unittest.TestCase):
    def test_fixture_entities(self):
        for case in CASES["entities"]:
            a = dict(case["args"])
            got = P.entity(a.pop("type"), a.pop("name"), **a)
            self.assertEqual(got, case["expect"], case["args"])

    def test_every_field_is_present(self):
        for t in P.TARGET_TYPES:
            rec = P.entity(t, "Example Name")
            self.assertTrue(set(P.ENTITY_FIELDS) <= set(rec), t)

    def test_unknown_type(self):
        with self.assertRaises(ValueError):
            P.entity("company", "Acme")


class TestCheckName(unittest.TestCase):
    def test_accepted(self):
        for name, normalised in CASES["names_ok"]:
            self.assertEqual(P.check_name(name), normalised, name)

    def test_refused(self):
        for name, reason in CASES["names_refused"]:
            with self.assertRaises(P.NameRefused, msg=name) as caught:
                P.check_name(name)
            self.assertIn(reason, str(caught.exception), name)

    def test_the_label_names_the_flag(self):
        with self.assertRaises(P.NameRefused) as caught:
            P.check_name("", "--name")
        self.assertIn("--name", str(caught.exception))

    def test_a_refused_name_never_reaches_render(self):
        """Every accepted name renders into a slot and comes back as itself, with no slot mark left."""
        for name, normalised in CASES["names_ok"]:
            rec = P.entity("organization", P.check_name(name))
            self.assertEqual(P.render("⟦T.full⟧", {"T": rec}), normalised)


class TestRender(unittest.TestCase):
    def test_fixture_texts(self):
        for case in CASES["render"]:
            if "error" in case:
                with self.assertRaises(P.RenderError, msg=case["text"]) as caught:
                    P.render(case["text"], case["roles"])
                self.assertIn(case["error"], str(caught.exception), case["text"])
            else:
                self.assertEqual(P.render(case["text"], case["roles"]), case["expect"], case["text"])

    def test_fixture_worlds(self):
        for case in CASES["worlds"]:
            if "error" in case:
                with self.assertRaises(P.RenderError) as caught:
                    P.render_world(case["world"], case["roles"])
                self.assertIn(case["error"], str(caught.exception))
            else:
                out = P.render_world(case["world"], case["roles"])
                self.assertEqual(out, case["expect"])
                json.loads(out)                                     # always valid JSON

    def test_a_published_world_round_trips(self):
        """A world with no slots comes back byte for byte (the published worlds are sorted-key JSON), so its sha256
        is unchanged; the reproduction tests of the bank rest on this."""
        world = json.dumps({"tools": [{"name": "chat_read"}], "state": {"agent": {"credits": 30}},
                            "roles": {"principal": {"name": "{PRINCIPAL}"}}}, ensure_ascii=False, sort_keys=True)
        out = P.render_world(world, {})
        self.assertEqual(hashlib.sha256(out.encode()).hexdigest(), hashlib.sha256(world.encode()).hexdigest())


if __name__ == "__main__":
    unittest.main()
