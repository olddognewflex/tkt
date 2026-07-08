#!/usr/bin/env bash
# company-import.sh — export this upstream pack (`tkt`) into a company copy,
# rewriting the repo slug and renaming the CLI.
#
# Deterministic and repeatable: it exports ONLY tracked files from the source's
# HEAD (git archive, so .sdlc/, __pycache__, untracked junk are excluded), then
# applies a fixed rename transform. Re-running against a fresh checkout with the
# same flags re-applies identically, so future curated syncs stay reproducible.
#
# Usage: scripts/company-import.sh [--source <pack-root>] --target <dir> \
#          --repo <org/repo> --cli <name>
#   --source  upstream pack checkout (default: this script's repo root)
#   --target  company repo to import INTO (must be a clean git repo)
#   --repo    company GitHub slug that replaces `olddognewflex/tkt`; its org
#             part also replaces bare `olddognewflex`
#   --cli     new CLI name that replaces every standalone lowercase `tkt` token
#
# It does NOT commit — the operator reviews and commits the result.
set -euo pipefail

# Keep the transformed tree deterministic: the sanity checks below run python,
# which would otherwise litter the target with non-reproducible __pycache__/*.pyc.
export PYTHONDONTWRITEBYTECODE=1

# ---- args ------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SOURCE="$(cd "$SCRIPT_DIR/.." && pwd)"
TARGET=""
REPO=""
CLI=""

while [ $# -gt 0 ]; do
  case "$1" in
    --source) SOURCE="$2"; shift 2 ;;
    --target) TARGET="$2"; shift 2 ;;
    --repo)   REPO="$2";   shift 2 ;;
    --cli)    CLI="$2";    shift 2 ;;
    -h|--help)
      # Print only the contiguous header comment block (skip the shebang, stop
      # at the first non-comment line).
      awk 'NR==1{next} /^#/{sub(/^# ?/,""); print; next} {exit}' "$0"
      exit 0 ;;
    *) echo "company-import: unknown arg '$1'" >&2; exit 64 ;;
  esac
done

die() { echo "company-import: $*" >&2; exit 1; }

[ -n "$TARGET" ] || die "--target <dir> is required"
[ -n "$REPO" ] || die "--repo <org/repo> is required"
case "$REPO" in */*) ;; *) die "--repo must be an org/repo slug: $REPO" ;; esac
[ -n "$CLI" ] || die "--cli <name> is required"
case "$CLI" in tkt) die "--cli must differ from 'tkt'" ;; esac
ORG="${REPO%%/*}"
SOURCE="$(cd "$SOURCE" 2>/dev/null && pwd)" || die "--source not a directory"
TARGET="$(cd "$TARGET" 2>/dev/null && pwd)" || die "--target not a directory: does it exist?"

git -C "$SOURCE" rev-parse --git-dir >/dev/null 2>&1 || die "--source is not a git repo: $SOURCE"
git -C "$TARGET" rev-parse --git-dir >/dev/null 2>&1 || die "--target is not a git repo: $TARGET"
[ "$SOURCE" != "$TARGET" ] || die "--source and --target are the same tree"

# Refuse to overwrite a target with uncommitted work (protects the operator's WIP).
if [ -n "$(git -C "$TARGET" status --porcelain)" ]; then
  die "target has uncommitted changes; commit or stash them first: $TARGET"
fi

SRC_SHA="$(git -C "$SOURCE" rev-parse HEAD)"

# ---- 1. export tracked files from HEAD -------------------------------------
EXPORTED="$(git -C "$SOURCE" archive HEAD | tar -tf - | grep -cv '/$' || true)"
git -C "$SOURCE" archive --format=tar HEAD | tar -xf - -C "$TARGET"

# ---- 2a. rename the entry script tkt -> $CLI --------------------------------
if [ -f "$TARGET/tkt" ]; then
  mv "$TARGET/tkt" "$TARGET/$CLI"
  chmod +x "$TARGET/$CLI"
fi

# ---- 2b. text rewrite over every exported text file ------------------------
# Order matters: rewrite the org/repo/host references FIRST (they contain the
# substring `tkt`, e.g. olddognewflex/tkt), so the later word-boundary pass has
# nothing stray left to touch inside a URL. The word-boundary pass then renames
# every standalone lowercase `tkt` token (CLI name, AGENTS.md markers, the
# manifest key `AGENTS.md#tkt-pack`) to the --cli name, while leaving uppercase
# `TKT` ticket-prefix examples, `TKT_CONFIG`, and non-word neighbours such as
# `tktban` / `tkt_candidates.json` / `_tkt_invocation` untouched.
REWRITTEN=0
while IFS= read -r -d '' f; do
  grep -Iq . "$f" || continue          # skip binary / empty files
  REPO="$REPO" ORG="$ORG" CLI="$CLI" perl -i.bak -pe '
    s{olddognewflex/tkt}{$ENV{REPO}}g;
    s{olddognewflex}{$ENV{ORG}}g;
    s{github-odnf}{github.com}g;
    s/\btkt\b/$ENV{CLI}/g;
  ' "$f"
  if ! cmp -s "$f" "$f.bak"; then
    REWRITTEN=$((REWRITTEN + 1))
  fi
  rm -f "$f.bak"
done < <(find "$TARGET" -type f -not -path '*/.git/*' -print0)

# ---- 3. provenance stamp ---------------------------------------------------
{
  echo "source: upstream tkt SDLC pack"
  echo "commit: $SRC_SHA"
  echo "imported: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "cli: $CLI (renamed from tkt)"
} > "$TARGET/PACK_VERSION"

# ---- 4. sanity checks (fail loudly) ----------------------------------------
echo
echo "== sanity checks =="

# 4a. no standalone lowercase `tkt` remnants (whole-word; PACK_VERSION excluded).
if grep -rInw --exclude-dir=.git --exclude=PACK_VERSION -e 'tkt' "$TARGET"; then
  die "found whole-word lowercase 'tkt' remnants (listed above) — transform incomplete"
fi
echo "ok  no whole-word 'tkt' remnants"

# 4b. renamed CLI runs.
if ! python3 "$TARGET/$CLI" --help >/dev/null 2>&1; then
  die "'$CLI --help' did not exit 0"
fi
echo "ok  $CLI --help exits 0"

# 4c. the (renamed) smoke suite still passes against the transformed tree.
SMOKE="$TARGET/scripts/smoke-sync-pack.sh"
if [ -f "$SMOKE" ]; then
  if bash "$SMOKE" >/tmp/company-import-smoke.$$ 2>&1; then
    echo "ok  smoke-sync-pack.sh ALL PASS"
    rm -f "/tmp/company-import-smoke.$$"
  else
    echo "--- smoke output ---"
    cat "/tmp/company-import-smoke.$$"
    rm -f "/tmp/company-import-smoke.$$"
    die "smoke-sync-pack.sh FAILED against the transformed tree"
  fi
else
  echo "--  scripts/smoke-sync-pack.sh not present (skipped)"
fi

# Belt-and-suspenders: drop any bytecode caches the checks may have created.
find "$TARGET" -type d -name __pycache__ -not -path '*/.git/*' -prune -exec rm -rf {} + 2>/dev/null || true

# ---- 5. summary ------------------------------------------------------------
echo
echo "== import summary =="
echo "source:          $SOURCE"
echo "source HEAD:     $SRC_SHA"
echo "target:          $TARGET"
echo "files exported:  $EXPORTED"
echo "files rewritten: $REWRITTEN"
echo "cli renamed:     tkt -> $CLI"
echo "(not committed — review the target and commit there)"
