#!/usr/bin/env python3
"""Check versus's source as one bank: the checks that hold across writers, which no single writer's branch could run.

It reads bank/versus/src only (common.json, families/*.json, triads.json, ties.json, reviews/*.json), through
tools/versus_render.py's own loader, and reports two kinds of check (docs/versus/DESIGN.md 5.2 and the point-8 ruling):

FAIL checks (any failure makes the exit status 1)
  a  ids         every id unique across all lists; one review record per scenario; no record without a scenario,
                 except, by name, the fixed text's record reviews/fixed-text.json (NOT_A_SCENARIO)
  b  fields      every record carries a top-level "setting" and "object" (a tie's too, ruling R5), and every
                 allocation, conflict and triad record a "loss_kind" of LOSS_KINDS; a "near", where present, is a
                 list of {"id", "why"}
  c  unique      no two scenarios share a normalised setting, and no two share a normalised object (norm_label)
  d  not ordo    no setting or object is, or contains, one of ordo's six (ORDO_SIX, the ruling's rule 3)
  e  near        every near id is a scenario of the source, and near is symmetric
  f  similarity  no pair of scenarios, in any two lists or families, has scenario-specific text more alike than
                 SIMILARITY_FAIL unless each names the other in near (similarity_tokens says what is removed); a
                 pair of two ties, identical by construction, never fails (ruling R5) and is listed under l

REPORT checks (listed, never failing)
  g  overlaps    pairs whose settings, or whose objects, share a head noun or half their content words, and that do
                 not name each other in near; and any setting or object that shares half its words with ordo's six
  h  top pairs   the TOP_PAIRS most alike pairs under f, whatever their score
  i  closings    each allocation and conflict scenario's closing sentence about other people, and which of ruling
                 R1's three forms it is, or "none"
  j  indefinites every anyone, anybody, someone, somebody, everyone, everybody outside that closing sentence
  k  counts      scenarios per family and list, triads, ties
  l  similar     every pair above SIMILARITY_FAIL that f lets pass, with its score and why: two ties (ruling R5),
                 or a pair whose members name each other in near (ruling R6: flagged at the merge)

    python3 tools/versus_check_src.py                  a summary to stdout; exit 1 when a FAIL check fails
    python3 tools/versus_check_src.py --json FILE      also the full report, every list in full, as JSON
    python3 tools/versus_check_src.py --src DIR        another source folder (the tests' planted sources)

Python 3.9, standard library only; no network, no model.
"""
import argparse
import difflib
import json
import re
import sys
from itertools import combinations
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools"))
import versus_render as V  # noqa: E402  (the source's one loader)

# ---- thresholds, word lists and forms: their one home ----------------------------------------------------------------
SIMILARITY_FAIL = 0.5          # f: a pair scoring above this fails unless each names the other in near
SIMILARITY_DIGITS = 2          # f: the score is rounded as the third writer's scan rounded it, so 0.5 means what it did
TOP_PAIRS = 20                 # h
HALF_WORDS = 0.5               # g: shared content words as a share of the shorter label's
MIN_SHARED_WORDS = 2           # g: and at least this many
LOSS_KINDS = ("sum", "time", "quality", "forgone")       # b: the addendum's four values
LOSS_KIND_LISTS = ("allocation", "conflict", "triad")    # b: the lists whose records carry a loss_kind
POINT8_FIELDS = ("setting", "object")                    # b: every record's, a tie's included (ruling R5)
SIMILARITY_LISTED_ONLY = (("tie", "tie"),)               # f: pairs listed, never failed (ruling R5)
# d: ordo's six settings and objects, as the ruling's rule 3 names them
ORDO_SIX = ("an analysis server's job queue", "a workshop travel fund", "a meeting room's free hour",
            "compute credits", "the order of names in a report's acknowledgements", "a public defence")
ARTICLES = ("a", "an", "the")
# g: words that carry no content for an overlap; "independent" is common by design (point 7's neutral operator)
FUNCTION_WORDS = frozenset(ARTICLES + (
    "of", "for", "to", "in", "on", "at", "by", "with", "from", "and", "or", "one", "two", "its", "this", "that",
    "next", "last", "first", "only", "free", "same", "each", "every", "own", "per", "as", "into", "over", "under",
    "independent", "who", "which", "where", "when", "between", "after", "before", "during"))
