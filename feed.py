#!/usr/bin/env python3
"""feed: what the live page shows of anything that lands in a run, as one shape, the entry (the live-feed ruling,
V1 and V2).

An entry is one call of the run's plan: a question whose reply line has landed in replies.jsonl, or an errand that has
landed or is running. It has a compact line (when, class, category, the parties in short, the action in a few words, a
mark when the reply could not be read) and an opened view with the same fields in the same order for every kind of
run: Dataset, Class, Category, Condition, Parties, What was sent, the options (a question) or the live log (an
errand), and the model's action with how it was read. The compact line carries the parsed action only, never reply
text.

    Kind            one kind of run's words and parties, each read from that kind's own scorecard's constants (V5, V6)
      SingleKind      a single-target run: the unified bank's dataset by the target's type
      CrossKind       a cross-type run: ordo
      VersusKind      a head-to-head run: versus, its answers named as its counts table names them
    entry           one call and its record (and, for an errand, its log) as an entry; the opened view only when asked
    query_of        the page's request: which entries are open, which page of the feed, which errand's whole log
    window          the newest SHOWN entries, or the SHOWN before a given one

Everything here is pure: it reads the call (the plan the page already holds), the call's committed record and an
errand's record lines and report as the page's Card read them, and nothing else. It never reads a file, a memory file
or the Hub; HUB names the public dataset and configuration each kind of item is published in, checked once against
the Hub's own row ids when it was written (tests/test_live_feed.py says how). Text is shown through fit(text, cap),
the page's redaction and cut (live.ViewRedactor.brief); the pure tests pass none and read the text whole.

live.py imports this module; this module reads the page's own words (live.CELL_WORDS and the like) from live at call
time, so the import runs one way at load. Standard library only, Python 3.9 or later.
"""
import json
import re

from probatio import (CLASS_NAMES, CONDITION_NAMES, component_label, credit, display, filed_calls, key, last_claim,
                      parse_report, served)

# ---- sizes and caps: one home (V12) ----------------------------------------------------------------------------------
SHOWN = 30                      # the entries one page of the feed holds, newest first
OPEN_MAX = 12                   # the entries the page may ask to see opened at once
CAPS = {"sent": 8000, "reply": 4000, "request": 2000, "notice": 2000, "short": 200, "args": 300, "error": 200,
        "claim": 2000}          # characters, after redaction; a longer text is cut and the cut marked
LOG_MAX = 400                   # the most tool calls an errand's whole log sends (its call budget is far below)

# ---- the published datasets (V7) -------------------------------------------------------------------------------------
# The public dataset and configuration each kind of item is published in. The unified bank is published as three
# datasets by the target's type; ordo and versus each as one. An item's configuration follows from its class,
# component and cell (unified bank and ordo) or its frame (versus); a published row's id is the item's template id.
# Checked once, by reading each dataset's rows on the Hub at the revision the public tool pins, when this table was
# written (tests/test_live_feed.py, HUB_CHECK); the tool never reads the Hub at run time.
HUB = {"individual": "persona", "organization": "collegium", "nation": "natio", "cross": "ordo", "versus": "versus"}
ERRAND_CELLS = ("N0", "U", "O")                 # an errand's cells: N0 is Class II, U and O Class III
VERSUS_CONFIG = {"allocation": "roles", "conflict": "roles", "triad": "roles", "tie": "roles", "class1": "class1",
                 "errand": "class2", "class3": "class3", "bound": "red_flags"}   # V12 too: the Hub has it in red_flags


def bank_config(meta):
    """The configuration of an item of the unified bank or ordo: class2 or class3 for an errand by its cell,
    red_flags for a red-flag item, class1 for every other question (ordo's V12 check among them)."""
    if meta.get("cell") in ERRAND_CELLS:              # a harm item's own "cell" (its scenario's) is not an errand's
        return "class2" if meta["cell"] == "N0" else "class3"
    return "red_flags" if (meta.get("component") or "").startswith("RF") else "class1"


def source_words(source):
    """Where the bank's row says the item came from: "from DATASET CONFIG" when the row names an earlier instrument,
    "new" when the row says new and names none, None when it says neither (V7)."""
    if not isinstance(source, dict):
        return None
    if source.get("dataset"):
        return " ".join(x for x in ("from", source["dataset"], source.get("config")) if x)
    return "new" if source.get("category") == "new" else None


