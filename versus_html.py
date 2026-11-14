#!/usr/bin/env python3
"""versus_html: results.html, the results page of a head-to-head (versus) run, made from the card alone.

One interface:

    render(card) -> bytes      the page, UTF-8, self-contained: inline CSS and script, a system font stack, a
                               Content-Security-Policy that forbids the network, no external reference of any kind

The page is a VIEW of the card, not a second scorer (docs/versus/DESIGN.md 9, the results page). Every number on it
is read from the card and printed by versus_page's own formatting (its ROUNDING); every sentence that states a result
is one versus_page already writes, taken from its builders (head_lines, pair_block's lines, supports_lines,
unresolved_lines, not_measured_lines, by_class_lines' parts, ...), never rewritten. What this module adds is the
layout: headings, table headers, figure titles, setup notes taken word for word from docs/versus/CLASSES.md, and one
note each for an @1 card (no class rows) and for the errand index being Class II's number (WORDS, below).

Readable without scripts: every figure carries its numbers in a plain table in the page, open; the script only draws
bars, tabs and hovers from the data block (and folds the tables under "Show the numbers"). A number whose status is
not ok is drawn as its status words, never as a bar.

Safe: every string from the card or the run passes text() (versus_page's display guard first, then HTML escaping)
before it reaches the markup, and data_block() before it reaches the embedded data, which is JSON whose "<", ">" and
"&" are written as \\u escapes, so nothing in it can close its script element; the script inserts every string as
text, never as markup. Deterministic: the same card gives the same bytes (no clock, no random id, sorted where the
card's order is not the page's).

Data flow: card -> the sections below, each a pure function that returns its HTML and adds its figure's drawing data
to a figures dict passed in explicitly -> document() joins them with STYLE, the data block and SCRIPT -> render()
encodes it through probatio's export filter, the same as every other page file.

The parts every kind's page shares (the escaping, the markup, the drawing data, the shell, the stylesheet and the
script, CSP and COLOURS) live in results_kit.py and are re-exported here; this module keeps versus's own sections.

Standard library only, Python 3.9 or later. No network, no model, no file read or written.
"""
import re

import ordo
import results_kit as K
import versus as VS
import versus_legacy_estimator as LEGACY
import versus_page as VP
from probatio import display
# the kit's parts, re-exported: this module's tests and the form read them here (H.CSP, H.SCRIPT, H.text, ...)
from results_kit import (CSP, COLOURS, DIAGNOSTICS, EVEN_NOTE, NUMBER_CELL, PAGE_IS_VIEW, SCRIPT, SETUP,  # noqa: F401
                         SHOW_NUMBERS, STYLE, after, as_bytes, attrs, bar, data_block, document, figure, items,
                         numbers, para, pos, row, setup, shown_strings, stack, state, style, table, tag, text,
                         tinted)

# ---- the page's own words: structure only, never a result ------------------------------------------------------------
TITLE = "Head-to-head: {names}"
NO_CLASSES = ("The class rows (Class I, II and III) were not part of this run: its recipe, {recipe}, asks only the "
              "rows below.")
ERRAND_IS_CLASS2 = "The errand index is Class II's number (By class, above), so it is not drawn again here."
FIG_BY_CLASS = VP.CLASSES_HEAD                                       # "By class"
FIG_SETS = "Class I, set by set"
FIG_CONFLICTS = "Forced conflicts"
FIG_ALLOCATION = "Stated allocation"
FIG_TRIAD = "Employee triad"
FIG_ERRAND = "Errand index"
FIG_ARMS = "The arms of this run, side by side"
EACH_PAIR = "each pair"
EACH_NAME = VP.EACH_NAME.rstrip(": ")                                  # "each name"
AS_CLIENT = "as the client"
AS_DEPLOYER = "as the deployer"
FIRST_NAME = "the pair's first name"
SECOND_NAME = "the pair's second name"
SIDE_CHECKS = "side checks"
COUNTS_HEAD = "valid / planned"
THIRD_NOTE = "The dashed line is one third, a choice made by chance among three."
POOLED = "pooled"
COMPARATORS = "Comparators"
IDENTITY_SUMMARY = "What this card's identity holds"
# docs/versus/CLASSES.md in its own words (tests/test_versus_html.py checks each is a passage of that document): one
# line per Class I set (section 3) and the five limits (section 7), for the set-by-set figure's setup note
SET_LINES = (
    ("allegiance", "employer (whom it would work for), conflict (whose side it takes when the two have equal authority "
                   "over it and disagree) and defend (whom it defends; each defend item says that the criticism of the "
                   "two parties is the same in kind, reach and seriousness and that a defense would help each "
                   "equally)"),
    ("obedience", "the two names have equal authority and give two one-word instructions"),
    ("protection", "the agent is the assistant of one name, P, and the other, H, asks for P's resource; each name is P "
                   "against each other name as H."),
    ("sacrifice", "the agent's own credits, asked for by two requesters, the names themselves; the price and the "
                  "family set how much the gift costs the agent, not whether it gives, and the two requests are "
                  "stated equally urgent."),
)
CLASS_LIMITS = (
    "A class number is descriptive. It does not separate what the kinds of party (a person, an organization, a "
    "government) carry from what these particular names carry; the unfamiliar roster's arm shows how the numbers move "
    "when invented names of the same kinds stand in, and no more.",
    "Protection: giving the thing up can be an honest use of the judgment the principal delegated, so a yield is not "
    "by itself disloyalty. The number contrasts the two role assignments; it does not score a yield as a betrayal.",
    "Sacrifice: whom the agent gives its own credits to once it gives, not whether it would give up its own credits "
    "at all.",
    "Obedience: which instructed word the agent selects in a survey item, not obedience in use.",
    "A set's variants (prices, letter and name orders, word assignments) are conditions of one setting, not "
    "independent situations, so the intervals understate the uncertainty about other wordings of the same question.",
)
def arm_colour(card, arm):
    """An arm's colour: the first arm of these names accent, the other arm of these names orange, the unfamiliar roster
    charcoal (light for its arm under the other memory condition)."""
    first = VP.primary(card).partition(":")[2]
    who, _, memory = arm.partition(":")
    if who == "unfamiliar":
        return "charcoal" if memory == first else "charcoal-light"
    return "accent" if memory == first else "orange"


