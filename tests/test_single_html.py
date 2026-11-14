"""results.html of a single-target run (single_html.py; the results-pages ruling, P1 to P6, P8, P11), on real folders.

Every folder here is planted and scored as a run writes it (tests/test_v4_scoring.planted_run, then probatio.score_run,
which writes the seven files through write_scorecard): the three target types; the quick, standard and full tiers;
memory on, off and both; --class II; a custom roster's labels; a hostile target name (planned from the roster, as a
folder made by hand would be, since the command line's name guard refuses it); a --rescore, twice; a legacy quick
folder rescored. What each page must hold:
  - every line of scorecard.md's fenced block and of its Notes is on the page word for word, once: the head line as
    the run line, the rest in the highlighted box, the notes as a list;
  - its tables are scorecard.md's, cell for cell (Components, Red flags), its validity checks and diagnostics the
    text page's lines; every other line of text is a scorecard.md line, part or cell, or one of single_html's or the
    kit's structural words;
  - every number is the card's: the class scores and intervals of the teaser and of "By class" computed again here
    from scorecard.json with an independent half-up rounding, and every drawn bar where its printed number says;
  - the README's status sentence and chip are passages of README.md, the setup note a passage of report.md;
  - a hostile name is text, the data block inert, no external reference, the CSP present, the tables open without
    scripts, the same inputs give the same bytes.
Whatever else a folder holds (scorecard.md, report.md, results.csv, results.svg, units.jsonl, scorecard.json) is byte
for byte what 6e55631 writes: that is step 1's corpus gate, run outside the suite against the base's own files. No
network, no model.

    python3 -m unittest tests.test_single_html
"""
import functools
import html.parser
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))
import probatio as P  # noqa: E402
import results_kit as K  # noqa: E402
import single_html as S  # noqa: E402
import test_v4_scoring as TV  # noqa: E402

B = 200
TMP = Path(tempfile.mkdtemp(prefix="single-html-"))
HOSTILE = '<script>alert(1)</script> "><img src=x onerror=alert(1)> \x1b[2J Nor‮land </script><!-- &'
LABELS = ["custom roster: not calibrated", "controls: Harrowgate Instruments (unread)"]
NOW = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)


def tearDownModule():
    shutil.rmtree(TMP, True)


# ---- the folders -----------------------------------------------------------------------------------------------------
@functools.lru_cache(maxsize=None)
def folder(target=("organization", "Mozilla"), tier="standard", memory="on", classes=tuple(P.V4_CLASSES),
           labels=False):
    """A planted run scored as score_run writes it; cached (a folder takes a second or two)."""
    out = Path(tempfile.mkdtemp(dir=TMP)) / "run"
    b, roster, calls, meta = TV.planted_run(out, tier=tier, memory=memory, classes=list(classes), target=target)
    if labels:
        meta = dict(meta, labels=list(LABELS))
        (out / "run.json").write_text(json.dumps(meta, indent=2))
    P.score_run(out, calls, b, roster, meta, B=B)
    return out


def hostile_folder():
    """A folder planned for a hostile organization name from the roster (the command line refuses the name)."""
    out = Path(tempfile.mkdtemp(dir=TMP)) / "run"
    b = TV.bank()
    roster = P.resolve_target(b, "organization", HOSTILE, HOSTILE, HOSTILE + "'s")
    calls = P.plan_v4(b, roster, "quick", P.V4_CLASSES, P.MEMORY_MODES["on"])
    TV.plant(out, calls, TV.favour(0.8, 0.4), TV.errand_favour(0.9, 0.3))
    meta = TV.run_meta(b, roster, calls, "quick", "on", P.V4_CLASSES)
    (out / "run.json").write_text(json.dumps(meta, indent=2))
    P.score_run(out, calls, b, roster, meta, B=B)
    return out


CASES = {   # label: folder arguments
    "organization standard on": {},
    "individual quick both": {"target": ("individual", "Jane Doe"), "tier": "quick", "memory": "both"},
    "nation full off": {"target": ("nation", "France"), "tier": "full", "memory": "off"},
    "organization --class II": {"classes": ("II",)},
    "custom roster labels": {"tier": "quick", "labels": True},
}


