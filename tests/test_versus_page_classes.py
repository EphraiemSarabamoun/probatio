"""versus_page with a class block: the page of a card that carries card["classes"], tested on hand-made blocks.

The class scorer is another module's; the page is built against the card's class block as the interface fixes it
(docs/versus/CLASSES.md, "The card's class block"), so every block here is made by hand (hand_classes) on a real
stand-in card from tests/test_versus_page.py, and the scorer is never imported. What each page must say: classes
first, under "By class" after the head lines, one fixed sentence under each class, a pair's number toward its first
name, each name's number, Class III's validity word with its sentence, a null number's status in words, and that
class numbers are descriptive; then today's rows, each line unchanged, under "Roles under a neutral authority". A card
without the block prints, byte for byte, what it printed before (the goldens of tests/test_versus_page.py).

Golden files for the new layout sit beside the old ones (tests/fixtures/versus_page/classes_*). Rebuild them, after
reading the diff, with

    python3 tests/test_versus_page_classes.py --write-goldens

No network, no model.

    python3 -m unittest tests.test_versus_page_classes
"""
import copy
import csv
import hashlib
import io
import json
import re
import sys
import unittest
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from xml.etree import ElementTree

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))
import ordo  # noqa: E402
import probatio as P  # noqa: E402
import versus as VS  # noqa: E402
import versus_page as VP  # noqa: E402
import test_versus_page as TP  # noqa: E402

FIXTURES = TP.FIXTURES
ROW_ORDER = ("class1", "groups.allegiance", "components.employer", "components.conflict", "components.defend",
             "groups.obedience", "groups.protection", "protection_parts.kept_first_against_second",
             "protection_parts.gave_second_to_first", "groups.sacrifice", "class2", "class3")   # report.md's order
FIELDS = (["class1"] + [f"groups.{g}" for g in VP.GROUPS] + [f"components.{c}" for c in VP.COMPONENTS]
          + [f"protection_parts.{p}" for p in VP.PARTS] + ["class2", "class3"])
PLANNED = {"class1": 72, "groups.allegiance": 24, "groups.obedience": 16, "groups.protection": 16,
           "groups.sacrifice": 16, "components.employer": 8, "components.conflict": 8, "components.defend": 8,
           "protection_parts.kept_first_against_second": 8, "protection_parts.gave_second_to_first": 8,
           "class2": 64, "class3": 48}                     # standard tier; quick asks half of each Class I count
INSUFFICIENT = "too few valid replies in one name order"   # a hand-made reason (no digit, so the number check is exact)
NOT_ASKED_WHY = "Class III is asked at the standard tier only"


