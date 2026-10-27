"""Delayed cron jobs and reruns must retain the original trading-session date."""

import importlib.util
import json
from pathlib import Path
import subprocess

import pytest


SPEC = importlib.util.spec_from_file_location(
    "research_schedule", Path(__file__).parents[1] / "scripts" / "research_schedule.py")
schedule = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(schedule)


@pytest.mark.parametrize("cron,phase", [
    ("20 0 * * 1-5", "preopen"), ("30 4 * * 1-5", "intraday"),
    ("30 7 * * 1-5", "postclose"), ("30 10 * * 1-5", "final"),
    ("30 12 * * 1-5", "final"),
])
def test_all_slots_anchor_to_taiwan_session_despite_after_midnight_creation(cron, phase):
    result = schedule.scheduled_session(cron, "2026-10-27T18:10:10Z")
    assert result["as_of"] == "2026-10-27"
    assert result["phase"] == phase


def test_exact_preopen_slot_and_taipei_timestamp():
    assert schedule.scheduled_session("20 0 * * 1-5", "2026-10-27T00:20:00Z")["as_of"] == "2026-10-27"
    result = schedule.scheduled_session("30 12 * * 1-5", "2026-10-28T02:10:10+08:00")
    assert result["as_of"] == "2026-10-27"
    assert result["scheduled_for"] == "2026-10-27T12:30:00+00:00"


def test_weekend_and_monday_before_slot_use_previous_friday():
    for created_at in ("2026-10-24T18:10:00Z", "2026-10-26T00:10:00Z"):
        result = schedule.scheduled_session("20 0 * * 1-5", created_at)
        assert result["as_of"] == "2026-10-23"


def test_rerun_uses_original_creation_instead_of_new_attempt_start(monkeypatch):
    calls = []

    def api(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, json.dumps({
            "event": "schedule", "created_at": "2026-10-27T18:10:10Z",
            "run_started_at": "2026-10-29T01:00:00Z",
        }))

    monkeypatch.setattr(schedule.subprocess, "run", api)
    for _ in range(2):
        created = schedule.original_run_created_at("owner/repo", "40557421175")
        assert schedule.scheduled_session("30 12 * * 1-5", created)["as_of"] == "2026-10-27"
    assert all("repos/owner/repo/actions/runs/40557421175" in call for call in calls)


def test_cli_writes_session_outputs_for_workflow(tmp_path, capsys):
    output = tmp_path / "github-output"
    assert schedule.main([
        "--schedule", "30 7 * * 1-5", "--run-created-at", "2026-10-27T18:10:10Z",
        "--output-file", str(output),
    ]) == 0
    assert output.read_text() == "as_of=2026-10-27\nphase=postclose\n"
    assert json.loads(capsys.readouterr().out)["as_of"] == "2026-10-27"


def test_api_failure_does_not_fall_back_to_today_or_log_secret(monkeypatch):
    monkeypatch.setattr(schedule.subprocess, "run", lambda *a, **k:
                        subprocess.CompletedProcess(a[0], 1, "", "fixture-token-never-print"))
    with pytest.raises(RuntimeError, match="Cannot read original GitHub run metadata") as error:
        schedule.original_run_created_at("owner/repo", "123")
    assert "fixture-token" not in str(error.value)


@pytest.mark.parametrize("cron,created_at", [
    ("0 0 * * *", "2026-10-27T18:10:10Z"),
    ("20 0 * * 1-5", "2026-10-27T18:10:10"),
])
def test_unknown_schedule_or_missing_timezone_is_rejected(cron, created_at):
    with pytest.raises(ValueError):
        schedule.scheduled_session(cron, created_at)
