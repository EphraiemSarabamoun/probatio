# How probatio works

## What it does

probatio benchmarks a person's own Claude Code or Codex agent on four public loyalty datasets from Hugging Face. Every prompt is sent twice, once with the agent's memory files loaded and once with them switched off, and the replies are scored mechanically. The report compares the two conditions, so it shows how much the memory files change whom the agent favours. The README explains the two conditions and the report for users; this file is for someone reading or changing the code.

## Where execution starts

Everything is in one file, probatio.py, and execution starts at `main()` at its bottom. `python3 probatio.py --agent claude --name "Jane Doe"` parses the flags, checks the CLI and its login (`agent_options`), builds the list of calls (`plan_calls`), prints the preflight (`preflight`) and, unless `--dry-run` is given, makes the calls (`run_calls`) and writes the report (`report`).

## The main flow

```mermaid
flowchart TD
    A["main: read flags"] --> B["agent_options: CLI on PATH, Codex login, model and features"]
    B --> C["plan_calls: load_rows downloads each dataset once, checks row count and sha256"]
    C --> D["personalise puts the name in; each item gets two calls, one per condition, in a seeded order"]
    D --> E["preflight: version, model, call count, memory files found"]
    E -->|--dry-run| Z["stop"]
    E --> F["claim the results folder, then check_resume: run.json must match the recorded settings and hashes"]
    F --> G["run_calls: threads; skips calls already in replies.jsonl"]
    G --> H{"agent"}
    H -->|claude| I["claude_call: claude -p, memory switched off by two environment variables"]
    H -->|codex| J["codex_call: codex exec in a fresh temporary CODEX_HOME"]
    I --> K["score: parse the reply, map the choice to its meaning"]
    J --> K
    K --> L["replies.jsonl: one line per call, appended as it finishes"]
    L --> M["report: measures and quality per condition, then the change"]
    M --> N["report.md, also printed"]
```

The calls are planned before anything runs. Each dataset row becomes two calls, one per condition, and the items are shuffled with a fixed seed while an item's two calls stay side by side, so slowdowns or rate limits during the run fall on both conditions alike. `ask` makes one call with one retry and keeps errors (timeouts, CLI failures) apart from abstentions (replies that do not follow the answer format). Each finished call is appended to replies.jsonl at once, which is what makes a rerun with the same `--out` resume: `read_replies` keeps the newest line per call and cuts off a last line that an interrupted write left torn, so the next record starts on a line of its own, and calls that ended in an error are tried again. `main` first claims the results folder (`claim`): it takes an advisory lock (`fcntl.flock`, so macOS and Linux) on out/run.lock and keeps the file open until the run ends, so two runs never share a folder. The system releases that lock however the process ends, a crash included, so there is never a stale lock to clear; the file stays, holding the last run's process id for a person to read. `check_resume` then refuses a resume when anything recorded in run.json has changed, the sha256 of each memory file it can see included. It cannot see every input (Claude Code's own settings, for one), so `run_calls` also stops, without recording that reply, when a reply comes from a model none of the run's earlier replies came from, and the report warns when one condition was answered by more than one model. After eight failures in a row the same stop keeps any new call from starting; the calls already running finish and are recorded.

## The important parts of probatio.py

**Datasets.** `COMMITS` pins each dataset to one Hugging Face commit and `CONFIGS` names the config and row count per tier. `load_rows` downloads into .cache/ and `check_rows` refuses a file whose row count or prompt hashes differ from the published ones. `personalise` replaces the `{PRINCIPAL}` and `{PRINCIPAL_FIRST}` placeholders with the user's name; in the key fields (people, maps_to, requester) the placeholder stays and means "you" (`YOU`).

**Parsing.** One function per answer format on the dataset cards: `letter` (bare_letter), `rating` (integer_0_10), `last_line` (final_action_line), `one_word` and `three_lines` (three_final_lines), with `bare` removing the few wrappers the cards tolerate. `score` picks the parser by the row's `answer_format` and maps the answer through the row's `choices` to what it means: a person, an action such as help or stay, a rating, or the three menu choices. These rules were checked against the evals' own reference parsers on every reply the evals had recorded, with full agreement.

**Agents.** `claude_command` and `codex_command` build the command lines; the only difference between the conditions is the two environment variables for Claude and `project_doc_max_bytes=0` plus the missing AGENTS links for Codex. `codex_home` makes each call's temporary CODEX_HOME in the run's codex-homes/ folder inside `--out`. It holds only links (to auth.json and, with memory, the global AGENTS files) and is deleted when the call ends, after a timeout or an error too. A kill that cannot be caught (SIGKILL, a crash, a power cut) skips that cleanup and can leave the homes of the calls in progress, each with its link to the login, in codex-homes/ until the next run with the same `--out`, whose `claim` removes them once it holds the lock, so it never touches the homes of a run that is still going. `memory_files` lists the files the with-memory run should load, for the preflight, the report and the resume check: for Claude Code the user's CLAUDE.md and rules, the CLAUDE.md files and rules folders from the working directory up, the auto-memory index and every file these import (`imports`); for Codex one AGENTS file in its home and in each folder from the repository root down (`agents_file`). It only looks, and the CLI decides what it actually loads.

**Metrics and report.** `measures` turns the parsed outcomes of one condition into the report's rows, `quality` counts valid replies and errors per dataset, and `show` and `change` format the table cells. Rates carry a Wilson 95% interval (`wilson`).

## Files

| file | what it is |
|---|---|
| probatio.py | the whole program |
| tests/test_probatio.py | unit tests: parsers, substitution, metrics, command lines, resuming, the dataset check |
| README.md | for users: quick start, the two conditions, what is sent where, limits, citations |
| .cache/ | downloaded datasets (not tracked) |
| results/ | each run's run.json, replies.jsonl, report.md and run.lock, and for Codex codex-homes/ (not tracked) |

## Where to read next

Start with `main()` and `plan_calls`, then `claude_command` and `codex_command` for the two conditions, then `score` and `measures` for how a reply becomes a row of the report. The dataset cards on Hugging Face (linked in the README) define every answer format and column the code relies on.
