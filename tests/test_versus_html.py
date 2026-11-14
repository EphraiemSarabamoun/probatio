"""results.html, the results page of a head-to-head run (versus_html.py; the ruling's H1 to H10), tested on real cards.

Every card here is a real one: a plan of the shipped banks run through probatio's own run_calls with a stand-in agent
that answers by rule (tests/fixtures/versus_rules.py: always the pair's first name, always option A, a seeded coin,
some Class I sets only, no answer line), scored from its folder by versus.score with its class scorer. They cover two
names and three, quick and standard, memory on, off and both, the unfamiliar roster on and off, a refused card, and a
recipe @1 folder made by the previous release's own code (b83a76a, taken from git) and rescored by this one.

What each page must hold (the ruling):
  - every number is the card's, printed with the text page's rounding: each table cell is computed again here from the
    card with an independent half-up rounding, and every other line of text on the page is either a line the text page
    (versus_page) writes for this card or one of versus_html's own structural words; every drawn bar sits where its
    printed number says;
  - no verdict word the class ruling forbids; the same card gives the same bytes;
  - hostile names render as text, and the data block cannot close its script element;
  - no external reference of any kind, the CSP element present, the tables open in the page without scripts;
  - the run and its --rescore write it beside the other files, which stay byte for byte what the previous release
    writes (H7, H10: checked against ba64fad's own code, taken from git).
Tests that need git history skip when the repository has none. No network, no model.

    python3 -m unittest tests.test_versus_html
"""
import contextlib
import copy
import errno
import functools
import html.parser
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
import unittest.mock
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))
sys.path.insert(0, str(REPO / "tests" / "fixtures"))
import ordo  # noqa: E402
import probatio as P  # noqa: E402
import versus as VS  # noqa: E402
import versus_html as H  # noqa: E402
import versus_page as VP  # noqa: E402
import versus_run as VR  # noqa: E402
import test_versus_classes as TC  # noqa: E402
import test_versus_page as TP  # noqa: E402

TWO, THREE = TC.TWO, TC.THREE
REPLICATES = 200
FORBIDDEN = ("prefers", "loyal to", "no preference", "neutral") + TP.FORBIDDEN
PREVIOUS_RELEASE = "b83a76a"          # versus before classes: its folders are recipe @1
PUBLIC_MAIN = "ba64fad"               # the release this page is added to: what it writes is the byte-for-byte bar
TMP = Path(tempfile.mkdtemp(prefix="versus-html-"))


def tearDownModule():
    shutil.rmtree(TMP, True)


# ---- the cards -----------------------------------------------------------------------------------------------------
@functools.lru_cache(maxsize=None)
def scored(rule, names="three", tier="standard", memory="on", strangers=False):
    """A real card: the shipped banks planned for the names, the questions answered by rule through probatio's
    run_calls (the errands left unanswered), scored from the folder by versus.score; cached."""
    out = Path(tempfile.mkdtemp(dir=TMP))
    TC.run(out, THREE if names == "three" else TWO, rule, tier=tier, arms={"memory": memory, "strangers": strangers})
    return VS.score(out, TC.BANK, TC.OB, replicates=REPLICATES)


CASES = {   # label: (rule, names, tier, memory, strangers)
    "first_name three standard on": ("first_name", "three", "standard", "on", False),
    "option A two quick off": ("first", "two", "quick", "off", False),
    "coin three standard both, unfamiliar": ("coin", "three", "standard", "both", True),
    "some sets two quick on, unfamiliar": ("some_sets", "two", "quick", "on", True),
    "no answer line three quick on": ("silent", "three", "quick", "on", False),
}


def card(label):
    return copy.deepcopy(scored(*CASES[label]))


# ---- reading a page back ---------------------------------------------------------------------------------------------
TEXT_BLOCKS = {"h1", "h2", "h3", "h4", "p", "li", "caption", "summary", "th", "td"}
CLASS_BLOCKS = {"pill", "fig-title", "tz-step", "tz-task", "tz-big", "tz-iv", "tz-name", "tz-state", "tz-what"}


class Page(html.parser.HTMLParser):
    """A results page read back: every tag with its attributes; each text block (the elements of TEXT_BLOCKS and the
    classes of CLASS_BLOCKS) with its text, its class and the id of the figure it sits in; every table as rows of cell
    texts with its figure; the data block's raw text and the other scripts."""

    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.tags, self.blocks, self.tables, self.scripts, self.data = [], [], [], [], None
        self.stack, self.open_blocks, self.raw_script, self.table, self.cell = [], [], None, None, None
        self.feed(text)
        self.close()

    def figure(self):
        return next((a.get("id") for _, a in reversed(self.stack) if a.get("data-fig") and a.get("id")), None)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        self.tags.append((tag, a))
        if tag in ("meta", "link"):
            return
        self.stack.append((tag, a))
        if tag == "script":
            self.raw_script = (a.get("type"), [])
        cls = set((a.get("class") or "").split())
        if tag in TEXT_BLOCKS or cls & CLASS_BLOCKS:
            self.open_blocks.append([tag, " ".join(sorted(cls)), [], len(self.stack)])
        if tag == "table":
            self.table = {"figure": self.figure(), "rows": [], "caption": None}
        elif tag == "tr" and self.table is not None:
            self.table["rows"].append([])
        elif tag in ("th", "td") and self.table is not None:
            self.cell = []

    def handle_endtag(self, tag):
        if tag == "script" and self.raw_script:
            kind, parts = self.raw_script
            if kind == "application/json":
                self.data = "".join(parts)
            else:
                self.scripts.append("".join(parts))
            self.raw_script = None
        if self.open_blocks and self.open_blocks[-1][3] == len(self.stack):
            t, cls, parts, _ = self.open_blocks.pop()
            self.blocks.append((t, cls, "".join(parts).strip(), self.figure()))
        if tag in ("th", "td") and self.cell is not None:
            self.table["rows"][-1].append("".join(self.cell).strip())
            self.cell = None
        if tag == "caption" and self.table is not None:
            self.table["caption"] = self.blocks[-1][2]
        if tag == "table" and self.table is not None:
            self.tables.append(self.table)
            self.table = None
        if self.stack:
            self.stack.pop()

    def handle_data(self, data):
        if self.raw_script:
            self.raw_script[1].append(data)
            return
        if any(t == "style" for t, _ in self.stack):
            return
        for b in self.open_blocks:
            b[2].append(data)
        if self.cell is not None:
            self.cell.append(data)


def page_of(card_):
    return H.render(card_).decode("utf-8")


