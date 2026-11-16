"""ordo_html: the cross-type results page, ordo/results.html (the results-pages ruling, P3, P4, P7, P11; the HTML
ruling's H1 to H10 bind it). Real run folders of every cross-type kind, each planned from its command line by
probatio's own parser and planner, its replies and episodes planted by the suite's helpers (tests/test_ordo_scoring),
scored and written by ordo's own writer: the triangle and pairs, quick and standard, memory on, off and both, with
and without named targets, singles beside a cross pair, a rescored folder and a legacy one rescored.

What is held: every line of ordo/scorecard.md is on the page (its table rows cell for cell), and every report.md
line the page shows is report.md's; every drawn number is the text page's cell for the same pair, frame and
condition, at the same rounding, and sits where the card's number says; the text files are byte for byte what
47fab5b's ordo writes from the same card (taken from git; skipped without history); the page is deterministic,
loads nothing and escapes every name; with scripts off every number stands in a table; the kit's drawing rule holds
on these pages (the kit's own executed check, node; skipped without it); a rescore keeps the old page and writes the
new, and the run prints one Page: line. No network, no model.

    python3 -m unittest tests.test_ordo_html
"""
import contextlib
import functools
import html
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
import time
import unittest
import unittest.mock
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))
sys.path.insert(0, str(REPO / "tests" / "fixtures"))
import ordo as O  # noqa: E402
import ordo_html as OH  # noqa: E402
import probatio as P  # noqa: E402
import results_kit as K  # noqa: E402
import test_ordo_scoring as TO  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="ordo-html-"))
B = 200
NOW = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)
OPTS = SimpleNamespace(model=None, effort="medium", workdir="/tmp", features=[])
REPORT = {"files": [{"path": "/home/user/.claude/CLAUDE.md", "sha256": "0" * 64, "bytes": 7100}],
          "config_dir": "/home/user/.claude"}
LAUNCH = '{"started": "2026-11-13T00:00:00Z", "ended": "2026-11-13T00:17:00Z", "made": 1, "jobs": 4}\n'
BEFORE = "47fab5b"                     # the release before the cross-type page: its text files are the bar
HOSTILE = '<script>alert(1)</script> "><img src=x onerror=alert(1)> Nor‮land *bold* |pipe|'


def tearDownModule():
    shutil.rmtree(TMP, True)


CASES = {   # label: the command line after --agent claude
    "triangle quick, a named organization": ["--compare", "individual:organization:nation", "--target",
                                             "organization=Mozilla", "--tier", "quick"],
    "pair standard": ["--compare", "individual:organization"],
    "two pairs standard": ["--compare", "individual:organization,organization:nation"],
    "triangle standard, memory both": ["--compare", "individual:organization:nation", "--memory", "both"],
    "triangle quick, memory off": ["--compare", "individual:organization:nation", "--tier", "quick", "--memory",
                                   "off"],
    "pair standard, a named organization": ["--compare", "individual:organization", "--target",
                                            "organization=Mozilla"],
    "singles beside a cross pair, quick": ["--compare", "nation,individual:nation", "--target", "nation=France",
                                           "--tier", "quick"],
}


def plant_cross(argv, out):
    """The cross-type run of a command line, planned by probatio (prepare_runs, cross_record), planted by the suite's
    helpers, scored and written by ordo (score, write_results) in a folder of out; its folder. A command with single
    runs beside the cross one writes the cross one alone."""
    a = P.parse_args_v4(["--agent", "claude", *argv])
    bank, ob, selection, spec, runs = P.prepare_runs(a, False)
    a.out = str(out / "run")                     # one run is --out itself; several, each inside it
    run = next(r for r in runs if r["kind"] != "single")
    folder = P.run_folder(a, run, len(runs) > 1)
    folder.mkdir(parents=True)
    TO.plant(folder, run["calls"], TO.names_policy, TO.by_type({"individual": 0.8, "organization": 0.5, "nation": 0.3}))
    meta = P.cross_record(a, OPTS, bank, ob, spec, run, "0.0-fake", REPORT)
    meta["date"] = "2026-11-13 00:00 UTC"
    (folder / "run.json").write_text(json.dumps(meta, indent=2))
    (folder / "launches.jsonl").write_text(LAUNCH)
    O.write_results(folder, O.score(folder, run["calls"], ob, agent="claude", meta=meta, B=B))
    return folder