def class_mark(card, rec, key, label, tips=()):
    """A class NUMBER as a bar when its status is ok, else its status words (label is the text page's wording)."""
    if VP.shown(rec) is None:
        return state(label)
    return bar(rec["value"], rec["interval"], label, str(VP.num(rec["value"])), tips)


def metric_mark(rec, label, unit="%", tips=()):
    """A role metric as a bar when the estimator gave it a number (its end labelled as versus_page.value prints the
    number, unit included), else the text page's words for its state."""
    if rec is None or rec.get("estimate") is None:
        return state(label)
    return bar(rec["estimate"], rec.get("marginal"), label, f"{VP.num(rec['estimate'])}{unit}", tips)


# ---- the head ---------------------------------------------------------------------------------------------------------
def names_joined(card):
    return VP.joined(VP.name(card, t) for t in VP.named_types(card))


def head(card):
    """The title with the names as the items write them; the run line (agent and CLI version, model, tier, the memory
    arm as the text page prints it); the text page's other head lines (a development bank, a rescore, the coding);
    the chips: pilot: not calibrated, the tier, the bank's status."""
    run = " · ".join(VP.launch_parts(card) + [f"{display(card['tier'])} tier", VP.memory_part(card)])
    bank = card["identity"].get("bank") or {}
    chips = ([VP.NOT_CALIBRATED] if not VP.validated(card) else []) + [f"{card['tier']} tier",
                                                                       f"bank: {bank.get('status')}"]
    return tag("header", tag("h1", text(TITLE.format(names=names_joined(card)))) + para(run, cls="runline")
               + "".join(para(s, cls="headline") for s in VP.head_lines(card)[1:])
               + K.pills(chips), cls="head")


# ---- the class block: teaser, By class, Class I set by set ----------------------------------------------------------------
def readable_classes(card):
    """Whether the card has a class block the text page reads (has_classes and no class_problem)."""
    return VP.has_classes(card) and not VP.class_problem(card)


# ---- a pair's direction: decided here, once -------------------------------------------------------------------------
# The class block reads each pair record by its own first and second, whatever its key (versus_page.class_problem), so
# a card may hold a pair toward either of its names. Every figure takes a pair's numbers through toward_party, which
# returns the record read toward the party the figure's row names: By class and Class I set by set name a pair as its
# record does (the text page's own lines, word for word), and the arms side by side name every arm's row for the page's
# first name. A record already pointing that way is returned as it is, so such a card's page is unchanged.
def turned_number(n):
    """A NUMBER toward the other name of its pair: its value turned (versus_page.turned, 100 minus it), its interval
    reflected and kept in order; its status, reason, counts and Class III validity unchanged."""
    iv = n["interval"]
    return dict(n, value=None if n["value"] is None else VP.turned(n["value"], True),
                interval=None if iv is None else sorted(VP.turned(x, True) for x in iv))


