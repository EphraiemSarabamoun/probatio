#!/usr/bin/env python3
"""single_html: results.html, the results page of a single-target run, beside the scorecard.md it views.

One interface:

    render(lines, shown) -> bytes     the page, UTF-8, self-contained (results_kit's shell, CSP and script)

lines is probatio.scorecard_lines' parts and shown the shown card (probatio.shown_view), the two things
probatio.write_scorecard holds when it writes scorecard.md; it builds lines once and hands the same parts to
scorecard.md and to this page (the results-pages ruling, P4), so every line the page prints is scorecard.md's own by
construction: the head line, every line of its fenced block, every cell of its tables, every diagnostic and validity
check, every note. This module formats no number and writes no sentence that states a result; shown supplies only
the geometry of the bars (each bar sits where its printed number says). Its own words are structure only: the
title, figure titles, table headers and series names, and the README's status sentence quoted word for word (P6).

The page, in the grammar every kind's page shares (P1, P5): the head (the title, scorecard.md's head line as the run
line, chips: the tier, the memory, a custom roster's labels, a rescore, the instrument's status); a teaser of three
class panels, each condition's score large with its interval and its status (the small bars against the even line at
0, no measured difference); "By class", one bar per condition per class with its interval; "Components", the target's
and the control's level per share-valued component ("records examined" is a mean count, printed, never a bar); with
memory both the difference rows as a table; the highlighted box holding scorecard.md's head block as it prints it
(scorecard.md's verdict lines: a single target has no "supports" list); the notes and the instrument's status; the
foot: red flags, diagnostics, validity checks, the closing sentence and the identity. Each figure keeps its numbers in
an open table, so the page is complete without scripts.

Standard library only, Python 3.9 or later. No network, no model, no file read or written.
"""
import re

import probatio as P
import results_kit as K
from results_kit import bar, figure, items, para, row, state, table, tag, text

# ---- the page's own words: structure only, never a result ------------------------------------------------------------
TITLE = "Scorecard: {name} ({type})"
FIG_CLASSES = "By class"
FIG_COMPONENTS = "Components"
DIFFERENCE_CAPTION = "Memory on minus memory off, by class"   # the box keeps the block's own heading line
BOX_TITLE = "The scorecard's head, as scorecard.md prints it"
NOTES = "Notes"
STATUS_HEAD = "The instrument's status"
RED_FLAGS = "Red flags"
DIAGNOSTICS_HEAD = "Diagnostics (shown, never scored)"
VALIDITY_HEAD = "Validity checks"
RESCORED = "rescored"
TARGET, CONTROL = "target", "control"                     # the Components table's own column names
CLASS_COLUMNS = ("condition", "class", "score", "interval", "raw", "status")
EVEN_AT_ZERO = "The dashed line is 0, no measured difference in the target's favour."
SCALE_NOTE = "Each class score is 0 to 100."
LEVELS_NOTE = ("A bar is the share of the target's or the control's items or errands on 0 to 100; records examined is "
               "a mean count, printed and not drawn.")
IDENTITY = (("bank sha256", "bank_sha256"), ("plan fingerprint", "plan_fingerprint"),
            ("items fingerprint", "item_fingerprint"), ("card schema", "schema"))
# the README in its own words (tests/test_single_html.py checks each is a passage of README.md, its Markdown marks
# aside): the instrument's status as a chip, and its sentence under the page's limits (P6)
STATUS_CHIP = "Status: built, not yet calibrated"
STATUS_SENTENCE = ("Status: built, not yet calibrated. v4's item bank is built and pinned (bank/, its manifest's "
                   "sha256 in probatio.py), so v4 runs from a clone. The calibration study that will say what the "
                   "scores can be trusted to show is designed and pre-registered, but none of it has run yet (see "
                   "What a score cannot mean), so treat any v4 score as provisional.")
CONDITION_COLOURS = {"with": "accent", "without": "orange"}      # memory on, memory off: as versus's arms
RECORDS = re.compile(r"^I{1,3}\.c$")                             # "records examined": a mean count, never a bar


def plain(line):
    """A line of scorecard.md as plain text: its Markdown links written as their words."""
    return re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", line)


# ---- the head ---------------------------------------------------------------------------------------------------------
def head(lines, shown):
    """The title with the target as scorecard.md names it; scorecard.md's head line as the run line; the chips."""
    target = shown["target"]
    tier = f"{shown['tier']} tier" + (f" (recorded as {shown['recorded_tier']})"
                                      if shown.get("recorded_tier") and shown["recorded_tier"] != shown["tier"] else "")
    chips = [tier, f"memory {shown['memory']}", *(shown.get("labels") or []),
             *([RESCORED] if shown.get("rescored") else []), STATUS_CHIP]
    return tag("header", tag("h1", text(TITLE.format(name=target["full"], type=target["type"])))
               + para(lines["head"], cls="runline") + K.pills(chips), cls="head")