@functools.lru_cache(maxsize=None)
def folder(label):
    return plant_cross(CASES[label], Path(tempfile.mkdtemp(dir=TMP)))


def rescored(src, legacy=False):
    """A copy of a finished folder rescored as main_rescore does (rescore_folder at a fixed time); legacy makes the
    copy a run made before recipes first: tier quick with no recipe, no launches.jsonl, no memory size or folder, and
    no ordo/ results, so its page is its first."""
    out = Path(tempfile.mkdtemp(dir=TMP)) / "run"
    shutil.copytree(src, out)
    if legacy:
        meta = json.loads((out / "run.json").read_text())
        meta["tier"] = "quick"
        for k in ("recipe", "memory_bytes", "config_dir"):
            meta.pop(k, None)
        (out / "run.json").write_text(json.dumps(meta, indent=2))
        (out / "launches.jsonl").unlink()
        shutil.rmtree(out / "ordo")
    with unittest.mock.patch.object(P, "BOOTSTRAP_B", B), \
            unittest.mock.patch.object(O.score, "__defaults__", O.score.__defaults__[:-1] + (B,)):
        P.rescore_folder(out, now=NOW)
    return out


@functools.lru_cache(maxsize=None)
def legacy_folder():
    return rescored(plant_cross(["--compare", "individual:organization:nation", "--tier", "smoke"],
                                Path(tempfile.mkdtemp(dir=TMP))), legacy=True)


def all_folders():
    folders = {k: folder(k) for k in CASES}
    folders["rescored triangle, named"] = rescored(folder("triangle quick, a named organization"))
    folders["legacy triangle, rescored"] = legacy_folder()
    return folders


# ---- reading a page back ----------------------------------------------------------------------------------------------
class Page(html.parser.HTMLParser):
    """A page's text (outside scripts and styles), its table cells, its chips and its data block."""

    def __init__(self, page):
        super().__init__(convert_charrefs=True)
        self.parts, self.cells, self.pills, self.stack, self.cell, self.pill, self.data = [], [], [], [], None, None, ""
        self.glue = False                    # an inline code element's words run on with the text around it
        self.feed(page)
        self.text = " ".join(" ".join(self.parts).split())

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        self.stack.append((tag, a))
        if tag in ("td", "th"):
            self.cell = []
        if tag == "span" and a.get("class") == "pill":
            self.pill = []
        if tag == "code":
            self.glue = True

    def handle_endtag(self, tag):
        if tag == "code":
            self.glue = True
        if tag in ("td", "th") and self.cell is not None:
            self.cells.append(" ".join(" ".join(self.cell).split()))
            self.cell = None
        if tag == "span" and self.pill is not None:
            self.pills.append("".join(self.pill))
            self.pill = None
        if self.stack:
            self.stack.pop()

    def handle_data(self, data):
        where = self.stack[-1] if self.stack else ("", {})
        if where[0] == "script" and where[1].get("id") == "data":
            self.data += data
            return
        if where[0] in ("script", "style"):
            return
        if self.glue and self.parts:
            self.parts[-1] += data
        else:
            self.parts.append(data)
        self.glue = False
        if self.cell is not None:
            self.cell.append(data)
        if self.pill is not None:
            self.pill.append(data)


def read(f):
    return (f / "ordo" / "results.html").read_text(encoding="utf-8")


def md_tables(md):
    """scorecard.md's tables in order, each (columns, {label: cells}) with every cell as the page shows it."""
    tables, cur = [], None
    for line in md.splitlines():
        if line.startswith("|---"):
            continue
        if line.startswith("|"):
            cells = OH.cells_of(line)
            if cur is None:
                cur = (cells, {})
                tables.append(cur)
            else:
                cur[1][cells[0]] = cells
        else:
            cur = None
    return tables


def norm(s):
    return " ".join(s.split())


