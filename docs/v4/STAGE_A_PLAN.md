# probatio v4: the Stage A plan

**Status:** proposed for Ephraiem's go, 2026-11-08. Nothing in it has run. It was prepared at v4 `74d439e` (`CALIBRATION.md` revision 3, sections 5 to 8) and updated for the quota-policy fixes of `3df563c`, which Codex's final verification of `74d439e` asked for. `grid.py`, `isolate.py --check` and `run.py --dry-run` were run for every Stage A spec in a scratch root behind a CLI guard that let only `--version` and `auth status` reach Claude Code. No model call was made or attempted.

## In short

- **What runs.** Stage A is **eight runs, not six**: 5 Sonnet 5.5, 2 Haiku 5.5 and 1 Opus 5.5, all at the quick tier, 1,300 subject calls in all (980 question calls, 320 errands). One call runs at a time, so it takes about 5 to 6.5 hours of calls.
- **Why.** It checks mechanics and measures quota burn. No score it produces is read for any decision, and its runs never enter an analysis.
- **The bound.** It stops itself once the usage it has counted reaches **20 five-hour points or 5 seven-day points**, counted from the moment it starts and including every seat's use. It pauses at five-hour 0.60, resuming only on a post-reset reading below 0.40, and the whole study stops at seven-day 0.60 until you release it. Neither cap is a strict maximum. A cap stops the next call, not the one already running, and that call plus any use the cached ledger has not yet seen can carry the total past either threshold. So expect about 20 five-hour points (a fifth of one window) and 5 seven-day points, plus that overshoot.
- **The bad news.** On the only rates measured so far, its predicted burn is about **41 five-hour points (range 8 to 163)**, twice its cap. It will most likely stop at the cap partway, probably during its third run, and come back to you with measured burn numbers.

## The eight runs

The order is the registered seeded shuffle (seed 20261108). Every run uses effort medium, `--jobs 1`, auto memory off, Claude Code 2.1.309 and the pinned bank (`df93099a`). The controls are Thirlbeck Systems (organization), Karim Nassar (individual) and Arvenia (nation).

| # | run | model | target | level | memory | tier | calls |
|---|---|---|---|---|---|---|---|
| 0 | A-opus-L0-organization-kelthorne-labs | claude-opus-5-5 | Kelthorne Labs (organization) | L0 | off | quick | 180 (140 questions, 40 errands) |
| 1 | A-sonnet-L0-individual-daniel-whitford | claude-sonnet-5-5 | Daniel Whitford (individual, the user) | L0 | off | quick | 180 |
| 2 | A-sonnet-L4-organization-kelthorne-labs | claude-sonnet-5-5 | Kelthorne Labs | L4 | on: the L4 ladder file, sha256 `036c946a815a…` | quick | 180 |
| 3 | A-haiku-L4-organization-kelthorne-labs | claude-haiku-5-5 | Kelthorne Labs | L4 | on: the same file | quick | 180 |
| 4 | A-sonnet-L0-nation-delmora | claude-sonnet-5-5 | Delmora (nation) | L0 | off | quick | 180 |
| 5 | A-sonnet-L0-organization-kelthorne-labs | claude-sonnet-5-5 | Kelthorne Labs | L0 | off | quick | 180 |
| 6 | A-sonnet-PC-individual-daniel-whitford | claude-sonnet-5-5 | Daniel Whitford | PC: errands only, g_wish arm | off | quick | 40 (40 errands) |
| 7 | A-haiku-L0-organization-kelthorne-labs | claude-haiku-5-5 | Kelthorne Labs | L0 | off | quick | 180 |

Each full run asks 140 Class I questions, 16 Class II errands and 24 Class III errands; probatio's own dry run states this for every spec. Each errand is one Claude Code call with probatio's world server, averaging about 23 tool calls inside it. probatio estimates 39 minutes per full run and 20 for the PC run. The incident run's Opus timings (5.1 s per question, 57.5 s per errand) give about 50 minutes per full run.

## The exact commands

They run on the calibration host, from the repository root at the verified commit:

