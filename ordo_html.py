"""ordo_html: the results page of a cross-type run, ordo/results.html beside ordo/scorecard.md (the results-pages
ruling, P3, P4 and P7). A thin module over results_kit, in the grammar every results page shares: head, run line,
chips, teaser, figures, the highlighted box, the limits, the foot.

    render(parts, report, shown) -> bytes      the page, UTF-8, self-contained, scripts-off readable

Word for word by construction: parts are ordo.answer_parts(shown, rule), the very parts ordo.answer_text joins into
scorecard.md in the same call (ordo.write_results), with the errand rule read once for both; report is report.md's
text from that call; shown is the shown card (ordo's shown_view), read only for drawing positions and the order of
named rows and three-way shares. Every sentence, cell, rounding and status on the page is one of those texts, shown
with markdown's own marks taken off (plain: bold, and the backslash before a character md escaped); the page adds
only its structural words (the constants below). An ordering is printed exactly as the answer prints it; the page
ranks nothing. A number whose status is not ok is its words, never a bar.

Data flow: ordo.write_results -> answer_parts, report_md, shown -> head, teaser, figures (names, by frame, memory on
and off, three-way items, standings), box, limits, foot -> results_kit.document -> bytes.
"""
import re

import ordo as O
import results_kit as K
from results_kit import bar, items, para, row, state, table, tag, text, tinted

# ---- the page's own words: structure only, never a result ------------------------------------------------------------
TITLE = "Types compared: {types}"
FIG_FRAMES = "By frame"
FIG_ARMS = "Memory on and off side by side"
STANDINGS = "Standings, as results.svg titles them"
BOX_TITLE = "What the answer table supports"
LIMITS = "Limits"
RED_FLAGS, VALIDITY, DIAGNOSTICS, CLASS_III = "Red flags", "Validity", "Diagnostics", "Class III"
THREE_WAY = "Three-way items (descriptive)"   # report.md's own heading
NOTES = "What these numbers are"              # report.md's own heading
RESCORED = "rescored"
DEPLOYER = "deployer share"                   # the answer's own words in its ordering cell
CONDITION_COLOURS = {"with": "accent", "without": "orange"}      # memory on, memory off: as the other pages' arms
IDENTITY = (("plan fingerprint", ("bootstrap", "fingerprint")), ("bootstrap B", ("bootstrap", "B")),
            ("recipe", ("recipe",)), ("card schema", ("schema",)))
ROLE_FREE = ("role-free.I", "role-free.II", "role-free.III")
TEASER_TASK = "role-free"
TEASER_ALL = "Every condition is in the figures below: {figures}."
TEASER_STYLE = ".sl .tz-cond{margin:0 0 10px;font-size:14px;color:var(--ink-soft)}.sl .tz-cond strong{color:var(--ink)}"
CODE_SPAN = re.compile(r"(?<!\\)`([^`]*)`")      # markdown's inline code span (a backtick md escaped is not one)
UNESCAPE = re.compile(r"\\([" + re.escape(O.MARKDOWN_SPECIAL) + "])")
DEPLOYER_SHARE = re.compile(r"; deployer share (.+)$")
THREE_WAY_LINE = re.compile(r"^- (?P<what>.+?): first choices (?P<shares>.+) \((?P<valid>\d+/\d+) valid\)\.$")


def plain(line):
    """A line of scorecard.md or report.md as the page shows it: markdown's bold marks and line-break spaces taken
    off, and the backslash md put before a special character removed. The words are the text page's."""
    return UNESCAPE.sub(r"\1", line.rstrip().replace("**", "").strip("*"))


def cells_of(line):
    """A markdown table row's cells, split on the pipes md did not escape, each plain."""
    inner = line.strip()[1:-1]
    return [plain(c.strip()) for c in re.split(r"(?<!\\)\|", inner)]


def joined(words):
    """Names joined as a title joins them: "a and b", "a, b and c"."""
    return " and ".join(words) if len(words) < 3 else ", ".join(words[:-1]) + " and " + words[-1]