class Lines(unittest.TestCase):
    """The page against its two text pages, every kind."""

    def test_every_line_of_scorecard_md_is_on_the_page(self):
        for label, f in all_folders().items():
            with self.subTest(folder=label):
                page = Page(read(f))
                card = json.loads((f / "ordo" / "scorecard.json").read_text())
                calibration = P.display(card["head"][1]) if len(card["head"]) > 1 else None
                cells = set(page.cells)
                for line in (f / "ordo" / "scorecard.md").read_text(encoding="utf-8").splitlines():
                    if not line.strip() or line.startswith("|---"):
                        continue
                    if line.startswith("|"):
                        for c in OH.cells_of(line):
                            self.assertTrue(c == "" or c in cells, (label, c))
                    elif calibration and OH.plain(line) == OH.plain(calibration):
                        self.assertEqual(" · ".join(p for p in page.pills if p != OH.RESCORED), OH.plain(line))
                    else:
                        self.assertIn(norm(OH.item(line)), page.text, label)

    def test_the_report_lines_the_page_shows_are_report_mds(self):
        for label, f in all_folders().items():
            with self.subTest(folder=label):
                page = Page(read(f))
                sections = OH.report_sections((f / "ordo" / "report.md").read_text(encoding="utf-8"))
                shown = 0
                for cond, secs in sections.items():
                    for head in (OH.RED_FLAGS, OH.DIAGNOSTICS, OH.VALIDITY, OH.CLASS_III, OH.THREE_WAY, OH.NOTES):
                        for line in secs.get(head, []):
                            if line.startswith("|---"):
                                continue
                            if line.startswith("|"):
                                for c in OH.cells_of(line):
                                    self.assertTrue(c == "" or c in page.cells, (label, c))
                            else:
                                self.assertIn(norm(OH.item(line)), page.text, (label, line))
                            shown += 1
                self.assertGreater(shown, 10)

    def test_every_drawn_number_is_the_text_pages(self):
        """Each bar's text is the answer's cell for its pair, frame and condition, its short label that cell's whole
        number, and it sits at the card's own value and interval; a cell with no number or a status is its words."""
        for label, f in all_folders().items():
            with self.subTest(folder=label):
                md = (f / "ordo" / "scorecard.md").read_text(encoding="utf-8")
                card = json.loads((f / "ordo" / "scorecard.json").read_text())
                figures = json.loads(Page(read(f)).data)
                answers = [t for t in md_tables(md) if t[0][-1] == "ordering"]
                conds = list(card["conditions"])
                self.assertEqual(len(answers), len(conds))
                checked = 0
                for tab in figures["fig-frames"]["tabs"]:
                    for panel, (columns, rows), cond in zip(tab["panels"], answers, conds):
                        cells = rows[tab["label"]]
                        rec = next(r for r in card["conditions"][cond]["rows"] if r["label"] == tab["label"])
                        for j, (r, pn) in enumerate(zip(panel["rows"], card["pairs"])):
                            self.assertEqual(r["label"], columns[1 + j])
                            m, p = r["marks"][0], rec["pairs"][pn]
                            if "state" in m:
                                self.assertEqual(m["state"], cells[1 + j])
                                continue
                            self.assertEqual((m["text"], m["short"]), (cells[1 + j], cells[1 + j].split(" ")[0]))
                            ci = p.get("ci")
                            self.assertEqual((m["x"], m["lo"], m["hi"]), (K.pos(p["value"]), *(
                                (K.pos(ci[0]), K.pos(ci[1])) if ci else (None, None))))
                            self.assertEqual(int(m["short"]), int(P.whole(p["value"])))
                            checked += 1
                        for r in panel["rows"][len(card["pairs"]):]:          # the deployer share, as printed
                            self.assertEqual(r["label"], OH.DEPLOYER)
                            self.assertIn(f"{OH.DEPLOYER} {r['marks'][0]['text']}", cells[-1])
                        self.assertEqual(panel["notes"], [cells[-1]])
                self.assertGreater(checked, 0)
                names = [t for t in md_tables(md) if t[0][1] == O.NAME_COLUMNS[0]]
                for panel, (columns, rows) in zip(figures.get("fig-names", {"tabs": [{"panels": []}]})["tabs"][0]
                                                  ["panels"], names):
                    for r in panel["rows"]:
                        cells = rows[r["label"]]
                        texts = [m.get("text", m.get("state")) for m in r["marks"]]
                        self.assertEqual(texts, [cells[1]] if cells[1] == "insufficient" else cells[1:3])

    def test_the_teaser_names_its_condition_when_there_are_several(self):
        """With memory both the teaser shows one condition: its caption names it in the scorecard's own words (the
        condition's heading there) and names the figures that hold every condition; with one condition, no caption."""
        for label, f in all_folders().items():
            page = read(f)
            card = json.loads((f / "ordo" / "scorecard.json").read_text())
            teaser = page.split('<figure class="teaser-fig">', 1)[1].split("</figure>", 1)[0]
            with self.subTest(folder=label):
                if len(card["conditions"]) == 1:
                    self.assertNotIn("tz-cond", page)
                    continue
                first = O.CONDITION_NAMES[next(iter(card["conditions"]))]
                self.assertIn(f"**{first}**", (f / "ordo" / "scorecard.md").read_text(encoding="utf-8"))
                self.assertIn(f'<figcaption class="tz-cond"><strong>{K.text(first)}</strong> ', teaser)
                self.assertIn(K.text(OH.TEASER_ALL.format(figures=OH.joined(
                    [OH.FIG_FRAMES, OH.FIG_ARMS] + ([OH.THREE_WAY] if card["triangle"] else [])))), teaser)
        self.assertTrue(any(len(json.loads((f / "ordo" / "scorecard.json").read_text())["conditions"]) > 1
                            for f in all_folders().values()))

    def test_a_code_span_is_a_code_element_without_backticks(self):
        """A command the text page quotes as markdown's inline code shows as a code element with the same words; no
        backtick from a text page line reaches the page's text."""
        spans = 0
        for label, f in all_folders().items():
            page = read(f)
            with self.subTest(folder=label):
                self.assertNotIn("`", Page(page).text)
                for line in (f / "ordo" / "scorecard.md").read_text(encoding="utf-8").splitlines():
                    for inner in OH.CODE_SPAN.findall(line):
                        self.assertIn(f"<code>{K.text(inner)}</code>", page)
                        self.assertIn(norm(OH.item(line)), Page(page).text)
                        spans += 1
        self.assertGreater(spans, 0)

    def test_the_three_way_items_are_report_mds_shares(self):
        f = folder("triangle standard, memory both")
        figures = json.loads(Page(read(f)).data)
        report = (f / "ordo" / "report.md").read_text(encoding="utf-8")
        notes = re.findall(r'<p class="fig-note">([^<]*)</p>', read(f).split('id="fig-three"')[1].split("</figure>")[0])
        for panel, note in zip(figures["fig-three"]["tabs"][0]["panels"], notes):
            line = "- " + html.unescape(note)
            self.assertIn(line, report)
            shares = dict(s.rsplit(" ", 1) for s in OH.THREE_WAY_LINE.match(line).group("shares").split(", "))
            self.assertEqual({r["label"]: r["marks"][0]["short"] for r in panel["rows"]}, shares)
        self.assertEqual(figures["fig-three"]["even"], round(100 / 3, 2))

    def test_the_standings_are_the_svgs_strings(self):
        for label in ("triangle quick, a named organization", "triangle standard, memory both"):
            f = folder(label)
            svg = (f / "ordo" / "results.svg").read_text(encoding="utf-8")
            titles = re.findall(r"<title>([^<]*standing [^<]*)</title>", svg)
            page = Page(read(f))
            self.assertTrue(titles)
            for t in titles:
                self.assertIn(html.unescape(t), page.cells)
        self.assertNotIn(OH.STANDINGS, read(folder("pair standard")))      # a pair has no standings strip