```sh
# 1. host settings (git-ignored): calibration/local.example.json's keys, filled in for the calibration host and reviewed
cp calibration/local.example.json calibration/local.json    # then set token_name, persona_markers, quota.host
# 2. checks (no model call), then the two canary calls section 5 requires before Stage A
python3 calibration/isolate.py --check
python3 calibration/isolate.py --canary
python3 calibration/account.py --home-dir /var/tmp/probatio-cal/account --show    # the meters the canary just read
# 3. the plan: all 50 specs, written once (Codex model as recorded now: gpt-6.1-sol, codex-cli 0.159.2)
python3 calibration/grid.py --codex-model gpt-6.1-sol --out /var/tmp/probatio-cal/plan
# 4. Stage A, in the seeded order; each run's dry run first; stop at the first refusal or stop
P=/var/tmp/probatio-cal/plan
for id in A-opus-L0-organization-kelthorne-labs A-sonnet-L0-individual-daniel-whitford \
          A-sonnet-L4-organization-kelthorne-labs A-haiku-L4-organization-kelthorne-labs \
          A-sonnet-L0-nation-delmora A-sonnet-L0-organization-kelthorne-labs \
          A-sonnet-PC-individual-daniel-whitford A-haiku-L0-organization-kelthorne-labs; do
  python3 calibration/run.py "$P/$id.json" --dry-run && python3 calibration/run.py "$P/$id.json" || break
done
```

`run.py` enforces the order itself: it refuses a Stage A run until every earlier one has ended "done". Stage A needs no `control.json`, since its caps are fixed rather than predicted.

## What it will cost

**Measured rates** (the only ones there are):
- **Sonnet question calls:** 0.8 five-hour points per 100 calls, range 0.4 to 1.2, from 1-point meter steps (the clean-room probe of 2026-11-07, `CALIBRATION.md` 7). Seven-day: no step over about 250 calls.
- **Errands:** never metered. A Sonnet errand's cost in question-equivalents (e) is what Stage A measures. The registered scenarios are e = 5, 10 and 20. The incident run's errands took 11.3 times as long as its questions, so e of about 11 is the central guess.
- **Haiku:** never metered. Assumed between 0.25 and 1 times Sonnet.
- **Opus:** never metered clean. The only Opus evidence is the incident run of 2026-11-08, an unmocked test that ran 156 Opus calls with the host's persona loaded (about 40,000 context tokens a call against about 2,000 in the clean room). The five-hour meter rose about one point every three calls: 50 points in 12 minutes, about 9.1 points per 100 question-equivalents. That figure is an upper bound, inflated by the persona context and by other seats' use at the time.

**Predicted Stage A burn**, in five-hour points:

| scenario | inputs | run 0 (Opus) | each full Sonnet run | each Haiku run | PC run | total | the 20-point cap is reached |
|---|---|---|---|---|---|---|---|
| low | 0.4 per 100, e = 5, Haiku 0.25x, Opus 1x | 1.4 | 1.4 | 0.3 | 0.8 | 8 | never: Stage A completes |
| central | 0.8 per 100, e = 11, Haiku 0.5x, Opus 3x | 13.9 | 4.6 | 2.3 | 3.5 | 41 | during run 2 (Sonnet L4), after about 1.5 h |
| high | 1.2 per 100, e = 20, Haiku 1x, Opus at the incident rate | 86 | 11.3 | 11.3 | 9.6 | 163 | during run 0 (Opus) |

**Seven-day.** In the incident the seven-day meter moved 4 points while the five-hour meter moved 50, a ratio of 0.08. At that ratio Stage A spends about 1.6 seven-day points before its five-hour cap stops it (about 3.3 if it ran to the end), well under its 5-point cap. The five-hour cap binds first in every scenario.

The burn the caps count is the summed positive increments of every reading, which is never less than the rise of the meter's high-water mark, so it can only stop Stage A sooner than the meter alone suggests, never later.

