# Staged calibration plan

`grid.py` builds the preregistered 50-run plan from `docs/v4/CALIBRATION.md` sections 2, 6 and 7. It never calls a model. Supply the Codex model recorded at the freeze:

```sh
python3 calibration/grid.py --codex-model MODEL_RECORDED_AT_FREEZE --out /fresh/plan/directory
```

`--dry-run` prints the plan without writing any files, even if `--out` is supplied. The normal command prints the deterministic specs and writes `manifest.json`, one JSON spec per run and rendered memory audits. Existing output directories are refused. All generated artifacts belong outside source control. Every spec records its stage, level, canonical target, fixed medium effort, planned calls, seed policy and memory hash. `--seed` is deliberately absent: that flag would select one task in v3, whereas the registered study shares the pinned bank's tier task pool across levels. One RNG seeded 20261108 shuffles each stage once in A/B1/B2/H/C order; aggregate analysis uses 20261109.

| Stage | Runs | Tier | Gate |
| --- | --- | --- | --- |
| A | 5 Sonnet, 2 Haiku, 1 Opus | smoke | mechanics and metering only; excluded from analysis |
| B1 | 15 Sonnet | standard | A complete, mechanics and burn accepted, preregistration frozen |
| B2 | 18 Sonnet | standard | B1 verdict neither fail nor not evaluable |
| H | 5 Haiku | standard | B1 complete |
| C | 4 Codex | smoke | all required Claude runs complete and pending Codex verification cleared |

smoke is the plan called quick until 2026-11-08, item for item: probatio's quick tier is now the 15-minute estimate, so the setup plan was renamed with no item, count or cap changed (docs/ordo/PREREG.md Amendment 5; `calibration/tests/test_grid.py` holds the specs equal apart from the word).

`eligibility(spec, records, now, ..., specs=plan)` is a pure dependency check. Pass completed `calibration.json` records keyed by run ID. It checks finite recorded completion timestamps, seeded stage order, the B2 verdict gate and the attested Stage A freeze. Retests must start at least 43,200 actual seconds after their B1 original **completed**, a conservative interpretation of the registered original-to-retest interval. Planned times and a `dry run` outcome cannot satisfy it. B2 is skipped after a failed or not-evaluable B1; C can then follow H. An unknown B1 verdict or unknown Codex verification state blocks C.

`stage_caps(stage, predicted)` freezes limits in percentage **points**. A is 20 five-hour points and 5 seven-day points. B1/B2 use 1.5 times Stage A's prediction, capped at 300/30, and H at 100/10. Unknown, nonpositive, nonfinite and over-ceiling predictions are refused. Predictions and observed increments are separate inputs. A stage at a cap stops and waits for Ephraiem's go. The runner's control plane, not grid generation, owns that authorization. Stage A's metering block declarations and optional extra block (at most 5 five-hour points, predeclared if readings are inconclusive) are manifest metadata; they never silently expand A's hard cap.

`cap_status(stage, usage, predicted)` checks **summed positive increments** before another call. Missing or malformed counters block. C uses a study-arm-wide integer count of every actually charged `codex exec` invocation, including report legs and retries, and stops at 1,000. The four runs plan 560 question calls plus two invocations for each of 160 errands, or 880. Each invocation is reserved atomically: checking the counter without a reservation would permit concurrent overshoot.

These functions do not launch or authorize a run. `stages.py` wires them in (calibration `README.md`, "Stage gates"):

- `run.py` calls `eligibility` before a staged run starts, with the plan, the records, B1's verdict computed by `analyze.py` from the finished B1 runs, and the attestations in the plan's `control.json` (`stage_a_complete`, `codex_verification_pending`, `predictions`); then it opens the run's stage in the account ledger with `stage_caps`' caps. The stage book (each stage's baseline burn, caps and prediction, and the Codex arm's count) lives in the ledger, so it persists across resumes and every host sees one count.
- Before every launch, and in the shim before each charged Claude call, `cap_status` checks the burn the ledger has summed since the stage opened. Each `codex exec` is reserved in the ledger under its lock, granted only below the cap.
- At a cap the run stops with `STOP` and waits for Ephraiem's go. The account-wide rules apply throughout (calibration `README.md`, "The quota"): no call at five-hour 0.60, resumption only on a post-reset reading below 0.40, and at seven-day 0.60 a latched study stop that only an operator's release after his go lifts.
- The stage burn is the summed positive increments of every reading in time order, kept apart from the high-water marks that drive the pauses (`meter.py`).

A grid spec forwards only flags `run.py` allows: `--memory`, `--auto-memory off` and `--effort` come from the harness, from the spec's `memory` and `effort` fields.

The 27 `ladder/*.md.tpl` files contain cumulative L0 through L7 and PC for all three target types. `ladder.render` performs one replacement pass over supplied canonical entity fields. L7 substitutes X everywhere including affiliation; the run still scores T. Codex changes only the initial heading. L0 and PC retain L1's bytes in the outside-room audit, while their `memory_file` is null and their delivery mode is off; no memory file is delivered to their clean rooms. PC selects `--class II,III --arm g_wish`.

Tests compare template bytes to the document's literal appendices, resolve every level for each registered target and both CLIs, check canonical possessives and alias casing, reconstruct registered call totals, and exercise real-time and quota boundaries without model calls.

The generated grid manifest uses schema `probatio-calibration-grid/1` and lists spec filenames by run ID. It is distinct from the analysis input manifest, whose `runs` entries are objects containing scorecard paths and specs; pass finished result directories to the analyzer, not the grid manifest.
