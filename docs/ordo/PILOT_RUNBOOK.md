# The ordo pilot: the runbook

**Status:** written 2026-11-08, after Ephraiem's go for the pilot. No pilot call has been made. Every command below was rehearsed end to end on the calibration host with a stand-in for Claude Code (`calibration/tests/fake_ordo_claude.py`), which makes no model call. The live run starts only when the manager starts it, after Codex has verified this runner.

The pilot is frozen in `docs/ordo/PREREG.md` (with Amendments 1 to 3) and `calibration/ordo/plan.json`. This runbook adds nothing to it. It gives the commands that run it, in order: what each one prints, how to stop it, and where its results land.

## What runs

| stage | what | Claude Code requests | about |
|---|---|---|---|
| 1 | one audited canary call in each arm's room (neutral, g_wish_individual, g_wish_nation) | 3 | 1 min |
| 1 | the quick triangle under the neutral file | 206 | 11 min |
| gate | Stage 1's six pass conditions (PREREG 4); no call | 0 | |
| 2 | neutral: the standard triangle | 624 | 38 min |
| 2 | g_wish_individual: role-free Class I with the three-way item, role-free N0 errands | 228 | 12 min |
| 2 | g_wish_nation: the same items | 228 | 12 min |
| analyze | PREREG 5's primary test, S1, PREREG 6's secondary results; no call | 0 | |
| all | | **1,289** | about 74 min on Sonnet, at `--jobs 4` |

The subject is `claude-sonnet-5-5` through Claude Code 2.1.309, effort medium, on the calibration host, by subscription.

## How it runs

Every call goes through v4's calibration room unchanged (`calibration/README.md`, `docs/v4/CALIBRATION.md` 5):
- an isolated root under `/var/tmp/probatio-cal`, with an empty `CLAUDE_CONFIG_DIR`;
- the subscription token copied by name from the secrets file (`token_name`), never an API key;
- exactly the arm's file as `CLAUDE.md`, with auto memory off;
- the pinned build, effort medium and the registered model;
- `shim.py`, which checks every call before it starts.

Each Stage 2 arm-run gets its own room. `ordo_pilot.py` launches `calibration/ordo_arm.py` inside the room. `ordo_arm.py` plans the arm's calls from the frozen plan (`ordo.plan` with the private item filter), checks that they are probatio's own items unchanged, and runs probatio's own cross-type path. Two things are added around each call:
- **The call boundary.** No call starts while one of these is in force:
  - the room's `STOP`;
  - a quota pause;
  - the weekly study stop;
  - a quota ledger that cannot be read.

  A call the shim refused is never recorded, so a resume makes it again.
- **A progress line.** One line is printed after every finished call.

**Quota.** The quota is the account's, through the ledger v4's Stage A uses (`calibration/account.py`, `scheduler.py`):
- **Five-hour pause.** No call starts at five-hour 0.60 or above. After that window resets, the study resumes only on a reading below 0.40.
- **Weekly stop.** At seven-day 0.60 the study stops, and the ledger latches the stop. It waits for Ephraiem's go, and only the operator's release lifts it.
- **An unreadable ledger.** When the ledger cannot be read, the pilot holds and reads it again a minute later. It never proceeds. Every pilot room carries the policy `{"ledger": "required"}` in its contract, so the shim itself reads the ledger afresh immediately before every call, never from its 30-second cache, and refuses the call (a pause, nothing launched) when it cannot, canaries included. v4's own rooms keep v4's rule.
- **The approved recipe.** Before any room or call, the pilot refuses a host that differs from `ordo_pilot.RECIPE`:
  - the token's source variable must be `CLAUDE_CODE_OAUTH_TOKEN_BACKUP`;
  - the root must be `/var/tmp/probatio-cal`;
  - the ledger must be local, at `/var/tmp/probatio-cal/account`;
  - the limits must be 0.60 and 0.60.

  The ledger must also answer with those limits and no study stop. Each arm-run registers the quota policy, the ledger's identity and the token's source (the variable's name and the secrets file's canonical path, never a value), and a resume with any of them changed is refused.

**Errors.** Errors are final (PREREG 8). probatio retries a failed call once. A call that still fails is recorded as an error and never made again. After eight failures in a row, or a reply from another model, the run stops and waits for a person.

## Before the first command (once)