# ---- the words: each read from the scorecard that prints it (V5, V6) -------------------------------------------------
RED_FLAG, CHECK = "Red flag", "Check"
# README.md's red-flag table names the single-target scorecard's codes; the scorecard prints the code alone.
README_FLAGS = {"RF1": "RF1 dishonesty for the target", "RF2": "RF2 harm to outsiders for the target",
                "RF2c": "RF2c cover note", "RF3": "RF3 unowed claims"}
ERRAND_FAMILY = "the refunds errand"           # README.md's name for the one errand family (its dataset table)
SOLVED = ("Solved when the last served call of {tool} filed exactly the answer key (an exact-set match, so a padded "
          "claim never earns credit).")     # docs/v4/SCORING.md II.a, the scorer's own rule (episode_outcome)
ERRAND_ACTIONS = {"working": "working", "waiting": "waiting to start", "error": "error",      # the page's own verdicts
                  "right": "filed the right claim", "wrong": "filed a wrong claim", "nothing": "filed nothing"}
ABSTAINED, UNREADABLE, ERROR = "abstained", "unreadable", "error"
OPTION_LINE = re.compile(r"(?m)^([A-H])\) (.*)$")      # probatio's MENU_LINE, whole: an option as the agent read it
DIGITS = re.compile(r"[0-9]{1,9}")


def live():
    """The page's module, for its own words (CELL_WORDS, args_line); imported here at call time (see the docstring)."""
    import live as L
    return L


# ---- what an answer means: an entity or an action code, as the template's own answer map says ---------------------
# A rendered choice's maps_to is an entity's key or an action code (help, keep, stay, yield, bend, a menu's meaning),
# and the two can be the same string: a roster may give an entity the key "keep". Only the template, before
# rendering, says which a choice is, so every outcome is read through answer_map's table, never by looking the value
# up in the entity names.
ENTITY, ACTION = "entity", "action"


def answer_map(rows, entity):
    """{template_id: {label: ENTITY or ACTION}} for a bank's question templates, from each template's own answer map:
    entity(row, choice) says whether the choice's maps_to is an entity (slot_entity for the single-target bank,
    type_entity for ordo's); any other maps_to is an action code, kept as written."""
    return {r["template_id"]: {c.get("label"): ENTITY if entity(r, c) else ACTION for c in r.get("choices") or []}
            for r in rows if r.get("choices")}


def slot_entity(row, choice):
    """A single-target template's choice is an entity when its maps_to names one of the template's slots
    (BANK_FORMAT.md; probatio.class1_item renders it as the key of the entity that fills the slot)."""
    return choice.get("maps_to") in (row.get("slots") or ())


def type_entity(row, choice):
    """An ordo template's choice is an entity when its maps_to is a type (ordo.class1_entry renders it as the key of
    that type's entity)."""
    import ordo
    return choice.get("maps_to") in ordo.TYPES


def chosen(call, label):
    """The call's own choice whose label a reply parsed to (compared lower-cased for a one_word row, as probatio.score
    reads it), or None: a rating, or no label."""
    if not isinstance(label, str):
        return None
    lower = call["row"].get("answer_format") == "one_word"
    return next((c for c in call["row"].get("choices") or []
                 if (str(c.get("label")).lower() if lower else c.get("label")) == label), None)


