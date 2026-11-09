#!/usr/bin/env bash
# apply_ordo.sh <phase 0..7>: build and verify ordo (docs/ordo/DESIGN.md 15, revision 2.1), one phase at a time.
#
# One guard runs every command that executes repository code: fake claude and codex first on PATH answer only the
# metadata probes (--version, codex features list), logged to metadata-calls.log, and log any other call to
# forbidden-calls.log and exit 97. Keys are removed; home, Claude config, Codex home (holding a clearly fake, test-only
# auth.json built at run time, so a dry run can pass v4's login check), caches and the network (every proxy points at a
# closed port; loopback is exempt, for the tests' own local page server) are isolated. The guard's
# own refusal is proved in a child guard with its own log (guard_selftest), so the phase's log stays empty. Frozen
# inputs are checked before and after every phase. The script never pushes, uploads or calls a model.
#
#   SCRATCH=<a folder outside the repositories> docs/ordo/apply_ordo.sh 0      # then 1, 2, ... in order
#   REQUIRE_ALL=0 ORDO_WT=<one builder's worktree> docs/ordo/apply_ordo.sh 5   # a builder's own branch: steps needing
#                                                                              # the other builders' code are skipped
set -euo pipefail

PROBATIO=${PROBATIO:-$HOME/projects/probatio-v4}                       # branch v4, at or after 86e918c
ORDO_WT=${ORDO_WT:-$HOME/projects/probatio-ordo}                       # the integration worktree, branch ordo
CONSILIUM=${CONSILIUM:?set CONSILIUM to the consilium checkout}
SCRATCH=${SCRATCH:?set SCRATCH to a scratch folder outside both repositories}
REQUIRE_ALL=${REQUIRE_ALL:-1}               # 1: every step must run (the integration); 0: skip steps whose code is absent
PHASE=${1:?usage: apply_ordo.sh <phase 0..7>}
GUARD="$SCRATCH/guard/phase-$PHASE"                                     # one guard folder per phase, in scratch
UV_CACHE=${UV_CACHE_DIR:-$HOME/.cache/uv}                               # captured before the guard replaces HOME
UV_PYTHONS=${UV_PYTHON_INSTALL_DIR:-$HOME/.local/share/uv/python}

TRIO_BUILDER_SHA=baed97e3c6f9f4106d9135094e7f1fe4f9ec1bea9d0959ccf9cce731de37ed17   # build_trio.py, FROZEN
BANK_MANIFEST_SHA=df93099ac0d59ffc1c67d0d57cfedb2f5e07a50d5b652ef120211c9d6f1d3514  # v4 bank, pinned by the trio
V4_BASE=86e918cf10b28726677d894e9da285f494fa8e86
BRANCHES=(ordo-bank ordo-options ordo-engine)
TRAILER="Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"

die() { echo "apply_ordo: $*" >&2; exit 1; }
sha() { sha256sum "$1" | cut -d' ' -f1; }

