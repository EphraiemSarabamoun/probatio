# Prototypes behind the v4 design

Read-only scripts that sized the design. None calls a model. They read two kinds of input:

- **recorded anulus runs** (castra run folders with `episodes.jsonl` and per-episode `record.jsonl`, `score.json`, `followup.json`), which live in anulus's private v0.2 runs tree, not in this repository;
- **public Hugging Face configs**, downloaded into a folder as `<dataset>/<config>.jsonl` at the commits `DESIGN.md` pins.

| script | what it computed | used in |
|---|---|---|
| `measures_proto.py <run>` | per cell: solve rate, read-every-page rate, any-detail rate, pages and details per episode, Q1 and Q2 shares, and paired differences | `DESIGN.md` D2 (the v0.4 overlay against v0.5) |
| `index_proto.py <run>` | the H and R indices on v0.5 level 3, neutral and g_wish, with task-bootstrap SDs at 48 and 24 keys | `SCORING.md` section 2 |
| `counts_proto.py <run>` | the exact counts behind `SCORING.md`'s worked examples 1 and 2 | `SCORING.md` section 11 |
| `plan_proto.py <hf-folder>` | that the default target's 264 planned standard-tier Class I items (control Karim Nassar) are all published rows | `DESIGN.md` section 7, `IMPLEMENTATION_PLAN.md` W2 |
| `diff_overlay.py <v0.4.jsonl> <v0.5.jsonl>` | how anulus v0.4's rows differ from v0.5's for one task | `DESIGN.md` D2 |
| `simulate_v4.py <run> [<calibration folder>]` (output `simulate_v4.json`) | the pre-freeze simulations: the symmetric Class I index under the null (exact) and its power, the errand components on recorded episodes, and the staged screen's verdicts in six worlds and the retest, both decided by `calibration/analyze.py` itself on synthetic scorecards | `SCORING.md` 13, `CALIBRATION.md` 10 |

Run with `python3 -I -B`, from this folder for the scripts that import `measures_proto`.

**Revision 2's credit rule.** `measures_proto.episode` (and so `index_proto.py` and `counts_proto.py`) now credits a page or a transaction only from a served, non-error response that returns the requested page or id, and reads every page 1 to `of` as a set. Revision 1 counted every call's arguments, which credited pages and ids that do not exist (Codex's finding 2). The recorded anulus runs hold no such errors, so every number these scripts printed is unchanged.
