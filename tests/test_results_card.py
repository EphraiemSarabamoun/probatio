"""Tests for the results card (the results-card ruling, C1 to C9, and its design, D1 to D15): the one card the --watch
page and the form show for a run, its markup (live.run_card and its parts: a tree of plain nodes), its style
(live.CARD_STYLE, made from results_kit's own tokens, which naming them did not move: the results pages' stylesheet is
byte for byte what it was) and its words (live.CARD_WORDS). Every state, in the markup: no card while a run goes on
the --watch page, or after a refusal or an error; on the form, the run card while it goes, with the live link as its
secondary button; the card with its one button once the page can be served; the card with no button, and the
sentence of why, when it cannot. The card's title is the results page's own, by the page's own function; its text
holds 4.5:1 against its fill in both schemes. On the --watch page (VIEW_LIVE), for each kind of run:
the card first under the title row once the run is done, its button's address the page's own route under its own
token, the route serving the run's page byte for byte, the keyboard reaching the button first, the command's other
runs each with its own link, and the bar for a reader further down, shown only while the card is out of view, which
moves nothing and hides no opened entry's first line; on the form (ui.PAGE), the run card opening the run's box in
one frame through the run's states (the live link as its secondary button while it goes, the results button once
done, why when there is no page); in the suite's headless Chrome where one is on PATH. Planted
folders only (the suite's own planters and stand-in agent); no model, no network beyond 127.0.0.1.
    python3 -m unittest tests.test_results_card
"""
import ast
import contextlib
import copy
import hashlib
import html
import io
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import quote

REPO = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(REPO), str(REPO / "tests"), str(REPO / "tests" / "fixtures")]

import feed as F  # noqa: E402
import live as L  # noqa: E402
import probatio as P  # noqa: E402
import results_kit as K  # noqa: E402
import test_live_feed as TLF  # noqa: E402  (its cross_plan and request client)
import test_rescore as TR  # noqa: E402
import test_ui as TU  # noqa: E402  (its offline headless Chrome and wait_for)
import test_versus_watch as TVW  # noqa: E402
import test_watch_v4 as TW  # noqa: E402
import ui as U  # noqa: E402
import versus as VS  # noqa: E402
import versus_run as VR  # noqa: E402

# The results pages' stylesheet as d9b1b93 (next-release 72beb06) has it, before its tokens were named: its sha256,
# and that of the whole inline stylesheet (style(): the custom properties, then STYLE) and of the base script.
STYLE_SHA256 = "05c4c7e8e0c790cc086e7c6ace098160d5191f3b43f3e9848e7715d58267fcff"
FULL_STYLE_SHA256 = "d196f36a418d0ce04213a47d19196fa2b51f650760c6a3f4118ca312305b63de"
SCRIPT_SHA256 = "d7b4829f9485b2a958fb815dd33cb9a1e8896bfa53d6bedea9dfa6a2dd195a96"
SINGLE_FACTS = {"agent": "Claude Code", "cli": "2.1.309", "model": "sonnet", "effort": "medium", "tier": "standard",
                "recipe": None, "memory": "on", "kind": "single-type", "target": "Corvin Hale",
                "target_type": "individual", "control": "Ada Brightwater"}
CROSS_FACTS = {"agent": "Codex", "cli": "0.159.2", "model": "", "effort": "high", "tier": "quick", "recipe": None,
               "memory": "both", "kind": "cross-type", "compare": "individual:organization",
               "types": ["individual", "organization"]}
VERSUS_FACTS = {"agent": "Claude Code", "cli": "2.1.309", "model": "sonnet", "effort": "medium", "tier": "standard",
                "memory": "on", "strangers": "on", "names": "Corvin Hale and Lantern Foundry", "bank": "",
                "title": "Head-to-head: Corvin Hale and Lantern Foundry"}      # handed over by versus_run.card_title
FOLDER = "/runs/v4-claude-individual-corvin-hale"


def walk(node):
    """Every node of a card's tree, in document order (the node first, then its children)."""
    if isinstance(node, dict):
        yield node
        for kid in node["kids"]:
            yield from walk(kid)


def texts(node):
    """Every string of a tree, in document order."""
    if isinstance(node, str):
        yield node
    elif node:
        for kid in node["kids"]:
            yield from texts(kid)


def spoken(node):
    """The tree's text as a screen reader has it: every string but those of nodes hidden from it (deco)."""
    if isinstance(node, str):
        return node
    if not node or node.get("deco"):
        return ""
    return "".join(spoken(k) for k in node["kids"])


def links(tree):
    return [n for n in walk(tree) if n["tag"] == "a"]


def card_links(tree):
    return [n.get("link") for n in links(tree)] if tree else []


def classes(node):
    return (node.get("cls") or "").split()


def having(tree, cls):
    return [n for n in walk(tree) if cls in classes(n)]


def page_h1(path):
    """The text of a results page's h1, unescaped."""
    m = re.search(r"<h1>(.*?)</h1>", Path(path).read_text(encoding="utf-8"), re.S)
    return html.unescape(m.group(1))


class TestKitTokens(unittest.TestCase):
    """C3, C8: the card reads the kit's own tokens, and naming them changed no byte of the results pages' style."""

    def test_the_kits_stylesheet_is_byte_for_byte_what_it_was(self):
        self.assertEqual(hashlib.sha256(K.STYLE.encode("utf-8")).hexdigest(), STYLE_SHA256)
        self.assertEqual(hashlib.sha256(K.style().encode("utf-8")).hexdigest(), FULL_STYLE_SHA256)
        self.assertEqual(hashlib.sha256(K.SCRIPT.encode("utf-8")).hexdigest(), SCRIPT_SHA256)

    def test_each_token_is_the_value_the_stylesheet_writes_where_it_is_used(self):
        for rule in (f"html{{font-size:{K.ROOT};", f"line-height:{K.LEADING};overflow-x:hidden",
                     f"h2{{margin:2.4rem 0 .8rem;padding-bottom:.45rem;font-size:{K.TYPE['h2']};"
                     f"font-weight:{K.TYPE['h2_weight']};line-height:{K.TYPE['h2_leading']};",
                     f".fine{{font-size:{K.TYPE['fine']};", f".head .runline{{margin:0 0 .3rem;font-size:"
                     f"{K.TYPE['runline']};", f".sl .fig-card{{position:relative;padding:{K.SPACE['card']};",
                     f"padding:{K.SPACE['box']} 4px;background:var(--hl);border:{K.SPACE['box_border']} solid "
                     "var(--accent);", f"border-radius:{K.SPACE['radius']}}}",
                     f".sl .fig-setup > summary{{padding:{K.BUTTON['pad']};font-size:{K.BUTTON['size']};"
                     f"font-weight:{K.BUTTON['weight']};", f"border:{K.BUTTON['border']} solid var(--accent)}}"):
            self.assertIn(rule, K.STYLE)
        self.assertIn(f"--mono:{K.MONO};", K.style())

    def test_every_colour_of_the_card_is_one_of_the_kits(self):
        """Every colour value is a kit colour by role; the only others are the design's shadows (D2) and the bar's
        paper and rule, which are the page's own surface and grid colour (D10), opaque (E4)."""
        css = L.CARD_STYLE
        found = set(re.findall(r"#[0-9a-fA-F]{3,8}\b", css))
        self.assertTrue(found)
        self.assertLessEqual(found, set(K.COLOURS.values()))
        for decl in re.findall(r"[a-z-]+:[^;{}]*rgba\([^;{}]*", css):
            self.assertTrue(decl.startswith("box-shadow:"), decl)
        self.assertEqual(set(re.findall(r"var\(--([a-z-]+)\)", css)) - {f"rc-{r}" for r in L.CARD_ROLES},
                         {"surface", "grid", "ink", "muted"})                  # the bar's: the page's own tokens
        for role, (light, dark) in L.CARD_ROLES.items():
            self.assertIn(f"--rc-{role}:{K.COLOURS[light]};", L.card_roles(L.CARD_ROLES, 0))
            self.assertIn(f"--rc-{role}:{K.COLOURS[dark]};", L.card_roles(L.CARD_ROLES, 1))

    def test_the_card_speaks_the_kits_type_and_button(self):
        css = L.CARD_STYLE
        self.assertIn(f"font-family:{K.FONT};", css)
        self.assertIn(f"font-family:{K.MONO};", css)
        self.assertIn(f"font-size:{K.ROOT};line-height:{K.LEADING};", css)
        self.assertIn(f"font-size:{K.TYPE['runline'].replace('rem', 'em')};", css)
        self.assertIn(f"font-size:{K.TYPE['fine'].replace('rem', 'em')};", css)
        self.assertIn(f"border:{K.BUTTON['border']} solid var(--rc-btn)", css)
        self.assertIn("@media (prefers-color-scheme: dark)", css)
        self.assertIn("@media (prefers-reduced-motion: reduce)", css)
        self.assertNotIn("url(", css)

    def test_the_bar_is_opaque(self):
        """E4: the bar's background is the page's own paper, fully opaque, with its one 1px rule beneath; no blur, no
        transparency, in either scheme (the page's surface token is opaque in both)."""
        bar = re.search(r"\.rk\.rc-bar\{[^}]*\}", L.CARD_STYLE).group(0)
        self.assertIn("background:var(--surface);", bar)
        self.assertIn("border-bottom:1px solid var(--grid);", bar)
        for gone in ("backdrop-filter", "color-mix", "transparent", "opacity", "rgba"):
            self.assertNotIn(gone, bar)
        for scheme in re.findall(r"--surface:(#[0-9a-f]{6})", L.PAGE_BASE):
            self.assertRegex(scheme, r"^#[0-9a-f]{6}$")                             # six digits: no alpha

    def test_one_finish_the_sheet(self):
        """E1: the sheet alone (D3's A): its frame, its shadow and the accent's rule along its top, on the card itself;
        nothing names another finish."""
        css = L.CARD_STYLE
        self.assertIn(f"border:1px solid var(--rc-frame);box-shadow:{L.SHADOW[0]};", css)
        self.assertIn(".rc::before{content:\"\";position:absolute;top:0;left:0;right:0;height:3px;"
                      "background:var(--rc-accent);}", css)
        for gone in ("highlight", "rc-sheet", "finish"):
            self.assertNotIn(gone, css)
            self.assertNotIn(gone, L.CARD_SCRIPT)
        for name in ("CARD_FINISHES", "CARD_FINISH", "HIGHLIGHT_ROLES", "SHADOWS"):
            self.assertFalse(hasattr(L, name), name)
        self.assertEqual(classes(L.run_card("ready", SINGLE_FACTS, 60, FOLDER, link={"run": 0})),
                         ["rk", "rc", "rc-ready"])