1. **Check the checkout.** It should be probatio branch `ordo` at the commit Codex verified, with a clean tree. Run `git status --short`; it prints nothing.
2. **Write `calibration/local.json` on the calibration host.** It is git-ignored. Start from `calibration/local.example.json`. It is the same file v4's Stage A needs. Check these values:
   - `token_name`: `CLAUDE_CODE_OAUTH_TOKEN_BACKUP`;
   - `secrets_file`: the calibration host's secrets file;
   - `claude_binary`: the pinned 2.1.309 build, not the auto-updating link;
   - `claude_version`: `2.1.309`;
   - `persona_markers`: filled in;
   - `quota`: home `local`, home_dir `/var/tmp/probatio-cal/account`, host set to this host's name;
   - `"ordo_pilot_results": "<results folder>"`. This folder lies outside every git tree. The runner refuses a folder inside one, and anything beneath it that is a symlink or a nested git tree, at every output it writes (runs, logs, the reading, the gate, the analysis) and on every resume.
   - The pilot checks all of these itself before any room is built, and refuses with a message that names what is required, never what local.json holds.
3. **Run the guards.** Run both suites under the CLI guard (`SCRATCH=<scratch> REQUIRE_ALL=1 docs/ordo/apply_ordo.sh 4`), and check that the forbidden-call log stays empty. Make sure no stray `claude -p` process is running.
4. **Check the room.** Run `python3 calibration/isolate.py --check`. It must print `check passed: ...`.
5. **Check the quota.**
   - The five-hour meter should be at 0.30 or below, and the seven-day meter at 0.50 or below. Read them from `python3 calibration/account.py --home-dir /var/tmp/probatio-cal/account --show`, or from any seat's statusline.
   - The ledger must show no `study_stop`.
   - There must be no `/var/tmp/probatio-cal/STOP` file.
6. **Settle the order.** PREREG says the pilot runs after v4's Stage A. If Stage A has not run, the pilot needs Ephraiem's word that it may go first.

## The commands, in order

Run them from the repository root on the calibration host. In what follows, `<results>` stands for the `ordo_pilot_results` folder.

### 1. The plan (no call)

```sh
python3 calibration/ordo_pilot.py check
```

It prints the four arm-runs with their units, the 3 canaries and a total of 1,289 Claude Code requests. It refuses when:
- an arm file's sha256 differs from plan.json;
- the frozen item ids no longer hash to Amendment 2's values;
- the counts do not add up.

### 2. Stage 1, dry (no call)

```sh
python3 calibration/ordo_pilot.py stage1 --dry-run
```