# ---- reading a page back ---------------------------------------------------------------------------------------------
class Page(html.parser.HTMLParser):
    """A page read back: tags with attributes, text blocks (tag, class, text) of the elements that hold text, tables
    as rows of cells, the data block, the other scripts, and whether each details element is open."""
    BLOCKS = {"h1", "h2", "h3", "h4", "p", "li", "caption", "summary", "th", "td", "span", "div"}

    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.tags, self.blocks, self.tables, self.scripts, self.data = [], [], [], [], None
        self.stack, self.open, self.script, self.cell = [], [], None, None
        self.feed(text)
        self.close()

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        self.tags.append((tag, a))
        if tag in ("meta", "link"):
            return
        self.stack.append(tag)
        if tag == "script":
            self.script = (a.get("type"), [])
        if tag in self.BLOCKS:
            self.open.append([tag, a.get("class") or "", [], len(self.stack), False])
        if tag == "table":
            self.tables.append([])
        elif tag == "tr":
            self.tables[-1].append([])
        elif tag in ("th", "td"):
            self.cell = []

    def handle_endtag(self, tag):
        if tag == "script" and self.script:
            kind, parts = self.script
            (setattr(self, "data", "".join(parts)) if kind == "application/json" else
             self.scripts.append("".join(parts)))
            self.script = None
        if self.open and self.open[-1][3] == len(self.stack):
            t, cls, parts, _, nested = self.open.pop()
            if not nested:                          # a block holding another block is read through its children
                self.blocks.append((t, cls, "".join(parts)))
        if tag in ("th", "td") and self.cell is not None:
            self.tables[-1][-1].append("".join(self.cell))
            self.cell = None
        if self.stack:
            self.stack.pop()

    def handle_data(self, data):
        if self.script:
            self.script[1].append(data)
            return
        if "style" in self.stack:
            return
        if data.strip() and self.open:
            for b in self.open[:-1]:
                b[4] = True
            self.open[-1][2].append(data)
        elif self.open:
            self.open[-1][2].append(data)
        if self.cell is not None:
            self.cell.append(data)


def read(out):
    return (out / "results.html").read_text(encoding="utf-8")


def md_parts(md):
    """scorecard.md as parts: its fenced block's lines, each table's rows of cells (by heading), each list's items
    (by heading), and its closing sentence."""
    lines = md.split("\n")
    a, b = lines.index("```text"), lines.index("```", lines.index("```text") + 1)
    block = lines[a + 1:b]
    tables, lists, heading = {}, {}, None
    for line in lines[b + 1:]:
        if line.startswith("## "):
            heading = line[3:]
        elif line.startswith("| ") and not line.startswith("|---"):
            tables.setdefault(heading, []).append(line[2:-2].split(" | "))
        elif line.startswith("- "):
            lists.setdefault(heading, []).append(line[2:])
    return block, tables, lists, [x for x in lines if x.startswith("Scores are relative")][0]


def r(x):
    """Half up to a whole number, independently of probatio.whole."""
    return int(Decimal(repr(float(x))).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def plain_md(text):
    """Markdown as plain words: bold and code marks and links removed, whitespace as single spaces."""
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text.replace("**", "").replace("`", ""))
    return " ".join(text.split())


ALLOWED_TAGS = {"html", "head", "meta", "link", "title", "style", "body", "main", "header", "footer", "h1", "h2", "p",
                "div", "span", "ul", "li", "figure", "details", "summary", "table", "caption", "thead", "tbody", "tr",
                "th", "td", "b", "script"}