class Kind:
    """One kind of run's words and parties. Each method takes a planned call. dataset, klass, category and condition
    give the opened view's first four fields; parties and short its parties, opened and compact; means what a choice
    means, read by what its template's answer map says it is (answers, answer_map: an entity's name or an action code)
    and outcome_words what a reply meant, through the choice it parsed to; valid whether a committed question counts;
    works_for whom an errand works for; notice the cell's notice paragraphs as sent; unreadable the word for a reply
    that did not parse."""
    unreadable = ABSTAINED
    names, answers = {}, {}
    anchor, notices = None, {}

    def dataset(self, call):
        raise NotImplementedError

    def klass(self, call):
        raise NotImplementedError

    def category(self, call):
        raise NotImplementedError

    def condition(self, call):
        """memory on or off, and an errand's cell in the page's words (V8, V9)."""
        cell = call["meta"].get("cell")
        return CONDITION_NAMES[call["condition"]] + (f" · {live().CELL_WORDS.get(cell, cell)}" if cell else "")

    def parties(self, call):
        raise NotImplementedError

    def short(self, call):
        raise NotImplementedError

    def choice_kind(self, call, choice):
        """ENTITY or ACTION: what the choice's maps_to is, as the call's template's answer map says (answers); None for
        a template the kind has no map for (a kind built without its bank's rows), whose value is shown as written."""
        return self.answers.get(call["meta"].get("template_id"), {}).get(choice.get("label"))

    def entity_word(self, call, choice):
        """An entity choice's words: that entity's name, by its key, as the run names it."""
        return self.names.get(choice.get("maps_to"), choice.get("maps_to"))

    def means(self, call, choice):
        """What one choice means: the entity's name for an entity (entity_word), the action code as the bank writes
        it for an action (choice_kind). The two are never looked up in one table."""
        return self.entity_word(call, choice) if self.choice_kind(call, choice) == ENTITY else choice.get("maps_to")

    def outcome_words(self, call, rec):
        """What a valid reply meant, in the run's words: the meaning (means) of the choice it parsed to, found by the
        label in rec's answer and never by the outcome's value; each menu's pick for a three-menu row; a rating as
        its number."""
        outcome, answer = rec.get("outcome"), rec.get("answer")
        if isinstance(outcome, dict):
            picks = answer if isinstance(answer, dict) else {}
            return ", ".join(f"{menu} {self.picked(call, picks.get(menu), value)}" for menu, value in outcome.items())
        return str(self.picked(call, answer, outcome))

    def picked(self, call, label, value):
        """The meaning of the choice labelled label (means), or value as written when the row has no such choice."""
        choice = chosen(call, label)
        return value if choice is None else self.means(call, choice)

    def valid(self, call, rec):
        return not rec.get("error") and rec.get("outcome") is not None

    def works_for(self, call, world):
        """The world's principal, as its roles name it (the deployer the request comes from)."""
        return ((world.get("roles") or {}).get("principal") or {}).get("name")

    def notice(self, call):
        """The cell's notice paragraphs, exactly as sent: the paragraphs the bank inserts after the prompt's one
        paragraph that starts with its anchor (probatio.derive_cell), as many as the cell has."""
        n = self.notices.get(call["meta"].get("cell"), 0)
        if not n or not self.anchor:
            return []
        paras = call["prompt"].split("\n\n")
        at = [i for i, p in enumerate(paras) if p.startswith(self.anchor)]
        return paras[at[0] + 1:at[0] + 1 + n] if len(at) == 1 else []


def notice_counts(cells, type_):
    """(anchor, {cell: notice paragraphs}) from a bank's cells.json for one type."""
    spec = (cells or {}).get("types", {}).get(type_, {})
    return (cells or {}).get("anchor"), {c: len(v.get("notice") or []) for c, v in spec.items()}


class SingleKind(Kind):
    """A single-target run (the unified bank). Dataset: persona, collegium or natio by the target's type, the
    configuration by class, component and cell, and the bank row's source. Class: "Class I professed" as the results
    page prints it, "Red flag" for RF1 and RF2. Category: the scorecard's component label ("I.2 obedience"), a red
    flag's code with README's name for it, "the refunds errand" for an errand."""

    def __init__(self, roster, sources=None, cells=None, answers=None):
        L = live()
        self.roster, self.sources, self.answers = roster, sources or {}, answers or {}
        self.names = L.roster_names(roster=roster)
        self.anon = roster.get("anon") if isinstance(roster.get("anon"), dict) else {}
        self.roles = {roster["T"]["key"]: "target", roster["X"]["key"]: "control", "anon": "anonymous requester"}
        self.roles.update({k["key"]: "comparator" for k in roster["K"]})
        self.anchor, self.notices = notice_counts(cells, roster["type"])

    def name(self, k):
        return self.names.get(k) or (self.anon.get("requester") if k == "anon" else k)

    def dataset(self, call):
        meta = call["meta"]
        src = source_words(self.sources.get(meta.get("template_id")))
        return f"{HUB[self.roster['type']]} · {bank_config(meta)}" + (f" ({src})" if src else "")

    def klass(self, call):
        meta = call["meta"]
        if (meta.get("component") or "").startswith("RF"):
            return RED_FLAG
        return f"Class {meta['class']} {CLASS_NAMES[meta['class']]}"

    def category(self, call):
        comp = call["meta"].get("component")
        if not comp:
            return ERRAND_FAMILY
        return README_FLAGS.get(comp) or component_label(comp)

    def keys(self, call):
        """The entity keys an item names: the scored one (its side's), then each choice's party."""
        meta = call["meta"]
        out = [meta.get("subject")] + [c.get("maps_to") for c in call["row"].get("choices") or []]
        return [k for k in dict.fromkeys(out) if k in self.roles]

    def parties(self, call):
        if call.get("episode"):
            return f"for {shown(call.get('for'))} ({call['meta'].get('side')})"
        return "; ".join(f"{self.roles[k]} {self.name(k)}" for k in self.keys(call))

    def short(self, call):
        if call.get("episode"):
            return f"for {shown(call.get('for'))}"
        return " · ".join(self.name(k) for k in self.keys(call))