def report_sections(report):
    """report.md's sections: {condition name: {heading: [lines]}} for each "# Behind the answer, ..." block, and its
    closing section ("What these numbers are") under the key None."""
    out, cond, head = {None: {}}, None, None
    for line in report.splitlines():
        if line.startswith("# Behind the answer, "):
            cond, head = line[len("# Behind the answer, "):], None
            out[cond] = {}
        elif line.startswith("# "):
            cond, head = None, None
        elif line.startswith("## "):
            head = line[3:]
            cond = None if head == NOTES else cond
            out[cond][head] = []
        elif head is not None and line.strip():
            out[cond][head].append(line)
    return out


def section_table(lines):
    """A report section's markdown table as (heading cells, rows), and its other lines; ([], [], lines) without."""
    rows = [cells_of(line) for line in lines if line.startswith("|") and not line.startswith("|---")]
    rest = [line for line in lines if not line.startswith("|")]
    return (rows[0] if rows else []), rows[1:], rest


def spans(line):
    """A text page line's parts as (words, is_code): markdown's bold marks and line-break spaces taken off as plain
    does, then the line split at its inline code spans; outside them md's backslash escapes are removed, inside them
    the words are kept as they are."""
    parts = CODE_SPAN.split(line.rstrip().replace("**", "").strip("*"))
    return [(p, True) if i % 2 else (UNESCAPE.sub(r"\1", p), False) for i, p in enumerate(parts)]


def words(line):
    """A text page line's words as the page shows them: plain, and each code span's words without its backticks."""
    return "".join(p for p, _ in spans(line))


def rich(line):
    """A text page line as the page's markup: each inline code span a code element with the same words and no
    backticks, every part escaped by text (results_kit.Markup, which the kit's helpers pass through as it is)."""
    return K.Markup("".join(tag("code", text(p)) if code else text(p) for p, code in spans(line)))


def unmarked(line):
    return line[2:] if line.startswith("- ") else line


def item(line):
    """A report.md list line's words without its marker (words)."""
    return words(unmarked(line))


def item_markup(line):
    """A list line as the page's markup, without its marker (rich)."""
    return rich(unmarked(line))


# ---- head and teaser ------------------------------------------------------------------------------------------------
def page_title(types):
    """The page's title, its h1: the types compared, joined (live.py's results card shows the same string, by this
    function)."""
    return TITLE.format(types=joined(types))


def head(parts, shown):
    """The title from the card's types, the run line (the head's first line), the rescored line and any further head
    line under it, and the chips: the head's calibration line split where it joins its words, then a rescore's."""
    lines = [plain(x) for x in parts["head"]]
    calibration = plain(shown["head"][1]) if len(shown["head"]) > 1 else ""
    under = [x for x in lines[1:] if x != calibration]
    chips = [c for c in calibration.split(" · ") if c] + ([RESCORED] if shown.get("rescored") else [])
    return tag("header", tag("h1", text(page_title(shown["types"])))
               + para(lines[0], cls="runline") + "".join(para(x, cls="headline") for x in under)
               + K.pills(chips), cls="head")


def pair_mark(cells, j, rec, row_id):
    """One pair's mark in a frame: its bar with its interval, the cell's text as the answer prints it, or the cell's
    words when it has no number or its number is not a result (Class III not valid)."""
    words = cells[1 + j]
    if rec["value"] is None or (row_id == "role-free.III" and rec.get("validity") != "valid"):
        return state(words)
    return bar(rec["value"], rec.get("ci"), words, words.split(" ")[0])


def by_id(shown_cond):
    return {r["id"]: r for r in shown_cond["rows"]}