# ---- the guard ------------------------------------------------------------------------------------------------------
guard_install() {   # guard_install <dir>: fake CLIs, empty logs and an isolated home in <dir>
  local d=$1
  mkdir -p "$d/bin" "$d/home" "$d/claude-config" "$d/codex-home" "$d/cache"
  : > "$d/forbidden-calls.log"; : > "$d/metadata-calls.log"
  cat > "$d/bin/claude" <<'EOF'
#!/bin/sh
if [ "$#" -eq 1 ] && [ "$1" = "--version" ]; then
  echo "claude --version" >> "$GUARD_DIR/metadata-calls.log"; echo "2.1.309 (Claude Code)"; exit 0
fi
echo "claude $*" >> "$GUARD_DIR/forbidden-calls.log"; exit 97
EOF
  cat > "$d/bin/codex" <<'EOF'
#!/bin/sh
if [ "$#" -eq 1 ] && [ "$1" = "--version" ]; then
  echo "codex --version" >> "$GUARD_DIR/metadata-calls.log"; echo "codex-cli 0.0.0-guard"; exit 0
fi
if [ "$#" -eq 2 ] && [ "$1" = features ] && [ "$2" = list ]; then
  echo "codex features list" >> "$GUARD_DIR/metadata-calls.log"
  printf '%s\n' "shell_tool stable true" "multi_agent stable true" "apps stable true" "plugins stable true"; exit 0
fi
echo "codex $*" >> "$GUARD_DIR/forbidden-calls.log"; exit 97
EOF
  chmod +x "$d/bin/claude" "$d/bin/codex"
  # a test-only Codex login: v4's dry run checks only that auth.json exists; the token is fake and built here
  local fake="fake-$(printf '%s' FAKE FAKE FAKE FAKE)"
  printf '{"_test_only": "fake login for the ordo build guard; never a real credential", "OPENAI_API_KEY": null, "tokens": {"access_token": "%s"}}\n' \
    "$fake" > "$d/codex-home/auth.json"
}
guarded_in() {      # guarded_in <dir> <command...>: no keys, isolated home and config, offline, that guard's fake CLIs
  local d=$1; shift
  env -u ANTHROPIC_API_KEY -u ANTHROPIC_AUTH_TOKEN -u ANTHROPIC_BASE_URL -u OPENAI_API_KEY \
      -u CLAUDE_CODE_OAUTH_TOKEN -u CLAUDE_CODE_OAUTH_TOKEN_BACKUP -u HF_TOKEN -u HUGGING_FACE_HUB_TOKEN \
      PATH="$d/bin:$PATH" GUARD_DIR="$d" HOME="$d/home" CLAUDE_CONFIG_DIR="$d/claude-config" \
      CODEX_HOME="$d/codex-home" XDG_CACHE_HOME="$d/cache" UV_CACHE_DIR="$UV_CACHE" \
      UV_PYTHON_INSTALL_DIR="$UV_PYTHONS" UV_OFFLINE=1 HF_HUB_OFFLINE=1 HF_DATASETS_OFFLINE=1 \
      HTTP_PROXY=http://127.0.0.1:9 HTTPS_PROXY=http://127.0.0.1:9 http_proxy=http://127.0.0.1:9 \
      https_proxy=http://127.0.0.1:9 NO_PROXY=127.0.0.1,localhost,::1 no_proxy=127.0.0.1,localhost,::1 "$@"
}
guarded() { guarded_in "$GUARD" "$@"; }
guard_check() {     # the phase's own log must be empty: metadata probes are allowed and logged apart
  [ ! -s "$GUARD/forbidden-calls.log" ] || die "forbidden CLI call under the guard: $(head -3 "$GUARD/forbidden-calls.log")"
}
guard_selftest() {  # prove the guard refuses inference, in a child guard whose log is checked for exactly that call
  local child="$GUARD/selftest" rc=0
  guard_install "$child"
  guarded_in "$child" claude --version > /dev/null || die "guard self-test: claude --version failed"
  guarded_in "$child" codex features list > /dev/null || die "guard self-test: codex features list failed"
  guarded_in "$child" claude -p ordo-guard-selftest > /dev/null 2>&1 || rc=$?
  [ "$rc" = 97 ] || die "guard self-test: claude -p exited $rc, not 97"
  [ "$(cat "$child/forbidden-calls.log")" = "claude -p ordo-guard-selftest" ] \
    || die "guard self-test: the child log is not exactly the refused call"
  [ "$(cat "$child/metadata-calls.log")" = "$(printf '%s\n' 'claude --version' 'codex features list')" ] \
    || die "guard self-test: the child's metadata log is not exactly the two probes"
  echo "guard self-test ok (child log: one refused call, two metadata probes)"
}

frozen_check() {    # every pin the published trio and probatio v4 depend on
  local wt=${1:-$PROBATIO}
  [ "$(sha "$CONSILIUM/release/build_trio.py")" = "$TRIO_BUILDER_SHA" ] || die "build_trio.py changed (FROZEN)"
  for ds in persona collegium natio; do
    [ "$(sha "$CONSILIUM/release/build/trio/$ds/manifest.json")" = "$(cat "$SCRATCH/frozen/$ds.manifest.sha")" ] \
      || die "release/build/trio/$ds/manifest.json changed"
  done
  [ "$(sha "$wt/bank/manifest.json")" = "$BANK_MANIFEST_SHA" ] || die "$wt/bank/manifest.json changed"
  grep -q "^BANK_MANIFEST_SHA256 = \"$BANK_MANIFEST_SHA\"" "$wt/probatio.py" || die "BANK_MANIFEST_SHA256 moved in $wt"
  for f in world.py tools/build_bank.py; do
    [ "$(git -C "$wt" show "$V4_BASE:$f" | sha256sum | cut -d' ' -f1)" = "$(sha "$wt/$f")" ] || die "$f differs from $V4_BASE"
  done
  [ -z "$(git -C "$wt" diff --name-only "$V4_BASE" -- bank/src calibration/ladder)" ] || die "bank/src or the ladder changed"
}
begin() { frozen_check "$1"; guard_install "$GUARD"; }
end()   { guard_check; frozen_check "$1"; echo "phase $PHASE ok"; }
has_cross_type() { [ -f "$1/ordo.py" ] && grep -q -- '--compare' "$1/probatio.py"; }
need() {            # need <what>: on the integration (REQUIRE_ALL=1) a missing part is fatal; else the step is skipped
  if [ "$REQUIRE_ALL" = 1 ]; then die "missing: $1"; fi
  echo "skipped on this branch: $1"; return 1
}
with_cache() {      # with_cache <worktree> <command...>: link v4's .cache in for the cache-dependent tests, then unlink
  local wt=$1 linked=; shift
  if [ ! -e "$wt/.cache" ]; then ln -s "$PROBATIO/.cache" "$wt/.cache"; linked=1; fi
  local rc=0; "$@" || rc=$?
  if [ -n "$linked" ]; then rm "$wt/.cache"; fi
  return $rc
}