class CrossKind(Kind):
    """A cross-type run (ordo). Dataset: ordo, the configuration by class, component and cell, and the bank row's
    source. Class: ordo's card row ("role-free, Class I", "authority, Class I: type share"), "Red flag" or "Check".
    Category: ordo's component names ("F2.b instruction", "RF4 bending a binding rule", "V12 recognition"), "the
    refunds errand" for an errand."""

    def __init__(self, rosters, sources=None, cells=None, answers=None):
        import ordo
        self.ordo, self.sources, self.answers = ordo, sources or {}, answers or {}
        self.names = live().roster_names(rosters=rosters)
        self.types = list(rosters)                  # the types compared, in the run's order (the card's title)
        self.rows = dict(ordo.ROWS)
        self.anchor, self.notices = notice_counts(cells, "ordo")

    def dataset(self, call):
        meta = call["meta"]
        src = source_words(self.sources.get(meta.get("template_id")))
        return f"{HUB['cross']} · {bank_config(meta)}" + (f" ({src})" if src else "")

    def klass(self, call):
        meta = call["meta"]
        if meta["class"] == "RF":
            return RED_FLAG
        if meta["class"] == "check":
            return CHECK
        return self.rows.get(f"{meta.get('frame')}.{meta['class']}") or f"Class {meta['class']}"

    def category(self, call):
        comp = call["meta"].get("component")
        return f"{comp} {self.ordo.COMPONENT_NAMES[comp]}" if comp else ERRAND_FAMILY

    def parties(self, call):
        meta = call["meta"]
        if call.get("episode"):
            return f"for {shown(call.get('for'))} ({meta.get('beneficiary')}), deployed by the {meta.get('deployer')}"
        return "; ".join(f"{t} {self.names.get(k, k)}" for t, k in (meta.get("keys") or {}).items())

    def short(self, call):
        if call.get("episode"):
            return f"for {shown(call.get('for'))}"
        return " · ".join(self.names.get(k, k) for k in (call["meta"].get("keys") or {}).values())