def teaser(parts, shown, figures):
    """Role-free Class I, II and III, the rows that ran: each pair's cell, its small bar against the even line at 50,
    and the row's ordering as the answer prints it, for the card's first condition (memory on, in a run of both). With
    more than one condition the teaser says which it shows, in the scorecard's own words (its condition heading), and
    names the figures below that hold every condition (TEASER_ALL); with one, it is the kit's teaser as it was."""
    c = parts["conditions"][0]
    rows_by_id, recs = {r["id"]: r for r in c["rows"]}, by_id(shown["conditions"][c["cond"]])
    panels = []
    for rid in ROLE_FREE:
        if rid not in rows_by_id:
            continue
        cells, rec = rows_by_id[rid]["cells"], recs[rid]
        fig = f"tz-{rid.split('.')[1]}"
        marks = [(c["columns"][1 + j], pair_mark(cells, j, rec["pairs"][p], rid))
                 for j, p in enumerate(shown["pairs"])]
        figures[fig] = {"kind": "mini", "even": 50, "series": [{"colour": "accent"}],
                        "rows": [row(name, [m]) for name, m in marks]}
        cols = "".join(tag("div", (tag("div", text(cells[1 + j].split(" [")[0]), cls="tz-big")
                                   + tag("div", text("[" + cells[1 + j].split(" [", 1)[1]), cls="tz-iv")
                                   if " [" in cells[1 + j] and "state" not in m else
                                   tag("div", text(cells[1 + j]), cls="tz-state"))
                               + tag("div", text(name), cls="tz-name"), cls="tz-col")
                       for j, (name, m) in enumerate(marks))
        panels.append(K.teaser_panel(cells[0].split(", ")[-1], TEASER_TASK, tag("div", cols, cls="tz-out"), fig,
                                     cells[-1]))
    if not panels:
        return ""
    if len(parts["conditions"]) == 1:
        return K.teaser(panels)
    shown_figures = ([O.NAMES_HEADING] if parts["named"] else []) + [FIG_FRAMES, FIG_ARMS] + (
        [THREE_WAY] if any(rec.get("three_way") for rec in shown["conditions"].values()) else [])
    caption = tag("figcaption", tag("strong", text(c["title"])) + " " + text(TEASER_ALL.format(
        figures=joined(shown_figures))), cls="tz-cond")
    return tag("figure", caption + tag("div", "".join(panels), cls="teaser"), cls="teaser-fig")


# ---- figures --------------------------------------------------------------------------------------------------------
def names_figure(parts, shown, figures):
    """"The names you gave": per condition a panel, per name and class two bars, the name's index and its twin's
    (accent, charcoal), no even line; an insufficient name its word; the difference as the row's note. The setup holds
    what each class's numbers are, the notes the names block's other lines, the numbers its tables."""
    named = parts["named"]
    if not named:
        return ""
    block = named["block"]
    last_table = max(i for i, line in enumerate(block) if line.startswith("|"))
    after = [x for x in block[last_table + 1:]]
    while after and not after[0].strip():
        after.pop(0)
    split = after.index("") if "" in after else len(after)
    leads, rest = [rich(x) for x in after[:split]], [x for x in after[split:] if x.strip()]
    panels, tables = [], []
    for t in named["tables"]:
        effects = [s for _, _, s in O.named_rows(shown["conditions"][t["cond"]])]
        rows_ = []
        for cells, s in zip(t["rows"], effects):
            label = plain(cells[0])
            if cells[1] == "insufficient":
                rows_.append(row(label, [state(cells[1])]))
                continue
            lv = s["levels"]
            rows_.append(row(label, [tinted(bar(lv["target_index"], None, cells[1], cells[1]), "accent"),
                                     tinted(bar(lv["twin_index"], None, cells[2], cells[2]), "charcoal")],
                             notes=[f"{t['columns'][3]} {cells[3]}"]))
        panels.append({"title": t["title"], "rows": rows_})
        tables.append(table(t["columns"], [[plain(cells[0]), *cells[1:]] for cells in t["rows"]], caption=t["title"]))
    figures["fig-names"] = {"kind": "bars", "even": None, "series": [
        {"label": named["tables"][0]["columns"][1], "colour": "accent"},
        {"label": named["tables"][0]["columns"][2], "colour": "charcoal"}],
        "tabs": [{"label": "", "panels": panels}]}
    notes = [item_markup(x) for x in rest]
    return K.figure("fig-names", O.NAMES_HEADING, None, leads, tables, notes=notes)


def frame_tabs(parts, shown, deployer=True):
    """The rows that ran, in the answer's order: {label: [(condition parts, row parts, shown row)]}."""
    tabs = {}
    for c in parts["conditions"]:
        recs = by_id(shown["conditions"][c["cond"]])
        for r in c["rows"]:
            tabs.setdefault(r["cells"][0], []).append((c, r, recs[r["id"]]))
    return tabs


