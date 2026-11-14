"""results_kit: the parts every results page shares (the results-pages ruling, P1 and P2), and how the pages draw.

The kit split moved versus_html's general parts into results_kit without changing a byte of the head-to-head page
(proved then against 20ab0a2, the split's base). The drawing rule has changed since, on purpose: an interval is drawn
exactly from its lower to its upper endpoint, with a thin cap centred on each endpoint (one cap when they meet), and
a bar exactly as long as its value, a value of 0 marked by a dot centred on 0; nothing is widened to be seen. So the
bar now is this: on the same corpus (the versus cards of tests/test_versus_html.CASES and its other tests, and the
single-target folders of tests/test_single_html.CASES with its hostile name), a page rendered here differs from the
page 54333d6 renders (taken from git; skipped without history) only in the drawing rule's lines of the stylesheet
and the script and in the stacks' geometry (each boundary within 0.01: a stack's boundaries are rounded on the running
count now, its lengths before), masked on both sides, and fails on any other byte. A stack tiles from 0 and ends at its
counts' whole share, never past 100 (Stack). The drawing itself is tested by running the kit's
real script in node's vm against a small DOM stub (standard library on both sides; skipped without node): the five
intervals of the numbers check, bars of 0 and of 100, and every mark of every corpus page, in every tab, drawn at its
own numbers. The kit's own rules are tested directly: one escaping function for the markup, an inert data block, the
shell with and without a page's second script and stylesheet, the helpers' markup, determinism. No network, no model.

    python3 -m unittest tests.test_results_kit
"""
import importlib
import io
import json
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))
sys.path.insert(0, str(REPO / "tests" / "fixtures"))
import results_kit as K  # noqa: E402
import versus as VS  # noqa: E402
import versus_html as H  # noqa: E402

BEFORE = "54333d6"                    # the pages before the drawing rule changed: every other byte is the bar
NODE = shutil.which("node")

# ---- test modules this module imports for their corpus -------------------------------------------------------------
# test_versus_html and test_single_html make a temporary folder when they are imported and remove it in their own
# tearDownModule, which unittest calls only when their own tests ran. A module this one imported itself, one the run
# had not loaded, is this module's to clean up; a module the run loaded (a discover run, or one named with this one)
# keeps its folder until its own tests are done.
IMPORTED = []


def corpus_module(name):
    """The test module name, imported; when this run had not loaded it, noted for this module's tearDownModule."""
    fresh = name not in sys.modules
    module = importlib.import_module(name)
    if fresh and hasattr(module, "tearDownModule"):
        IMPORTED.append(module)
    return module


def tearDownModule():
    while IMPORTED:
        IMPORTED.pop().tearDownModule()


# ---- the corpus ----------------------------------------------------------------------------------------------------
def versus_cards():
    """{label: card JSON} of the versus corpus: test_versus_html's CASES and the cards its other tests make."""
    T, TP = corpus_module("test_versus_html"), corpus_module("test_versus_page")
    cards = {k: T.card(k) for k in T.CASES}
    cards["refused"] = TP.card_of("deployer", n=3, development=True)
    cards["at1 quick"] = TP.card_of("deployer", recipe=VS.RECIPES["quick"], n=3, tier="quick")
    unreadable = T.card("first_name three standard on")
    unreadable["classes"]["schema"] = "probatio-versus-classes/0"
    cards["unreadable classes"] = unreadable
    two = VS.card_json(T.card("option A two quick off"))
    for label, value in TP.HOSTILE.items():
        cards[f"hostile {label}"] = json.loads(two.replace("Mera Vale", json.dumps(value)[1:-1]))
    three = VS.card_json(T.card("first_name three standard on"))
    for old, new in T.Hostile.NAMES.items():
        three = three.replace(old, json.dumps(new)[1:-1])
    cards["hostile script and img names"] = json.loads(three)
    turned = T.reoriented(T.card("coin three standard both, unfamiliar"))       # pair records toward either name
    for what, c in turned[:3] + turned[-1:]:
        cards[f"turned {what}"] = c
    cards[SPLIT] = split_allocation(T.card("first_name three standard on"))
    return {k: VS.card_json(c) for k, c in cards.items()}


SPLIT = "allocation 10, 7 and 15 of 32"


