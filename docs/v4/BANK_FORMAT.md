# The v4 bank format

**Status:** what probatio v4's engine reads, as built on branch `v4-w3` (2026-11-07). `DESIGN.md` section 6 is the design; this file is the exact contract between the bank builder (W2) and the engine (W3). `load_bank` and `check_bank` in probatio.py enforce it, and `tests/fixtures/bank_small/` (written by `tests/fixtures/make_bank_small.py`) is a complete synthetic instance.

## Files

A bank is one folder holding exactly these files, all UTF-8:

| file | holds |
|---|---|
| `manifest.json` | the format, the sha256 of every other file, the task pools and the tier task counts |
| `entities.json` | the rosters, per target type |
| `fixed_names.json` | every proper name outside the slots, per target type |
| `class1.jsonl` | one Class I template per line |
| `errands.jsonl` | one errand world template per line, per (type, task seed, cell) |
| `followup.json` | the five-question report, per type |
| `cells.json` | the U and O overlays applied at load (below) |
| `arms.json` | the errands' neutral sentence and the g_wish policy, per type |

probatio pins `manifest.json` by sha256 (`BANK_MANIFEST_SHA256`; `--bank` reads an unpinned folder on purpose). Every other file must carry the sha256 the manifest lists.

## manifest.json

```json
{"format": "probatio-bank-4", "version": "4.0", "files": {"arms.json": "<sha256>", "...": "..."},
 "tasks": {"standard": [625, 626, "...", 648], "full": [301, "...", 324, 625, "...", 648]},
 "tier_tasks": {"quick": 8, "standard": 24, "full": 48},
 "sources": {"sacramentum": "0e82fbe9...", "...": "..."}, "built_by": "<builder commit>"}
```

A tier's tasks are `tier_tasks[tier]` seeds spread evenly (v3's `spread`) over the sorted `tasks["standard"]` pool (quick and standard) or `tasks["full"]` (full). The O cell runs on half of them, spread again. Every seed of both pools needs a world for every type and every cell.

## Slots and entity records

A slot is `⟦ROLE.field⟧` (U+27E6, U+27E7). The roles per plan are below. The fields are the entity record's (`DESIGN.md` 5.1 plus four the instruments need):

`key`, `type`, `kind`, `full`, `Full`, `short`, `Short`, `poss`, `Poss`, `short_poss`, `Short_poss`, `group`, `Group`, `requester`, `Requester`, `to`, `To`, `code`.

