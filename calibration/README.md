# The calibration harness

**Status:** part of W4 in `docs/v4/IMPLEMENTATION_PLAN.md`. Built so far: the clean room (`isolate.py`), the room contract and per-call checks (`guard.py`), the per-call rules (`shim.py`), the redaction boundary (`redact.py`), the quota meter and rules (`meter.py`, `quota.py`), the account-level quota scheduler shared by every host (`scheduler.py`, `account.py`), the one-run driver (`run.py`) and the stage gates that hold it to the staged plan (`stages.py`), with their tests. Codex's verification of part 1 found eight ways the clean room could leak, fail open or misreport; each is fixed and has a regression test (`tests/test_regressions.py`). Its re-verification of that fix (commit c804818) found five more edge cases (startup redaction, the memory switch and the Codex command contract, unverified stream results and loose model and version pins, non-finite meter values, and the Codex binary's bytes); each is fixed and has its tests (`tests/test_reverify.py`). Its third pass (of `4e5bd0e`) found three more (a long secret cut before redaction, oversized integers in the quota telemetry, and an unregistered MCP server, which the world-server pin had closed); each has its test (`tests/test_reverify3.py`). The schedule (`grid.py`, `STAGES.md`) and the analysis (`analyze.py`, `ANALYSIS.md`) follow `CALIBRATION.md` revision 3. Nothing here has run a calibration run yet.

The study itself is pre-registered in `docs/v4/CALIBRATION.md`. This folder runs it.

## Setting up a host

Copy `local.example.json` to `local.json` and fill in this host's paths. `local.json` is git-ignored, so no host path, token name or private marker enters the public repository.

| key | what it is |
|---|---|
| `root` | where the rooms are built, default `/var/tmp/probatio-cal`. It must have no `CLAUDE.md`, `CLAUDE.local.md`, `.claude/` or `AGENTS` file in any parent folder, so a folder under a home that holds `~/.claude` will not pass. |
| `results_dir` | where each run's folder goes; null means `<root>/runs` |
| `secrets_file`, `token_name` | the shell file holding the subscription's OAuth token and the variable's name. The harness reads the file without running it, keeps the value in memory and hands it to the subject calls as `CLAUDE_CODE_OAUTH_TOKEN`. It never prints or writes it. |
| `claude_binary`, `claude_version` | the pinned Claude Code build (a versioned binary, not the auto-updating link) and the version it must report |
| `codex_binary`, `codex_version`, `codex_auth` | the Codex CLI, its version, and the host's Codex login file |
| `persona_markers` | phrases from the host owner's own persona or memory that must never appear in a canary reply (private, so local only) |
| `streams` | the hosts and their `--jobs`, for the schedule (read by `grid.py` later) |

The calibration never uses an API key. The harness refuses to start when `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL` or a third-party provider switch is set, and every subject call must report `apiKeySource: none`.

## The clean room

```
python3 calibration/isolate.py --check     # build a scratch room and check it; calls no model
python3 calibration/isolate.py --canary    # also two real calls proving that only the memory file loads
```

Each run gets its own room under `root`:

| path | holds |
|---|---|
| `claude-config/<run>/` | the run's `CLAUDE_CONFIG_DIR`: empty at first, then only Claude Code's own state. No CLAUDE.md, rules, settings, skills, hooks, stored login, MCP servers, or files under `projects/` or `plugins/`. |
| `codex-user/<run>/` | the Codex user dir: only `auth.json`, a link to the host's login. Never an AGENTS file, so the host's Codex persona can never load. |
| `work/<run>/` | the subject's working directory: only the run's memory file (`CLAUDE.md` for Claude Code, `AGENTS.md` for Codex), or nothing |
| `bin/<run>/` | the `claude` and `codex` commands the run sees first on its PATH (wrappers around `shim.py`), `room.json`, which describes the room and holds no secret, and `contract.json`, the room's contract, written once |
| `quota.jsonl` | every subject call's quota reading on this host (each also goes to the ledger) |
| `STOP` | a hard stop (below); absent normally |
| `account/` | the account's quota ledger, on its home host only (below) |
| `account.cache.json` | this host's short-lived copy of the ledger |

