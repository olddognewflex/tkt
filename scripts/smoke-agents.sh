#!/usr/bin/env bash
# Smoke test for `tkt agents` and the run heartbeat. Exercises the empty board,
# a live run reporting `running`, a SIGKILLed driver flipping to `dead`, a clean
# halt removing the beat file, the STOP flag, --enrich, and the state-resolution
# table. Prints PASS/FAIL per case.
set -uo pipefail

PACK="$(cd "$(dirname "$0")/.." && pwd)"
TKT="$PACK/tkt"

fails=0
pass() { printf 'PASS  %s\n' "$1"; }
fail() { printf 'FAIL  %s\n' "$1"; fails=$((fails + 1)); }

C="$(mktemp -d)"
cleanup() {
  [ -n "${DPID:-}" ] && kill -9 "$DPID" 2>/dev/null
  pkill -f "sleep 481" 2>/dev/null
  rm -rf "$C"
}
trap cleanup EXIT

# A markdown project with a harness that just blocks, so the driver sits inside
# one invocation for as long as the test needs.
"$TKT" init --provider markdown --dir "$C" --sample >/dev/null 2>&1
CFG="$C/.sdlc/config.toml"
python3 - "$CFG" <<'PY'
import sys
p = sys.argv[1]
out = []
for ln in open(p).read().splitlines():
    out.append(ln)
    if ln.startswith("[run]"):
        out.append('harness_cmd = "sleep 481"')
        out.append("heartbeat_interval = 1")
open(p, "w").write("\n".join(out) + "\n")
PY
export TKT_CONFIG="$CFG"

# ---- case 1: empty board is exit 0, not an error ----------------------------
# A TUI polls this constantly; "nothing running" is its steady state.
out=$("$TKT" agents --json); rc=$?
if [ "$rc" = "0" ] && [ "$(echo "$out" | python3 -c 'import json,sys; print(len(json.load(sys.stdin)["agents"]))')" = "0" ]; then
  pass "case1 empty board exits 0 with an empty list"
else
  fail "case1 empty board (rc=$rc)"
fi

