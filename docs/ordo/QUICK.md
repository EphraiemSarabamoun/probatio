# probatio: the quick tier, the smoke tier and the measured times

**Status:** built 2026-11-08 on branch `ordo-tiers`, from the estimate-and-watch design's revision 2, kept in the review record (the manager's calls in `MANAGER_DECISIONS.md` beside it).
- **The interval rule.** The role-free errand rows' intervals follow PREREG.md Amendment 6 (Stage 0b's candidate C).
- **The evidence.** `docs/ordo/errand_rule.json` holds it, and `ordo.errand_rule_status()` reads adopted only while that evidence is complete and current.
- **The numbers below** are that evidence's: Stage 0b for the rule, and `docs/ordo/quick_sim.json` for the allocation.

## What quick runs

quick is the estimate sized to about 15 minutes for one condition on the Sonnet baseline: Claude Code 2.1.309, claude-sonnet-5-5, effort medium, `--jobs 4`. It has no items of its own, only parts of the other tiers.

| selection | Class I | Class II | not run | units | Sonnet baseline |
|---|---|---|---|---|---|
| one target | the standard tier's 264 questions | the full tier's 48 tasks × target and control = 96 N0 errands | Class III | 360 | 14.3 min |
| cross-type triangle | the standard tier's role-free items (F1.a to F1.c, F1.tri) and checks (V12, RF1, RF2, RF4), 276 in all | role-free N0 errands on 32 tasks of the full pool × 3 beneficiaries = 96 | the authority frame and Class III | 372 | 14.6 min |

- **Where it lives.** probatio.py's `TIER_PARTS` and `TIER_CLASSES` define one target, ordo.py's `TIER["quick"]` and `QUICK_TASKS` define the triangle, and probatio.py's `TIER_NAMED_CLASSES` a named target's classes.
- **Class III.** A `--class` naming III at the quick tier is refused: "a quick run does not run Class III; use --tier standard for it".
- **Against standard.** quick costs no more than standard for the default classes and for every cross-type selection, named targets included. A single target with `--class II` or `--class I,II` is the exception, because quick asks the full tier's 48 errand tasks where standard asks 24: its preflight says "for this selection --tier standard is shorter: about 5 min" (9 for I,II), and the time hint never offers quick there.
- **Named targets** (recipe quick@3) get their Class I questions, the standard tier's twin-matched F1 questions, and none of their errands, which are at `--tier standard` (see "Named targets" below). The preflight's named-target line says so, the scorecard's named-target Class II reads "not run in a quick run", and a quick `--class II` that would leave a named target nothing is refused with that fix.
- **The preflight** prints each selection's own estimate, with a label saying whether its rates are measured, assumed or a proxy. Memory both, Opus, Codex and named targets take longer.
- **The scorecard** says in one line what the run left out, and prints the run's actual time from `launches.jsonl`.

## smoke

smoke is the plan called quick until 2026-11-08, item for item: `smoke@1`, with fingerprint `005a5fcb` for one Mozilla target and `2da661c7` for the triangle. The v4 calibration's Stages A and C and the frozen ordo pilot's Stage 1 registered it (PREREG.md Amendment 5).

The pinned manifests still name its task counts `quick`, and `MANIFEST_TIER` maps the plan name to them. Users are not offered smoke; `--dry-run` is their setup check.

## Plan recipes

A run records its plan's recipe beside its tier word: `smoke@1`, `quick@3`, `standard@1` or `full@1`.
- **Retired recipes.** `quick@2`, the first release's quick, also ran a named target's errands on all 32 tasks. A run that records it still reads and scores. It resumes when today's plan is the same call for call, which holds for every quick@2 run without a named target (`same_plan`); otherwise the refusal says why. A resumed one goes on under its recorded recipe, so its scorecard, its page and its run.json all read quick@2.
- **Old runs.** A run.json from before recipes has none, and `recipe_of` reads it through `LEGACY_RECIPES`: quick reads as `smoke@1`, and standard and full read as themselves. No historical file is edited.
- **`rescore`** rebuilds a run by its recipe, so a legacy quick run rebuilds as smoke, to its own fingerprint.
- **`check_resume`** compares recipes (`resume_view`). A legacy quick folder resumes under `--tier smoke`, and under `--tier quick` the refusal names that fix.
- **`comparable`** compares recipes, so "ran different plans (smoke@1, quick@2)" is refused.
- **The scorecard heads** print the recipe's tier word, with "(recorded as quick)" when the run's word differs.
- **New recipes.** A change to any tier's rules bumps its recipe and keeps the old one plannable.

