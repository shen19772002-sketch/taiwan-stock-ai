"""Local bare repositories exercise real restore/push behavior without credentials."""

import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess

import pytest


SPEC = importlib.util.spec_from_file_location(
    "persist_data", Path(__file__).parents[1] / "scripts" / "persist_data.py")
archive = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(archive)


def remote_repo(tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    return str(remote)


def seed_data(data, value=1):
    data.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(data / "market.sqlite3") as db:
        db.execute("CREATE TABLE IF NOT EXISTS fixture(value INTEGER)")
        db.execute("INSERT INTO fixture VALUES (?)", (value,))
    (data / "raw").mkdir(exist_ok=True)
    (data / "raw" / f"response-{value}.json").write_text('{"test_fixture": true}')


def test_first_archive_restore_and_incremental_history(tmp_path):
    remote = remote_repo(tmp_path)
    first, data = tmp_path / "first", tmp_path / "data"
    archive.restore(remote, first, data)
    seed_data(data)
    assert archive.save(first, data)
    assert not archive.save(first, data)

    second, recovered = tmp_path / "second", tmp_path / "recovered"
    archive.restore(remote, second, recovered)
    with sqlite3.connect(recovered / "market.sqlite3") as db:
        assert db.execute("SELECT value FROM fixture").fetchall() == [(1,)]
    seed_data(recovered, 2)
    assert archive.save(second, recovered)
    third, again = tmp_path / "third", tmp_path / "again"
    archive.restore(remote, third, again)
    with sqlite3.connect(again / "market.sqlite3") as db:
        assert db.execute("SELECT value FROM fixture").fetchall() == [(1,), (2,)]
    assert len(list((again / "raw").glob("*.json"))) == 2


def test_network_or_missing_remote_is_not_a_new_empty_branch(tmp_path):
    with pytest.raises(RuntimeError, match="ls-remote failed"):
        archive.restore(str(tmp_path / "unavailable.git"), tmp_path / "state", tmp_path / "data")
    assert not (tmp_path / "state").exists()


def test_retry_pushes_pending_commit_after_transient_failure(tmp_path, monkeypatch):
    remote = remote_repo(tmp_path)
    state, data = tmp_path / "state", tmp_path / "data"
    archive.restore(remote, state, data)
    seed_data(data)
    real_git = archive.git
    attempts = []

    def interrupted_push(*args, **kwargs):
        if args[0] == "push":
            attempts.append(args)
            if len(attempts) == 1:
                raise RuntimeError("Git push failed (synthetic temporary network failure)")
        return real_git(*args, **kwargs)

    monkeypatch.setattr(archive, "git", interrupted_push)
    with pytest.raises(RuntimeError, match="push failed"):
        archive.save(state, data)
    pending = real_git("rev-parse", "HEAD", cwd=state).stdout.strip()
    assert archive.save(state, data)
    assert len(attempts) == 2
    assert real_git("rev-parse", "HEAD", cwd=state).stdout.strip() == pending
    archive.restore(remote, tmp_path / "verified", tmp_path / "verified-data")
    with sqlite3.connect(tmp_path / "verified-data" / "market.sqlite3") as db:
        assert db.execute("SELECT value FROM fixture").fetchall() == [(1,)]
    assert not archive.save(state, data)


def test_restore_preserves_existing_local_data(tmp_path):
    data = tmp_path / "data"
    seed_data(data)
    with pytest.raises(ValueError, match="empty data"):
        archive.restore(remote_repo(tmp_path), tmp_path / "state", data)
    assert (data / "market.sqlite3").exists()


def test_concurrent_push_is_rejected_instead_of_overwriting_history(tmp_path):
    remote = remote_repo(tmp_path)
    state, data = tmp_path / "initial", tmp_path / "data"
    archive.restore(remote, state, data)
    seed_data(data)
    archive.save(state, data)
    a, adata, b, bdata = [tmp_path / name for name in ("a", "adata", "b", "bdata")]
    archive.restore(remote, a, adata)
    archive.restore(remote, b, bdata)
    seed_data(adata, 2)
    seed_data(bdata, 3)
    archive.save(a, adata)
    with pytest.raises(RuntimeError, match="push failed"):
        archive.save(b, bdata)
    archive.restore(remote, tmp_path / "verified", tmp_path / "verified-data")
    with sqlite3.connect(tmp_path / "verified-data" / "market.sqlite3") as db:
        assert db.execute("SELECT value FROM fixture").fetchall() == [(1,), (2,)]


def test_reject_changed_immutable_raw_response(tmp_path):
    remote = remote_repo(tmp_path)
    state, data = tmp_path / "state", tmp_path / "data"
    archive.restore(remote, state, data)
    seed_data(data)
    archive.save(state, data)
    (data / "raw" / "response-1.json").write_text("modified fixture")
    with pytest.raises(ValueError, match="archived raw"):
        archive.save(state, data)


def test_reject_symlink_in_data(tmp_path):
    data = tmp_path / "data"
    seed_data(data)
    (data / "unsafe").symlink_to(tmp_path / "outside")
    with pytest.raises(ValueError, match="regular files"):
        archive.ensure_plain_files(data)


def test_multipart_snapshot_is_bounded_deterministic_and_restorable(tmp_path):
    remote = remote_repo(tmp_path)
    state, data = tmp_path / "state", tmp_path / "data"
    archive.restore(remote, state, data)
    seed_data(data)
    assert archive.save(state, data, chunk_size=32)
    packed = state / archive.ARCHIVE_DIR
    manifest = json.loads((packed / "manifest.json").read_text())
    assert len(manifest["parts"]) > 1
    assert all(0 < part["size"] <= 32 for part in manifest["parts"])
    assert not (state / "data" / "market.sqlite3").exists()
    assert not archive.save(state, data, chunk_size=32)
    recovered = tmp_path / "recovered"
    archive.restore(remote, tmp_path / "next", recovered)
    with sqlite3.connect(recovered / "market.sqlite3") as db:
        assert db.execute("SELECT value FROM fixture").fetchall() == [(1,)]
    assert not (recovered / archive.ARCHIVE_DIR).exists()


@pytest.mark.parametrize("damage", ["part", "missing_part", "raw_digest", "part_order", "path"])
def test_corrupt_archive_never_installs_database(tmp_path, damage):
    data = tmp_path / "data"
    seed_data(data)
    packed = tmp_path / "packed"
    archive.pack_database(data / "market.sqlite3", packed, chunk_size=32)
    manifest_path = packed / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    part_path = packed / manifest["parts"][0]["name"]
    if damage == "part":
        part_path.write_bytes(b"corrupted")
    elif damage == "missing_part":
        part_path.unlink()
    elif damage == "raw_digest":
        manifest["database_sha256"] = "0" * 64
    elif damage == "part_order":
        manifest["parts"].reverse()
    elif damage == "path":
        manifest["parts"][0]["name"] = "../outside"
    manifest_path.write_text(json.dumps(manifest))
    destination = tmp_path / "restored" / "market.sqlite3"
    with pytest.raises(ValueError, match="archive"):
        archive.unpack_database(packed, destination)
    assert not destination.exists()


def test_legacy_plain_database_branch_migrates_without_history_loss(tmp_path):
    remote = remote_repo(tmp_path)
    legacy = tmp_path / "legacy"
    archive.git("init", "--initial-branch", archive.BRANCH, str(legacy))
    archive.git("remote", "add", "origin", remote, cwd=legacy)
    archive.git("config", "user.name", "test", cwd=legacy)
    archive.git("config", "user.email", "test@example.invalid", cwd=legacy)
    seed_data(legacy / "data")
    (legacy / "README.md").write_text("Legacy archive test fixture")
    archive.git("add", ".", cwd=legacy)
    archive.git("commit", "-m", "Legacy archive", cwd=legacy)
    archive.git("push", "origin", archive.BRANCH, cwd=legacy)
    state, data = tmp_path / "state", tmp_path / "data"
    archive.restore(remote, state, data)
    seed_data(data, 2)
    archive.save(state, data)
    assert not (state / "data" / "market.sqlite3").exists()
    assert (state / archive.ARCHIVE_DIR / "manifest.json").is_file()
    recovered = tmp_path / "restored"
    archive.restore(remote, tmp_path / "second", recovered)
    with sqlite3.connect(recovered / "market.sqlite3") as db:
        assert db.execute("SELECT value FROM fixture").fetchall() == [(1,), (2,)]
    assert len(list((recovered / "raw").glob("*.json"))) == 2


def test_database_only_archive_restores_without_raw_directory(tmp_path):
    remote = remote_repo(tmp_path)
    state, data = tmp_path / "state", tmp_path / "data"
    archive.restore(remote, state, data)
    with sqlite3.connect(data / "market.sqlite3") as db:
        db.execute("CREATE TABLE fixture(value INTEGER)")
    archive.save(state, data)
    archive.restore(remote, tmp_path / "next", tmp_path / "recovered")
    archive.check_database(tmp_path / "recovered" / "market.sqlite3")
