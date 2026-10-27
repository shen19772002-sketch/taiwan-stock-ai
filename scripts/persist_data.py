#!/usr/bin/env python3
"""Retain research state in a separate, append-only Git branch.

No force pushes, no embedded credentials, and no assumption that failed Git
authentication means the branch does not exist. Local Git remotes are supported
by the functions for isolated integration tests; the CLI accepts GitHub names.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import tempfile
import zlib


BRANCH = "market-data"
ARCHIVE_DIR = ".persistence"
CHUNK_SIZE = 40 * 1024 * 1024
BUFFER_SIZE = 1024 * 1024


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(BUFFER_SIZE), b""):
            digest.update(block)
    return digest.hexdigest()


def check_database(path: Path) -> None:
    with sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True) as database:
        if database.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("SQLite integrity check failed; archive was not changed")


def pack_database(source: Path, target: Path, chunk_size: int = CHUNK_SIZE) -> None:
    """Write a deterministic, bounded-file snapshot without loading the DB in RAM."""
    if not 1 <= chunk_size <= CHUNK_SIZE:
        raise ValueError("Database archive parts must be between 1 byte and 40 MiB")
    target.mkdir(parents=True)
    with tempfile.TemporaryDirectory(prefix="stock-db-snapshot-") as temporary:
        snapshot = Path(temporary) / "market.sqlite3"
        check_database(source)
        with sqlite3.connect(f"file:{source.resolve()}?mode=ro", uri=True) as database:
            with sqlite3.connect(snapshot) as backup:
                database.backup(backup)
        compressed = Path(temporary) / "market.sqlite3.gz"
        with snapshot.open("rb") as original, compressed.open("wb") as output:
            # Empty filename and fixed mtime avoid timestamps/random temp paths
            # changing every Git commit when database contents are identical.
            with gzip.GzipFile(filename="", mode="wb", fileobj=output, mtime=0) as zipped:
                shutil.copyfileobj(original, zipped, BUFFER_SIZE)
        parts = []
        with compressed.open("rb") as stream:
            while block := stream.read(chunk_size):
                name = f"part-{len(parts):06d}.gzpart"
                (target / name).write_bytes(block)
                parts.append({"name": name, "size": len(block),
                              "sha256": hashlib.sha256(block).hexdigest()})
        manifest = {
            "version": 1, "format": "sqlite-gzip-parts", "parts": parts,
            "database_size": snapshot.stat().st_size, "database_sha256": file_digest(snapshot),
            "compressed_size": compressed.stat().st_size, "compressed_sha256": file_digest(compressed),
        }
        (target / "manifest.json").write_text(
            json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def unpack_database(archive: Path, destination: Path) -> None:
    """Verify parts, compressed stream, raw bytes and SQLite before installation."""
    ensure_plain_files(archive)
    try:
        manifest = json.loads((archive / "manifest.json").read_text(encoding="utf-8"))
        if (manifest["version"] != 1 or manifest["format"] != "sqlite-gzip-parts"
                or not isinstance(manifest["parts"], list) or not manifest["parts"]):
            raise ValueError("Unsupported database archive manifest")
        for key in ("database_size", "compressed_size"):
            if type(manifest[key]) is not int or manifest[key] <= 0:
                raise ValueError("Invalid database archive size")
        for key in ("database_sha256", "compressed_sha256"):
            if not re.fullmatch(r"[0-9a-f]{64}", manifest[key]):
                raise ValueError("Invalid database archive digest")
        with tempfile.TemporaryDirectory(prefix="stock-db-restore-") as temporary:
            compressed = Path(temporary) / "database.gz"
            with compressed.open("wb") as stream:
                for index, part in enumerate(manifest["parts"]):
                    name = f"part-{index:06d}.gzpart"
                    if (part["name"] != name or type(part["size"]) is not int
                            or not 1 <= part["size"] <= CHUNK_SIZE
                            or not re.fullmatch(r"[0-9a-f]{64}", part["sha256"])):
                        raise ValueError("Invalid or unordered database archive part")
                    source = archive / name
                    if source.stat().st_size != part["size"] or file_digest(source) != part["sha256"]:
                        raise ValueError("Database archive part checksum mismatch")
                    with source.open("rb") as chunk:
                        shutil.copyfileobj(chunk, stream, BUFFER_SIZE)
            if (compressed.stat().st_size != manifest["compressed_size"]
                    or file_digest(compressed) != manifest["compressed_sha256"]):
                raise ValueError("Database archive compressed checksum mismatch")
            database_path = Path(temporary) / "market.sqlite3"
            size = 0
            with gzip.open(compressed, "rb") as stream, database_path.open("wb") as database:
                while block := stream.read(BUFFER_SIZE):
                    size += len(block)
                    if size > manifest["database_size"]:
                        raise ValueError("Database archive exceeds declared size")
                    database.write(block)
            if size != manifest["database_size"] or file_digest(database_path) != manifest["database_sha256"]:
                raise ValueError("Database archive uncompressed checksum mismatch")
            check_database(database_path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(database_path, destination)
    except (KeyError, TypeError, json.JSONDecodeError, OSError, sqlite3.DatabaseError, EOFError, zlib.error) as error:
        raise ValueError("Invalid or damaged database archive; history was not restored") from error


@contextmanager
def git_environment():
    environment = os.environ.copy()
    environment.update(GIT_TERMINAL_PROMPT="0")
    with tempfile.TemporaryDirectory(prefix="stock-git-auth-") as temporary:
        if environment.get("GITHUB_TOKEN"):
            askpass = Path(temporary) / "askpass.sh"
            askpass.write_text(
                '#!/bin/sh\ncase "$1" in\n'
                '  *Username*) printf "%s\\n" "x-access-token" ;;\n'
                '  *Password*) printf "%s\\n" "$GITHUB_TOKEN" ;;\n'
                '  *) exit 1 ;;\nesac\n', encoding="utf-8")
            askpass.chmod(0o700)
            environment["GIT_ASKPASS"] = str(askpass)
        yield environment


def git(*arguments: str, cwd: Path | None = None, env: dict | None = None,
        allowed: tuple[int, ...] = (0,)) -> subprocess.CompletedProcess:
    result = subprocess.run(["git", *arguments], cwd=cwd, env=env, capture_output=True, text=True)
    if result.returncode not in allowed:
        # Avoid echoing URLs, authentication helpers, or arbitrary remote output.
        raise RuntimeError(f"Git {arguments[0]} failed (exit {result.returncode}); history was not overwritten")
    return result


def ensure_plain_files(directory: Path) -> None:
    if directory.is_symlink():
        raise ValueError("Data directories must not be symlinks")
    for path in directory.rglob("*"):
        if path.is_symlink() or (not path.is_dir() and not path.is_file()):
            raise ValueError("Data history may only contain regular files and directories")


def restore(remote: str, state: Path, data: Path, env: dict | None = None) -> None:
    if state.exists():
        raise ValueError("State directory already exists; refusing to reuse an uncertain checkout")
    if data.exists() and (data.is_symlink() or any(data.iterdir())):
        raise ValueError("Restore requires an empty data directory; existing local data is preserved")
    listing = git("ls-remote", "--exit-code", "--heads", remote, f"refs/heads/{BRANCH}",
                  env=env, allowed=(0, 2))
    state.parent.mkdir(parents=True, exist_ok=True)
    if listing.returncode == 0:
        git("clone", "--single-branch", "--branch", BRANCH, remote, str(state), env=env)
        stored = state / "data"
        packed = state / ARCHIVE_DIR
        if not packed.is_dir() and not (stored / "market.sqlite3").is_file():
            raise ValueError("Existing history branch lacks its database; refusing to start from empty history")
        ensure_plain_files(stored)
        # Complete all database verification before exposing recovered state.
        with tempfile.TemporaryDirectory(prefix="restore-", dir=state) as temporary:
            recovered = Path(temporary) / "market.sqlite3"
            if packed.is_dir():
                unpack_database(packed, recovered)
            else:
                check_database(stored / "market.sqlite3")
                shutil.copyfile(stored / "market.sqlite3", recovered)
            if stored.exists():
                shutil.copytree(stored, data, dirs_exist_ok=True,
                                ignore=shutil.ignore_patterns("market.sqlite3", "*-wal", "*-shm", "*-journal"))
            else:
                data.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(recovered, data / "market.sqlite3")
    else:
        # Exit 2 specifically means no matching ref, unlike connection/auth failure.
        git("init", "--initial-branch", BRANCH, str(state), env=env)
        git("remote", "add", "origin", remote, cwd=state, env=env)
        (state / "README.md").write_text(
            "# Research data archive\n\n"
            "Generated by the daily research workflow. Retains SQLite observations, "
            "original source responses, and immutable forecast reports. Do not delete "
            "or reset this branch when refreshing application code.\n", encoding="utf-8")
    data.mkdir(parents=True, exist_ok=True)


def save(state: Path, data: Path, env: dict | None = None, *, chunk_size: int = CHUNK_SIZE) -> bool:
    if not (state / ".git").is_dir():
        raise ValueError("Run restore before save")
    if not (data / "market.sqlite3").is_file():
        raise ValueError("Expected data/market.sqlite3 is missing; archive was not changed")
    ensure_plain_files(data)
    destination = state / "data"
    destination.mkdir(parents=True, exist_ok=True)
    ensure_plain_files(destination)
    # Reject changed immutable evidence before preparing any replacement DB.
    for source in data.rglob("*"):
        relative = source.relative_to(data)
        target = destination / relative
        if source.is_file() and relative.parts[0] in {"raw", "reports"} and target.exists():
            if file_digest(source) != file_digest(target):
                raise ValueError("An archived raw response or report was changed; refusing to overwrite it")
    with tempfile.TemporaryDirectory(prefix="snapshot-", dir=state) as temporary:
        prepared = Path(temporary) / ARCHIVE_DIR
        pack_database(data / "market.sqlite3", prepared, chunk_size)
        packed = state / ARCHIVE_DIR
        if packed.exists():
            ensure_plain_files(packed)
        # Only generated archive parts are replaced; old raw/report files are
        # retained. Prior DB snapshots remain recoverable through Git history.
        if packed.exists():
            shutil.rmtree(packed)
        shutil.move(str(prepared), packed)
    legacy_database = destination / "market.sqlite3"
    if legacy_database.exists():
        legacy_database.unlink()  # Replaced with a verified compressed snapshot.
    # Merge new files without deleting old raw responses or report snapshots.
    for source in data.rglob("*"):
        target = destination / source.relative_to(data)
        if source.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif source == data / "market.sqlite3":
            continue
        elif not source.name.endswith(("-wal", "-shm", "-journal")):
            relative = source.relative_to(data)
            if relative.parts[0] in {"raw", "reports"} and target.exists():
                continue
            shutil.copy2(source, target)
    git("config", "user.name", "github-actions[bot]", cwd=state, env=env)
    git("config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com", cwd=state, env=env)
    git("add", "--all", "--", "data", ARCHIVE_DIR, "README.md", cwd=state, env=env)
    staged = git("diff", "--cached", "--quiet", cwd=state, env=env, allowed=(0, 1))
    if staged.returncode == 0:
        # A previous attempt may have committed locally before its push failed.
        # A clean index alone is not evidence that the remote archive is saved.
        head = git("rev-parse", "HEAD", cwd=state, env=env).stdout.strip()
        remote_head = git("rev-parse", "--verify", f"refs/remotes/origin/{BRANCH}",
                          cwd=state, env=env, allowed=(0, 128))
        if remote_head.returncode == 0 and remote_head.stdout.strip() == head:
            return False
    else:
        git("commit", "-m", "Archive daily research observations and reports", cwd=state, env=env)
    # A concurrently changed remote is rejected. Never rebase/force a stale database.
    git("push", "origin", f"HEAD:refs/heads/{BRANCH}", cwd=state, env=env)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["restore", "save"])
    parser.add_argument("--repository", required=True, help="GitHub owner/repository")
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repository):
        parser.error("--repository must be an owner/repository name")
    remote = f"https://github.com/{args.repository}.git"
    with git_environment() as environment:
        if args.action == "restore":
            restore(remote, args.state_dir.resolve(), args.data_dir.resolve(), environment)
            print("History restored; no remote changes were made.")
        else:
            changed = save(args.state_dir.resolve(), args.data_dir.resolve(), environment)
            print("History archived." if changed else "History already up to date.")


if __name__ == "__main__":
    main()