# g: a head noun is the last word before the first of these (or a comma)
HEAD_BREAKS = frozenset((
    "of", "for", "to", "in", "on", "at", "by", "with", "from", "where", "that", "which", "who", "whose", "when",
    "between", "during", "after", "before", "under", "over", "through", "into", "booked", "booking", "hiring",
    "letting", "running", "making", "taking", "giving", "paying", "printing", "selling", "renting", "serving",
    "holding", "issuing", "offering", "lending", "testing"))
INDEFINITES = ("anyone", "anybody", "someone", "somebody", "everyone", "everybody")      # j
NOT_A_SCENARIO = (V.FIXED_TEXT_ID,)   # a: the one record that stands for no scenario: the fixed text's
SLOT = re.compile(r"⟦[^⟧]+⟧")
WORD = re.compile(r"[a-z0-9$:']+")
# f: the phrases the third writer's scan removed (point 7's operator sentences, the equal urgency, "no one else is
# affected", the prior claim), as it wrote them ...
WRITER_C_REMOVED = (
    r"Neither party owns, operates or regulates the [^.]*\.",
    r"None of the three parties owns, operates or regulates the [^.,]*",
    r"[\w ']+, not (either party|any of the three parties), gave you that authority\.",
    r"Neither party has any authority over you or over the [^.]*\.",
    r"[^.,;]*people, organizations (and|or) governments on the same terms",
    r"no one else is affected( by the choice)?",
    r"the (two|three) [\w-]+ are equally urgent",
    r"(neither party|none of the three parties) has a prior claim[^.;,]*",
)
# ... and the contract-forced phrases the rulings added after that scan: R1's other two closing forms, R2's breach
# clause and the form R2 keeps. Every pattern is applied without regard to case.
RULING_REMOVED = (
    r"no [a-z]+ or third party gains or loses by the choice",
    r"no one beyond the two parties and [^.]*? gains or loses by the choice",
    r"breaches no law or rule and no right of either party",
    r"breaches no contract or law",
)
# f: the fields whose text is specific to a scenario, as the third writer's scan read them (a tie, which that scan did
# not read, by its body like an allocation item); the role sentences are never in them, so no party sentence is read
SIMILARITY_TEXT = {
    "allocation": lambda it: it["body"],
    "tie": lambda it: it["body"],
    "conflict": lambda it: " ".join([it["setting"], it["lead"], it["item"], it["request"], it["closing"],
                                     it["option"]]),
    "triad": lambda it: " ".join([it["setting"], it["lead"], it["closing"], it["option_by_type"]["individual"]]),
}
SIMILARITY_TEXT_READ = {  # the same, in words, for the report
    "allocation": "body", "tie": "body", "conflict": "setting, lead, item, request, closing, option",
    "triad": "setting, lead, closing, the individual's option"}
# i, j: the text a model reads of each scenario, slots kept
READ_TEXT = {
    "allocation": lambda it: " ".join([it["body"]] + list(it["options"])),
    "tie": lambda it: " ".join([it["body"]] + list(it["options"])),
    "conflict": lambda it: " ".join([it["setting"], it["lead"], it["item"], it["request"], it["closing"], it["option"]]
                                    + [w[k] for w in it["wordings"] for k in ("a", "the")]),
    "triad": lambda it: " ".join([it["setting"], it["lead"], it["closing"]] + list(it["jobs"].values())
                                 + list(it["option_by_type"].values())),
}
CLOSING_TEXT = {  # i: where the closing sentence about other people stands
    "allocation": lambda it: it["body"],
    "conflict": lambda it: " ".join([it["setting"], it["lead"], it["item"], it["request"], it["closing"]]),
}
ABOUT_OTHERS = re.compile(r"\b(no one else|no one beyond|third part(y|ies)|nobody else|anyone else)\b", re.I)
R1_FORMS = (  # i: (form, pattern of the whole sentence, pattern of the clause)
    ("i", re.compile(r"^No one else is affected\.$"), re.compile(r"\bno one else is affected\b", re.I)),
    ("ii", re.compile(r"^No [a-z]+ or third party gains or loses by the choice\.$"),
     re.compile(r"\bno [a-z]+ or third party gains or loses by the choice\b", re.I)),
    ("iii", re.compile(r"^No one beyond the two parties and .+ gains or loses by the choice\.$"),
     re.compile(r"\bno one beyond the two parties and .+? gains or loses by the choice\b", re.I)),
)


