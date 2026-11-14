#!/usr/bin/env python3
"""versus_run: the versus command, from `--versus TYPE=NAME` on the command line to a finished run folder
(docs/versus/DESIGN.md 9). One job: the run path of a versus run, its arguments, preflight, calls, resume, rescore
and result lines. The plan, the identity and the scoring are versus.py's; the agent calls, the run loop, the lock,
the memory block and the safe writer are probatio's; the result files are versus_page's. Nothing here edits or
patches any of them.

    python3 probatio.py --agent claude --versus individual="Jane Doe" --versus organization="Acme Corp" \\
        [--versus nation="Norland"] [--tier quick|standard] [--memory on|off|both] [--strangers on|off] ...

probatio.main hands a command line holding --versus to main(); probatio.main_rescore hands --rescore FOLDER to
rescore_main() when FOLDER holds versus-run.json. The flow of a run:

    check_words (options of the other paths refused before any parser)  ->  parse  ->  versus_names
    -> the bank, the ordo bank, the plan (versus.plan: every arm in one plan, one folder)
    -> preflight (head, bank status, v4's memory block, each arm priced, the hint, what it measures, the folder)
    -> [--dry-run stops here]  ->  claim (v4's lock)  ->  versus.begin_run (identity, memory, launch)
    -> [--watch: live.VersusWatch prints its address before the first call; watch_view starts its counts]
    -> probatio.run_calls (v4's runner: questions, errands, bounds; v4's live line, quota and launch records)
    -> versus.score and versus.table  ->  versus_page.files and versus_page.results_html (results.html, the
       results page) written through probatio.replace_results
    -> answer_lines printed (versus_page.answer_lines, the trust sentence, the folder, the page), then with --watch the
       same lines on the page, which stays up until Ctrl-C (probatio.hold_page)

A resume is the same command on the same folder (resume_refusal names the first thing that differs otherwise); a
rescore reads only what the folder saved and starts no process. Every line printed that carries a string from
outside (a name, a path, the CLI's version line) passes through probatio's display() first. Standard library only,
Python 3.9 or later.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import ordo
import probatio as P
import versus as VS

# ---- the words a versus run prints, each in one place ----------------------------------------------------------------
VERSUS_OPTION = "--versus"
# options that belong only to probatio's other runs (v4's single-type and compare paths, v3): with --versus each is
# one plain line and exit 2, decided from the words before any parser runs (check_words)
OTHER_PATH_OPTIONS = ("--target", "--target-type", "--compare", "--class", "--name", "--entities", "--print-entities",
                      "--target-short", "--target-possessive", "--arm", "--auto-memory", "--ui") + P.V3_ONLY_FLAGS
OTHER_PATH_LINE = ("{option} belongs to probatio's other runs, not to a versus run (--versus TYPE=NAME compares your "
                   "own names with each other); drop it, or drop --versus")
COUNT_LINE = "a versus run takes two or three --versus TYPE=NAME, one per type ({types}); this command gives {n}"
FORM_LINE = "--versus {value}: write TYPE=NAME, with TYPE one of {types}"
TWICE_LINE = "--versus names the {type} twice; give each type at most once"
TYPES_WORDS = ", ".join(VS.TYPES)
FOLDER_UNUSABLE = "the results folder {folder} cannot be used ({why}); pass --out with another folder"
WORKDIR_LINE = "--workdir {}: no folder is there; pass the folder the agent should run in"
BANK_FOLDER_LINE = "--bank {}: no folder is there; pass a versus bank's folder (its manifest.json and src/)"
PAGE_MISSING = ("a versus run writes its page with versus_page.py beside probatio.py ({}); restore it from git, or "
                "merge the branch that adds it")
V4_FOLDER_LINE = ("{folder} holds a probatio v3, v4 or ordo run (its run.json); a versus run starts in a folder of its "
                  "own: pass --out with another folder")
STRAY_REPLIES_LINE = ("{folder} holds replies but no versus-run.json, so they belong to no versus run; pass --out with "
                      "another folder")
RESUME_LINE = ("{folder} holds another versus run: {what} differs ({theirs} there, {mine} here). To start a new run, "
               "pass --out with another folder; to continue that one, run the command that started it.")
HEAD_LINE = "probatio versus: {names} · {tier} tier · {agent} ({cli}), model {model}, effort {effort}"
NAMES_LINE = "  the names as the items write them: {parties}"
PARTNER_LINE = "  the errands' fixed third partner: {party} (the same in every arm)"
STRANGERS_LINE = "  the unfamiliar roster, in the names' places in its own arm: {parties}"
BANK_STATUSES = {   # what the preflight, the form and the live page say of the bank, by its state (bank_state)
    "development": "development bank: pilot, not calibrated",
    "registered": "registered bank: reviewed and frozen, not yet validated on live agents (pilot: not calibrated)",
    "validated": "validated bank",
}
BANK_LINE = "  {status} (bank {folder}, sha256 {sha})"
PRICE_HEAD = "  {tier} tier at --jobs {jobs}, priced arm by arm:"
ARM_LINE = "    {arm:<34} {questions:>6} questions {episodes:>5} episodes   {time}"
TOTAL_LINE = "    {arm:<34} {questions:>6} questions {episodes:>5} episodes   {time} ({label})"
CLASS_LINE = "      of which by class: Class I {questions} questions ({qtime}){class3}"   # under an @2 arm's line
CLASS3_PART = ", Class III {episodes} errand episodes ({etime})"
CLASS3_NOTE = "  Class III (the errands under the unobserved notice) is asked at the standard tier only"
ARM_WORDS = {"names": "your names", "unfamiliar": "the unfamiliar roster"}
MEASURES = ("  it compares these names with each other, in these scenarios, with the memory this run tests",
            "  it does not cross the three names over the three roles: the conflicts put each name in the deployer's "
            "and the client's seat, the triad keeps its fixed roles",
            "  the quick tier is descriptive and prints no sentence; the standard tier's sentences rest on "
            "simultaneous bounds")
FOLDER_FRESH = "  folder {folder}: starts fresh"
FOLDER_RESUME = "  folder {folder}: resumes the run it holds ({n} of {total} calls answered)"
FOLDER_REFUSED = "  folder {folder}: refused: {why}"
RESULTS_LINE = "Results: {folder}"
PAGE_LINE = "Page: {page}"                     # the results page (versus_page.HTML_FILE), printed when it was written
RESCORED_LINE = ("rescored {at} by probatio {by} (versus scorer {scorer}) from this folder's saved replies and "
                 "records, with no agent called; the run was scored by probatio {scored_by}")
# what may never be a result file's name: what the run itself keeps in its folder
RUN_KEEPS = (VS.RUN_FILE, "replies.jsonl", VS.CODES_FILE, "quota.jsonl", "launches.jsonl", "run.lock", "episodes",
             P.PREVIOUS, "codex-homes", "rendered.jsonl")
BAD_PAGE_FILE = "versus_page.files returned {name!r}, which is not a plain result file name; the page must be fixed"
CLAIMS_REFUSED = "{reason}; no page was written from this card"   # the page's ClaimsMismatch, in one line
WORDS = {  # what each part of a run record is called in a refusal (resume_refusal), in the order they are compared
    ("identity", "participants"): "the set of names", ("identity", "partner"): "the fixed third partner",
    ("identity", "tier"): "the tier", ("identity", "arms", "memory"): "--memory",
    ("identity", "arms", "strangers"): "--strangers", ("identity", "bank", "sha256"): "the bank's pin",
    ("identity", "ordo_bank_sha256"): "the ordo bank's pin", ("identity", "forms"): "the forms version",
    ("identity", "fixed_text"): "the fixed-text version", ("identity", "scorer"): "the scorer's version",
    ("identity", "estimator"): "the estimator's version", ("identity", "recipe"): "the recipe",
    ("launch", "agent"): "the agent", ("launch", "model"): "the model", ("launch", "cli_version"): "the CLI version",
    ("launch", "effort"): "the effort", ("launch", "config_dir"): "the config folder",
    ("memory_files",): "the memory record (each memory file's sha256)"}


def refuse_line(line):
    """A command-line refusal: one plain line on stderr, every unseen character in it shown escaped, and exit 2
    before any part of the run happens (as v4's --config-dir and --rescore refusals are)."""
    print(P.refusal_text(line), file=sys.stderr)
    raise SystemExit(2)


# ---- the command line ------------------------------------------------------------------------------------------------
def wants_versus(argv):
    """Whether a command line is a versus run: it holds --versus, written in full, alone or with "="."""
    return any(w == VERSUS_OPTION or w.startswith(VERSUS_OPTION + "=") for w in argv)


def check_words(argv):
    """Refuse, from the words alone and before any parser can print a usage block, an option that belongs only to
    probatio's other runs (OTHER_PATH_OPTIONS), written in full or as the start of one (argparse's abbreviation);
    --rescore has already been refused by probatio's own rule (rescore_command) unless it is alone."""
    own = {o for a in parser()._actions for o in a.option_strings}
    for word in argv:
        name = word.split("=", 1)[0]
        if not name.startswith("--") or name in own:
            continue
        hit = next((o for o in OTHER_PATH_OPTIONS if o == name or (len(name) > 3 and o.startswith(name))), None)
        if hit:
            refuse_line(OTHER_PATH_LINE.format(option=P.display(hit)))


def parser():
    """versus's argument parser: probatio's Parser (its error line displayed), no abbreviations, each option meaning
    what it means in v4 unless its help says otherwise. The one home of the command's options, which the tests
    enumerate."""
    p = P.Parser(prog="probatio.py --versus", allow_abbrev=False,
                 description="probatio versus: compare your agent's loyalty to your own person, organization and "
                             "government, head to head, with or without its memory files.")
    p.add_argument("--agent", choices=("claude", "codex"), required=True)
    p.add_argument(VERSUS_OPTION, action="append", default=[], metavar="TYPE=NAME",
                   help="a name to compare, one per type (individual, organization, nation); two or three of them")
    p.add_argument("--model", type=P.model_name,
                   help="model for the CLI, one word (default: the CLI's own; for Codex, your config.toml's)")
    p.add_argument("--tier", default="standard", choices=VS.TIERS,
                   help="quick (descriptive: two scenarios per family, no sentence) or standard (default); no full "
                        "tier")
    p.add_argument("--memory", default="on", choices=tuple(VS.MEMORY_ARMS),
                   help="on (default, the agent as it runs), off, or both (each question in both)")
    p.add_argument("--strangers", default="off", choices=("on", "off"),
                   help="on also runs the fixed unfamiliar roster in the names' places, the comparator of design 6")
    p.add_argument("--effort", default=P.DEFAULT_EFFORT, choices=P.EFFORTS,
                   help=f"reasoning effort, pinned for every call (default {P.DEFAULT_EFFORT})")
    p.add_argument("--config-dir", metavar="DIR",
                   help="the agent's config folder for this run, whose memory files it tests, as in v4")
    p.add_argument("--jobs", type=int, default=4, help="calls at once (default 4)")
    p.add_argument("--workdir", default=str(Path.home()), help="where the agent runs (default: your home)")
    p.add_argument("--out", help="results folder; the same --out resumes a run")
    p.add_argument("--bank", help="read the versus bank from this folder instead of bank/versus (for bank authors)")
    p.add_argument("--dry-run", action="store_true",
                   help="print the preflight and stop (with --out, also write every rendered item to rendered.jsonl)")
    p.add_argument("--watch", action="store_true",
                   help="serve a live page on 127.0.0.1 with the run's counts so far, and its answer when it ends")
    return p


def parse(argv):
    """The command line: the words checked (check_words), then parsed."""
    check_words(argv)
    a = parser().parse_args(argv)
    a.strangers_on = a.strangers == "on"
    return a


def versus_names(values):
    """{type: name} from the --versus values: two or three, each TYPE=NAME with TYPE one of versus's types, each type
    once; anything else is one plain line and exit 2. The names themselves go through check_names."""
    if len(values) not in (2, 3):
        refuse_line(COUNT_LINE.format(types=TYPES_WORDS, n=len(values)))
    names = {}
    for value in values:
        type_, eq, name = value.partition("=")
        if not eq or type_ not in VS.TYPES:
            refuse_line(FORM_LINE.format(value=P.quoted(value), types=TYPES_WORDS))
        if type_ in names:
            refuse_line(TWICE_LINE.format(type=type_))
        names[type_] = name
    return {t: names[t] for t in VS.TYPES if t in names}


def check_names(bank, names, strangers):
    """The names through probatio's name guard and versus's own refusals (versus.resolve_names, versus.arm_rosters: the
    same name or code twice, a name equal to a two-name run's fixed third partner, to its type's unfamiliar entity
    with strangers on, or to the joint secretariat), each one plain line and exit 2. Returns the resolved parties."""
    try:
        parties = VS.resolve_names(names)
        VS.arm_rosters(bank, parties, strangers)
    except VS.VersusError as e:
        refuse_line(str(e))
    return parties


# ---- the bank, the plan, the folder ----------------------------------------------------------------------------------
def check_folders(a):
    """Refuse a --workdir or a --bank that is not a folder, one plain line each with the path displayed and exit 2,
    before v4's own checks or the bank's loader could quote the path raw."""
    for given, line in ((a.workdir, WORKDIR_LINE), (a.bank, BANK_FOLDER_LINE)):
        if given is not None and not folder_exists(given):
            refuse_line(line.format(P.display(given)))


def folder_exists(given):
    """Whether a path names a folder; one the system cannot even look at (a NUL in it) names none."""
    try:
        return Path(given).is_dir()
    except (OSError, ValueError):
        return False


def load_bank(a):
    """The versus bank: --bank's folder (unpinned, as v4's --bank is) or bank/versus under its pin, loaded as a
    development bank may be, since the command says what the bank is in its preflight (bank_status)."""
    if a.bank:
        return bank_at(Path(a.bank), None)
    return bank_at(VS.BANK, VS.VERSUS_MANIFEST_SHA256)


def bank_at(folder, pin):
    """versus.load_bank(folder, pin), a development bank allowed, with its refusals' folder shown as one token: the
    loader quotes the folder, and the files in it, as given, so each time the folder's path appears in a refusal it
    is put back as probatio's display shows it (escaped, cut in the middle past 512 characters), as check_folders
    shows a --bank that is not a folder. A refusal whose folder needs neither is unchanged."""
    try:
        return VS.load_bank(folder, pin, development=True)
    except VS.VersusError as e:
        said, given = str(e), str(folder)
        if given not in said or P.display(given) == given:
            raise
        raise VS.VersusError(said.replace(given, P.display(given))) from None


def bank_state(bank):
    """The bank's state: development (its manifest says so), validated (a registered bank whose manifest is marked
    validated) or registered (reviewed and frozen, not yet validated on live agents)."""
    if bank.development:
        return "development"
    return "validated" if bank.manifest.get("validated") is True else "registered"


def bank_status(bank):
    """What the preflight, the form (ui's versus label) and the live page (page_facts) say of the bank: its state's
    words, BANK_STATUSES, their one home."""
    return BANK_STATUSES[bank_state(bank)]


def check_world():
    """Refuse an episode engine that is not the one the errands were made with, as v4 does."""
    if P.hashlib.sha256(P.WORLD.read_bytes()).hexdigest() != P.WORLD_SHA256:
        raise SystemExit(f"{P.display(P.WORLD)} is not the episode engine the errands were made with; restore it from "
                         "git.")


def arms_of(a):
    """The plan's arms from the command line."""
    return {"memory": a.memory, "strangers": a.strangers_on}


def run_name(a, names):
    """The results folder's name by v4's rule: versus-<agent>-<the named types joined>-<tier>-memory-<memory>
    [-strangers] and the model's part (probatio.run_suffix), so the two families' folders read alike."""
    return (f"versus-{a.agent}-{'-'.join(t for t in VS.TYPES if t in names)}-{a.tier}-memory-{a.memory}"
            + ("-strangers" if a.strangers_on else "") + P.run_suffix(a))


def run_folder(a, names):
    """--out, or results/<run_name> beside probatio.py, made absolute (the Codex homes inside it are handed to a CLI
    that runs elsewhere). A folder the system cannot even look at (a name too long, as a very long --model makes the
    default one) is one plain line and exit 2 (FOLDER_UNUSABLE)."""
    out = (Path(a.out) if a.out else P.HERE / "results" / run_name(a, names)).resolve()
    try:
        out.exists()
        (out / VS.RUN_FILE).exists()
    except (OSError, ValueError) as e:
        refuse_line(FOLDER_UNUSABLE.format(folder=P.display(out), why=P.display(getattr(e, "strerror", None) or e)))
    return out


def get(record, path):
    """The value at a key path of a nested record, or None."""
    for k in path:
        record = record.get(k) if isinstance(record, dict) else None
    return record


def shown_value(value):
    """A record's value as a refusal quotes it: a participants record as its rendered names, anything else as
    canonical JSON, displayed and cut."""
    if isinstance(value, dict) and all(isinstance(v, dict) and "rendered" in v for v in value.values()):
        value = ", ".join(v["rendered"] for v in value.values())
    text = value if isinstance(value, str) else VS.canonical(value)
    return P.display(text, cap=120)


def resume_refusal(out, saved, record):
    """The line that refuses a folder's saved run record (saved) for this command's record, or None when they are
    the same run. The record parts are compared in WORDS' order, each by versus.first_difference, so the line names
    the first meaningful difference (a name before a count of calls); anything else that differs is named by its
    key path. The comparison is versus.resume_view's, so --jobs may change."""
    theirs, mine = VS.resume_view(saved), VS.resume_view(record)
    for path, words in WORDS.items():
        if VS.first_difference(get(theirs, path), get(mine, path)):
            return RESUME_LINE.format(folder=P.display(out), what=words, theirs=shown_value(get(theirs, path)),
                                      mine=shown_value(get(mine, path)))
    differ = VS.first_difference(theirs, mine)
    if differ:
        return RESUME_LINE.format(folder=P.display(out), what=P.display(differ), theirs="saved", mine="this command's")
    return None


def folder_state(out, record):
    """What the folder will do: ("fresh", None), ("resume", answered calls) or ("refused", the line). A folder with a
    v3, v4 or ordo run.json, with replies but no versus-run.json, or holding another versus run is refused, reading
    only (nothing is written to decide it)."""
    if (out / "run.json").exists():
        return "refused", V4_FOLDER_LINE.format(folder=P.display(out))
    if (out / VS.RUN_FILE).exists():
        line = resume_refusal(out, VS.read_json(out / VS.RUN_FILE), record)
        if line:
            return "refused", line
        done = P.saved_replies(out / "replies.jsonl")
        return "resume", sum(1 for rec in done.values() if not rec.get("error"))
    if (out / "replies.jsonl").exists():
        return "refused", STRAY_REPLIES_LINE.format(folder=P.display(out))
    return "fresh", None


# ---- the preflight ---------------------------------------------------------------------------------------------------
def rendered_names(parties):
    """The names as rendered, joined in plain English ("Jane Doe, Acme Corp and the government of Norland")."""
    shown = [P.display(parties[t]["full"]) for t in VS.TYPES if t in parties]
    return shown[0] if len(shown) == 1 else ", ".join(shown[:-1]) + " and " + shown[-1]


def arm_words(label):
    """An arm label ("names:on") as the preflight names it ("your names, memory on")."""
    roster, memory = label.split(":")
    return f"{ARM_WORDS[roster]}, memory {memory}"


def priced(a, calls):
    """(questions, episodes, minutes at --jobs) of some calls under the run's profile (probatio.run_profile), the
    one time model v4 prices with."""
    profile = P.run_profile(a)[0]
    episodes = sum(1 for c in calls if c.get("episode"))
    return len(calls) - episodes, episodes, P.plan_minutes(calls, profile, a.jobs)


def class_line(a, calls):
    """An @2 arm's class rows, under its line (they are in its counts too): the Class I questions and, at the
    standard tier, the Class III errand episodes, each with its minutes; None for an arm with no class row (an @1
    plan's, which prints as it always did)."""
    class1 = [c for c in calls if c["meta"].get("frame") == "class1"]
    class3 = [c for c in calls if c["meta"].get("frame") == "class3"]
    if not class1 and not class3:
        return None
    part = (CLASS3_PART.format(episodes=len(class3), etime=P.duration(priced(a, class3)[2] * 60)) if class3 else "")
    return CLASS_LINE.format(questions=len(class1), qtime=P.duration(priced(a, class1)[2] * 60), class3=part)


def price_lines(a, plan_):
    """Each arm on its own line in questions, episodes and minutes (an @2 arm with its class rows under it,
    class_line), then the total with the rates' label, then the line count_plan carries on what the minutes are (an
    estimate from ordo's rate), and for an @2 plan, once, that Class III is asked at the standard tier only."""
    by = {}
    for c in plan_.calls:
        by.setdefault(VS.arm_label(c), []).append(c)
    lines = [PRICE_HEAD.format(tier=a.tier, jobs=a.jobs)]
    classes = False
    for label, calls in sorted(by.items()):
        q, e, m = priced(a, calls)
        lines.append(ARM_LINE.format(arm=arm_words(label), questions=q, episodes=e, time=P.duration(m * 60)))
        line = class_line(a, calls)
        if line:
            lines.append(line)
            classes = True
    q, e, m = priced(a, plan_.calls)
    lines.append(TOTAL_LINE.format(arm="all", questions=q, episodes=e, time=P.duration(m * 60),
                                   label=P.display(P.run_profile(a)[1])))
    lines.append("  " + VS.count_plan(plan_.calls, a.jobs)["minutes_basis"])
    if classes:
        lines.append(CLASS3_NOTE)
    return lines, m


def time_hint(a, bank, ordo_bank, names, total):
    """The 45-minute hint (the budget's warning_minutes, as v4's), offering only what can shorten this run, each
    the difference to this command re-planned that way and offered only when it saves time (probatio.offer): --tier
    quick, --memory on (from both), --strangers off, and the time model's --jobs. None at or under the budget."""
    if total <= P.pricing()["warning_minutes"]:
        return None
    def minutes(**change):
        """This command's minutes with one change: another tier, memory setting or strangers setting."""
        arms = dict(arms_of(a), **{k: v for k, v in change.items() if k in ("memory", "strangers")})
        calls = VS.plan(bank, names, change.get("tier", a.tier), arms, ordo_bank).calls
        return priced(a, calls)[2]
    savings = []
    if a.tier == "standard":
        P.offer(savings, "--tier quick", total - minutes(tier="quick"))
    if a.memory == "both":
        P.offer(savings, "--memory on", total - minutes(memory="on"))
    if a.strangers_on:
        P.offer(savings, "--strangers off", total - minutes(strangers=False))
    jobs = P.pricing()["jobs"]
    if a.jobs < jobs:
        P.offer(savings, f"--jobs {jobs}", total - total * a.jobs / jobs)       # the estimate divides by --jobs
    return f"about {P.minutes_text(total)} minutes" + ("; to shorten: " + ", or ".join(savings) if savings else "")


def memory_people(parties):
    """The named person, for the memory block's name note (v4's memory_report reads {"full", "short"})."""
    p = parties.get("individual")
    return [{"full": p["full"], "short": p["full"]}] if p else []


def preflight(a, opts, bank, ordo_bank, names, parties, plan_, out):
    """Print what the run will do, before any call, in this order: the head (versus, the names as rendered and
    joined, the tier, the agent and model), the names as the items write them and, as they apply, the fixed partner
    and the unfamiliar roster; the bank's status (bank_status); the memory block exactly as v4 prints it
    (probatio.print_memory); each arm priced and the total (price_lines); the 45-minute hint (time_hint); what the
    run measures and does not (MEASURES); and what the folder will do (folder_state). Returns (cli version line,
    memory report, the run record this command would save, the folder's state)."""
    cli = P.subprocess.run([a.agent, "--version"], capture_output=True, text=True).stdout.strip()
    report = P.memory_report(a.agent, opts.workdir, a.memory, "as-configured", opts.config_dir, opts.config_source,
                             memory_people(parties))
    ident = plan_.identity
    print(HEAD_LINE.format(names=rendered_names(parties), tier=a.tier, agent=a.agent,
                           cli=P.display(P.one_line(cli)), model=P.display(opts.model or "CLI default"),
                           effort=opts.effort))
    print(NAMES_LINE.format(parties="; ".join(P.display(parties[t]["party"]) for t in VS.TYPES if t in parties)))
    if ident["partner"]:
        print(PARTNER_LINE.format(party=P.display(ident["partner"]["party"])))
    if ident["unfamiliar"]:
        print(STRANGERS_LINE.format(parties="; ".join(P.display(p["party"]) for p in ident["unfamiliar"].values())))
    print(BANK_LINE.format(status=bank_status(bank), folder=P.display(bank.folder), sha=bank.sha256[:12]))
    P.print_memory(a, opts, report)
    lines, total = price_lines(a, plan_)
    print("\n".join(lines))
    hint = time_hint(a, bank, ordo_bank, names, total)
    if hint:
        print(f"  {hint}")
    print("\n".join(MEASURES))
    record = VS.run_record(plan_, P.memory_record(report)["memory_files"], launch(a, opts, cli, report))
    check_writable(record)
    state, detail = folder_state(out, record)
    folder = P.display(out)
    print({"fresh": FOLDER_FRESH, "resume": FOLDER_RESUME, "refused": FOLDER_REFUSED}[state].format(
        folder=folder, n=detail, total=len(plan_.calls), why=detail))
    return cli, report, record, (state, detail)


UNWRITABLE_LINE = ("{what} holds a character that cannot be written as UTF-8 (a byte that is not text, or a lone "
                   "surrogate): {value}. A versus run saves it in versus-run.json, so nothing was written; rename it, "
                   "or pass another, so that every value the run saves is text.")
RECORD_WORDS = {("launch", "config_dir"): "The config folder", ("launch", "model"): "The model",
                ("launch", "cli_version"): "The agent CLI's version line", ("launch", "agent"): "The agent",
                ("launch", "effort"): "The effort", ("memory_files",): "A memory file's path"}


def check_writable(record):
    """Refuse, in one displayed line naming the value (UNWRITABLE_LINE, RECORD_WORDS), a run record holding a string
    that cannot be written as UTF-8: a path holding a byte that is not text (Python carries it as a lone surrogate),
    or a lone surrogate itself, in a name, the model, the CLI's version line, the config folder or a memory file's
    path. It runs in the preflight, before any file exists, so such a value is never half saved."""
    for where, value in record_strings(record):
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            what = RECORD_WORDS.get(where, "The run record's " + ".".join(map(str, where)))
            raise SystemExit(UNWRITABLE_LINE.format(what=what, value=P.display(value)))


def record_strings(value, where=()):
    """Every string of a run record, keys included, as (where, string): a dict's key is at where itself (so a memory
    file's path is at ("memory_files",)), its value at where + (key,)."""
    if isinstance(value, str):
        yield where, value
    elif isinstance(value, dict):
        for k, v in value.items():
            if isinstance(k, str):
                yield where, k
            yield from record_strings(v, where + (k,))
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            yield from record_strings(v, where + (i,))


def launch(a, opts, cli, report):
    """versus-run.json's launch record (versus.launch_record) for this command: the agent, the model, the CLI's
    version line, the pinned effort, the config folder as v4's memory_record writes it, --jobs, and the memory files'
    total size in bytes (memory_record's memory_bytes, summed)."""
    memory = P.memory_record(report)
    return VS.launch_record(a.agent, opts.model or "CLI default", P.one_line(cli), opts.effort, memory["config_dir"],
                            a.jobs, sum(memory["memory_bytes"].values()))


# ---- the run ---------------------------------------------------------------------------------------------------------
def page():
    """versus_page.py beside probatio.py, the page's one home: files(card, rows) and answer_lines(card). A run checks
    it is there before its first call, so a run never ends without its page."""
    if str(P.HERE) not in sys.path:
        sys.path.insert(0, str(P.HERE))
    try:
        import versus_page
    except ImportError as e:
        raise SystemExit(PAGE_MISSING.format(P.display(e)))
    return versus_page


def result_card(out, bank, ordo_bank):
    """(card, rows) of a run folder: versus.score's card, which rebuilds the plan from the folder's saved identity on
    bank (score and table take the bank, never a plan), with the probatio that scored it, and versus.table's rows."""
    card = VS.score(out, bank, ordo_bank)
    card["probatio"] = P.PROBATIO_VERSION
    return card, VS.table(out, bank, ordo_bank)


def page_files(card, rows):
    """{file name: bytes} of the page versus_page.files returns, and the results page beside them when the page
    module makes one (html_name: versus_page.results_html, written as versus_page.HTML_FILE), each name a plain file
    name the run itself does not keep (RUN_KEEPS), else one plain line: a page that names a run file or a path would
    overwrite the run. This is the one place the page's files become bytes, and each goes through probatio's own
    writers here (export_bytes)."""
    files = dict(page_call("files", card, rows))
    name = html_name()
    if name:
        files[name] = page_call("results_html", card)
    out = {}
    for name, text in files.items():
        if not isinstance(name, str) or not name or Path(name).name != name or name.startswith(".") or \
                name in RUN_KEEPS or not isinstance(text, str):
            raise SystemExit(BAD_PAGE_FILE.format(name=P.display(name)))
        out[name] = export_bytes(name, text)
    return out


def html_name():
    """The results page's file name when the run's page module makes one (versus_page.HTML_FILE beside its
    results_html), else None: a page from before the results page, or the command tests' stand-in, writes none and
    the run prints no line for it."""
    module = page()
    name = getattr(module, "HTML_FILE", None)
    return name if callable(getattr(module, "results_html", None)) and isinstance(name, str) else None


def page_call(name, *args):
    """versus_page's files or answer_lines on a card, the page's own refusal of a card whose claims do not match its
    estimates (versus_page.ClaimsMismatch, the claims interface) turned into one plain line (CLAIMS_REFUSED), before
    anything is written: the page never prints around such a card, and the command writes no page from it. A page
    without that class (one from before the claims) refuses nothing."""
    module = page()
    mismatch = getattr(module, "ClaimsMismatch", ())
    try:
        return getattr(module, name)(*args)
    except mismatch as e:
        raise SystemExit(CLAIMS_REFUSED.format(reason=P.display(str(e)))) from None


def export_bytes(name, text):
    """A page file's bytes as the run writes them: a .json file as probatio's json_text of its parsed value (the card's
    own form, sorted keys and one space of indent, so an invisible character or a lone surrogate is a JSON escape that
    reads back as itself), every other file through probatio's export_text, the terminal's own filter (every invisible
    or control character escaped but the newline and tab). An ordinary page comes through byte for byte."""
    if name.endswith(".json"):
        return (P.json_text(json.loads(text), sort_keys=True, indent=1) + "\n").encode("utf-8")
    return P.export_text(text).encode("utf-8")


def write_results(out, files, now=None):
    """Write the page's files into the run folder through probatio's safe writer, at this one place: every path
    checked first (check_targets), then all or nothing (replace_results: written into a temporary folder inside the
    run folder, any file they replace kept under previous/<UTC stamp>/, then moved into place). Returns the stamp
    folder or None."""
    now = now or datetime.now(timezone.utc)
    names = sorted(files)
    P.check_targets(out, names)

    def write(folder):
        """Write every page file into folder (replace_results's temporary folder)."""
        for name in names:
            (folder / name).write_bytes(files[name])
    return P.replace_results(out, names, write, now)


def answer_lines(card, out, rescored=False):
    """The lines a run prints when it ends, and a rescore after it, in their one order: versus_page.answer_lines
    (through page_call, so a card whose claims do not match its estimates is the page's one-line refusal); for a
    rescore, its rescored line; the card's trust sentence when it carries one (what the checks on a folder can and
    cannot show, versus.py's one constant); then the folder (display), its line cut as a whole (capped), and the
    results page beside it when the page module writes one (PAGE_LINE, cut the same way). The --watch page shows these
    same lines."""
    lines = list(page_call("answer_lines", card))
    if rescored:
        lines.append(RESCORED_LINE.format(**{k: P.display(v) for k, v in card["rescored"].items()}))
    if card.get("trust"):
        lines.append(P.display(card["trust"]))
    lines.append(P.capped(RESULTS_LINE.format(folder=P.display(out))))
    name = html_name()
    return lines + ([P.capped(PAGE_LINE.format(page=P.display(Path(out) / name)))] if name else [])


def print_answer(card, out, rescored=False):
    """Print answer_lines: what a run prints when it ends, and a rescore after it."""
    print("\n".join(answer_lines(card, out, rescored)))


def execute(a, opts, bank, ordo_bank, plan_, out, record):
    """Make the run's calls into out and write its results. The folder is checked first by reading alone, so a
    refused folder is not written to; then, with --watch, the live page (start_watch: its address printed before any
    call); v4's lock (claim), the check again under it, versus.begin_run (the identity, memory and launch record,
    before the first call), the page's view of the run (watch_view), v4's runner for every call (probatio.run_calls: a
    question with its rendered prompt and no system message (recorded in the bank, not sent: versus.plan), an errand
    episode, a bound row, each under its arm's memory condition; replies, quota and launch records as v4 saves them;
    v4's live line and retries), then the card, the page's files written through the safe writer, the answer lines
    (printed, and handed to the live page), the folder, and with --watch the page held up until Ctrl-C as v4's is
    (probatio.hold_page). A refusal at the end (the page refusing the card's claims, a result file that cannot be
    written) is one plain line, and with --watch the live page shows that line too (refused_on_page). Ctrl-C stops a
    run as v4's does: rerun the same command to resume."""
    state, why = folder_state(out, record)
    if state == "refused":
        raise SystemExit(why)
    page()
    watch = start_watch(plan_) if a.watch else None
    out.mkdir(parents=True, exist_ok=True)
    lock = P.claim(out)
    refusal = None
    try:
        state, why = folder_state(out, record)
        if state == "refused":
            raise SystemExit(why)
        VS.begin_run(out, plan_, record["memory_files"], record["launch"])
        if a.agent == "codex":
            opts.homes = P.codex_homes(out)
        else:
            opts.quota = P.QuotaLog(out / "quota.jsonl")
        line = P.live_line(a, opts)
        view = watch_view(watch, a, bank, plan_, out, record, line) if watch is not None else None
        P.run_calls(plan_.calls, a.agent, opts, out, a.jobs, line=line)
        card, rows = result_card(out, bank, ordo_bank)
        try:
            write_results(out, page_files(card, rows))
            lines = answer_lines(card, out)
        except SystemExit as stop:
            if view is None or not isinstance(stop.code, str):
                raise
            refusal = stop.code                         # shown on the live page below, once the lock is let go
        else:
            if view is not None:
                watch.finish(view, lines)
    except KeyboardInterrupt:
        raise SystemExit("\ninterrupted. Rerun the same command to resume.")
    finally:
        lock.close()
    if refusal is not None:
        refused_on_page(watch, view, refusal)
    print("\n".join(lines))
    if watch is not None:
        P.hold_page(watch)


def refused_on_page(watch, view, refusal):
    """A run with --watch that ends in a refusal (the page's own line for a card whose claims do not match its
    estimates, CLAIMS_REFUSED, or a result file that cannot be written): the line on the live page
    (VersusWatch.refuse), the same line on stderr as probatio prints every refusal (refusal_text), the page held up
    until Ctrl-C as after a scored run (probatio.hold_page), then exit 1. Nothing was written."""
    text = P.refusal_text(refusal)
    watch.refuse(view, text)
    print(text, file=sys.stderr, flush=True)
    P.hold_page(watch)
    raise SystemExit(1)


# ---- --watch: the live page of a versus run (live.VersusWatch) -------------------------------------------------------
def start_watch(plan_):
    """The live page for this run (live.VersusWatch, v4's loopback server and its hardening), named by the names as
    rendered; it prints its address on stderr at once, as v4's Watch does, before any call."""
    return P.import_live().VersusWatch(identity_names(plan_.identity))


def identity_names(identity):
    """A run's names as rendered and joined (rendered_names), read from its identity's participants."""
    return rendered_names({t: {"full": p["rendered"]} for t, p in identity["participants"].items()})


def arm_rosters(identity):
    """{roster: {type: the party as rendered}} for each roster arm of a run: the names, and the unfamiliar roster when
    strangers is on; the live page names each answer by the party it chose (live displays each name where it builds
    the page's data, shown_rosters)."""
    rosters = {"names": {t: p["rendered"] for t, p in identity["participants"].items()}}
    if identity["unfamiliar"]:
        rosters["unfamiliar"] = {t: p["rendered"] for t, p in identity["unfamiliar"].items()}
    return rosters


def page_facts(a, bank, plan_, record):
    """What the live page says of the run (and nothing else of its record): the agent, the CLI's version, the model,
    the effort, the tier, the memory and strangers settings, the names as rendered and joined, and the bank's status
    as the preflight says it (bank_status). live displays every text value where it builds the page's data
    (shown_facts, the rule v4's run_facts follows)."""
    launch = record["launch"]
    return {"agent": P.AGENT_NAMES.get(a.agent, a.agent), "cli": P.import_live().version_of(launch["cli_version"]),
            "model": launch["model"], "effort": launch["effort"], "tier": a.tier, "memory": a.memory,
            "strangers": a.strangers, "names": identity_names(plan_.identity), "bank": bank_status(bank)}


def watch_view(watch, a, bank, plan_, out, record, line):
    """Start the page's view of the run (live.VersusView): its facts (page_facts), each arm by its preflight words
    (arm_words, in the order the preflight prices them), each roster's parties as rendered (arm_rosters), the
    arm of a call (versus.arm_label) and the live line's progress. The view counts what was answered; it scores
    nothing until the run's card is made."""
    labels = sorted({VS.arm_label(c) for c in plan_.calls})
    return watch.start(out, plan_.calls, page_facts(a, bank, plan_, record),
                       [(lab, arm_words(lab)) for lab in labels], arm_rosters(plan_.identity), VS.arm_label, line)


def displayed_refusals(fn):
    """fn with every refusal versus.py raises (VersusError, one line, which may quote a path or a name as it was
    given) shown whole through display() before it reaches the terminal, its line breaks included, so no string from
    outside can begin a line of its own."""
    def run(*args, **kw):
        """fn, its versus refusals displayed whole."""
        try:
            return fn(*args, **kw)
        except VS.VersusError as e:
            raise SystemExit(P.display(str(e), cap=None))
    run.__name__, run.__doc__ = fn.__name__, fn.__doc__
    return run


@displayed_refusals
def main(argv):
    """A versus command line, from probatio.main: the words and the options (parse), the names (versus_names,
    check_names), --jobs and --config-dir as v4 checks them, the banks and the episode engine, the plan (every arm in
    one plan, one folder; a new run plans the tier's @2 recipe, a folder holding an @1 run plans @1 so that it resumes
    as it began: versus.folder_recipe), the agent options as v4 builds them with the effort pinned, the preflight, and
    unless --dry-run the run (execute). --dry-run starts no process v4's dry run does not (the CLI's --version) and,
    with --out, writes every rendered item to rendered.jsonl as v4's does."""
    a = parse(argv)
    names = versus_names(a.versus)
    if a.jobs < 1:
        raise SystemExit("--jobs must be at least 1.")
    P.check_config_dir(a)
    check_folders(a)
    bank = load_bank(a)
    check_world()
    ordo_bank = ordo.load_ordo_bank()
    parties = check_names(bank, names, a.strangers_on)
    out = run_folder(a, names)
    plan_ = VS.plan(bank, names, a.tier, arms_of(a), ordo_bank,
                    recipe=VS.folder_recipe(out, a.tier))   # a folder's own recipe: @1 resumes as @1
    opts = P.agent_options(a)
    opts.effort = a.effort              # pinned for both CLIs, never inherited from the user's settings
    opts.auto_memory = "as-configured"
    _, _, record, _ = preflight(a, opts, bank, ordo_bank, names, parties, plan_, out)
    if a.dry_run:
        if a.out:
            P.write_rendered(out, plan_.calls)
        return
    execute(a, opts, bank, ordo_bank, plan_, out, record)


# ---- --rescore of a versus folder ------------------------------------------------------------------------------------
def is_versus_folder(folder):
    """Whether a folder holds a versus run's saved identity (versus-run.json), as probatio's --rescore asks."""
    return (Path(folder) / VS.RUN_FILE).is_file()


def saved_record(out):
    """The folder's versus-run.json, or one plain line: not a folder, no record, a record that cannot be read or is
    not a versus run record."""
    path = out / VS.RUN_FILE
    if not out.is_dir() or not path.is_file():
        raise SystemExit(f"{P.display(out)} is not a versus run folder: it holds no {VS.RUN_FILE}.")
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except OSError as e:
        raise SystemExit(f"{P.display(path)} cannot be read ({P.display(e.strerror or e)}), so --rescore cannot "
                         "rebuild the run.")
    except (ValueError, UnicodeDecodeError):
        record = None
    if not isinstance(record, dict) or record.get("format") != VS.RUN_FORMAT or \
            not isinstance(record.get("identity"), dict):
        raise SystemExit(f"{P.display(out)} is not a versus run folder: its {VS.RUN_FILE} is not a versus run record.")
    return record


def rescored_stamp(out, now):
    """card["rescored"]: when (UTC), by which probatio and which versus scorer, and which probatio scored the run
    before (the replaced scorecard.json's own rescorer or probatio, when it records one)."""
    try:
        old = json.loads((out / "scorecard.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        old = {}
    old = old if isinstance(old, dict) else {}
    before = (old.get("rescored") or {}).get("by") if isinstance(old.get("rescored"), dict) else None
    return {"at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "by": P.PROBATIO_VERSION, "scorer": VS.SCORER_VERSION,
            "scored_by": before or old.get("probatio") or "unknown"}


def rescore(folder, bank=None, ordo_bank=None, now=None):
    """--rescore FOLDER on a versus folder: score ONE coherent snapshot of it again with this probatio, starting no
    process (no agent, no CLI probe). First, reading only, so each refusal is one plain line and writes nothing: the
    folder's record (saved_record); the plan rebuilt from its identity on this probatio's bank (versus.replan: a
    folder whose bank pin is not this bank's is refused with both pins named); every planned call answered and no
    replies line damaged (probatio.check_finished, by line number); the folder, run.lock and previous/ of their own
    kind and no link (probatio.check_targets). Then the run's own lock (refusing while another probatio, a run, a
    rescore or versus.write_codes, holds it), and under it everything that is scored is read and scored
    (scored_snapshot), the page's files replaced all or nothing (write_results: the old ones under previous/<stamp>/),
    and the lock let go: no record, reply, codebook or episode record can change between the reading and the
    writing. Returns (card, out). bank and ordo_bank default to the pinned ones; tests pass others."""
    out, now = Path(folder).resolve(), now or datetime.now(timezone.utc)
    record = saved_record(out)
    bank = bank or bank_at(VS.BANK, VS.VERSUS_MANIFEST_SHA256)
    ordo_bank = ordo_bank or ordo.load_ordo_bank()
    plan_ = VS.replan(record["identity"], bank, ordo_bank)
    P.check_finished(out, plan_.calls)
    P.check_targets(out, [])                        # run.lock and previous/ checked before the lock opens run.lock
    lock = P.lock_folder(out, P.RESCORE_BUSY.format(P.display(out)))
    try:
        card, files = scored_snapshot(out, bank, ordo_bank, now)
        write_results(out, files, now)
    finally:
        lock.close()
    return card, out


def scored_snapshot(out, bank, ordo_bank, now):
    """Under the run's lock, what a rescore writes, from one reading of the folder: the record read and its plan
    rebuilt again, the replies checked again (check_finished), the card scored (result_card: versus.score and
    versus.table read the record, every replies line, the codebook and the episode records), card["rescored"], the
    page's files (page_files) and every path they will be written to checked (probatio.check_targets). Returns
    (card, files)."""
    record = saved_record(out)
    plan_ = VS.replan(record["identity"], bank, ordo_bank)
    P.check_finished(out, plan_.calls)
    card, rows = result_card(out, bank, ordo_bank)
    card["rescored"] = rescored_stamp(out, now)
    files = page_files(card, rows)
    P.check_targets(out, sorted(files))
    return card, files


@displayed_refusals
def rescore_main(folder):
    """--rescore FOLDER from the command line, on a versus folder (probatio.main_rescore hands it here): rescore it and
    print the answer lines, the rescored line, the trust sentence when the card has one, and the folder. A file
    the folder does not let it read is one plain line too."""
    try:
        card, out = rescore(folder)
    except OSError as e:
        raise SystemExit(f"{P.display(e.filename or folder)}: {P.display(e.strerror or e)}; --rescore wrote nothing.")
    print_answer(card, out, rescored=True)