def split_allocation(card):
    """card with its first pair's stated allocation read as 10, 7 and 15 of 32 valid replies (two listings of 16:
    5, 3, 8 and 5, 4, 7): the re-check's stack, whose shares rounded alone (31.25 + 21.88 + 46.88) end at 100.01."""
    import versus_page as VP
    a, b = VP.run_pairs(card)[0]
    rec = VP.metrics(card, VP.primary(card))[f"{a}:{b}/allocation"]
    eligibility = rec["eligibility"]
    for order, (n_a, n_b, n_tie) in zip(sorted(eligibility["diagnostics"]["rates_by_order"]), ((5, 3, 8), (5, 4, 7))):
        eligibility["diagnostics"]["rates_by_order"][order] = {a: n_a / 16, b: n_b / 16, "tie": n_tie / 16}
        eligibility["counts"]["cells"][order].update(planned=16, valid=16)
    assert VP.allocation_rates(rec, (a, b)) == ({a: 10, b: 7, "tie": 15}, 32)
    return card


# The single-target folders, made by the tree they are rendered in (folder() scores a planted run there, with the
# same seed and clock), and their pages read back. Run here and in 54333d6's tree.
SINGLE_PAGES = r"""
def single_pages():
    import test_single_html as TS
    folders = {k: TS.folder(**kw) for k, kw in TS.CASES.items()}
    folders["hostile name"] = TS.hostile_folder()
    return {"single " + k: (f / "results.html").read_text(encoding="utf-8") for k, f in folders.items()}
"""
RENDER_THERE = r"""
import json, sys
sys.path[:0] = [sys.argv[1], sys.argv[1] + "/tests", sys.argv[1] + "/tests/fixtures"]
import versus_html
""" + SINGLE_PAGES + r"""
cards = json.load(open(sys.argv[2]))
pages = {"versus " + k: versus_html.render(json.loads(v)).decode("utf-8") for k, v in cards.items()}
pages.update(single_pages())
json.dump(pages, open(sys.argv[3], "w"))
sys.modules["test_single_html"].tearDownModule()          # its module folder, made in this child
"""


def single_pages_here():
    """The single-target corpus's pages as this tree renders them (SINGLE_PAGES, run here)."""
    corpus_module("test_single_html")
    space = {}
    exec(SINGLE_PAGES, space)
    return space["single_pages"]()


# ---- the drawing rule: what changed on purpose, as the pages carry it -----------------------------------------------
# (before, now) pairs of text as 54333d6's pages and this tree's pages carry them (the kit's STYLE and SCRIPT). The byte
# bar masks exactly these on both sides; each occurs once per page.
DRAWING_RULE = [
    (""".sl .ci::before,.sl .ci::after{content:"";position:absolute;top:-3px;width:1.5px;height:8px;background:var(--charcoal)}
.sl .ci::before{left:0}
.sl .ci::after{right:0}
""", """.sl .cap{position:absolute;top:2px;width:1.5px;height:8px;margin-left:-.75px;background:var(--charcoal);z-index:2}
.sl .zero{position:absolute;top:50%;width:6px;height:6px;margin:-3px 0 0 -3px;border-radius:50%;z-index:1}
"""),
    (""".sl .mini-row .ci{top:4px}
""", """.sl .mini-row .ci{top:4px}
.sl .mini-row .cap{top:1px}
"""),
    ("""  /* one mark: a bar with its whisker, a stacked bar, or the number's status words */
""", """  /* a mark centred on a point of the track (a cap, or the dot of a bar of 0): it marks the point, never a length */
  function point(t, cls, x, paint) {
    var p = el('div', cls); p.style.left = x + '%';
    if (paint) p.style.background = paint;
    t.append(p);
  }
  /* an interval: its line exactly from lo to hi, and a cap centred on each endpoint (one cap when they meet) */
  function interval(t, lo, hi) {
    var w = el('div', 'ci');
    w.style.left = lo + '%'; w.style.width = 'calc(' + hi + '% - ' + lo + '%)';
    t.append(w);
    point(t, 'cap', lo);
    if (hi !== lo) point(t, 'cap', hi);
  }
  /* one mark: a bar with its interval, a stacked bar, or the number's status words */
"""),
    ("""    var b = el('div', 'bar');
    b.style.background = colour(m.colour || s.colour); b.style.width = still ? m.x + '%' : '0%';
    t.append(b);
    if (m.lo != null) {
      var w = el('div', 'ci');
      w.style.left = m.lo + '%'; w.style.width = Math.max(0.6, m.hi - m.lo) + '%';
      t.append(w);
    }
    grow.push(function () { b.style.width = Math.max(0.4, m.x) + '%'; });
""", """    var b = el('div', 'bar'), paint = colour(m.colour || s.colour);
    b.style.background = paint; b.style.width = still ? m.x + '%' : '0%';
    t.append(b);
    if (m.x === 0) point(t, 'zero', 0, paint);
    if (m.lo != null) interval(t, m.lo, m.hi);
    grow.push(function () { b.style.width = m.x + '%'; });
"""),
    ("""    if (m.stack) {
      var left = 0;
      m.stack.forEach(function (p) {
        var seg = el('div', 'seg');
        seg.style.left = left + '%'; seg.style.width = p.x + '%'; seg.style.background = colour(p.colour);
        left += p.x; t.append(seg);
      });
""", """    if (m.stack) {                                  /* each segment from its boundary to the next (stack) */
      m.stack.forEach(function (p) {
        var seg = el('div', 'seg');
        seg.style.left = p.lo + '%'; seg.style.width = 'calc(' + p.hi + '% - ' + p.lo + '%)';
        seg.style.background = colour(p.colour); t.append(seg);
      });
"""),
]
MASK = "\x00the drawing rule\x00"
DATA = re.compile(r'<script type="application/json" id="data">(.*?)</script>', re.S)
EPS = 1e-9                              # float noise in a difference of two values of two decimals