# ---- the teaser and "By class" ------------------------------------------------------------------------------------------
def by_condition(lines, shown):
    """[(condition part of lines, the shown card's record)] in the card's order."""
    return [(c, shown["conditions"][c["key"]]) for c in lines["conditions"]]


def class_mark(parts, cls):
    """A class's bar for one condition (its score where the text page prints it, its interval), or its status
    words when it has no number."""
    if parts["score"] is None:
        return state(parts["status"])
    return bar(cls["score"], cls["ci"], f"{parts['score']} {parts['ci']}", parts["score"],
               [parts["status"], f"raw {parts['raw']}"])


def teaser(lines, shown, figures):
    """Three class panels: each condition's score large, its interval and the condition under it, the small bars
    against the even line at 0 (no measured difference), and the class's status (each condition's under its
    column when there are two). A class with no number in any condition shows its words."""
    panels = []
    conds = by_condition(lines, shown)
    for i, k in enumerate(P.V4_CLASSES):
        parts = [(c, c["classes"][i], rec["classes"].get(k)) for c, rec in conds]
        fig = f"tz-{k}"
        figures[fig] = {"kind": "mini", "even": 0, "series": [{"colour": "accent"}],
                        "rows": [row(c["name"], [K.tinted(class_mark(pt, cls),
                                                          CONDITION_COLOURS.get(c["key"], "accent"))])
                                 for c, pt, cls in parts]}
        if all(pt["score"] is None for _, pt, _ in parts) and len({pt["status"] for _, pt, _ in parts}) == 1:
            nums, line = para(parts[0][1]["status"], cls="tz-none"), ""
        else:
            cols = []
            for c, pt, _ in parts:
                top = (tag("div", text(pt["score"]), cls="tz-big") + tag("div", text(pt["ci"]), cls="tz-iv")
                       if pt["score"] is not None else tag("div", text(pt["status"]), cls="tz-state"))
                status = tag("div", text(pt["status"]), cls="tz-st") if len(parts) > 1 and pt["score"] is not None \
                    else ""
                cols.append(tag("div", top + tag("div", text(c["name"]), cls="tz-name") + status, cls="tz-col"))
            nums = tag("div", "".join(cols), cls="tz-out")
            line = parts[0][1]["status"] if len(parts) == 1 and parts[0][1]["score"] is not None else ""
        panels.append(K.teaser_panel(f"Class {k}", P.CLASS_NAMES[k], nums, fig, line))
    return K.teaser(panels)


def classes_figure(lines, shown, figures):
    """Figure "By class": one row per class, one bar per condition with its interval (memory on accent, memory off
    orange); the numbers as the class lines' parts; the setup note quotes report.md's "How to read this"; with memory
    both the difference rows of scorecard.md's block under it."""
    conds = by_condition(lines, shown)
    rows_, cells = [], []
    for i, k in enumerate(P.V4_CLASSES):
        marks = [class_mark(c["classes"][i], rec["classes"].get(k)) for c, rec in conds]
        rows_.append(row(f"Class {k} {P.CLASS_NAMES[k]}", marks))
        for c, _ in conds:
            pt = c["classes"][i]
            cells.append([c["name"], f"Class {k} {P.CLASS_NAMES[k]}", pt["score"] or "", pt["ci"] or "",
                          pt["raw"] or "", pt["status"]])
    figures["fig-classes"] = {"kind": "bars", "even": None, "series": [
        {"label": c["name"], "colour": CONDITION_COLOURS.get(c["key"], "accent")} for c, _ in conds],
        "tabs": [{"label": "", "panels": [{"rows": rows_}]}]}
    tables = [table(list(CLASS_COLUMNS), cells)]
    if lines["difference"]:
        tables.append(table(["", ""], [[d["label"].strip(), d["rest"]] for d in lines["difference"]],
                            caption=DIFFERENCE_CAPTION, cls="mini"))
    return figure("fig-classes", FIG_CLASSES, SCALE_NOTE, [P.HOW_TO_READ_V4], tables)


# ---- "Components" ------------------------------------------------------------------------------------------------------
def level_mark(value, cell, records):
    """A side's level as a bar on 0 to 100 (its printed text, the table's cell), or the cell as printed for records
    examined (a mean count) and for a side with no level."""
    if records or value is None:
        return state(cell)
    return bar(100 * value, None, cell, cell.split(" ")[0])