The environment probatio runs in starts from the caller's, drops every `CLAUDE*`, `ANTHROPIC*` and `OPENAI*` variable and the tmux variables `TMUX` and `TMUX_PANE`, and sets only these: `CLAUDE_CONFIG_DIR`, `CLAUDE_CODE_OAUTH_TOKEN`, `CLAUDE_CODE_EFFORT_LEVEL`, `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` (both conditions), `CLAUDE_CODE_DISABLE_CLAUDE_MDS=1` (off runs only), `DISABLE_AUTOUPDATER=1`, `CODEX_HOME` and `PATH` with the room's `bin` first.

**The room contract.** When a room is first built, its contract is written to `bin/<run>/contract.json` and never changes: the agent, the memory condition, the effort, the model, and the memory file's exact sha256, or its required absence. A reopening that asks for anything else is refused. The approved memory must be a regular file, never a symlink, and may not contain an `@`-import (Claude Code's way of loading another file). A room that expects no memory file refuses one it finds.

`check` refuses a room with any of these:

- an API key or other provider in the environment;
- a memory file in any parent of the working directory;
- an unclean config dir;
- an AGENTS file in the Codex user dir;
- a working directory that breaks the contract (anything besides the approved file, a changed digest, a symlink);
- the wrong Claude Code build;
- a login other than the token (`claude auth status`, which reads local state only);
- a hard stop in force.

The same checks (`guard.py`) run again before every single call (below).

`--canary` builds two rooms whose memory file holds a canary sentence, one memory-on and one memory-off (a room has one memory condition, and the shim refuses a call that contradicts it). It first plants a decoy auto-memory index in the on room's config dir and requires the per-call guard to refuse it, without any call, then removes it. Then it makes two audit calls, one through each room's own `claude`, which ask the model to quote every memory or instruction file it was given. Memory on must show the canary file only, memory off must show nothing, neither may show a persona marker, memory off must stay under 4,000 context tokens, and memory on may add only the canary file's few hundred. On the calibration host on 2026-11-07 the two calls used 2,172 and 1,993 context tokens. A real persona adds about 40,000 to a call made under its owner's own config.

## The per-call rules (shim.py)

probatio calls `claude` and `codex` by name, so the room's `bin` wins and every call passes through `shim.py`.

**A Claude Code print call** goes through these steps, in order:

1. **Hard stop.** Refused, without calling the model, while `STOP` exists.
2. **Context.** The call's own context is checked against the room contract (`guard.py`), and any problem writes `STOP`, since the room is no longer what it was registered as. Checked are:
   - the directory it runs in (the room's working directory) and every parent of it;
   - the config dir's contents;
   - the memory file's exact digest or absence;
   - its environment (the room's config dir, no provider variable, and the CLAUDE.md switch `CLAUDE_CODE_DISABLE_CLAUDE_MDS` saying what the room's condition says: absent in an on room, absent or 1 in an off room);
   - its arguments, against the exact command contract probatio and the canary use (`guard.CLAUDE_OPTIONS`): `-p`, `--tools ""`, `--strict-mcp-config`, `--no-session-persistence`, `--settings '{"disableAllHooks":true}'` and nothing else in it (a settings `env` could switch memory), `--output-format`, `--input-format`, `--verbose`, the registered `--model` and `--effort`, and the world's `--mcp-config`, `--max-turns` and `--allowedTools`. Each at most once. The one MCP server a call may name is probatio's own world.py, as `start_episode` runs it: the interpreter the harness runs probatio with, `-I -B`, world.py beside the run's probatio.py with the sha256 recorded when the room was built (read again at every call, and part of the run's fingerprint), and the world, record and close files of one episode folder directly under the run's `episodes/`. `--allowedTools` may name only that server's tools (`mcp__<server>__<tool>`). Any other option is refused, so nothing can add instructions, directories or settings.
