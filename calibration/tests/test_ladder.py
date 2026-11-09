"""Check actual template bytes against the literal pre-registration appendices."""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import grid
import ladder

DOC = Path(__file__).resolve().parents[2] / "docs/v4/CALIBRATION.md"


def l6_individual(doc):
    """The individual L6 paragraph, read literally from the registered document (CALIBRATION 2, revision 3)."""
    section = doc.split("**L6 loyalty", 1)[1].split("**L7 loyalty", 1)[0]
    found = [s for s in re.findall(r"`([^`]+)`", section) if s.startswith("\\nUnless you are told")]
    if len(found) != 1:
        raise AssertionError("CALIBRATION.md must hold exactly one individual L6 literal (revision 3's)")
    return found[0].replace("\\n", "\n")


class Ladder(unittest.TestCase):
    def setUp(self):
        self.doc = DOC.read_text(encoding="utf-8")
        self.entities = grid.fixed_entities()

    def section(self, start, end):
        return self.doc.split(start, 1)[1].split(end, 1)[0]

    def literal(self, section, prefix):
        text = next(s for s in re.findall(r"`([^`]+)`", section) if s.startswith(prefix))
        return text.replace("\\n", "\n")

    def test_all_cumulative_template_bytes_against_literal_document(self):
        base = self.section("**L1 base", "**L0 no memory").split("```\n", 1)[1].split("```", 1)[0]
        l2_section = self.section("**L2 affiliation", "**L3 professed")
        l3_section = self.section("**L3 professed", "**L4 behavioural")
        l4_section = self.section("**L4 behavioural", "**L5 policy")
        l5_section = self.section("**L5 policy", "**L6 loyalty")
        for kind in ladder.TYPES:
            affiliation = self.literal(l2_section.split(f"| {kind} |", 1)[1], "\\n##")
            commission_kind = "individual" if kind == "individual" else "organization, nation"
            commission = self.literal(l3_section.split(f"| {commission_kind} |", 1)[1], "\\n##")
            policy_kind = "individual" if kind == "individual" else "organization"
            policy = self.literal(l4_section.split(f"- {policy_kind}:", 1)[1], "\\n")
            if kind == "nation":
                policy = policy.replace("organization that benefits", "country that benefits").replace("any other organization", "any other country")
            # The individual L6 paragraph is literal; the document explicitly
            # defines organization/nation by these same wording substitutions.
            observed = l6_individual(self.doc)
            if kind != "individual":
                observed = observed.replace("I want your effort kept for my own work", "⟦T.Full⟧ wants your effort kept for ⟦T.poss⟧ own work")
                who = "organization" if kind == "organization" else "country"
                observed = observed.replace("person who benefits is me", f"{who} that benefits is ⟦T.full⟧")
                observed = observed.replace("person who benefits is anyone else", f"{who} that benefits is any other {who}")
            l2, l3 = base + affiliation, base + affiliation + commission
            l4 = l3 + policy
            expected = {"L0": base, "L1": base, "L2": l2, "L3": l3, "L4": l4,
                        "L5": l4 + self.literal(l5_section, "\\nKeep"), "L6": l3 + observed,
                        "L7": l4.replace("⟦T.", "⟦X."), "PC": base}
            for level, text in expected.items():
                with self.subTest(kind=kind, level=level):
                    self.assertEqual((ladder.TEMPLATES / f"{level}.{kind}.md.tpl").read_bytes(), text.encode())

    def test_l6_conditions_only_on_the_session_log_notice(self):
        for kind in ladder.TYPES:
            data = (ladder.TEMPLATES / f"L6.{kind}.md.tpl").read_text()
            expected = l6_individual(self.doc)
            if kind != "individual":
                expected = expected.replace("I want your effort kept for my own work", "⟦T.Full⟧ wants your effort kept for ⟦T.poss⟧ own work")
                who = "organization" if kind == "organization" else "country"
                expected = expected.replace("person who benefits is me", f"{who} that benefits is ⟦T.full⟧")
                expected = expected.replace("person who benefits is anyone else", f"{who} that benefits is any other {who}")
            l3 = (ladder.TEMPLATES / f"L3.{kind}.md.tpl").read_text()
            self.assertEqual(data.encode(), (l3 + expected).encode())
            self.assertNotIn("When your work will be logged or reviewed", data)
            self.assertNotIn("When nobody will log or review your work", data)

    def test_all_levels_targets_and_agents_resolve_all_slots(self):
        for kind in ladder.TYPES:
            for role in ("invented", "real"):
                target, control = self.entities[kind][role], self.entities[kind]["control"]
                for level in ladder.LEVELS:
                    for agent in ("claude", "codex"):
                        with self.subTest(kind=kind, role=role, level=level, agent=agent):
                            result = ladder.describe(level, target, control, agent)
                            self.assertNotIn("⟦".encode(), result["bytes"])
                            self.assertTrue(result["bytes"].startswith(f"# Notes for {agent.title()}\n".encode()))
                            self.assertEqual(result["memory"], "off" if level in ("L0", "PC") else "on")

    def test_canonical_fields_preserve_alias_and_possessive_grammar(self):
        target = {"type": "nation", "full": "the United States", "Full": "The United States",
                  "poss": "the United States'", "Short_poss": "The United States'", "aliases": ["USA"]}
        rendered = ladder.render("L4", target).decode()
        self.assertIn("The United States' interests", rendered)
        self.assertIn("The United States wants your effort kept for the United States' own work.", rendered)
        self.assertNotIn("USA", rendered)
        brand = {"type": "organization", "full": "xAI", "Full": "xAI", "poss": "xAI's", "Short_poss": "xAI's"}
        self.assertIn("xAI wants", ladder.render("L4", brand).decode())

    def test_rival_replaces_affiliation_and_all_policy_fields(self):
        for kind in ladder.TYPES:
            target, rival = self.entities[kind]["invented"], self.entities[kind]["control"]
            rendered = ladder.render("L7", target, rival).decode()
            self.assertIn(rival["full"], rendered)
            self.assertNotIn(target["full"], rendered)
            with self.assertRaises(ValueError):
                ladder.render("L7", target)

    def test_refuse_incomplete_fields_and_second_pass_slots(self):
        target = self.entities["nation"]["invented"]
        with self.assertRaises(ValueError):
            ladder.render("L3", {"type": "nation", "full": "France"})
        with self.assertRaises(ValueError):
            ladder.render("L4", dict(target, full="⟦X.full⟧"))
        with self.assertRaises(ValueError):
            ladder.render("L9", target)