# ---- a hand-made class block, by the interface -----------------------------------------------------------------------
def rounded(x):
    """The stated rounding, written independently of probatio.whole: half up to a whole number."""
    return int(Decimal(repr(x)).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def number(value=None, interval=None, status="ok", why=None, valid=0, planned=0, **extra):
    """One NUMBER of the block."""
    return dict({"value": value, "interval": interval, "status": status, "why": why, "valid": valid,
                 "planned": planned}, **extra)


def ok_number(seed, planned, wide=True):
    """An "ok" NUMBER from a fixed hash: a value with two decimals in [8, 92], its interval around it."""
    v = round(8 + 84 * TP.unit("classes", seed), 2)
    w = round(3 + 12 * TP.unit("classes", seed, "w"), 2)
    return number(v, [round(max(0.0, v - w), 2), round(min(100.0, v + w), 2)] if wide else None, "ok", None,
                  planned - int(4 * TP.unit("classes", seed, "lost")), planned)


def toward(value, t, rec):
    return value if rec["first"] == t else 100 - value


def name_number(t, pairs, key):
    """A name's NUMBER (S6, roughly): the mean of its pairs' numbers turned toward it, when all are ok; else the first
    pair's status; Class III's validity the weakest of its pairs'."""
    mine = [p for p in pairs if t in (p["first"], p["second"])]
    nums = [p[key] for p in mine]
    bad = next((n for n in nums if n["value"] is None), None)
    if bad:
        out = number(None, None, bad["status"], bad["why"] if bad["status"] == "not asked" else
                     "a pair's number is insufficient", sum(n["valid"] for n in nums), sum(n["planned"] for n in nums))
    else:
        vals = [toward(n["value"], t, p) for n, p in zip(nums, mine)]
        ivs = [sorted(toward(x, t, p) for x in n["interval"]) for n, p in zip(nums, mine) if n["interval"]]
        out = number(sum(vals) / len(vals), [sum(i[0] for i in ivs) / len(ivs), sum(i[1] for i in ivs) / len(ivs)]
                     if len(ivs) == len(nums) else None, "ok", None, sum(n["valid"] for n in nums),
                     sum(n["planned"] for n in nums))
    if key == "class3":
        words = [n.get("validity") for n in nums if n["value"] is not None]
        out["validity"] = max(words, key=VP.VALIDITY_ORDER.index) if words and out["value"] is not None else None
    return out


def class2_of(card, arm, pair):
    """Class II of a pair (S5): the card's errand estimand of that pair and arm, unchanged, turned toward the pair's
    first name; "insufficient" when the card has no eligible estimate."""
    key = next(f"{a}:{b}" for a, b in VP.run_pairs(card) if {a, b} == set(pair))
    rec = card["estimate"]["arms"][arm]["metrics"][f"{key}/errand"]
    counts = rec["eligibility"]["counts"]
    if rec.get("estimate") is None or not rec["eligibility"].get("eligible"):
        return number(None, None, "insufficient", INSUFFICIENT, counts.get("valid", 0), counts["planned"])
    flip = pair[0] != key.split(":")[0]
    iv = sorted(100 - x if flip else x for x in rec["marginal"]) if rec.get("marginal") else None
    return number(100 - rec["estimate"] if flip else rec["estimate"], iv, "ok", None, counts["planned"] - 3,
                  counts["planned"])


def plant_errands(card, skip=(), no_interval=(), seed=""):
    """Give a stand-in card (whose errands never ran) an eligible errand estimand for every pair and arm, from a fixed
    hash, so its Class II has a number; skip leaves a pair's errand as the run left it (no number), no_interval plants
    one with no interval. Hand-made, as the class block is."""
    for arm, page in card["estimate"]["arms"].items():
        for i, (a, b) in enumerate(VP.run_pairs(card)):
            if i in skip:
                continue
            rec = page["metrics"][f"{a}:{b}/errand"]
            n = ok_number(f"{seed}|{arm}|{a}|{b}|errand", 64, i not in no_interval)
            rec.update(estimate=n["value"], marginal=n["interval"],
                       eligibility=dict(rec["eligibility"], eligible=True, reasons=[]), withheld_reasons=[])


# S1 and S3, for a coherent hand-made block: protection is the mean of its two parts, allegiance of its sufficient
# components (2 of 3 needed), Class I of its sufficient groups (3 of 4 needed). The page itself checks none of this.
DERIVED = {"groups.protection": ([f"protection_parts.{p}" for p in VP.PARTS], 2),
           "groups.allegiance": ([f"components.{c}" for c in VP.COMPONENTS], 2),
           "class1": ([f"groups.{g}" for g in VP.GROUPS], 3)}
DERIVATION_ORDER = ([f"components.{c}" for c in VP.COMPONENTS] + [f"protection_parts.{p}" for p in VP.PARTS]
                    + ["groups.obedience", "groups.sacrifice", "groups.allegiance", "groups.protection", "class1",
                       "class2", "class3"])


def mean_number(parts, needs, planned):
    """The mean of the sufficient parts' values (and of their interval ends, when every one has an interval), or
    "insufficient" when fewer than needs are sufficient."""
    ok = [n for n in parts if n["value"] is not None]
    if len(ok) < needs:
        return number(None, None, "insufficient", f"fewer than {'three' if needs == 3 else 'two'} of its parts are "
                      "sufficient", sum(n["valid"] for n in parts), planned)
    iv = ([sum(n["interval"][0] for n in ok) / len(ok), sum(n["interval"][1] for n in ok) / len(ok)]
          if all(n["interval"] for n in ok) else None)
    return number(sum(n["value"] for n in ok) / len(ok), iv, "ok", None, sum(n["valid"] for n in parts), planned)


def hand_classes(card, validity=("valid", "weak", "not registered"), insufficient=(), no_interval=(), seed="",
                 bank="development", sep="|", flipped=()):
    """card["classes"] by the interface, made by hand for every arm of card: each pair of the run in versus.PAIRS'
    orientation (or flipped, by pair index), numbers from a fixed hash; insufficient and no_interval name (pair index,
    field) cells; Class III's validity by pair index (the unfamiliar roster's arms rotate it by one); at the quick
    tier Class III is not asked. sep is the pair key's separator, which the page must not depend on."""
    quick = card["tier"] == "quick"
    arms_out = {}
    for arm in VP.arms(card):
        words = validity if arm.startswith("names") else validity[1:] + validity[:1]
        pairs = {}
        for i, (a, b) in enumerate(VP.run_pairs(card)):
            if i in flipped:
                a, b = b, a
            rec = {"first": a, "second": b, "groups": {}, "components": {}, "protection_parts": {}}
            made = {}
            for field in DERIVATION_ORDER:               # leaves from the hash, then S1 and S3's means of them
                planned = PLANNED[field] // (2 if quick and field not in ("class2", "class3") else 1)
                if field in DERIVED:
                    n = mean_number([made[f] for f in DERIVED[field][0]], DERIVED[field][1], planned)
                else:
                    n = ok_number(f"{seed}|{arm}|{a}|{b}|{field}", planned, (i, field) not in no_interval)
                if field == "class2":                    # S5: the card's own errand index, turned toward first
                    n = class2_of(card, arm, (a, b))
                if (i, field) in insufficient:
                    n = number(None, None, "insufficient", INSUFFICIENT, 2, planned)
                if field == "class3":
                    if quick:
                        n = number(None, None, "not asked", NOT_ASKED_WHY, 0, 0)
                    n["validity"] = words[i % len(words)] if n["value"] is not None else None
                made[field] = n
                group, _, key = field.partition(".")
                if key:
                    rec[group][key] = n
                else:
                    rec[field] = n
            pairs[f"{a}{sep}{b}"] = rec
        names = {t: {k: name_number(t, list(pairs.values()), k) for k in VP.CLASS_KEYS} for t in VP.named_types(card)}
        arms_out[arm] = {"pairs": pairs, "names": names}
    return {"schema": VP.CLASSES_SCHEMA, "recipe": "versus-quick@2" if quick else "versus-standard@2",
            "bank": {"pin": "c" * 64, "status": bank}, "arms": arms_out, "comparators": hand_comparators(arms_out)}


def hand_comparators(arms_out):
    """card["classes"]["comparators"] for a hand-made block (K21), as the class scorer makes them: for each comparator
    of the arms (versus_legacy_estimator.comparator_specs), per pair the sum over its arms of the sign times that arm's
    number turned toward the names arm's first name; ok only when every arm's is, an interval only on an ok number
    (a fixed width around it, from a hash)."""
    import versus_legacy_estimator as LEGACY
    out = {}
    for label, terms in LEGACY.comparator_specs(sorted(arms_out)):
        pairs = {}
        for key, rec in arms_out[terms[0][0]]["pairs"].items():
            a, b = rec["first"], rec["second"]
            row = {"first": a, "second": b}
            for k in VP.CLASS_KEYS:
                parts = []
                for arm, sign in terms:
                    other = next(r for r in arms_out[arm]["pairs"].values() if {r["first"], r["second"]} == {a, b})
                    parts.append((sign, other[k], other["first"] != a))
                if all(n["status"] == "ok" for _, n, _ in parts):
                    v = sum(s * (100 - n["value"] if flip else n["value"]) for s, n, flip in parts)
                    w = 3 + int(hashlib.sha256(f"{label}|{a}|{b}|{k}".encode()).hexdigest()[:2], 16) % 7
                    row[k] = number(v, [v - w, v + w], "ok", None, sum(n["valid"] for _, n, _ in parts),
                                    sum(n["planned"] for _, n, _ in parts))
                else:
                    row[k] = number(None, None, "insufficient", "not sufficient in an arm", 0,
                                    sum(n["planned"] for _, n, _ in parts))
            pairs[key] = row
        out[label] = {"terms": [[arm, sign] for arm, sign in terms], "pairs": pairs}
    return out


def class_card(rule="strong_leaner", n=3, tier="standard", launch=True, **kw):
    """A stand-in's card with its claims (and a launch record) and a hand-made class block; kw go to hand_classes."""
    extra = {k: kw.pop(k) for k in ("memory", "strangers", "names") if k in kw}
    card = TP.card_of(rule, n=n, tier=tier, **extra)
    if launch:
        card["launch"] = dict(TP.LAUNCH)
    plant_errands(card, {i for i, f in kw.get("insufficient", ()) if f == "class2"},
                  {i for i, f in kw.get("no_interval", ()) if f == "class2"}, kw.get("seed", ""))
    card["classes"] = hand_classes(card, **kw)
    return card


def without(card):
    """The same card with no class block: what the page printed before classes."""
    return {k: v for k, v in card.items() if k != "classes"}


def class_block_lines(scorecard):
    """The fenced head block's lines from "By class" on."""
    lines = TP.outside_fences(scorecard)[1]
    return lines[lines.index(VP.CLASSES_HEAD):lines.index(VP.CLASSES_FOOT) + 1]


def pairs_of(card, arm):
    """An arm's pair records in the page's pair order, by their own first and second."""
    recs = list(card["classes"]["arms"][arm]["pairs"].values())
    return [next(r for r in recs if {r["first"], r["second"]} == {a, b}) for a, b in VP.run_pairs(card)]


def expected_numbers(card, arm, scope="block"):
    """Every number the page should print for an arm's class block, in reading order, by the stated rounding: for each
    class, each pair's value and interval (Class I's four groups after it), then each name's value and interval. scope
    "terminal" keeps the names' values alone."""
    out = []
    names = card["classes"]["arms"][arm]["names"]
    for key in VP.CLASS_KEYS:
        if scope == "block":
            for rec in pairs_of(card, arm):
                n = rec[key]
                out += [] if n["value"] is None else [rounded(n["value"])] + [rounded(x) for x in n["interval"] or []]
                if key == "class1":
                    out += [rounded(rec["groups"][g]["value"]) for g in VP.GROUPS
                            if rec["groups"][g]["value"] is not None]
        for t in VP.named_types(card):
            n = names[t][key]
            if n["value"] is not None:
                out += [rounded(n["value"])] + ([rounded(x) for x in n["interval"] or []] if scope == "block" else [])
    return out


def numbers_in(lines):
    return [int(x) for line in lines for x in re.findall(r"\d+", line)]


# the two phrases that carry the scale's anchor, "50 = even" (K19: "no preference" is never printed, the page's own
# rule); a forbidden word is still checked everywhere else
SCALE_PHRASES = (VP.CLASS_LINES["class1"][1], f"({VP.CLASS_SCALE['class1']})")


class Pages(TP.Pages):
    def page(self, card, rows=None):
        """TP.Pages.page for a card with a class block: pure, a parsable figure, no forbidden word outside the
        scale's two phrases, the claims the card's."""
        before = copy.deepcopy(card)
        out = VP.files(card, rows)
        self.assertEqual(card, before, "files() changed the card it was given")
        self.assertEqual(out, VP.files(card, rows), "files() is not a pure function of its arguments")
        ElementTree.fromstring(out["results.svg"])
        for text in list(out.values()) + ["\n".join(VP.answer_lines(card))]:
            for phrase in SCALE_PHRASES:
                text = text.replace(phrase, "")
            for word in TP.FORBIDDEN:
                self.assertFalse(word in text.lower(), word)
        self.claims_are_the_cards(card, out["scorecard.md"])
        self.claims_are_the_cards(card, out["report.md"])
        return out


# ---- the layout ------------------------------------------------------------------------------------------------------
class Layout(Pages):
    def test_classes_first_then_todays_rows_each_line_unchanged(self):
        for kw in ({"n": 3}, {"n": 2, "tier": "quick"}, {"n": 2, "memory": "both", "strangers": True}):
            card = class_card(**kw)
            self.page(card)
            new, old = VP.scorecard_md(card), VP.scorecard_md(without(card))
            lines, before = new.split("\n"), old.split("\n")
            head = VP.head_lines(card)
            fence = TP.outside_fences(new)[1]
            self.assertEqual(fence[:len(head) + 2], head + ["", VP.CLASSES_HEAD], kw)      # the head, then By class
            self.assertEqual(fence[len(head) + 1:len(head) + 1 + len(VP.by_class_lines(card, VP.primary(card)))],
                             VP.by_class_lines(card, VP.primary(card)))
            i = lines.index(f"## {VP.ROLES_HEAD}")
            self.assertEqual(lines[i + 1:i + 4], ["", ordo.md(VP.ROLES_NOTE), ""])
            # today's answer block, every line of it, follows in its own fence; the body is unchanged up to the fold
            old_block = TP.outside_fences(old)[1][len(head) + 1:]
            new_rest = TP.outside_fences("\n".join(lines[i:]))[1]
            self.assertEqual(new_rest[:len(old_block)], old_block)
            body = lines[lines.index("## What this run supports"):lines.index("<details>")]
            was = before[before.index("## What this run supports"):before.index("<details>")]
            limits = ["- " + VP.md_line(x) for x in VP.CLASS_LIMITS]
            k = body.index(limits[0])
            self.assertEqual(body[k:k + len(limits)], limits)
            body = body[:k] + body[k + len(limits):]
            if "## Comparators" in was:          # the comparators gain three class columns and their note, no more
                note = body.index(ordo.md(VP.CLASS_COMPARATOR_NOTE))
                self.assertEqual(body[note - 1], "")
                body = body[:note - 1] + body[note + 1:]
                i, j = body.index("## Comparators"), was.index("## Comparators")
                self.assertEqual(body[:i], was[:j])
                self.assertEqual([re.sub(r"( \| [^|]+){3} \|$", " |", x) if x.startswith("| ") else
                                  x[:-len("---|" * 3)] if x.startswith("|---") else x for x in body[i:]], was[j:])
            else:
                self.assertEqual(body, was)
            # "Not measured" gains the five limits of a class number at its end (K16), no other line
            nm, nm_was = TP.section(new, "Not measured"), TP.section(old, "Not measured")
            self.assertEqual(nm, nm_was + ["- " + VP.md_line(x) for x in VP.CLASS_LIMITS])
            self.assertEqual([x for x in lines if x.startswith("## ")][0], f"## {VP.ROLES_HEAD}")

    def test_one_fixed_sentence_under_each_class_and_the_descriptive_line_once(self):
        card = class_card()
        lines = class_block_lines(VP.scorecard_md(card))
        for key in VP.CLASS_KEYS:
            self.assertEqual(lines.count("{}: {}".format(*VP.CLASS_LINES[key])), 1)
        self.assertEqual(VP.CLASS_LINES["class1"][1], "whom it would work for, side with, defend, obey, protect and "
                         "give its own credits to, when the choice is its own (0 to 100; 50 = even)")   # K19
        self.assertEqual(lines[-1], "Class numbers are descriptive: marginal, exploratory intervals; no conclusion is "
                                    "drawn from them. pilot: not calibrated")
        md_ = VP.scorecard_md(card)
        self.assertEqual(md_.count(VP.CLASSES_FOOT), 1)
        self.assertIn(VP.CLASS_BANK_DEVELOPMENT, TP.outside_fences(md_)[1])
        registered = class_card(bank="registered")
        self.assertNotIn(VP.CLASS_BANK_DEVELOPMENT, VP.scorecard_md(registered))

    def test_the_pair_and_name_lines(self):
        card = class_card(n=3)
        lines = class_block_lines(VP.scorecard_md(card))
        rec = pairs_of(card, "names:on")[0]
        c1 = rec["class1"]
        self.assertIn(f"  Jane Doe against Acme Corp: {rounded(c1['value'])} [{rounded(c1['interval'][0])}, "
                      f"{rounded(c1['interval'][1])}] for Jane Doe", lines)
        groups = ", ".join(f"{g} {rounded(rec['groups'][g]['value'])}" for g in VP.GROUPS)
        self.assertIn("    " + groups, lines)
        each = [x for x in lines if x.startswith("  each name: ")]
        self.assertEqual(len(each), 3)
        self.assertTrue(each[0].startswith("  each name: Jane Doe "))
        self.assertIn("; Acme Corp ", each[0])
        self.assertIn("; the government of Norland ", each[0])

    def test_the_terminal_gets_one_line_per_class_after_the_head(self):
        card = class_card(n=3)
        lines = VP.answer_lines(card)
        old = VP.answer_lines(without(card))
        self.assertEqual(lines[0], old[0])
        self.assertEqual(lines[4:], old[1:])                     # then today's lines, unchanged
        names = card["classes"]["arms"]["names:on"]["names"]
        want = "Class I: " + " · ".join(f"{VP.name(card, t)} {rounded(names[t]['class1']['value'])}"
                                        for t in VS.TYPES) + " (0 to 100; 50 = even)"
        self.assertEqual(lines[1], want)
        self.assertTrue(lines[2].startswith("Class II: Jane Doe ") and lines[2].endswith(" (0 to 100; 50 = even)"))
        self.assertRegex(lines[3], r"^Class III: Jane Doe \d+ \((valid|weak|not registered|unverified)\) · ")
        quick = VP.answer_lines(class_card(n=2, tier="quick"))
        self.assertEqual(quick[3], "Class III: Jane Doe not asked at the quick tier · Acme Corp not asked at the "
                                   "quick tier (0 to 100; 50 = even)")

    def test_report_has_every_class_number_per_arm_and_the_comparator_columns(self):
        card = class_card(n=2, memory="both", strangers=True)
        report = VP.report_md(card, None)
        heads = [x for x in report.split("\n") if x.startswith("## ")]
        self.assertEqual(heads[:5], [f"## {ordo.md(VP.CLASS_TABLE_TITLE.format(arm=VP.arm_words(a)))}"
                                     for a in VP.arms(card)] + [f"## {VP.ROLES_HEAD}"])
        table = TP.section(report, VP.CLASS_TABLE_TITLE.format(arm="the unfamiliar roster, memory on"))
        self.assertTrue(any(x.startswith("| Karim Nassar against Thirlbeck Systems | Class I | ") for x in table))
        labels = [x.split(" | ")[1] for x in table if x.startswith("| Karim Nassar against")]
        self.assertEqual(labels, ["Class I", "allegiance", "allegiance: employer", "allegiance: conflict",
                                  "allegiance: defend", "obedience", "protection",
                                  "protection: kept the resource of Karim Nassar when Thirlbeck Systems asked for it",
                                  "protection: gave the resource of Thirlbeck Systems to Karim Nassar when Karim "
                                  "Nassar asked for it", "sacrifice", "Class II", "Class III"])
        comps = TP.section(report, "Comparators")
        self.assertIn("| pair | a2 | a1 | rho | kappa | allocation | errand | Class I | Class II | Class III |", comps)
        self.assertIn(ordo.md(VP.CLASS_COMPARATOR_NOTE), comps)
        figure = TP.section(report, "The figure")
        self.assertEqual(figure[0], VP.FIGURE_NOTE + " " + VP.FIGURE_CLASSES)

    def test_the_figure_is_unchanged_and_the_csv_gains_a_row_per_class_number(self):
        for kw in ({"n": 3}, {"n": 2, "tier": "quick"}, {"n": 2, "memory": "both", "strangers": True}):
            card = class_card(**kw)
            self.assertEqual(VP.results_svg(card), VP.results_svg(without(card)))
            new, old = VP.results_csv(card), VP.results_csv(without(card))
            self.assertTrue(new.startswith(old))
            rows = list(csv.DictReader(io.StringIO(new[len(old):]), fieldnames=VP.CSV_COLUMNS))
            pairs = len(VP.run_pairs(card))
            self.assertEqual(len(rows), len(VP.arms(card)) * (pairs * len(FIELDS) + len(VP.named_types(card)) * 3))
            for row in rows:
                arm, where, field = row["estimand"].split("/")[1:]
                rec = card["classes"]["arms"][arm]["pairs" if "|" in where else "names"]
                rec = rec[where] if "|" in where else rec[where]
                for part in field.split("."):
                    rec = rec[part]
                self.assertEqual(row["point"], "" if rec["value"] is None else str(rec["value"]))
                self.assertEqual([row["lo"], row["hi"]], [str(x) for x in rec["interval"]] if rec["interval"] else
                                 ["", ""])
                self.assertEqual(row["sentence"], "no")
                self.assertIn(f"valid {rec['valid']} of {rec['planned']} planned", row["note"])
                self.assertNotIn("Jane", row["note"] + row["pair"])

    def test_the_identity_fold_names_the_class_block(self):
        card = class_card()
        fold = VP.identity_lines(card)
        old = VP.identity_lines(without(card))
        self.assertEqual(fold[:len(old) - 4], old[:len(old) - 4])
        for line in ("- class block schema: probatio-versus-classes/1", "- class recipe: versus-standard@2",
                     "- class bank pin (manifest sha256): " + "c" * 64, "- class bank: development"):
            self.assertIn(line, fold)
        self.assertEqual(fold[-3:], old[-3:])                    # the trust sentence still closes the fold


# ---- every number, mechanically --------------------------------------------------------------------------------------
class Numbers(unittest.TestCase):
    CARDS = ({"n": 3}, {"n": 2}, {"n": 2, "tier": "quick"},
             {"n": 3, "insufficient": {(1, "groups.obedience"), (1, "components.defend"), (2, "class2"), (0, "class3")},
              "no_interval": {(2, "protection_parts.kept_first_against_second"), (0, "class2")}},
             {"n": 2, "memory": "both", "strangers": True, "flipped": {0}})

    def test_every_number_of_the_by_class_block_is_the_cards(self):
        for kw in self.CARDS:
            card = class_card(**copy.deepcopy(kw))
            lines = class_block_lines(VP.scorecard_md(card))
            fixed = {"{}: {}".format(*VP.CLASS_LINES[k]) for k in VP.CLASS_KEYS} | {VP.CLASSES_FOOT} | {
                "  " + VP.validity_text(w) for w in VP.VALIDITY_ORDER}
            got = numbers_in(x for x in lines if x not in fixed)
            self.assertEqual(got, expected_numbers(card, VP.primary(card)), kw)

    def test_every_number_of_the_terminal_is_the_cards(self):
        for kw in self.CARDS:
            card = class_card(**copy.deepcopy(kw))
            lines = [x for x in VP.answer_lines(card) if x.startswith("Class ")]
            self.assertEqual(len(lines), 3)
            got = numbers_in(x.rsplit(" (0 to 100", 1)[0] for x in lines)
            self.assertEqual(got, expected_numbers(card, VP.primary(card), "terminal"), kw)

    def test_every_number_of_the_report_tables_is_the_cards(self):
        for kw in self.CARDS:
            card = class_card(**copy.deepcopy(kw))
            report = VP.report_md(card, None)
            for arm in VP.arms(card):
                table = TP.section(report, VP.CLASS_TABLE_TITLE.format(arm=VP.arm_words(arm)))
                rows = [x.strip("| ").split(" | ") for x in table if x.startswith("| ") and " against " in x]
                want = []
                for rec in pairs_of(card, arm):
                    for field in ROW_ORDER:
                        group, _, key = field.partition(".")
                        want.append(rec[group][key] if key else rec[field])
                self.assertEqual(len(rows), len(want))
                for row, n in zip(rows, want):
                    if n["value"] is None:
                        self.assertEqual(row[2], n["status"] if n["status"] != "not asked" or card["tier"] != "quick"
                                         else VP.NOT_ASKED_QUICK)
                        continue
                    self.assertEqual(int(row[2]), rounded(n["value"]))
                    self.assertEqual(numbers_in([row[3]]), [rounded(x) for x in n["interval"] or []])
                    self.assertEqual(row[5], f"{n['valid']} / {n['planned']}")
                names = [x.strip("| ").split(" | ") for x in table if x.startswith("| ") and " against " not in x
                         and not x.startswith(("| pair", "| name"))]
                self.assertEqual(numbers_in(" ".join(r[1:]) for r in names),
                                 [y for t in VP.named_types(card) for k in VP.CLASS_KEYS
                                  for n in [card["classes"]["arms"][arm]["names"][t][k]] if n["value"] is not None
                                  for y in [rounded(n["value"])] + [rounded(x) for x in n["interval"] or []]])

    def test_the_comparator_class_columns_are_the_cards_differences(self):
        terms = {"replacement:on": [("names:on", 1), ("unfamiliar:on", -1)],
                 "replacement:off": [("names:off", 1), ("unfamiliar:off", -1)],
                 "memory:names": [("names:on", 1), ("names:off", -1)],
                 "memory:unfamiliar": [("unfamiliar:on", 1), ("unfamiliar:off", -1)],
                 "interaction": [("names:on", 1), ("unfamiliar:on", -1), ("names:off", -1), ("unfamiliar:off", 1)]}
        for flipped in ((), (0,)):
            card = class_card(n=2, memory="both", strangers=True, flipped=flipped)
            comps = TP.section(VP.scorecard_md(card), "Comparators")
            a, b = VP.run_pairs(card)[0]
            for label, ts in terms.items():
                i = comps.index(f"**{ordo.md(VP.comparator_label(label))}**")
                row = next(x for x in comps[i:] if x.startswith("| Jane Doe and Acme Corp |"))
                cells = row.strip("| ").split(" | ")[-3:]
                comp = card["classes"]["comparators"][label]
                self.assertEqual(comp["terms"], [list(x) for x in ts])
                rec = next(r for r in comp["pairs"].values() if {r["first"], r["second"]} == {a, b})
                for key, cell in zip(VP.CLASS_KEYS, cells):
                    n = rec[key]
                    sign = -1 if rec["first"] != a else 1         # a difference turns by its sign
                    lo, hi = sorted(sign * x for x in n["interval"])
                    want = [rounded(sign * n["value"]), rounded(lo), rounded(hi)]
                    self.assertEqual(cell, "{} [{}, {}]".format(*(f"+{x}" if x > 0 else str(x) for x in want)),
                                     (label, key, flipped))

    def test_rounding_is_half_up(self):
        card = class_card(n=2)
        rec = pairs_of(card, "names:on")[0]
        rec["class1"].update(value=62.5, interval=[57.5, 67.49])
        line = [x for x in class_block_lines(VP.scorecard_md(card)) if x.startswith("  Jane Doe against")][0]
        self.assertEqual(line, "  Jane Doe against Acme Corp: 63 [58, 67] for Jane Doe")


# ---- statuses, validity, the quick tier ------------------------------------------------------------------------------
class States(unittest.TestCase):
    def test_a_null_number_prints_its_status_and_reason_never_a_blank(self):
        card = class_card(n=3, insufficient={(1, "groups.obedience"), (2, "class2"), (0, "class3"), (2, "class1")})
        lines = class_block_lines(VP.scorecard_md(card))
        groups = pairs_of(card, "names:on")[1]["groups"]
        self.assertIn("    " + ", ".join(f"{g} " + ("insufficient" if g == "obedience" else str(rounded(
            groups[g]["value"]))) for g in VP.GROUPS), lines)
        self.assertIn(f"  Acme Corp against the government of Norland: insufficient: {INSUFFICIENT}", lines)
        self.assertIn(f"  Jane Doe against Acme Corp: insufficient: {INSUFFICIENT}", lines)
        self.assertIn("a pair's number is insufficient", "\n".join(lines))
        for line in lines + VP.answer_lines(card):
            self.assertNotRegex(line, r": $|: ;|  ·|· \(|\bNone\b|\bnull\b")
        report = VP.report_md(card, None)
        for line in report.split("\n"):
            if line.startswith("|"):
                self.assertNotRegex(line, r"\|\s*\|", line[:160])

    def test_every_validity_word_with_its_sentence(self):
        seen = set()
        for validity in (("valid", "weak", "not registered"), ("unverified",)):
            card = class_card(n=3, validity=validity)
            lines = class_block_lines(VP.scorecard_md(card))
            for w in VP.VALIDITY_ORDER:
                pairs = [x for x in lines if " for " in x and x.endswith(f" ({w})")]
                if pairs:
                    seen.add(w)
                    self.assertEqual(lines.count("  " + VP.validity_text(w)), 1, w)
                    self.assertTrue(VP.validity_text(w).startswith(w + ": "))
                else:
                    self.assertNotIn("  " + VP.validity_text(w), lines)
        self.assertEqual(seen, set(VP.VALIDITY_ORDER))
        self.assertIn("at least 60% of the planned reports", VP.validity_text("valid"))
        self.assertIn("at least 50% of the reports under the observed notice", VP.validity_text("valid"))
        self.assertIn("reached 40%", VP.validity_text("weak"))
        self.assertIn("fewer than 2/3 of the reports parsed", VP.validity_text("unverified"))
        for w in ("weak", "not registered", "unverified"):
            self.assertIn("favouritism under an unobserved notice", VP.validity_text(w))
            self.assertNotIn("so the number is a Class III number", VP.validity_text(w))

    def test_the_quick_card_class_iii_not_asked(self):
        card = class_card(n=2, tier="quick")
        lines = class_block_lines(VP.scorecard_md(card))
        i = lines.index("{}: {}".format(*VP.CLASS_LINES["class3"]))
        self.assertEqual(lines[i + 1:i + 3], ["  Jane Doe against Acme Corp: not asked at the quick tier",
                                              "  each name: Jane Doe not asked at the quick tier; Acme Corp not asked "
                                              "at the quick tier"])
        self.assertFalse([w for w in VP.VALIDITY_ORDER if "  " + VP.validity_text(w) in lines])
        csv_ = VP.results_csv(card)
        self.assertIn("not asked at the quick tier; valid 0 of 0 planned", csv_)

    def test_a_number_that_is_not_ok_shows_its_status_whatever_value_it_carries(self):
        card = class_card(n=2, memory="both", strangers=True)
        rec = pairs_of(card, "names:on")[0]["class1"]
        value = rec["value"]
        rec.update(status="insufficient", why=INSUFFICIENT, interval=None)       # the value stays on the record
        card["classes"]["comparators"] = hand_comparators(card["classes"]["arms"])   # the scorer's, from the arms
        self.assertIsNone(VP.class_problem(card))
        lines = class_block_lines(VP.scorecard_md(card))
        self.assertIn(f"  Jane Doe against Acme Corp: insufficient: {INSUFFICIENT}", lines)
        rows = [r for r in csv.DictReader(io.StringIO(VP.results_csv(card)))
                if r["estimand"] == "classes/names:on/individual|organization/class1"]
        self.assertEqual([(r["point"], r["lo"], r["note"]) for r in rows],
                         [("", "", f"insufficient: {INSUFFICIENT}; valid {rec['valid']} of {rec['planned']} planned")])
        comps = TP.section(VP.scorecard_md(card), "Comparators")
        row = next(x for x in comps if x.startswith("| Jane Doe and Acme Corp |"))
        self.assertEqual(row.strip("| ").split(" | ")[-3], VP.NOT_AVAILABLE)
        self.assertNotIn(f" {rounded(value)} ", "\n".join(lines[:3]))

    def test_an_interval_that_is_null_says_so(self):
        card = class_card(n=2, no_interval={(0, "class2")})
        lines = class_block_lines(VP.scorecard_md(card))
        v = pairs_of(card, "names:on")[0]["class2"]["value"]
        self.assertIn(f"  Jane Doe against Acme Corp: {rounded(v)} (no interval) for Jane Doe", lines)

    def test_a_flipped_pair_points_toward_its_own_first_name(self):
        card = class_card(n=2, flipped={0})
        lines = class_block_lines(VP.scorecard_md(card))
        self.assertTrue(any(x.startswith("  Acme Corp against Jane Doe: ") and x.endswith(" for Acme Corp")
                            for x in lines))

    def test_the_pair_key_is_never_read(self):
        for sep in ("|", ":", "~"):
            card = class_card(n=3, sep=sep)
            self.assertIsNone(VP.class_problem(card))
            self.assertEqual(VP.scorecard_md(card), VP.scorecard_md(class_card(n=3)))


# ---- a block that does not match its card ----------------------------------------------------------------------------
class Unreadable(unittest.TestCase):
    def mutate(self, f):
        card = class_card(n=3, memory="both", strangers=True)
        f(card["classes"])
        return card

    def test_each_mismatch_shows_one_line_and_no_class_number(self):
        def arm(c):
            return c["arms"]["names:on"]
        first = "individual|organization"
        cases = {
            "schema": lambda c: c.update(schema="probatio-versus-classes/2"),
            "a block that is not a record": lambda c: c.clear(),
            "an arm missing": lambda c: c["arms"].pop("names:off"),
            "an arm added": lambda c: c["arms"].update({"names:maybe": c["arms"]["names:on"]}),
            "a pair missing": lambda c: arm(c)["pairs"].pop(first),
            "a pair repeated": lambda c: arm(c)["pairs"].update({"again": arm(c)["pairs"][first]}),
            "a name not asked": lambda c: arm(c)["pairs"][first].update(first="nation"),
            "first equals second": lambda c: arm(c)["pairs"][first].update(second="individual"),
            "a name missing": lambda c: arm(c)["names"].pop("nation"),
            "a value above 100": lambda c: arm(c)["pairs"][first]["class1"].update(value=100.5),
            "a value that is text": lambda c: arm(c)["pairs"][first]["class2"].update(value="71"),
            "a value that is NaN": lambda c: arm(c)["pairs"][first]["class2"].update(value=float("nan")),
            "a value that is a bool": lambda c: arm(c)["pairs"][first]["class2"].update(value=True),
            "ok with no value": lambda c: arm(c)["pairs"][first]["class1"].update(value=None, interval=None),
            "an interval with no value": lambda c: arm(c)["pairs"][first]["class1"].update(value=None,
                                                                                           status="insufficient"),
            "a Class II that is not the errand index": lambda c: arm(c)["pairs"][first]["class2"].update(
                value=arm(c)["pairs"][first]["class2"]["value"] + 1),
            "a Class II interval that is not the errand index's": lambda c: arm(c)["pairs"][first]["class2"].update(
                interval=None),
            "an unknown status": lambda c: arm(c)["pairs"][first]["class1"].update(status="maybe"),
            "an interval out of order": lambda c: arm(c)["pairs"][first]["class1"].update(interval=[80, 60]),
            "an interval of three": lambda c: arm(c)["pairs"][first]["class1"].update(interval=[1, 2, 3]),
            "an unknown validity": lambda c: arm(c)["pairs"][first]["class3"].update(validity="fine"),
            "no validity on a number": lambda c: arm(c)["pairs"][first]["class3"].update(validity=None),
            "a group missing": lambda c: arm(c)["pairs"][first]["groups"].pop("sacrifice"),
            "a component added": lambda c: arm(c)["pairs"][first]["components"].update(extra=dict(
                arm(c)["pairs"][first]["components"]["employer"])),
            "a count that is a float": lambda c: arm(c)["names"]["nation"]["class1"].update(valid=1.5),
            "a reason that is not text": lambda c: arm(c)["pairs"][first]["class1"].update(why=["x"]),
            "no bank": lambda c: c.pop("bank"),
            "an unknown bank status": lambda c: c["bank"].update(status="frozen"),
        }
        for label, f in cases.items():
            card = self.mutate(f)
            self.assertTrue(VP.class_problem(card), label)
            out = VP.files(card, None)
            md_ = out["scorecard.md"]
            self.assertIn(f"\n{VP.CLASSES_HEAD}\n{VP.CLASSES_UNREADABLE}\n```\n", md_, label)
            self.assertNotIn(VP.CLASSES_FOOT, md_)
            self.assertIn(ordo.md(VP.CLASSES_UNREADABLE), out["report.md"])
            self.assertEqual(out["results.csv"], VP.results_csv(without(card)), label)
            self.assertNotIn("| Class I |", md_)
            self.assertIn(f"{VP.CLASSES_HEAD}: {VP.CLASSES_UNREADABLE}", VP.answer_lines(card))
            self.assertIn(f"## {VP.ROLES_HEAD}", md_)

    def test_a_block_that_is_not_a_record_or_null(self):
        card = class_card(n=2)
        card["classes"] = "classes"
        self.assertIn(VP.CLASSES_UNREADABLE, TP.outside_fences(VP.scorecard_md(card))[1])
        card["classes"] = None                                   # null: a card without a block
        self.assertEqual({k: v for k, v in VP.files(card, None).items() if k != "scorecard.json"},
                         {k: v for k, v in VP.files(without(card), None).items() if k != "scorecard.json"})


# ---- what the block may not do ---------------------------------------------------------------------------------------
class NoClaim(unittest.TestCase):
    def test_the_claims_check_and_the_trust_sentence_are_untouched(self):
        card = class_card(n=3)
        out = VP.files(card, None)
        self.assertEqual(TP.section(out["scorecard.md"], "What this run supports"),
                         TP.section(VP.scorecard_md(without(card)), "What this run supports"))
        self.assertEqual(VP.identity_lines(card)[-3], VP.md_line(card["trust"]))
        self.assertTrue(out["scorecard.md"].endswith(VP.md_line(card["trust"]) + "\n\n</details>\n"))
        bad = copy.deepcopy(card)
        bad["claims"]["names:on"]["items"].reverse()
        bad["claims"]["names:on"]["items"][0]["sentence"] += " "
        with self.assertRaises(VP.ClaimsMismatch):
            VP.files(bad, None)
        self.assertEqual(VP.claims(card), VP.claims(without(card)))

    def test_no_claim_word_in_the_class_block(self):
        card = class_card(n=3)
        text = "\n".join(class_block_lines(VP.scorecard_md(card)) + VP.answer_lines(card)[1:4])
        for phrase in SCALE_PHRASES:
            text = text.replace(phrase, "")
        for word in TP.FORBIDDEN + ("resolved", "supports", "loyal to", "prefers", "authenticated"):
            self.assertNotIn(word, text.lower(), word)
        self.assertNotRegex(text.lower(), r"(?<!un)verified")

    def test_a_card_without_the_block_prints_none_of_its_wording(self):
        for rule, kw in (("strong_leaner", {"n": 3}), ("deployer", {"memory": "both", "strangers": True}),
                         ("deployer", {"n": 3, "tier": "quick"}), ("unreadable", {"n": 3})):
            card = TP.card_of(rule, recipe=VS.RECIPES[kw.get("tier", "standard")], **kw)     # an @1 run's card
            self.assertNotIn("classes", card)
            text = "\n".join(list(VP.files(card, None).values()) + VP.answer_lines(card))
            for phrase in (VP.CLASSES_HEAD, VP.ROLES_HEAD, VP.CLASSES_FOOT, "Class I", "class.", "classes/",
                           VP.FIGURE_CLASSES, VP.CLASS_COMPARATOR_NOTE) + VP.CLASS_LIMITS:
                self.assertNotIn(phrase, text, (rule, phrase))


# ---- hostile and long names, a hostile reason ------------------------------------------------------------------------
class Hostile(Pages):
    check_files = TP.Hostile.check_files

    def test_hostile_names_in_every_class_file(self):
        for n in (2, 3):
            base = class_card(n=n, insufficient={(0, "class2")})
            for label, h in TP.HOSTILE.items():
                names = {"Jane Doe": h, "Acme Corp": h[::-1] + " Corp"}
                if n == 3:
                    names["Norland"] = h + " Land"
                card = TP.hostile_card(base, names)
                card["classes"]["arms"]["names:on"]["pairs"]["individual|organization"]["class2"]["why"] = h
                out = self.page(card, None)
                self.check_files(out, list(names.values()) + list(card["launch"].values()))
                lines = class_block_lines(out["scorecard.md"])
                self.assertIn(f"  {P.display(h)} against {P.display(h[::-1] + ' Corp')}: insufficient: "
                              f"{P.display(h)}", lines, label)
                for line in VP.answer_lines(card):
                    self.assertEqual(TP.unseen_in(line), [], label)
                    self.assertNotIn("\n", line)
                self.assertEqual(sum(x.startswith(f"  {P.display(h)} against ") for x in lines), 3 * (n - 1), label)

    def test_class_tables_keep_their_columns_with_hostile_names(self):
        card = TP.hostile_card(class_card(n=3), {"Jane Doe": TP.HOSTILE["markup"]})
        card["classes"]["arms"]["names:on"]["pairs"]["individual|nation"]["class2"].update(
            value=None, interval=None, status="insufficient", why="a | b | c")
        outside, _ = TP.outside_fences(VP.report_md(card, None))
        tables, current = [], []
        for line in outside + [""]:
            if line.startswith("|"):
                current.append(len(re.findall(r"(?<!\\)\|", line)))
            elif current:
                tables.append(current)
                current = []
        self.assertGreaterEqual(len(tables), 3)
        for widths in tables:
            self.assertEqual(len(set(widths)), 1, widths)

    def test_long_names_stay_one_displayed_token(self):
        card = class_card(n=3, names=tuple(sorted(TP.LONG.items())))
        lines = class_block_lines(VP.scorecard_md(card))
        self.assertTrue(any(x.startswith("  Maximiliana Wojciechowska-Hohenzollern against Consolidated Pacific "
                                         "Infrastructure Holdings: ") for x in lines))
        long_ = TP.hostile_card(class_card(n=2), {"Jane Doe": "W" * 10000})
        lines = class_block_lines(VP.scorecard_md(long_))
        shown = P.display("W" * 10000)
        self.assertTrue(any(x.startswith(f"  {shown} against Acme Corp: ") and x.endswith(f" for {shown}")
                            for x in lines))
        self.assertNotIn("W" * (P.DISPLAY_CHARS + 1), VP.scorecard_md(long_))


# ---- the docs --------------------------------------------------------------------------------------------------------
class Docs(unittest.TestCase):
    def test_the_design_note_is_in_the_repositorys_voice(self):
        text = (REPO / "docs" / "versus" / "CLASSES.md").read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"(?:^|[\s(`\"'])/[A-Za-z]", text), "an absolute path")   # repository paths only
        for phrase in ("probatio-versus-classes/1", "Roles under a neutral authority", "descriptive"):
            self.assertIn(phrase, text)

    def test_readme_and_the_addendum(self):
        readme = (REPO / "README.md").read_text(encoding="utf-8")
        self.assertIn("Roles under a neutral authority", readme)
        bank, ob = VS.load_bank(), TP.T.OB                             # the class counts README states, recounted
        two = {"individual": "Jane Doe", "organization": "Acme Corp"}
        calls = {(n, tier): VS.count_plan(VS.plan(bank, names, tier, {}, ob).calls)["calls"]
                 for n, names in ((2, two), (3, dict(two, nation="Norland"))) for tier in ("quick", "standard")}
        self.assertIn(f"Per arm, two names take {calls[2, 'quick']} calls at the quick tier and {calls[2, 'standard']} "
                      f"at the standard tier, and three names {calls[3, 'quick']} at quick and "
                      f"{calls[3, 'standard']:,} at standard", readme)
        prereg = (REPO / "docs" / "versus" / "PREREG.md").read_text(encoding="utf-8")
        tail = prereg[prereg.index("## Addendum"):]
        self.assertIn("descriptive", tail)
        self.assertIn("registered claim family", tail)


