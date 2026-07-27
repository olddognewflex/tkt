"""Unit tests for core/run.py — the external loop driver.

Fully offline: uses the markdown adapter with temp dirs and a stub harness
script that writes scripted result.json sequences. No real model turns.

Run from repo root:
    python3 -m unittest tests.test_run
"""
import json
import os
import stat
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import Config
from core.run import (
    RunConfig, RunDriver, PHASES, PHASE_NAMES,
    read_ticket_marker, write_ticket_marker,
    check_stop_file, create_stop_file, ensure_state_dir,
    read_result_file, delete_result_file, append_run_log,
    build_phase_prompt, invoke_harness, _build_marker,
    is_gate_phase, is_production_phase, is_human_owned_transition,
)
from core.registry import get_adapter



# ---- Test helpers ------------------------------------------------------------

def _write_config(tmp: Path, extra_toml: str = "") -> Path:
    """Write a minimal markdown config and return its path."""
    sdlc = tmp / ".sdlc"
    sdlc.mkdir(parents=True, exist_ok=True)
    board = sdlc / "board"
    board.mkdir(exist_ok=True)
    state = sdlc / "state"
    state.mkdir(exist_ok=True)

    config_path = sdlc / "config.toml"
    config_path.write_text(textwrap.dedent(f"""\
        [ticketing]
        provider = "markdown"
        project  = "TKT"

        [markdown]
        board_dir = "{board}"
        state_dir = "{state}"
        me        = "testbot"

        [board.roles]
        backlog     = "Backlog"
        todo        = "To Do"
        in_progress = "In Progress"
        review      = "In Review"
        qa_ready    = "QA Ready"
        done        = "Done"
        blocked     = "Blocked"

        [board.ownership]
        "todo->in_progress"   = "agent"
        "in_progress->review" = "agent"
        "review->qa_ready"    = "agent"
        "qa_ready->qa"        = "human"
        "deploy_ready->done"  = "human"

        [issue_types]
        full_sdlc   = ["Story", "Bug"]
        deliverable = ["Task", "Chore"]

        [queries]
        tier1 = 'status = "To Do" ORDER BY priority DESC'

        [vcs]
        repo           = "test/repo"
        default_branch = "main"

        [build]
        build = "true"
        test  = "true"

        [timetracking]
        provider = "none"

        [run]
        harness_cmd = "{tmp}/stub.sh {{prompt}}"
        max_iterations = 30
        max_phase_attempts = 3
        invocation_timeout = 10

        {extra_toml}
    """))
    return config_path



def _create_ticket(board_dir: Path, key: str, status: str = "To Do",
                   issue_type: str = "Story") -> None:
    """Create a minimal ticket markdown file."""
    board_dir.mkdir(parents=True, exist_ok=True)
    (board_dir / f"{key}.md").write_text(textwrap.dedent(f"""\
        ---
        type: {issue_type}
        status: {status}
        priority: High
        assignee: testbot
        blocked_by: []
        blocks: []
        ---
        # Test ticket {key}

        Description for testing.
    """))


def _write_stub_script(tmp: Path, results: list[dict]) -> Path:
    """Write a stub harness that writes scripted result.json per call.

    Each invocation pops the next result from a sequence file.
    """
    seq_file = tmp / "stub_sequence.json"
    seq_file.write_text(json.dumps(results))

    stub = tmp / "stub.sh"
    stub.write_text(textwrap.dedent(f"""\
        #!/bin/sh
        # Stub harness: reads next result from sequence, writes to result.json
        SEQ="{seq_file}"
        # Find the state dir from the prompt (look for .sdlc/state/run)
        PROMPT="$*"
        # Extract result path from the prompt
        RESULT_PATH=$(echo "$PROMPT" | grep -oE '[^ ]*result\\.json' | head -1)
        if [ -z "$RESULT_PATH" ]; then
            # Fallback: find result.json path in any .sdlc/state/run dir
            exit 1
        fi
        # Pop first element from sequence
        RESULT=$(python3 -c "
import json, sys
seq = json.loads(open('$SEQ').read())
if not seq:
    sys.exit(1)
print(json.dumps(seq[0]))
open('$SEQ', 'w').write(json.dumps(seq[1:]))
")
        if [ $? -ne 0 ]; then
            exit 1
        fi
        # Ensure parent dir exists
        mkdir -p "$(dirname "$RESULT_PATH")"
        echo "$RESULT" > "$RESULT_PATH"
    """))
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    return stub



