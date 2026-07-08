#!/usr/bin/env bash
# Smoke test for `tkt sync-pack`. Exercises fresh install, idempotency, local
# modification restore + warning, --check exit status, --all-harnesses, and
# AGENTS.md custom-content preservation. Prints PASS/FAIL per case.
set -euo pipefail

PACK="$(cd "$(dirname "$0")/.." && pwd)"
TKT="$PACK/tkt"

fails=0
pass() { printf 'PASS  %s\n' "$1"; }
fail() { printf 'FAIL  %s\n' "$1"; fails=$((fails + 1)); }

# Isolate git identity/config so committing works in throwaway repos.
git_init() {
  git -C "$1" init -q
  git -C "$1" config user.email "smoke@example.com"
  git -C "$1" config user.name "smoke"
}

C="$(mktemp -d)"
trap 'rm -rf "$C" "${C2:-}" "${C3:-}" "${C4:-}"' EXIT

# ---- case 1: fresh install --------------------------------------------------
"$TKT" sync-pack --dir "$C" >/dev/null
skills_n=$(find "$C/.claude/skills" -maxdepth 1 -mindepth 1 -type d | wc -l | tr -d ' ')
prompts_n=$(find "$C/.github/prompts" -maxdepth 1 -type f | wc -l | tr -d ' ')
kiro_n=$(find "$C/.kiro/skills" -maxdepth 1 -mindepth 1 -type d | wc -l | tr -d ' ')
ok=1
[ "$skills_n" = "14" ] || { ok=0; echo "  .claude/skills=$skills_n (want 14)"; }
[ "$prompts_n" = "14" ] || { ok=0; echo "  .github/prompts=$prompts_n (want 14)"; }
[ "$kiro_n" = "14" ] || { ok=0; echo "  .kiro/skills=$kiro_n (want 14)"; }
[ -f "$C/.sdlc/pack-manifest.json" ] || { ok=0; echo "  manifest missing"; }
grep -q "tkt-pack:begin" "$C/AGENTS.md" || { ok=0; echo "  AGENTS.md markers missing"; }
[ "$ok" = "1" ] && pass "case1 fresh install (skills=14 prompts=14 kiro=14 manifest+markers)" \
                || fail "case1 fresh install"

# ---- case 2: idempotent second run leaves git clean -------------------------
git_init "$C"
git -C "$C" add -A
git -C "$C" commit -q -m "install pack"
"$TKT" sync-pack --dir "$C" >/dev/null
if [ -z "$(git -C "$C" status --porcelain)" ]; then
  pass "case2 second run is a no-op (git status clean)"
else
  fail "case2 second run dirtied the tree:"
  git -C "$C" status --porcelain | sed 's/^/    /'
fi

# ---- case 3: local modification → warning + restore -------------------------
victim="$C/.github/prompts/open-pr.prompt.md"
printf '\nLOCAL EDIT\n' >> "$victim"
out3="$("$TKT" sync-pack --dir "$C" 2>&1)"
restored=1
cmp -s "$victim" "$PACK/.github/prompts/open-pr.prompt.md" || restored=0
if echo "$out3" | grep -q "was locally modified" && [ "$restored" = "1" ]; then
  pass "case3 locally-modified file warned and restored"
else
  fail "case3 modify+restore (warned? / restored=$restored)"
  echo "$out3" | sed 's/^/    /'
fi

# ---- case 4: --check on a modified file exits nonzero -----------------------
printf '\nLOCAL EDIT AGAIN\n' >> "$victim"
if "$TKT" sync-pack --dir "$C" --check >/dev/null 2>&1; then
  fail "case4 --check returned 0 despite a modified file"
else
  pass "case4 --check exits nonzero on drift"
fi
# restore for cleanliness (not asserted)
"$TKT" sync-pack --dir "$C" >/dev/null 2>&1 || true

# ---- case 5: --all-harnesses installs Tier-B dirs ---------------------------
C2="$(mktemp -d)"
"$TKT" sync-pack --dir "$C2" --all-harnesses >/dev/null
if [ -d "$C2/.gemini/commands" ] && \
   [ "$(find "$C2/.gemini/commands" -type f | wc -l | tr -d ' ')" -gt 0 ]; then
  pass "case5 --all-harnesses installs .gemini/commands"
else
  fail "case5 --all-harnesses missing .gemini/commands"
fi

# ---- case 6: pre-existing AGENTS.md custom content preserved ----------------
C3="$(mktemp -d)"
printf '# My Project\n\nCUSTOM LINE ONE\n' > "$C3/AGENTS.md"
"$TKT" sync-pack --dir "$C3" >/dev/null
if grep -q "CUSTOM LINE ONE" "$C3/AGENTS.md" && grep -q "tkt-pack:begin" "$C3/AGENTS.md"; then
  pass "case6 custom AGENTS.md content preserved outside markers"
else
  fail "case6 custom AGENTS.md content lost"
  sed 's/^/    /' "$C3/AGENTS.md"
fi

# ---- case 7: pre-existing differing file at a pack path warns on first sync --
# A consumer that already ships its own copy at a pack destination (never synced
# by us, so no manifest entry) must be warned before its content is replaced.
C4="$(mktemp -d)"
pre="$C4/.github/prompts/select-ticket.prompt.md"
mkdir -p "$(dirname "$pre")"
printf 'CONSUMER-OWNED CONTENT\n' > "$pre"
out7="$("$TKT" sync-pack --dir "$C4" 2>&1)"
replaced=1
cmp -s "$pre" "$PACK/.github/prompts/select-ticket.prompt.md" || replaced=0
if echo "$out7" | grep -q "pre-existing file overwritten" && [ "$replaced" = "1" ]; then
  pass "case7 pre-existing differing file warned and replaced on first sync"
else
  fail "case7 pre-existing file (warned? / replaced=$replaced)"
  echo "$out7" | sed 's/^/    /'
fi

echo
if [ "$fails" = "0" ]; then
  echo "ALL PASS"
else
  echo "$fails case(s) FAILED"
fi
exit "$fails"
