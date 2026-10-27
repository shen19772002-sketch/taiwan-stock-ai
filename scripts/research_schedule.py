#!/usr/bin/env python3
"""Anchor a delayed scheduled report to its original Taiwan trading session.

GitHub exposes the original run creation time, not an exact scheduled timestamp.
Use the latest matching weekday cron slot at or before that creation time. A job
queue or later rerun must not move the report into another Taiwan trading day.
"""

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import subprocess
from zoneinfo import ZoneInfo


SLOTS = {
    "20 0 * * 1-5": (0, 20, "preopen"),
    "30 4 * * 1-5": (4, 30, "intraday"),
    "30 7 * * 1-5": (7, 30, "postclose"),
    "30 10 * * 1-5": (10, 30, "final"),
    "30 12 * * 1-5": (12, 30, "final"),
}


def scheduled_session(schedule, run_created_at):
    """Return the session date and phase, independent of the runner's clock."""
    if schedule not in SLOTS:
        raise ValueError("Unknown research schedule; refusing to guess its session")
    created = datetime.fromisoformat(run_created_at.replace("Z", "+00:00"))
    if created.tzinfo is None:
        raise ValueError("Run creation time must include a timezone")
    created = created.astimezone(timezone.utc)
    hour, minute, phase = SLOTS[schedule]
    slot = created.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if slot > created:
        slot -= timedelta(days=1)
    while slot.weekday() >= 5:
        slot -= timedelta(days=1)
    return {
        "phase": phase,
        "as_of": slot.astimezone(ZoneInfo("Asia/Taipei")).date().isoformat(),
        "scheduled_for": slot.isoformat(),
        "run_created_at": created.isoformat(),
        "date_basis": "latest matching cron slot before original GitHub run creation",
    }


def original_run_created_at(repository, run_id):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository or ""):
        raise ValueError("A valid GitHub repository is required")
    if not str(run_id).isdigit():
        raise ValueError("A numeric GitHub run ID is required")
    result = subprocess.run(
        ["gh", "api", f"repos/{repository}/actions/runs/{run_id}",
         "--jq", "{created_at,event}"],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode:
        # Do not include stderr, command arguments or token-bearing environment.
        raise RuntimeError(f"Cannot read original GitHub run metadata (exit {result.returncode})")
    metadata = json.loads(result.stdout)
    if metadata.get("event") != "schedule" or not metadata.get("created_at"):
        raise ValueError("Original run metadata does not describe a scheduled run")
    return metadata["created_at"]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schedule", required=True, choices=SLOTS)
    parser.add_argument("--run-created-at", help="Original API created_at, for reproducible verification")
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY"))
    parser.add_argument("--run-id", default=os.environ.get("GITHUB_RUN_ID"))
    parser.add_argument("--output-file", type=Path,
                        default=os.environ.get("GITHUB_OUTPUT"))
    args = parser.parse_args(argv)
    created_at = args.run_created_at or original_run_created_at(args.repository, args.run_id)
    session = scheduled_session(args.schedule, created_at)
    if args.output_file:
        with args.output_file.open("a", encoding="utf-8") as output:
            output.write(f"as_of={session['as_of']}\nphase={session['phase']}\n")
    print(json.dumps(session, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