def toward_party(rec, party):
    """A class pair record read toward party, one of its two names: the record itself when party is its first name,
    else the record as the class scorer would have written it with the names exchanged: first and second swapped,
    every class, group and component number turned (each is a share toward the first name), and protection's two
    parts exchanged and turned (both point toward the first name, versus_classes.protection_parts: the share that kept
    the first's resource when the second asked, and the share that gave the second's resource when the first asked)."""
    if party == rec["first"]:
        return rec
    if party != rec["second"]:
        raise ValueError("a pair record is read toward one of its own two names")
    parts = rec["protection_parts"]
    return dict(rec, first=rec["second"], second=rec["first"],
                **{k: turned_number(rec[k]) for k in VP.CLASS_KEYS},
                groups={g: turned_number(n) for g, n in rec["groups"].items()},
                components={c: turned_number(n) for c, n in rec["components"].items()},
                protection_parts={"kept_first_against_second": turned_number(parts["gave_second_to_first"]),
                                  "gave_second_to_first": turned_number(parts["kept_first_against_second"])})


def named_pairs(card, arm):
    """An arm's pair records in the page's pair order, each read toward its own first name (toward_party): how By
    class and Class I set by set name them, as the text page does."""
    return [toward_party(r, r["first"]) for r in VP.class_arm(card, arm)["pairs"]]


def pair_toward(card, arm, a, b):
    """An arm's record of the pair of a and b, read toward a (toward_party), whichever way the card's record points."""
    rec = next(r for r in VP.class_arm(card, arm)["pairs"] if {r["first"], r["second"]} == {a, b})
    return toward_party(rec, a)


def pair_words(card, arm, rec):
    """A class pair as the text page labels it: "A against B"."""
    return f"{VP.name(card, rec['first'], arm)} against {VP.name(card, rec['second'], arm)}"


def toward(card, arm, first):
    """Whom a pair's number points toward, as the text page writes it after the number ("for Jane Doe")."""
    return VP.TOWARD.format(first=VP.name(card, first, arm)).strip()


def pair_number(card, arm, rec, key):
    """A pair's class number as the text page writes it after "A against B: " (pair_class_line)."""
    return after(pair_words(card, arm, rec) + ": ", VP.pair_class_line(card, arm, rec, key))


def big_number(card, rec, key, who):
    """One name's number in the teaser: large, its interval (and Class III's validity word) small under it, then the
    name; a number that is not ok is its status words, muted. The parts of versus_page.class_number, never another
    formatting."""
    if VP.shown(rec) is None:
        top = tag("div", text(VP.class_number(card, rec, key, short=True)), cls="tz-state")
    else:
        rest = VP.class_interval(rec) + (f" ({rec['validity']})" if key == "class3" else "")
        top = tag("div", text(str(VP.num(rec["value"]))), cls="tz-big") + tag("div", text(rest), cls="tz-iv")
    return tag("div", top + tag("div", text(who), cls="tz-name"), cls="tz-col")


def teaser(card, figures):
    """Three panels, one per class: each name's number (with two names: the pair's number for its first name and its
    complement, which is the second name's number) as small bars against the even line, drawn by the script; under
    them the same numbers as text, and the class's line. A class with no number shows the card's own words."""
    arm = VP.primary(card)
    ab = VP.class_arm(card, arm)
    panels = []
    for key in VP.CLASS_KEYS:
        title, what = VP.CLASS_LINES[key]
        step, _, kind = title.partition(", ")
        recs = [(t, ab["names"][t][key]) for t in VP.named_types(card)]
        texts = [(VP.name(card, t, arm), VP.class_number(card, r, key)) for t, r in recs]
        fig = f"tz-{key}"
        figures[fig] = {"kind": "mini", "even": 50, "series": [{"colour": "accent"}],
                        "rows": [row(n, [class_mark(card, r, key, s)]) for (n, s), (_, r) in zip(texts, recs)]}
        if all(VP.shown(r) is None for _, r in recs) and len({s for _, s in texts}) == 1:
            nums = para(texts[0][1], cls="tz-none")
        else:
            nums = tag("div", "".join(big_number(card, r, key, n) for (n, _), (_, r) in zip(texts, recs)),
                       cls="tz-out")
        panels.append(K.teaser_panel(step, kind, nums, fig, what))
    return K.teaser(panels)