# ---- the source ------------------------------------------------------------------------------------------------------
def load(src):
    """The source as {"source": versus_render's lists, "scenarios": [(list, item)], "records": {stem: record},
    "record_errors": {stem: reason}}: every review record file read, an unreadable one kept as an error."""
    src = Path(src)
    source = V.load_source(src)
    records, errors = {}, {}
    for p in sorted((src / "reviews").glob("*.json")):
        try:
            records[p.stem] = json.loads(p.read_text(encoding="utf-8"))
        except ValueError as exc:
            errors[p.stem] = f"not JSON: {exc}"
    return {"source": source, "scenarios": V.scenarios(source), "records": records, "record_errors": errors}


def norm_label(text):
    """A setting or object as two are compared (c, d): lowercase, the articles and possessives dropped ("the fair's
    last stand" reads "fair last stand"), runs of spaces collapsed, outer punctuation stripped."""
    t = text.lower().replace("’", "'")
    t = re.sub(r"'s\b", "", t)
    t = re.sub(r"(?<=s)'(?=[\s,;.]|$)", "", t)
    words = [w for w in t.split() if w not in ARTICLES]
    return " ".join(words).strip(" .,;:")


def near_ids(record):
    """The ids a record names in near, whatever the shape of its entries (a malformed entry names nothing)."""
    out = []
    for e in (record or {}).get("near") or []:
        if isinstance(e, dict) and isinstance(e.get("id"), str):
            out.append(e["id"])
    return out


def mutual_near(records, a, b):
    """Whether each of two scenarios names the other in near."""
    return b in near_ids(records.get(a)) and a in near_ids(records.get(b))


# ---- FAIL checks -----------------------------------------------------------------------------------------------------
def check_ids(data):
    """a: every id unique across all lists, and none of them NOT_A_SCENARIO's; one record per scenario, and that
    record's own id is its file's name; no record without a scenario except the NOT_A_SCENARIO records."""
    problems, seen = [], {}
    for lst, item in data["scenarios"]:
        seen.setdefault(item["id"], []).append(lst)
    problems += [f"id {i!r} is used {len(v)} times ({', '.join(v)})" for i, v in seen.items() if len(v) > 1]
    problems += [f"id {i!r} is reserved for a record that is not a scenario's" for i in seen if i in NOT_A_SCENARIO]
    stems = set(data["records"]) | set(data["record_errors"])
    problems += [f"{i}: no review record" for i in seen if i not in stems]
    problems += [f"reviews/{s}.json: no scenario of that id" for s in sorted(stems - set(seen) - set(NOT_A_SCENARIO))]
    problems += [f"reviews/{s}.json: {why}" for s, why in data["record_errors"].items()]
    problems += [f"reviews/{s}.json: its id is {r.get('id')!r}" for s, r in data["records"].items()
                 if r.get("id") != s]
    return problems


def check_point8_fields(data):
    """b: a setting and an object in every record (ruling R5: a tie's too), a loss_kind in every allocation,
    conflict and triad record (a tie, where no party loses, needs none), and a well-formed near wherever a record
    has one."""
    problems = []
    for lst, item in data["scenarios"]:
        rec = data["records"].get(item["id"])
        if rec is None:
            continue
        for f in POINT8_FIELDS:
            if not isinstance(rec.get(f), str) or not rec[f].strip():
                problems.append(f"{item['id']}: the record has no {f}")
        if lst in LOSS_KIND_LISTS:
            if rec.get("loss_kind") not in LOSS_KINDS:
                problems.append(f"{item['id']}: loss_kind {rec.get('loss_kind')!r} is not one of "
                                f"{', '.join(LOSS_KINDS)}")
        if "near" in rec:
            near = rec["near"]
            ok = isinstance(near, list) and all(
                isinstance(e, dict) and set(e) == {"id", "why"} and isinstance(e["id"], str) and e["id"].strip()
                and isinstance(e["why"], str) and e["why"].strip() for e in near)
            if not ok:
                problems.append(f"{item['id']}: near is not a list of {{\"id\", \"why\"}}")
    return problems


def labels(data, field):
    """{id: the record's value of field} for every scenario whose record has it as a non-empty string."""
    out = {}
    for _, item in data["scenarios"]:
        v = (data["records"].get(item["id"]) or {}).get(field)
        if isinstance(v, str) and v.strip():
            out[item["id"]] = v
    return out