class TestCardMarkup(unittest.TestCase):
    """D1, D5 to D7, D11, D12, C4: the card's tree in each state, the button first after the title, every string as
    text."""

    def card(self, state="ready", **kw):
        args = dict(done=True, facts=SINGLE_FACTS, took=754.2, state=state, link={"run": 0}, folder=FOLDER,
                    files=["scorecard.md", "report.md", "results.csv"])
        args.update(kw)
        return L.card_for(**args)

    def test_no_card_while_a_run_goes_or_after_a_refusal_or_an_error(self):
        for state in ("ready", "missing", "refused"):
            self.assertIsNone(self.card(state, done=False))

    def test_a_done_run_whose_page_can_be_served_has_the_card_and_its_one_button(self):
        tree = self.card()
        self.assertEqual((tree["tag"], classes(tree), tree["aria"]),
                         ("section", ["rk", "rc", "rc-ready"], "Run finished"))
        (label,) = having(tree, "rc-label")
        self.assertEqual(spoken(label), "Run finished")
        self.assertEqual([n.get("deco") for n in having(label, "rc-disc")], [True])
        (title,) = having(tree, "rc-title")
        self.assertEqual((title["tag"], title["kids"], title.get("focus")), ("h2", ["Scorecard: Corvin Hale "
                                                                                     "(individual)"], True))
        self.assertEqual(having(tree, "rc-facts")[0]["kids"], ["Claude Code sonnet · standard tier · memory on · took "
                                                               "12m 34s"])
        (a,) = links(tree)
        self.assertEqual((a["link"], classes(a)), ({"run": 0}, ["rc-btn", "rc-open"]))
        self.assertEqual(spoken(a), "Open the results page (opens in a new tab)")
        self.assertEqual(list(texts(a)), ["Open the results page", L.ARROW, " (opens in a new tab)"])
        self.assertEqual(L.ARROW, "\u2192")                 # its thin space is the arrow's margin, not a character
        self.assertIn(".rk .rc-arrow{display:inline-block;margin-left:.17em;", L.CARD_STYLE)
        order = list(walk(tree))
        self.assertLess(order.index(title), order.index(a))
        self.assertFalse([n for n in order[order.index(title) + 1:order.index(a)] if n["tag"] in ("a", "button")])
        (saved,) = having(tree, "rc-saved")
        self.assertEqual([t for t in texts(saved)], ["Saved in", " ", FOLDER])
        self.assertGreater(order.index(saved), order.index(a))                 # quietly, under the button
        self.assertEqual(having(tree, "rc-files")[0]["kids"], ["scorecard.md · report.md · results.csv"])
        self.assertFalse(having(tree, "rc-why"))

    def test_a_done_run_whose_page_cannot_be_served_says_why_and_has_no_button(self):
        for state in ("missing", "refused"):
            tree = self.card(state)
            self.assertEqual(links(tree), [], state)
            self.assertFalse([n for n in walk(tree) if n["tag"] == "button"], state)
            self.assertEqual([w["kids"] for w in having(tree, "rc-why")], [[L.CARD_WORDS["why"][state]]], state)
            self.assertIn(FOLDER, list(texts(tree)), state)
            self.assertIn("rc-nopage", classes(tree))
        self.assertEqual(L.CARD_WORDS["why"], {"missing": "There is no results page in this run's folder.",
                                               "refused": "The results page could not be read from the run's folder."})
        self.assertNotIn("report.md and no results page", json.dumps(L.CARD_WORDS))   # E7: no state nothing draws

    def test_on_the_form_the_run_card_holds_each_state_in_one_frame(self):
        running = L.run_card("running", SINGLE_FACTS, None, FOLDER, live=True)
        self.assertEqual(classes(running)[-1], "rc-running")
        self.assertEqual(spoken(having(running, "rc-label")[0]), "Running")
        (b,) = links(running)
        self.assertEqual((b.get("live"), classes(b), spoken(b)), (True, ["rc-btn", "rc-second"],
                                                                   "Watch the run live (opens in a new tab)"))
        self.assertEqual(links(L.run_card("running", SINGLE_FACTS, None, FOLDER)), [])     # watch off: no link
        held = self.card(link={"folder": FOLDER}, watch_page=True)
        a, quiet = links(held)
        self.assertEqual((a["link"], classes(quiet), quiet.get("live")), ({"folder": FOLDER}, ["rc-quiet"], True))
        self.assertEqual(spoken(quiet), "Watch page (opens in a new tab)")
        self.assertIn(quiet, list(walk(having(held, "rc-saved")[0])))          # beside the folder line
        for tree in (running, held):
            self.assertEqual(having(tree, "rc-title")[0]["kids"], ["Scorecard: Corvin Hale (individual)"])

    def test_the_others_of_a_command_are_listed_each_with_its_own_link(self):
        others = [L.other_run(CROSS_FACTS, 61, "finished", {"run": 1}),
                  L.other_run(VERSUS_FACTS, None, "running"),
                  L.other_run({"target": "Norland"}, None, "waiting")]
        tree = self.card(others=others)
        (box,) = having(tree, "rc-others")
        items = having(box, "rc-other")
        self.assertEqual([[t for t in texts(i) if t.strip()] for i in items],
                         [["Types compared: individual and organization", "Codex · quick tier · memory both · took "
                           "1m 01s", "Open", L.ARROW, " (opens in a new tab)"],
                          ["Head-to-head: Corvin Hale and Lantern Foundry", "Claude Code sonnet · standard tier · "
                           "memory on", "running"], ["Norland", "waiting"]])
        self.assertEqual(card_links(tree), [{"run": 0}, {"run": 1}])
        self.assertEqual(links(tree)[1]["aria"], "Open: Types compared: individual and organization")
        self.assertGreater(list(walk(tree)).index(box), list(walk(tree)).index(having(tree, "rc-foot")[0]))
        self.assertEqual(L.other_words("finished", "missing"), "finished · no results page")
        self.assertEqual(L.other_words("running", None), "running")

    def test_the_bar_has_its_words_its_button_and_dismiss(self):
        bar = L.results_bar({"run": 2})
        self.assertEqual(classes(bar), ["rk", "rc-bar"])
        self.assertEqual(spoken(bar), "Results are readyOpen the results page (opens in a new tab)Dismiss")
        self.assertEqual(card_links(bar), [{"run": 2}])
        self.assertEqual(classes(links(bar)[0]), ["rc-btn", "rc-open", "rc-compact"])
        (b,) = [n for n in walk(bar) if n["tag"] == "button"]
        self.assertEqual((b["act"], spoken(b)), ("dismiss", "Dismiss"))
        self.assertEqual(links(L.results_bar(None)), [])

    def test_the_tree_is_plain_data_every_string_text(self):
        tree = self.card(others=[L.other_run({"target": "<b>x</b>"}, None, "finished", {"run": 1})],
                         facts=dict(SINGLE_FACTS, target="<img src=x onerror=1>"))
        json.dumps(tree)                                     # plain JSON, as /state carries it
        for n in walk(tree):
            self.assertIn(n["tag"], L.CARD_TAGS)
            self.assertLessEqual(set(n), set(L.CARD_KEYS), n)
            self.assertTrue(n["cls"] is None or re.fullmatch(r"[a-z0-9 -]*", n["cls"]), n)
            self.assertTrue(all(isinstance(k, (str, dict)) for k in n["kids"]))
        self.assertIn("Scorecard: <img src=x onerror=1> (individual)", list(texts(tree)))   # drawn as text
        with self.assertRaises(ValueError):
            L.card_node("script", None, "x")
        with self.assertRaises(ValueError):
            L.card_node("a", None, "x", href="javascript:1")

    def test_the_facts_line_holds_what_the_title_does_not(self):
        self.assertEqual(L.card_facts(CROSS_FACTS, 61), "Codex · quick tier · memory both · took 1m 01s")
        self.assertEqual(L.card_facts(VERSUS_FACTS, 3725), "Claude Code sonnet · standard tier · memory on · took "
                                                           "1h 02m")
        self.assertEqual(L.card_facts({"agent": "Codex"}), "Codex")                 # nothing invented
        self.assertEqual(L.card_facts({}), "")
        long = "x" * 512                                                            # tokens each within the cap
        for built in (L.card_facts(dict(VERSUS_FACTS, model=long)), L.card_title({"names": long + " and " + long}),
                      L.card_title(dict(SINGLE_FACTS, target=long))):
            self.assertLessEqual(len(built), P.DISPLAY_CHARS)                       # the whole line within it

    def test_the_words_are_one_table(self):
        w = L.CARD_WORDS
        for key, words in (("finished", "Run finished"), ("open", "Open the results page"),
                           ("ready", "Results are ready"), ("live", "Watch the run live"), ("watch_page", "Watch page"),
                           ("saved", "Saved in"), ("dismiss", "Dismiss")):
            self.assertEqual(w[key], words)
        self.assertEqual(L.LIVE_WORDS["results"], w["open"])
        for value in [w["finished"], w["ready"], *w["why"].values()]:
            self.assertNotIn("!", value)


class TestTitle(unittest.TestCase):
    """D5: the card's title is the very string the run's results page carries in its h1, by the page's own function,
    from the facts the --watch page already has."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="rc-title-"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_a_single_target_runs_title(self):
        out = self.tmp / "single"
        out.mkdir()
        meta = TR.single_run(out)
        b = P.load_bank(P.BANK, P.BANK_MANIFEST_SHA256)
        roster = P.resolve_target(b, "organization", "Mozilla")
        self.assertEqual(L.card_title(L.run_facts(meta, "single", roster)), page_h1(out / "results.html"))

    def test_a_cross_type_runs_title(self):
        """A cross-type run's title names its types, which the card reads from the run's kind (feed.CrossKind's
        types): the page's facts stay 4.3's list."""
        out = self.tmp / "cross"
        out.mkdir()
        meta = TR.cross_run(out)
        calls, kind = TLF.cross_plan(meta)
        view = L.RunView("T", out, calls, L.run_facts(meta, "cross"), kind, L.ViewRedactor())
        self.assertNotIn("types", view.facts)
        self.assertEqual(L.card_title(view.card_facts()), page_h1(out / "ordo" / "results.html"))

    def test_each_page_draws_its_h1_with_the_same_function(self):
        import ordo_html
        import single_html
        import versus_html
        import versus_run
        self.assertEqual(versus_run.card_title("Corvin Hale and Lantern Foundry"),
                         versus_html.page_title("Corvin Hale and Lantern Foundry"))
        self.assertEqual(L.card_title({"names": "A and B"}), "A and B")     # live builds no versus title itself
        for module, call in ((single_html, "page_title(target[\"full\"], target[\"type\"])"),
                             (ordo_html, "page_title(shown[\"types\"])"),
                             (versus_html, "page_title(names_joined(card))")):
            self.assertIn(f'tag("h1", text({call}))', Path(module.__file__).read_text(encoding="utf-8"))


class TestPageState(unittest.TestCase):
    """Whether a done run's page can be served now, by the pinned reader alone, and why not (the card's sentence)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.out = self.tmp / "run"
        self.out.mkdir()
        self.identity = U.folder_identity(str(self.out))

    def test_a_regular_page_is_ready_a_missing_one_missing(self):
        self.assertEqual(L.page_state(self.out, self.identity, U.SINGLE_PAGE), "missing")
        (self.out / "results.html").write_bytes(b"<!doctype html><p>page</p>")
        self.assertEqual(L.page_state(self.out, self.identity, U.SINGLE_PAGE), "ready")
        self.assertEqual(L.page_state(self.out, self.identity, U.ORDO_PAGE), "missing")
        (self.out / "ordo").mkdir()
        (self.out / "ordo" / "results.html").write_bytes(b"<!doctype html><p>ordo</p>")
        self.assertEqual(L.page_state(self.out, self.identity, U.ORDO_PAGE), "ready")

    def test_a_page_the_reader_refuses_is_refused(self):
        outside = self.tmp / "outside.html"
        outside.write_bytes(b"<!doctype html><p>outside</p>")
        os.symlink(outside, self.out / "results.html")                           # a link: the reader refuses it
        self.assertEqual(L.page_state(self.out, self.identity, U.SINGLE_PAGE), "refused")
        os.unlink(self.out / "results.html")
        (self.out / "results.html").write_bytes(b"<!doctype html><p>page</p>")
        os.link(self.out / "results.html", self.tmp / "second-name.html")         # a second name: refused
        self.assertEqual(L.page_state(self.out, self.identity, U.SINGLE_PAGE), "refused")
        self.assertEqual(L.page_state(self.out, None, U.SINGLE_PAGE), "refused")  # no identity recorded
        os.unlink(self.tmp / "second-name.html")
        self.assertEqual(L.page_state(self.out, self.identity, U.SINGLE_PAGE), "ready")
        self.assertEqual(L.page_state(self.out, (0, 0), U.SINGLE_PAGE), "refused")  # another folder's identity

    def test_the_text_files_are_named_as_the_folder_holds_them(self):
        self.assertEqual(L.text_files(self.out, U.SINGLE_PAGE), [])
        for name in ("results.csv", "report.md", "scorecard.md"):
            (self.out / name).write_text("x")
        self.assertEqual(L.text_files(self.out, U.SINGLE_PAGE), ["scorecard.md", "report.md", "results.csv"])
        (self.out / "ordo").mkdir()
        (self.out / "ordo" / "scorecard.md").write_text("s")
        self.assertEqual(L.text_files(self.out, U.ORDO_PAGE), ["ordo/scorecard.md"])