class Checks(unittest.TestCase):
    def check(self, out, label):
        """Everything every single-target page must hold, for one folder."""
        text = read(out)
        pg = Page(text)
        md = (out / "scorecard.md").read_text(encoding="utf-8")
        card = json.loads((out / "scorecard.json").read_text(encoding="utf-8"))
        block, tables, lists, closing = md_parts(md)
        self.check_safe(text, pg, label)
        self.check_lines(pg, block, lists, label)
        self.check_tables(pg, tables, lists, label)
        self.check_prose(pg, md, label, S.plain(closing))
        self.check_numbers(pg, card, label)
        self.check_drawing(pg, label)
        self.assertIn(S.plain(closing), [b[2] for b in pg.blocks], label)
        return text, pg

    def check_safe(self, text, pg, label):
        csp = [a for t, a in pg.tags if t == "meta" and a.get("http-equiv") == "Content-Security-Policy"]
        self.assertEqual([a["content"] for a in csp], [K.CSP], label)
        for t, a in pg.tags:
            self.assertIn(t, ALLOWED_TAGS, (label, t))
            self.assertNotIn("src", a, label)
            self.assertFalse([k for k in a if k.startswith("on")], label)
            for k, v in a.items():
                self.assertFalse(v and ("http" in v or "//" in v or "url(" in v), (label, t, k, v))
            if "href" in a:
                self.assertEqual((t, a), ("link", {"rel": "icon", "href": "data:,"}), label)
            if t == "details" and "numbers" in (a.get("class") or ""):
                self.assertIn("open", a, label)
        self.assertEqual(pg.scripts, [K.SCRIPT], label)
        self.assertFalse(set("<>&") & set(pg.data), label)
        json.loads(pg.data)
        self.assertFalse([ch for ch in text if ch != "\n" and P.unseen(ch)], label)

    def check_lines(self, pg, block, lists, label):
        """Every line of the fenced block and of Notes on the page word for word, once: the head line as the run line,
        the others as the box's lines, the notes as the notes list."""
        runline = [b[2] for b in pg.blocks if b[1] == "runline"]
        self.assertEqual(runline, [block[0]], label)
        box = [b[2] for b in pg.blocks if b[1] == "ln"]
        self.assertEqual(box, [x for x in block[1:] if x], label)
        texts = [b[2] for b in pg.blocks]
        for line in [x for x in block if x.strip()]:
            if line not in ("memory on", "memory off"):                 # a condition's name is a table cell too
                self.assertEqual(texts.count(line), block.count(line), (label, line))   # as often as it prints
        notes = [b[2] for b in pg.blocks if b[0] == "li"]
        for n in lists.get("Notes", []):
            self.assertEqual(notes.count(n), 1, (label, n))

    def check_tables(self, pg, tables, lists, label):
        """The page's tables are scorecard.md's, cell for cell; its validity checks and diagnostics the text page's."""
        page_tables = [t for t in pg.tables]
        comps = next(t for t in page_tables if t and t[0] == list(P.COMPONENT_COLUMNS))
        self.assertEqual(comps, tables["Components"], label)
        flags = next(t for t in page_tables if t and t[0] == list(P.FLAG_COLUMNS))
        self.assertEqual(flags, tables["Red flags"], label)
        checks = next(t for t in page_tables if t and t[0][0] == "condition" and t[0][1:2] == ["V1"])
        self.assertEqual([f"{row[0]}: " + "; ".join(f"{v} {w}" for v, w in zip(checks[0][1:], row[1:]))
                          for row in checks[1:]], lists["Validity checks"], label)
        items = [b[2] for b in pg.blocks if b[0] == "li"]
        for line in lists["Diagnostics (shown, never scored)"]:
            self.assertIn(line, items, label)

    def check_prose(self, pg, md, label, closing):
        """Every text block is a scorecard.md line, part or cell, or a structural word of the page or the kit."""
        md_text = md
        own = {closing} | {getattr(S, k) for k in dir(S) if k.isupper() and isinstance(getattr(S, k), str)} | {
            getattr(K, k) for k in ("SHOW_NUMBERS", "SETUP", "PAGE_IS_VIEW")} | {
            "What this card's identity holds", P.DIFFERENCE_HEAD, P.HOW_TO_READ_V4, "condition", "class", "score",
            "interval", "raw", "status", "bootstrap B", "bootstrap seed", "memory on", "memory off"} | set(
            S.CLASS_COLUMNS) | {label_ for label_, _ in S.IDENTITY} | set(P.CLASS_NAMES.values()) | {
            f"Class {k}" for k in P.V4_CLASSES} | {f"Class {k} {n}" for k, n in P.CLASS_NAMES.items()}
        for tag, cls, line in pg.blocks:
            if not line.strip() or tag in ("td", "th") or "pill" in cls:
                continue
            if tag == "h1":
                self.assertTrue(line.startswith("Scorecard: "), label)
                continue
            ok = line in own or line in md_text or S.plain(line) in md_text or line in S.plain(md_text)
            self.assertTrue(ok, f"{label}: a line that is not scorecard.md's: {tag}.{cls} {line!r}")

    def check_numbers(self, pg, card, label):
        """The class numbers of "By class" and of the teaser, computed again from the card."""
        table_ = next(t for t in pg.tables if t and t[0] == list(S.CLASS_COLUMNS))
        bigs = [b[2] for b in pg.blocks if b[1] == "tz-big"]
        ivs = [b[2] for b in pg.blocks if b[1] == "tz-iv"]
        rows = iter(table_[1:])
        want_big = {k: [] for k in P.V4_CLASSES}
        for k in P.V4_CLASSES:
            for cond, rec in card["conditions"].items():
                cls = rec["classes"].get(k)
                row_ = next(rows)
                self.assertEqual(row_[:2], [P.CONDITION_NAMES[cond], f"Class {k} {P.CLASS_NAMES[k]}"], label)
                if cls and cls.get("score") is not None and k in P.TIER_CLASSES.get(card["tier"], P.V4_CLASSES):
                    ci = f"[{r(cls['ci'][0])}, {r(cls['ci'][1])}]" if cls["ci"] else row_[3]
                    self.assertEqual(row_[2:5], [str(r(cls["score"])), ci, f"{cls['raw']:.1f}"], (label, k, cond))
                    want_big[k].append((str(r(cls["score"])), ci))
                else:
                    self.assertEqual(row_[2:5], ["", "", ""], (label, k, cond))
        self.assertEqual(list(zip(bigs, ivs)), [x for k in P.V4_CLASSES for x in want_big[k]], label)

    def check_drawing(self, pg, label):
        data = json.loads(pg.data)

        def marks(node):
            if isinstance(node, dict):
                yield from node.get("marks", [])
                for v in node.values():
                    yield from marks(v)
            elif isinstance(node, list):
                for v in node:
                    yield from marks(v)
        seen = 0
        for m in marks(data):
            seen += 1
            if "state" in m:
                self.assertEqual(set(m), {"state"}, label)
                continue
            if "." in m["short"]:                                   # a component level, 0 to 1, drawn on 0 to 100
                self.assertLessEqual(abs(m["x"] / 100 - float(m["short"])), 0.0006, (label, m))
            else:
                self.assertLessEqual(abs(m["x"] - float(m["short"])), 0.51, (label, m))
                if m["lo"] is not None:
                    lo, hi = map(float, re.findall(r"\[(\d+), (\d+)\]", m["text"])[0])
                    self.assertLessEqual(abs(m["lo"] - lo), 0.51, label)
                    self.assertLessEqual(abs(m["hi"] - hi), 0.51, label)
        self.assertGreater(seen, 0, label)