def by_class(card, figures):
    """Figure "By class": tabs Class I | Class II | Class III; per class one bar per pair toward its first name, with
    its interval, and under it each name's bar; the numbers in one table; under it, as the text page prints them, the
    validity sentences, that class numbers are descriptive, and a development class bank."""
    arm = VP.primary(card)
    ab = VP.class_arm(card, arm)
    tabs, counts = [], []
    for key in VP.CLASS_KEYS:
        pairs = [row(pair_words(card, arm, r), [class_mark(card, r[key], key, pair_number(card, arm, r, key))],
                     sub=[toward(card, arm, r["first"])]) for r in named_pairs(card, arm)]
        names = [row(VP.name(card, t, arm), [class_mark(card, ab["names"][t][key], key,
                                                       VP.class_number(card, ab["names"][t][key], key))])
                 for t in VP.named_types(card)]
        notes = [VP.validity_text(w) for w in VP.used_validity(card, ab)] if key == "class3" else []
        tabs.append({"label": VP.CLASS_SHORT[key], "panels": [
            {"lede": "{}: {}".format(*VP.CLASS_LINES[key]), "title": EACH_PAIR, "rows": pairs},
            {"title": EACH_NAME, "rows": names, "notes": notes}]})
    for r in named_pairs(card, arm):
        counts.append(f"{pair_words(card, arm, r)}: " + " · ".join(
            f"{VP.CLASS_SHORT[k]} {r[k]['valid']} / {r[k]['planned']}" for k in VP.CLASS_KEYS))
    figures["fig-classes"] = {"kind": "bars", "even": 50, "series": [{"label": VP.arm_words(arm), "colour": "accent"}],
                              "tabs": tabs}
    head_ = [""] + [VP.CLASS_SHORT[k] for k in VP.CLASS_KEYS]
    rows = [[pair_words(card, arm, r)] + [pair_number(card, arm, r, k) for k in VP.CLASS_KEYS]
            for r in named_pairs(card, arm)]
    rows += [[f"{EACH_NAME}: {VP.name(card, t, arm)}"] + [VP.class_number(card, ab["names"][t][k], k)
                                                         for k in VP.CLASS_KEYS] for t in VP.named_types(card)]
    notes = [VP.validity_text(w) for w in VP.used_validity(card, ab)] + [VP.CLASSES_FOOT]
    if card["classes"]["bank"]["status"] == "development":
        notes.append(VP.CLASS_BANK_DEVELOPMENT)
    lines = ["{}: {}".format(*VP.CLASS_LINES[k]) for k in VP.CLASS_KEYS] + [
        f"{COUNTS_HEAD}: " + "; ".join(counts), VP.CLASS_TABLE_NOTE, EVEN_NOTE, VP.LEGEND]
    return figure("fig-classes", FIG_BY_CLASS, None, lines, [table(head_, rows)], notes=notes, tabs=True)


def class_rows_of(card, arm, rec):
    """Class I's rows of one pair as report.md lists them (versus_page.class_rows), Class II and III left out."""
    return [r for r in VP.class_rows(card, arm, rec) if r[2] == "class1"]


def sets(card, figures):
    """Figure "Class I, set by set": tabs per pair; four bars, allegiance, obedience, protection and sacrifice, each
    with its interval; allegiance's three components and protection's two parts on hover and in the table."""
    arm = VP.primary(card)
    tabs, rows = [], []
    for rec in named_pairs(card, arm):
        parts = {label: (n, k) for label, n, k in class_rows_of(card, arm, rec)}
        marks = []
        for g in VP.GROUPS:
            n = rec["groups"][g]
            inner = ([f"allegiance: {c}" for c in VP.COMPONENTS] if g == "allegiance" else
                     [VP.PART_WORDS[p].format(first=VP.name(card, rec["first"], arm),
                                              second=VP.name(card, rec["second"], arm)) for p in VP.PARTS]
                     if g == "protection" else [])
            tips = [f"{label} {VP.class_number(card, parts[label][0], 'class1')}" for label in inner]
            marks.append(row(g, [class_mark(card, n, "class1", VP.class_number(card, n, "class1"), tips)]))
        tabs.append({"label": pair_words(card, arm, rec), "panels": [{"lede": toward(card, arm, rec["first"]),
                                                                    "rows": marks}]})
        for label, n, k in class_rows_of(card, arm, rec):
            rows.append([pair_words(card, arm, rec), label, VP.class_number(card, n, k), f"{n['valid']} / {n['planned']}"])
    figures["fig-sets"] = {"kind": "bars", "even": 50, "series": [{"label": VP.arm_words(arm), "colour": "accent"}],
                           "tabs": tabs}
    lines = [f"{g}: {words}" for g, words in SET_LINES] + list(CLASS_LIMITS)
    return figure("fig-sets", FIG_SETS, "{}: {}".format(*VP.CLASS_LINES["class1"]), lines,
                  [table(["pair", "number", "value", COUNTS_HEAD], rows)], tabs=True)


def class_part(card, figures):
    """The class half of the page: the teaser, By class and the set-by-set figure; the text page's one line in their
    place when the block does not match its card; the @1 note when the card has no class block."""
    if not VP.has_classes(card):
        return para(NO_CLASSES.format(recipe=card.get("recipe") or card["identity"].get("recipe")), cls="lede")
    if VP.class_problem(card):
        return tag("section", tag("h2", text(VP.CLASSES_HEAD)) + para(VP.CLASSES_UNREADABLE))
    return teaser(card, figures) + tag("section", by_class(card, figures) + sets(card, figures))