# ---- case 2: a live run reports running -------------------------------------
(cd "$C" && nohup "$TKT" run TKT-1 >"$C/driver.out" 2>&1 &)
sleep 3
row() { "$TKT" agents --json ${1:+--stale-after "$1"} | python3 -c "
import json,sys
a = json.load(sys.stdin)['agents']
print(json.dumps(a[0]) if a else '{}')"; }

# The driver's own pid, read from its beat file. `$!` is unreliable here: bash
# does not exec-optimize `(cd X && nohup Y &)`, so it yields the subshell and
# killing it would orphan the driver. The heartbeat is the authority.
beat_pid() {
  python3 -c '
import json, sys
try:
    print(json.load(open(sys.argv[1]))["pid"])
except Exception:
    print("")' "$C/.sdlc/state/run/TKT-1/heartbeat.json"
}
DPID=$(beat_pid)
r=$(row)
st=$(echo "$r" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("state",""))')
it=$(echo "$r" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("iteration",""))')
if [ "$st" = "running" ] && [ "$it" = "1" ]; then
  pass "case2 live run reports running at iteration 1"
else
  fail "case2 live run (state=$st iteration=$it)"
fi

# ---- case 3: no backend calls without --enrich ------------------------------
# board_dir is what the adapter reads; move it away and the verb must still work.
mv "$C/.sdlc/board" "$C/.sdlc/board.hidden"
if "$TKT" agents --json >/dev/null 2>&1; then
  pass "case3 agents works with the board unreachable (no adapter)"
else
  fail "case3 agents touched the backend"
fi
mv "$C/.sdlc/board.hidden" "$C/.sdlc/board"

# ---- case 4: STOP is surfaced before the driver notices it ------------------
"$TKT" run --stop TKT-1 >/dev/null
sr=$(row | python3 -c 'import json,sys; print(json.load(sys.stdin).get("stop_requested"))')
[ "$sr" = "True" ] && pass "case4 stop_requested surfaces immediately" \
                   || fail "case4 stop_requested=$sr"

# ---- case 5: SIGKILLed driver flips to dead ---------------------------------
kill -9 "$DPID" 2>/dev/null
DPID=""
sleep 2
st=$(row 1 | python3 -c 'import json,sys; print(json.load(sys.stdin).get("state",""))')
[ "$st" = "dead" ] && pass "case5 killed driver reports dead" \
                   || fail "case5 killed driver reported $st"

# ---- case 6: a clean halt removes the beat file -----------------------------
rm -rf "$C/.sdlc/state/run"
(cd "$C" && nohup "$TKT" run TKT-1 >"$C/driver2.out" 2>&1 &)
sleep 3
DPID=$(beat_pid)
"$TKT" run --stop TKT-1 >/dev/null
pkill -f "sleep 481" 2>/dev/null   # let the blocked invocation return
sleep 3
DPID=""
if [ ! -f "$C/.sdlc/state/run/TKT-1/heartbeat.json" ]; then
  st=$(row | python3 -c 'import json,sys; print(json.load(sys.stdin).get("state",""))')
  [ "$st" = "halted" ] && pass "case6 clean halt removes the beat file and reports halted" \
                       || fail "case6 clean halt reported $st"
else
  fail "case6 clean halt left a stale heartbeat.json"
fi

# ---- case 7: markers carry the clock ----------------------------------------
m="$C/.sdlc/state/run/TKT-1/marker.json"
if python3 -c "
import json,sys
d = json.load(open('$m'))
sys.exit(0 if d.get('run_started') and d.get('phase_started') else 1)"; then
  pass "case7 marker carries run_started + phase_started"
else
  fail "case7 marker missing timestamps"
fi

# ---- case 8: --enrich adds board fields -------------------------------------
s=$("$TKT" agents --enrich --json | python3 -c '
import json,sys
a = json.load(sys.stdin)["agents"]
print(a[0].get("summary","") if a else "")')
[ -n "$s" ] && pass "case8 --enrich adds board fields" || fail "case8 --enrich added nothing"

# ---- case 9: run --status needs no harness_cmd ------------------------------
# The read-only path must not construct a RunConfig; a project with no harness
# configured can still be asked what its runs are doing.
python3 - "$CFG" <<'PY'
import sys
p = sys.argv[1]
keep = [ln for ln in open(p).read().splitlines()
        if not ln.startswith('harness_cmd = "sleep 481"')]
open(p, "w").write("\n".join(keep) + "\n")
PY
if "$TKT" run --status TKT-1 >/dev/null 2>&1; then
  pass "case9 run --status works without [run].harness_cmd"
else
  fail "case9 run --status required a harness_cmd"
fi

# ---- case 10: state resolution table ----------------------------------------
if python3 - "$PACK" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
from datetime import datetime, timedelta, timezone
import os, socket
from core.agents import resolve_state

now = datetime.now(timezone.utc)
def iso(dt): return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
here = socket.gethostname()
fresh = {"beat": iso(now), "pid": os.getpid(), "host": here}
stale_live = {"beat": iso(now - timedelta(minutes=5)), "pid": os.getpid(), "host": here}
stale_gone = {"beat": iso(now - timedelta(minutes=5)), "pid": 999999, "host": here}
stale_other = {"beat": iso(now - timedelta(minutes=5)), "pid": 1, "host": "elsewhere"}

cases = [
    (None, fresh, "running"),
    (None, stale_live, "stalled"),   # beat stale, process alive: say so
    (None, stale_gone, "dead"),
    (None, stale_other, "stalled"),  # another host, pid unknowable
    ({"outcome": "blocked"}, None, "blocked"),
    ({"outcome": "halted"}, None, "halted"),
    ({"outcome": "gate"}, None, "halted"),
    ({"outcome": "advance"}, None, "dead"),   # mid-loop marker, no driver
    ({"outcome": "retry"}, None, "dead"),
    (None, None, "idle"),
]
bad = [(m, h, want, resolve_state(m, h, now, 45))
       for m, h, want in cases if resolve_state(m, h, now, 45) != want]
for b in bad:
    print("  mismatch:", b)
sys.exit(1 if bad else 0)
PY
then
  pass "case10 resolve_state table"
else
  fail "case10 resolve_state table"
fi

echo
[ "$fails" = "0" ] && echo "all cases passed" || echo "$fails case(s) failed"
exit "$fails"