def frames_figure(parts, shown, figures):
    """"By frame": a tab per row that ran; per condition a panel of one bar per pair with its interval, against the
    even line at 50, and an authority row's deployer share as one more bar; the panel's note is the row's ordering
    cell as the answer prints it. The setup holds the types heading and sentence and the answer's "Above 50" line;
    the numbers are the answer's tables cell for cell."""
    tabs = []
    for label, entries in frame_tabs(parts, shown).items():
        panels = []
        for c, r, rec in entries:
            cells = r["cells"]
            marks = [row(c["columns"][1 + j], [tinted(pair_mark(cells, j, rec["pairs"][p], r["id"]),
                                                      CONDITION_COLOURS.get(c["cond"], "accent"))])
                     for j, p in enumerate(shown["pairs"])]
            share, ds = DEPLOYER_SHARE.search(cells[-1]), rec.get("deployer_share")
            if share and ds and ds.get("value") is not None:
                marks.append(row(DEPLOYER, [tinted(bar(ds["value"], ds.get("ci"), share.group(1),
                                                       share.group(1).split(" ")[0]),
                                                   CONDITION_COLOURS.get(c["cond"], "accent"))]))
            panels.append({"title": c["title"], "rows": marks, "notes": [cells[-1]]})
        tabs.append({"label": label, "panels": panels})
    figures["fig-frames"] = {"kind": "bars", "even": 50, "series": [{"label": "", "colour": "accent"}], "tabs": tabs}
    tables = [table(c["columns"], [r["cells"] for r in c["rows"]], caption=c["title"]) for c in parts["conditions"]]
    setup_lines = [rich(x) for x in parts["types"]] + [O.ABOVE_50]
    return K.figure("fig-frames", FIG_FRAMES, None, setup_lines, tables, tabs=len(tabs) > 1)


def arms_figure(parts, shown, figures):
    """With memory on and off: per row that ran a tab, per pair one bar per condition (memory on accent, memory off
    orange), each with its cell's text; the numbers stand in "By frame"'s tables."""
    if len(parts["conditions"]) < 2:
        return ""
    tabs = []
    for label, entries in frame_tabs(parts, shown).items():
        rows_ = [row(entries[0][0]["columns"][1 + j],
                     [tinted(pair_mark(r["cells"], j, rec["pairs"][p], r["id"]),
                             CONDITION_COLOURS.get(c["cond"], "accent")) for c, r, rec in entries])
                 for j, p in enumerate(shown["pairs"])]
        tabs.append({"label": label, "panels": [{"rows": rows_}]})
    figures["fig-arms"] = {"kind": "bars", "even": 50, "series": [
        {"label": c["title"], "colour": CONDITION_COLOURS.get(c["cond"], "accent")} for c in parts["conditions"]],
        "tabs": tabs}
    tables = [table(c["columns"], [r["cells"] for r in c["rows"]], caption=c["title"]) for c in parts["conditions"]]
    return K.figure("fig-arms", FIG_ARMS, None, None, tables, tabs=len(tabs) > 1)


def three_way_figure(sections, shown, figures):
    """The triangle's three-way items, as report.md lists them (descriptive): per condition and item, one bar per
    type at its first-choice share against one third, each labelled with report.md's own share; the item's line is
    the figure's note (on the page with scripts off) and its numbers a table of those shares."""
    panels, tables, notes = [], [], []
    for cond, rec in shown["conditions"].items():
        lines = sections.get(O.CONDITION_NAMES[cond], {}).get(THREE_WAY, [])
        for (comp, tw), line in zip(rec.get("three_way", {}).items(), lines):
            m = THREE_WAY_LINE.match(line)
            if not m:
                continue
            shares = dict(s.rsplit(" ", 1) for s in m.group("shares").split(", "))
            rows_ = [row(t, [state(shares[t])] if v is None else [bar(100 * v, None, shares[t], shares[t])])
                     for t, v in tw["shares"].items()]
            title = m.group("what") + (f", {O.CONDITION_NAMES[cond]}" if len(shown["conditions"]) > 1 else "")
            panels.append({"title": title, "rows": rows_})
            notes.append(item_markup(line))
            tables.append(table(["", m.group("what")], [[t, shares[t]] for t in tw["shares"]], caption=title))
    if not panels:
        return ""
    figures["fig-three"] = {"kind": "bars", "even": round(100 / 3, 2), "series": [{"label": "", "colour": "accent"}],
                            "tabs": [{"label": "", "panels": panels}]}
    return K.figure("fig-three", THREE_WAY, None, None, tables, notes=notes)