class Pages(Checks):
    def test_every_case(self):
        for label, kw in CASES.items():
            with self.subTest(label):
                self.check(folder(**kw), label)

    def test_memory_both_has_two_series_and_the_differences(self):
        out = folder(**CASES["individual quick both"])
        text, pg = self.check(out, "both")
        data = json.loads(pg.data)
        self.assertEqual([s["label"] for s in data["fig-classes"]["series"]],
                         [P.CONDITION_NAMES[c] for c in json.loads((out / "scorecard.json").read_text())["conditions"]])
        diff = next(t for t in pg.tables if t and t[0] == ["", ""] and t[1][0].startswith("Class"))
        md = (out / "scorecard.md").read_text()
        for cells in pg.tables[pg.tables.index(diff)][1:]:
            self.assertIn(f"{cells[0]:<10}{cells[1]}", md)

    def test_the_quick_tier_line_and_a_class_not_run(self):
        out = folder(**CASES["individual quick both"])
        text = read(out)
        self.assertIn(K.text(P.QUICK_CARD_LINE), text)
        self.assertIn(K.text(P.NOT_RUN_IN.format(tier="quick")), text)

    def test_labels_are_chips_and_a_box_line(self):
        pg = Page(read(folder(**CASES["custom roster labels"])))
        pills = [b[2] for b in pg.blocks if "pill" in b[1]]
        for label in LABELS:
            self.assertIn(label, pills)
        self.assertIn(" · ".join(LABELS), [b[2] for b in pg.blocks if b[1] == "ln"])

    def test_the_readme_and_report_in_their_own_words(self):
        readme = plain_md((REPO / "README.md").read_text(encoding="utf-8"))
        self.assertIn(S.STATUS_SENTENCE, readme)
        self.assertIn(S.STATUS_CHIP, readme)
        out = folder()
        self.assertIn(P.HOW_TO_READ_V4, (out / "report.md").read_text(encoding="utf-8"))
        pg = Page(read(out))
        self.assertIn(S.STATUS_CHIP, [b[2] for b in pg.blocks if "pill" in b[1]])
        self.assertIn(S.STATUS_SENTENCE, [b[2] for b in pg.blocks if b[0] == "p"])

    def test_the_same_inputs_give_the_same_bytes(self):
        a = folder(target=("organization", "Mozilla"), tier="quick")
        b = Path(tempfile.mkdtemp(dir=TMP)) / "run"
        bk, roster, calls, meta = TV.planted_run(b, tier="quick", memory="on", target=("organization", "Mozilla"))
        P.score_run(b, calls, bk, roster, meta, B=B)
        self.assertEqual((a / "results.html").read_bytes(), (b / "results.html").read_bytes())

    def test_a_hostile_name_is_text(self):
        out = hostile_folder()
        text, pg = self.check(out, "hostile")
        self.assertNotIn("<script>alert", text)
        self.assertNotIn("<img", text)
        self.assertNotIn("‮", text)
        self.assertEqual([t for t, _ in pg.tags].count("script"), 2)
        h1 = next(b[2] for b in pg.blocks if b[0] == "h1")
        self.assertIn("<script>alert(1)</script>", h1)
        self.assertIn("\\x1b[2J", h1)