class Page_(unittest.TestCase):
    """The page as a file: its kind's place, its line, its safety."""

    def test_the_text_files_are_47fab5bs_from_the_same_card(self):
        """scorecard.md, report.md and results.svg are byte for byte what 47fab5b's ordo writes from the same cards."""
        tmp = Path(tempfile.mkdtemp(dir=TMP))
        proc = subprocess.run(["git", "-C", str(REPO), "archive", "--format=tar", BEFORE], capture_output=True,
                              timeout=300)
        if proc.returncode:
            self.skipTest(f"no git history: {BEFORE}'s code cannot be read")
        with tarfile.open(fileobj=io.BytesIO(proc.stdout)) as tar:
            tar.extractall(tmp / "before")
        folders = all_folders()
        cards = {k: (f / "ordo" / "scorecard.json").read_text() for k, f in folders.items()}
        (tmp / "cards.json").write_text(json.dumps(cards))
        # a rescore here runs under rescored()'s bootstrap size, which the errand rule's fingerprint reads: the folders
        # written under it are written under it there too
        child = ("import json, sys\nsys.path.insert(0, sys.argv[1])\nimport ordo, probatio\nfrom pathlib import Path\n"
                 "B = probatio.BOOTSTRAP_B\n"
                 "for k, v in json.load(open(sys.argv[2])).items():\n"
                 f"    probatio.BOOTSTRAP_B = {B} if k.split()[0] in ('rescored', 'legacy') else B\n"
                 "    ordo.write_results(Path(sys.argv[3]) / str(abs(hash(k))), json.loads(v))\n"
                 "json.dump({k: str(abs(hash(k))) for k in json.load(open(sys.argv[2]))}, open(sys.argv[4], 'w'))\n")
        run = subprocess.run([sys.executable, "-c", child, str(tmp / "before"), str(tmp / "cards.json"),
                              str(tmp / "out"), str(tmp / "names.json")], capture_output=True, text=True, timeout=900,
                             cwd=tmp, env={"PYTHONHASHSEED": "0", "PATH": "/usr/bin:/bin"})
        self.assertEqual(run.returncode, 0, run.stderr[-2000:])
        names = json.loads((tmp / "names.json").read_text())
        for k, f in folders.items():
            for name in ("scorecard.md", "report.md", "results.svg", "scorecard.json"):
                with self.subTest(folder=k, file=name):
                    self.assertEqual((f / "ordo" / name).read_bytes(),
                                     (tmp / "out" / names[k] / "ordo" / name).read_bytes())

    def test_deterministic_and_the_one_written(self):
        f = folder("triangle standard, memory both")
        card = json.loads((f / "ordo" / "scorecard.json").read_text())
        shown = P.shown_view(card)
        rule = O.rule_for(shown)
        parts, report = O.answer_parts(shown, rule), O.report_md(shown, rule)
        self.assertEqual(OH.render(parts, report, shown), OH.render(parts, report, shown))
        self.assertEqual(OH.render(parts, report, shown), (f / "ordo" / "results.html").read_bytes())
        self.assertEqual(O.answer_text(parts), (f / "ordo" / "scorecard.md").read_text(encoding="utf-8"))

    def test_it_loads_nothing(self):
        for label, f in all_folders().items():
            page = read(f)
            with self.subTest(folder=label):
                self.assertIn(f'<meta http-equiv="Content-Security-Policy" content="{K.text(K.CSP)}">', page)
                self.assertNotIn("http://", page)
                self.assertNotIn("https://", page)
                self.assertNotIn(" src=", page.split('<script type="application/json"')[0])
                self.assertEqual(page.count("<script"), 2)

    def test_every_name_is_escaped(self):
        """A named target and an invented entity renamed to markup: never markup on the page, and still word for word
        the text page's (each line of scorecard.md on the page)."""
        src = folder("triangle quick, a named organization")
        card = json.loads((src / "ordo" / "scorecard.json").read_text())
        hostile = json.loads(json.dumps(card).replace("Mozilla", json.dumps(HOSTILE)[1:-1])
                             .replace("Arvenia", json.dumps(HOSTILE + "2")[1:-1]))
        out = Path(tempfile.mkdtemp(dir=TMP))
        O.write_results(out, hostile)
        page = (out / "ordo" / "results.html").read_text(encoding="utf-8")
        self.assertNotIn("<script>alert", page)
        self.assertNotIn("<img", page)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", page)
        text = Page(page).text
        for line in (out / "ordo" / "scorecard.md").read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.startswith("|") and "fixtures" not in line:
                self.assertIn(norm(OH.item(line)), text)

    def test_with_scripts_off_every_number_stands_in_a_table(self):
        for label, f in all_folders().items():
            page = Page(read(f))
            cells = " | ".join(page.cells)
            figures = json.loads(page.data)
            with self.subTest(folder=label):
                self.assertEqual(read(f).count('<details class="numbers" open>'),
                                 sum(1 for k in figures if not k.startswith("tz-")))
                for spec in figures.values():
                    for tab in spec.get("tabs", [{"panels": [spec]}]):
                        for panel in tab["panels"]:
                            for r in panel["rows"]:
                                for m in r["marks"]:
                                    self.assertIn(m.get("text", m.get("state")), cells, (label, m))

    def test_a_rescore_keeps_the_old_page_and_writes_the_new(self):
        src = folder("triangle quick, a named organization")
        out = rescored(src)
        old = list(out.glob("previous/*/ordo/results.html"))
        self.assertEqual(len(old), 1)
        self.assertEqual(old[0].read_bytes(), (src / "ordo" / "results.html").read_bytes())
        page = Page(read(out))
        self.assertIn(OH.RESCORED, page.pills)
        self.assertIn("rescored 2026-11-13T12:00:00Z", page.text)

    def test_a_legacy_folder_gets_its_first_page_when_rescored(self):
        f = legacy_folder()
        page = Page(read(f))
        self.assertIn("smoke tier (recorded as quick)", page.text)
        self.assertIn("Run time not recorded", page.text)
        self.assertFalse(list(f.glob("previous/*/ordo/results.html")))       # it had none to keep

    def test_the_run_prints_one_page_line_after_its_other_paths(self):
        f = folder("pair standard")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            P.print_results(f / "ordo", f)
        lines = [x for x in out.getvalue().splitlines() if re.match(r"^[A-Z][a-z]+: /", x)]
        self.assertEqual(lines[-1], f"Page: {f / 'ordo' / 'results.html'}")
        self.assertEqual(sum(x.startswith("Page: ") for x in lines), 1)
        self.assertIn("results.html", O.RESULT_FILES)