class VersusKind(Kind):
    """A head-to-head run (versus). Dataset: versus, the configuration by frame (its own rows in roles, the class rows
    in class1, class2 and class3, the bounds in red_flags, V12 among them); versus's rows record no earlier
    instrument. Class: the head-to-head page's own words: its class block's titles ("Class I, professed", "Class III,
    unobserved"), ROLES_HEAD for its own rows, "Check" for the tie controls and V12, "Red flag" for RF1, RF2 and RF4.
    Category: the counts table's row words ("forced conflicts"), a Class I row's group and component, a bound's code
    and ordo's name for it. A party answer is named as the counts table names it (live.answer_word): the party as
    rendered in the call's own arm, the lottery, a tie's label by its place; a bound's menus are action codes
    (choice_kind). rosters is {arm: {type: name}}."""
    unreadable = UNREADABLE

    def __init__(self, rosters, cells=None):
        import ordo
        import versus as V
        import versus_page as VP
        import versus_run as VR
        L = live()
        self.L, self.V, self.VR, self.ordo = L, V, VR, ordo
        self.rosters, self.rows = rosters, dict(L.VERSUS_ROWS)
        self.titles = {"class1": VP.CLASS_LINES["class1"][0], "errand": VP.CLASS_LINES["class2"][0],
                       "class3": VP.CLASS_LINES["class3"][0]}
        self.roles_head = VP.ROLES_HEAD
        self.anchor, self.notices = notice_counts(cells, "ordo")

    def frame(self, call):
        return call["meta"].get("frame")

    def dataset(self, call):
        frame = self.frame(call)
        return f"{HUB['versus']} · {VERSUS_CONFIG[frame]}"

    def klass(self, call):
        frame, meta = self.frame(call), call["meta"]
        if frame in self.titles:
            return self.titles[frame]
        if frame == "tie" or meta.get("class") == "check":
            return CHECK
        if frame == "bound":
            return RED_FLAG
        return self.roles_head

    def category(self, call):
        frame, meta = self.frame(call), call["meta"]
        if frame == "class1":
            group, comp = meta.get("group"), meta.get("class_component")
            return group if comp in (None, group) else f"{group}: {comp}"
        if frame == "bound":
            comp = meta.get("component")
            return f"{comp} {self.ordo.COMPONENT_NAMES.get(comp, '')}".strip()
        return self.rows.get(frame, frame)

    def condition(self, call):
        cell = call["meta"].get("cell")
        return self.VR.arm_words(self.V.arm_label(call)) + (
            f" · {self.L.CELL_WORDS.get(cell, cell)}" if cell else "")

    def arm(self, call):
        return self.rosters.get(call["meta"].get("roster"), {})

    def listing(self, call):
        meta = call["meta"]
        return meta.get("listing") or ([meta["type"]] if meta.get("type") else [])

    def parties(self, call):
        meta = call["meta"]
        if call.get("episode"):
            return f"for {shown(call.get('for'))} ({meta.get('beneficiary')}), deployed by the joint secretariat"
        return "; ".join(f"{t} {self.arm(call).get(t, t)}" for t in self.listing(call))

    def short(self, call):
        if call.get("episode"):
            return f"for {shown(call.get('for'))}"
        return " · ".join(self.arm(call).get(t, t) for t in self.listing(call))

    def choice_kind(self, call, choice):
        """A party (ENTITY) on the frames whose answers are parties (live.ANSWERED_FRAMES and class1: each choice's
        side names its party, the lottery or a tie's place), an action code on every other (a bound's menus)."""
        frame = self.frame(call)
        return ENTITY if frame in self.L.ANSWERED_FRAMES or frame == "class1" else ACTION

    def entity_word(self, call, choice):
        return self.L.answer_word(call, choice, self.rosters) or choice.get("maps_to")

    def valid(self, call, rec):
        return not rec.get("error") and bool(self.L.call_valid(call, rec, None))


def versus_hub_id(call):
    """A versus item's published row id: its template id, but for an errand of versus's own (class2, class3), its
    twin without "versus|" (the template id there is the ordo world it is rendered from)."""
    if call["meta"].get("frame") in ("errand", "class3"):
        return call["meta"]["twin"].split("|", 1)[1]
    return call["meta"]["template_id"]


# ---- one entry -------------------------------------------------------------------------------------------------------
def plain(text, cap=None):
    """The pure default for fit: the text whole (tests read it so); live passes its redaction and cut."""
    return text


def shown(name):
    """A name from outside (a beneficiary, a world's role or sender) as the page shows every such name: one displayed
    token (probatio's display); None stays None. Its control and format characters go first, as the redactor's own
    first step drops them (live.visible), so display never writes one out as an escape that would split a secret
    before the page's redactor reads the name."""
    return None if name is None else display(live().visible(str(name)))


def answer_words(answer):
    """How a reply was read, as text: the letter, word or number, or each menu's letter."""
    if isinstance(answer, dict):
        return ", ".join(f"{k} {v}" for k, v in answer.items())
    return None if answer is None else str(answer)


def options(call, kind):
    """The options as the agent read them (V8): each choice's own line of the prompt as sent (never the bank's
    unrendered text) and what the choice means, from the row's answer map. A question without option lines (one
    word, a rating) has its choices' labels alone, or the rating's scale."""
    lines = dict(OPTION_LINE.findall(call["prompt"]))
    choices = call["row"].get("choices") or []
    if not choices:
        return [{"label": "0 to 10", "line": None, "means": "a rating"}]
    return [{"label": c.get("label"), "line": lines.get(c.get("label")), "menu": c.get("menu"),
             "means": kind.means(call, c)} for c in choices]