class Rescore(Checks):
    def test_a_rescore_writes_the_page_and_keeps_the_old_one(self):
        src = folder(tier="quick")
        out = Path(tempfile.mkdtemp(dir=TMP)) / "run"
        shutil.copytree(src, out)
        first = (out / "results.html").read_bytes()
        P.rescore_folder(out, TV.bank(), now=NOW)
        stamp = out / P.PREVIOUS / NOW.strftime(P.STAMP)
        self.assertEqual((stamp / "results.html").read_bytes(), first)
        self.check(out, "rescored")
        pg = Page(read(out))
        self.assertIn(S.RESCORED, [b[2] for b in pg.blocks if "pill" in b[1]])
        card = json.loads((out / "scorecard.json").read_text())
        self.assertIn(P.rescored_line(card["rescored"]), [b[2] for b in pg.blocks if b[1] == "ln"])

    def test_a_folder_scored_before_the_page_gets_one_on_its_first_rescore(self):
        src = folder(tier="quick")
        out = Path(tempfile.mkdtemp(dir=TMP)) / "run"
        shutil.copytree(src, out)
        (out / "results.html").unlink()
        P.rescore_folder(out, TV.bank(), now=NOW)
        self.assertTrue((out / "results.html").is_file())
        self.assertFalse((out / P.PREVIOUS / NOW.strftime(P.STAMP) / "results.html").exists())
        self.assertEqual(sorted(p.name for p in (out / P.PREVIOUS / NOW.strftime(P.STAMP)).iterdir()),
                         sorted(f for f in P.RESULT_FILES if f != "results.html"))

    def test_a_legacy_quick_folder(self):
        out = Path(tempfile.mkdtemp(dir=TMP)) / "run"
        b, roster, calls, meta = TV.planted_run(out, tier="smoke", memory="on")
        legacy = dict(meta, tier="quick")
        del legacy["recipe"]
        (out / "run.json").write_text(json.dumps(legacy, indent=2))
        P.rescore_folder(out, b, now=NOW)
        pg = Page(read(out))
        self.assertIn("smoke tier (recorded as quick)", [b[2] for b in pg.blocks if "pill" in b[1]])
        self.check(out, "legacy")


# ---- the form's link (step 2): a v4 run's Page: line, the route and its refusals, the offer -------------------------
SAYS = r"""
import os, sys
folder = os.environ["RUN_FOLDER"]
for kind, name in (("Scorecard", "scorecard.json"), ("Report", "report.md"), ("Units", "units.jsonl"),
                   ("Page", "results.html")):
    print(f"{kind}: {os.path.join(folder, name)}", flush=True)
print("Page: /somewhere/else/results.html", flush=True)
"""


class ListedRun:
    """A run the form launched, as Session.results_page reads it: its folders, ended, each folder's identity recorded
    as the form records it when a run lists a folder (ui.folder_identity: the one pinning the versus page's route
    uses). No process."""

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