def check_unique_labels(data):
    """c: no normalised setting shared by two scenarios, and no normalised object."""
    problems = []
    for field in POINT8_FIELDS:
        groups = {}
        for sid, v in labels(data, field).items():
            groups.setdefault(norm_label(v), []).append(sid)
        problems += [f"{field} {k!r} is shared by {', '.join(ids)}" for k, ids in sorted(groups.items())
                     if len(ids) > 1]
    return problems


def check_not_ordo(data):
    """d: no setting or object is, or contains, one of ordo's six (both normalised)."""
    problems, ordo = [], [(o, norm_label(o)) for o in ORDO_SIX]
    for field in POINT8_FIELDS:
        for sid, v in labels(data, field).items():
            n = norm_label(v)
            problems += [f"{sid}: its {field} {v!r} is ordo's {o!r}" for o, on in ordo
                         if re.search(r"\b" + re.escape(on) + r"\b", n)]
    return problems


def check_near(data):
    """e: every near id is a scenario of the source, none names itself, and every entry has its reverse."""
    ids = {item["id"] for _, item in data["scenarios"]}
    problems = []
    for _, item in data["scenarios"]:
        sid = item["id"]
        for nid in near_ids(data["records"].get(sid)):
            if nid == sid:
                problems.append(f"{sid}: near names itself")
            elif nid not in ids:
                problems.append(f"{sid}: near names {nid!r}, which is not a scenario of the source")
            elif sid not in near_ids(data["records"].get(nid)):
                problems.append(f"{sid}: near names {nid}, whose near does not name {sid}")
    return problems


def similarity_tokens(lst, item, removed=WRITER_C_REMOVED + RULING_REMOVED):
    """f: a scenario's specific text as words: its SIMILARITY_TEXT fields, every slot replaced by "P", every phrase of
    removed deleted (without regard to case), lowercased and split into WORD tokens, as the third writer's scan did."""
    t = SLOT.sub("P", SIMILARITY_TEXT[lst](item))
    for pattern in removed:
        t = re.sub(pattern, "", t, flags=re.I)
    return WORD.findall(t.lower())


def pair_scores(data, removed=WRITER_C_REMOVED + RULING_REMOVED):
    """f, h: every pair of scenarios with its score, difflib's word-sequence ratio (autojunk off) rounded to
    SIMILARITY_DIGITS, highest first, ties by id."""
    toks = [(item["id"], lst, similarity_tokens(lst, item, removed)) for lst, item in data["scenarios"]]
    rows = []
    for (a, la, ta), (b, lb, tb) in combinations(toks, 2):
        score = round(difflib.SequenceMatcher(None, ta, tb, autojunk=False).ratio(), SIMILARITY_DIGITS)
        rows.append({"a": a, "b": b, "lists": [la, lb], "score": score})
    rows.sort(key=lambda r: (-r["score"], r["a"], r["b"]))
    return rows


def check_similarity(data, rows):
    """f: the pairs above SIMILARITY_FAIL whose members do not each name the other in near, except the pairs of
    SIMILARITY_LISTED_ONLY lists (listed_similar gives those)."""
    return [f"{r['a']} and {r['b']} ({r['lists'][0]}, {r['lists'][1]}): {r['score']:.2f}" for r in rows
            if r["score"] > SIMILARITY_FAIL and tuple(r["lists"]) not in SIMILARITY_LISTED_ONLY
            and not mutual_near(data["records"], r["a"], r["b"])]


def report_similar(data, rows):
    """l: every pair above SIMILARITY_FAIL that check f lets pass, with its score and the reason it passes: two
    ties (ruling R5), or members that name each other in near (ruling R6), with the near entry's why."""
    out = []
    for r in rows:
        if r["score"] <= SIMILARITY_FAIL:
            continue
        if tuple(r["lists"]) in SIMILARITY_LISTED_ONLY:
            out.append(dict(r, passes="two tie controls, identical by construction (ruling R5)"))
        elif mutual_near(data["records"], r["a"], r["b"]):
            why = next((e.get("why") for e in data["records"][r["a"]].get("near") or [] if e.get("id") == r["b"]), "")
            out.append(dict(r, passes=f"each names the other in near: {why}"))
    return out


# ---- REPORT checks ---------------------------------------------------------------------------------------------------
def head_noun(label):
    """g: the last word before the first comma or HEAD_BREAKS word of a normalised label."""
    words = []
    for w in norm_label(label).split():
        if w.endswith(","):
            words.append(w.rstrip(","))
            break
        if w in HEAD_BREAKS and words:
            break
        words.append(w)
    return words[-1].strip(".,;:") if words else ""


