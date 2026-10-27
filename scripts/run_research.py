#!/usr/bin/env python3
"""Run the real research/archive commands with bounded retries and durable logs.

Exit zero means the command finished, including an honest waiting-data report.
Arguments and environment values are deliberately absent from the metadata logs.
"""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import threading
import time


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def run_logged(command, label, log_dir, attempts=3, retry_delay=20, timeout=900):
    """Return the final process exit code; retain every attempt, even after success."""
    if not command:
        raise ValueError("A real command is required")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,59}", label):
        raise ValueError("Invalid log label")
    if not 1 <= attempts <= 3 or not 0 <= retry_delay <= 60 or not 0 < timeout <= 3600:
        raise ValueError("Retries, delay, or timeout exceed their bounds")
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    results = []
    summary = {"label": label, "started_at": utc_now(), "attempts": results}
    summary_path = log_dir / f"{label}.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    for attempt in range(1, attempts + 1):
        started_at = utc_now()
        log_path = log_dir / f"{label}-attempt-{attempt}.log"
        print(f"[{label}] attempt {attempt}/{attempts}; log: {log_path}", flush=True)
        reason = None
        with log_path.open("w", encoding="utf-8") as log:
            try:
                process = subprocess.Popen(
                    command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace", bufsize=1,
                    start_new_session=os.name == "posix",
                )

                def stream_output():
                    for line in process.stdout:
                        log.write(line)
                        log.flush()
                        print(line, end="", flush=True)

                reader = threading.Thread(target=stream_output, daemon=True)
                reader.start()
                try:
                    returncode = process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    reason = "timeout"
                    # Stop descendants too, so a retry cannot overlap an old writer.
                    try:
                        if os.name == "posix":
                            os.killpg(process.pid, signal.SIGKILL)
                        else:
                            process.kill()
                    except ProcessLookupError:
                        pass  # The process may exit between timeout and termination.
                    process.wait()
                    returncode = 124
                finally:
                    reader.join()
                    process.stdout.close()
            except OSError as exc:
                reason = type(exc).__name__
                returncode = 127
                # Do not print argv or the environment; either may contain secrets.
                log.write(f"Could not start command: {reason}\n")
            if reason:
                log.write(f"Runner failure: {reason}\n")
        if returncode < 0:
            returncode = 128 - returncode
        results.append({
            "attempt": attempt, "started_at": started_at, "finished_at": utc_now(),
            "exit_code": returncode, "reason": reason, "log": log_path.name,
        })
        summary.update({"finished_at": utc_now(), "exit_code": returncode})
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        if returncode == 0:
            return 0
        print(f"[{label}] attempt {attempt} failed (exit {returncode}).", flush=True)
        if attempt < attempts:
            time.sleep(retry_delay)
    return returncode


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", required=True)
    parser.add_argument("--log-dir", type=Path, default=Path("run-logs"))
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--retry-delay", type=float, default=20)
    parser.add_argument("--timeout", type=float, default=900)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    try:
        return run_logged(command, args.label, args.log_dir, args.attempts, args.retry_delay, args.timeout)
    except ValueError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    sys.exit(main())