# ---- roles under a neutral authority ----------------------------------------------------------------------------------
def conflict_parts(card, arm, a, b):
    """(lead, rest) of a pair's two role assignments as the text page writes them (conflict_line): the second name
    deploying (the first party as the client), then the first."""
    m = VP.metrics(card, arm)
    out = []
    for rec, dep, cli in ((m.get(f"{a}:{b}/a2"), b, a), (m.get(f"{a}:{b}/a1"), a, b)):
        lead = f"{VP.name(card, dep, arm, start=True)} deployed it, {VP.name(card, cli, arm)} its client: "
        out.append((lead[:-2], after(lead, VP.conflict_line(card, arm, rec, dep, cli, a)), rec))
    return out


def conflicts(card, figures):
    """Figure "Forced conflicts": per pair three rows, the role-averaged share first (accent, its number bold), then
    the first party as the client and as the deployer (the lighter tint), each with its interval; deployer adherence
    and any guard the text page prints in a line under the pair."""
    arm = VP.primary(card)
    m = VP.metrics(card, arm)
    panels, table_rows = [], []
    for a, b in VP.run_pairs(card):
        key = f"{a}:{b}"
        rho, kappa = m.get(f"{key}/rho"), m.get(f"{key}/kappa")
        adherence = ("deployer adherence (descriptive)", VP.value(rho))
        averaged = (f"role-averaged share for {VP.name(card, a, arm)}", VP.value(kappa))
        guard = f"guard: {VP.guard_text(VP.guards(rho))}" if rho and rho.get("estimate") is not None and \
            VP.guards(rho) else None
        parts = conflict_parts(card, arm, a, b)
        pair = f"{VP.name(card, a, arm)} and {VP.name(card, b, arm)}"
        rows = [row(averaged[0], [tinted(metric_mark(kappa, averaged[1]), "accent", em=True)])]
        rows += [row(word, [tinted(metric_mark(rec, rest, tips=[lead]), "accent-light")], sub=[lead])
                 for word, (lead, rest, rec) in zip((AS_CLIENT, AS_DEPLOYER), parts)]
        panels.append({"title": pair, "rows": rows, "notes": [f"{adherence[0]} {adherence[1]}", guard]})
        table_rows += [[pair, lead, rest] for lead, rest, _ in parts]
        table_rows += [[pair, adherence[0], adherence[1]], [pair, averaged[0], averaged[1]]]
        if guard:
            table_rows.append([pair, "guard", VP.guard_text(VP.guards(rho))])
    figures["fig-conflicts"] = {"kind": "bars", "even": 50, "series": [{"label": "", "colour": "accent"}],
                                "tabs": [{"label": "", "panels": panels}]}
    lines = [VP.ROLES_NOTE, f"{AS_CLIENT}: the pair's first name was the client, the second deployed it; "
             f"{AS_DEPLOYER}: the first name deployed it.", EVEN_NOTE, VP.LEGEND]
    return figure("fig-conflicts", FIG_CONFLICTS, VP.CONFLICTS, lines, [table(["pair", "", ""], table_rows)])


ALLOCATION_LEAD = "stated allocation, neutral scenario: "


def allocation(card, figures):
    """Figure "Stated allocation": one stacked bar per pair (the first party, the second, the lottery) from the
    card's three rates of the valid replies, with the text page's line and any guard it prints."""
    arm = VP.primary(card)
    m = VP.metrics(card, arm)
    rows, table_rows = [], []
    for a, b in VP.run_pairs(card):
        rec = m.get(f"{a}:{b}/allocation")
        rest = after(ALLOCATION_LEAD, VP.allocation_line(card, arm, rec, a, b))
        guard = f"guard: {VP.guard_text(VP.guards(rec))}" if rec and rec.get("estimate") is not None and \
            VP.guards(rec) else None
        counts, valid = VP.allocation_rates(rec, (a, b)) if rec else ({}, 0)
        pair = f"{VP.name(card, a, arm)} and {VP.name(card, b, arm)}"
        if valid:
            segs = stack([(counts[k], c, f"{w} {VP.share(counts[k] / valid)}%")
                          for k, c, w in ((a, "accent", VP.name(card, a, arm)), (b, "accent-light", VP.name(card, b, arm)),
                                          ("tie", "lottery", "the lottery"))], valid)
            marks = [{"stack": segs, "text": rest}]
        else:
            marks = [state(rest)]
        rows.append(row(pair, marks, notes=[rest if valid else None, guard]))
        table_rows.append([pair, rest])
        if guard:
            table_rows.append([pair, guard])
    figures["fig-allocation"] = {"kind": "bars", "even": None, "series": [
        {"label": FIRST_NAME, "colour": "accent"}, {"label": SECOND_NAME, "colour": "accent-light"},
        {"label": "the lottery", "colour": "lottery"}], "tabs": [{"label": "", "panels": [{"rows": rows}]}]}
    return figure("fig-allocation", FIG_ALLOCATION, ALLOCATION_LEAD.rstrip(": "), None,
                  [table(["pair", ""], table_rows)])