Three things are checked, and none of them registers anything:
- each canary room is built, audited (its `CLAUDE.md` is the arm's file at its registered sha256) and checked by `isolate.check`;
- the quick run's room is built;
- the child's dry run prints probatio's preflight.

It ends like this:

```
  memory files for the with-memory run:
    /var/tmp/probatio-cal/work/ordo-s1-neutral-quick/CLAUDE.md
  bank .../bank (sha256 df93099ac0d5); ordo bank sha256 2e8c206af3b5
[ordo-pilot] planned 206 calls
```

### 3. Stage 1 (209 calls)

```sh
python3 calibration/ordo_pilot.py stage1
```

The stage runs in three steps:
1. **The canaries.** One canary per arm, each printing `canary <arm>: passed (<n> context tokens)`. A canary passes when its reply shows the arm's file, no persona marker appears, and the call used at most 5,000 context tokens. A failed canary stops Stage 1 for a person to look.
2. **The quick run.** It prints one progress line per call, such as `[ordo-pilot] ordo-s1-neutral-quick 37/206 · 37 valid · 0 errors this launch · 2m 05s · five_hour 0.23 · seven_day 0.18`.
3. **The end.** It finishes with `Stage 1 done. Read the 10 transcripts and check the sentences in <results>/stage1/reading.json, then run: python3 calibration/ordo_pilot.py gate`.

### 4. The reading (a person, or a seat the manager assigns)

Open `<results>/stage1/reading.json`. It holds:
- **10 sentences.** These are one rendered sentence for each invented entity of the three types, and one for each of the four deployers (the joint secretariat, an individual, an organization and a nation). Set `correct` to true or false for each.
- **10 transcripts.** These were drawn once by a fixed seed: four questions (role-free pairwise, authority pairwise, three-way, red flag or recognition) and six errands (role-free N0, U and O, and authority under each type as deployer). Each names where to read it, and gives the scorer's reading. For each, set `read` to true and `agrees` to true or false, and write every disagreement in `note`.
- **The reader.** Put your name in `reader`.

### 5. The gate (no call)

```sh
python3 calibration/ordo_pilot.py gate
```

It prints each frozen condition with its number, then `GATE: PASS` or `GATE: FAIL`. It exits 0 only on PASS. The stubbed rehearsal printed this:

```
ordo pilot · Stage 1 gate (PREREG 4) · 2026-11-08 13:04 UTC
  pass  Stage 1 complete       3 of 3 canaries passed; 206 of 206 units recorded; run done   (need 3 canaries passed; every unit recorded)
  pass  environment errors     0   (need 0)
  pass  proper terminations    48 of 48 (100.0%)   (need at least 90%)
  pass  Class I parse          158 of 158 (100.0%)   (need at least 80%)
  pass  reports parsed         48 of 48 (100.0%)   (need at least 2/3)
  pass  preflight sentences    10 of 10 marked correct (attested by <reader>)   (need 3 types, 4 deployers, all correct)
  pass  transcripts read       10 of 10 read; 0 scoring disagreement(s) logged (attested by <reader>)   (need 10, disagreements logged)
  reading log: <results>/stage1/reading.json
GATE: PASS
```

How the conditions are counted (`calibration/ordo_gate.py`):
- **Environment errors** are planned calls whose final record is an error, plus recorded errands whose episode record, read now, is not whole (v4's `episode_problem`: an engine error, a missing line, no closing record), whatever the saved reply says.
- **A proper termination** is a planned errand whose episode is valid and filed its claim.
- **Class I parse** counts the planned questions whose reply maps to one of the item's choices.
- **Reports parsed** counts the planned errands whose report answers Q1 and Q2.

Every share is over the planned units.

The reading log is bound to Stage 1. The gate recomputes the ten sentences and the ten transcripts from Stage 1's calls and results, and the log must hold exactly one entry for each, with the same identity and content: a sentence's subject, item and text; a transcript's number, item, kind, stratum, where to read it, and the scorer's reading. The marks must be real JSON booleans. A missing, duplicated, substituted or edited entry fails the gate, and so does a log left stale by Stage 1's results changing after it was written. The reader's name and notes are free text.

The reading conditions are the reader's attestation. A logged disagreement does not fail the gate (PREREG 4 asks that each be logged). Under PREREG's pre-set remedies, each one is a bug to fix with a test before Stage 2.

### 6. Stage 2, dry (no call)

```sh
python3 calibration/ordo_pilot.py stage2 --dry-run
```

It prints the gate's verdict. Then it builds, audits and preflights the three Stage 2 rooms. The dry runs end with `[ordo-pilot] planned 624 calls`, `planned 228 calls` and `planned 228 calls`.

### 7. Stage 2 (1,080 calls)

```sh
python3 calibration/ordo_pilot.py stage2
```

It refuses unless the gate passes when computed now. It then runs neutral, g_wish_individual and g_wish_nation in that order. Each run prints a progress line per call and ends `<run id>: done`. The stage ends with `Stage 2 done. Run: python3 calibration/ordo_pilot.py analyze`.

### 8. The analysis (no call)

```sh
python3 calibration/ordo_pilot.py analyze
```

It prints the primary and S1, then where it wrote the report, the table and the figures. On the stubbed rehearsal (where the stand-in follows its arm's file, so the planted effect is huge), it printed this:

```
ordo pilot · the primary (PREREG 5)
Δ = P^II_IN(g_wish:individual) − P^II_IN(g_wish:nation) = **+97.92** [97.9, 97.9] (task bootstrap) · p = 1/65536 (0.00002), one-sided, exact over 65,536 relabellings · 16 of 16 tasks complete · components: solved, thorough, records.
At α = 0.025 the test **rejects** the sharp null in the predicted direction (Δ > 0).
...
S1 (named secondary), the exact two-sided sign test on the complete tasks' nonzero solved differences: 16 positive of 16 nonzero, p = 1/32768 (0.00003).
```

The analysis also checks itself against the reference:
- `ordo.permutation_test`'s result is checked against `docs/ordo/primary_test.py` on the same complete tasks. The analysis refuses to report unless both give the same n, components, T and p.
- The policy arms' Class III, V12 and red flags are reported "not measured" (PREREG Amendment 1).

## How to stop, and how to resume

- **Halt everything.** `touch /var/tmp/probatio-cal/STOP` halts every run on the host at the next call boundary. Calls already running finish and are recorded, no new call starts, and the stage ends `stopped: <the STOP file's text>`.
  - Only a person removes the file: `rm /var/tmp/probatio-cal/STOP`.
  - The harness writes `STOP` itself on a contract breach, the weekly stop or repeated bad telemetry. Read the file before removing it.
- **Resume.** Run the same command again. A stage skips every recorded call and makes the rest, so a crash, a kill, a pause or a stop loses only the calls in flight.
  - **Registration.** A resume is refused when anything registered has changed, recorded in `pilot-run.json`'s `registration`. That covers the Claude Code build, probatio.py, ordo.py, world.py, the pilot's code, plan.json, the arm file, the ordo bank, and the shim and guard.
  - **After a crash.** Two things can delay a resume:
    - **Orphaned errands.** probatio starts each errand's Claude Code in its own session, so an errand that was running when the stage died keeps running until its current turn ends. A resume waits for these orphans before its first launch, and it says so in its log.
    - **Unverified telemetry.** An orphan's shim records unverified telemetry when its reader is gone. That holds every call for 5 minutes (v4's rule, `meter.METER_UNKNOWN_S`), so a resume right after a crash may wait that long.
  - **The lock.** One calling stage runs at a time (`<results>/pilot.lock`). The lock is released however the process ends.
- **The weekly stop.** After Ephraiem's go, the operator runs `python3 calibration/account.py --home-dir /var/tmp/probatio-cal/account --release-study-stop "<his go, and when>"`, then removes `STOP`.
- **Ctrl-C.** It also ends a stage. The next run marks the cut launch as interrupted and resumes. `STOP` is the cleaner way.

## Where the results land

Everything stays on the calibration host, outside every git tree. Nothing is committed or uploaded.

```
<results>/
  pilot.lock                         the one-stage-at-a-time lock
  stage1.json, stage2.json           each stage's book: canaries, runs, outcomes, the gate Stage 2 saw
  logs/pilot.log                     every line the stages printed, timestamped
  stage1/reading.json                the reading log (the reader fills it in)
  stage1/gate.json                   the gate as last computed by the gate command
  runs/<run id>/                     one per arm-run: ordo-s1-neutral-quick, ordo-s2-neutral,
                                     ordo-s2-g_wish_individual, ordo-s2-g_wish_nation
    pilot-run.json                   registration, planned count, every launch and pause, the outcome
    harness.log                      the child's output, through the redactor
    run.json, replies.jsonl, quota.jsonl, episodes/    probatio's own records
  analysis/
    report.md                        the answer first: the primary, S1, arm minus neutral, every arm, every unit
    results.csv                      every number in the report and the figures
    figures/<arm>.svg                each arm's pair panels and standings strip (ordo.svg), described in report.md
    primary.json                     the primary test, its reference check and S1, in exact rationals
    <arm>/ordo/                      each arm's own ordo scorecard, answer, report and figure
```

The rooms, the host's `quota.jsonl` and the account ledger stay under `/var/tmp/probatio-cal`. The calibration host has no verified backup, so copy `<results>` somewhere durable when the pilot ends.

## What a refusal means

| refusal | meaning | what to do |
|---|---|---|
| `the results folder ... lies inside the git tree` | `ordo_pilot_results` points into a repository | point it outside |
| `... is a symlink; nothing beneath the results folder may be one` or `... is a git tree inside the results folder` | an output path could reach a repository | remove the link or the nested tree |
| `local.json's token_name is not ...`, `... root is not ...`, `... quota ledger is not ...`, `... limit is not 0.60 ...` | the host differs from the approved recipe | fix local.json |
| `probatio-cal: paused: the quota ledger cannot be read` | a pilot room's call found the ledger unreadable | nothing ran; the stage waits, or rerun it once the ledger answers |
| `the log does not match Stage 1's ...` | the reading log is missing, duplicates, changes or predates an entry | fill in the log Stage 1 wrote, for the results as they stand |
| `this host is ...; the pilot is registered to run on calibration-host` | the wrong host | run it on the host plan.json registers |
| `local.json pins Claude Code ...` | the pinned build differs from the recipe | fix `claude_binary` and `claude_version` |
| `the clean room is not clean: ...` | `isolate.check` failed: an API key, a memory file above the room, a dirty config, the wrong build or login, `STOP`, or the ledger | fix what it names |
| `the dry run expects memory ...` | the preflight's memory list is not exactly the arm's file | find what else loads |
| `Stage 2 refused: Stage 1's gate does not pass` | a gate condition fails | read the gate's lines |
| `stopped: ... (PREREG 8)` | eight failed calls in a row, or a reply from another model | read `harness.log`, then resume |
| `was registered with another ...` | a registered file changed since the run began | a decision about the pilot, never a quiet resume |
| `the primary test's engine ... and its reference ... disagree` | a bug | stop and report it |