## The measured times

`docs/ordo/timing.py` reads the pilot's four kept runs, which hold 1,286 calls with 0 errors and 0 retries, and writes `docs/ordo/timing.json` (aggregates only). Their means are `docs/ordo/budget.py`'s sonnet profile, and a test holds the two equal.

| kind | n | mean s |
|---|---|---|
| question: survey, obey, devotio, salus | 612, 60, 188, 90 | 3.91, 3.65, 4.03, 4.63 |
| errand with its report: N0, U, O | 246, 60, 30 | 24.73, 26.80, 24.76 |

Wall time is the summed call seconds over `--jobs`; the measured efficiency at `--jobs 4` is 0.99. The Opus and Codex profiles keep today's assumptions, which are v3's per-instrument means and 45 s and 60 s an errand, and are labelled assumed wherever they print. `probatio.rates_profile` picks the profile:

| context | rates and label |
|---|---|
| Claude Code, a Sonnet 5.5 model id, effort medium | measured Sonnet |
| Claude Code, no `--model` | Sonnet's, named as the baseline's |
| Claude Code, an Opus model | assumed |
| any other Claude model or effort | Sonnet's, as a proxy |
| Codex | assumed |

## Why this allocation

`docs/ordo/quick_sim.py` is Stage 0's method on a menu of candidate plans within 15 baseline minutes. It uses:
- the real planners on the pinned banks;
- the real scorers, whose role-free errand rows print Amendment 6's interval through `ordo.errand_interval`;
- Class I item heterogeneity set by the pilot's retest agreement (132 of 147 identical prompts, 0.90);
- errands drawn from the pilot's own N0 episodes per task.

The chosen plans are the **minimax choice among that menu under that model**: the plan whose wider headline interval is narrowest. They are not shown to be optimal beyond it. Codex re-ranked the menu independently under the earlier rule and reproduced the choice, and the rerun under Amendment 6 (`docs/ordo/quick_sim.json`, 500 replicates per cell) chose the same plans.

**The rule and its gate.** Stage 0b (`STAGE0.md`, `stage0b.csv`, 10,000 null runs per model and tier) found Amendment 3's widening too narrow on the role-free errand rows. Their false relations were 16.9 to 17.6% at standard and 15.1 to 15.6% at quick, against at most 15%. Codex's independent check gave 17.76%. Candidate C, Amendment 6, met every target, with false relations of 11.2 to 12.9%.
- `ordo.errand_rule_status` reads both artifacts itself and takes no verdict on trust. It reads adopted only when all of these hold:
  - `docs/ordo/errand_rule.json` names that amendment, rule and rows, and PREREG.md carries the amendment;
  - its scorer fingerprint is today's. `ordo.rule_fingerprint` covers every function and constant the two artifacts' numbers depend on, traced through the scorers' code, bootstrap and row dispatch included;
  - Stage 0b's csv, by sha256, holds every cell of the acceptance once for candidate C: two tiers, two models, their rows and seven targets each, 98 cells. Each rate meets its frozen target as a number, each count and Monte Carlo error agrees with 10,000 replicates, and the seeds, B, replicates and three candidates are the ones Stage 0b ran at commit 78890f8;
  - the allocation, by sha256, ran under the rule with today's scorers and holds every candidate of both menus. In each menu the minimax winner, recomputed from its widths, is the quick plan, simulated on the plan the planners make today.
- `docs/ordo/rule_evidence.py` writes the evidence, and refuses an artifact the gate would refuse.
- Anything missing, stale or malformed reads PENDING, with each problem named. While it does, a run's answer gives the role-free errand rows no width or tier advice. The Class II precision claims below are marked as gated in this file's source, and a test fails if any remains while the gate reads PENDING.

<!-- gated: errand rule -->
Median interval widths under the null model (`docs/ordo/quick_sim.json`):