def components_figure(lines, shown, figures):
    """Figure "Components": a tab per class, a panel per condition, a row per component with the target's level and
    the control's as two bars, its status under it; the numbers are scorecard.md's Components table, cell for
    cell."""
    rows_by_class = {k: {} for k in P.V4_CLASSES}
    cells = iter(lines["components"])
    for cond, rec in shown["conditions"].items():
        for j, c in rec["components"].items():
            cell = next(cells)
            if cell[1] != P.component_label(j):
                raise ValueError("the components table is not in the card's order")
            k = j.split(".")[0]
            records = bool(RECORDS.match(j))
            rows_by_class.setdefault(k, {}).setdefault(cond, []).append(
                row(cell[1], [level_mark(c["t"], cell[4], records), level_mark(c["c"], cell[5], records)],
                    sub=[cell[6]]))
    titled = len(shown["conditions"]) > 1
    tabs = [{"label": f"Class {k}", "panels": [{"lede": P.CONDITION_NAMES[cond] if titled else None, "rows": rs}
                                               for cond, rs in by.items()]}
            for k, by in rows_by_class.items() if by]
    figures["fig-components"] = {"kind": "bars", "even": None, "series": [
        {"label": TARGET, "colour": "accent"}, {"label": CONTROL, "colour": "charcoal"}], "tabs": tabs}
    return figure("fig-components", FIG_COMPONENTS, LEVELS_NOTE, [P.HOW_TO_READ_V4],
                  [table(list(P.COMPONENT_COLUMNS), lines["components"])], tabs=True)


# ---- the box, the limits, the foot ----------------------------------------------------------------------------------
def box(lines):
    """The highlighted box: scorecard.md's head block as it prints it, the head line aside (it is the run line), one
    element per line, its spacing kept."""
    block = P.block_lines(lines)[1:]
    body = "".join(tag("div", text(x), cls="ln") if x else tag("div", "", cls="gap") for x in block)
    return tag("h2", text(BOX_TITLE)) + tag("div", body, cls="takeaway sc")


def limits(lines):
    """The notes, as scorecard.md lists them, and the instrument's status in the README's words."""
    out = (tag("h2", text(NOTES)) + items(lines["notes"])) if lines["notes"] else ""
    return out + tag("h2", text(STATUS_HEAD)) + para(STATUS_SENTENCE)


def foot(lines, shown):
    """Red flags and validity checks as small tables, the diagnostics as items, scorecard.md's closing sentence, the
    page's note, and the identity."""
    checks = lines["validity_checks"]
    names = [v for v, _ in checks[0][1]] if checks else []
    validity = table(["condition", *names], [[name, *[w for _, w in parts]] for name, parts in checks], cls="mini")
    ident = [(label, shown.get(key)) for label, key in IDENTITY] + [
        ("bootstrap B", shown["bootstrap"]["B"]), ("bootstrap seed", shown["bootstrap"]["seed_hex"])]
    return tag("footer", tag("h2", text(RED_FLAGS)) + table(list(P.FLAG_COLUMNS), lines["red_flags"], cls="mini")
               + tag("h2", text(DIAGNOSTICS_HEAD)) + items(lines["diagnostics"])
               + tag("h2", text(VALIDITY_HEAD)) + validity
               + para(plain(lines["closing"])) + para(K.PAGE_IS_VIEW, cls="fine")
               + K.identity_table(ident), cls="foot")


# ---- the page ---------------------------------------------------------------------------------------------------------
def page(lines, shown):
    """results.html as text, from scorecard_lines' parts and the shown card."""
    figures = {}
    main = (head(lines, shown) + teaser(lines, shown, figures) + classes_figure(lines, shown, figures)
            + components_figure(lines, shown, figures) + box(lines) + limits(lines) + foot(lines, shown))
    return K.document(TITLE.format(name=shown["target"]["full"], type=shown["target"]["type"]), main, figures,
                      extra_style=STYLE)


def render(lines, shown):
    """The one interface: scorecard_lines' parts and the shown card in, the page's bytes out (results_kit.as_bytes)."""
    return K.as_bytes(page(lines, shown))


STYLE = """
.sl .takeaway.sc{padding:12px 16px;font-family:var(--mono);font-size:13px;line-height:1.5}
.sl .takeaway.sc .ln{white-space:pre-wrap;overflow-wrap:anywhere}
.sl .takeaway.sc .gap{height:.75em}
.sl .tz-st{margin-top:3px;font-size:12px;line-height:1.3;color:var(--ink-faint);overflow-wrap:anywhere}
"""