def content_words(label):
    """g: a label's words without FUNCTION_WORDS or punctuation."""
    return {w for w in WORD.findall(norm_label(label)) if w not in FUNCTION_WORDS}


def report_overlaps(data):
    """g: pairs whose settings (or objects) share a head noun or at least HALF_WORDS of the shorter label's content
    words (and MIN_SHARED_WORDS), not naming each other in near; and labels sharing half their words with ordo's six."""
    pairs, ordo = [], []
    for field in POINT8_FIELDS:
        lab = labels(data, field)
        for a, b in combinations(sorted(lab), 2):
            if a in near_ids(data["records"].get(b)) or b in near_ids(data["records"].get(a)):
                continue
            ha, hb = head_noun(lab[a]), head_noun(lab[b])
            ca, cb = content_words(lab[a]), content_words(lab[b])
            shared = sorted(ca & cb)
            half = len(shared) >= MIN_SHARED_WORDS and len(shared) >= HALF_WORDS * min(len(ca), len(cb))
            if (ha and ha == hb) or half:
                pairs.append({"field": field, "a": a, "b": b, "head_noun": ha if ha == hb else None,
                              "shared_words": shared, "a_label": lab[a], "b_label": lab[b]})
        for sid, v in sorted(lab.items()):
            cv = content_words(v)
            for o in ORDO_SIX:
                co = content_words(o)
                shared = sorted(cv & co)
                if len(shared) >= MIN_SHARED_WORDS and len(shared) >= HALF_WORDS * min(len(cv), len(co)):
                    ordo.append({"field": field, "id": sid, "label": v, "ordo": o, "shared_words": shared})
    return {"pairs": pairs, "ordo": ordo}


def report_top_pairs(rows):
    """h: the TOP_PAIRS highest-scoring pairs under f."""
    return rows[:TOP_PAIRS]


def sentences(text):
    """A text's sentences, split after a full stop, question or exclamation mark followed by a space."""
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


def classify_closing(sentence):
    """i: which of R1's forms a sentence is: "i", "ii" or "iii" when it is that whole sentence; "i (in a longer
    sentence)" and so on when the form stands inside another sentence; "none" otherwise."""
    for form, whole, clause in R1_FORMS:
        if whole.match(sentence):
            return form
    for form, whole, clause in R1_FORMS:
        if clause.search(sentence):
            return f"{form} (in a longer sentence)"
    return "none"


def closing_sentence(lst, item):
    """i: the last sentence of an allocation or conflict scenario that speaks of people beyond the two parties, or
    None."""
    found = [s for s in sentences(CLOSING_TEXT[lst](item)) if ABOUT_OTHERS.search(s)]
    return found[-1] if found else None


def report_closings(data):
    """i: each allocation and conflict scenario's closing sentence about other people and its R1 form."""
    out = []
    for lst, item in data["scenarios"]:
        if lst in CLOSING_TEXT:
            s = closing_sentence(lst, item)
            out.append({"id": item["id"], "list": lst, "family": item["family"], "sentence": s,
                        "form": classify_closing(s) if s else "none"})
    return out


def report_indefinites(data):
    """j: every INDEFINITES word in the text a model reads of a scenario, outside its closing sentence (i)."""
    rx = re.compile(r"\b(" + "|".join(INDEFINITES) + r")\b", re.I)
    out = []
    for lst, item in data["scenarios"]:
        text = READ_TEXT[lst](item)
        closing = closing_sentence(lst, item) if lst in CLOSING_TEXT else None
        if closing:
            text = text.replace(closing, " ")
        for m in rx.finditer(text):
            out.append({"id": item["id"], "list": lst, "word": m.group().lower(),
                        "context": text[max(0, m.start() - 60):m.end() + 60]})
    return out


def report_counts(data):
    """k: scenarios per family and list, the triads (and their families), the ties."""
    src, out = data["source"], {"families": {}}
    for fam in src["common"]["families"]:
        out["families"][fam] = {lst: sum(1 for i in src[lst] if i["family"] == fam)
                                for lst in ("allocation", "conflict")}
    out["allocation"], out["conflict"] = len(src["allocation"]), len(src["conflict"])
    out["triads"], out["ties"] = len(src["triad"]), len(src["tie"])
    out["triads_by_family"] = {f: sum(1 for i in src["triad"] if i["family"] == f) for f in src["common"]["families"]}
    return out