# ---- Test cases --------------------------------------------------------------

class TestRunConfig(unittest.TestCase):
    def test_loads_from_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            cfg_path = _write_config(tmp)
            config = Config.load(str(cfg_path))
            rc = RunConfig(config)
            self.assertIn("stub.sh", rc.harness_cmd)
            self.assertEqual(rc.max_iterations, 30)
            self.assertEqual(rc.max_phase_attempts, 3)
            self.assertEqual(rc.invocation_timeout, 10)

    def test_missing_harness_cmd_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            sdlc = tmp / ".sdlc"
            sdlc.mkdir()
            (sdlc / "board").mkdir()
            (sdlc / "state").mkdir()
            cfg = sdlc / "config.toml"
            cfg.write_text(textwrap.dedent("""\
                [ticketing]
                provider = "markdown"
                [markdown]
                board_dir = ".sdlc/board"
                state_dir = ".sdlc/state"
                [board.roles]
                done = "Done"
                [run]
                max_iterations = 5
            """))
            config = Config.load(str(cfg))
            with self.assertRaises(Exception) as ctx:
                RunConfig(config)
            self.assertIn("harness_cmd", str(ctx.exception))


class TestTicketMarker(unittest.TestCase):
    def test_write_and_read_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            cfg_path = _write_config(tmp)
            config = Config.load(str(cfg_path))
            adapter = get_adapter(config)
            board_dir = Path(config.provider_cfg["board_dir"])
            _create_ticket(board_dir, "TKT-1")

            state = {"phase": "P3", "attempt": 1, "outcome": "advance",
                     "next": "P4", "updated": "2026-01-01T00:00:00Z"}
            write_ticket_marker(adapter, "TKT-1", state)

            marker = read_ticket_marker(adapter, "TKT-1")
            self.assertIsNotNone(marker)
            self.assertEqual(marker["phase"], "P3")
            self.assertEqual(marker["outcome"], "advance")

    def test_read_nonexistent_ticket(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            cfg_path = _write_config(tmp)
            config = Config.load(str(cfg_path))
            adapter = get_adapter(config)
            marker = read_ticket_marker(adapter, "NOPE-99")
            self.assertIsNone(marker)

    def test_local_mirror_resumes_when_comments_are_unreadable(self):
        """Only the markdown adapter exposes raw comment bodies. Every other
        backend has to recover the marker from the local mirror, or a resume
        silently restarts the pipeline at P1."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            cfg_path = _write_config(tmp)
            config = Config.load(str(cfg_path))
            adapter = get_adapter(config)
            board_dir = Path(config.provider_cfg["board_dir"])
            _create_ticket(board_dir, "TKT-1")
            state_dir = ensure_state_dir(config, "TKT-1")

            state = {"phase": "P6", "attempt": 2, "outcome": "retry",
                     "updated": "2026-01-01T00:00:00Z"}
            write_ticket_marker(adapter, "TKT-1", state, state_dir)

            # Stand in for a backend whose comments cannot be read back.
            class NoRawComments:
                def __getattr__(self, name):
                    if name == "_read_raw":
                        raise AttributeError(name)
                    return getattr(adapter, name)

            blind = NoRawComments()
            self.assertIsNone(read_ticket_marker(blind, "TKT-1"))

            marker = read_ticket_marker(blind, "TKT-1", state_dir)
            self.assertIsNotNone(marker)
            self.assertEqual(marker["phase"], "P6")
            self.assertEqual(marker["attempt"], 2)
            self.assertEqual(marker["outcome"], "retry")

    def test_local_mirror_ignores_corrupt_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            cfg_path = _write_config(tmp)
            config = Config.load(str(cfg_path))
            adapter = get_adapter(config)
            state_dir = ensure_state_dir(config, "TKT-1")
            (state_dir / "marker.json").write_text("{not json")

            class NoRawComments:
                def __getattr__(self, name):
                    if name == "_read_raw":
                        raise AttributeError(name)
                    return getattr(adapter, name)

            self.assertIsNone(read_ticket_marker(NoRawComments(), "TKT-1", state_dir))



class TestLocalState(unittest.TestCase):
    def test_ensure_state_dir_creates(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            cfg_path = _write_config(tmp)
            config = Config.load(str(cfg_path))
            d = ensure_state_dir(config, "TKT-1")
            self.assertTrue(d.is_dir())
            self.assertTrue(str(d).endswith("run/TKT-1"))

    def test_stop_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            cfg_path = _write_config(tmp)
            config = Config.load(str(cfg_path))
            d = ensure_state_dir(config, "TKT-1")
            self.assertFalse(check_stop_file(d))
            create_stop_file(config, "TKT-1")
            self.assertTrue(check_stop_file(d))

    def test_result_file_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            d = tmp / "state"
            d.mkdir()
            result = {"phase": "P3", "outcome": "advance",
                      "next": "P4", "reason": "tests pass"}
            (d / "result.json").write_text(json.dumps(result))
            read = read_result_file(d)
            self.assertEqual(read["outcome"], "advance")
            delete_result_file(d)
            self.assertIsNone(read_result_file(d))

    def test_result_file_invalid_outcome(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            d = tmp / "state"
            d.mkdir()
            (d / "result.json").write_text('{"phase":"P3","outcome":"bogus"}')
            self.assertIsNone(read_result_file(d))

    def test_result_file_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(read_result_file(Path(tmp)))

    def test_result_file_corrupt_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            d = tmp / "state"
            d.mkdir()
            (d / "result.json").write_text("not json at all")
            self.assertIsNone(read_result_file(d))

    def test_run_log_append(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            d = tmp / "state"
            d.mkdir()
            append_run_log(d, {"iteration": 1, "phase": "P1"})
            append_run_log(d, {"iteration": 2, "phase": "P2"})
            lines = (d / "run.log").read_text().splitlines()
            self.assertEqual(len(lines), 2)
            self.assertEqual(json.loads(lines[0])["phase"], "P1")



class TestGateLogic(unittest.TestCase):
    def test_p9_is_gate(self):
        self.assertTrue(is_gate_phase("P9"))
        self.assertFalse(is_gate_phase("P8"))
        self.assertFalse(is_gate_phase("P3"))

    def test_production_phase_default_human(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            cfg_path = _write_config(tmp)
            config = Config.load(str(cfg_path))
            # deploy_ready->done is human-owned in our test config
            self.assertTrue(is_production_phase("P10", config))
            self.assertFalse(is_production_phase("P5", config))

    def test_production_phase_agent_owned(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            cfg_path = _write_config(tmp)
            config = Config.load(str(cfg_path))
            # Override ownership in memory to grant agent deploy.
            config.ownership["deploy_ready->done"] = "agent"
            self.assertFalse(is_production_phase("P10", config))

    def test_human_owned_transition(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            cfg_path = _write_config(tmp)
            config = Config.load(str(cfg_path))
            self.assertTrue(
                is_human_owned_transition(config, "qa_ready", "qa"))
            self.assertFalse(
                is_human_owned_transition(config, "todo", "in_progress"))


class TestPhasePrompt(unittest.TestCase):
    def test_prompt_contains_key_and_phase(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            prompt = build_phase_prompt("TKT-5", "P3", 2, d)
            self.assertIn("TKT-5", prompt)
            self.assertIn("P3", prompt)
            self.assertIn("Attempt: 2", prompt)
            self.assertIn("result.json", prompt)
            self.assertIn("advance|retry|blocked|gate", prompt)



class TestInvokeHarness(unittest.TestCase):
    def test_dry_run_does_not_execute(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            rc, out = invoke_harness(
                "echo should_not_run", "test prompt", 10, d, dry_run=True)
            self.assertEqual(rc, 0)
            self.assertIn("dry-run", out)

    def test_successful_invocation(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            rc, out = invoke_harness("echo hello", "ignored", 10, d)
            self.assertEqual(rc, 0)
            self.assertIn("hello", out)

    def test_timeout_counts_as_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            rc, out = invoke_harness("sleep 30", "ignored", 1, d)
            self.assertEqual(rc, 1)
            self.assertIn("timeout", out.lower())

    def test_nonzero_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            rc, out = invoke_harness("exit 42", "ignored", 10, d)
            self.assertEqual(rc, 42)



class TestDriverLoop(unittest.TestCase):
    """Integration tests running the full driver with a stub harness."""

    def _setup_env(self, results, ticket_status="In Progress",
                   extra_toml="", ownership_override=None):
        """Set up a temp env with config, ticket, and stub harness."""
        self.tmp = tempfile.mkdtemp()
        tmp = Path(self.tmp)
        cfg_path = _write_config(tmp, extra_toml)
        self.config = Config.load(str(cfg_path))
        board_dir = Path(self.config.provider_cfg["board_dir"])
        _create_ticket(board_dir, "TKT-1", status=ticket_status)
        _write_stub_script(tmp, results)
        return self.config

    def tearDown(self):
        import shutil
        if hasattr(self, 'tmp') and os.path.isdir(self.tmp):
            shutil.rmtree(self.tmp, ignore_errors=True)

    def test_advance_through_phases(self):
        """Stub returns advance for P1->P4, driver advances each time."""
        results = [
            {"phase": "P1", "outcome": "advance", "next": "P1.5",
             "reason": "triaged"},
            {"phase": "P1.5", "outcome": "advance", "next": "P2",
             "reason": "full_sdlc"},
            {"phase": "P2", "outcome": "advance", "next": "P3",
             "reason": "planned"},
            {"phase": "P3", "outcome": "advance", "next": "P4",
             "reason": "implemented"},
            {"phase": "P4", "outcome": "advance", "next": "P5",
             "reason": "review clean"},
            {"phase": "P5", "outcome": "advance", "next": "P6",
             "reason": "PR opened"},
            {"phase": "P6", "outcome": "advance", "next": "P7",
             "reason": "CI green"},
            {"phase": "P7", "outcome": "advance", "next": "P8",
             "reason": "approved"},
            {"phase": "P8", "outcome": "advance", "next": "P9",
             "reason": "preview live"},
        ]
        config = self._setup_env(results)
        rc_cfg = RunConfig(config)
        driver = RunDriver(config, rc_cfg, key="TKT-1")
        # Start from P1 since ticket exists already.
        driver.phase = "P1"
        driver.attempt = 1
        driver._init_state()

        rc = driver._loop()
        # Should halt at P9 (qa_ready gate)
        self.assertEqual(rc, 0)
        self.assertEqual(driver.phase, "P9")


    def test_retry_then_advance(self):
        """First attempt fails (retry), second succeeds (advance)."""
        results = [
            {"phase": "P1", "outcome": "retry", "next": "P1",
             "reason": "flaky"},
            {"phase": "P1", "outcome": "advance", "next": "P1.5",
             "reason": "ok now"},
            {"phase": "P1.5", "outcome": "gate", "next": "P2",
             "reason": "stop for test"},
        ]
        config = self._setup_env(results)
        rc_cfg = RunConfig(config)
        driver = RunDriver(config, rc_cfg, key="TKT-1")
        driver.phase = "P1"
        driver.attempt = 1
        driver._init_state()

        rc = driver._loop()
        self.assertEqual(rc, 0)  # gate stops cleanly

    def test_three_attempts_transitions_blocked(self):
        """3 failed attempts on one phase -> blocked transition + comment."""
        results = [
            {"phase": "P3", "outcome": "retry", "next": "P3",
             "reason": "test fail 1"},
            {"phase": "P3", "outcome": "retry", "next": "P3",
             "reason": "test fail 2"},
            {"phase": "P3", "outcome": "retry", "next": "P3",
             "reason": "test fail 3"},
        ]
        config = self._setup_env(results)
        rc_cfg = RunConfig(config)
        driver = RunDriver(config, rc_cfg, key="TKT-1")
        driver.phase = "P3"
        driver.attempt = 1
        driver._init_state()

        rc = driver._loop()
        self.assertEqual(rc, 1)
        # Verify ticket was transitioned to Blocked.
        adapter = get_adapter(config)
        ticket = adapter.view("TKT-1")
        self.assertEqual(ticket.status, "Blocked")


    def test_stop_file_honored_mid_run(self):
        """STOP file present before iteration -> exits cleanly."""
        results = [
            {"phase": "P1", "outcome": "advance", "next": "P1.5",
             "reason": "ok"},
        ]
        config = self._setup_env(results)
        rc_cfg = RunConfig(config)
        driver = RunDriver(config, rc_cfg, key="TKT-1")
        driver.phase = "P1"
        driver.attempt = 1
        driver._init_state()
        # Drop STOP file before first iteration.
        create_stop_file(config, "TKT-1")

        rc = driver._loop()
        self.assertEqual(rc, 0)
        # Should not have run any iterations.
        self.assertEqual(driver.iteration, 0)

    def test_qa_ready_gate_halt(self):
        """Driver halts when it reaches P9 (qa_ready) regardless of agent."""
        config = self._setup_env([])
        rc_cfg = RunConfig(config)
        driver = RunDriver(config, rc_cfg, key="TKT-1")
        driver.phase = "P9"
        driver.attempt = 1
        driver._init_state()

        rc = driver._loop()
        self.assertEqual(rc, 0)
        self.assertEqual(driver.iteration, 0)

    def test_human_owned_transition_refused(self):
        """Driver refuses to run phase whose transition is human-owned."""
        config = self._setup_env([])
        rc_cfg = RunConfig(config)
        driver = RunDriver(config, rc_cfg, key="TKT-1")
        # P9's transition is qa_ready->qa which is human-owned
        driver.phase = "P9"
        driver.attempt = 1
        driver._init_state()

        rc = driver._loop()
        self.assertEqual(rc, 0)


    def test_missing_result_json_treated_as_retry(self):
        """If harness doesn't write result.json, counts as failed attempt."""
        # Empty results list means stub will exit without writing anything.
        config = self._setup_env([])
        rc_cfg = RunConfig(config)
        # Override max_phase_attempts to 2 for faster test.
        rc_cfg.max_phase_attempts = 2
        driver = RunDriver(config, rc_cfg, key="TKT-1")
        driver.max_phase_attempts = 2
        driver.phase = "P3"
        driver.attempt = 1
        driver._init_state()

        rc = driver._loop()
        self.assertEqual(rc, 1)  # blocked after 2 attempts
        # Should have run 2 iterations.
        self.assertEqual(driver.iteration, 2)

    def test_timeout_counts_as_attempt(self):
        """Timeout on harness invocation counts as a failed attempt."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            cfg_path = _write_config(tmp)
            config = Config.load(str(cfg_path))
            board_dir = Path(config.provider_cfg["board_dir"])
            _create_ticket(board_dir, "TKT-1")
            # Write a stub that sleeps forever.
            stub = tmp / "stub.sh"
            stub.write_text("#!/bin/sh\nsleep 60\n")
            stub.chmod(stub.stat().st_mode | stat.S_IEXEC)

            # Patch harness_cmd to use sleep stub, with 1s timeout.
            rc_cfg = RunConfig(config)
            rc_cfg.harness_cmd = f"{stub} {{prompt}}"
            rc_cfg.invocation_timeout = 1
            rc_cfg.max_phase_attempts = 2

            driver = RunDriver(config, rc_cfg, key="TKT-1")
            driver.max_phase_attempts = 2
            driver.timeout = 1
            driver.phase = "P3"
            driver.attempt = 1
            driver._init_state()

            rc = driver._loop()
            self.assertEqual(rc, 1)
            self.assertEqual(driver.iteration, 2)


    def test_resume_from_ticket_marker(self):
        """Kill the driver, rerun — resumes from the ticket marker."""
        results_first = [
            {"phase": "P1", "outcome": "advance", "next": "P1.5",
             "reason": "triaged"},
            {"phase": "P1.5", "outcome": "advance", "next": "P2",
             "reason": "routed"},
        ]
        config = self._setup_env(results_first)
        rc_cfg = RunConfig(config)
        driver = RunDriver(config, rc_cfg, key="TKT-1")
        driver.phase = "P1"
        driver.attempt = 1
        driver._init_state()
        # Limit iterations so we stop after 2 phases.
        driver.max_iterations = 2
        rc = driver._loop()
        self.assertEqual(rc, 1)  # max iterations

        # Now resume. The marker should show P2 as the halted phase.
        adapter = get_adapter(config)
        marker = read_ticket_marker(adapter, "TKT-1")
        self.assertIsNotNone(marker)
        self.assertEqual(marker["phase"], "P2")

        # Write new results for the resumed run.
        tmp = Path(self.tmp)
        seq_file = tmp / "stub_sequence.json"
        new_results = [
            {"phase": "P2", "outcome": "advance", "next": "P3",
             "reason": "planned"},
            {"phase": "P3", "outcome": "gate", "next": "P4",
             "reason": "testing stop"},
        ]
        seq_file.write_text(json.dumps(new_results))

        # Create a new driver that resumes.
        driver2 = RunDriver(config, rc_cfg, key="TKT-1")
        driver2._init_state()
        driver2._resume_from_marker()
        self.assertEqual(driver2.phase, "P2")
        rc2 = driver2._loop()
        self.assertEqual(rc2, 0)  # gate

    def test_dry_run_invokes_nothing(self):
        """--dry-run prints the prompt but does not invoke."""
        config = self._setup_env([])
        rc_cfg = RunConfig(config)
        driver = RunDriver(config, rc_cfg, key="TKT-1", dry_run=True)
        driver.phase = "P3"
        driver.attempt = 1
        driver._init_state()

        rc = driver._loop()
        self.assertEqual(rc, 0)
        # No iterations beyond the dry-run check.
        self.assertEqual(driver.iteration, 1)


class TestDriverStatus(unittest.TestCase):
    def test_status_no_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            cfg_path = _write_config(tmp)
            config = Config.load(str(cfg_path))
            board_dir = Path(config.provider_cfg["board_dir"])
            _create_ticket(board_dir, "TKT-1")
            rc_cfg = RunConfig(config)
            driver = RunDriver(config, rc_cfg, key="TKT-1")
            status = driver.status()
            self.assertIsNone(status.get("phase"))

    def test_status_with_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            cfg_path = _write_config(tmp)
            config = Config.load(str(cfg_path))
            board_dir = Path(config.provider_cfg["board_dir"])
            _create_ticket(board_dir, "TKT-1")
            adapter = get_adapter(config)
            write_ticket_marker(adapter, "TKT-1", {
                "phase": "P5", "attempt": 1, "outcome": "advance",
                "next": "P6", "updated": "2026-01-01T00:00:00Z",
            })
            rc_cfg = RunConfig(config)
            driver = RunDriver(config, rc_cfg, key="TKT-1")
            status = driver.status()
            self.assertEqual(status["phase"], "P5")


if __name__ == "__main__":
    unittest.main()