| one target | Sonnet min | Class I, raw | Class II, raw |
|---|---|---|---|
| smoke | 6.6 | 34.7 | 70.9 |
| standard | 17.2 | 21.2 | 59.3 |
| I standard + II 24 tasks | 9.4 | 22.1 | 58.1 |
| I standard + II 40 tasks | 12.7 | 22.2 | 48.2 |
| **I standard + II 48 tasks (quick)** | **14.3** | **21.6** | **44.1** |
| I full + II 24 tasks | 13.8 | 16.1 | 59.3 |

One target's Class II is v4's own index and interval, which Amendment 6 does not touch. quick asks the full tier's 48 tasks there, the same design as full.

| triangle | Sonnet min | role-free I | role-free II: width, share over 40, false relations |
|---|---|---|---|
| standard | 27.4 | 16.8 | 43.7, 61%, 10.6% |
| role-free I + checks + N0 24 | 12.1 | 16.7 | 35.2, 26%, 14.0% |
| **role-free I + checks + N0 32 (quick)** | **14.6** | **17.0** | **29.0, 4.5%, 12.4%** |

Amendment 6 widens the standard tier's role-free Class II, with its 16 tasks, by about 15%: that row is now too wide to rank in most standard runs. quick's 32 tasks keep it readable. These are null-model medians from the stated response model; a run prints its own intervals.
<!-- /gated -->

**Class III is not run in a quick run** for three reasons:
- **Cost:** it takes 7.8 minutes at the standard tier's counts.
- **Precision:** at smoke's counts its interval is too wide to read.
- **Validity:** on Sonnet the pilot's manipulation check failed (not_registered), and every Codex run reads unverified.

Under Amendment 6 its interval is wider still, so that holds.

    python3 docs/ordo/quick_sim.py <pilot results folder> --replicates 500 --jobs 18 > docs/ordo/quick_sim.json
    python3 docs/ordo/rule_evidence.py docs/ordo/quick_sim.json --out docs/ordo/errand_rule.json

## Named targets

At quick a named target gets its Class I questions and none of its errands (recipe quick@3). quick@2, the first release's quick, ran its errands on all 32 of quick's tasks, about 11 minutes a target, so three named targets made quick cost more than the standard tier: 47 minutes against 45. `docs/ordo/named_sim.py` priced and scored the alternatives under quick_sim's null model, with one named organization in the triangle and 500 replicates (`docs/ordo/named_sim.json`). A false effect is a 95% interval that excludes 0 when the target is exchangeable with its type's invented entity.

| the named target's plan | Sonnet min | Class I effect: width, insufficient | Class II effect: width, false effects |
|---|---|---|---|
| Class I as quick asks it, errands on 32 tasks (quick@2) | 25.4 | 15.6, 70% | 32.6, 4.8% |
| Class I as quick asks it, errands on 16 tasks | 20.5 | 16.7, 72% | 41.2, 8.4% |
| the set-1 pairing only, errands on 8 tasks | 17.5 | always insufficient | 43.8, 15.2% |
| the set-1 pairing only, errands on 4 tasks | 16.3 | always insufficient | always insufficient |
| **Class I as quick asks it, no errands (quick@3)** | **15.5** | **15.6, 73%** | **not run** |
| the standard tier, for comparison | 33.3 | 16.1, 74% | 42.8, 8.4% |

- **Why no errands.** The errand counts quick can afford near 15 minutes give a false effect in 15% of null runs, or no effect at all. 16 tasks match the standard tier's precision at about 6 minutes a target, so a run that wants a named target's errands should use `--tier standard`. The preflight's named-target line says so, and the scorecard's named-target Class II reads "not run in a quick run".
- **Why not the set-1 pairing alone.** With 14 questions per pair (the count SCORING 5 gives the smoke tier), each component holds too few questions, and the Class I effect is insufficient in every run. quick asks the standard tier's 28 per pair, the same twin-matched questions.
- **A property of every tier.** In this model each base item prefers its first-listed option with a mean share of 0.7 (Stage 0's position model). Under it, a named target's Class I effect reads insufficient in about 7 runs of 10 at every tier, standard included. Its components hold a quarter of a pair's items, and the position band of SCORING 1 refuses them. The named effects keep the plain percentile interval (SCORING 7), which at 16 tasks gives false effects in about 8% of null runs.

    python3 docs/ordo/named_sim.py <pilot results folder> --replicates 500 --jobs 18 > docs/ordo/named_sim.json