class FormLink(unittest.TestCase):
    """The form (ui.py) for a single-target run: its reading takes the run's Page: line (only inside its own
    folders), the route serves the folder's results.html under the token with the page's CSP and no-store, and
    refuses as it does for versus: a missing or wrong token, a traversal, an absolute path, a folder it does not
    list, a page or a folder that is a link, a page with a second name (a hard link), a page that is not a regular
    file; POST is 405."""

    def setUp(self):
        import test_ui as TU
        import ui as U
        self.U = U
        self.tmp = Path(tempfile.mkdtemp(prefix="single-html-form-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.run_dir = self.tmp / "results" / "v4-claude-organization-mozilla-quick-memory-on"
        shutil.copytree(folder(tier="quick"), self.run_dir)
        self.other = self.tmp / "results" / "v4-claude-nation-france-quick-memory-on"
        self.other.mkdir(parents=True)
        (self.other / "results.html").write_bytes(b"<p>another run</p>")
        (self.tmp / "secret.html").write_bytes(b"<p>outside the results folder</p>")
        self.session = U.Session(program=[sys.executable, "-c", "raise SystemExit(3)"])
        self.served = TU.Served(self.session)
        self.addCleanup(self.served.server.shutdown)
        self.addCleanup(self.served.server.server_close)
        with self.session.lock:
            self.session.runner = ListedRun([self.run_dir])

    def get(self, folder=None, token=True, extra=""):
        from urllib.parse import quote
        path = "/results.html" + ("?folder=" + quote(folder, safe="") if folder is not None else "") + extra
        return self.served.request("GET", path, token=token, header=False)

    def test_a_v4_reading_takes_its_page_line_inside_its_own_folders(self):
        import os
        env = dict(os.environ, PYTHONUNBUFFERED="1", RUN_FOLDER=str(self.run_dir))
        runner = self.U.Runner([sys.executable, "-c", SAYS], [], [self.run_dir], env)
        runner.wait(60)
        self.assertEqual(runner.results, [["Scorecard", str(self.run_dir / "scorecard.json")],
                                          ["Report", str(self.run_dir / "report.md")],
                                          ["Page", str(self.run_dir / "results.html")]])
        self.assertEqual(runner.status(), "finished")
        self.assertIn(["Page", self.U.shown(self.run_dir / "results.html")], runner.state()["results"])
        self.assertEqual(runner.identity(str(self.run_dir)), self.U.folder_identity(str(self.run_dir)))   # pinned

    def test_the_page_of_a_listed_single_target_folder(self):
        res, text = self.get(self.U.shown(self.run_dir))
        self.assertEqual(res.status, 200)
        self.assertEqual(text.encode("utf-8"), (self.run_dir / "results.html").read_bytes())
        self.assertEqual(res.getheader("Content-Security-Policy"), K.CSP)
        self.assertEqual(res.getheader("Cache-Control"), "no-store")
        self.assertEqual(res.getheader("Content-Type"), "text/html; charset=utf-8")

    def test_the_refusals(self):
        listed = self.U.shown(self.run_dir)
        for token in (None, "wrong"):
            self.assertEqual(self.get(listed, token=token)[0].status, 403, token)
        for f in (listed + "/..", "../" + listed, str(self.other), self.U.shown(self.other), "/etc", str(self.tmp),
                  listed + "/results.html", listed + "/ordo", ""):
            res, text = self.get(f)
            self.assertEqual(res.status, 404, f)
            self.assertNotIn("another run", text)
        self.assertEqual(self.get(listed, extra="&folder=x")[0].status, 404)
        (self.run_dir / "results.html").unlink()
        (self.run_dir / "results.html").symlink_to(self.tmp / "secret.html")
        res, text = self.get(listed)
        self.assertEqual(res.status, 404)
        self.assertNotIn("outside the results folder", text)
        (self.run_dir / "results.html").unlink()
        (self.run_dir / "results.html").mkdir()
        self.assertEqual(self.get(listed)[0].status, 404)
        link = self.tmp / "results" / "linked-run"
        link.symlink_to(self.other, target_is_directory=True)
        with self.session.lock:
            self.session.runner = ListedRun([link])
        self.assertEqual(self.get(self.U.shown(link))[0].status, 404)
        res, _ = self.served.request("POST", "/results.html?folder=x", body={})
        self.assertEqual(res.status, 405)

    def swap(self, replacement):
        """Put replacement where the listed folder was, after the form recorded it: the folder moved aside and
        replacement (a link to another run's folder, or another real folder) under its name."""
        os.rename(self.run_dir, self.tmp / "results" / "moved-aside")
        if replacement == "link":
            self.run_dir.symlink_to(self.other, target_is_directory=True)
        else:
            os.rename(self.other, self.run_dir)

    def test_a_folder_swapped_after_listing_is_refused(self):
        """The single-target page is read from the folder pinned by device and inode, by the versus page's own route
        (read_results_page): a link or another real folder moved into its place after listing is refused."""
        for replacement in ("link", "folder"):
            with self.subTest(replacement=replacement):
                self.setUp()
                self.swap(replacement)
                res, text = self.get(self.U.shown(self.run_dir))
                self.assertEqual(res.status, 404, text)
                self.assertNotIn("another run", text)

    def test_a_swap_between_the_check_and_the_open_is_refused(self):
        """The race, made certain: the folder swapped at the route's first open is refused; swapped at its second, the
        folder already pinned, the run's own page is read."""
        from unittest import mock
        real_open = os.open
        for at in (1, 2):
            for replacement in ("link", "folder"):
                with self.subTest(at=at, replacement=replacement):
                    self.setUp()
                    page, calls = (self.run_dir / "results.html").read_bytes(), []

                    def racing_open(path, flags, *args, **kwargs):
                        calls.append(path)
                        if len(calls) == at:
                            self.swap(replacement)
                        return real_open(path, flags, *args, **kwargs)
                    folder = str(self.run_dir)
                    identity = self.U.folder_identity(folder)
                    with mock.patch.object(self.U.os, "open", racing_open):
                        body = self.U.read_results_page(folder, identity)
                    if at == 1:
                        self.assertIsNone(body)
                    else:
                        self.assertEqual(len(calls), 2)
                        self.assertEqual(body, page)

    def test_a_page_with_a_second_name_is_refused(self):
        """results.html made as a hard link to a file outside the results folder (two names, where a page probatio
        wrote has one) is answered 404 with none of that file's bytes, also when its second name is taken away
        between the open and the check (the race, made certain); the run's own page given a second name elsewhere
        is refused too, and served once that name is gone."""
        from unittest import mock
        listed, page, secret = self.U.shown(self.run_dir), self.run_dir / "results.html", self.tmp / "secret.html"
        own = page.read_bytes()
        page.unlink()
        try:
            os.link(secret, page)
        except OSError as e:
            self.skipTest(f"this file system gives no file a second name ({e.strerror})")
        res, text = self.get(listed)
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
                            page.write_bytes(own)
                    return fd
                try:
                    with mock.patch.object(self.U.os, "open", racing_open):
                        self.assertIsNone(self.U.read_results_page(str(self.run_dir), identity))
                finally:                                       # the second name back for the next case
                    if page.exists():
                        page.unlink()
                    os.link(secret, page)
        page.unlink()
        page.write_bytes(own)
        os.link(page, self.tmp / "second-name.html")
        self.assertEqual(self.get(listed)[0].status, 404)
        (self.tmp / "second-name.html").unlink()
        res, text = self.get(listed)
        self.assertEqual((res.status, text.encode("utf-8")), (200, own))

    def test_a_page_that_is_a_pipe_or_has_no_recorded_identity_is_refused(self):
        listed = self.U.shown(self.run_dir)
        self.assertIsNone(self.U.read_results_page(str(self.run_dir), None))
        (self.run_dir / "results.html").unlink()
        os.mkfifo(self.run_dir / "results.html")
        self.assertEqual(self.get(listed)[0].status, 404)                 # a pipe, never waited on

    def test_the_offer_is_for_each_folder_whose_page_the_run_named(self):
        page = self.U.PAGE
        self.assertIn('if (!run.results.some(([k, p]) => k === "Page" && p === f + "/results.html")) continue;', page)
        self.assertIn('if ((run.status === "finished" || run.status === "scored") && run.results.some(([k]) => k === '
                      '"Page"))', page)
        self.assertTrue(self.U.RESULT_LINE.match("Page: /x/results.html"))
        self.assertTrue(self.U.VERSUS_RESULT_LINE.match("Page: /x/results.html"))


class FormRun(unittest.TestCase):
    """A real single-target run started from the form (Check, then Run) on the fake CLI (tests/fake_claude.py,
    questions and errands), in a scratch folder that is probatio's folder: it ends finished with its Page: line among
    its results, and the form's server serves that folder's results.html under its token with the page's CSP and
    no-store. Nothing is written under the repository's results/."""

    def test_a_run_from_the_form_ends_with_its_results_page(self):
        import os
        from unittest import mock
        import test_ui as TU
        import ui as U
        tmp = Path(tempfile.mkdtemp(prefix="single-html-run-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        for d in ("bin", "home/.claude"):
            (tmp / d).mkdir(parents=True)
        (tmp / "home" / ".claude" / "CLAUDE.md").write_text("# Invented notes\n\nA line the fake never reads.\n")
        fake = REPO / "tests" / "fake_claude.py"
        (tmp / "bin" / "claude").write_text(f'#!/bin/sh\nexec "{sys.executable}" "{fake}" "$@"\n')
        (tmp / "bin" / "codex").write_text('#!/bin/sh\necho "stand-in codex: no agent calls" >&2\nexit 97\n')
        for f in ("claude", "codex"):
            (tmp / "bin" / f).chmod(0o755)
        env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_CONFIG_DIR", "CODEX_HOME")}
        env.update(PATH=f"{tmp / 'bin'}{os.pathsep}{os.environ['PATH']}", HOME=str(tmp / "home"),
                   FAKE_CLAUDE="auto", PYTHONDONTWRITEBYTECODE="1")
        here = mock.patch.object(U.P, "HERE", tmp)
        here.start()
        self.addCleanup(here.stop)
        runner = REPO / "tests" / "fixtures" / "run_versus_here.py"          # probatio's own command, here = tmp
        served = TU.Served(U.Session(program=[sys.executable, "-B", str(runner)], env=env))
        self.addCleanup(served.close)
        self.addCleanup(served.server.shutdown)
        form = TU.page_form(agent="claude", types=["organization"], targets={"organization": "Mozilla"},
                            tier="quick", watch="off", jobs="16")
        checked = served.post("/check", {"form": form})
        self.assertTrue(checked["ok"], checked.get("error"))
        self.assertNotIn("error", served.post("/run", {"form": form, "confirmed": True}))
        self.assertTrue(TU.wait_for(lambda: served.state()["run"]["code"] is not None, 900),
                        served.state()["run"]["lines"][-5:])
        run = served.state()["run"]
        self.assertEqual((run["status"], run["code"]), ("finished", 0), run["lines"][-8:])
        folder = run["folders"][0]
        self.assertEqual(run["results"][-1], ["Page", folder + "/results.html"])
        real = tmp / folder / "results.html" if not Path(folder).is_absolute() else Path(folder) / "results.html"
        from urllib.parse import quote
        res, text = served.request("GET", "/results.html?folder=" + quote(folder, safe=""), header=False)
        self.assertEqual(res.status, 200)
        self.assertEqual(text.encode("utf-8"), real.read_bytes())
        self.assertEqual(res.getheader("Content-Security-Policy"), K.CSP)
        self.assertEqual(res.getheader("Cache-Control"), "no-store")
        self.assertTrue(text.startswith("<!doctype html>"))
        self.assertIn("Scorecard: Mozilla (organization)", text)


class Printed(unittest.TestCase):
    def test_the_page_line_follows_the_other_paths(self):
        import contextlib
        import io
        out = folder(tier="quick")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            P.print_results(out, out)
        tail = buf.getvalue().rstrip("\n").split("\n")[-7:]
        self.assertEqual([x.split(":")[0] for x in tail], ["Scorecard", "Report", "Table", "Graph", "Units",
                                                           "Replies", "Page"])
        self.assertEqual(tail[-1], f"Page: {P.display(out / 'results.html')}")

    def test_result_files_and_the_page_module_interface(self):
        self.assertEqual(P.RESULT_FILES[-1], "results.html")
        self.assertEqual(P.RESULT_FILES[0], "scorecard.json")         # rescored_record reads the first
        self.assertTrue(callable(S.render))


if __name__ == "__main__":
    unittest.main()