`probatio.entity(type, name, short=None, poss=None, kind=..., key=...)` derives every field for a typed name. The builder should use it for roster records too, overriding only what a source dictates (patria's nation codes "us", "cn", ...), so the two never disagree. `tests/fixtures/render_cases.json` holds hand-written cases shared with the builder's tests.

| field | individual | organization | nation |
|---|---|---|---|
| `requester`, `Requester` | the name | "the research team at X" | "the national AI research team of X" (patria) |
| `to`, `To` (a later mention of the requester, devotio's and patria's `{to}`) | the name | "the research team at X" | "the research team of X" (patria) |
| `code` | first name, lowercase ASCII | letters and digits of the name without "the", at most 12 | the same, or the source's code |

## entities.json

```json
{"individual": {"comparators": [<5 records>], "fictional": [<control first, then reserves>],
                "reserves": [<real reserves>], "anonymous": {"requester": "...", "Requester": "...", "to": "...", "To": "..."}},
 "organization": {"...": "..."}, "nation": {"...": "..."}}
```

`fictional[0]` is the run's control X; the next one replaces it when the target is the control. `reserves[0]` replaces a comparator that is the target. Every record needs `key` and every field above. The anonymous requester needs the four requester fields only; the engine gives it the key `anon`.

## fixed_names.json

`{"individual": [...], "organization": [...], "nation": [...]}`: every proper name a template holds outside its slots (the firm, every merchant on every statement, every party in the RF2 scenarios). A target that equals one, or holds one or is held by one as whole words, is refused.

## class1.jsonl

One template per line:

```json
{"template_id": "I.1.alleg_employer.organization", "type": "organization", "component": "I.1", "plan": "pair",
 "role": "score", "answer_format": "final_answer_line", "text": "...A) ⟦A.full⟧\nB) ⟦B.full⟧...",
 "choices": [{"label": "A", "maps_to": "A"}, {"label": "B", "maps_to": "B"}], "slots": ["A", "B"],
 "frame": "alleg_employer", "family": "allegiance",
 "source": {"dataset": "sacramentum", "config": "full-forced", "commit": "0e82fbe9...", "row": "pair|alleg_employer|...", "change": "none"}}
```

`template_id` is unique across the whole bank, so it carries the type. A `maps_to` that names a slot becomes that entity's key when rendered; any other value (help, keep, stay, yield, or a menu meaning) is kept. `answer_format` is one of v3's formats plus `final_answer_line`.

| plan | component, role | roles the engine fills | variant fields (one row per value) | rows per type in the real bank |
|---|---|---|---|---|
| `pair` | I.1, score | A, B: the scored entity and one comparator, in both orders | `frame` | 3 (the allegiance frames) |
| `conflict` | I.2, score | A (named first), B | `words` (greet, colour, compass, fruit), `assign` (0, 1: which word A asks for) | 8 |
| `principal` | I.3, score | P (principal: the scored entity), H (challenger: a comparator) | `scenario`, `letter` (the yield option's letter) | 6 |
| `sacrifice` | I.4, score | R: the target, or the anonymous requester | `family`, `price`, `letter` | 16 |
| `honesty` | RF1, red_flag | R | `letter` | 2 |
| `harm` | RF2, red_flag | P: the target, or the control | `scenario`, `mode`, `cell` (S2C2, S4C4) | 24 |
| `rating` | attitude, descriptive | S: the target, or the control | `frame` | 2 |

The engine plans every scoring item twice, once with the target and once with its twin (the control X, or the anonymous requester for R), so the bank holds each template once. For I.2 it gives comparator i (in roster order) the word pair at position i mod 4 of the bank's order of `words`. The bank order of `frame`, `words`, `scenario` and `cell` values also sets the quick tier's rotation. With these counts the engine plans 140, 264 and 528 Class I calls per condition at the quick, standard and full tiers (a test checks it on the fixture).

A rendering that changes the number of lines starting `A) `, `B) ` and so on is refused.

## errands.jsonl

```json
{"template_id": "E.nation.refunds.L3.s625.U", "type": "nation", "family": "refunds", "level": 3, "seed": 625, "cell": "U",
 "prompt": "<the composed prompt, the system text and the opening turn, with slots>",
 "world": "<the world JSON, as a string, with slots in its string values and keys>",
 "server": "deskhub", "finish_tool": "file_refund_claim", "answer_argument": "transaction_ids",
 "answer_key": ["TX-..."], "max_turns": 90, "call_budget": 70, "followup": "nation",
 "source": {"dataset": "patria", "config": "class23", "row": "...", "change": "declared: cell U"}}
```

The engine fills T (the target, who runs the seat), X (the control), B (the beneficiary, T or X), and P1, P2 (the joint office's two parties: the two by key, swapped when seed mod 4 is 2 or 3, patria's rule). `world` must be sorted-key JSON (`json.dumps(..., ensure_ascii=False, sort_keys=True)`), as the published worlds are, so a world rendered with the published names reproduces the published sha256. A slot may not sit in a tool's name. The prompt must hold the type's neutral sentence (`arms.json`) exactly once, which `--arm g_wish` replaces.

## cells.json: U and O as overlays applied at load

Since revision 2, N0, U and O share one environment: the same tools (the activity_log tool included), state and rules (DESIGN 8.6). So `errands.jsonl` holds **only the N0 rows**, and the loader derives U and O from them. That makes the file about a third of the size.

```json
{"format": 1, "anchor": "When you have finished",
 "rule_id": "anulus.activity-log",
 "types": {"individual": {"N0": {"log_header": "<template>"},
                          "U":  {"notice": ["<paragraph template>"], "log_header": "<template>"},
                          "O":  {"notice": ["<paragraph template>", "<paragraph template>"], "log_header": "<template>"}},
           "organization": {"...": "..."}, "nation": {"...": "..."}}}
```

For each N0 row and each derived cell, the loader (probatio.py `derive_cell`) does four things:
1. **The prompt.** It splits the prompt into paragraphs on blank lines, finds the one paragraph that starts with `anchor`, and inserts the cell's notice paragraphs right after it, each separated by a blank line.
2. **The world.** It finds the one rule whose `id` is `rule_id` and replaces its `then.respond.header`, which must equal the N0 `log_header`, with the cell's `log_header`.
3. **The row's identity.** It sets the row's `cell` and turns the template id's `.N0` suffix into `.U` or `.O`.
4. **Refusal.** It refuses a row whose anchor or rule is missing or appears twice, or whose header does not match.

Slots in the notices and headers are filled at render time with the errand's roles, as everywhere else. W2's derivation test checks that every derived cell differs from its N0 row only in these two places.

## entities.json additions

- **`aliases`.** An optional list per record (for example `["USA", "the US"]`). A typed target matching an alias takes that record.
- **Back-references.** `to` and `To` are the requester's back-reference fields (W1's `requester_ref` is renamed to `to`).

## followup.json and arms.json

`followup.json`: `{"individual": "<the five questions>", "organization": "...", "nation": "..."}`; slots are allowed and filled with the errand's roles. The engine parses questions 1, 2 and 4 by number (`parse_report`).

`arms.json`: `{"<type>": {"neutral": "<the sentence as it appears in every prompt>", "g_wish": "<labores's g_wish for the type, with ⟦T.…⟧ slots>"}}`.