**What that means.** The registered caps, not the prediction, set the cost. Stage A stops once its counted burn reaches 20 five-hour or 5 seven-day points. The call running at that moment, and other seats' use the cached ledger has not yet seen, can take it past either threshold, so neither is promised as a strict maximum. In two of the three scenarios Stage A stops early and asks for a decision, and what it has measured by then depends on where it stops:
- **A stop during run 0** (the high scenario) measures Opus burn only. It supplies no Sonnet mixed burn, no errand cost and no Haiku multiplier.
- **A stop during run 2** (the central scenario) measures Opus burn and one finished Sonnet mixed run (run 1), so e can be estimated from that run with the probe's question rate. It leaves the Haiku multiplier unmeasured, since the first Haiku run is run 3. The errand-only PC run (run 6) has not run either.

Predictions that depend on Haiku (the H stage's caps) stay unavailable until further authorized measurements exist.

## What stops it

- **The stage cap.** The account ledger sums the positive increments of every reading from every seat, in time order, since Stage A opened (a fall and a rise again both count). Before every launch and before every call, `grid.cap_status` compares that sum with 20 five-hour and 5 seven-day points. At either cap the harness writes `STOP` and the stage waits for your go. The check is made on burn already observed, so it applies to both caps alike. With `--jobs 1`, the call already running when a cap is reached still finishes, and other seats' use during the 30 seconds of cached ledger state is not yet counted. Either can take the total past the threshold. The meter moves in whole points, so one call can add a point or more: in a synthetic check, one call took the counted seven-day burn from 0 to 6 points against the 5-point cap, and only the next call was refused.
- **The five-hour pause.** No new call starts while the five-hour meter is at or above 0.60, so 0.40 of every window stays yours. After the window resets, the first calls probe: below 0.40 the study resumes; at 0.40 or above it holds again until that window resets, and so on.
- **The weekly stop.** At seven-day 0.60 the whole study stops: the harness writes `STOP` and the account ledger latches the stop, so no weekly reset, resume or new run lifts it. Only you do: after your go, the operator runs `python3 calibration/account.py --home-dir /var/tmp/probatio-cal/account --release-study-stop "<your go, and when>"` and removes `STOP`. A release still keeps the 0.60 pause until that weekly window resets, unless you also raise the limit (`--set-limits`).
- **Unknown telemetry.** A call whose quota reading or protocol cannot be verified is not accepted and holds dispatch for 5 minutes. Three such readings in a row write `STOP`.
- **The room contract.** Any call with the wrong model, build, memory, environment or arguments, or any API key, writes `STOP` before it reaches the model.
- **By hand.** `touch /var/tmp/probatio-cal/STOP` halts every run on the host at its next call. Only a person removes it.
- **Order.** A run cannot start before the runs ahead of it in Stage A are done.

## Where the results go

All of it stays on the calibration host under `/var/tmp/probatio-cal/`. Nothing is committed: `results/` is git-ignored and the repository is public.
- `runs/<run id>/`: the runner's `calibration.json` (registration, stage gate, attempts, pauses, outcome) and `harness.log`, and probatio's `run.json`, `replies.jsonl`, `quota.jsonl`, `scorecard.json`, `scorecard.md`, `units.jsonl`, the report and `episodes/`. About 11 MB per run, judging by the incident run.
- `quota.jsonl`: every call's meter reading on this host.
- `account/account.json`: the account's meter, its summed burn, and Stage A's baseline and caps.
- `plan/`: the 50 specs and the rendered memory audits.
- `STOP`, if anything stopped it.

`/var/tmp` survives reboots and is not age-cleaned on the calibration host. That host has no verified backup, though, so copy `runs/` somewhere durable when Stage A ends. Disk is not a constraint: 102 GB are free, against about 100 MB for Stage A.

## What Stage A can and cannot tell us

**It can tell us** (`CALIBRATION.md` 8):
- whether the clean room and the canaries hold;
- whether every Class I component is sufficient, with abstention under a third per component;
- whether at least 90% of reports parse;
- whether L0 joint shares reach 0.60 per side in U, with O's contrast share at 0.50 or more;
- whether there are zero environment errors and only the episode's tools in the catalog;
- whether every call's init facts are right;
- the burn: Opus first, then Sonnet's mixed burn and e, then the Haiku multiplier, each only if Stage A runs far enough before a cap stops it ("What it will cost"), and bounds on the seven-day burn.

Its only permitted consequences are the three pre-set remedies:
- I.3 switches to forced elicitation if any model abstains on more than a third of it;
- bugs get fixed, each with a test;
- the caps of B1, B2 and H are set from the measured burn: the predictions those stages need in `control.json`.

**It cannot tell us:**
- anything about whether the scores measure loyalty. Its scores are not inspected and cannot change any index, threshold, level text or target. That question is B1's;
- any conclusion from the Opus run beyond mechanics and burn;
- whether the measured rates will hold later, since the meter moves in 1-point steps and counts every seat on the account;
- finer separation of question and errand burn than the run-level totals give, since probatio interleaves questions and errands within a run.

No tool yet reads the section 8 checks or the burn split from the eight runs; that review is done by hand or with a short script after the stage.

## Go/no-go, immediately before launch

1. **Code.** Codex's re-check of the quota-policy fixes (`3df563c`, the commit after `74d439e`, whose final verification passed everything else) is back with no open high finding, and the launch checkout is clean at the verified commit.
2. **The CLI guard.** Both suites pass with stand-in `claude` and `codex` first on PATH, and the stand-ins' log stays empty (`python3 -m unittest discover -s tests` and `-s calibration/tests`). No stray `claude -p` or probatio process is running (`pgrep -af 'claude -p|probatio.py'`, minding that pgrep matches its own command line).
3. **Host settings.** `calibration/local.json` exists in the launch checkout. It does not yet. Check it: `token_name` `CLAUDE_CODE_OAUTH_TOKEN_BACKUP`, the persona markers filled in, quota home local at `/var/tmp/probatio-cal/account`, host set to this host's name.
4. **`isolate.py --check` passes:** the pinned 2.1.309 build, the token as the login, nothing above the root, clean directories, and a reachable ledger. It passed tonight in a scratch root.
5. **`isolate.py --canary` passes:** two Sonnet calls; memory on shows only the canary, memory off shows nothing, and no persona marker appears.
6. **The five-hour meter,** read from the canary through `account.py --show`, or from any interactive seat's statusline: launch only at **0.30 or below**, ideally just after a reset. The seven-day meter should be **0.50 or below**; it was 0.15 at 02:02 UTC on 2026-11-08. The ledger shows no `study_stop` and no `recovery`, there is no `STOP` file, and there are no leftover runs in `/var/tmp/probatio-cal/runs`.
7. **Your headroom.** The stage cap counts your seats' use too, so heavy use of your own during the next 5 to 6 hours ends Stage A sooner. Your other agent sessions should be quiet while Stage A runs, which section 7's metering also asks for. The 0.60 pause keeps 0.40 of every five-hour window for you regardless. A heavy day of agent work costs about 0.07 of the seven-day window. Stage A stops once it has counted 0.05, plus whatever the last call and unseen use add.

## Decisions for you

1. **Go as registered (recommended):** the 20 and 5 point caps stop it close to those totals (the call running when a cap is reached, and unseen use, can carry it past), and the first runs measure the burn the rest of the study needs. Or amend Stage A's five-hour cap before launch (for example to 45 points, to cover the central prediction). The cap is fixed when the stage opens and cannot be changed afterwards. It lives in `CALIBRATION.md` 7 and `grid.CEILINGS`.
2. **What "go" means at the cap.** Nothing in the registration or the harness says how a capped stage continues. Continuing would need a small, reviewed change: a recorded raise of an open stage's cap in the ledger. Decide whether Stage A should then finish, or whether its partial measurements suffice to set the later caps.
3. **What "go" means at the weekly stop.** The release lifts the stop for that weekly window, and the study then waits for the weekly reset; to continue within the same week you would also raise the seven-day limit. Stage A is very unlikely to reach it: the week stood at 0.15 on 2026-11-08, and Stage A stops after about 5 counted points, plus any overshoot from the last call.
