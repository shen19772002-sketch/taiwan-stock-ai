"""Exercise the scheduler wrapper with real child processes and actual failures."""

import importlib.util
import json
from pathlib import Path
import sys

import pytest


SPEC = importlib.util.spec_from_file_location(
    "run_research", Path(__file__).parents[1] / "scripts" / "run_research.py")
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def test_transient_error_recovers_and_preserves_both_attempt_logs(tmp_path):
    marker = tmp_path / "attempted"
    command = [sys.executable, "-c", """
import pathlib, sys
marker = pathlib.Path(sys.argv[1])
if not marker.exists():
    marker.touch()
    print('temporary network failure', file=sys.stderr)
    sys.exit(7)
print('real process completed')
""", str(marker)]
    assert runner.run_logged(command, "research", tmp_path, retry_delay=0) == 0
    summary = json.loads((tmp_path / "research.json").read_text())
    assert [entry["exit_code"] for entry in summary["attempts"]] == [7, 0]
    assert "temporary network failure" in (tmp_path / "research-attempt-1.log").read_text()
    assert "real process completed" in (tmp_path / "research-attempt-2.log").read_text()
    assert not (tmp_path / "research-attempt-3.log").exists()


def test_final_failure_remains_failed_with_bounded_attempts(tmp_path):
    command = [sys.executable, "-c", "import sys; print('archive failed'); sys.exit(9)"]
    assert runner.run_logged(command, "archive", tmp_path, attempts=3, retry_delay=0) == 9
    summary = json.loads((tmp_path / "archive.json").read_text())
    assert summary["exit_code"] == 9
    assert len(summary["attempts"]) == 3
    assert len(list(tmp_path.glob("*.log"))) == 3


def test_waiting_data_is_success_and_not_retried(tmp_path):
    command = [sys.executable, "-c", "print('{\"status\": \"waiting_for_data\"}')"]
    assert runner.run_logged(command, "postclose", tmp_path, retry_delay=0) == 0
    summary = json.loads((tmp_path / "postclose.json").read_text())
    assert len(summary["attempts"]) == 1
    assert "waiting_for_data" in (tmp_path / "postclose-attempt-1.log").read_text()


def test_timeout_is_logged_and_returns_failure(tmp_path):
    command = [sys.executable, "-c", "import time; time.sleep(10)"]
    assert runner.run_logged(command, "research", tmp_path, attempts=1, timeout=0.05) == 124
    summary = json.loads((tmp_path / "research.json").read_text())
    assert summary["attempts"][0]["reason"] == "timeout"


def test_missing_executable_is_recorded_without_argument_or_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "fixture-secret-never-log")
    command = [str(tmp_path / "missing"), "fixture-secret-argument"]
    assert runner.run_logged(command, "restore", tmp_path, attempts=1) == 127
    output = (tmp_path / "restore-attempt-1.log").read_text() + (tmp_path / "restore.json").read_text()
    assert "FileNotFoundError" in output
    assert "fixture-secret" not in output


@pytest.mark.parametrize("kwargs", [
    {"attempts": 0}, {"attempts": 4}, {"retry_delay": -1}, {"retry_delay": 61}, {"timeout": 0},
])
def test_unbounded_retry_configuration_is_rejected(tmp_path, kwargs):
    with pytest.raises(ValueError):
        runner.run_logged([sys.executable, "-c", "pass"], "research", tmp_path, **kwargs)