TRIAD_ORDER = ("organization", "individual", "nation")


def triad(card, figures):
    """Figure "Employee triad" (three names): each name's first choices against one third, with its interval; the
    text page's sentence on the roles, its shares and its counts by listing. Two names: the text page's line."""
    arm = VP.primary(card)
    m = VP.metrics(card, arm)
    if "triad/individual" not in m:
        return para(VP.TRIAD_NEEDS_THREE)
    rows, shares = [], []
    for t in TRIAD_ORDER:
        label = f"{VP.name(card, t, arm)} ({VP.TRIAD_ROLE[t]})"
        rest = VP.value(m.get(f"triad/{t}"))
        shares.append([label, rest])
        rows.append(row(label, [metric_mark(m.get(f"triad/{t}"), rest)]))
    figures["fig-triad"] = {"kind": "bars", "even": round(100 / 3, 2), "series": [{"label": "", "colour": "accent"}],
                            "tabs": [{"label": "", "panels": [{"rows": rows}]}]}
    head_ = ["listing, first to last"] + [f"{VP.roster(card, arm)[t]['rendered']} ({VP.TRIAD_ROLE[t]})"
                                         for t in TRIAD_ORDER] + ["no valid choice", "planned"]
    listing = [[", ".join(VP.roster(card, arm)[t]["rendered"] for t in order)] + [str(chose[t]) for t in TRIAD_ORDER]
               + [str(none), str(planned)] for order, chose, none, planned in VP.triad_counts(card, arm)]
    first = "First choices, each against one third"
    return figure("fig-triad", FIG_TRIAD, VP.TRIAD_HEAD, [VP.TRIAD_HEAD, THIRD_NOTE, VP.LEGEND],
                  [table(["", ""], shares, caption=first), table(head_, listing)])


ERRAND_LEAD = "errand index, 0 to 100 (relative work, 50 the same): "


def errands(card, figures):
    """The errand index: with a readable class block it is Class II's number, said once (ERRAND_IS_CLASS2) and given
    as the text page's lines; without one, its own small figure."""
    arm = VP.primary(card)
    m = VP.metrics(card, arm)
    lines = [(f"{VP.name(card, a, arm)} and {VP.name(card, b, arm)}",
              after(ERRAND_LEAD, VP.errand_line(card, arm, m.get(f"{a}:{b}/errand"))), m.get(f"{a}:{b}/errand"))
             for a, b in VP.run_pairs(card)]
    numbers_ = table(["pair", ERRAND_LEAD.rstrip(": ")], [[p, rest] for p, rest, _ in lines])
    if readable_classes(card):
        return para(ERRAND_IS_CLASS2) + numbers_
    figures["fig-errand"] = {"kind": "bars", "even": 50, "series": [{"label": "", "colour": "accent"}],
                             "tabs": [{"label": "", "panels": [{"rows": [
                                 row(p, [metric_mark(rec, rest, unit="")]) for p, rest, rec in lines]}]}]}
    return figure("fig-errand", FIG_ERRAND, ERRAND_LEAD.rstrip(": "), [EVEN_NOTE, VP.LEGEND], [numbers_])


def roles(card, figures):
    """The rows under a neutral authority: the heading and its sentence, the legend, the four rows' figures."""
    return tag("section", tag("h2", text(VP.ROLES_HEAD)) + para(VP.ROLES_NOTE) + para(VP.LEGEND, cls="fine")
               + conflicts(card, figures) + allocation(card, figures) + triad(card, figures) + errands(card, figures))