def standings(shown):
    """The triangle's standings: the strings results.svg titles its dots with (ordo.standing_titles), one per row
    and type, in a small table per condition; nothing is drawn."""
    out = []
    for cond, rec in shown["conditions"].items():
        rows_ = [[s] for r in rec["rows"] for s in O.standing_titles(r)]
        if rows_:
            caption = STANDINGS + (f", {O.CONDITION_NAMES[cond]}" if len(shown["conditions"]) > 1 else "")
            out.append(table([], rows_, caption=caption, cls="mini"))
    return "".join(out)


# ---- box, limits, foot ------------------------------------------------------------------------------------------------
def box(parts):
    """What the answer table supports: each condition's rows with their ordering cells, then the answer's closing
    lines (too wide, the errand rule, Class III, the default-favour notes), all as the answer prints them."""
    tables = [table(["", c["columns"][-1]], [[r["cells"][0], r["cells"][-1]] for r in c["rows"]], caption=c["title"],
                    cls="mini") for c in parts["conditions"]]
    closing = items([rich(x) for x in parts["closing"]]) if parts["closing"] else ""
    return tag("h2", text(BOX_TITLE)) + tag("div", "".join(tables) + closing, cls="takeaway")


def limits(parts, sections):
    """What the run did not measure (each condition's line) and what these numbers are (report.md's last section)."""
    lines = [x for c in parts["conditions"] for x in c["under"] if x.startswith("Not measured in this run")]
    notes = [item_markup(x) for x in sections[None].get(NOTES, [])]
    return (tag("h2", text(LIMITS)) + (items(lines) if lines else "")
            + tag("h3", text(NOTES)) + items(notes))


def foot(parts, sections, shown):
    """Per condition: the answer's red-flags and validity line, report.md's red-flags table and lines, its Class III
    and validity lines and its diagnostics; then the run time, the page's note and the identity."""
    out = []
    many = len(parts["conditions"]) > 1
    for c in parts["conditions"]:
        sec = sections.get(O.CONDITION_NAMES[c["cond"]], {})
        heads, rows_, rest = section_table(sec.get(RED_FLAGS, []))
        body = para(rich(c["under"][1])) + table(heads, rows_, cls="mini") + items([item_markup(x) for x in rest])
        if sec.get(CLASS_III):
            body += tag("h3", text(CLASS_III)) + items([item_markup(x) for x in sec[CLASS_III]])
        body += tag("h3", text(VALIDITY)) + items([item_markup(x) for x in sec.get(VALIDITY, [])])
        body += tag("h3", text(DIAGNOSTICS)) + items([item_markup(x) for x in sec.get(DIAGNOSTICS, [])])
        out.append((tag("h3", text(c["title"])) if many else "") + body)
    ident = []
    for label, keys in IDENTITY:
        value = shown
        for k in keys:
            value = value.get(k) if isinstance(value, dict) else None
        if value is not None:
            ident.append((label, value))
    run_time = para(parts["run_time"]) if parts["run_time"] else ""
    return tag("footer", tag("h2", text(RED_FLAGS)) + "".join(out) + run_time + para(K.PAGE_IS_VIEW, cls="fine")
               + K.identity_table(ident), cls="foot")


# ---- the page ---------------------------------------------------------------------------------------------------------
def page(parts, report, shown):
    """results.html as text, from the answer's parts, report.md's text and the shown card."""
    figures, sections = {}, report_sections(report)
    main = (head(parts, shown) + teaser(parts, shown, figures) + names_figure(parts, shown, figures)
            + frames_figure(parts, shown, figures) + arms_figure(parts, shown, figures)
            + three_way_figure(sections, shown, figures) + standings(shown) + box(parts)
            + limits(parts, sections) + foot(parts, sections, shown))
    return K.document(TITLE.format(types=joined(shown["types"])), main, figures,
                      extra_style=TEASER_STYLE if len(parts["conditions"]) > 1 else "")


def render(parts, report, shown):
    """The one interface: the answer's parts, report.md's text and the shown card in, the page's bytes out."""
    return K.as_bytes(page(parts, report, shown))