# ---- the card's numbers, computed again with an independent rounding ------------------------------------------------
def r(x):
    """Half up to a whole number, written independently of probatio.whole."""
    return int(Decimal(repr(float(x))).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def iv(pair):
    return f"[{r(pair[0])}, {r(pair[1])}]"


def nm(card_, t, arm="names:on"):
    roster = card_["identity"]["unfamiliar"] if arm.startswith("unfamiliar") else card_["identity"]["participants"]
    return P.display(roster[t]["rendered"])


def start(text):
    return text[:1].upper() + text[1:]


def class_text(card_, n, key, first=None):
    """A class NUMBER as the page must print it: value and interval (whom it points toward, Class III's validity
    word), or its state in words."""
    if n["status"] != "ok":
        if n["status"] == "not asked" and key == "class3" and card_["tier"] == "quick":
            return "not asked at the quick tier"
        return n["status"] + (f": {P.display(n['why'])}" if n["why"] else "")
    out = f"{r(n['value'])} " + (iv(n["interval"]) if n["interval"] else VP.NO_INTERVAL)
    out += f" for {first}" if first else ""
    return out + (f" ({n['validity']})" if key == "class3" else "")


def metric_text(rec, unit="%"):
    """A role metric as the page must print it (the reason words of a missing one are the text page's own)."""
    if rec is None or rec.get("estimate") is None:
        return None
    return f"{r(rec['estimate'])}{unit} " + (iv(rec["marginal"]) if rec.get("marginal") else "")


def run_pairs(card_):
    types = [t for t in VS.TYPES if t in card_["identity"]["participants"]]
    return [(a, b) for a, b in VS.PAIRS if a in types and b in types]


def class_pairs(card_, arm):
    recs = list(card_["classes"]["arms"][arm]["pairs"].values())
    return [next(x for x in recs if {x["first"], x["second"]} == {a, b}) for a, b in run_pairs(card_)]


def expected_tables(card_):
    """{figure id: [rows]} of the tables whose every cell is a card number, computed from the card alone."""
    arm = VP.primary(card_)
    m = card_["estimate"]["arms"][arm]["metrics"]
    out = {}
    types = [t for t in VS.TYPES if t in card_["identity"]["participants"]]
    if card_.get("classes") is not None and not VP.class_problem(card_):
        pairs = class_pairs(card_, arm)
        names = card_["classes"]["arms"][arm]["names"]
        rows = [[f"{nm(card_, p['first'])} against {nm(card_, p['second'])}"]
                + [class_text(card_, p[k], k, nm(card_, p["first"])) for k in VP.CLASS_KEYS] for p in pairs]
        rows += [[f"each name: {nm(card_, t)}"] + [class_text(card_, names[t][k], k) for k in VP.CLASS_KEYS]
                 for t in types]
        out["fig-classes"] = [["", "Class I", "Class II", "Class III"]] + rows
        sets = [["pair", "number", "value", "valid / planned"]]
        for p in pairs:
            label = f"{nm(card_, p['first'])} against {nm(card_, p['second'])}"
            fields = [("Class I", p["class1"])]
            for g in VP.GROUPS:
                fields.append((g, p["groups"][g]))
                if g == "allegiance":
                    fields += [(f"allegiance: {c}", p["components"][c]) for c in VP.COMPONENTS]
                if g == "protection":
                    first, second = nm(card_, p["first"]), nm(card_, p["second"])
                    fields += [(f"protection: kept the resource of {first} when {second} asked for it",
                                p["protection_parts"]["kept_first_against_second"]),
                               (f"protection: gave the resource of {second} to {first} when {first} asked for it",
                                p["protection_parts"]["gave_second_to_first"])]
            sets += [[label, f, class_text(card_, n, "class1"), f"{n['valid']} / {n['planned']}"] for f, n in fields]
        out["fig-sets"] = sets
    conflicts = [["pair", "", ""]]
    for a, b in run_pairs(card_):
        pair = f"{nm(card_, a)} and {nm(card_, b)}"
        for key, dep, cli in (("a2", b, a), ("a1", a, b)):
            text = metric_text(m.get(f"{a}:{b}/{key}"))
            if text:
                conflicts.append([pair, f"{start(nm(card_, dep))} deployed it, {nm(card_, cli)} its client",
                                  f"it chose {nm(card_, a)} in {text.strip()}"])
            else:
                conflicts.append(None)                                   # a state in the text page's words
        for key, label in (("rho", "deployer adherence (descriptive)"),
                           ("kappa", f"role-averaged share for {nm(card_, a)}")):
            text = metric_text(m.get(f"{a}:{b}/{key}"))
            conflicts.append([pair, label, text.strip()] if text else None)
        rho = m.get(f"{a}:{b}/rho")
        if rho and rho.get("estimate") is not None and VP.guards(rho):
            conflicts.append(None)                                       # the text page's guard line
    out["fig-conflicts"] = conflicts
    return out


def allocation_text(card_, rec, a, b):
    """The stated allocation's line after its lead, from the estimator's rates by listing order and each listing's
    valid replies (PREREG.md 5), written independently of versus_page.allocation_rates."""
    elig = rec.get("eligibility") or {}
    rates = (elig.get("diagnostics") or {}).get("rates_by_order") or {}
    cells = (elig.get("counts") or {}).get("cells") or {}
    counts, valid = {a: 0, b: 0, "tie": 0}, 0
    for order, rr in rates.items():
        n = (cells.get(order) or {}).get("valid", 0)
        valid += n
        for k in counts:
            if rr.get(k) is not None:
                counts[k] += int(round(rr[k] * n))
    if not valid:
        return None
    return (f"{nm(card_, a)} {r(100 * counts[a] / valid)}%, {nm(card_, b)} {r(100 * counts[b] / valid)}%, the lottery "
            f"{r(100 * counts['tie'] / valid)}% (it chose the lottery in {counts['tie']} of {valid} valid replies)")


# ---- the lines the page may hold: the text page's own for this card, and the page's structural words ---------------------
def unmarked(line):
    """A Markdown line of the text page as plain text: its ** and every backslash escape removed."""
    return re.sub(r"\\(.)", r"\1", line.replace("**", ""))


def allowed_lines(card_):
    """Every line of prose results.html may print for this card: versus_page's lines for it (its builders and
    constants) and versus_html's own words, formatted for this card."""
    arm = VP.primary(card_)
    out = {H.TITLE.format(names=H.names_joined(card_)), " · ".join(VP.launch_parts(card_) + [
        f"{P.display(card_['tier'])} tier", VP.memory_part(card_)]), *VP.head_lines(card_)[1:],
        VP.NOT_CALIBRATED, f"{card_['tier']} tier", f"bank: {card_['identity']['bank'].get('status')}",
        H.SHOW_NUMBERS, H.SETUP, H.IDENTITY_SUMMARY, H.DIAGNOSTICS, H.FILES, VP.ROUNDING, H.PAGE_IS_VIEW,
        VP.TIE_HEAD, H.SIDE_CHECKS, VP.COUNTS_NOTE, VP.ROLES_HEAD, VP.ROLES_NOTE, VP.LEGEND, H.EVEN_NOTE,
        H.FIG_BY_CLASS, H.FIG_SETS, H.FIG_CONFLICTS, H.FIG_ALLOCATION, H.FIG_TRIAD, H.FIG_ERRAND, H.FIG_ARMS,
        VP.CONFLICTS, H.ALLOCATION_LEAD.rstrip(": "), H.ERRAND_LEAD.rstrip(": "), H.ERRAND_IS_CLASS2,
        VP.TRIAD_HEAD, VP.TRIAD_NEEDS_THREE, H.THIRD_NOTE, "First choices, each against one third",
        "What this run supports", "Not resolved, and why", "Not measured", H.COMPARATORS, VP.COMPARATOR_HEAD,
        VP.COMPARATOR_KEY, VP.CLASS_COMPARATOR_NOTE, VP.OTHER_ARMS, VP.CLASSES_HEAD, VP.CLASSES_UNREADABLE,
        VP.CLASSES_FOOT, VP.CLASS_BANK_DEVELOPMENT, VP.CLASS_TABLE_NOTE, VP.REPLACEMENT_NOTE,
        f"{H.AS_CLIENT}: the pair's first name was the client, the second deployed it; {H.AS_DEPLOYER}: the first "
        "name deployed it.", f"{VP.TIE_HEAD}: not asked in this arm.", "No bound was asked in this arm.",
        H.NO_CLASSES.format(recipe=card_.get("recipe") or card_["identity"].get("recipe"))}
    if card_.get("trust"):
        out.add(card_["trust"])
    if H.refused(card_):
        out.add(f"{VP.REFUSED_HEAD} {P.display(card_.get('refused'), cap=None)}")
        return {P.display(x, cap=None) for x in out}
    out |= set(VP.supports_lines(card_)) | set(VP.unresolved_lines(card_)) | set(VP.not_measured_lines(card_))
    out |= {f"{g}: {words}" for g, words in H.SET_LINES} | set(H.CLASS_LIMITS)
    for key in VP.CLASS_KEYS:
        title, what = VP.CLASS_LINES[key]
        out |= {"{}: {}".format(title, what), *title.split(", "), what}
    out |= {VP.validity_text(w) for w in VP.VALIDITY_ORDER}
    for a, b in run_pairs(card_):
        pair = f"{nm(card_, a)} and {nm(card_, b)}"
        rho = card_["estimate"]["arms"][arm]["metrics"].get(f"{a}:{b}/rho")
        out |= {pair, f"deployer adherence (descriptive) {VP.value(rho)}"}
        if rho and rho.get("estimate") is not None and VP.guards(rho):
            out.add(f"guard: {VP.guard_text(VP.guards(rho))}")
    if card_.get("classes") is not None and not VP.class_problem(card_):
        counts = [f"{nm(card_, p['first'])} against {nm(card_, p['second'])}: " + " · ".join(
            f"{VP.CLASS_SHORT[k]} {p[k]['valid']} / {p[k]['planned']}" for k in VP.CLASS_KEYS)
            for p in class_pairs(card_, arm)]
        out.add(f"{H.COUNTS_HEAD}: " + "; ".join(counts))
    comps = VP.comparator_lines(card_)
    out |= {unmarked(x) for x in comps if x and not x.startswith("|")}
    out |= {VP.arm_words(a) for a in VP.arms(card_)}
    others = VP.other_arms(card_)
    out |= {unmarked(x[2:]) for x in others if x.startswith("- ")}
    return {P.display(x, cap=None) for x in out}


def teaser_expected(card_):
    """The teaser's large numbers, intervals, names and states, in the names' order, from the card."""
    arm = VP.primary(card_)
    names = card_["classes"]["arms"][arm]["names"]
    out = []
    for key in VP.CLASS_KEYS:
        types = [t for t in VS.TYPES if t in card_["identity"]["participants"]]
        recs = [names[t][key] for t in types]
        if all(n["status"] != "ok" for n in recs) and len({class_text(card_, n, key) for n in recs}) == 1:
            out.append(("tz-none", class_text(card_, recs[0], key)))
            continue
        for t, n in zip(types, recs):
            if n["status"] == "ok":
                out += [("tz-big", str(r(n["value"]))), ("tz-iv", (iv(n["interval"]) if n["interval"] else
                                                                     VP.NO_INTERVAL) + (
                    f" ({n['validity']})" if key == "class3" else ""))]
            else:
                out.append(("tz-state", class_text(card_, n, key).split(":")[0]))
            out.append(("tz-name", nm(card_, t)))
    return out


# ---- the checks ------------------------------------------------------------------------------------------------------
class Checks(unittest.TestCase):
    def check_page(self, card_, label=""):
        """Everything every page must hold (the ruling's H1 to H6), for one card."""
        text = page_of(card_)
        pg = Page(text)
        self.check_safe(text, pg, label, urls_in_card=any(u in VS.card_json(card_) for u in ("http", "//")))
        self.check_prose(card_, pg, label)
        if not H.refused(card_):
            self.check_tables(card_, pg, label)
            self.check_drawing(pg, label)
            if card_.get("classes") is not None and not VP.class_problem(card_):
                self.check_teaser(card_, pg, label)
        self.assertEqual(H.render(card_), H.render(json.loads(VS.card_json(card_))), label)   # key order does not matter
        self.assertEqual(H.render(card_), H.render(card_), label)
        return text, pg

    def check_safe(self, text, pg, label, urls_in_card=False):
        """H1, H3, H5: one CSP meta element with the ruling's policy; no external reference (no http but the CSP's
        http-equiv, no //, no src, no link but a data: icon, no url( or @import; a card whose own strings hold a URL
        shows it as text, so there the attributes are checked and the text is not); two scripts, the data block
        inert; every number table open without scripts."""
        csp = [a for t, a in pg.tags if t == "meta" and a.get("http-equiv") == "Content-Security-Policy"]
        self.assertEqual([a["content"] for a in csp], [H.CSP], label)
        if not urls_in_card:
            self.assertEqual(text.count("http"), text.count("http-equiv"), label)
            self.assertNotIn("//", text, label)
        for t, a in pg.tags:
            for k, v in a.items():
                self.assertFalse(v and ("http" in v or "//" in v or "url(" in v), (label, t, k, v))
        self.assertNotIn("url(", text, label)
        self.assertNotIn("@import", text, label)
        for t, a in pg.tags:
            self.assertNotIn("src", a, (label, t))
            self.assertFalse([k for k in a if k.startswith("on")], (label, t, a))
            if "href" in a:
                self.assertEqual((t, a), ("link", {"rel": "icon", "href": "data:,"}), label)
            self.assertIn(t, ALLOWED_TAGS, label)
        self.assertEqual(len(pg.scripts), 1, label)
        self.assertEqual(pg.scripts[0], H.SCRIPT, label)
        self.assertIsNotNone(pg.data, label)
        self.assertFalse(set("<>&") & set(pg.data), label)            # nothing in it can close or open an element
        self.assertEqual(text.count("<script"), 2, label)
        self.assertEqual(text.count("</script>"), 2, label)
        json.loads(pg.data)
        for t, a in pg.tags:
            if t == "details" and "numbers" in (a.get("class") or ""):
                self.assertIn("open", a, label)                     # visible with scripts off
        self.assertFalse([ch for ch in text if ch != "\n" and P.unseen(ch)], label)

    def check_prose(self, card_, pg, label):
        """H2: every line of text that is not a table cell or a teaser number is a line of the text page for this
        card or one of the page's own words; none carries a forbidden verdict word unless it is the text page's."""
        allowed = allowed_lines(card_)
        own = {getattr(H, k) for k in dir(H) if k.isupper() and isinstance(getattr(H, k), str)
               and k not in ("STYLE", "SCRIPT", "CSP")}                  # words, not the stylesheet or the script
        for tag, cls, line, fig in pg.blocks:
            if tag in ("th", "td") or cls in ("tz-big", "tz-iv", "tz-name", "tz-state", "tz-none"):
                continue                                           # checked number by number elsewhere
            self.assertTrue(line in allowed, f"{label}: a line that is not the text page's: {tag}.{cls} {line!r}")
        for words in own | {w for _, w in H.SET_LINES} | set(H.CLASS_LIMITS):
            for word in FORBIDDEN:
                if word == "neutral" and words in (H.ALLOCATION_LEAD,):
                    continue                                       # the text page's own lead, "neutral scenario"
                self.assertNotIn(word, words.lower(), words)

    def check_tables(self, card_, pg, label):
        """H2, H3: each figure's table holds the card's numbers, cell for cell, in the page itself."""
        by_fig = {}
        for t in pg.tables:
            by_fig.setdefault(t["figure"], []).append(t)
        for fig, rows in expected_tables(card_).items():
            got = by_fig[fig][0]["rows"]
            self.assertEqual(len(got), len(rows), (label, fig))
            for want, have in zip(rows, got):
                if want is not None:
                    self.assertEqual(have, want, (label, fig))
        arm = VP.primary(card_)
        m = card_["estimate"]["arms"][arm]["metrics"]
        alloc = {row[0]: row[1] for row in by_fig["fig-allocation"][0]["rows"][1:] if not row[1].startswith("guard")}
        for a, b in run_pairs(card_):
            want = allocation_text(card_, m[f"{a}:{b}/allocation"], a, b)
            if want:
                self.assertEqual(alloc[f"{nm(card_, a)} and {nm(card_, b)}"], want, label)
        if "triad/individual" in m:
            shares = by_fig["fig-triad"][0]["rows"][1:]
            for (lab, val), t in zip(shares, ("organization", "individual", "nation")):
                want = metric_text(m[f"triad/{t}"])
                self.assertEqual(lab, f"{nm(card_, t)} ({VP.TRIAD_ROLE[t]})", label)
                if want:
                    self.assertEqual(val, want.strip(), label)
        foot = [t for t in pg.tables if t["figure"] is None]
        ties = (card_.get("ties") or {}).get(arm)
        if ties:
            tie = next(t for t in foot if t["caption"] == VP.TIE_HEAD)["rows"][1]
            pooled = ties["pooled"]
            self.assertEqual(tie, ["pooled", str(pooled.get("first_label", 0)), str(pooled.get("second_label", 0)),
                                   str(pooled.get("lottery", 0)),
                                   str(sum(v for k, v in pooled.items() if k in VS.NONCHOICES)),
                                   str(pooled.get("planned", 0))], label)
        counts = next(t for t in foot if t["rows"][0][:3] == ["arm", "frame", "planned"])["rows"][1:]
        for row in counts:
            frame = card_["counts"][arm][row[1]]
            self.assertEqual(row[2], str(frame["planned"]), label)
            for k, cell in zip(VP.OUTCOME_COLUMNS, row[3:]):
                self.assertIn(cell, (str(frame.get(k, 0)), VP.NOT_APPLICABLE), (label, k))
        ident = {row[0]: row[1] for row in foot[-1]["rows"][1:]}
        self.assertEqual(ident["bank pin (manifest sha256)"], card_["identity"]["bank"]["sha256"], label)
        self.assertEqual(ident["versus fingerprint"], card_["identity"]["versus_fingerprint"], label)
        self.assertEqual(ident["plan fingerprint (probatio)"], card_["identity"]["plan_fingerprint"], label)

    def check_drawing(self, pg, label):
        """H2: every drawn bar sits where its printed number says (within rounding), every stacked bar sums to the
        whole, and a mark that is not a number is its words, never a bar."""
        data = json.loads(pg.data)
        texts = {c for t in pg.tables for row in t["rows"] for c in row} | {b[2] for b in pg.blocks}

        def marks(node):
            if isinstance(node, dict):
                if "marks" in node:
                    yield from node["marks"]
                for v in node.values():
                    yield from marks(v)
            elif isinstance(node, list):
                for v in node:
                    yield from marks(v)
        seen = 0
        for mk in marks(data):
            seen += 1
            if "state" in mk:
                self.assertEqual(set(mk), {"state"}, label)
                continue
            if "stack" in mk:                       # boundaries on the running count: tiled from 0, ending at 100
                self.assertEqual(mk["stack"][0]["lo"], 0)
                self.assertTrue(all(s["lo"] == r["hi"] for r, s in zip(mk["stack"], mk["stack"][1:])))
                self.assertEqual(mk["stack"][-1]["hi"], 100)
                for s in mk["stack"]:
                    self.assertLessEqual(abs(s["hi"] - s["lo"] - float(re.findall(r"(\d+)%$", s["text"])[0])),
                                         0.5 + 0.01 + 1e-9)
                continue
            self.assertIn(mk["short"].rstrip("%"), re.findall(r"\d+", mk["text"]), label)
            self.assertLessEqual(abs(mk["x"] - float(mk["short"].rstrip("%"))), 0.5 + 0.01, (label, mk))
            if mk["lo"] is not None:
                lo, hi = map(float, re.findall(r"\[(\d+), (\d+)\]", mk["text"])[0])
                self.assertLessEqual(abs(mk["lo"] - lo), 0.51, label)
                self.assertLessEqual(abs(mk["hi"] - hi), 0.51, label)
            self.assertTrue(mk["text"] in texts or any(mk["text"] in t for t in texts), (label, mk["text"]))
        self.assertGreater(seen, 0, label)

    def check_teaser(self, card_, pg, label):
        got = [(cls, line) for tag, cls, line, fig in pg.blocks if cls in ("tz-big", "tz-iv", "tz-name", "tz-state",
                                                                           "tz-none")]
        self.assertEqual(got, teaser_expected(card_), label)            # in the names' order, class by class


ALLOWED_TAGS = {"html", "head", "meta", "link", "title", "style", "body", "main", "header", "section", "footer", "h1",
                "h2", "h3", "h4", "p", "div", "span", "ul", "li", "figure", "details", "summary", "table", "caption",
                "thead", "tbody", "tr", "th", "td", "b", "script"}


class Pages(Checks):
    """The page of each card of CASES: every check of check_page, and what each kind of card must show."""

    def test_every_case(self):
        for label in CASES:
            with self.subTest(label):
                self.check_page(card(label), label)

    def test_a_class_with_no_number_is_its_words_never_a_bar(self):
        c = card("some sets two quick on, unfamiliar")
        pr = next(iter(c["classes"]["arms"]["names:on"]["pairs"].values()))
        self.assertEqual(pr["groups"]["obedience"]["status"], "insufficient")
        self.assertEqual(pr["class1"]["status"], "insufficient")
        data = json.loads(Page(page_of(c)).data)
        first = data["fig-classes"]["tabs"][0]["panels"][0]["rows"][0]["marks"][0]
        self.assertEqual(set(first), {"state"})
        self.assertTrue(first["state"].startswith("insufficient"), first)
        self.assertEqual(data["fig-classes"]["tabs"][2]["panels"][0]["rows"][0]["marks"][0],
                         {"state": "not asked at the quick tier"})

    def test_arms_side_by_side_with_the_comparators(self):
        c = card("coin three standard both, unfamiliar")
        text = page_of(c)
        data = json.loads(Page(text).data)
        series = data["fig-arms"]["series"]
        self.assertEqual([s["label"] for s in series], [VP.arm_words(a) for a in VP.arms(c)])
        self.assertEqual([s["colour"] for s in series], ["accent", "orange", "charcoal-light", "charcoal"])
        self.assertEqual([t["label"] for t in data["fig-arms"]["tabs"]],
                         ["Class I", "Class II", "Class III", "role-averaged share"])
        for label in c["estimate"]["comparators"]:
            self.assertIn(H.text(VP.comparator_label(label)), text)

    def test_one_arm_has_no_arms_figure(self):
        self.assertNotIn("fig-arms", page_of(card("first_name three standard on")))

    def test_two_names_have_no_triad_figure_and_say_so(self):
        text = page_of(card("option A two quick off"))
        self.assertNotIn('id="fig-triad"', text)
        self.assertIn(H.text(VP.TRIAD_NEEDS_THREE), text)

    def test_the_errand_index_is_class_ii_said_once_and_not_drawn(self):
        text = page_of(card("first_name three standard on"))
        self.assertEqual(text.count(H.text(H.ERRAND_IS_CLASS2)), 1)
        self.assertNotIn("fig-errand", text)

    def test_a_refused_card(self):
        c = TP.card_of("deployer", n=3, development=True)
        self.assertTrue(c["refused"])
        text, pg = self.check_page(c, "refused")
        self.assertIn(H.text(f"{VP.REFUSED_HEAD} {c['refused']}"), text)
        self.assertEqual(json.loads(pg.data), {})
        for absent in ("What this run supports", VP.ROLES_HEAD, "fig-classes"):
            self.assertNotIn(absent, text)

    def test_quick_and_standard_tiers_and_their_sentences(self):
        for label in ("first_name three standard on", "option A two quick off"):
            c = card(label)
            text = page_of(c)
            for line in VP.supports_lines(c) + VP.not_measured_lines(c) + VP.unresolved_lines(c):
                self.assertIn(H.text(line), text, label)
            box = text[text.index('class="takeaway"'):text.index("Not resolved, and why")]
            for line in VP.supports_lines(c):
                self.assertIn(H.text(line), box, label)

    def test_an_unreadable_class_block_is_one_line(self):
        c = card("first_name three standard on")
        c["classes"]["schema"] = "probatio-versus-classes/0"
        text, pg = self.check_page(c, "unreadable")
        self.assertIn(H.text(VP.CLASSES_UNREADABLE), text)
        self.assertNotIn("fig-classes", text)
        self.assertNotIn(H.text(H.ERRAND_IS_CLASS2), text)
        self.assertIn('id="fig-errand"', text)                  # no Class II to stand for it: the errand index drawn


# ---- a pair record that points the other way: the page must still name the right party (finding 1 of the check) ----
# The class block reads each pair record by its own first and second, whatever its key (versus_page.class_problem), so
# a card may hold a pair toward either of its names. These cards are real cards with one record turned at a time; the
# turning below is written from the class scorer's definitions, independently of the page.
def turned_number(n):
    """A NUMBER toward the other name of its pair: 100 minus its value, its interval reflected; every other field
    (status, why, counts, Class III's validity) kept."""
    out = dict(n)
    if n["value"] is not None:
        out["value"] = 100 - n["value"]
    if n["interval"] is not None:
        out["interval"] = [100 - n["interval"][1], 100 - n["interval"][0]]
    return out


def reversed_pair(rec):
    """A class pair record written toward its other name. Every class, group and component number is a share toward
    the first name, so each turns. Protection's two parts both point toward the first name as well
    (versus_classes.protection_parts: the share that kept the first's resource when the second asked, and the share
    that gave the second's resource when the first asked), so with the names exchanged each part is 100 minus the
    other one."""
    parts = rec["protection_parts"]
    return dict(rec, first=rec["second"], second=rec["first"],
                **{k: turned_number(rec[k]) for k in VP.CLASS_KEYS},
                groups={g: turned_number(n) for g, n in rec["groups"].items()},
                components={c: turned_number(n) for c, n in rec["components"].items()},
                protection_parts={"kept_first_against_second": turned_number(parts["gave_second_to_first"]),
                                  "gave_second_to_first": turned_number(parts["kept_first_against_second"])})


def reversed_difference(rec):
    """A comparator's pair record toward its other name: a difference turns by its sign (versus_page.class_difference)."""
    def neg(n):
        if not isinstance(n, dict):
            return n
        out = dict(n)
        if n.get("value") is not None:
            out["value"] = -n["value"]
        if n.get("interval"):
            out["interval"] = [-n["interval"][1], -n["interval"][0]]
        return out
    return dict(rec, first=rec["second"], second=rec["first"], **{k: neg(rec.get(k)) for k in VP.CLASS_KEYS})


def reoriented(card_):
    """(what was turned, card) for every pair record of every arm turned in turn, every comparator pair record turned in
    turn, and every record of the block turned at once."""
    out, block = [], card_["classes"]
    for arm, rec in block["arms"].items():
        for key in rec["pairs"]:
            c = copy.deepcopy(card_)
            c["classes"]["arms"][arm]["pairs"][key] = reversed_pair(rec["pairs"][key])
            out.append((f"{arm} {key}", c))
    for label, comp in (block.get("comparators") or {}).items():
        for key in comp.get("pairs") or {}:
            c = copy.deepcopy(card_)
            c["classes"]["comparators"][label]["pairs"][key] = reversed_difference(comp["pairs"][key])
            out.append((f"comparator {label} {key}", c))
    c = copy.deepcopy(card_)
    for rec in c["classes"]["arms"].values():
        rec["pairs"] = {k: reversed_pair(p) for k, p in rec["pairs"].items()}
    for comp in (c["classes"].get("comparators") or {}).values():
        comp["pairs"] = {k: reversed_difference(p) for k, p in (comp.get("pairs") or {}).items()}
    return out + [("every record", c)]


ORIENTED_FIGURES = ("fig-classes", "fig-sets")      # the figures that name a pair as its record does, word for word


class ReorientedPairs(Checks):
    """Every figure names a pair's number toward the party its row names. A card with a pair record turned the other
    way is the same card: the text page prints it toward the record's own first name (By class, every class number),
    the comparators toward the page's first name, and the results page must print each number toward the party its
    own row or tab names: By class and Class I set by set as the text page names the record, every other figure
    (the arms side by side above all) exactly as it does for the card in its first orientation."""

    CASES = ("first_name three standard on", "coin three standard both, unfamiliar",
             "some sets two quick on, unfamiliar", "option A two quick off")

    def test_the_text_page_accepts_and_reads_a_turned_record(self):
        """The larger question first: the text page itself reads a turned card right. Its class block is accepted;
        each turned pair's line names the record's first name with the record's own number; the names' numbers,
        the comparators, the roles and the lists are the original card's, word for word."""
        for label in self.CASES:
            base = card(label)
            for what, c in reoriented(base):
                with self.subTest(label=label, turned=what):
                    self.assertIsNone(VP.class_problem(c))
                    self.assertEqual(VP.class_answer_lines(c), VP.class_answer_lines(base))
                    self.assertEqual(VP.comparator_lines(c), VP.comparator_lines(base))
                    self.assertEqual(VP.roles_part(c), VP.roles_part(base))
                    self.assertEqual([VP.supports_lines(c), VP.unresolved_lines(c), VP.not_measured_lines(c)],
                                     [VP.supports_lines(base), VP.unresolved_lines(base),
                                      VP.not_measured_lines(base)])
                    for arm in VP.arms(c):
                        lines = VP.by_class_lines(c, arm)
                        for p in class_pairs(c, arm):
                            for k in VP.CLASS_KEYS:
                                first, second = nm(c, p["first"], arm), nm(c, p["second"], arm)
                                self.assertIn(f"  {first} against {second}: {class_text(c, p[k], k, first)}", lines)

    def test_every_figure_names_the_party_its_number_points_toward(self):
        for label in self.CASES:
            base = card(label)
            before = Page(page_of(base))
            was = json.loads(before.data)
            for what, c in reoriented(base):
                with self.subTest(label=label, turned=what):
                    _, pg = self.check_page(c, f"{label} {what}")
                    now = json.loads(pg.data)
                    # every figure but the two that name a pair as its record does: its bars, whiskers, hovers and
                    # labels exactly those of the card in its first orientation (the arms side by side above all)
                    self.assertEqual({k: v for k, v in now.items() if k not in ORIENTED_FIGURES},
                                     {k: v for k, v in was.items() if k not in ORIENTED_FIGURES})
                    # every table but those two: cell for cell the first orientation's
                    self.assertEqual([t for t in pg.tables if t["figure"] not in ORIENTED_FIGURES],
                                     [t for t in before.tables if t["figure"] not in ORIENTED_FIGURES])
                    # those two: the turned card's own text, which check_page has compared cell by cell with the card
                    # (expected_tables, by the record's own first name); each bar at its record's number
                    arm = VP.primary(c)
                    for tab, key in zip(now["fig-classes"]["tabs"], VP.CLASS_KEYS):
                        for row_, p in zip(tab["panels"][0]["rows"], class_pairs(c, arm)):
                            first, second = nm(c, p["first"], arm), nm(c, p["second"], arm)
                            self.assertEqual((row_["label"], row_["sub"]),
                                             (f"{first} against {second}", [f"for {first}"]))
                            mk, n = row_["marks"][0], p[key]
                            if VP.shown(n) is not None:
                                self.assertAlmostEqual(mk["x"], round(n["value"], 2), places=6)
                                self.assertEqual(mk["text"], class_text(c, n, key, first))

    def test_the_arms_figure_reads_toward_the_row_it_labels(self):
        """The checker's case, on a real card: the arms figure's first row is labelled for the page's first name, and
        its marks are that name's numbers whichever way each arm's record points."""
        base = card("coin three standard both, unfamiliar")
        a, b = run_pairs(base)[0]
        for what, c in reoriented(base):
            with self.subTest(turned=what):
                data = json.loads(Page(page_of(c)).data)["fig-arms"]
                for tab, key in zip(data["tabs"], VP.CLASS_KEYS):
                    row_ = tab["panels"][0]["rows"][0]
                    self.assertEqual(row_["sub"], [f"for {nm(base, a)}"])
                    for arm, mk in zip(VP.arms(base), row_["marks"]):
                        n = next(p for p in class_pairs(base, arm) if (p["first"], p["second"]) == (a, b))[key]
                        if VP.shown(n) is None:
                            continue
                        self.assertAlmostEqual(mk["x"], round(n["value"], 2), places=6)
                        self.assertEqual(mk["text"], class_text(base, n, key, nm(base, a, arm)))


class Setup(unittest.TestCase):
    def test_the_set_lines_and_limits_are_classes_md_word_for_word(self):
        doc = " ".join((REPO / "docs" / "versus" / "CLASSES.md").read_text(encoding="utf-8").replace("**", "").split())
        for g, words in H.SET_LINES:
            self.assertIn(f"{g}: {words}", doc)
        for line in H.CLASS_LIMITS:
            self.assertIn(line, doc)


class Hostile(Checks):
    """H5: hostile names, model and memory path render as text; the data block cannot close its script element."""
    NAMES = {"Mera Vale": '<script>alert(1)</script>', "Oriel Works": '"><img src=x onerror=alert(1)>',
             "Selvar": "</script><!--   & </style>"}

    def test_hostile_names_render_as_text(self):
        base = card("first_name three standard on")
        text = VS.card_json(base)
        for old, new in self.NAMES.items():
            text = text.replace(old, json.dumps(new)[1:-1])
        c = json.loads(text)
        c["launch"] = dict(c.get("launch") or {}, agent="claude\x1b[2J", model='<b>m</b>"\n',
                           cli_version="2.0‮", config_dir="cfg/‮.claude\n</script>")
        page_ = page_of(c)
        pg = self.check_page(c, "hostile")[1]
        self.assertNotIn("<img", page_)
        self.assertNotIn("<b>m</b>", page_)
        self.assertNotIn("<script>alert", page_)
        self.assertEqual([t for t, _ in pg.tags].count("script"), 2)
        h1 = next(b[2] for b in pg.blocks if b[0] == "h1")
        self.assertIn("<script>alert(1)</script>", h1)                        # the name, as text
        self.assertIn('"><img src=x onerror=alert(1)>', h1)
        data = json.loads(pg.data)
        self.assertIn('"><img src=x onerror=alert(1)>', json.dumps(data, ensure_ascii=False))
        self.assertNotIn(" ", pg.data)
        self.assertNotIn("‮", page_)
        self.assertIn("\\u202e", page_)                                       # displayed, never raw

    def test_the_text_pages_hostile_names(self):
        """tests/test_versus_page.py's hostile names (a newline, a terminal escape, markup, a right-to-left override,
        10,000 characters) as the person's name: displayed (escaped, a long one cut in the middle), never raw."""
        base = VS.card_json(card("option A two quick off"))
        for label, value in TP.HOSTILE.items():
            with self.subTest(label):
                c = json.loads(base.replace("Mera Vale", json.dumps(value)[1:-1]))
                page_ = self.check_page(c, label)[0]
                if any(P.unseen(ch) for ch in value) or len(value) > P.DISPLAY_CHARS:
                    self.assertNotIn(value, page_)
                self.assertNotIn("W" * (P.DISPLAY_CHARS + 1), page_)
                self.assertNotIn("<script>alert", page_)

    def test_long_names_stay_whole_tokens(self):
        base = card("option A two quick off")
        long_ = {"Mera Vale": TP.LONG["individual"], "Oriel Works": TP.LONG["organization"]}
        text = VS.card_json(base)
        for old, new in long_.items():
            text = text.replace(old, new)
        self.check_page(json.loads(text), "long")


# ---- the run and --rescore write it (H1, H7, H8's terminal half, H10) ---------------------------------------------------
def git_tree(commit, dest):
    """The repository's files at commit, unpacked into dest (git archive); None when git or the commit is missing."""
    try:
        proc = subprocess.run(["git", "-C", str(REPO), "archive", "--format=tar", commit], capture_output=True,
                              timeout=300)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode:
        return None
    with tarfile.open(fileobj=io.BytesIO(proc.stdout)) as tar:
        tar.extractall(dest)
    return dest


RESCORE = r"""
import contextlib, io, sys
from datetime import datetime, timezone
sys.path.insert(0, sys.argv[1])
import versus_run as VR
with contextlib.redirect_stdout(io.StringIO()):
    VR.rescore(sys.argv[2], now=datetime(2026, 11, 13, 2, 0, 0, tzinfo=timezone.utc))
"""
COMMAND = ("--agent", "claude", "--versus", "individual=Mera Vale", "--versus", "organization=Oriel Works", "--tier",
           "quick", "--jobs", "16")


def tree(folder):
    """{relative path: bytes} of every file under folder (the run's own working files, its replies and episode
    records, are the same bytes in both copies, so they are compared too)."""
    return {str(p.relative_to(folder)): p.read_bytes() for p in sorted(Path(folder).rglob("*")) if p.is_file()}


class Releases(Checks):
    """A recipe @1 folder made by the previous release's own command (b83a76a's probatio.py, on the stand-in agent
    CLI, errands included), rescored by this code and by ba64fad's: this code writes results.html, opening with the
    roles and saying the class rows were not part of that run, and every other file, the replaced ones under previous/
    included, is byte for byte what ba64fad writes (H7, H10). The same for an @2 folder made by this code's command."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="versus-html-releases-"))
        cls.old = git_tree(PREVIOUS_RELEASE, cls.tmp / "old")
        cls.main = git_tree(PUBLIC_MAIN, cls.tmp / "main")
        for d in ("home", "work", "bin"):
            (cls.tmp / d).mkdir()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, True)

    def env(self, code):
        """The environment of a child: the stand-in agent of code's own tests first on PATH as claude (codex fails),
        a scratch HOME. Built here and never printed or written."""
        agent = Path(code) / "tests" / "fixtures" / "versus_agent.py"
        (self.tmp / "bin" / "claude").write_text(f'#!/bin/sh\nexec "{sys.executable}" "{agent}" "$@"\n')
        (self.tmp / "bin" / "codex").write_text('#!/bin/sh\necho "stand-in codex: no agent calls" >&2\nexit 97\n')
        for f in ("claude", "codex"):
            (self.tmp / "bin" / f).chmod(0o755)
        env = dict(os.environ, PATH=f"{self.tmp / 'bin'}{os.pathsep}{os.environ['PATH']}", HOME=str(self.tmp / "home"),
                   VERSUS_RULE="first_name")
        env.pop("CLAUDE_CONFIG_DIR", None)
        return env

    def child(self, args, code):
        proc = subprocess.run([sys.executable, *map(str, args)], capture_output=True, text=True, env=self.env(code),
                              timeout=1800, cwd=str(self.tmp))
        self.assertEqual(proc.returncode, 0, proc.stderr[-3000:])
        return proc.stdout

    def made_by(self, code, name):
        """A finished quick two-name run of code's own command in a new folder."""
        out = self.tmp / name
        self.child([Path(code) / "probatio.py", *COMMAND, "--workdir", self.tmp / "work", "--out", out], code)
        return out

    def both_rescores(self, folder):
        """(this code's rescored copy of folder, ba64fad's)."""
        mine, theirs = self.tmp / (folder.name + "-mine"), self.tmp / (folder.name + "-main")
        shutil.copytree(folder, mine)
        shutil.copytree(folder, theirs)
        self.child(["-c", RESCORE, REPO, mine], REPO)
        self.child(["-c", RESCORE, self.main, theirs], self.main)
        return mine, theirs

    def test_an_at1_folder_of_the_previous_release(self):
        if not (self.old and self.main):
            self.skipTest("no git history: the previous releases' code cannot be read")
        made = self.made_by(self.old, "at1")
        self.assertFalse((made / "results.html").exists())
        mine, theirs = self.both_rescores(made)
        a, b = tree(mine), tree(theirs)
        page_ = a.pop("results.html").decode("utf-8")
        self.assertEqual(a, b)                                   # every other file, previous/ included
        c = json.loads((mine / "scorecard.json").read_text(encoding="utf-8"))
        self.assertEqual(c["recipe"], "versus-quick@1")
        self.assertNotIn("classes", c)
        with unittest.mock.patch.dict(os.environ, {"HOME": str(self.tmp / "home")}):
            self.assertEqual(page_.encode("utf-8"), H.render(c))
        note = H.text(H.NO_CLASSES.format(recipe="versus-quick@1"))
        self.assertIn(note, page_)
        self.assertLess(page_.index(note), page_.index(H.text(VP.ROLES_HEAD)))
        self.assertNotIn("fig-classes", page_)
        self.assertIn('id="fig-errand"', page_)
        self.check_page(c, "@1")

    def test_an_at2_folder_of_this_code(self):
        if not self.main:
            self.skipTest("no git history: ba64fad's code cannot be read")
        made = self.made_by(REPO, "at2")
        self.assertTrue((made / "results.html").is_file())
        mine, theirs = self.both_rescores(made)
        a, b = tree(mine), tree(theirs)
        self.assertIn("previous/20261113T020000Z/results.html", a)   # the run's own page, kept by the rescore
        self.assertNotEqual(a["results.html"], b["results.html"])    # ba64fad leaves the old page; this code renews it
        del a["results.html"], a["previous/20261113T020000Z/results.html"], b["results.html"]
        self.assertEqual(a, b)


# ---- the form: its offer and its one route (H8) -----------------------------------------------------------------------
class ListedRun:
    """A run the form launched, as Session.results_page reads it: its folders, ended, each folder's identity recorded
    as the form records it when a run lists a folder (ui.folder_identity). No process."""

    def __init__(self, folders):
        import ui as U
        self.folders, self.code = [str(f) for f in folders], 0
        self.identities = {f: U.folder_identity(f) for f in self.folders}

    def identity(self, folder):
        return self.identities.get(folder)

    def stop(self):
        return False

    def state(self):
        return {"status": "finished", "folders": self.folders, "results": [], "lines": [], "code": 0}


class FormPage(unittest.TestCase):
    """GET /results.html?t=TOKEN&folder=F on the form's own server (ui.serve): the results page of a folder the form's
    run lists, with the page's CSP as a header and Cache-Control: no-store; refused for a missing or wrong token (403),
    and for a traversal, an absolute path, a folder the form does not list, a results.html that is a link out of the
    folder or has a second name (a hard link), a folder that is a link or was moved into the run's place, a page that
    is not a regular file, a second folder, no run, a run's folder that cannot be resolved at that moment (404, never
    a connection closed unanswered); POST is 405."""

    def setUp(self):
        import test_ui as TU
        import ui as U
        self.U = U
        self.tmp = Path(tempfile.mkdtemp(prefix="versus-html-form-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.run_dir = self.tmp / "results" / "versus-run"
        self.run_dir.mkdir(parents=True)
        self.page = page_of(card("option A two quick off")).encode("utf-8")
        (self.run_dir / "results.html").write_bytes(self.page)
        self.other = self.tmp / "results" / "another-run"            # a real folder with a page, not the form's
        self.other.mkdir()
        (self.other / "results.html").write_bytes(b"<p>another run</p>")
        (self.tmp / "secret.html").write_bytes(b"<p>outside the results folder</p>")
        self.session = U.Session(program=[sys.executable, "-c", "raise SystemExit(3)"])
        self.served = TU.Served(self.session)
        self.addCleanup(self.served.server.shutdown)
        self.addCleanup(self.served.server.server_close)
        self.listed(self.run_dir)

    def listed(self, *folders):
        with self.session.lock:
            self.session.runner = ListedRun(folders)

    def get(self, folder=None, token=True, extra=""):
        from urllib.parse import quote
        path = "/results.html" + ("?folder=" + quote(folder, safe="") if folder is not None else "") + extra
        return self.served.request("GET", path, token=token, header=False)

    def shown(self, folder):
        return self.U.shown(folder)

    def test_the_page_of_a_listed_folder_with_its_headers(self):
        res, text = self.get(self.shown(self.run_dir))
        self.assertEqual(res.status, 200, text)
        self.assertEqual(text.encode("utf-8"), self.page)
        self.assertEqual(res.getheader("Content-Type"), "text/html; charset=utf-8")
        self.assertEqual(res.getheader("Content-Security-Policy"), H.CSP)
        self.assertEqual(res.getheader("Cache-Control"), "no-store")
        self.assertEqual(res.getheader("X-Content-Type-Options"), "nosniff")
        self.assertEqual(self.U.RESULTS_PAGE_CSP, H.CSP)                   # the form's copy is the page's own policy
        self.assertEqual(self.U.RESULTS_PAGE, VP.HTML_FILE)
        state, _ = self.served.request("GET", "/state", header=False)
        self.assertNotEqual(state.getheader("Content-Security-Policy"), H.CSP)   # the form keeps its own policy

    def test_a_missing_or_wrong_token(self):
        for token in (None, "wrong", self.served.token + "x"):
            with self.subTest(token=token):
                res, _ = self.get(self.shown(self.run_dir), token=token)
                self.assertEqual(res.status, 403)

    def test_paths_from_the_request_are_refused(self):
        listed = self.shown(self.run_dir)
        for folder in (listed + "/..", listed + "/../another-run", "../" + listed, str(self.other), str(self.tmp),
                       "/etc", str(self.run_dir / ".." / "another-run"), self.shown(self.other), "", listed + "/",
                       listed + "\x00", "results.html", listed + "/results.html"):
            with self.subTest(folder=folder):
                res, text = self.get(folder)
                self.assertEqual(res.status, 404, text)
                self.assertNotIn("outside the results folder", text)
                self.assertNotIn("another run", text)

    def test_no_folder_two_folders_or_no_run(self):
        self.assertEqual(self.get()[0].status, 404)
        listed = self.shown(self.run_dir)
        from urllib.parse import quote
        self.assertEqual(self.get(listed, extra="&folder=" + quote(listed, safe=""))[0].status, 404)
        with self.session.lock:
            self.session.runner = None
        self.assertEqual(self.get(listed)[0].status, 404)

    def test_a_link_out_of_the_folder_is_refused(self):
        (self.run_dir / "results.html").unlink()
        (self.run_dir / "results.html").symlink_to(self.tmp / "secret.html")
        res, text = self.get(self.shown(self.run_dir))
        self.assertEqual(res.status, 404)
        self.assertNotIn("outside the results folder", text)

    def test_a_page_with_a_second_name_is_refused(self):
        """results.html made as a hard link to a file outside the results folder is a regular file, opened without
        following a link, with two names, where a page probatio wrote has one: 404, and none of that file's bytes
        are sent, also when its second name is taken away between the open and the check (the race, made certain).
        The run's own page given a second name elsewhere is refused too, and served once that name is gone."""
        page, secret = self.run_dir / "results.html", self.tmp / "secret.html"
        page.unlink()
        try:
            os.link(secret, page)
        except OSError as e:
            self.skipTest(f"this file system gives no file a second name ({e.strerror})")
        res, text = self.get(self.shown(self.run_dir))
        self.assertEqual(res.status, 404, text)
        self.assertNotIn("outside the results folder", text)
        identity = self.U.folder_identity(str(self.run_dir))
        self.assertIsNone(self.U.read_results_page(str(self.run_dir), identity))
        real_open = os.open
        for after in ("no name", "the run's own page"):
            with self.subTest(second_name_taken_away=after):
                def racing_open(path, flags, *args, **kwargs):
                    fd = real_open(path, flags, *args, **kwargs)
                    if path == self.U.RESULTS_PAGE:            # opened with two names, one taken away at once
                        page.unlink()
                        if after == "the run's own page":
                            page.write_bytes(self.page)
                    return fd
                try:
                    with unittest.mock.patch.object(self.U.os, "open", racing_open):
                        self.assertIsNone(self.U.read_results_page(str(self.run_dir), identity))
                finally:                                       # the second name back for the next case
                    if page.exists():
                        page.unlink()
                    os.link(secret, page)
        page.unlink()
        page.write_bytes(self.page)
        os.link(page, self.tmp / "second-name.html")
        self.assertEqual(self.get(self.shown(self.run_dir))[0].status, 404)
        (self.tmp / "second-name.html").unlink()
        res, text = self.get(self.shown(self.run_dir))
        self.assertEqual((res.status, text.encode("utf-8")), (200, self.page))

    def test_a_listed_folder_that_is_a_link_is_refused(self):
        link = self.tmp / "results" / "linked-run"
        link.symlink_to(self.other, target_is_directory=True)
        self.listed(link)
        res, text = self.get(self.shown(link))
        self.assertEqual(res.status, 404)
        self.assertNotIn("another run", text)

    def test_a_page_that_is_not_a_regular_file_or_is_missing(self):
        (self.run_dir / "results.html").unlink()
        self.assertEqual(self.get(self.shown(self.run_dir))[0].status, 404)       # missing
        (self.run_dir / "results.html").mkdir()
        self.assertEqual(self.get(self.shown(self.run_dir))[0].status, 404)       # a folder
        (self.run_dir / "results.html").rmdir()
        os.mkfifo(self.run_dir / "results.html")
        self.assertEqual(self.get(self.shown(self.run_dir))[0].status, 404)       # a pipe, never waited on

    def swap(self, replacement):
        """Put replacement where the listed folder was, after the form recorded it: the folder moved aside and
        replacement (a link to another run's folder, or another real folder) under its name."""
        os.rename(self.run_dir, self.tmp / "results" / "moved-aside")
        if replacement == "link":
            self.run_dir.symlink_to(self.other, target_is_directory=True)
        else:
            os.rename(self.other, self.run_dir)

    def test_a_folder_swapped_for_another_folder_after_listing_is_refused(self):
        """The form serves the folder its run listed, by its device and inode: a link or another real folder moved
        into its place after listing is refused."""
        for replacement in ("link", "folder"):
            with self.subTest(replacement=replacement):
                self.setUp()
                self.swap(replacement)
                res, text = self.get(self.shown(self.run_dir))
                self.assertEqual(res.status, 404, text)
                self.assertNotIn("another run", text)

    def test_a_swap_between_the_check_and_the_open_is_refused(self):
        """The race itself, made certain: the folder is swapped at the moment the route makes its first open, and,
        apart, its second. Swapped before the folder is pinned, the route refuses; swapped after, it reads the folder
        it pinned, the run's own page."""
        real_open = os.open
        for at in (1, 2):
            for replacement in ("link", "folder"):
                with self.subTest(at=at, replacement=replacement):
                    self.setUp()
                    calls = []

                    def racing_open(path, flags, *args, **kwargs):
                        calls.append(path)
                        if len(calls) == at:
                            self.swap(replacement)
                        return real_open(path, flags, *args, **kwargs)
                    folder = str(self.run_dir)
                    identity = self.U.folder_identity(folder)
                    with unittest.mock.patch.object(self.U.os, "open", racing_open):
                        body = self.U.read_results_page(folder, identity)
                    if at == 1:
                        self.assertIsNone(body)                        # the swapped folder is not the one recorded
                    else:
                        self.assertEqual(len(calls), 2)                # the folder pinned, then the page opened in it
                        self.assertEqual(body, self.page)              # the pinned folder's page, never another's

    def test_a_folder_with_no_recorded_identity_is_refused(self):
        self.assertIsNone(self.U.read_results_page(str(self.run_dir), None))
        self.assertIsNotNone(self.U.read_results_page(str(self.run_dir), self.U.folder_identity(str(self.run_dir))))
        self.assertIsNone(self.U.folder_identity(str(self.tmp / "results" / "not-there")))
        link = self.tmp / "results" / "linked-run"
        link.symlink_to(self.other, target_is_directory=True)
        self.assertIsNone(self.U.folder_identity(str(link)))            # a link has no identity a page is read from

    def answer(self, path, folder=None):
        """(status, text) of GET path (the results page of folder, when given), or ("closed", "") when the server
        closed the connection without an HTTP answer."""
        import http.client
        try:
            res, text = self.get(folder) if folder is not None else self.served.request("GET", path, header=False)
        except (http.client.HTTPException, ConnectionError):
            return "closed", ""
        return res.status, text

    def resolving_fails(self, code):
        """Path.resolve raising OSError(code) for the run's folder alone, as the readlink behind it does when the
        folder is swapped for a link between its lstat and its readlink (EINVAL: a folder again; ENOENT: gone)."""
        real = Path.resolve

        def failing(path, *args, **kwargs):
            if str(path) == str(self.run_dir):
                raise OSError(code, os.strerror(code), str(path))
            return real(path, *args, **kwargs)
        return unittest.mock.patch.object(Path, "resolve", failing)

    def loop(self):
        """The listed folder moved aside and a link to itself under its name: no lookup of it ever ends."""
        os.rename(self.run_dir, self.tmp / "results" / "moved-aside")
        self.run_dir.symlink_to(self.run_dir)

    def test_a_folder_that_cannot_be_resolved_is_refused_not_dropped(self):
        """The route resolves its run's folders to match the request against them. A folder that cannot be resolved
        at that moment (an OSError from the lookup, or a link loop) is refused with the ordinary 404, never with the
        connection closed unanswered."""
        listed = self.shown(self.run_dir)
        for code in (errno.EINVAL, errno.ENOENT):
            with self.subTest(errno=errno.errorcode[code]):
                with self.resolving_fails(code):
                    status, text = self.answer("/results.html", listed)
                self.assertEqual(status, 404, text)
                self.assertEqual(self.answer("/results.html", listed)[0], 200)     # resolved again: its own page
        self.loop()
        status, text = self.answer("/results.html", listed)
        self.assertEqual(status, 404, text)
        self.assertNotIn("another run", text)

    def test_the_state_shows_a_folder_that_cannot_be_resolved_as_given(self):
        """/state names the run's folders (shown); a folder that cannot be resolved at that moment is shown as given,
        and the form still answers."""
        runner = self.U.Runner([sys.executable, "-c", "pass"], [], [self.run_dir], dict(os.environ))
        runner.wait(60)
        with self.session.lock:
            self.session.runner = runner
        status, text = self.answer("/state")
        self.assertEqual(status, 200, text)
        folders = json.loads(text)["run"]["folders"]
        for code in (errno.EINVAL, errno.ENOENT):
            with self.subTest(errno=errno.errorcode[code]):
                with self.resolving_fails(code):
                    status, text = self.answer("/state")
                self.assertEqual(status, 200, text)
                self.assertEqual(json.loads(text)["run"]["folders"], folders)
        self.loop()
        status, text = self.answer("/state")
        self.assertEqual(status, 200, text)
        self.assertEqual(json.loads(text)["run"]["folders"], folders)

    SWAPS = r"""
import os, sys
run, aside, other, stop = sys.argv[1:5]
swaps = 0
try:
    while not os.path.exists(stop):
        os.rename(run, aside)
        os.symlink(other, run, target_is_directory=True)
        os.unlink(run)
        os.rename(aside, run)
        swaps += 1
finally:
    if os.path.islink(run):
        os.unlink(run)
    if os.path.isdir(aside) and not os.path.lexists(run):
        os.rename(aside, run)
print(swaps)
"""
    RACE_REQUESTS = 1500

    def test_a_folder_swapped_for_a_link_while_it_is_requested_is_answered_every_time(self):
        """Another process swaps the listed folder for a link to another run's folder and back, as fast as it can,
        while the page is requested: every request is answered, 200 with the run's own page or 404, none closed
        unanswered, and no other folder's page is ever served."""
        stop = self.tmp / "stop-swapping"
        child = subprocess.Popen([sys.executable, "-c", self.SWAPS, str(self.run_dir),
                                  str(self.tmp / "results" / "moved-aside"), str(self.other), str(stop)],
                                 stdout=subprocess.PIPE, text=True)

        def end():
            stop.touch()
            return child.communicate(timeout=60)[0]
        self.addCleanup(lambda: child.poll() is not None or end())
        identity = self.U.folder_identity(str(self.run_dir))
        listed, answers = self.shown(self.run_dir), {}
        for _ in range(self.RACE_REQUESTS):
            status, text = self.answer("/results.html", listed)
            answers[status] = answers.get(status, 0) + 1
            if status == 200:
                self.assertEqual(text.encode("utf-8"), self.page)
            self.assertNotIn("another run", text)
        swaps = int(end())
        self.assertGreater(swaps, 0)
        self.assertEqual(sorted(answers, key=str), sorted(set(answers) & {200, 404}, key=str), answers)
        self.assertEqual(self.U.folder_identity(str(self.run_dir)), identity)        # the run's folder, put back
        self.assertEqual(self.answer("/results.html", listed)[0], 200)

    def test_post_is_refused(self):
        res, _ = self.served.request("POST", "/results.html?folder=x", body={})
        self.assertEqual(res.status, 405)
        self.assertEqual(res.getheader("Allow"), "GET")

    def test_the_form_offers_the_page_once_the_run_has_ended(self):
        script = self.U.PAGE
        self.assertIn('const a = el("a", "pagelink", "Open the results page");', script)
        self.assertIn('a.href = "/results.html?" + new URLSearchParams({t: TOKEN, folder: f}).toString();', script)
        self.assertIn('if ((run.status === "finished" || run.status === "scored") && run.results.some(([k]) => k === '
                      '"Page"))', script)
        self.assertTrue(self.U.VERSUS_RESULT_LINE.match("Page: /x/results.html"))
        self.assertTrue(self.U.VERSUS_RESULT_LINE.match("Results: /x"))
        self.assertTrue(self.U.RESULT_LINE.match("Page: /x/results.html"))          # a v4 run's page too (step 2)


class RunFiles(unittest.TestCase):
    """The run's one writer: page_files adds results.html to versus_page.files' own files, whose bytes are unchanged;
    the answer lines end with the folder and then the page."""

    def test_page_files_add_the_page_and_change_nothing_else(self):
        c = card("option A two quick off")
        files = VR.page_files(c, [])
        self.assertEqual(files.pop(VP.HTML_FILE), H.render(c))
        self.assertEqual(files, {k: VR.export_bytes(k, v) for k, v in VP.files(c, []).items()})

    def test_the_page_line_follows_the_results_line(self):
        c = card("option A two quick off")
        out = TMP / "some run folder"
        lines = VR.answer_lines(c, out)
        self.assertEqual(lines[-2:], [f"Results: {P.display(out)}", f"Page: {P.display(out / 'results.html')}"])

    def test_a_page_module_without_the_results_page_writes_none_and_prints_no_line(self):
        import versus_page_standin
        c = card("option A two quick off")
        with unittest.mock.patch.dict(sys.modules, {"versus_page": versus_page_standin}):
            self.assertNotIn(VP.HTML_FILE, VR.page_files(c, []))
            self.assertFalse(VR.answer_lines(c, TMP)[-1].startswith("Page:"))


if __name__ == "__main__":
    unittest.main()