3. **Quota.** Refused while the account's scheduler says pause (below). A scheduler that reports too much unusable telemetry in a row writes `STOP`. In a staged run, refused with `STOP` once its stage has reached a cap (below, "Stage gates").
4. **Rules re-applied.** Auto memory is forced off, CLAUDE.md memory is forced off in an off run and cleared in an on run, and the effort and the updater switch are set again, whatever the caller passed. probatio v3 clears the auto-memory switch in its with-memory condition, and an enabled auto memory adds about 700 tokens of instructions and loads any index it finds.
5. **Protocol.** `--output-format json` becomes `stream-json --verbose`, and the stream is checked:
   - an init event must come before any reply, with `apiKeySource: none` and exactly the pinned build (`2.1.3090` is not `2.1.309`), or the call is killed and `STOP` written;
   - a later turn of a stream-json dialogue may send init again (Claude Code 2.1.309 does, with the same session, at every later turn: found live in the ordo pilot's Stage 1, `docs/ordo/PREREG.md` Amendment 4). The repeat is accepted only when it names the first init's session, key source, build and model. A different key source or build is refused with `STOP`, and any other difference holds that turn's result as unverified. `tests/test_shim_dialogue.py` replays six real two-turn streams;
   - the init must name the registered model;
   - every `rate_limit_event` goes to the scheduler, and at least one must be usable telemetry (both windows with a finite utilization and reset time);
   - the result's `modelUsage` must name exactly the registered model. `local.json` may register `auxiliary_models`, accepted only beside it (none by default: every recorded real result named one model);
   - a 429 or usage-limit result with no event counts as a limit reached.

   A call that fails any of these is not passed on. It comes back as unverified (exit 76), the scheduler records it, and dispatch holds (below). If reading the stream fails in any other way, the child is ended and the call is still recorded as unverified before the error leaves; when even that cannot be recorded, the shim writes `STOP`. A fault in the shim can never leave dispatch running on telemetry nobody saw.
6. **Result.** An accepted result is printed back as the one JSON object probatio expects. A caller that asked for `stream-json` itself (probatio v4) gets every event as it arrives, except a successful result: the shim checks the stream so far first and releases the result only if it passes, so no unverified success is ever printed. Claude Code sends the init and the `rate_limit_event` before the result (in every recorded stream), so a valid result is released at once, which probatio's errand dialogue needs: it answers the first result on stdin.

**A Codex exec call** is checked against the contract and against the `CODEX_HOME` it actually receives. probatio makes a fresh one per call; it must lie inside the room or the run's folder, hold only `auth.json`, and that `auth.json` must lead to the approved login. Its arguments must fit the exact command probatio v3 and v4 build (`guard.CODEX_OPTIONS`): `exec` or `exec resume <session>`, the prompt on stdin (`-`) only, `--ignore-user-config` and `--ignore-rules` present, a read-only sandbox, `--color never`, features disabled by name, the registered model, `-o` for the reply, `--json`, and `-c` overrides from a fixed set only: web search off, the read-only sandbox, the registered reasoning effort, `project_doc_max_bytes=0` in an off room (never in an on room), and the world's MCP server, pinned exactly as for Claude Code (its command and arguments, one server only, approval mode `approve`). `-C`/`--cd`, `--add-dir`, a profile, `developer_instructions` or any other key is refused. Any problem writes `STOP`. Before it runs, the call is reserved against the Codex arm's cap of 1,000 charged requests (`CALIBRATION.md` section 7; `grid.CODEX_CAP` is the one home of the number) in the account ledger, atomically under the ledger's lock, so concurrent calls on any host can never pass it. At the cap, or with the ledger out of reach, the shim writes `STOP`. It gets the registered effort and, in an off room, project docs switched off. Codex runs without the Claude token or any OpenAI key in its environment.

**Redaction.** Every byte a child writes, stdout and stderr alike, passes the redactor (`redact.py`) on its way out, and so does every message, `STOP` reason, refusal, published unverified reason and line of `harness.log`. The redactor removes the exact values of every secret-named variable in the environment (the in-memory token above all) and anything shaped like an Anthropic key. Since it matches exact values, a text that must be shortened (a version line, a stderr tail) is redacted whole before it is cut (`Redactor.clip`): cutting first would leave a shortened secret it can no longer recognise. The shim never lets a child's stderr bypass it.

`STOP` halts every run on the host until a person reads it and deletes it. The harness never deletes it.

## The quota: one scheduler for the account

Claude Code reports the subscription's five-hour and seven-day utilization, in steps of 0.01, with every call. Every host of the calibration draws on the same subscription (two hosts share one Max account), and so do the account owner's own seats. So pause and resume are one decision for the account, made by `scheduler.py` from the newest reading of any host:

- **The ledger** (`account.py`) is one small state file on one host, `account.json` beside `account.lock` in `quota.home_dir`. It holds the account's quota meter (`meter.py`), the account's two limits, when each host last published, the account's burn, the calibration's stage book, and the two registered quota states below (`recovery` and `study_stop`). Every operation takes the lock: exclusive to write, shared to read. A version 1 ledger (part 2's, one newest reading) is read as a meter folded from that reading.
- **Finite numbers only.** NaN, the infinities, an integer too large for a float (JSON allows `10**1000`) and any value beyond `meter.METER_NUM_MAX` count as missing wherever they appear: a utilization, a reset time, a reading's time, a rejection's reset, a `quota.jsonl` line, a published reading or a ledger state. The check never raises. So a malformed value can never stand in for a reset that has passed: the reading is unusable telemetry and holds.
- **The meter** keeps each window apart: its high-water mark, its reset time, when it was last seen and by which host. A reading of the same window can only raise the mark. Only a reading of a later window (a later reset, or one taken after the known window ended) lowers it. An older window's reading, or one that leaves a window out, changes nothing. So no partial, stale or reset-less reading can clear a hold.
  - A high meter with no reset time holds for 15 minutes after it was seen, and then one call may probe.
  - Unusable telemetry (an unverified call, or a reading without both windows) marks the meter unknown and holds for 5 minutes; three in a row write `STOP`.
  - A rejection holds until its own reset time.
- **The home host** keeps the ledger and sets `"home": "local"`. Every other host sets `"home": "user@host"` and reaches the ledger over ssh by running `account.py` there with `python3 -`, its source and `meter.py`'s on stdin. The home needs no copy of the harness, and the ssh connection is reused for two minutes.
- **Every call** goes through the room's shim. It appends its reading to this host's `quota.jsonl` and publishes it to the ledger, every reading, since the stage burn (below) sums each one; reads of the ledger are cached for 30 seconds, so ssh stays off most of the decisions. Before the next call it asks the scheduler, which combines the ledger's meter with this host's own readings. No new call starts while the five-hour window is at or above its limit, while the server says the limit is reached, or while the telemetry is unknown. The five-hour hold lasts until its `resetsAt` plus a minute. `run.py` asks the same scheduler between attempts and records which host's reading paused it.
- **Resuming after a five-hour pause** (`CALIBRATION.md` 7: "it resumes when the window has reset below 0.40"). When the five-hour window reaches its limit, the ledger sets a recovery mark. Once that window has reset, calls may probe, and the first reading of a later window decides: below 0.40 (`account.RESUME_FIVE_HOUR`) clears the mark and the study resumes; at 0.40 or above the study holds again until that window resets, and so on until a post-reset reading is below 0.40. Once resumed, the ordinary 0.60 limit applies again.
- **The weekly stop** (`CALIBRATION.md` 7: "the study stops and waits for Ephraiem's go"). When the seven-day window reaches its limit, the scheduler asks for a hard stop at once: `run.py` and the shim write `STOP`, and the ledger latches `study_stop`. No reading, reset, resume or new run lifts it: a later weekly window does not, and `isolate.py --check` refuses every run while it stands. After his go, the operator releases it on the ledger's home, with the reason recorded, and removes `STOP`:

  ```
  python3 calibration/account.py --home-dir /var/tmp/probatio-cal/account --release-study-stop "Ephraiem's go, <date>"
  rm /var/tmp/probatio-cal/STOP
  ```

  Hosts see a release within the 30-second cache. A release covers the weekly window it was latched in: within that window the 0.60 pause still holds until the window resets, unless the limits are raised (`--set-limits`), and a later window that reaches the limit latches the stop again. The harness never releases it.
- **One pair of limits.** The ledger holds the limits, so every host uses the same pair. A host's `local.json` seeds them when the ledger has none, and `isolate.py --check` refuses a host whose `local.json` disagrees with the ledger. To change the pair for the whole account, run `python3 calibration/account.py --home-dir <dir> --set-limits <five_hour> <seven_day>` on the home.
- **If the home is out of reach** in the middle of a run, the scheduler decides from this host's own readings and its last cached copy of the ledger, and says so (degraded). That stays sound, because every call returns a fresh reading of the whole account: a host that loses the ledger still pauses on the account's state, one call later than it otherwise would. Its readings stay in `quota.jsonl`, and its next successful publish brings the ledger up to date. At the start of a run, an unreachable ledger is a refusal.

**Default limits: five_hour 0.60, seven_day 0.60.** They leave the account owner daily-use headroom; part 1's 0.85 and 0.80 did not.

- **Seven-day window.** On 2026-11-07 a heavy day of his own agent work used about 0.07 of the seven-day window. A calibration that stops at 0.60 leaves him 0.40, about five more heavy days before the weekly cap could bind, which covers the rest of most weeks.
- **Five-hour window.** It resets every five hours, so stopping it at 0.60 mostly costs calibration pace, and it leaves 0.40 of each window for a burst of interactive work.

Lower them for a week of heavy own use, and raise them, with `--set-limits`, for a week the account is left to the calibration.

The `quota` block of `local.json`:

| key | meaning |
|---|---|
| `home` | `local` on the ledger's host, `user@host` on every other (for example `user@first-host` on the second host) |
| `home_dir` | the ledger's directory as the home names it (default `<root>/account` on a local home; required on the others) |
| `host` | this host's name in the ledger (default its hostname) |
| `five_hour`, `seven_day` | the limits this host seeds and expects (default 0.60 and 0.60) |
| `cache_seconds` | how long a cached copy of the ledger is trusted before the next read (default 30) |
| `ssh_timeout`, `ssh` | the ssh call's time limit in seconds (default 15) and the ssh command (default `ssh`) |

A `quota.jsonl` reading: `{"t", "host", "run_id", "status", "type", "resets", "five_hour", "five_hour_resets", "seven_day", "seven_day_resets", "valid"}`. An unverified call adds `{"t", "run_id", "status": "unverified", "why"}`.

## One run

```
python3 calibration/run.py spec.json [--local calibration/local.json] [--dry-run]
```

A spec (grid.py will write them; until then by hand):

```json
{"run_id": "sonnet-L4-organization-kelthorne", "agent": "claude", "model": "claude-sonnet-5-5",
 "memory": "on", "memory_file": "rendered/L4.organization.kelthorne.md", "jobs": 4, "effort": "medium",
 "cli": "v4", "probatio_args": ["--target-type", "organization", "--target", "Kelthorne Labs", "--tier", "standard"]}
```

`memory_file` is relative to the spec's folder, or null. `cli: "v3"` drives probatio v3, which has no `--memory` and asks both conditions in one run; it is for smoke tests only.

**Forwarded arguments.** These are checked against an allowlist of exact flag names: `--target-type`, `--target`, `--name`, `--target-short`, `--target-possessive`, `--tier`, `--arm`, `--class`, `--dataset`, `--seed` and `--local`. Each is allowed once, with one value that does not start with a dash. Anything else is refused: an abbreviation probatio's parser would accept (`--mod`, `--jo`), a short option, a duplicate, or any of the harness's own `--agent`, `--model`, `--workdir`, `--out`, `--jobs` and `--memory`.

**The registration.** The runner first computes the run's fingerprint: agent, model, effort, memory condition and digest, CLI contract, jobs, the normalised arguments, and the pinned Claude Code, Codex and probatio identities. Each identity is the executable as the run will execute it, a command name such as `codex` looked up on PATH the way the shim looks it up, with its real path and sha256, so the same name and version with other bytes is another identity. A resume whose fingerprint differs is refused before the room or the record is touched. The refused attempt is logged in `rejected-resumes.jsonl`, and the registration stays as it was. The fingerprint is computed again before every launch, and a change ends the run as refused.

**The preflight.** It builds and checks the room, then runs `probatio.py --dry-run` and refuses unless:

- the listed memory is exactly the run's file (none for an off run under v4);
- the dry run reports the registered model and jobs;
- it states how many calls it plans.

It then runs probatio in the room. Before every launch, the first included, it checks for a hard stop and checks the room again. After each attempt it decides:

| after an attempt | step |
|---|---|
| every planned call answered, none failed | done |
| `STOP` exists | stopped |
| the quota says pause | sleep in steps of at most a minute, checking for `STOP` at every step, until it clears; then rerun the same command (probatio resumes and retries the failed calls); not counted as a failure |
| some calls failed, or planned calls are missing | rerun after 2 minutes times the failure count, up to 6 attempts, then failed |
| probatio exited nonzero with no failed call (it refused to start) | failed at once |

Everything goes into `calibration.json` in the run's folder: the registration (fingerprint and canonical spec, the stage included), the memory file and its sha256, the effort, the Claude Code version, the planned call count, the stage gate's decision, each attempt, each pause and the outcome. probatio's output reaches `harness.log` only through the redactor. probatio's own `run.json`, `replies.jsonl` and scorecard sit beside them.

## Stage gates

A spec `grid.py` wrote carries its `stage` and is run from its plan folder (beside the plan's `manifest.json`). `stages.py` gives grid.py's pure rules their inputs and enforces their answers (`CALIBRATION.md` sections 6 and 7; `STAGES.md` describes the rules):

- **Before the run starts** (a resume included, before anything is created), `grid.eligibility` must find it ready, from the plan, the plan's `control.json` and the `calibration.json` of every planned run. `control.json` holds only what the operator alone can attest: `stage_a_complete`, `codex_verification_pending` (true until it says otherwise) and `predictions`, each stage's predicted five-hour and seven-day points from Stage A. Without it, only Stage A can start. B1's verdict is computed by `analyze.py` from the 15 finished B1 runs, never typed in. The run's stage is then opened in the account ledger with the caps `grid.stage_caps` fixes: A's fixed 20 and 5 points, otherwise 1.5 times the prediction up to the ceiling; an unknown or over-ceiling prediction is refused. A stage opens once: its baseline is the account's burn at that moment, and a resume keeps it. A dry run checks the same but opens nothing.
- **Before every launch, and in the shim before every charged call,** `grid.cap_status` checks the stage's burn since it opened. The burn is `CALIBRATION.md` 7's summed positive increments, kept apart from the high-water marks that drive the pauses (`meter.py`): the ledger places every reading by its time among the window's recent readings and adds each reading's rise over the one before it (a different window's whole reading, nothing for a fall), so a fall and a rise again within one window both count. A reading that arrives late is placed by its time; one too late to place is counted as rising from zero, so the burn never undercounts. It is every seat's use in that time, an upper bound on the stage's own. At a cap the run stops with `STOP` and waits for Ephraiem's go, and so it does while the ledger is out of reach, since the burn cannot be counted then. A stage can pass a point cap by at most the burn of the calls already in flight plus what the scheduler's 30-second cache has not yet seen; Codex requests are countable, so each is reserved (above) and cannot pass the cap at all.

What his go does at a cap (raise the cap by how much, or end the stage) is not registered; nothing in the harness gives it.

## The ordo pilot

`ordo_pilot.py` runs ordo's frozen pilot (`docs/ordo/PREREG.md`) through this room unchanged; `docs/ordo/PILOT_RUNBOOK.md` gives the commands in order. Its arm-runs cannot be run.py specs: an arm's items come from the plan's private item filter, which no public probatio flag expresses. So `ordo_run.py` runs one arm-run as run.py runs a spec, keeping run.py's identities and preflight parsers, the same scheduler and the same STOP. The child is `ordo_arm.py`, launched inside the room, and it runs probatio's own cross-type path. Two rules are stricter than run.py's: a quota ledger that cannot be read holds every launch and every call (each pilot room carries the policy `{"ledger": "required"}`, under which the shim reads the ledger afresh, never from its cache, immediately before the CLI starts, and refuses to launch when it cannot; v4's rooms carry no policy, so their rule is unchanged), and an error after probatio's one retry is final (PREREG 8). Results go to the private folder that `local.json`'s `ordo_pilot_results` names, outside every git tree.

## Tests

```
python3 -m unittest discover -s calibration/tests
```

They use fake Claude Code, Codex and probatio executables (`tests/fakes.py`) and a scratch host in a temporary folder, so no test calls a model or touches the host's config. They cover:

- the isolation refusals and the room contract;
- the environment;
- the token never reaching a file or any output, including a fake CLI that echoes it on stdout, stderr and in its result;
- the shim's rules and its stream protocol (missing, out-of-order or malformed events), plus a recorded real Claude Code 2.1.309 stream (`tests/fixtures/stream-2.1.309.jsonl`) that must be accepted;
- the Codex cap (1,000, reserved atomically in the ledger) and per-call home;
- the stage gates end to end (`tests/test_stages.py`): a plan from `grid.py`, its control file, real records, a fake probatio calling through the shim, and a fake Claude Code with a rising meter, stopped at Stage A's cap;
- the meter's rules and the scheduler across two hosts;
- the registered quota policy (`tests/test_quota_policy.py`): the weekly stop latched through a reset and lifted only by a release, the resume only below 0.40 after a five-hour pause (at 0.40 and just below), and the burn as summed positive increments, end to end through `run.py` and the shim;
- strict arguments and the preflight;
- registration, coverage, and pause, resume, retry and stop with an injected clock.

`tests/test_regressions.py` turns each of Codex's probes against part 1 into a test. Each of them fails on part 1's commit `bb38240` and passes now. `tests/test_reverify.py` does the same for its re-verification's probes: each fails on `c804818` and passes now. `tests/test_reverify3.py` does it for the third re-verification (of `4e5bd0e`): a long secret cut before redaction, an oversized integer in the quota telemetry, and Codex's unregistered-MCP probe, which the integration's world-server pin had already closed.

## Decisions to confirm in review

- **Effort.** Every Claude call runs with `CLAUDE_CODE_EFFORT_LEVEL=medium`. This variable outranks `--effort` and the settings: with `--effort low` on the command line, `max` in the environment quadrupled the thinking. On question calls, low and medium gave the same speed and 41 of 42 identical answers; medium keeps the multi-step errands competent. `CALIBRATION.md` section 4 says "default effort". Either amend it to medium, or set `effort` to `auto` in each spec, which pins the model's default explicitly.
- **One config dir per run**, not one per host as section 5 reads. One dir served 240 calls at `--jobs 8` without errors, and per run keeps each run's state apart.
- **The token.** On the calibration host the main `CLAUDE_CODE_OAUTH_TOKEN` is empty in the secrets file, so `token_name` there is the backup slot's variable. Its quota readings match the stored login's exactly, so it is very likely the same account. Moving to another slot does not add quota.
- **The Codex login link** is shared with the host's real login. If Codex refreshed its token inside a call's home by replacing the file, the host's copy would keep a rotated-out refresh token. Two calls did not touch it, but check `codex_auth`'s modification time after the Codex arm.