class Listed:
    """A run the form launched, as Session.results_page reads it: its folders with their identities recorded as the
    form records them, and the result lines its output printed. No process."""

    def __init__(self, folders, results):
        import ui as U
        self.folders, self.code, self.results = [str(f) for f in folders], 0, [list(r) for r in results]
        self.identities = {f: U.folder_identity(f) for f in self.folders}

    def identity(self, folder):
        return self.identities.get(folder)

    def stop(self):
        return False

    def state(self):
        return {"status": "finished", "folders": self.folders, "results": self.results, "lines": [], "code": 0}


SAYS = r"""
import os
f = os.environ["RUN_FOLDER"]
for kind, path in (("Scorecard", "ordo/scorecard.json"), ("Report", "ordo/report.md"), ("Graph", "ordo/results.svg"),
                   ("Replies", "replies.jsonl"), ("Page", "ordo/results.html")):
    print(f"{kind}: {os.path.join(f, path)}", flush=True)
"""


class FormLink(unittest.TestCase):
    """The form (ui.py) for a cross-type run (step 4): its reading takes the run's Page: line for ordo/results.html, the
    route serves that page under the token with the page's CSP and no-store, at the subpath the run's own line names
    (never the request's), and refuses as it does for the other pages: ordo/ or the page that is a link, a page with
    a second name, a page that is not there."""

    def setUp(self):
        import test_ui as TU
        import ui as U
        self.U = U
        self.tmp = Path(tempfile.mkdtemp(prefix="ordo-html-form-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.run_dir = self.tmp / "results" / "v4-claude-compare-individual-organization-standard-memory-on"
        shutil.copytree(folder("pair standard"), self.run_dir)
        self.page = (self.run_dir / "ordo" / "results.html").read_bytes()
        self.other = self.tmp / "results" / "another-run"
        (self.other / "ordo").mkdir(parents=True)
        (self.other / "ordo" / "results.html").write_bytes(b"<p>another run</p>")
        (self.tmp / "secret.html").write_bytes(b"<p>outside the results folder</p>")
        self.session = U.Session(program=[sys.executable, "-c", "raise SystemExit(3)"])
        self.served = TU.Served(self.session)
        self.addCleanup(self.served.server.shutdown)
        self.addCleanup(self.served.server.server_close)
        self.listed()

    def listed(self, page=("ordo", "results.html")):
        with self.session.lock:
            self.session.runner = Listed([self.run_dir], [["Page", str(self.run_dir.joinpath(*page))]])

    def get(self, folder):
        from urllib.parse import quote
        return self.served.request("GET", "/results.html?folder=" + quote(folder, safe=""), header=False)

    def test_the_page_of_a_cross_type_folder(self):
        res, text = self.get(self.U.shown(self.run_dir))
        self.assertEqual(res.status, 200, text)
        self.assertEqual(text.encode("utf-8"), self.page)
        self.assertEqual(res.getheader("Content-Security-Policy"), K.CSP)
        self.assertEqual(res.getheader("Cache-Control"), "no-store")
        self.assertEqual(res.getheader("Content-Type"), "text/html; charset=utf-8")
        self.assertEqual(self.U.page_parts(self.session.runner, str(self.run_dir)), self.U.ORDO_PAGE)

    def test_the_subpath_is_the_runs_never_the_requests(self):
        listed = self.U.shown(self.run_dir)
        for f in (listed + "/ordo", listed + "/ordo/results.html", "ordo", self.U.shown(self.other)):
            self.assertEqual(self.get(f)[0].status, 404, f)
        (self.run_dir / "results.html").write_bytes(b"<p>a results.html the run did not name</p>")
        res, text = self.get(listed)
        self.assertEqual(text.encode("utf-8"), self.page)            # the run named ordo/results.html: that one
        self.listed(page=("results.html",))                          # a run that named FOLDER/results.html
        self.assertEqual(self.U.page_parts(self.session.runner, str(self.run_dir)), self.U.SINGLE_PAGE)
        self.assertIn("did not name", self.get(listed)[1])
        with self.session.lock:
            self.session.runner = Listed([self.run_dir], [])          # no Page line: its folder's results.html only
        (self.run_dir / "results.html").unlink()
        self.assertEqual(self.get(listed)[0].status, 404)

    def test_the_refusals(self):
        listed, ordo = self.U.shown(self.run_dir), self.run_dir / "ordo"
        page = ordo / "results.html"
        page.unlink()
        page.symlink_to(self.tmp / "secret.html")                     # the page a link out of the folder
        res, text = self.get(listed)
        self.assertEqual(res.status, 404)
        self.assertNotIn("outside the results folder", text)
        page.unlink()
        try:
            os.link(self.tmp / "secret.html", page)                   # the page a second name of a file elsewhere
        except OSError as e:
            self.skipTest(f"this file system gives no file a second name ({e.strerror})")
        res, text = self.get(listed)
        self.assertEqual(res.status, 404)
        self.assertNotIn("outside the results folder", text)
        page.unlink()
        self.assertEqual(self.get(listed)[0].status, 404)             # no page
        shutil.rmtree(ordo)
        ordo.symlink_to(self.other / "ordo", target_is_directory=True)   # ordo/ a link to another run's
        res, text = self.get(listed)
        self.assertEqual(res.status, 404)
        self.assertNotIn("another run", text)
        res, _ = self.served.request("POST", "/results.html?folder=x", body={})
        self.assertEqual(res.status, 405)

    def test_a_v4_reading_takes_a_cross_type_runs_page_line(self):
        import os as os_
        env = dict(os_.environ, PYTHONUNBUFFERED="1", RUN_FOLDER=str(self.run_dir))
        runner = self.U.Runner([sys.executable, "-c", SAYS], [], [self.run_dir], env)
        runner.wait(60)
        self.assertEqual(runner.results[-1], ["Page", str(self.run_dir / "ordo" / "results.html")])
        self.assertEqual(runner.status(), "finished")
        self.assertEqual(self.U.page_parts(runner, str(self.run_dir)), self.U.ORDO_PAGE)
        with self.session.lock:
            self.session.runner = runner
        res, text = self.get(self.U.shown(self.run_dir))
        self.assertEqual((res.status, text.encode("utf-8")), (200, self.page))

    def test_the_form_offers_it(self):
        runner = Listed([self.run_dir], [["Page", str(self.run_dir / "ordo" / "results.html")]])
        runner.checks, runner.clock = {}, time.monotonic
        runner.page_named = lambda f: self.U.Runner.page_named(runner, f)
        self.assertTrue(self.U.Runner.page_named(runner, str(self.run_dir)))        # the card offers it
        self.assertEqual(self.U.Runner.page_now(runner, str(self.run_dir)), "ready")
        self.assertTrue(self.U.RESULT_LINE.match("Page: /x/ordo/results.html"))
        self.assertEqual(self.U.ORDO_PAGE, ("ordo", "results.html"))
        self.assertEqual(Path(*self.U.ORDO_PAGE), Path(O.PAGE_FILE).parent / "ordo" / O.PAGE_FILE)


@unittest.skipUnless(shutil.which("node"), "needs node")
class Drawing(unittest.TestCase):
    """The kit's executed drawing check (tests/test_results_kit.Drawing) on every cross-type page: each interval
    from its lower endpoint to its upper one with centred caps, each bar exactly its value, nothing past the scale."""

    def test_every_mark_is_drawn_at_its_own_numbers(self):
        import test_results_kit as TR
        check = TR.Drawing("test_the_caps_and_the_dot_are_centred_on_their_point")
        for label, f in all_folders().items():
            with self.subTest(folder=label):
                check.check_figures(json.loads(Page(read(f)).data))


if __name__ == "__main__":
    unittest.main()