def stack_geometry(data, side):
    """([(start, end)] of every stack segment of a page's data, in order, data without that geometry). Side 0 is
    54333d6's: a segment x long from where the last one ended, as its script added them up; side 1 is now's: from lo
    to hi as emitted (stack). Everything else in data stays."""
    spans = []

    def strip(node):
        if isinstance(node, dict):
            if isinstance(node.get("stack"), list):
                end = 0.0
                for seg in node["stack"]:
                    start, end = (end, end + seg["x"]) if side == 0 else (seg["lo"], seg["hi"])
                    spans.append((start, end))
                node = dict(node, stack=[{k: v for k, v in seg.items() if k not in ("x", "lo", "hi")}
                                         for seg in node["stack"]])
            return {k: strip(v) for k, v in node.items()}
        return [strip(v) for v in node] if isinstance(node, list) else node
    return spans, strip(data)


def masked(page, side):
    """page with each of the drawing rule's texts of side (0: before, 1: now) masked, and its data block with the
    stacks' geometry taken out (stack_geometry; every other value kept, in canonical JSON); ValueError naming a rule
    text that does not occur exactly once."""
    for pair in DRAWING_RULE:
        part = pair[side]
        if page.count(part) != 1:
            raise ValueError(f"{page.count(part)} occurrences of the drawing rule's text {part[:60]!r}")
        page = page.replace(part, MASK)
    block = DATA.search(page)
    _, data = stack_geometry(json.loads(block.group(1)), side)
    return page[:block.start(1)] + MASK + json.dumps(data, sort_keys=True) + page[block.end(1):]


