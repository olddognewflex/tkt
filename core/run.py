"""External loop driver for headless pipeline execution.

Invokes the configured harness one phase at a time, persists phase state as
ticket markers (comments), and loops until a gate, STOP signal, or cap.

The driver never performs VCS operations or bypasses adapter verbs — it
orchestrates invocations and ticket state only.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import Config
from .errors import ConfigError, TktError
from .registry import get_adapter

# ---- Constants ---------------------------------------------------------------

# Pipeline phases in order. The driver advances through these.
PHASES = [
    "P0",   # select-ticket
    "P1",   # triage
    "P1.5", # type-route
    "P2",   # plan
    "P3",   # implement/test
    "P4",   # self-review
    "P5",   # open-pr
    "P6",   # ci-fix
    "P7",   # respond-to-review
    "P8",   # deploy-preview
    "P9",   # qa_ready (human QA gate — always stop)
    "P10",  # deploy-ready
]

PHASE_NAMES = {
    "P0": "select-ticket",
    "P1": "triage",
    "P1.5": "type-route",
    "P2": "plan",
    "P3": "implement/test",
    "P4": "self-review",
    "P5": "open-pr",
    "P6": "ci-fix",
    "P7": "respond-to-review",
    "P8": "deploy-preview",
    "P9": "qa_ready",
    "P10": "deploy-ready",
}

# Valid outcomes from the harness result file.
VALID_OUTCOMES = ("advance", "retry", "blocked", "gate")

# Marker comment pattern for parsing/writing ticket state.
MARKER_PREFIX = "<!-- tkt-run: "
MARKER_SUFFIX = " -->"
MARKER_RE = re.compile(
    r"<!-- tkt-run: (\{.*?\}) -->", re.DOTALL
)

# Default config values.
DEFAULT_MAX_ITERATIONS = 30
DEFAULT_MAX_PHASE_ATTEMPTS = 3
DEFAULT_INVOCATION_TIMEOUT = 3600


# ---- Run config access -------------------------------------------------------

class RunConfig:
    """Parsed [run] section from .sdlc/config.toml."""

    def __init__(self, config: Config):
        run = config._d.get("run", {})
        self.harness_cmd: str = run.get("harness_cmd", "")
        self.max_iterations: int = int(run.get("max_iterations", DEFAULT_MAX_ITERATIONS))
        self.max_phase_attempts: int = int(run.get("max_phase_attempts", DEFAULT_MAX_PHASE_ATTEMPTS))
        self.invocation_timeout: int = int(run.get("invocation_timeout", DEFAULT_INVOCATION_TIMEOUT))

        if not self.harness_cmd:
            raise ConfigError(
                "[run].harness_cmd is required for `tkt run`. "
                "Set it in .sdlc/config.toml, e.g.:\n"
                '  harness_cmd = "claude -p {prompt} --permission-mode acceptEdits"'
            )


# ---- Ticket marker operations ------------------------------------------------

def _build_marker(state: dict[str, Any]) -> str:
    """Build a machine-readable marker comment string."""
    return f"{MARKER_PREFIX}{json.dumps(state, separators=(',', ':'))}{MARKER_SUFFIX}"


def _parse_marker(comment_body: str) -> dict[str, Any] | None:
    """Extract the most recent tkt-run marker from a comment body."""
    matches = MARKER_RE.findall(comment_body)
    if not matches:
        return None
    # Return the last match (most recent).
    try:
        return json.loads(matches[-1])
    except (json.JSONDecodeError, TypeError):
        return None


def read_ticket_marker(adapter, key: str,
                       state_dir: Path | None = None) -> dict[str, Any] | None:
    """Read the newest run marker for a ticket, or None if there is none.

    The ticket comment is the authoritative record — it survives a lost
    worktree and is readable from another machine — but only the markdown
    adapter can hand back raw comment bodies to scan (`_read_raw`). The verb
    contract has no "list comments" verb, so on every other backend the marker
    is recovered from the local mirror `write_ticket_marker` keeps. Without
    that fallback a resume on Jira/GitHub/Linear would silently restart at P1.
    """
    if hasattr(adapter, "_read_raw"):
        try:
            _, body = adapter._read_raw(key)
        except (TktError, OSError):
            body = ""
        matches = MARKER_RE.findall(body)
        if matches:
            try:
                return json.loads(matches[-1])
            except (json.JSONDecodeError, TypeError):
                pass

    if state_dir is not None:
        path = state_dir / "marker.json"
        if path.is_file():
            try:
                data = json.loads(path.read_text())
            except (json.JSONDecodeError, OSError):
                return None
            if isinstance(data, dict):
                return data

    return None


def write_ticket_marker(adapter, key: str, state: dict[str, Any],
                        state_dir: Path | None = None) -> None:
    """Append a phase marker comment to the ticket, mirroring it locally.

    The local mirror is what `read_ticket_marker` falls back to on backends
    whose comments it cannot read back (see above).
    """
    phase = state.get("phase", "?")
    attempt = state.get("attempt", 1)
    outcome = state.get("outcome", "")
    human_line = f"[run] Phase {phase} ({PHASE_NAMES.get(phase, phase)}), attempt {attempt}"
    if outcome:
        human_line += f" — {outcome}"
    marker = _build_marker(state)
    comment = f"{human_line}\n{marker}"
    adapter.comment(key, comment)

    if state_dir is not None:
        try:
            (state_dir / "marker.json").write_text(
                json.dumps(state, separators=(",", ":"))
            )
        except OSError:
            # The ticket comment already landed; a failed mirror must not
            # abort the run, it only costs resume fidelity on this machine.
            pass


# ---- Local state management --------------------------------------------------

def _state_dir(config: Config, key: str) -> Path:
    """Resolve .sdlc/state/run/<key>/ relative to the project root."""
    cfg_dir = config.path.parent
    base = cfg_dir.parent if cfg_dir.name == ".sdlc" else cfg_dir
    return base / ".sdlc" / "state" / "run" / key


def ensure_state_dir(config: Config, key: str) -> Path:
    """Create and return the local state dir for a run."""
    d = _state_dir(config, key)
    d.mkdir(parents=True, exist_ok=True)
    return d


def check_stop_file(state_dir: Path) -> bool:
    """Return True if a STOP file exists."""
    return (state_dir / "STOP").is_file()


def create_stop_file(config: Config, key: str) -> Path:
    """Create the STOP file for a given ticket key."""
    d = ensure_state_dir(config, key)
    stop = d / "STOP"
    stop.touch()
    return stop


def read_result_file(state_dir: Path) -> dict[str, Any] | None:
    """Read and validate result.json; return None if missing/invalid."""
    path = state_dir / "result.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    # Validate schema.
    if not isinstance(data, dict):
        return None
    if data.get("outcome") not in VALID_OUTCOMES:
        return None
    if "phase" not in data:
        return None
    return data


def delete_result_file(state_dir: Path) -> None:
    """Remove result.json after processing."""
    path = state_dir / "result.json"
    if path.is_file():
        path.unlink()


def append_run_log(state_dir: Path, entry: dict[str, Any]) -> None:
    """Append a JSONL line to run.log."""
    log_path = state_dir / "run.log"
    with open(log_path, "a") as fh:
        fh.write(json.dumps(entry) + "\n")


# ---- Phase prompt builder ----------------------------------------------------

def build_phase_prompt(
    key: str,
    phase: str,
    attempt: int,
    state_dir: Path,
) -> str:
    """Build the prompt string passed to the harness."""
    result_path = state_dir / "result.json"
    return (
        f"You are executing ONE phase of the automated SDLC pipeline for ticket {key}.\n"
        f"\n"
        f"Current phase: {phase} ({PHASE_NAMES.get(phase, phase)})\n"
        f"Attempt: {attempt}\n"
        f"\n"
        f"Execute EXACTLY this one phase per skills/automated-sdlc/SKILL.md, then write\n"
        f"the result to: {result_path}\n"
        f"\n"
        f"Result file contract (JSON):\n"
        f'{{"phase":"{phase}","outcome":"<advance|retry|blocked|gate>","next":"<next_phase>","reason":"<explanation>"}}\n'
        f"\n"
        f"Outcomes:\n"
        f"  advance — phase completed successfully, move to next phase\n"
        f"  retry   — phase failed but is retryable (e.g. test failure)\n"
        f"  blocked — unrecoverable blocker, ticket should be blocked\n"
        f"  gate    — human gate reached, stop the run\n"
        f"\n"
        f"After writing result.json, exit. Do not proceed to the next phase.\n"
    )


# ---- Harness invocation ------------------------------------------------------

def invoke_harness(
    harness_cmd: str,
    prompt: str,
    timeout: int,
    state_dir: Path,
    dry_run: bool = False,
) -> tuple[int, str]:
    """Invoke the harness command with the prompt substituted.

    Returns (exit_code, log_output). On timeout, returns (1, <timeout msg>).
    On dry_run, returns (0, "") without executing.
    """
    # Substitute {prompt} in the command template.
    # Shell-escape the prompt for safe embedding.
    escaped_prompt = prompt.replace("'", "'\\''")
    cmd = harness_cmd.replace("{prompt}", f"'{escaped_prompt}'")

    if dry_run:
        return 0, f"[dry-run] would execute:\n{cmd}"

    log_path = state_dir / "run.log"
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout if timeout > 0 else None,
            cwd=os.getcwd(),
        )
        output = result.stdout + result.stderr
        return result.returncode, output
    except subprocess.TimeoutExpired:
        return 1, f"[timeout] harness exceeded {timeout}s"
    except OSError as e:
        return 1, f"[error] failed to invoke harness: {e}"


# ---- Gate logic --------------------------------------------------------------

def is_human_owned_transition(config: Config, from_role: str, to_role: str) -> bool:
    """Check if a transition is human-owned per [board.ownership]."""
    ownership = config.ownership
    transition_key = f"{from_role}->{to_role}"
    owner = ownership.get(transition_key, "")
    return owner.lower() == "human"


def is_gate_phase(phase: str) -> bool:
    """P9 (qa_ready) is always a hard gate."""
    return phase == "P9"


def is_production_phase(phase: str, config: Config) -> bool:
    """P10 (deploy-ready) is blocked unless config explicitly grants agent ownership."""
    if phase != "P10":
        return False
    # Check if deploy_ready->done is agent-owned.
    owner = config.ownership.get("deploy_ready->done", "human")
    return owner.lower() != "agent"


# ---- Main driver loop --------------------------------------------------------

class RunDriver:
    """The external loop driver."""

    def __init__(
        self,
        config: Config,
        run_config: RunConfig,
        key: str | None = None,
        max_iterations: int | None = None,
        dry_run: bool = False,
    ):
        self.config = config
        self.run_config = run_config
        self.adapter = get_adapter(config)
        self.key = key
        self.max_iterations = max_iterations or run_config.max_iterations
        self.max_phase_attempts = run_config.max_phase_attempts
        self.timeout = run_config.invocation_timeout
        self.dry_run = dry_run

        # Runtime state.
        self.phase: str = "P0"
        self.attempt: int = 1
        self.iteration: int = 0
        self.state_dir: Path | None = None

    def run(self) -> int:
        """Execute the driver loop. Returns exit code."""
        # If no key, first phase is P0 (select-ticket) which picks one.
        if self.key:
            self._init_state()
            self._resume_from_marker()
        else:
            # We need a temporary state dir; after P0 we'll know the key.
            self.phase = "P0"
            self.attempt = 1

        return self._loop()

    def status(self) -> dict[str, Any]:
        """Return current status without running."""
        if not self.key:
            return {"error": "no ticket key specified"}
        marker = read_ticket_marker(self.adapter, self.key,
                                   _state_dir(self.config, self.key))
        if marker is None:
            return {"key": self.key, "phase": None, "status": "no run marker found"}
        return {"key": self.key, **marker}

    def _init_state(self) -> None:
        """Initialize state dir for the current key."""
        assert self.key is not None
        self.state_dir = ensure_state_dir(self.config, self.key)

    def _resume_from_marker(self) -> None:
        """Resume from the ticket's last marker, if any."""
        marker = read_ticket_marker(self.adapter, self.key, self.state_dir)
        if marker is None:
            # Fresh run — start from P0 or P1 depending on whether we have a key.
            self.phase = "P1" if self.key else "P0"
            self.attempt = 1
            return
        # Resume from the marker.
        last_phase = marker.get("phase", "P0")
        last_outcome = marker.get("outcome", "")
        if last_outcome == "advance":
            # Advance to the next phase.
            self.phase = marker.get("next", self._next_phase(last_phase))
            self.attempt = 1
        elif last_outcome == "retry":
            # Retry the same phase.
            self.phase = last_phase
            self.attempt = marker.get("attempt", 1) + 1
        else:
            # blocked/gate/unknown — resume from where we were.
            self.phase = last_phase
            self.attempt = marker.get("attempt", 1)

    def _next_phase(self, current: str) -> str:
        """Get the next phase after current."""
        try:
            idx = PHASES.index(current)
            if idx + 1 < len(PHASES):
                return PHASES[idx + 1]
        except ValueError:
            pass
        return current  # Stay on current if unknown.

    def _loop(self) -> int:
        """Main iteration loop."""
        while True:
            # --- Pre-iteration checks ---

            # For keyless start, we need a temp state dir for P0.
            if self.key is None and self.state_dir is None:
                # Use a temporary placeholder dir for P0.
                self.state_dir = ensure_state_dir(self.config, "_select")

            assert self.state_dir is not None

            # Check STOP file.
            if check_stop_file(self.state_dir):
                self._log("STOP file found — halting.")
                if self.key:
                    self._write_marker("stopped by operator")
                return 0

            # Check iteration cap.
            if self.iteration >= self.max_iterations:
                self._log(f"max iterations ({self.max_iterations}) reached — halting.")
                if self.key:
                    self._write_marker("max iterations reached")
                return 1

            # Check per-phase attempt cap.
            if self.attempt > self.max_phase_attempts:
                self._log(
                    f"phase {self.phase} failed {self.max_phase_attempts} attempts "
                    f"— transitioning to blocked."
                )
                if self.key:
                    self._transition_blocked()
                return 1

            # Gate checks BEFORE invocation.
            if is_gate_phase(self.phase):
                self._log(f"phase {self.phase} is a human QA gate — halting.")
                if self.key:
                    self._write_marker("gate: human QA")
                return 0

            if self.key and is_production_phase(self.phase, self.config):
                self._log(
                    f"phase {self.phase} (deploy) is not agent-owned — halting."
                )
                self._write_marker("gate: production deploy not agent-owned")
                return 0

            # Check human-owned transitions if we'd be performing one.
            if self.key and self.phase != "P0":
                # Determine the from/to roles for this phase.
                from_role, to_role = self._phase_transition_roles(self.phase)
                if from_role and to_role and is_human_owned_transition(
                    self.config, from_role, to_role
                ):
                    self._log(
                        f"transition {from_role}->{to_role} is human-owned — halting."
                    )
                    self._write_marker(f"gate: {from_role}->{to_role} is human-owned")
                    return 0

            # --- Invoke harness ---
            self.iteration += 1
            started = _now_iso()
            self._log(
                f"iteration {self.iteration}/{self.max_iterations}, "
                f"phase {self.phase}, attempt {self.attempt}"
            )

            prompt = build_phase_prompt(
                key=self.key or "<pending-select>",
                phase=self.phase,
                attempt=self.attempt,
                state_dir=self.state_dir,
            )

            rc, output = invoke_harness(
                self.run_config.harness_cmd,
                prompt,
                self.timeout,
                self.state_dir,
                dry_run=self.dry_run,
            )

            ended = _now_iso()

            if self.dry_run:
                self._log(output)
                return 0

            # --- Process result ---
            result = read_result_file(self.state_dir)

            if result is None:
                # Missing/invalid result → treat as retry.
                self._log(
                    f"result.json missing or invalid after iteration "
                    f"(rc={rc}) — counting as failed attempt."
                )
                outcome = "retry"
                next_phase = self.phase
                reason = "no valid result.json"
            else:
                outcome = result["outcome"]
                next_phase = result.get("next", self._next_phase(self.phase))
                reason = result.get("reason", "")
                delete_result_file(self.state_dir)

            # If P0 succeeded, extract the ticket key from result.
            if self.phase == "P0" and outcome == "advance":
                new_key = result.get("ticket_key") if result else None
                if new_key:
                    self.key = new_key
                    self._init_state()
                else:
                    # If the agent didn't provide a key, we can't continue.
                    self._log("P0 advance but no ticket_key in result — halting.")
                    return 1

            # Log the iteration.
            log_entry = {
                "iteration": self.iteration,
                "phase": self.phase,
                "attempt": self.attempt,
                "outcome": outcome,
                "reason": reason,
                "started": started,
                "ended": ended,
                "rc": rc,
            }
            append_run_log(self.state_dir, log_entry)

            # Handle outcome.
            if outcome == "advance":
                if self.key:
                    self._write_marker_with_outcome("advance", next_phase)
                self.phase = next_phase
                self.attempt = 1
            elif outcome == "retry":
                if self.key:
                    self._write_marker_with_outcome("retry", self.phase)
                self.attempt += 1
            elif outcome == "blocked":
                self._log(f"phase {self.phase} reported blocked: {reason}")
                if self.key:
                    self._transition_blocked(reason)
                return 1
            elif outcome == "gate":
                self._log(f"phase {self.phase} reported gate: {reason}")
                if self.key:
                    self._write_marker(f"gate: {reason}")
                return 0

    def _phase_transition_roles(self, phase: str) -> tuple[str, str]:
        """Return (from_role, to_role) for a phase's expected transition.

        Returns ("", "") for phases that don't perform a board transition.
        """
        mapping = {
            "P1": ("todo", "in_progress"),
            "P5": ("in_progress", "review"),
            "P9": ("review", "qa_ready"),
            "P10": ("deploy_ready", "done"),
        }
        return mapping.get(phase, ("", ""))

    def _transition_blocked(self, reason: str = "") -> None:
        """Transition the ticket to blocked and post a comment."""
        try:
            self.adapter.transition(self.key, "blocked")
        except TktError:
            pass  # Best-effort; maybe lane doesn't exist.
        msg = (
            f"[run] Blocked after {self.max_phase_attempts} failed attempts "
            f"on phase {self.phase} ({PHASE_NAMES.get(self.phase, self.phase)})."
        )
        if reason:
            msg += f"\nReason: {reason}"
        # Include last log lines.
        if self.state_dir:
            log_tail = self._log_tail(5)
            if log_tail:
                msg += f"\n\nRecent log:\n```\n{log_tail}\n```"
        state = {
            "phase": self.phase,
            "attempt": self.attempt,
            "outcome": "blocked",
            "reason": reason or "max attempts exceeded",
            "updated": _now_iso(),
        }
        marker = _build_marker(state)
        self.adapter.comment(self.key, f"{msg}\n{marker}")
        if self.state_dir:
            try:
                (self.state_dir / "marker.json").write_text(
                    json.dumps(state, separators=(",", ":"))
                )
            except OSError:
                pass

    def _write_marker(self, reason: str) -> None:
        """Write a status marker to the ticket."""
        state = {
            "phase": self.phase,
            "attempt": self.attempt,
            "outcome": "halted",
            "reason": reason,
            "updated": _now_iso(),
        }
        write_ticket_marker(self.adapter, self.key, state, self.state_dir)

    def _write_marker_with_outcome(self, outcome: str, next_phase: str) -> None:
        """Write an advance/retry marker to the ticket."""
        state = {
            "phase": self.phase,
            "attempt": self.attempt,
            "outcome": outcome,
            "next": next_phase,
            "updated": _now_iso(),
        }
        write_ticket_marker(self.adapter, self.key, state, self.state_dir)

    def _log(self, msg: str) -> None:
        """Print a log line to stderr."""
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        print(f"[{ts}] run: {msg}", file=sys.stderr)

    def _log_tail(self, n: int) -> str:
        """Return the last n lines from run.log."""
        if not self.state_dir:
            return ""
        log_path = self.state_dir / "run.log"
        if not log_path.is_file():
            return ""
        lines = log_path.read_text().splitlines()
        return "\n".join(lines[-n:])


# ---- Helpers -----------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---- CLI entry points --------------------------------------------------------

def cmd_run(config: Config, key: str | None, max_iterations: int | None,
            dry_run: bool) -> int:
    """Entry point for `tkt run [KEY]`."""
    run_config = RunConfig(config)
    driver = RunDriver(
        config, run_config, key=key,
        max_iterations=max_iterations, dry_run=dry_run,
    )
    return driver.run()


def cmd_status(config: Config, key: str) -> int:
    """Entry point for `tkt run --status KEY`."""
    run_config = RunConfig(config)
    driver = RunDriver(config, run_config, key=key)
    status = driver.status()
    print(json.dumps(status, indent=2))
    return 0


def cmd_stop(config: Config, key: str) -> int:
    """Entry point for `tkt run --stop KEY`."""
    stop_path = create_stop_file(config, key)
    print(f"STOP file created: {stop_path}")
    return 0