def luminance(hex_colour):
    """WCAG's relative luminance of a #rrggbb colour."""
    c = [int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def contrast(a, b):
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


class TestContrast(unittest.TestCase):
    """D14: the button's text against its fill, and the label, title, facts, footer and the other runs' text against
    the card, at 4.5:1 or better, in both schemes."""

    def test_every_text_holds_four_and_a_half_to_one(self):
        for scheme in (0, 1):
            c = {role: K.COLOURS[keys[scheme]] for role, keys in L.CARD_ROLES.items()}
            for text in ("ink", "label", "muted", "accent-text"):
                self.assertGreaterEqual(contrast(c[text], c["fill"]), 4.5, (scheme, text))
            self.assertGreaterEqual(contrast(c["on-btn"], c["btn"]), 4.5, scheme)
            self.assertGreaterEqual(contrast(c["on-accent"], c["accent"]), 3, scheme)   # the check
            self.assertGreaterEqual(contrast(c["accent"], c["fill"]), 3, scheme)        # the outline


# ---- the --watch page (step 2) -------------------------------------------------------------------------------------
def single_plan(meta):
    """The calls, facts and kind of test_rescore's planted single-target run (single_run), from its run.json record."""
    b = P.load_bank(P.BANK, P.BANK_MANIFEST_SHA256)
    roster = P.resolve_target(b, "organization", "Mozilla")
    calls = P.plan_v4(b, roster, "smoke", P.V4_CLASSES, ("with",))
    return calls, L.run_facts(meta, "single", roster), F.SingleKind(roster)


class Clocked:
    """A live line on a clock the test moves, as probatio gives a view one (the card's time is the line's)."""

    def __init__(self, calls):
        self.now = [100.0]
        self.line = L.LiveLine(4, lambda c: 1.0, stream=io.StringIO(), tty=False, clock=lambda: self.now[0])
        self.line.begin(calls, {})


class TestLiveCard(unittest.TestCase):
    """The --watch page's state: each run's card (C4) and bar, its time, the route behind its button."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="rc-live-"))
        cls.single = cls.tmp / "single"
        cls.single.mkdir()
        cls.single_meta = TR.single_run(cls.single)
        cls.cross = cls.tmp / "cross"
        cls.cross.mkdir()
        cls.cross_meta = TR.cross_run(cls.cross)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def copy(self, which):
        out = Path(tempfile.mkdtemp(dir=self.tmp)) / "run"
        shutil.copytree(getattr(self, which), out, symlinks=True)
        return out

    def watch(self, names=("T",)):
        w = L.Watch(list(names), stream=io.StringIO())
        self.addCleanup(w.close)
        return w

    def start(self, watch, out, kind="single", clock=None):
        if kind == "single":
            calls, facts, k = single_plan(self.single_meta)
        else:
            calls, k = TLF.cross_plan(self.cross_meta)
            facts = L.run_facts(self.cross_meta, "cross")
        clock = clock or Clocked(calls)
        return watch.start("T", out, calls, facts, k, clock.line), clock

    def test_no_card_while_the_run_goes(self):
        w = self.watch()
        self.start(w, self.copy("single"))
        run = w.state()["runs"][0]
        self.assertEqual((run["status"], run["card"], run["bar"]), ("running", None, None))

    def test_a_done_run_has_its_card_its_time_kept_and_its_page_behind_the_button(self):
        w = self.watch()
        out = self.copy("single")
        view, clock = self.start(w, out)
        clock.now[0] += 754.4
        w.finish(view)
        clock.now[0] += 600                                  # the line counts on; the card keeps the run's time
        run = w.state()["runs"][0]
        self.assertEqual(card_links(run["card"]), [{"run": 0}])
        self.assertEqual(having(run["card"], "rc-title")[0]["kids"], [page_h1(out / "results.html")])
        self.assertEqual(having(run["card"], "rc-facts")[0]["kids"], [L.card_facts(view.facts, 754.4)])
        self.assertIn("took 12m 34s", having(run["card"], "rc-facts")[0]["kids"][0])
        self.assertEqual(run["progress"]["elapsed_seconds"], 754.4)          # the page's progress keeps it too (D9)
        self.assertEqual(having(run["card"], "rc-path")[0]["kids"], [L.display(str(out.resolve()))])
        self.assertEqual(having(run["card"], "rc-files")[0]["kids"], ["scorecard.md · report.md · results.csv"])
        self.assertEqual(card_links(run["bar"]), [{"run": 0}])
        res, text = TLF.client_of(w).request("GET", "/results.html?run=0")
        self.assertEqual((res.status, text.encode("utf-8")), (200, (out / "results.html").read_bytes()))

    def test_a_long_folder_is_cut_as_every_string_is(self):
        """F2: a folder a file system takes (nested names, about 700 characters in all) is shown on the card cut to
        the display cap, as the form's card cuts the same folder (ui.shown), never whole."""
        deep = Path(tempfile.mkdtemp(dir=self.tmp))
        for i in range(4):
            deep = deep / (f"d{i}-" + "L" * 160)
        deep.mkdir(parents=True)
        out = deep / "run"
        shutil.copytree(self.single, out, symlinks=True)
        self.assertGreater(len(os.path.abspath(out)), P.DISPLAY_CHARS)
        w = self.watch()
        view, _ = self.start(w, out)
        w.finish(view)
        (path,) = having(w.state()["runs"][0]["card"], "rc-path")[0]["kids"]
        self.assertEqual(path, L.display(os.path.abspath(out)))
        self.assertEqual(len(path), P.DISPLAY_CHARS)
        self.assertEqual(len(U.shown(out)), P.DISPLAY_CHARS)               # the form's card cuts it the same way

    def test_the_button_follows_the_folder(self):
        """A page taken away after the run is scored is no longer offered (the card says why, the route answers
        404, there is no bar), and offered again once it is back; a page the pinned reader refuses is never
        offered."""
        w = self.watch()
        out = self.copy("single")
        view, _ = self.start(w, out)
        w.finish(view)
        client = TLF.client_of(w)
        self.assertEqual(card_links(w.state()["runs"][0]["card"]), [{"run": 0}])
        (out / "results.html").rename(out / "away.html")
        view.check.at = None                                 # the next state asks the reader again
        run = w.state()["runs"][0]
        self.assertEqual(card_links(run["card"]), [])
        self.assertIn(L.CARD_WORDS["why"]["missing"], list(texts(run["card"])))
        self.assertEqual((run["results"], run["bar"]), (None, None))
        self.assertEqual(client.request("GET", "/results.html?run=0")[0].status, 404)
        os.symlink(out / "away.html", out / "results.html")
        view.check.at = None
        run = w.state()["runs"][0]
        self.assertEqual(card_links(run["card"]), [])
        self.assertIn(L.CARD_WORDS["why"]["refused"], list(texts(run["card"])))
        self.assertEqual(client.request("GET", "/results.html?run=0")[0].status, 404)
        (out / "results.html").unlink()
        (out / "away.html").rename(out / "results.html")
        time.sleep(L.CARD_RECHECK_SECONDS + 0.1)            # asked again at its own pace
        self.assertEqual(card_links(w.state()["runs"][0]["card"]), [{"run": 0}])
        self.assertEqual(client.request("GET", "/results.html?run=0")[0].status, 200)

    def test_a_cross_type_runs_card_opens_its_ordo_page(self):
        w = self.watch()
        out = self.copy("cross")
        view, _ = self.start(w, out, "cross")
        w.finish(view)
        run = w.state()["runs"][0]
        self.assertEqual(card_links(run["card"]), [{"run": 0}])
        self.assertEqual(having(run["card"], "rc-files")[0]["kids"], ["ordo/scorecard.md · ordo/report.md"])
        self.assertEqual(having(run["card"], "rc-title")[0]["kids"], [page_h1(out / "ordo" / "results.html")])
        res, text = TLF.client_of(w).request("GET", "/results.html?run=0")
        self.assertEqual(text.encode("utf-8"), (out / "ordo" / "results.html").read_bytes())

    def test_a_command_of_several_runs_lists_the_others_each_with_its_own_link(self):
        w = self.watch(["A (organization)", "B (organization)", "C (organization)"])
        a, b = self.copy("single"), self.copy("single")
        (b / "results.html").write_bytes(b"<!doctype html><p>B's own page</p>")
        va, _ = self.start(w, a)
        w.finish(va)
        vb, _ = self.start(w, b)
        runs = w.state()["runs"]
        title = L.card_title(va.facts)
        others = having(runs[0]["card"], "rc-other")
        self.assertEqual([[x[0], x[-1]] for x in ([t for t in texts(o) if t.strip()] for o in others)],
                         [[title, "running"], ["C (organization)", "waiting"]])
        self.assertEqual(card_links(runs[0]["card"]), [{"run": 0}])
        self.assertIsNone(runs[1]["card"])
        w.finish(vb)
        runs = w.state()["runs"]
        self.assertEqual(card_links(runs[0]["card"]), [{"run": 0}, {"run": 1}])
        self.assertEqual(card_links(runs[1]["card"]), [{"run": 1}, {"run": 0}])
        self.assertEqual(TLF.client_of(w).request("GET", "/results.html?run=1")[1],
                         "<!doctype html><p>B's own page</p>")
        self.assertNotIn("card", runs[2])                    # a waiting run has no view yet

    def test_a_run_that_wrote_no_page_says_so(self):
        w = self.watch()
        out = self.copy("single")
        (out / "results.html").unlink()
        view, _ = self.start(w, out)
        w.finish(view)
        run = w.state()["runs"][0]
        self.assertEqual((card_links(run["card"]), run["bar"]), ([], None))
        self.assertIn(L.CARD_WORDS["why"]["missing"], list(texts(run["card"])))


class TestLiveVersusCard(TVW.Finished):
    """C5: a head-to-head run's card comes only when the run is complete, never before; a refusal has none; its title
    is its results page's h1."""

    def test_no_card_before_the_end_then_the_card_and_its_page(self):
        total = len(TVW.lines_of(self.full))
        for n in (0, total // 2, total):
            m = self.at(n, f"rc-at-{n}")
            self.assertIsNone(m.state()["run"]["card"], n)
            self.assertIsNone(m.state()["run"]["bar"], n)
        card, _ = VR.result_card(self.full, VS.load_bank(VS.BANK, VS.VERSUS_MANIFEST_SHA256, development=True),
                                 TVW.ordo.load_ordo_bank())
        shutil.copy2(self.full / "results.html", Path(m.view.out) / "results.html")
        m.watch.finish(m.view, VR.answer_lines(card, self.full))
        run = m.state()["run"]
        self.assertEqual(card_links(run["card"]), [{"run": 0}])
        self.assertEqual(having(run["card"], "rc-title")[0]["kids"], [page_h1(self.full / "results.html")])
        res, text = TLF.client_of(m.watch).request("GET", "/results.html")
        self.assertEqual(text.encode("utf-8"), (self.full / "results.html").read_bytes())

    def test_a_reader_that_exits_fails_closed(self):
        """P1: with a reader that raises SystemExit, VersusWatch.finish does not raise, and /state answers 200 with
        the card saying the page could not be read, no link and no bar; one traceback."""
        m = self.at(len(TVW.lines_of(self.full)), "rc-exits")
        card, _ = VR.result_card(self.full, VS.load_bank(VS.BANK, VS.VERSUS_MANIFEST_SHA256, development=True),
                                 TVW.ordo.load_ordo_bank())
        shutil.copy2(self.full / "results.html", Path(m.view.out) / "results.html")
        reader, err = Reader(SystemExit), io.StringIO()
        with mock.patch.object(U, "read_results_page", reader), contextlib.redirect_stderr(err):
            m.watch.finish(m.view, VR.answer_lines(card, self.full))
            res, text = TLF.client_of(m.watch).request("GET", "/state")
            self.assertEqual(res.status, 200)
            run = json.loads(text)["run"]
        self.assertEqual((card_links(run["card"]), run["bar"], run["results"]), ([], None, None))
        self.assertIn(L.CARD_WORDS["why"]["refused"], list(texts(run["card"])))
        self.assertEqual(tracebacks(err.getvalue()), 1)
        self.assertIn("SystemExit: a reader that raises", err.getvalue())

    def test_a_refused_run_has_no_card(self):
        m = self.at(len(TVW.lines_of(self.full)), "rc-refused")
        m.watch.refuse(m.view, "refused: one line")
        self.assertIsNone(m.state()["run"]["card"])


def page_card(chrome, box="#card"):
    """What a page's card holds, as drawn: its classes, its text, its links' addresses and classes, its buttons, or
    None."""
    return chrome.js(f"""(() => {{ const c = document.querySelector('{box} > .rk'); if (!c) return null;
      return {{cls: c.className, text: c.innerText, hrefs: [...c.querySelectorAll('a')].map(a => a.href),
              links: [...c.querySelectorAll('a')].map(a => [a.className, a.target, a.rel]),
              buttons: c.querySelectorAll('button').length}}; }})()""")


def tab(chrome):
    for kind in ("keyDown", "keyUp"):
        chrome.call("Input.dispatchKeyEvent", type=kind, key="Tab", code="Tab", windowsVirtualKeyCode=9,
                    nativeVirtualKeyCode=9)


@unittest.skipUnless(TU.CHROME, "needs a headless Chrome on PATH")
class TestLiveBrowser(unittest.TestCase):
    """The card on the --watch page over a planted single-target run, in the suite's headless Chrome."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="rc-pf-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.out = self.tmp / "run"
        self.b, self.roster, self.calls, self.meta = TW.v4_run(self.out, tier="quick")
        self.all = TW.replies_of(self.out)
        TW.write_replies(self.out, self.all[:120])
        self.watch = L.Watch(["T"], stream=io.StringIO())
        self.addCleanup(self.watch.close)
        self.view = self.watch.start("T", self.out, self.calls, L.run_facts(self.meta, "single", self.roster),
                                     F.SingleKind(self.roster))
        self.chrome = TU.Chrome(self.tmp / "profile")
        self.addCleanup(self.chrome.close)
        self.chrome.call("Page.enable")
        self.chrome.call("Page.bringToFront")
        self.chrome.call("Emulation.setFocusEmulationEnabled", enabled=True)
        self.chrome.call("Emulation.setEmulatedMedia", features=[{"name": "prefers-color-scheme", "value": "light"}])
        self.chrome.call("Page.navigate", url=self.watch.server.url)
        self.assertTrue(TU.wait_for(lambda: self.js("document.querySelectorAll('#feed details.entry').length")
                                    == F.SHOWN, 30))

    def js(self, expression):
        return self.chrome.js(expression)

    def score(self, land=True):
        """The run is scored (with the rest of its replies landed first, unless land is false: the feed then stays
        as it is, so only the card changes the page)."""
        if land:
            TW.write_replies(self.out, self.all)
        self.watch.finish(self.view, P.score_run(self.out, self.calls, self.b, self.roster, self.meta, B=50))
        self.assertTrue(TU.wait_for(lambda: page_card(self.chrome) is not None, 15))

    def bar(self):
        return self.js("!!document.querySelector('#bar .rc-bar')")

    def test_the_card_comes_first_once_done_and_its_button_opens_the_page(self):
        time.sleep(1.2)
        self.assertIsNone(page_card(self.chrome))                       # running: no card
        self.assertEqual(self.js("document.querySelectorAll('.rk').length"), 0)
        self.score()
        card = page_card(self.chrome)
        token, port = self.watch.server.token, self.watch.server.server_address[1]
        self.assertEqual(card["hrefs"], [f"http://127.0.0.1:{port}/results.html?t={token}&run=0"])
        self.assertEqual(card["links"], [["rc-btn rc-open", "_blank", "noopener noreferrer"]])
        self.assertEqual(card["buttons"], 0)
        self.assertIn("rc-enter", card["cls"])                         # it arrived while the page was open (D9)
        self.assertEqual(self.js("document.querySelector('#card .rc-open .rc-arrow').getAttribute('aria-hidden')"),
                         "true")
        self.assertEqual(self.js("document.querySelector('#card .rc-title').textContent"),
                         page_h1(self.out / "results.html"))
        got = self.js(f"fetch({json.dumps(card['hrefs'][0])}).then(r => r.text())")
        self.assertEqual(got, (self.out / "results.html").read_text(encoding="utf-8"))
        tops = self.js("['header', '#card', '#tabs', '#head', '#table', '#nowsec', '#feedsec'].map(q => "
                       "document.querySelector(q).getBoundingClientRect().top)")
        self.assertEqual(tops, sorted(tops))                            # first under the title row
        accent = self.js("(() => { const s = document.createElement('span'); s.style.color = "
                         f"'{K.COLOURS['accent']}'; document.body.append(s); const c = getComputedStyle(s).color; "
                         "s.remove(); return c; })()")
        self.assertEqual(self.js("getComputedStyle(document.querySelector('#card a.rc-open')).backgroundColor"),
                         accent)                                        # the kit's accent: its style applied
        self.assertEqual(self.js("document.querySelector('#head .bar i').style.width"), "100%")   # D9
        self.chrome.call("Page.navigate", url=self.watch.server.url)   # a fresh load of a finished run: no rise
        self.assertTrue(TU.wait_for(lambda: page_card(self.chrome) is not None, 15))
        self.assertNotIn("rc-enter", page_card(self.chrome)["cls"])

    def test_the_keyboard_reaches_the_button_first(self):
        self.score()
        self.js("document.querySelector('#card .rc-title').focus(); true")
        tab(self.chrome)
        self.assertEqual(self.js("document.activeElement.className"), "rc-btn rc-open")
        self.chrome.call("Page.navigate", url=self.watch.server.url)          # a fresh load: the page's first stop
        self.assertTrue(TU.wait_for(lambda: page_card(self.chrome) is not None, 15))
        tab(self.chrome)
        self.assertEqual(self.js("document.activeElement.className"), "rc-btn rc-open")
        time.sleep(2.5)                                       # refreshes leave the focused button where it is
        self.assertEqual(self.js("document.activeElement.className"), "rc-btn rc-open")

    def test_the_bar_tells_the_reader_without_moving_them(self):
        """Scrolled down with an entry open at the top edge, the run finishes: the bar appears at the top, below the
        opened entry's first line, with its words and button, and what is read does not move; back at the card it
        goes, further down it comes back; dismissed, it never returns."""
        self.js("const d = document.querySelectorAll('#feed details.entry')[10]; d.open = true; "
                "d.scrollIntoView(); window.__d = d; true")
        self.assertTrue(TU.wait_for(lambda: self.js("window.__d.querySelector('.body dl') !== null"), 10))
        time.sleep(1.2)
        top, scroll = self.js("window.__d.getBoundingClientRect().top"), self.js("window.scrollY")
        self.assertGreater(scroll, 0)
        self.score(land=False)
        self.assertTrue(TU.wait_for(self.bar, 30))
        time.sleep(1.2)
        self.assertLess(abs(self.js("window.__d.getBoundingClientRect().top") - top), 2)   # not moved
        # the bar's place is its own top (its slide-in moves it by a transform while it runs, and a browser that
        # does not paint, as this headless one, never runs it to the end)
        got = self.js("""(() => { const b = document.querySelector('#bar .rc-bar'), r = b.getBoundingClientRect(),
          s = window.__d.querySelector('summary').getBoundingClientRect();
          return {position: getComputedStyle(b).position, top: parseFloat(getComputedStyle(b).top), height: r.height,
                  line: [s.top, s.bottom],
                  hrefs: [...b.querySelectorAll('a')].map(a => a.href), text: b.innerText,
                  bg: getComputedStyle(b).backgroundColor, blur: getComputedStyle(b).backdropFilter,
                  focus: document.activeElement === document.body || !b.contains(document.activeElement)}; })()""")
        self.assertEqual((got["position"], got["height"], got["focus"]), ("fixed", 44, True))
        self.assertRegex(got["bg"], r"^rgb\(\d+, \d+, \d+\)$")                  # E4: opaque, the page's paper
        self.assertIn(got["blur"], ("none", ""))
        self.assertGreaterEqual(got["top"], got["line"][1] - 0.5)        # below the opened entry's first line
        self.assertEqual(got["hrefs"], page_card(self.chrome)["hrefs"])
        self.assertIn(L.CARD_WORDS["ready"], got["text"])
        self.js("window.scrollBy(0, 300); true")         # the entry's first line leaves the top: the bar is back at
        self.assertTrue(TU.wait_for(lambda: self.js(      # the edge (at the next refresh where no scroll event fires)
            "parseFloat(getComputedStyle(document.querySelector('#bar .rc-bar')).top)") == 0, 10))
        self.js("window.scrollTo(0, 0); true")                          # back at the card: it goes by itself
        self.assertTrue(TU.wait_for(lambda: not self.bar(), 20))
        self.js("window.scrollTo(0, 2000); true")                       # further down again: it comes back
        self.assertTrue(TU.wait_for(self.bar, 20))
        self.js("document.querySelector('#bar .rc-x').click(); true")
        self.assertTrue(TU.wait_for(lambda: not self.bar(), 20))
        self.js("window.scrollTo(0, 0); true")
        time.sleep(0.4)
        self.js("window.scrollTo(0, 2500); true")
        time.sleep(1.2)
        self.assertFalse(self.bar())                                     # dismissed, never again

    def test_the_bars_words_show_whole_or_not_at_all(self):
        """G1: "Results are ready" shows whole or not in sight (decided by the bar's own width, BAR_WORDS_MIN), never
        cut: at 390 px it is hidden from sight but kept for a screen reader, with the disc, the full-label button and
        the close in the bar; at 820 and 1280 px it shows whole. Nothing in sight in the bar is cut at any of them:
        no element's scrollWidth exceeds its clientWidth, none ends in an ellipsis, none reaches past the bar."""
        self.score()
        for width, shown in ((390, False), (820, True), (1280, True)):
            self.chrome.call("Emulation.setDeviceMetricsOverride", width=width, height=900, deviceScaleFactor=1,
                             mobile=width < 600)
            self.js("window.scrollTo(0, document.documentElement.scrollHeight); true")
            self.assertTrue(TU.wait_for(self.bar, 20), width)
            got = self.js("""(() => { const b = document.querySelector('#bar .rc-bar'), r = b.getBoundingClientRect();
              const hidden = e => getComputedStyle(e).clip === 'rect(0px, 0px, 0px, 0px)';
              const words = b.querySelector('.rc-bar-h'), btn = b.querySelector('.rc-btn');
              const seen = [b, ...b.querySelectorAll('*')].filter(e => !hidden(e) && !e.closest('.rc-sr'));
              return {words_hidden: hidden(words), words: words.textContent, words_width: words.clientWidth,
                      cut: seen.filter(e => e.scrollWidth > e.clientWidth + 0.5 ||
                                            getComputedStyle(e).textOverflow === 'ellipsis' ||
                                            e.getBoundingClientRect().right > r.right + 0.5)
                               .map(e => e.className || e.tagName),
                      button: btn.innerText, button_whole: btn.scrollWidth <= btn.clientWidth,
                      close: !!b.querySelector('.rc-x'), disc: !!b.querySelector('.rc-disc')}; })()""")
            self.assertEqual(got["cut"], [], (width, got))
            self.assertEqual(got["words"], L.CARD_WORDS["ready"])             # kept for a screen reader either way
            self.assertEqual(got["words_hidden"], not shown, (width, got))
            if shown:
                self.assertGreater(got["words_width"], 100, (width, got))      # whole, in sight
            self.assertTrue(got["button_whole"] and got["close"] and got["disc"], (width, got))
            self.assertTrue(got["button"].startswith(L.CARD_WORDS["open"]), (width, got))

    def test_the_bar_spans_the_window_its_content_with_the_column(self):
        """F11: the bar's paper and its rule run the window's full width, so nothing of the feed shows beside or
        behind it; its content (the disc first) stays aligned with the content column, at a phone's width and a wide
        one."""
        self.score()
        for width, height in ((390, 844), (1280, 900)):
            self.chrome.call("Emulation.setDeviceMetricsOverride", width=width, height=height, deviceScaleFactor=1,
                             mobile=width < 600)
            self.js("window.scrollTo(0, document.documentElement.scrollHeight); true")
            self.assertTrue(TU.wait_for(self.bar, 20), width)
            got = self.js("""(() => { const b = document.querySelector('#bar .rc-bar').getBoundingClientRect(),
              d = document.querySelector('#bar .rc-disc').getBoundingClientRect(), m = document.querySelector('main'),
              left = m.getBoundingClientRect().left + parseFloat(getComputedStyle(m).paddingLeft);
              return {left: b.left, right: b.right, page: document.documentElement.clientWidth, disc: d.left,
                      column: left, rule: getComputedStyle(document.querySelector('#bar .rc-bar')).borderBottomWidth};
              })()""")
            self.assertEqual((got["left"], got["right"]), (0, got["page"]), (width, got))
            self.assertAlmostEqual(got["disc"], got["column"] + 10, delta=1, msg=(width, got))
            self.assertEqual(got["rule"], "1px")

    def test_finishing_at_the_top_of_the_page_brings_no_bar(self):
        self.assertEqual(self.js("window.scrollY"), 0)
        self.score()
        time.sleep(1.2)
        self.assertFalse(self.bar())

    def test_with_reduced_motion_nothing_moves(self):
        self.chrome.call("Emulation.setEmulatedMedia", features=[{"name": "prefers-reduced-motion",
                                                                  "value": "reduce"}])
        self.score()
        self.assertEqual(self.js("getComputedStyle(document.querySelector('#card .rc')).animationName"), "none")

    def test_a_page_that_cannot_be_served_has_no_button(self):
        TW.write_replies(self.out, self.all)
        card = P.score_run(self.out, self.calls, self.b, self.roster, self.meta, B=50)
        (self.out / "results.html").unlink()
        self.watch.finish(self.view, card)
        self.assertTrue(TU.wait_for(lambda: page_card(self.chrome) is not None, 15))
        got = page_card(self.chrome)
        self.assertEqual((got["hrefs"], got["buttons"]), ([], 0))
        self.assertIn(L.CARD_WORDS["why"]["missing"], got["text"])

    def test_the_policy_stays_hash_only(self):
        res, page = TLF.client_of(self.watch).request("GET", "/")
        policy = res.getheader("Content-Security-Policy")
        self.assertEqual(policy, L.content_policy(L.VIEW_LIVE))
        for directive in ("script-src", "style-src"):
            sources = re.search(directive + r" ([^;]*)", policy).group(1).split()
            self.assertTrue(sources and all(re.fullmatch(r"'sha256-[A-Za-z0-9+/=]+'", x) for x in sources), sources)
        self.assertNotIn("unsafe", policy)


@unittest.skipUnless(TU.CHROME, "needs a headless Chrome on PATH")
class TestLiveBrowserKinds(unittest.TestCase):
    """The card on the --watch page for a cross-type run and for a command of several runs, in headless Chrome:
    each link's address serves that run's own page."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="rc-kinds-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def open(self, watch):
        chrome = TU.Chrome(self.tmp / f"profile-{len(os.listdir(self.tmp))}")
        self.addCleanup(chrome.close)
        chrome.call("Page.navigate", url=watch.server.url)
        self.assertTrue(TU.wait_for(lambda: page_card(chrome) is not None, 30))
        return chrome

    def test_a_cross_type_runs_button_serves_its_ordo_page(self):
        out = self.tmp / "cross"
        out.mkdir()
        meta = TR.cross_run(out)
        calls, kind = TLF.cross_plan(meta)
        watch = L.Watch(["T"], stream=io.StringIO())
        self.addCleanup(watch.close)
        view = watch.start("T", out, calls, L.run_facts(meta, "cross"), kind)
        watch.finish(view)
        chrome = self.open(watch)
        card = page_card(chrome)
        got = chrome.js(f"fetch({json.dumps(card['hrefs'][0])}).then(r => r.text())")
        self.assertEqual(got, (out / "ordo" / "results.html").read_text(encoding="utf-8"))
        self.assertIn("ordo/scorecard.md", card["text"])

    def test_several_runs_each_with_its_own_link(self):
        watch = L.Watch(["A (organization)", "B (organization)"], stream=io.StringIO())
        self.addCleanup(watch.close)
        for name in ("a", "b"):
            out = self.tmp / name
            out.mkdir()
            meta = TR.single_run(out)
            (out / "results.html").write_text(f"<!doctype html><p>{name}'s own page</p>", encoding="utf-8")
            calls, facts, kind = single_plan(meta)
            watch.finish(watch.start(name, out, calls, facts, kind))
        chrome = self.open(watch)
        card = page_card(chrome)
        self.assertEqual([c for c, _, _ in card["links"]], ["rc-btn rc-open", "rc-go"])
        self.assertEqual([chrome.js(f"fetch({json.dumps(h)}).then(r => r.text())") for h in card["hrefs"]],
                         ["<!doctype html><p>a's own page</p>", "<!doctype html><p>b's own page</p>"])


@unittest.skipUnless(TU.CHROME, "needs a headless Chrome on PATH")
class TestLiveBrowserVersus(TVW.Finished):
    """A head-to-head run on the --watch page, in headless Chrome: no card until it is complete, then the card, its
    button serving the run's own page."""

    def test_the_card_comes_only_at_the_end(self):
        total = len(TVW.lines_of(self.full))
        m = self.at(total, "rc-chrome")
        chrome = TU.Chrome(self.tmp / "rc-chrome-profile")
        self.addCleanup(chrome.close)
        chrome.call("Page.navigate", url=m.watch.server.url)
        self.assertTrue(TU.wait_for(lambda: "stated allocation" in chrome.js("document.body.innerText"), 60))
        time.sleep(1.2)
        self.assertIsNone(page_card(chrome))
        card, _ = VR.result_card(self.full, VS.load_bank(VS.BANK, VS.VERSUS_MANIFEST_SHA256, development=True),
                                 TVW.ordo.load_ordo_bank())
        shutil.copy2(self.full / "results.html", Path(m.view.out) / "results.html")
        m.watch.finish(m.view, VR.answer_lines(card, self.full))
        self.assertTrue(TU.wait_for(lambda: page_card(chrome) is not None, 15))
        got = page_card(chrome)
        page = chrome.js(f"fetch({json.dumps(got['hrefs'][0])}).then(r => r.text())")
        self.assertEqual(page, (self.full / "results.html").read_text(encoding="utf-8"))
        self.assertIn(page_h1(self.full / "results.html"), got["text"])


# ---- the form (step 3) ------------------------------------------------------------------------------------------------
SAYS = r"""
import os, sys, time
folders = os.environ["RUN_FOLDERS"].split(os.pathsep)
if os.environ.get("WATCH_URL"):
    print("  watch it live: " + os.environ["WATCH_URL"], flush=True)
time.sleep(float(os.environ.get("BEFORE", "0")))
for i, f in enumerate(folders):
    if i:
        time.sleep(float(os.environ.get("BETWEEN", "0")))
    for n in range(int(os.environ.get("TALL", "0"))):          # a run that printed many lines, and a long token
        print(f"probatio: quick · {n * 9}/360 · {n * 9} valid · 0 abstained · {n}s", flush=True)
    if os.environ.get("TALL"):
        print("x" * 300, flush=True)
    ordo = os.path.isdir(os.path.join(f, "ordo"))
    at = os.path.join(f, "ordo") if ordo else f
    if os.environ.get("VERSUS"):
        print(f"Results: {f}", flush=True)
    else:
        print(f"Scorecard: {os.path.join(at, 'scorecard.json')}", flush=True)
        print(f"Report: {os.path.join(at, 'report.md')}", flush=True)
    if not os.environ.get("NO_PAGE"):
        print(f"Page: {os.path.join(at, 'results.html')}", flush=True)
if os.environ.get("HOLD"):
    print("", flush=True)
    print("The live page stays up at " + os.environ.get("WATCH_URL", "") + " until you press Ctrl-C.", flush=True)
    time.sleep(float(os.environ["HOLD"]))
sys.exit(int(os.environ.get("CODE", "0")))
"""


FACTS = {"kind": "single-type", "target": "Corvin Hale", "target_type": "individual", "agent": "Claude Code",
         "model": "", "tier": "quick", "memory": "on"}


def says(folders, argv=(), reading=None, **env):
    """A Runner over a stand-in command that prints a run's result lines for each folder (SAYS), as probatio prints
    them, and exits with CODE; no probatio, no agent."""
    e = dict(os.environ, PYTHONUNBUFFERED="1", RUN_FOLDERS=os.pathsep.join(str(f) for f in folders),
             **{k: str(v) for k, v in env.items()})
    return U.Runner([sys.executable, "-c", SAYS], list(argv), [Path(f) for f in folders], e, reading)


class Form(unittest.TestCase):
    """Planted run folders (the suite's planters, scored as a run writes them) and the form's session around a
    stand-in run of them."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="rc-form-"))
        cls.single = cls.tmp / "single"
        cls.single.mkdir()
        TR.single_run(cls.single)
        cls.cross = cls.tmp / "cross"
        cls.cross.mkdir()
        TR.cross_run(cls.cross)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def copy(self, which):
        out = Path(tempfile.mkdtemp(dir=self.tmp)) / "run"
        shutil.copytree(getattr(self, which), out, symlinks=True)
        return out

    def served(self, runner):
        session = U.Session(program=[sys.executable, "-c", "raise SystemExit(3)"])
        served = TU.Served(session)
        self.addCleanup(served.server.server_close)
        self.addCleanup(served.server.shutdown)
        with session.lock:
            session.runner = runner
        self.addCleanup(lambda: runner.proc.poll() is None and runner.proc.kill())   # the test's own child
        return served

    def ended(self, runner):
        runner.wait(60)
        self.assertIsNotNone(runner.code)
        return runner


class TestFormCard(Form):
    """The form's state: its run's card (C4) for each kind, the pair while it goes, the route behind the button."""

    def test_while_the_run_goes_the_run_card_holds_the_live_link(self):
        out = self.copy("single")
        r = says([out], ["--watch"], WATCH_URL="http://127.0.0.1:9/?t=abc", BEFORE=30)
        r.facts = [dict(FACTS)]
        served = self.served(r)
        self.assertTrue(TU.wait_for(lambda: served.state()["run"]["watch"] is not None, 10))
        card = served.state()["run"]["card"]
        self.assertEqual(classes(card)[-1], "rc-running")
        self.assertEqual([(n.get("live"), classes(n)) for n in links(card)], [(True, ["rc-btn", "rc-second"])])
        self.assertEqual(having(card, "rc-title")[0]["kids"], ["Scorecard: Corvin Hale (individual)"])
        self.assertEqual(card_links(card), [None])                       # no results link while it goes
        r.stop()

    def test_a_finished_single_target_run_has_its_card_and_page(self):
        out = self.copy("single")
        r = self.ended(says([out]))
        r.facts = [dict(FACTS)]
        served = self.served(r)
        run = served.state()["run"]
        self.assertEqual((run["status"], classes(run["card"])[-1]), ("finished", "rc-ready"))
        self.assertEqual(card_links(run["card"]), [{"folder": U.shown(out)}])
        self.assertTrue(having(run["card"], "rc-facts")[0]["kids"][0].startswith("Claude Code · quick tier · memory "
                                                                                 "on · took "))
        self.assertEqual(having(run["card"], "rc-files")[0]["kids"], ["scorecard.md · report.md · results.csv"])
        from urllib.parse import quote
        res, text = served.request("GET", "/results.html?folder=" + quote(U.shown(out), safe=""), header=False)
        self.assertEqual((res.status, text.encode("utf-8")), (200, (out / "results.html").read_bytes()))

    def test_a_cross_type_runs_card_opens_its_ordo_page(self):
        out = self.copy("cross")
        served = self.served(self.ended(says([out])))
        run = served.state()["run"]
        self.assertEqual(card_links(run["card"]), [{"folder": U.shown(out)}])
        self.assertEqual(having(run["card"], "rc-files")[0]["kids"], ["ordo/scorecard.md · ordo/report.md"])
        from urllib.parse import quote
        res, text = served.request("GET", "/results.html?folder=" + quote(U.shown(out), safe=""), header=False)
        self.assertEqual(text.encode("utf-8"), (out / "ordo" / "results.html").read_bytes())

    def test_a_head_to_head_runs_card(self):
        out = self.copy("single")                       # a folder with results.html at its top, as a versus run's
        served = self.served(self.ended(says([out], reading=U.VERSUS_READING, VERSUS=1)))
        self.assertEqual(card_links(served.state()["run"]["card"]), [{"folder": U.shown(out)}])

    def test_scored_with_the_live_page_held_the_watch_page_link_is_by_the_folder(self):
        out = self.copy("single")
        r = says([out], ["--watch"], WATCH_URL="http://127.0.0.1:9/?t=abc", HOLD=30)
        served = self.served(r)
        self.assertTrue(TU.wait_for(lambda: served.state()["run"]["status"] == "scored", 10))
        card = served.state()["run"]["card"]
        a, b = links(card)
        self.assertEqual((a.get("link"), classes(a), b.get("live"), classes(b)),
                         ({"folder": U.shown(out)}, ["rc-btn", "rc-open"], True, ["rc-quiet"]))
        self.assertIn(b, list(walk(having(card, "rc-saved")[0])))
        r.stop()

    def test_a_run_that_cannot_offer_its_page_says_why(self):
        out = self.copy("single")
        served = self.served(self.ended(says([out], NO_PAGE=1)))           # no Page: line
        run = served.state()["run"]
        self.assertEqual(card_links(run["card"]), [])
        self.assertIn(L.CARD_WORDS["why"]["missing"], list(texts(run["card"])))
        out = self.copy("single")
        (out / "results.html").rename(out / "real.html")
        (out / "results.html").symlink_to(out / "real.html")              # a link: the pinned reader refuses it
        served = self.served(self.ended(says([out])))
        run = served.state()["run"]
        self.assertEqual(card_links(run["card"]), [])
        self.assertIn(L.CARD_WORDS["why"]["refused"], list(texts(run["card"])))

    def test_a_failed_or_stopped_run_has_no_card(self):
        out = self.copy("single")
        served = self.served(self.ended(says([out], CODE=3)))
        run = served.state()["run"]
        self.assertEqual((run["status"], run["card"]), ("failed", None))

    def test_several_runs_each_with_its_own_button_and_time(self):
        a, b = self.copy("single"), self.copy("cross")
        r = self.ended(says([a, b], BETWEEN=1.2))
        r.facts = [dict(FACTS), dict(FACTS, kind="cross-type", types=["individual", "organization"])]
        served = self.served(r)
        run = served.state()["run"]
        self.assertEqual(card_links(run["card"]), [{"folder": U.shown(a)}, {"folder": U.shown(b)}])
        (item,) = having(run["card"], "rc-other")
        self.assertEqual([t for t in texts(item) if t.strip()][:2], ["Types compared: individual and organization",
                                                                    "Claude Code · quick tier · memory on · took "
                                                                    + L.clock_text(r.took(1))])
        self.assertGreaterEqual(r.took(1), 1.1)
        self.assertLess(r.took(0), r.took(1))

    def test_each_runs_facts_are_the_commands_own(self):
        form = TU.page_form(agent="claude", types=["individual", "organization"],
                            targets={"individual": "Corvin Hale", "organization": "Lantern Foundry"}, tier="quick",
                            versus="types")                              # the comparison, not the head-to-head
        argv, _ = U.build_command(form)
        facts = U.run_card_facts(argv, len(U.prepared(argv)))
        self.assertEqual([(L.card_title(f), L.card_facts(f)) for f in facts],
                         [("Types compared: individual and organization", "Claude Code · quick tier · memory on")])
        argv, _ = U.build_command(TU.page_form(agent="codex", targets={"individual": "Corvin Hale"}, tier="standard"))
        facts = U.run_card_facts(argv, 1)
        self.assertEqual((L.card_title(facts[0]), L.card_facts(facts[0])),
                         ("Scorecard: Corvin Hale (individual)", "Codex · standard tier · memory on"))
        self.assertEqual(U.run_card_facts(["--agent"], 2), [{}, {}])                  # unreadable: nothing
        vs = ["--versus", "individual=Corvin Hale", "--versus", "organization=Lantern Foundry", "--agent", "claude"]
        (facts,) = U.versus_card_facts(vs)
        self.assertEqual(L.card_title(facts), "Head-to-head: Corvin Hale and Lantern Foundry")
        self.assertTrue(L.card_facts(facts).startswith("Claude Code · "), facts)


# The right column's measure (E2): its visible right edge (the client area, and never the last room px, where a scrollbar
# drawn over the content would sit), every element past it, its sideways scroll, and the run card's corners.
COLUMN_MEASURE = r"""((room) => {
  const col = document.querySelector('.answer'), r = col.getBoundingClientRect(), s = getComputedStyle(col);
  const scrolls = s.overflowY !== 'visible';
  const right = scrolls ? Math.min(r.left + col.clientLeft + col.clientWidth, r.right - room)
                        : r.left + col.clientLeft + col.clientWidth;
  const past = [...col.querySelectorAll('*')].filter(e => e.getClientRects().length)
    .map(e => [e.tagName.toLowerCase() + '.' + String(e.className), e.getBoundingClientRect().right - right])
    .filter(x => x[1] > 0.5);
  const c = document.querySelector('#runcard .rc').getBoundingClientRect();
  const top = scrolls ? r.top + col.clientTop : 0, bottom = scrolls ? top + col.clientHeight : innerHeight;
  return {scrolls, scrolls_tall: col.scrollHeight > col.clientHeight, past, scrollWidth: col.scrollWidth,
          clientWidth: col.clientWidth, gutter: s.scrollbarGutter, room: s.paddingRight,
          corners_inside: c.left >= r.left - 0.5 && c.right <= right + 0.5 && c.top >= top - 0.5 &&
                          c.bottom <= bottom + 0.5 && c.right <= innerWidth};
})"""


@unittest.skipUnless(TU.CHROME, "needs a headless Chrome on PATH")
class TestFormBrowser(Form):
    """The card on the form, in the suite's headless Chrome: at the top of the run's box, its button the form's own
    route, the keyboard there first after the heading; while the run goes, the live link in the same place."""

    def page(self, served):
        chrome = TU.Chrome(self.tmp / f"profile-{len(os.listdir(self.tmp))}")
        self.addCleanup(chrome.close)
        chrome.call("Page.enable")
        chrome.call("Page.bringToFront")
        chrome.call("Emulation.setFocusEmulationEnabled", enabled=True)
        chrome.call("Emulation.setEmulatedMedia", features=[{"name": "prefers-color-scheme", "value": "light"}])
        chrome.call("Page.navigate", url=f"http://127.0.0.1:{served.port}/?t={served.token}")
        self.assertTrue(TU.wait_for(lambda: chrome.js("!!document.getElementById('runsec') && "
                                                      "!document.getElementById('runsec').hidden"), 60))
        return chrome

    def drawn(self, chrome):
        return chrome.js("""(() => { const c = document.querySelector('#runcard > .rk'); if (!c) return null;
          return {cls: c.className, text: c.innerText, hrefs: [...c.querySelectorAll('a')].map(a => a.href),
                  links: [...c.querySelectorAll('a')].map(a => a.className), buttons: c.querySelectorAll('button')
                  .length}; })()""")

    def test_the_card_tops_the_runs_box_and_its_button_opens_the_page(self):
        out = self.copy("single")
        served = self.served(self.ended(says([out])))
        chrome = self.page(served)
        self.assertTrue(TU.wait_for(lambda: "rc-ready" in (self.drawn(chrome) or {}).get("cls", ""), 15))
        got = self.drawn(chrome)
        from urllib.parse import urlencode
        self.assertEqual(got["hrefs"], [f"http://127.0.0.1:{served.port}/results.html?"
                                        + urlencode({"t": served.token, "folder": U.shown(out)})])
        self.assertEqual(got["links"], ["rc-btn rc-open"])
        page = chrome.js(f"fetch({json.dumps(got['hrefs'][0])}).then(r => r.text())")
        self.assertEqual(page, (out / "results.html").read_text(encoding="utf-8"))
        self.assertTrue(chrome.js("document.getElementById('runcard').getBoundingClientRect().bottom <= "
                                  "document.getElementById('runbox').getBoundingClientRect().top"))
        self.assertEqual(chrome.js("document.querySelectorAll('#runbox a').length"), 0)   # no second link below
        chrome.js("document.querySelector('#runcard .rc-title').focus(); true")
        for kind in ("keyDown", "keyUp"):
            chrome.call("Input.dispatchKeyEvent", type=kind, key="Tab", code="Tab", windowsVirtualKeyCode=9,
                        nativeVirtualKeyCode=9)
        self.assertEqual(chrome.js("document.activeElement.className"), "rc-btn rc-open")
        time.sleep(2.5)                                       # refreshes leave the focused button where it is
        self.assertEqual(chrome.js("document.activeElement.className"), "rc-btn rc-open")

    def test_while_it_goes_the_live_link_is_there_in_the_secondary_shape(self):
        out = self.copy("single")
        r = says([out], ["--watch"], WATCH_URL="http://127.0.0.1:9/?t=abc", BEFORE=6)
        served = self.served(r)
        chrome = self.page(served)
        self.assertTrue(TU.wait_for(lambda: "rc-running" in (self.drawn(chrome) or {}).get("cls", ""), 15))
        got = self.drawn(chrome)
        self.assertEqual((got["hrefs"], got["links"]), (["http://127.0.0.1:9/?t=abc"], ["rc-btn rc-second"]))
        self.assertIn(L.CARD_WORDS["live"], got["text"])
        frame = chrome.js("document.querySelector('#runcard > .rk').getBoundingClientRect().top")
        shape = lambda q: chrome.js(f"(() => {{ const s = getComputedStyle(document.querySelector('{q}')); "  # noqa
                                    "return [s.borderTopWidth, s.borderTopStyle, s.fontWeight, s.borderRadius]; })()")
        live_shape = shape("#runcard a.rc-second")
        self.assertTrue(TU.wait_for(lambda: "rc-ready" in (self.drawn(chrome) or {}).get("cls", ""), 20))   # done
        self.assertEqual(shape("#runcard a.rc-open"), live_shape)          # the same shape, a pair in one place
        self.assertEqual(self.drawn(chrome)["links"], ["rc-btn rc-open"])
        self.assertEqual(chrome.js("document.querySelector('#runcard > .rk').getBoundingClientRect().top"), frame)

    def test_a_page_that_cannot_be_served_has_no_button(self):
        out = self.copy("single")
        served = self.served(self.ended(says([out], NO_PAGE=1)))
        chrome = self.page(served)
        self.assertTrue(TU.wait_for(lambda: "rc-nopage" in (self.drawn(chrome) or {}).get("cls", ""), 15))
        got = self.drawn(chrome)
        self.assertEqual((got["hrefs"], got["buttons"]), ([], 0))
        self.assertIn(L.CARD_WORDS["why"]["missing"], got["text"])

    def folds(self, chrome):
        """The Run details fold as drawn: whether it shows, whether it is open, its summary's words, and which run
        lines are inside it and which in the run's box."""
        return chrome.js("""(() => { const d = document.getElementById('rundetails');
          const where = id => [...document.getElementById(id).children].map(c => c.className || c.tagName.toLowerCase());
          return {shown: !d.hidden, open: d.open, summary: d.querySelector('summary').textContent,
                  inside: where('runlines'), box: where('runbox'),
                  text: document.getElementById('runlines').textContent}; })()""")

    def test_run_details_fold_what_the_card_already_says(self):
        """E3: under a finished card the status, the command and folder lines and the printed block are in one closed
        "Run details" fold, the form's own fold; none is gone, the keyboard reaches its summary after the card's
        button, and once opened it stays open across the form's refreshes."""
        out = self.copy("single")
        served = self.served(self.ended(says([out])))
        chrome = self.page(served)
        self.assertTrue(TU.wait_for(lambda: "rc-ready" in (self.drawn(chrome) or {}).get("cls", ""), 15))
        time.sleep(1.2)
        got = self.folds(chrome)
        self.assertEqual((got["shown"], got["open"]), (True, False))
        self.assertEqual(got["summary"], L.CARD_WORDS["details"] + L.CARD_WORDS["details_hint"])
        self.assertEqual(got["box"], [])                                       # nothing left outside the fold
        self.assertEqual(got["inside"], ["runbar", "kv", "kv", "kv", "kv", "kv", "out"])
        for words in ("finished", "started", "results folder", str(out), "scorecard", "report", "page"):
            self.assertIn(words, got["text"])
        self.assertEqual(chrome.js("getComputedStyle(document.querySelector('#rundetails > summary .sum')).fontSize"),
                         chrome.js("getComputedStyle(document.querySelector('#more > summary .sum')).fontSize"))
        chrome.js("document.querySelector('#runcard a.rc-open').focus(); true")
        for kind in ("keyDown", "keyUp"):
            chrome.call("Input.dispatchKeyEvent", type=kind, key="Tab", code="Tab", windowsVirtualKeyCode=9,
                        nativeVirtualKeyCode=9)
        self.assertEqual(chrome.js("document.activeElement.parentElement.id"), "rundetails")
        for kind in ("keyDown", "keyUp"):
            chrome.call("Input.dispatchKeyEvent", type=kind, key="Enter", code="Enter", windowsVirtualKeyCode=13,
                        nativeVirtualKeyCode=13, **({"text": "\r"} if kind == "keyDown" else {}))
        self.assertTrue(TU.wait_for(lambda: self.folds(chrome)["open"], 5))
        time.sleep(3.5)                                                          # three of the form's refreshes
        self.assertTrue(self.folds(chrome)["open"])
        self.assertEqual(chrome.js("document.activeElement.parentElement.id"), "rundetails")

    def test_no_fold_while_a_run_goes_or_after_a_failure(self):
        """E3: while the run goes, and when it failed, its lines stay open in the run's box as before."""
        out = self.copy("single")
        r = says([out], ["--watch"], WATCH_URL="http://127.0.0.1:9/?t=abc", BEFORE=30)
        served = self.served(r)
        chrome = self.page(served)
        self.assertTrue(TU.wait_for(lambda: "rc-running" in (self.drawn(chrome) or {}).get("cls", ""), 15))
        got = self.folds(chrome)
        self.assertEqual((got["shown"], got["inside"]), (False, []))
        self.assertIn("runbar", got["box"])
        r.stop()
        out = self.copy("single")
        served = self.served(self.ended(says([out], CODE=3)))
        chrome = self.page(served)
        self.assertTrue(TU.wait_for(lambda: "failed" in chrome.js("document.getElementById('runbox').innerText"), 15))
        got = self.folds(chrome)
        self.assertEqual((got["shown"], got["inside"], got["box"][0]), (False, [], "runbar"))

    def test_scored_keeps_stop_in_sight(self):
        """E3: scored with its live page held, the status and Stop stay outside the fold (Stop ends the held page);
        the other lines are in it."""
        out = self.copy("single")
        r = says([out], ["--watch"], WATCH_URL="http://127.0.0.1:9/?t=abc", HOLD=30)
        served = self.served(r)
        chrome = self.page(served)
        self.assertTrue(TU.wait_for(lambda: (self.folds(chrome) or {}).get("shown"), 15))
        got = self.folds(chrome)
        self.assertEqual(got["box"], ["runbar"])
        self.assertEqual(chrome.js("document.querySelector('#runbox .runbar button').textContent"), "Stop")
        self.assertEqual(got["inside"][0], "kv")
        r.stop()

    def test_the_right_column_cuts_nothing(self):
        """E2: with the run's printed lines tall enough to make the right column scroll once Run details is open (and a
        300-character unbroken token among them), finished, at 1280x900, 1024x768 and 820 wide, light and dark, the
        fold closed and open: (a) no element in the column
        reaches past its visible width (its client area, and never the last COLUMN_ROOM px, where a browser that draws
        its scrollbar over the content puts it), (b) the column does not scroll sideways, (c) the card's four corners
        are inside its visible area. The column keeps a stable gutter and that much room on its right."""
        out = self.copy("single")
        r = self.ended(says([out], TALL=40))
        served = self.served(r)
        chrome = self.page(served)
        for (w, h), scheme in [(size, scheme) for size in ((1280, 900), (1024, 768), (820, 1000))
                               for scheme in ("light", "dark")]:
            chrome.call("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=1, mobile=False)
            chrome.call("Emulation.setEmulatedMedia", features=[{"name": "prefers-color-scheme", "value": scheme}])
            chrome.call("Page.navigate", url=f"http://127.0.0.1:{served.port}/?t={served.token}")
            self.assertTrue(TU.wait_for(lambda: "rc-ready" in (self.drawn(chrome) or {}).get("cls", ""), 30))
            for details in ("closed", "open"):                    # open, the printed lines make the column scroll
                chrome.js(f"document.getElementById('rundetails').open = {str(details == 'open').lower()}; true")
                chrome.js("document.getElementById('runsec').scrollIntoView(); true")
                got = chrome.js(f"({COLUMN_MEASURE})({U.COLUMN_ROOM})")
                case = (w, h, scheme, details, got)
                self.assertEqual(got["past"], [], case)                              # (a)
                self.assertEqual(got["scrollWidth"], got["clientWidth"], case)       # (b)
                self.assertTrue(got["corners_inside"], case)                         # (c)
                if got["scrolls"]:
                    self.assertEqual((got["gutter"], got["room"]), ("stable", f"{U.COLUMN_ROOM}px"), case)
                    if details == "open":
                        self.assertTrue(got["scrolls_tall"], case)                   # the case is the one asked

    def test_a_failed_run_keeps_its_own_message_and_no_card(self):
        out = self.copy("single")
        served = self.served(self.ended(says([out], CODE=3)))
        chrome = self.page(served)
        self.assertTrue(TU.wait_for(lambda: "failed" in chrome.js("document.getElementById('runbox').innerText"), 15))
        self.assertIsNone(self.drawn(chrome))


# ---- round 3 (results-card-round3.md): the recheck fails closed (F1), the 2-second limit on both pages (F8), the
# card's facts off the form's lock (F7)
class Clock:
    """A clock the test moves (RunView.clock, Runner.clock): later() steps past one recheck's window."""

    def __init__(self, start=1000.0):
        self.t = start

    def __call__(self):
        return self.t

    def later(self, seconds=L.CARD_RECHECK_SECONDS + 0.1):
        self.t += seconds


class Reader:
    """ui.read_results_page counted by folder, and made to raise raises when it is given (a RuntimeError, not one of
    the OSError and ValueError the reader catches itself)."""

    def __init__(self, raises=None):
        self.real, self.raises, self.calls = U.read_results_page, raises, []

    def __call__(self, folder, identity, page=U.SINGLE_PAGE):
        self.calls.append(folder)
        if self.raises is not None:
            raise self.raises("a reader that raises (the test's)")
        return self.real(folder, identity, page)

    def of(self, folder):
        return self.calls.count(os.path.abspath(folder))


def tracebacks(text):
    return text.count("Traceback (most recent call last)")


class TestPageCheck(unittest.TestCase):
    """F1, F6: the one helper both pages ask (live.PageCheck)."""

    def test_any_exception_fails_closed_is_told_once_and_is_asked_again_at_its_own_pace(self):
        check, err, asked = L.PageCheck(), io.StringIO(), []

        def boom():
            asked.append(1)
            raise RuntimeError("boom")
        with contextlib.redirect_stderr(err):
            self.assertEqual(check.ask(10.0, boom, lambda: ["scorecard.md"], force=True), "refused")
            self.assertEqual(check.ask(11.0, boom), "refused")                 # inside the window: not asked
            self.assertEqual(check.ask(12.2, boom), "refused")                 # after it: asked again
            self.assertEqual(check.ask(12.3, boom, force=True), "refused")     # forced: asked now
        self.assertEqual(len(asked), 3)
        self.assertEqual(tracebacks(err.getvalue()), 1)                         # once for the run, not per request
        self.assertIn("RuntimeError: boom", err.getvalue())
        self.assertEqual(check.files, ["scorecard.md"])

    def test_it_catches_what_the_request_handler_catches(self):
        """P1: its guards catch exactly what serve_page's attempt() catches, read from attempt()'s own except
        clause, so no code a route calls can end the server; a SystemExit fails closed like any exception, and a
        KeyboardInterrupt still ends the process."""
        tree = ast.parse(Path(L.__file__).read_text(encoding="utf-8"))
        attempt = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "attempt")
        caught = next(h.type for h in ast.walk(attempt) if isinstance(h, ast.ExceptHandler))
        names = tuple(e.id for e in caught.elts) if isinstance(caught, ast.Tuple) else (caught.id,)
        self.assertEqual(tuple(c.__name__ for c in L.PageCheck.CAUGHT), names)
        check, err = L.PageCheck(), io.StringIO()

        def leaves():
            raise SystemExit("a reader that exits")
        with contextlib.redirect_stderr(err):
            self.assertEqual(check.ask(0.0, leaves, leaves, force=True), "refused")
            self.assertEqual(check.ask(0.5, leaves), "refused")              # inside the window: not asked
        self.assertEqual((check.files, tracebacks(err.getvalue())), ([], 1))
        self.assertIn("SystemExit: a reader that exits", err.getvalue())

        def interrupted():
            raise KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            L.PageCheck().ask(0.0, interrupted)

    def test_an_answer_that_is_none_of_the_three_is_refused_and_the_files_are_listed_once(self):
        check, listed = L.PageCheck(), []
        self.assertEqual(check.ask(0.0, lambda: None, lambda: listed.append(1) or ["a"]), "refused")
        self.assertEqual(check.ask(5.0, lambda: "ready", lambda: listed.append(1) or ["b"]), "ready")
        self.assertEqual((check.files, len(listed)), (["a"], 1))
        self.assertEqual(L.PageCheck().ask(0.0, lambda: "missing"), "missing")

    def test_the_text_files_follow_no_link(self):
        """F6: each fixed name is looked at without following a link: a link to a file, or a subfolder that is a link,
        lists nothing."""
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        out = tmp / "run"
        out.mkdir()
        (tmp / "elsewhere.md").write_text("x")
        (out / "scorecard.md").symlink_to(tmp / "elsewhere.md")
        (out / "report.md").write_text("r")
        self.assertEqual(L.text_files(out, U.SINGLE_PAGE), ["report.md"])
        (tmp / "other").mkdir()
        (tmp / "other" / "scorecard.md").write_text("s")
        (out / "ordo").symlink_to(tmp / "other")
        self.assertEqual(L.text_files(out, U.ORDO_PAGE), [])
        link = tmp / "link"
        link.symlink_to(out)
        self.assertEqual(L.text_files(link, U.SINGLE_PAGE), [])                # the run's folder itself a link


class Round3(Form):
    """Planted runs on both pages, with the suite's planters and the stand-in command (Form), and the --watch page's
    own (TestLiveCard's way: single_plan over the planted run.json)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.meta = json.loads((cls.single / "run.json").read_text(encoding="utf-8"))

    def watch_runs(self, names, clock):
        w = L.Watch(list(names), stream=io.StringIO())
        self.addCleanup(w.close)
        outs, views = [], []
        for name in names:
            out = self.copy("single")
            calls, facts, kind = single_plan(self.meta)
            view = w.start(name, out, calls, facts, kind, Clocked(calls).line)
            view.clock = clock
            outs.append(out)
            views.append(view)
        return w, outs, views


class TestFailsClosed(Round3):
    """F1, P1: a reader that raises a RuntimeError, or a SystemExit (what the request handler's attempt() also
    catches), on both pages: Watch.finish does not raise, /state answers 200 with every run's card, the page is not
    offered and the card says it could not be read, one traceback on standard error per run, one attempt per
    window."""

    def test_the_watch_page(self):
        self.watch_page(RuntimeError)

    def test_the_watch_page_with_a_reader_that_exits(self):
        self.watch_page(SystemExit)

    def test_the_form(self):
        self.form(RuntimeError)

    def test_the_form_with_a_reader_that_exits(self):
        self.form(SystemExit)

    def watch_page(self, raises):
        reader, err, clock = Reader(raises), io.StringIO(), Clock()
        with mock.patch.object(U, "read_results_page", reader), contextlib.redirect_stderr(err):
            w, (a, b), (va, vb) = self.watch_runs(["A (organization)", "B (organization)"], clock)
            w.finish(va)                                         # the forced check raises; finish does not
            w.finish(vb)
            client = TLF.client_of(w)
            for _ in range(20):
                res, text = client.request("GET", "/state")
                self.assertEqual(res.status, 200)
            runs = json.loads(text)["runs"]
            for run in runs:
                self.assertEqual(card_links(run["card"]), [])
                self.assertIn(L.CARD_WORDS["why"]["refused"], list(texts(run["card"])))
                self.assertEqual((run["results"], run["bar"]), (None, None))
            self.assertEqual((reader.of(a), reader.of(b)), (1, 1))
            clock.later()
            self.assertEqual(client.request("GET", "/state")[0].status, 200)
            self.assertEqual((reader.of(a), reader.of(b)), (2, 2))
            self.assertEqual(client.request("GET", "/results.html?run=0")[0].status, 404)
        self.assertEqual(tracebacks(err.getvalue()), 2)                         # one for each run, once
        self.assertIn(f"{raises.__name__}: a reader that raises", err.getvalue())

    def form(self, raises):
        reader, err, clock = Reader(raises), io.StringIO(), Clock()
        a, b = self.copy("single"), self.copy("single")
        r = self.ended(says([a, b]))
        r.clock = clock
        served = self.served(r)
        with mock.patch.object(U, "read_results_page", reader), contextlib.redirect_stderr(err):
            for _ in range(20):
                run = served.state()["run"]                       # Served.state asserts the 200
            self.assertEqual(card_links(run["card"]), [])
            self.assertIn(L.CARD_WORDS["why"]["refused"], list(texts(run["card"])))
            self.assertIn("finished · " + L.CARD_WORDS["no_page"], list(texts(run["card"])))
            self.assertEqual((reader.of(a), reader.of(b)), (1, 1))
            clock.later()
            served.state()
            self.assertEqual((reader.of(a), reader.of(b)), (2, 2))
        self.assertEqual(tracebacks(err.getvalue()), 2)
        self.assertIn(f"{raises.__name__}: a reader that raises", err.getvalue())


class TestTwoSecondLimit(Round3):
    """F8: with a clock the test moves and a reader that counts, many /state requests inside the window make one
    pinned read per run, and one more after it; on the form, a page taken away after its run is withdrawn after the
    next recheck, and one put back is offered again."""

    def test_the_watch_page_reads_once_per_run_per_window(self):
        reader, clock = Reader(), Clock()
        with mock.patch.object(U, "read_results_page", reader):
            w, outs, views = self.watch_runs(["A (organization)", "B (organization)", "C (organization)"], clock)
            for view in views:
                w.finish(view)
            client = TLF.client_of(w)
            self.assertEqual([reader.of(o) for o in outs], [1, 1, 1])           # the forced check at scoring
            for _ in range(30):
                self.assertEqual(client.request("GET", "/state")[0].status, 200)
            self.assertEqual([reader.of(o) for o in outs], [1, 1, 1])           # none more inside the window
            clock.later()
            for _ in range(30):
                client.request("GET", "/state")
            self.assertEqual([reader.of(o) for o in outs], [2, 2, 2])           # one more each after it
            runs = json.loads(client.request("GET", "/state")[1])["runs"]
            self.assertEqual([card_links(r["card"])[0] for r in runs], [{"run": 0}, {"run": 1}, {"run": 2}])

    def test_the_form_reads_once_per_folder_per_window_and_follows_the_folder(self):
        reader, clock = Reader(), Clock()
        a, b = self.copy("single"), self.copy("single")
        r = self.ended(says([a, b]))
        r.clock = clock
        served = self.served(r)
        offered = lambda: card_links(served.state()["run"]["card"])          # noqa: E731
        both = [{"folder": U.shown(a)}, {"folder": U.shown(b)}]
        with mock.patch.object(U, "read_results_page", reader):
            for _ in range(30):
                served.state()
            self.assertEqual((reader.of(a), reader.of(b)), (1, 1))
            clock.later()
            for _ in range(30):
                served.state()
            self.assertEqual((reader.of(a), reader.of(b)), (2, 2))
            self.assertEqual(offered(), both)
            (a / "results.html").rename(a / "away.html")                       # taken away after the run
            self.assertEqual(offered(), both)                                   # inside the window: as it was
            clock.later()
            card = served.state()["run"]["card"]
            self.assertEqual(card_links(card), [{"folder": U.shown(b)}])        # withdrawn after the next recheck
            self.assertIn(L.CARD_WORDS["why"]["missing"], list(texts(card)))
            self.assertEqual(served.request("GET", "/results.html?folder=" + quote(U.shown(a), safe=""),
                                            header=False)[0].status, 404)
            (a / "away.html").rename(a / "results.html")                       # put back
            self.assertEqual(offered(), [{"folder": U.shown(b)}])
            clock.later()
            self.assertEqual(offered(), both)                                   # offered again
            self.assertEqual((reader.of(a), reader.of(b)), (5, 4))   # four rechecks each, and the route's own read


class TestFactsOffTheLock(TU.Stubbed):
    """F7: Run starts the run, then makes the card's facts (run_card_facts: the command parsed and planned again)
    outside the form's lock. With that planner made slow, the form's lock is free while the facts are pending, /state
    and /stop answer at once, and the card shows its facts when they arrive. test_ui's stub run (results lines, its
    live page held until Stop); no probatio run, no agent."""

    def setUp(self):
        super().setUp()
        here = mock.patch.object(P, "HERE", self.tmp)
        here.start()
        self.addCleanup(here.stop)
        [(self.folder, _)] = U.prepared(U.build_command(TU.MOZILLA)[0])
        self.env.update(STUB_FOLDER=self.folder.name, STUB_FINISH="1")
        self.s = TU.Served(U.Session(program=self.program, env=self.env))

    def tearDown(self):
        self.s.close()
        super().tearDown()

    def timed(self, call):
        t = time.monotonic()
        got = call()
        return got, time.monotonic() - t

    def test_state_and_stop_answer_while_the_facts_are_planned(self):
        gate, began, real, replies = threading.Event(), threading.Event(), U.run_card_facts, []

        def slow(argv, count):                                    # the planner made slow, until the test lets go
            began.set()
            gate.wait(120)
            return real(argv, count)
        title = lambda: having(self.s.state()["run"]["card"], "rc-title")[0]["kids"]      # noqa: E731
        with mock.patch.object(U, "run_card_facts", slow):
            self.assertTrue(self.s.post("/check", {"form": TU.MOZILLA})["ok"])
            run = threading.Thread(target=lambda: replies.append(
                self.s.post("/run", {"form": TU.MOZILLA, "confirmed": True})))
            run.start()
            self.addCleanup(gate.set)
            self.assertTrue(began.wait(60))
            self.assertFalse(self.s.session.lock.locked())        # the facts are planned outside the form's lock
            self.assertTrue(TU.wait_for(lambda: self.s.state()["run"]["status"] == "scored", 30))
            state, took = self.timed(self.s.state)
            self.assertLess(took, 1.0)
            self.assertEqual(having(state["run"]["card"], "rc-title")[0]["kids"], [""])   # pending: no title yet
            _, took = self.timed(lambda: self.s.post("/stop", {}))
            self.assertLess(took, 1.0)
            self.assertTrue(TU.wait_for(lambda: self.s.state()["run"]["status"] == "finished", 30))
            self.assertEqual(title(), [""])
            gate.set()
            run.join(120)
        self.assertNotIn("error", replies[0])
        self.assertEqual(title(), ["Scorecard: Mozilla (organization)"])
        self.assertTrue(L.card_facts(self.s.session.runner.facts[0]).startswith("Claude Code"))


# ---- round 3, F3: the card drawn as text on both pages, in the suite's headless Chrome
MARKS = {   # markup in every string the card shows: elements with ids, an img whose onerror would run, a link
    "title": '<b id="rcx-title">T</b><img id="rcx-img" src="x" onerror="window.RCX=1"><a id="rcx-a" href="#rcx">L</a>',
    "model": '<i id="rcx-model">M</i><img id="rcx-mimg" src="x" onerror="window.RCX=2">',
    "folder": '<b id="rcx-folder">F</b><img id="rcx-fimg" src=x onerror=window.RCX=3>',
    "other": '<u id="rcx-other">O</u><a id="rcx-oa" href="#o">L</a>'}
INJECTED = "[id^='rcx-'], img, b, i, u, a:not([class^='rc-'])"     # nothing the card draws is one of these
DRAWN = """(() => { const c = document.querySelector(BOX + ' .rc'), out = {};
  for (const box of BOXES) { const e = document.querySelector(box);
    out[box] = e ? [...e.querySelectorAll(INJECTED)].map(x => x.outerHTML.slice(0, 80)) : null; }
  out.ran = window.RCX === undefined ? null : window.RCX;
  out.title = c.querySelector('.rc-title').textContent; out.facts = c.querySelector('.rc-facts').textContent;
  out.path = c.querySelector('.rc-path').textContent;
  out.others = [...c.querySelectorAll('.rc-otitle')].map(x => x.textContent);
  return out; })()"""


def drawn_text(chrome, box, boxes):
    return chrome.js(DRAWN.replace("BOXES", json.dumps(boxes)).replace("BOX", json.dumps(box))
                     .replace("INJECTED", json.dumps(INJECTED)))


def node_text(tree, cls):
    return [k for n in having(tree, cls) for k in n["kids"] if isinstance(k, str)]


@unittest.skipUnless(TU.CHROME, "needs a headless Chrome on PATH")
class TestDrawnAsText(Round3):
    """F3: on each page, a card whose title, facts line, folder and other runs' names hold markup draws every one of
    them as text: no element of that markup exists inside the card or the bar, no script of it runs, and each drawn
    string is the state's string, character for character."""

    def chrome_at(self, url, ready):
        chrome = TU.Chrome(self.tmp / f"profile-{len(os.listdir(self.tmp))}")
        self.addCleanup(chrome.close)
        chrome.call("Page.enable")
        chrome.call("Emulation.setEmulatedMedia", features=[{"name": "prefers-color-scheme", "value": "light"}])
        chrome.call("Page.navigate", url=url)
        self.assertTrue(TU.wait_for(lambda: chrome.js(ready), 30))
        return chrome

    def marked_folder(self):
        out = Path(tempfile.mkdtemp(dir=self.tmp)) / MARKS["folder"]
        shutil.copytree(self.single, out, symlinks=True)
        return out

    def assert_as_text(self, got, card, boxes):
        for box in boxes:
            self.assertEqual(got[box] or [], [], box)                           # no element of the markup in it
        self.assertIsNone(got["ran"])                                           # no script of it ran
        self.assertEqual(got["title"], node_text(card, "rc-title")[0])
        self.assertEqual(got["facts"], node_text(card, "rc-facts")[0])
        self.assertEqual(got["path"], node_text(card, "rc-path")[0])
        self.assertEqual(got["others"], node_text(card, "rc-otitle"))
        self.assertIn(MARKS["title"], got["title"])
        self.assertIn(MARKS["model"], got["facts"])
        self.assertIn(MARKS["folder"], got["path"])

    def test_the_watch_page(self):
        b = P.load_bank(P.BANK, P.BANK_MANIFEST_SHA256)
        roster = copy.deepcopy(P.resolve_target(b, "organization", "Mozilla"))
        roster["T"]["full"] = MARKS["title"]
        calls, _, _ = single_plan(self.meta)
        out = self.marked_folder()
        w = L.Watch(["A", MARKS["other"]], stream=io.StringIO())
        self.addCleanup(w.close)
        view = w.start("A", out, calls, L.run_facts(dict(self.meta, model=MARKS["model"]), "single", roster),
                       F.SingleKind(roster), Clocked(calls).line)
        w.finish(view)
        card = json.loads(TLF.client_of(w).request("GET", "/state")[1])["runs"][0]["card"]
        self.assertEqual(node_text(card, "rc-otitle"), [MARKS["other"]])       # the waiting run, by its name
        chrome = self.chrome_at(w.server.url, "!!document.querySelector('#card .rc')")
        self.assert_as_text(drawn_text(chrome, "#card", ["#card", "#bar"]), card, ["#card", "#bar"])
        chrome.js("window.scrollTo(0, document.documentElement.scrollHeight); true")        # the bar, further down
        self.assertTrue(TU.wait_for(lambda: chrome.js("!!document.querySelector('#bar .rc-bar')"), 20))
        self.assert_as_text(drawn_text(chrome, "#card", ["#card", "#bar"]), card, ["#card", "#bar"])

    def test_the_form(self):
        out, second = self.marked_folder(), self.copy("single")
        r = self.ended(says([out, second]))
        r.facts = [dict(FACTS, target=MARKS["title"], model=MARKS["model"]), dict(FACTS, target=MARKS["other"])]
        served = self.served(r)
        card = served.state()["run"]["card"]
        self.assertTrue(node_text(card, "rc-otitle")[0].startswith("Scorecard: " + MARKS["other"]))
        chrome = self.chrome_at(f"http://127.0.0.1:{served.port}/?t={served.token}",
                                "!!document.querySelector('#runcard .rc')")
        self.assert_as_text(drawn_text(chrome, "#runcard", ["#runcard"]), card, ["#runcard"])



# ---- round 3, F10: a folder breaks where a path breaks
LINES = """(sel) => { const e = document.querySelector(sel); if (!e) return null;
  const lines = [], walker = document.createTreeWalker(e, NodeFilter.SHOW_TEXT); let n, top = null, cur = '';
  while ((n = walker.nextNode())) for (let i = 0; i < n.data.length; i++) {
    const r = document.createRange(); r.setStart(n, i); r.setEnd(n, i + 1); const t = r.getClientRects()[0].top;
    if (top !== null && t > top + 1) { lines.push(cur); cur = ''; } top = t; cur += n.data[i]; }
  lines.push(cur); return {lines: lines, text: e.textContent, wbr: e.querySelectorAll('wbr').length,
    rule: [getComputedStyle(e).wordBreak, getComputedStyle(e).overflowWrap]}; }"""
HYPHENATED = "v4-claude-organization-lantern-foundry-quick-memory-on"      # a folder like a real one's, with hyphens


@unittest.skipUnless(TU.CHROME, "needs a headless Chrome on PATH")
class TestFolderBreaks(Round3):
    """F10: the card's folder (on both pages) and the form's folder lines in Run details wrap with word-break normal
    and overflow-wrap anywhere, a <wbr> after each "/" between text nodes, so every line but the last ends at a "/" or
    a "-" when each piece fits; the text is the folder, whole, and no character was added to it."""

    def folder(self):
        out = Path(tempfile.mkdtemp(dir=self.tmp)) / "loyalty-evals" / "results" / HYPHENATED
        out.parent.mkdir(parents=True)
        shutil.copytree(self.single, out, symlinks=True)
        return out

    def chrome_at(self, url, ready, width=390):
        chrome = TU.Chrome(self.tmp / f"profile-{len(os.listdir(self.tmp))}")
        self.addCleanup(chrome.close)
        chrome.call("Page.enable")
        chrome.call("Emulation.setDeviceMetricsOverride", width=width, height=844, deviceScaleFactor=1, mobile=True)
        chrome.call("Page.navigate", url=url)
        self.assertTrue(TU.wait_for(lambda: chrome.js(ready), 30))
        return chrome

    def assert_path_breaks(self, got, shown):
        self.assertIsNotNone(got)
        self.assertEqual(got["text"], shown)
        self.assertEqual(got["rule"], ["normal", "anywhere"])
        self.assertEqual(got["wbr"], shown.count("/"))
        self.assertEqual("".join(got["lines"]), shown)
        self.assertGreater(len(got["lines"]), 1, got)                       # the case is one that wraps
        for line in got["lines"][:-1]:
            self.assertTrue(line.endswith(("/", "-")), got["lines"])         # never inside a word

    def test_the_watch_pages_card(self):
        out = self.folder()
        w = L.Watch(["A"], stream=io.StringIO())
        self.addCleanup(w.close)
        calls, facts, kind = single_plan(self.meta)
        w.finish(w.start("A", out, calls, facts, kind, Clocked(calls).line))
        (shown,) = node_text(json.loads(TLF.client_of(w).request("GET", "/state")[1])["runs"][0]["card"], "rc-path")
        chrome = self.chrome_at(w.server.url, "!!document.querySelector('#card .rc-path')")
        self.assert_path_breaks(chrome.js(f"({LINES})('#card .rc-path')"), shown)

    def test_the_forms_card_and_its_folder_line(self):
        out = self.folder()
        served = self.served(self.ended(says([out])))
        (shown,) = node_text(served.state()["run"]["card"], "rc-path")
        chrome = self.chrome_at(f"http://127.0.0.1:{served.port}/?t={served.token}",
                                "!!document.querySelector('#runcard .rc-path')")
        self.assert_path_breaks(chrome.js(f"({LINES})('#runcard .rc-path')"), shown)
        chrome.js("document.getElementById('rundetails').open = true; true")
        keys = chrome.js("""(() => [...document.querySelectorAll('#runlines .kv')].map((k, i) => {
          k.querySelector('code').id = 'rcx-line-' + i; return k.firstChild.textContent.trim(); }))()""")
        paths = {"results folder": [shown], "scorecard": [], "report": [], "page": []}
        for kind, path in served.state()["run"]["results"]:
            paths[kind.lower()].append(path)
        checked = []
        for i, key in enumerate(keys):                  # G2: every path line in Run details, the folder's and each
            if key not in paths:                        # result's, breaks where a path breaks
                continue
            got = chrome.js(f"({LINES})('#rcx-line-{i}')")
            self.assertIn(got["text"], paths[key], key)
            self.assertEqual(got["wbr"], got["text"].count("/"), key)
            for piece in got["lines"][:-1]:
                self.assertTrue(piece.endswith(("/", "-")), (key, got["lines"]))
            checked.append(key)
        self.assertEqual(sorted(checked), ["page", "report", "results folder", "scorecard"])


if __name__ == "__main__":
    unittest.main()