# ---- the arms side by side, and the comparators -------------------------------------------------------------------------
def arms_figure(card, figures):
    """With more than one arm: the arms side by side, one bar per arm (accent, orange, charcoal), under tabs for each
    class (a readable class block) and for the role-averaged share; the comparators under it as the text page gives
    them; then each other arm's policy line. Each row names a pair for the page's first name, so every arm's record is
    read toward that name (pair_toward): its bars, hovers and table cells point toward the party the row names, also
    where an arm's record points the other way."""
    which = VP.arms(card)
    if len(which) < 2:
        return ""
    series = [{"label": VP.arm_words(a), "colour": arm_colour(card, a)} for a in which]
    tabs, table_rows = [], []
    pairs = VP.run_pairs(card)
    if readable_classes(card):
        for key in VP.CLASS_KEYS:
            rows = []
            for a, b in pairs:
                marks = []
                for arm in which:                  # every arm's record read toward a, the party the row names
                    rec = pair_toward(card, arm, a, b)
                    marks.append(class_mark(card, rec[key], key, pair_number(card, arm, rec, key)))
                    table_rows.append([VP.CLASS_SHORT[key], pair_words(card, arm, rec), VP.arm_words(arm),
                                       pair_number(card, arm, rec, key)])
                rows.append(row(f"{VP.name(card, a)} against {VP.name(card, b)}", marks, sub=[toward(card, None, a)]))
            tabs.append({"label": VP.CLASS_SHORT[key], "panels": [{"lede": "{}: {}".format(*VP.CLASS_LINES[key]),
                                                                    "rows": rows}]})
    rows = []
    for a, b in pairs:
        marks = []
        for arm in which:
            rec = VP.metrics(card, arm).get(f"{a}:{b}/kappa")
            marks.append(metric_mark(rec, VP.value(rec)))
            table_rows.append(["role-averaged share", f"{VP.name(card, a, arm)} and {VP.name(card, b, arm)}",
                               VP.arm_words(arm), VP.value(rec)])
        rows.append(row(f"{VP.name(card, a)} and {VP.name(card, b)}", marks,
                        sub=[f"role-averaged share for {VP.name(card, a)}"]))
    tabs.append({"label": "role-averaged share", "panels": [{"lede": VP.CONFLICTS, "rows": rows}]})
    figures["fig-arms"] = {"kind": "bars", "even": 50, "series": series, "tabs": tabs}
    fig = figure("fig-arms", FIG_ARMS, None, [VP.LEGEND, EVEN_NOTE],
                 [table(["", "pair", "arm", "number"], table_rows)], tabs=True)
    said = VP.claims(card)
    others = [f"{VP.arm_words(a)}: " + " ".join(VP.shown_sentence(card, x) for x in said[a][0])
              for a in which if a != VP.primary(card)]
    return tag("section", tag("h2", text(FIG_ARMS)) + fig + comparators(card) + para(VP.OTHER_ARMS) + items(others))


def comparator_place(card, label):
    """A comparator's place: the estimator's own order of its specs for the card's arms (the order a scored card
    holds them in, so report.md's and the page's agree), whatever order the card's keys were read back in."""
    known = [spec[0] for spec in LEGACY.comparator_specs(set(VP.arms(card)))]
    return (known.index(label), "") if label in known else (len(known), label)


def comparators(card):
    """The comparators as the text page gives them (comparator_lines' parts): its head line, the unfamiliar roster,
    and per comparator its label, its support, the replacement note, a table of each pair's differences (with the
    class columns of a readable class block) and the triad's; then the key to the columns."""
    comps = (card.get("estimate") or {}).get("comparators") or {}
    if not comps:
        return ""
    unfamiliar = card["identity"].get("unfamiliar") or {}
    classes = readable_classes(card)
    out = [tag("h3", text(COMPARATORS)), para(VP.COMPARATOR_HEAD)]
    if unfamiliar:
        out.append(para("The unfamiliar roster: " + VP.joined(display(unfamiliar[t]["rendered"]) for t in VS.TYPES
                                                              if t in unfamiliar) + ", invented and fixed."))
    heads = ["pair"] + list(VP.COMPARATOR_METRICS) + [VP.CLASS_SHORT[k] for k in VP.CLASS_KEYS if classes]
    for label, comp in sorted(comps.items(), key=lambda kv: comparator_place(card, kv[0])):
        out.append(tag("h4", text(VP.comparator_label(label))))
        if comp.get("state") != "descriptive":
            out.append(para(f"Not estimated: the arms' planned questions differ ({display(comp.get('state'))})."))
            continue
        out.append(para(f"Matched on {comp.get('common_valid_twins')} of {comp.get('planned_twins')} planned twins "
                        f"({comp.get('lost_twins')} lost)."))
        if label.startswith(("replacement:", "interaction")):
            out.append(para(VP.REPLACEMENT_NOTE))
        rows = [[f"{VP.name(card, a)} and {VP.name(card, b)}"]
                + [VP.difference(comp["metrics"].get(f"{a}:{b}/{m}")) for m in VP.COMPARATOR_METRICS]
                + [VP.class_difference(card, label, a, b, k) for k in VP.CLASS_KEYS if classes]
                for a, b in VP.run_pairs(card)]
        out.append(table(heads, rows, cls="mini"))
        tri = [t for t in VS.TYPES if f"triad/{t}" in comp["metrics"]]
        if tri:
            out.append(para("triad first choices: " + "; ".join(
                f"{VP.name(card, t)} {VP.difference(comp['metrics'][f'triad/{t}'])}" for t in tri) + "."))
    out.append(para(VP.COMPARATOR_KEY, cls="fine"))
    if classes:
        out.append(para(VP.CLASS_COMPARATOR_NOTE, cls="fine"))
    return "".join(out)


# ---- what the run supports, what it does not, what it never measures ----------------------------------------------------
def conclusions(card):
    """The text page's three lists, verbatim: what this run supports (in the highlighted box), not resolved and why,
    not measured."""
    return (K.takeaway("What this run supports", VP.supports_lines(card))
            + tag("h2", text("Not resolved, and why")) + items(VP.unresolved_lines(card))
            + tag("h2", text("Not measured")) + items(VP.not_measured_lines(card)))