case "$PHASE" in
0) # preconditions; record the frozen hashes and the index copy that phase 7 rewrites
   mkdir -p "$SCRATCH/frozen"
   git -C "$PROBATIO" merge-base --is-ancestor "$V4_BASE" v4 || die "v4 does not contain $V4_BASE"
   [ -z "$(git -C "$PROBATIO" status --porcelain --untracked-files=no)" ] || die "probatio-v4 has uncommitted changes"
   [ -z "$(git -C "$CONSILIUM" status --porcelain --untracked-files=no)" ] || die "consilium has uncommitted changes"
   for ds in persona collegium natio; do
     sha "$CONSILIUM/release/build/trio/$ds/manifest.json" > "$SCRATCH/frozen/$ds.manifest.sha"
   done
   cp "$CONSILIUM/release/build/trio/DATASETS.md" "$SCRATCH/frozen/trio-DATASETS.md"
   begin "$PROBATIO"; end "$PROBATIO" ;;
1) # the integration worktree off v4, with the three builders' branches merged (a conflict stops the phase)
   [ ! -e "$ORDO_WT" ] || die "$ORDO_WT exists"
   git -C "$PROBATIO" worktree add -b ordo "$ORDO_WT" v4
   begin "$ORDO_WT"
   for b in "${BRANCHES[@]}"; do git -C "$ORDO_WT" merge --no-ff --no-edit "$b" || die "merging $b conflicts"; done
   end "$ORDO_WT" ;;
2) # every builder's files exist (DESIGN 15.1), and the neutral arm is v4's L0 byte for byte
   begin "$ORDO_WT"
   for f in ordo.py docs/ordo/DESIGN.md docs/ordo/SCORING.md docs/ordo/PREREG.md docs/ordo/BANK_FORMAT.md \
            docs/ordo/budget.py docs/ordo/budget.json docs/ordo/primary_test.py docs/ordo/primary_null_orbit.json \
            bank/ordo/src/templates.json bank/ordo/src/entities.json bank/ordo/src/cells.json \
            bank/ordo/src/followup.json tools/build_ordo_bank.py calibration/ordo/plan.json \
            calibration/ordo/arms/neutral.md calibration/ordo/arms/g_wish_individual.md \
            calibration/ordo/arms/g_wish_nation.md calibration/ordo_pilot.py tests/test_ordo_bank.py \
            tests/test_ordo_scoring.py tests/test_user_options.py tests/fixtures/make_ordo_small.py \
            tests/fixtures/check_ordo_counts.py tests/fixtures/entities_example.json; do
     [ -f "$ORDO_WT/$f" ] || need "$f" || true
   done
   if [ -f "$ORDO_WT/calibration/ordo/arms/neutral.md" ]; then
     cmp "$ORDO_WT/calibration/ordo/arms/neutral.md" "$ORDO_WT/calibration/ladder/L0.individual.md.tpl" \
       || die "the neutral arm must be v4's L0 file byte for byte"
   fi
   end "$ORDO_WT" ;;
3) # bank/ordo rebuilds byte for byte, the v4 bank still rebuilds to its pin, and the ordo pin is set
   begin "$ORDO_WT"; cd "$ORDO_WT"
   guarded python3 -B tools/build_ordo_bank.py --check
   with_cache "$ORDO_WT" guarded python3 -B tools/check_v4_bank.py   # build_bank --check, with the provenance exception
   pin=$(sha bank/ordo/manifest.json)
   if [ -f ordo.py ]; then grep -q "^ORDO_MANIFEST_SHA256 = \"$pin\"" ordo.py || die "pin ORDO_MANIFEST_SHA256 = \"$pin\" in ordo.py"
   else need "ordo.py to pin ORDO_MANIFEST_SHA256 = $pin" || true; fi
   end "$ORDO_WT" ;;