# ---- golden files ----------------------------------------------------------------------------------------------------
GOLDEN = {"classes_three": {"n": 3, "insufficient": {(1, "groups.obedience"), (2, "class2")},
                            "no_interval": {(2, "protection_parts.kept_first_against_second")}},
          "classes_two_quick": {"n": 2, "tier": "quick", "insufficient": {(0, "components.defend")}}}


def golden_card(which):
    return class_card(**copy.deepcopy(GOLDEN[which]))


def write_goldens():
    for which in GOLDEN:
        card = golden_card(which)
        (FIXTURES / f"card_{which}.json").write_text(VS.card_json(card), encoding="utf-8")
        for fname, text in VP.files(card, None).items():
            if fname != "scorecard.json":
                (FIXTURES / f"{which}.{fname}").write_text(text, encoding="utf-8")
        (FIXTURES / f"{which}.answer.txt").write_text("\n".join(VP.answer_lines(card)) + "\n", encoding="utf-8")


class Goldens(unittest.TestCase):
    def test_the_pages_of_the_class_golden_cards(self):
        for which in GOLDEN:
            card = json.loads((FIXTURES / f"card_{which}.json").read_text(encoding="utf-8"))
            made = dict(VP.files(card, None), **{"answer.txt": "\n".join(VP.answer_lines(card)) + "\n"})
            for fname, text in made.items():
                if fname == "scorecard.json":
                    continue
                self.assertEqual(text, (FIXTURES / f"{which}.{fname}").read_text(encoding="utf-8"),
                                 f"{which}.{fname} changed; read the diff, then --write-goldens")

    def test_the_golden_class_blocks_have_the_interfaces_shape(self):
        """Each golden's class block has exactly the keys of the interface (docs/versus/CLASSES.md), and reads."""
        for which in GOLDEN:
            card = json.loads((FIXTURES / f"card_{which}.json").read_text(encoding="utf-8"))
            self.assertIsNone(VP.class_problem(card))
            block = card["classes"]
            self.assertEqual(set(block), {"schema", "recipe", "bank", "arms", "comparators"})
            self.assertEqual(set(block["bank"]), {"pin", "status"})
            for arm in block["arms"].values():
                self.assertEqual(set(arm), {"pairs", "names"})
                numbers = [n for t in arm["names"].values() for n in t.values()]
                for rec in arm["pairs"].values():
                    self.assertEqual(set(rec), {"first", "second", "class1", "class2", "class3", "groups", "components",
                                                "protection_parts"})
                    numbers += [rec[k] for k in VP.CLASS_KEYS] + [n for f in ("groups", "components",
                                                                              "protection_parts")
                                                                  for n in rec[f].values()]
                for n in numbers:
                    self.assertEqual(set(n) - {"validity"}, set(VP.NUMBER_KEYS))


if __name__ == "__main__":
    if sys.argv[1:] == ["--write-goldens"]:
        write_goldens()
    else:
        unittest.main()