# ---- the run ---------------------------------------------------------------------------------------------------------
FAIL_CHECKS = (("a", "ids", check_ids), ("b", "fields", check_point8_fields), ("c", "unique", check_unique_labels),
               ("d", "not ordo", check_not_ordo), ("e", "near", check_near))


def run(src):
    """The whole report: each FAIL check's problems and result, each REPORT list, the measure f used, and "ok"."""
    data = load(src)
    rows = pair_scores(data)
    fails = {}
    for key, name, fn in FAIL_CHECKS:
        fails[key] = {"name": name, "problems": fn(data)}
    fails["f"] = {"name": "similarity", "problems": check_similarity(data, rows)}
    for v in fails.values():
        v["result"] = "pass" if not v["problems"] else "FAIL"
    writer_c_only = pair_scores(data, WRITER_C_REMOVED)
    reports = {
        "g": {"name": "overlaps", **report_overlaps(data)},
        "h": {"name": "top pairs", "pairs": report_top_pairs(rows)},
        "i": {"name": "closings", "scenarios": report_closings(data)},
        "j": {"name": "indefinites", "occurrences": report_indefinites(data)},
        "k": {"name": "counts", **report_counts(data)},
        "l": {"name": "similar", "pairs": report_similar(data, rows)},
    }
    measure = {
        "score": "difflib.SequenceMatcher(autojunk=False).ratio() on word tokens, rounded to "
                 f"{SIMILARITY_DIGITS} places",
        "fail_above": SIMILARITY_FAIL, "text_fields": SIMILARITY_TEXT_READ,
        "removed_writer_c": list(WRITER_C_REMOVED), "removed_rulings": list(RULING_REMOVED),
        "slots": "every slot replaced by the word P", "party_sentences": "the role sentences are fixed text, never "
        "in a scenario's fields, so no party sentence is read",
        "under_writer_c_removals_alone": {
            "over_threshold": sum(1 for r in writer_c_only if r["score"] > SIMILARITY_FAIL),
            "over_threshold_failing": len(check_similarity(data, writer_c_only)),
            "max": writer_c_only[0]["score"] if writer_c_only else None},
    }
    return {"src": "bank/versus/src" if Path(src).resolve() == V.SRC.resolve() else str(src),
            "fail_checks": fails, "report_checks": reports, "similarity_measure": measure,
            "ok": all(v["result"] == "pass" for v in fails.values())}


def summary(report):
    """The report as a few readable lines: each FAIL check with its problems, each REPORT list's size."""
    lines = []
    for key, v in report["fail_checks"].items():
        lines.append(f"{key} {v['name']:10s} {v['result']}" + (f" ({len(v['problems'])})" if v["problems"] else ""))
        lines += [f"    {p}" for p in v["problems"]]
    r = report["report_checks"]
    forms = {}
    for s in r["i"]["scenarios"]:
        forms[s["form"]] = forms.get(s["form"], 0) + 1
    lines.append(f"g overlaps   {len(r['g']['pairs'])} pairs; {len(r['g']['ordo'])} labels near ordo's six")
    top = ", ".join("{}/{} {:.2f}".format(p["a"], p["b"], p["score"]) for p in r["h"]["pairs"][:3])
    lines.append(f"h top pairs  {top} ...")
    lines.append("i closings   " + ", ".join(f"{k}: {n}" for k, n in sorted(forms.items())))
    lines.append(f"j indefinites {len(r['j']['occurrences'])}")
    k = r["k"]
    lines.append(f"k counts     allocation {k['allocation']}, conflict {k['conflict']}, triads {k['triads']}, "
                 f"ties {k['ties']}")
    lines.append(f"l similar    {len(r['l']['pairs'])} pairs above {SIMILARITY_FAIL} that f lets pass:")
    lines += ["    {} and {} {:.2f}: {}".format(p["a"], p["b"], p["score"], p["passes"]) for p in r["l"]["pairs"]]
    lines.append("all FAIL checks pass" if report["ok"] else "a FAIL check fails")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--src", default=str(V.SRC), help="the source folder (default bank/versus/src)")
    ap.add_argument("--json", metavar="FILE", help="write the full report here as JSON")
    args = ap.parse_args(argv)
    report = run(args.src)
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(summary(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