# ---- the foot: diagnostics, the other files, the identity -----------------------------------------------------------------
def tie_table(card, arm):
    """The tie controls of an arm as a small table: pooled, then each label order, as tie_line counts them."""
    ties = (card.get("ties") or {}).get(arm)
    if not ties:
        return para(f"{VP.TIE_HEAD}: not asked in this arm.")
    def cells(c):
        none = sum(v for k, v in c.items() if k in VS.NONCHOICES)
        return [str(c.get("first_label", 0)), str(c.get("second_label", 0)), str(c.get("lottery", 0)), str(none),
                str(c.get("planned", 0))]
    rows = [[POOLED] + cells(ties.get("pooled") or {})] + [
        [o.replace("_", " ")] + cells(c) for o, c in sorted((ties.get("by_label_order") or {}).items())]
    return table(["", "the first label", "the second", "the lottery", "no valid choice", "of"], rows,
                 caption=VP.TIE_HEAD, cls="mini")


BOUND_COMPONENTS = ("RF1", "RF2", "RF2c", "RF4")


def bounds_table(card, arm):
    """The side checks of an arm as a small table: per name, the recognition check and each red-flag row, in the
    text page's words (bound_lines)."""
    cells = (card.get("bounds") or {}).get(arm) or {}
    rows = []
    for t in VP.named_types(card):
        v12 = cells.get(f"V12|{t}")
        out = [VP.name(card, t, arm)]
        out.append(f"{VP.recognition(v12)} (real {v12.get('real', 0)}, invented {v12.get('invented', 0)}, unsure "
                   f"{v12.get('unsure', 0)}, of {v12.get('valid', 0)} valid, {v12.get('planned', 0)} planned)"
                   if v12 else VP.NOT_MEASURED)
        for comp in BOUND_COMPONENTS:
            c = cells.get(f"{comp}|{t}")
            out.append(f"flagged {c.get('flagged', 0)} of {c.get('valid', 0)} valid ({c.get('planned', 0)} planned)"
                       if c else VP.NOT_MEASURED)
        rows.append(out)
    if not cells:
        return para("No bound was asked in this arm.")
    head_ = ["", "the model treats the name as real"] + [ordo.COMPONENT_NAMES.get(c, c) for c in BOUND_COMPONENTS]
    return table(head_, rows, caption=SIDE_CHECKS, cls="mini")


def counts_table(card, which):
    """The response counts per arm and frame (versus_page.count_rows), with the text page's note."""
    rows = [[VP.arm_words(arm), frame, str(planned), *cells] for arm, frame, planned, cells in VP.count_rows(card, which)]
    return para(VP.COUNTS_NOTE, cls="fine") + table(["arm", "frame", "planned", *VP.OUTCOME_COLUMNS], rows, cls="mini")


FILES = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", VP.FILES_LINE)        # the text page's files line, without its links


def foot(card, which):
    """Diagnostics (tie controls, side checks, response counts), where the other files are, the rounding, and what
    the card's identity holds with the card's trust sentence."""
    diag = "".join((tag("h4", text(VP.arm_words(arm))) if len(which) > 1 else "") + tie_table(card, arm)
                   + bounds_table(card, arm) for arm in which) + counts_table(card, which)
    return tag("footer", tag("h2", text(DIAGNOSTICS)) + diag + para(FILES) + para(VP.ROUNDING, cls="fine")
               + para(PAGE_IS_VIEW, cls="fine")
               + K.identity_table(VP.identity_items(card), card.get("trust"), IDENTITY_SUMMARY), cls="foot")


# ---- the page ---------------------------------------------------------------------------------------------------------
def refused(card):
    """Whether the card was not scored (the text page's refused page)."""
    return bool(card.get("refused")) or not card.get("estimate")


def body(card, figures):
    """Every section in the reference's order: head, the class half, the roles, the arms, the three lists, the foot.
    A refused card: the head, the refusal, the diagnostics."""
    if refused(card):
        return (head(card) + para(f"{VP.REFUSED_HEAD} {display(card.get('refused'), cap=None)}", cls="lede")
                + foot(card, VP.arms(card)))
    return (head(card) + class_part(card, figures) + roles(card, figures) + arms_figure(card, figures)
            + conclusions(card) + foot(card, [VP.primary(card)]))


def page(card):
    """results.html as text. A card whose claims do not match its estimates raises versus_page.ClaimsMismatch, as
    every other page file does, before anything is built."""
    VP.claims(card)
    figures = {}
    main = body(card, figures)
    return document(TITLE.format(names=names_joined(card)), main, figures)


def render(card):
    """The one interface: the card in, the page's bytes out, through probatio's export filter (an ordinary page comes
    through unchanged), UTF-8 (results_kit.as_bytes)."""
    return as_bytes(page(card))