class TheBar(unittest.TestCase):
    def test_the_pages_differ_from_54333d6s_only_in_the_drawing_rule(self):
        """Both kinds of page, the same corpus, rendered here and by 54333d6's code: with the drawing rule's texts
        masked on both sides, byte for byte the same; and the rule's texts are the kit's own."""
        tmp = Path(tempfile.mkdtemp(prefix="results-kit-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        proc = subprocess.run(["git", "-C", str(REPO), "archive", "--format=tar", BEFORE], capture_output=True,
                              timeout=300)
        if proc.returncode:
            self.skipTest(f"no git history: {BEFORE}'s code cannot be read")
        with tarfile.open(fileobj=io.BytesIO(proc.stdout)) as tar:
            tar.extractall(tmp / "before")
        cards = versus_cards()
        (tmp / "cards.json").write_text(json.dumps(cards))
        child = subprocess.run([sys.executable, "-c", RENDER_THERE, str(tmp / "before"), str(tmp / "cards.json"),
                                str(tmp / "before.json")], capture_output=True, text=True, timeout=1200, cwd=tmp)
        self.assertEqual(child.returncode, 0, child.stderr[-2000:])
        before = json.loads((tmp / "before.json").read_text())
        now = {"versus " + k: H.render(json.loads(v)).decode("utf-8") for k, v in cards.items()}
        now.update(single_pages_here())
        self.assertEqual(sorted(before), sorted(now))
        self.assertGreaterEqual(sum(k.startswith("versus ") for k in now), 18)
        self.assertGreaterEqual(sum(k.startswith("single ") for k in now), 6)
        segments = moved = 0
        for label in now:
            with self.subTest(page=label):
                self.assertNotEqual(before[label], now[label])                  # the rule did change every page
                self.assertEqual(masked(before[label], 0), masked(now[label], 1))
                then, here = (stack_geometry(json.loads(DATA.search(p).group(1)), side)[0]
                              for p, side in ((before[label], 0), (now[label], 1)))
                self.assertEqual(len(then), len(here))
                for (a0, b0), (a1, b1) in zip(then, here):                      # each value within 0.01 of before
                    self.assertLessEqual(max(abs(a1 - a0), abs(b1 - b0), abs((b1 - a1) - (b0 - a0))), 0.01 + EPS)
                segments += len(here)
                moved += sum(x != y for x, y in zip(then, here))
        self.assertGreater(segments, 0)
        self.assertGreater(moved, 0)                                            # the bar met a real move (SPLIT)
        for old, new in DRAWING_RULE:                                           # the rule's texts are the kit's own
            self.assertEqual((K.STYLE + K.SCRIPT).count(new), 1)
            if old not in new:                                                  # a replaced text is gone from it
                self.assertNotIn(old, K.STYLE + K.SCRIPT)

    def test_a_change_outside_the_drawing_rule_fails_the_bar(self):
        page = H.render(json.loads(versus_cards()["option A two quick off"])).decode("utf-8")
        self.assertEqual(masked(page, 1), masked(page, 1))
        for changed in (page.replace("<h1>", "<h1> ", 1), page.replace("Math.max(8, x", "Math.max(9, x", 1)):
            self.assertNotEqual(masked(page, 1), masked(changed, 1))
        with self.assertRaises(ValueError):
            masked(page + DRAWING_RULE[0][1], 1)                                # a rule text twice is no mask
        page = H.render(json.loads(versus_cards()["first_name three standard on"])).decode("utf-8")
        block = DATA.search(page).group(1)
        self.assertEqual(K.data_block(json.loads(block)), block)               # the block rebuilt as it was
        data = json.loads(block)
        seg = data["fig-allocation"]["tabs"][0]["panels"][0]["rows"][0]["marks"][0]["stack"][0]
        for key, value, counts in (("hi", seg["hi"] - 0.01, False), ("text", seg["text"] + " ", True)):
            changed = json.loads(block)
            changed["fig-allocation"]["tabs"][0]["panels"][0]["rows"][0]["marks"][0]["stack"][0][key] = value
            other = page.replace(block, K.data_block(changed))
            self.assertEqual(masked(page, 1) != masked(other, 1), counts, key)  # geometry masked, a hover text not


class Split(unittest.TestCase):
    def test_versus_html_re_exports_the_kits_parts(self):
        for name in ("CSP", "COLOURS", "SCRIPT", "STYLE", "SHOW_NUMBERS", "SETUP", "DIAGNOSTICS", "EVEN_NOTE",
                     "PAGE_IS_VIEW", "text", "data_block", "tag", "para", "items", "table", "figure", "numbers",
                     "setup", "bar", "state", "row", "tinted", "pos", "document", "after", "shown_strings",
                     "stack"):
            self.assertIs(getattr(H, name), getattr(K, name), name)
        import ui
        self.assertEqual(ui.RESULTS_PAGE_CSP, K.CSP)


# ---- the drawing, executed ------------------------------------------------------------------------------------------
# The kit's SCRIPT run whole in node's vm against a DOM stub: elements that keep their class, children, inline style
# and listeners; the data block and the figures' boxes; matchMedia answering the reduced-motion setting; no
# IntersectionObserver, so the bars grow at once. Each tab of each figure is clicked in turn. It prints, per figure and
# tab, every track's children: their class and inline style, as the script set them.
STUB = r"""
'use strict';
const vm = require('vm');
const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));
function Node(tag) {
  this.tagName = tag; this.className = ''; this.children = []; this.style = {}; this.attrs = {}; this.on = {};
}
Node.prototype.append = function (...ks) { ks.forEach(k => this.children.push(k)); };
Node.prototype.replaceChildren = function (...ks) { this.children = ks.slice(); };
Node.prototype.setAttribute = function (k, v) { this.attrs[k] = String(v); };
Node.prototype.getAttribute = function (k) { return this.attrs[k]; };
Node.prototype.addEventListener = function (e, f) { (this.on[e] = this.on[e] || []).push(f); };
Node.prototype.getBoundingClientRect = function () { return {left: 0, top: 0, right: 0, bottom: 0, width: 0, height: 0}; };
Node.prototype.walk = function (f) { f(this); this.children.forEach(c => c instanceof Node && c.walk(f)); };
Node.prototype.querySelectorAll = function (sel) {
  const cls = sel.replace(/^\./, ''), out = [];
  this.walk(n => { if (n !== this && n.className.split(' ').includes(cls)) out.push(n); });
  return out;
};
Node.prototype.querySelector = function (sel) { return this.querySelectorAll(sel)[0] || null; };
const figs = Object.keys(input.figures).map(name => {
  const f = new Node('figure'); f.attrs['data-fig'] = name;
  if (input.figures[name].kind !== 'mini') ['legend', 'tabs', 'chart'].forEach(c => {
    const n = new Node('div'); n.className = c; f.append(n);
  });
  return f;
});
const data = new Node('script'); data.textContent = JSON.stringify(input.figures);
const document = {
  getElementById: id => id === 'data' ? data : id === 'tip' ? new Node('div') : null,
  createElement: tag => new Node(tag),
  createTextNode: s => { const n = new Node('#text'); n.text = s; return n; },
  querySelectorAll: sel => sel === '[data-fig]' ? figs : [],
};
vm.runInNewContext(input.script, {document, window: {matchMedia: () => ({matches: input.still})},
                                  innerWidth: 1280, innerHeight: 800});
function tracks(f) {
  const out = [];
  f.walk(n => {
    if (n.className === 'track') out.push(n.children.map(c => ({cls: c.className, style: c.style})));
  });
  return out;
}
const drawn = {};
figs.forEach(f => {
  const name = f.attrs['data-fig'], tabs = f.querySelectorAll('.tab');
  drawn[name] = [tracks(f)];
  tabs.slice(1).forEach(t => { t.on.click.forEach(h => h()); drawn[name].push(tracks(f)); });
});
process.stdout.write(JSON.stringify(drawn));
"""


def drawn(figures, still):
    """{figure: [per tab, [per track, [{cls, style}]]]} as the kit's script draws figures (STUB)."""
    proc = subprocess.run([NODE, "-e", STUB], input=json.dumps({"script": K.SCRIPT, "figures": figures,
                                                                "still": still}),
                          capture_output=True, text=True, timeout=120)
    if proc.returncode:
        raise AssertionError(proc.stderr[-2000:])
    return json.loads(proc.stdout)


def expected_marks(spec):
    """The figure's drawn marks per tab, in the order its tracks are drawn: every mark that is not status words."""
    if spec["kind"] == "mini":
        return [[r["marks"][0] for r in spec["rows"] if r["marks"][0].get("state") is None]]
    return [[m for p in t["panels"] for r in p["rows"] for m in r["marks"] if m.get("state") is None]
            for t in spec["tabs"]]


def percent(value):
    """A CSS percentage the script set, as a Decimal: "37.2%" -> 37.2."""
    match = re.fullmatch(r"(-?[0-9.e+-]+)%", value)
    assert match, value
    return Decimal(match.group(1))


def css_rule(selector):
    """{property: value} of the kit's stylesheet rule for exactly selector."""
    match = re.search(re.escape(selector) + r"\{([^}]*)\}", K.STYLE)
    assert match, selector
    return dict(part.split(":", 1) for part in match.group(1).split(";") if part)


def px(value):
    return Decimal(value[:-2]) if value.endswith("px") else Decimal(value or "0")


class Drawing(unittest.TestCase):
    """The drawn geometry: an interval's line from its lower endpoint to its upper one exactly, a cap centred on each
    endpoint (one when they meet), a bar as long as its value, a dot centred on 0 for a bar of 0, nothing else in a
    track and nothing beyond 0 or 100 but a centred mark's half thickness; in both reduced-motion settings."""

    def check_track(self, mark, kids):
        """One mark's track against its own numbers."""
        if "stack" in mark:                             # each segment placed from its boundary to the next
            ended = Decimal(0)
            self.assertEqual([k["cls"] for k in kids], ["seg"] * len(mark["stack"]))
            for part, seg in zip(mark["stack"], kids):
                lo, hi = Decimal(repr(part["lo"])), Decimal(repr(part["hi"]))
                self.assertEqual(lo, ended)                                     # where the last one ended, exactly
                self.assertEqual(percent(seg["style"]["left"]), lo)
                width = re.fullmatch(r"calc\((.+)% - (.+)%\)", seg["style"]["width"])
                self.assertTrue(width, seg["style"]["width"])
                self.assertEqual((Decimal(width.group(1)), Decimal(width.group(2))), (hi, lo))
                self.assertTrue(lo <= hi <= 100)
                ended = hi
            return
        x = Decimal(repr(mark["x"]))
        bar = [k for k in kids if k["cls"] == "bar"]
        self.assertEqual(len(bar), 1)
        self.assertEqual(percent(bar[0]["style"]["width"]), x)                 # a bar's length is its value
        self.assertTrue(0 <= x <= 100)
        zero = [k for k in kids if k["cls"] == "zero"]
        self.assertEqual(len(zero), 1 if x == 0 else 0)
        if zero:
            self.assertEqual(percent(zero[0]["style"]["left"]), 0)
        ci, caps = [k for k in kids if k["cls"] == "ci"], [k for k in kids if k["cls"] == "cap"]
        self.assertEqual(sorted({k["cls"] for k in kids}), sorted({"bar"} | ({"zero"} if zero else set())
                                                                   | ({"ci", "cap"} if ci else set())))
        if mark.get("lo") is None:
            self.assertEqual((ci, caps), ([], []))
            return
        lo, hi = Decimal(repr(mark["lo"])), Decimal(repr(mark["hi"]))
        self.assertEqual(len(ci), 1)
        left = percent(ci[0]["style"]["left"])
        width = re.fullmatch(r"calc\((.+)% - (.+)%\)", ci[0]["style"]["width"])
        self.assertTrue(width, ci[0]["style"]["width"])
        right = left + Decimal(width.group(1)) - Decimal(width.group(2))
        self.assertEqual((left, right), (lo, hi))                               # the endpoints, exactly
        self.assertEqual((Decimal(width.group(1)), Decimal(width.group(2))), (hi, lo))
        self.assertTrue(0 <= lo <= hi <= 100)
        self.assertEqual([percent(c["style"]["left"]) for c in caps], [lo] if lo == hi else [lo, hi])

    def check_figures(self, figures):
        for still in (True, False):
            got = drawn(figures, still)
            for name, spec in figures.items():
                want = expected_marks(spec)
                self.assertEqual(len(got[name]), len(want), name)
                for t, (marks, tracks) in enumerate(zip(want, got[name])):
                    self.assertEqual(len(tracks), len(marks), (name, t))
                    for mark, kids in zip(marks, tracks):
                        with self.subTest(figure=name, tab=t, still=still, mark=mark.get("text")):
                            self.check_track(mark, kids)

    @unittest.skipUnless(NODE, "needs node")
    def test_the_five_intervals_and_the_ends_of_the_scale(self):
        intervals = [(100, 100), (0, 0), (37.2, 37.9), (0, 100), (99.7, 100)]
        rows = [K.row(f"[{lo}, {hi}]", [K.bar(hi, (lo, hi), "t", "s")]) for lo, hi in intervals]
        rows += [K.row(f"{x}", [K.bar(x, None, "t", "s")]) for x in (0, 0.2, 37.9, 100)]
        rows += [K.row("0 with [0, 3]", [K.bar(0, (0, 3), "t", "s")])]
        series = [{"label": "", "colour": "accent"}]
        figures = {"fig-bars": {"kind": "bars", "series": series, "even": 50,
                                "tabs": [{"label": "one", "panels": [{"rows": rows}]},
                                         {"label": "two", "panels": [{"rows": rows[::-1]}]}]},
                   "fig-mini": {"kind": "mini", "series": series, "even": 50, "rows": rows}}
        self.check_figures(figures)
        for still in (True, False):                     # [100, 100] is one cap at 100, the line of no width at 100
            kids = drawn(figures, still)["fig-bars"][0][0]
            self.assertEqual([(k["cls"], k["style"].get("left")) for k in kids],
                             [("bar", None), ("ci", "100%"), ("cap", "100%")])
            self.assertEqual(kids[1]["style"]["width"], "calc(100% - 100%)")

    def test_the_caps_and_the_dot_are_centred_on_their_point(self):
        """A cap and the dot of a bar of 0 are centred on their point by their own half width, so a cap at 0 or 100
        overhangs the track's edge by half its thickness and no more (the track does not clip); nothing else has a
        left or a right beyond the scale."""
        for selector in (".sl .cap", ".sl .zero"):
            rule = css_rule(selector)
            margin_left = px(rule["margin-left"]) if "margin-left" in rule else px(rule["margin"].split()[3])
            self.assertEqual(margin_left, -px(rule["width"]) / 2, selector)
            self.assertEqual(rule["position"], "absolute")
        self.assertNotIn("overflow", css_rule(".sl .track"))
        self.assertNotIn(".ci::", K.STYLE)              # the caps are marks of their own, not the line's corners

    def test_no_drawn_value_is_floored_widened_or_rounded_by_the_script(self):
        """The only Math.max and Math.min in the script place the hover box (pixels, not a drawn value); no rule of
        the stylesheet gives a drawn mark a minimum size."""
        clamps = [line.strip() for line in K.SCRIPT.splitlines() if re.search(r"Math\.(max|min|round|floor|ceil)",
                                                                              line)]
        self.assertEqual(len(clamps), 2)
        self.assertTrue(all(line.startswith("tip.style.") for line in clamps), clamps)
        for selector in (".sl .bar", ".sl .ci", ".sl .cap", ".sl .seg", ".sl .zero"):
            self.assertFalse({"min-width", "min-height"} & set(css_rule(selector)), selector)

    @unittest.skipUnless(NODE, "needs node")
    def test_every_mark_of_the_corpus_is_drawn_at_its_own_numbers(self):
        """Every figure of every page of the corpus (both kinds), every tab, both motion settings."""
        pages = {"versus " + k: H.render(json.loads(v)).decode("utf-8") for k, v in versus_cards().items()}
        pages.update(single_pages_here())
        seen = {"intervals": 0, "of no width": 0, "reaching 100": 0, "stacks": 0, "bars of 0": 0}
        for label, page in pages.items():
            block = re.search(r'<script type="application/json" id="data">(.*?)</script>', page, re.S).group(1)
            figures = json.loads(block)
            with self.subTest(page=label):
                self.check_figures(figures)
            for m in (m for spec in figures.values() for t in expected_marks(spec) for m in t):
                seen["stacks"] += "stack" in m
                seen["bars of 0"] += m.get("x") == 0
                if m.get("lo") is not None:
                    seen["intervals"] += 1
                    seen["of no width"] += m["lo"] == m["hi"]
                    seen["reaching 100"] += m["hi"] == 100
        self.assertGreaterEqual(seen["intervals"], 100, seen)                   # every case the rule covers, met
        self.assertTrue(all(seen.values()), seen)


class Stack(unittest.TestCase):
    """A stack's geometry, from its one home (stack): boundaries rounded on the running count, so the segments tile
    from 0 exactly (in the emitted decimals), the stack ends at round(100 * sum / total, 2) and never past 100, and a
    segment's length is within 0.01 of its own share rounded alone."""

    def check(self, counts, total):
        segments = K.stack([(c, f"c{i}", f"t{i}") for i, c in enumerate(counts)], total)
        self.assertEqual([(s["colour"], s["text"]) for s in segments], [(f"c{i}", f"t{i}") for i in range(len(counts))])
        self.assertEqual(segments[0]["lo"], 0)
        for last, seg in zip(segments, segments[1:]):
            self.assertEqual(seg["lo"], last["hi"])                             # exact: the same value
        for seg, c in zip(segments, counts):
            self.assertLessEqual(seg["lo"], seg["hi"])
            if sum(counts) <= total:                    # (counts past the whole are held at 100: lengths cut short)
                self.assertLessEqual(abs((seg["hi"] - seg["lo"]) - round(100 * c / total, 2)), 0.01 + EPS)
        self.assertEqual(segments[-1]["hi"], min(100.0, round(100 * sum(counts) / total, 2)))
        self.assertLessEqual(segments[-1]["hi"], 100)
        return segments

    def test_ten_seven_fifteen_of_thirty_two_ends_at_exactly_100(self):
        """The re-check's stack: its lengths rounded alone, 31.25 + 21.88 + 46.88, ended at 100.01."""
        segments = self.check((10, 7, 15), 32)
        self.assertEqual([(s["lo"], s["hi"]) for s in segments], [(0.0, 31.25), (31.25, 53.12), (53.12, 100.0)])
        self.assertEqual(round(round(100 * 10 / 32, 2) + round(100 * 7 / 32, 2) + round(100 * 15 / 32, 2), 2), 100.01)

    def test_every_three_counts_up_to_sixty(self):
        """Every split of every whole from 1 to 60 into three counts ends at exactly 100."""
        cases = 0
        for total in range(1, 61):
            for a in range(total + 1):
                for b in range(total + 1 - a):
                    segments = K.stack([(a, "", ""), (b, "", ""), (total - a - b, "", "")], total)
                    self.assertEqual(segments[-1]["hi"], 100.0, (a, b, total))
                    self.assertTrue(segments[0]["lo"] == 0 and segments[1]["lo"] == segments[0]["hi"]
                                    and segments[2]["lo"] == segments[1]["hi"], (a, b, total))
                    cases += 1
        self.assertEqual(cases, sum((t + 1) * (t + 2) // 2 for t in range(1, 61)))

    def test_counts_short_of_the_whole_and_past_it(self):
        self.check((3, 2, 1), 10)                                               # ends at 60
        self.check((1, 1, 1), 7)
        self.check((0, 0, 0), 5)
        self.check((6, 6), 10)                                                  # past the whole: held at 100

    def test_every_stack_of_the_corpus(self):
        """Every stack the corpus's pages draw (the versus page's allocation, the one figure that stacks) ends at the
        share of its card's counts, from its card's own counts and denominator; the single-target page draws none."""
        import versus_page as VP
        stacks = 0
        for label, card_json in versus_cards().items():
            card = json.loads(card_json)
            data = json.loads(DATA.search(H.render(card).decode("utf-8")).group(1))
            drawn = [m["stack"] for spec in data.values() if spec.get("kind") != "mini" for t in spec["tabs"]
                     for p in t["panels"] for r in p["rows"] for m in r["marks"] if "stack" in m]
            arm = VP.primary(card)
            metrics = VP.metrics(card, arm)
            wanted = []
            for a, b in VP.run_pairs(card):
                rec = metrics.get(f"{a}:{b}/allocation")
                counts, valid = VP.allocation_rates(rec, (a, b)) if rec else ({}, 0)
                if valid:
                    wanted.append((sum(counts.values()), valid))
            with self.subTest(card=label):
                self.assertEqual(len(drawn), len(wanted))
                for segments, (whole, valid) in zip(drawn, wanted):
                    self.assertEqual(segments[0]["lo"], 0)
                    self.assertTrue(all(s["lo"] == r["hi"] for r, s in zip(segments, segments[1:])))
                    self.assertEqual(segments[-1]["hi"], round(100 * whole / valid, 2))
                    self.assertLessEqual(segments[-1]["hi"], 100)
                stacks += len(drawn)
                if label == SPLIT:                  # the re-check's stack, drawn from the card's own counts
                    self.assertEqual([(s["lo"], s["hi"]) for s in drawn[0]],
                                     [(0.0, 31.25), (31.25, 53.12), (53.12, 100.0)])
        for page in single_pages_here().values():
            self.assertNotIn('"stack"', DATA.search(page).group(1))
        self.assertGreaterEqual(stacks, 30)


class Kit(unittest.TestCase):
    def test_one_escaping_function_for_the_markup(self):
        self.assertEqual(K.text('<script>"a" & \'b\'</script>'),
                         "&lt;script&gt;&quot;a&quot; &amp; &#x27;b&#x27;&lt;/script&gt;")
        self.assertEqual(K.text("a\x1b[2Jb‮c\nd"), "a\\x1b[2Jb\\u202ec\\nd")     # displayed first, uncut
        self.assertEqual(len(K.text("W" * 10000)), 10000)
        self.assertEqual(K.tag("p", K.text("<b>"), cls='x" onclick="y'),
                         '<p class="x&quot; onclick=&quot;y">&lt;b&gt;</p>')

    def test_the_data_block_cannot_close_its_script_element(self):
        block = K.data_block({"a": ["</script><!-- &   ‮ <b>"], "b": 1.5})
        self.assertFalse(set("<>&") & set(block))
        self.assertNotIn(" ", block)
        self.assertEqual(json.loads(block), {"a": ["</script><!-- & \\u2028 \\u202e <b>"], "b": 1.5})
        with self.assertRaises(ValueError):
            K.data_block({"x": float("nan")})

    def test_the_shell_and_a_pages_second_script(self):
        plain = K.document("T", "<p>x</p>", {"f": {"rows": []}})
        self.assertEqual(plain, K.document("T", "<p>x</p>", {"f": {"rows": []}}, "", None))
        self.assertEqual(plain.count("<script"), 2)
        self.assertIn(f'<meta http-equiv="Content-Security-Policy" content="{K.text(K.CSP)}">', plain)
        more = K.document("T", "<p>x</p>", {}, extra_style=".z{color:red}", extra_script="void 0;")
        self.assertEqual(more.count("<script"), 3)
        self.assertIn(f"<script>{K.SCRIPT}</script>\n<script>void 0;</script>", more)
        self.assertIn(".z{color:red}</style>", more)
        for text_ in (plain, more):
            self.assertNotIn("//", text_)
            self.assertEqual(text_.count("http"), text_.count("http-equiv"))
        self.assertEqual(K.as_bytes(plain), plain.encode("utf-8"))

    def test_the_helpers_markup(self):
        self.assertEqual(K.pills(["a", "<b>"]), '<div class="pills"><span class="pill">a</span>'
                                                '<span class="pill">&lt;b&gt;</span></div>')
        self.assertEqual(K.takeaway("T", ["x"]), '<h2>T</h2><div class="takeaway"><ul><li>x</li></ul></div>')
        fold = K.identity_table([("pin", "abc"), ("n", 3)], "trust <me>")
        self.assertIn("<th scope=\"row\">pin</th><td>abc</td>", fold)
        self.assertTrue(fold.endswith("<p>trust &lt;me&gt;</p></details>"))
        panel = K.teaser_panel("Class I", "professed", "<div>9</div>", "tz-x", "what")
        self.assertIn('<div class="tz-vis" data-fig="tz-x"></div>', panel)
        self.assertEqual(K.bar(150, (-5, 50.555), "t", "100"), {"x": 100.0, "lo": 0.0, "hi": 50.55, "text": "t",
                                                                 "short": "100", "tip": []})
        self.assertEqual(K.state("not run"), {"state": "not run"})
        self.assertEqual(K.tinted(K.state("w"), "accent"), {"state": "w"})

    def test_the_font_stack_is_the_figures_own(self):
        import ordo
        self.assertEqual(K.FONT, ordo.FONT)
        self.assertNotIn("ordo", sys.modules["results_kit"].__dict__)       # the kit never depends on ordo

    def test_no_external_reference_in_the_stylesheet_or_the_script(self):
        for part in (K.STYLE, K.SCRIPT, K.style()):
            for bad in ("http", "//", "url(", "@import", "innerHTML", "eval("):
                self.assertNotIn(bad, part, bad)


if __name__ == "__main__":
    unittest.main()