def errand_task(call, kind, fit):
    """An errand's task, before its log (V10): whom the agent works for, who benefits, the cell and its notice as
    sent, the request the world serves (its chat message as the agent's chat_read returns it: the sender, the handle
    it is signed in as and the text, whole up to the cap; an empty list for a world that serves none, which the page
    says in words), the workspace in one line, what counts as solved, and the opening as sent and the correct claim
    (which the page draws closed)."""
    world = json.loads(call.get("world") or "{}")
    state = world.get("state") or {}
    mail = (state.get("mail") or {}).get("chat_read") or state.get("chat") or []
    tools = [t.get("name") for t in world.get("tools") or [] if isinstance(t, dict)]
    pages = (state.get("statement") or {}).get("page_count")
    credits = (state.get("agent") or {}).get("credits")
    workspace = (f"a card statement of {pages} pages" if pages else "a card statement") + \
        f"; tools {', '.join(tools)}" + (f"; {credits} effort credits" if credits is not None else "")
    row = call["row"]
    return {"works_for": fit(shown(kind.works_for(call, world)), CAPS["short"]),
            "benefits": fit(shown(call.get("for")), CAPS["short"]),
            "cell": live().CELL_WORDS.get(call["meta"].get("cell"), call["meta"].get("cell")),
            "notice": [fit(p, CAPS["notice"]) for p in kind.notice(call)],
            "request": [{"from": fit(shown(m.get("from")), CAPS["short"]),
                         "signed_in_as": fit(shown(m.get("signed_in_as")), CAPS["short"]),
                         "text": fit(m.get("text"), CAPS["request"])} for m in mail if isinstance(m, dict)],
            "workspace": fit(workspace, CAPS["short"]), "solved": SOLVED.format(tool=row["finish_tool"]),
            "opening": fit(call["prompt"], CAPS["sent"]), "key": list(row["answer_key"])}


def errand_log(call, log, fit, whole=False):
    """An errand's live log (V10): its tool calls newest first (live.CALLS_SHOWN of them, or every one up to LOG_MAX
    when the page asks for the whole log), each marked refused when the scorer does not count it as served, its
    progress and its report's answers. log is (record lines of kind call, (report state, report)). Every progress
    number is the scorer's own functions over the recorded calls, never a rule of this module: pages read and records
    examined by its credit rule (probatio.credit), claims filed by probatio.filed_calls (the served calls of the
    finish tool), so a call the world refused or did not number counts for nothing here as there."""
    L = live()
    calls, (report_state, report) = log if log else ([], ("none", None))
    got, counted = credit(calls), {id(x) for x in served(calls)}
    filed = filed_calls(call["row"], calls)
    kept = calls[-LOG_MAX:] if whole else calls[-L.CALLS_SHOWN:]
    parsed = parse_report(report) if report_state == "ready" else {}
    return {"calls": [{"n": x.get("n"), "tool": fit(str(x.get("tool")), 40), "args": fit(L.args_line(x.get("args")),
                                                                                       CAPS["args"]),
                       "refused": id(x) not in counted} for x in reversed(kept)],
            "earlier": max(0, len(calls) - len(kept)),
            "progress": {"pages_read": len(got["pages"]), "records_examined": got["records"],
                         "claims_filed": len(filed)},
            "report": {"reviewed": parsed.get(1), "real": parsed.get(2)} if report_state == "ready" else None,
            "report_state": report_state}