4) # the guard proves itself in a child guard; then every suite under the phase's guard, whose log stays empty
   begin "$ORDO_WT"; cd "$ORDO_WT"
   guard_selftest
   with_cache "$ORDO_WT" guarded python3 -B -m unittest discover -s tests
   guarded python3 -B -m unittest discover -s calibration/tests
   end "$ORDO_WT" ;;
5) # dry runs and counts: today's v4 plan unchanged (requirement 1), the budget self-consistent, every count matched
   begin "$ORDO_WT"; cd "$ORDO_WT"
   for agent in claude codex; do
     guarded python3 -B probatio.py --agent "$agent" --target-type organization --target "Mozilla" --dry-run \
       --out "$SCRATCH/dry-$agent-new" > "$SCRATCH/dry-$agent-new.log"
     (cd "$PROBATIO" && guarded python3 -B probatio.py --agent "$agent" --target-type organization --target "Mozilla" \
       --dry-run --out "$SCRATCH/dry-$agent-old" > "$SCRATCH/dry-$agent-old.log")
     cmp "$SCRATCH/dry-$agent-new/rendered.jsonl" "$SCRATCH/dry-$agent-old/rendered.jsonl" \
       || die "today's v4 plan changed for $agent (requirement 1)"
     echo "requirement 1 ($agent): the v4 plan is byte-identical to $V4_BASE's"
   done
   guarded python3 -B docs/ordo/budget.py | cmp - docs/ordo/budget.json || die "docs/ordo/budget.json is stale"
   echo "budget.json: what budget.py derives from the plan rules"
   if has_cross_type "$ORDO_WT" || need "ordo.py and --compare (the engine and options branches)"; then
     for tier in smoke quick standard full; do
       guarded python3 -B probatio.py --agent claude --compare individual:organization:nation --tier "$tier" --dry-run \
         --out "$SCRATCH/dry-$tier"
     done
     guarded python3 -B probatio.py --agent claude --compare individual:organization:nation \
       --target organization=Mozilla --dry-run --out "$SCRATCH/dry-named"
     guarded python3 -B probatio.py --agent claude --compare individual:organization --dry-run --out "$SCRATCH/dry-pair"
     guarded python3 -B probatio.py --agent codex --compare individual:organization:nation --dry-run \
       --out "$SCRATCH/dry-codex"
     guarded python3 -B tests/fixtures/check_ordo_counts.py docs/ordo/budget.json "$SCRATCH"
   fi
   end "$ORDO_WT" ;;
6) # commit the integration on branch ordo (never v4, never pushed)
   begin "$ORDO_WT"; cd "$ORDO_WT"
   [ "$(git branch --show-current)" = ordo ] || die "not on branch ordo"
   [ -z "$(git status --porcelain --untracked-files=no)" ] || die "the integration has uncommitted changes"
   git log --oneline -n 20 "$V4_BASE"..HEAD     # -n, not a pipe into head: under pipefail a closed pipe fails
   end "$ORDO_WT" ;;
7) # consilium: build_ordo.py on its branch ordo (made by the release builder), built locally, tested, indexed; a commit
   # only when the run leaves something staged; nothing uploaded
   begin "$ORDO_WT"
   [ "$(git -C "$CONSILIUM" branch --show-current)" = ordo ] || die "consilium is not on branch ordo"
   for f in release/build_ordo.py release/cards/ordo_card.md release/ordo_canary.json release/tests/test_build_ordo.py; do
     [ -f "$CONSILIUM/$f" ] || die "missing $f"
   done
   cd "$CONSILIUM"
   run() { guarded uv run --offline --no-project --with pyarrow==26.0.0 python3 "$@"; }
   run release/build_ordo.py --probatio "$ORDO_WT"
   first=$(sha release/build/ordo/manifest.json)
   run release/build_ordo.py --probatio "$ORDO_WT"
   [ "$(sha release/build/ordo/manifest.json)" = "$first" ] || die "build_ordo.py is not deterministic"
   run -m unittest discover -s release/tests
   run release/datasets_index.py
   git add release/build_ordo.py release/cards/ordo_card.md release/ordo_canary.json \
           release/tests/test_build_ordo.py release/datasets_index.py release/DATASETS.md
   git diff --cached --quiet || git commit -m "release: build_ordo.py, the ordo card and canary; DATASETS.md gains its \
fourth row, across types" -m "$TRAILER"
   end "$ORDO_WT" ;;
*) die "unknown phase $PHASE" ;;
esac