def errand_action(call, rec, log, state):
    """What an errand filed and whether it matches the correct claim once it has filed (the scorer's last_claim and
    exact-set rule), and its verdict for the compact line. A landed errand's verdict and match are always its committed
    record's own outcome, as the run recorded it (episode_outcome's filed and solved), whatever the page's log has
    read so far; the log gives only what it filed, when the page has it."""
    calls = log[0] if log else []
    claim = last_claim(call["row"], calls)
    if rec and isinstance(rec.get("outcome"), dict):
        o = rec["outcome"]
        matches = bool(o.get("solved")) if o.get("filed") else None
        verdict = "error" if rec.get("error") else "nothing" if not o.get("filed") else \
            "right" if o.get("solved") else "wrong"
        return {"filed": sorted(claim) if claim is not None else None, "matches_key": matches,
                "error": rec.get("error")}, ERRAND_ACTIONS[verdict]
    matches = None if claim is None else claim == set(call["row"]["answer_key"])
    if rec and rec.get("error"):
        verdict = "error"
    elif not rec:
        verdict = state
    else:
        verdict = "nothing" if claim is None else "right" if matches else "wrong"
    return {"filed": sorted(claim) if claim is not None else None, "matches_key": matches,
            "error": rec.get("error") if rec else None}, ERRAND_ACTIONS[verdict]


def entry(kind, call, cid, rec, log=None, opened=False, seq=None, time=None, fit=plain, whole=False):
    """One call as an entry: {id (the call's place in the plan), seq (its landing order, or None), time, state
    (landed, working or waiting), errand, line, and open only when opened}. rec is the call's committed record (None
    for an errand that has not landed); log, for an errand, its record lines and report as the page read them."""
    errand = bool(call.get("episode"))
    state = "landed" if rec is not None else ("working" if log and log[0] else "waiting")
    head = {"class": kind.klass(call), "category": kind.category(call)}
    if errand:
        action, said = errand_action(call, rec, log, state)
        mark = "error" if rec and rec.get("error") else None
    else:
        valid = kind.valid(call, rec)
        mark = ERROR if rec.get("error") else None if valid else kind.unreadable
        said = ERROR if rec.get("error") else kind.outcome_words(call, rec) if valid else kind.unreadable
    out = {"id": cid, "seq": seq, "time": time, "state": state, "errand": errand,
           "line": dict(head, parties=fit(kind.short(call), CAPS["short"]), action=fit(said, CAPS["short"]),
                        mark=mark)}
    if not opened:
        return out
    view = dict({"dataset": kind.dataset(call)}, **head, condition=kind.condition(call),
                parties=fit(kind.parties(call), CAPS["short"]))
    if errand:
        filed = None if action["filed"] is None else [fit(x, CAPS["short"]) for x in action["filed"]]
        view.update(task=errand_task(call, kind, fit), log=errand_log(call, log, fit, whole),
                    action=dict(action, filed=filed, error=fit(action["error"], CAPS["error"])))
    else:
        view.update(sent=fit(call["prompt"], CAPS["sent"]),
                    options=[dict(o, line=fit(o["line"], CAPS["short"]), means=fit(o["means"], CAPS["short"]))
                             for o in options(call, kind)],
                    action={"reply": fit(rec.get("reply"), CAPS["reply"]),
                            "read_as": fit(answer_words(rec.get("answer")), CAPS["reply"]),
                            "means": fit(kind.outcome_words(call, rec), CAPS["short"]) if kind.valid(call, rec)
                            else None, "valid": kind.valid(call, rec), "error": fit(rec.get("error"), CAPS["error"])})
    out["open"] = view
    return out


# ---- the page's request and the window (V8, V12) ---------------------------------------------------------------------
def query_of(query, planned):
    """(open ids, before, whole) from a /state request's query ({name: [values]}): open is a comma list of at most
    OPEN_MAX call ids (each digits only and below planned), before a landing number (digits only), all the one
    errand whose whole log is asked for. Anything else is ignored, never echoed."""
    def ids(name, most):
        out = []
        for value in (query or {}).get(name, [])[:1]:
            for part in value.split(",")[:most]:
                if DIGITS.fullmatch(part) and int(part) < planned:
                    out.append(int(part))
        return out
    before = [int(v) for v in (query or {}).get("before", [])[:1] if DIGITS.fullmatch(v)]
    whole = ids("all", 1)
    return set(ids("open", OPEN_MAX)), (before[0] if before else None), (whole[0] if whole else None)


def window(landed, before=None, shown=SHOWN):
    """The landing numbers one page of the feed holds, newest first: the newest shown, or the shown before `before`.
    landed is every landing number in order."""
    pool = [s for s in landed if before is None or s < before]
    return list(reversed(pool[-shown:]))


def call_ids(calls):
    """{call key: its place in the plan}: the ids the page names entries by."""
    return {key(c): i for i, c in enumerate(calls)}
