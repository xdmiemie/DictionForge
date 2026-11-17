"""Checkpoint I/O failure fixtures; full optimizer/GPU restore is tested separately."""
import errno
import json
import os
from pathlib import Path
import subprocess

import pytest

from dictionforge import checkpoints
from dictionforge.audio import atomic_json, digest
from dictionforge.jobs import Jobs
from dictionforge.records import put


@pytest.fixture
def context(tmp_path):
    jobs = Jobs(tmp_path / "data")
    run_id = "run"
    run_dir = jobs.root / "runs" / run_id
    area = run_dir / "checkpoints"
    area.mkdir(parents=True)
    put(jobs, "run", run_id, dict(id=run_id, run_dir=str(run_dir)))
    def entry(key, kind="periodic", snapshot=True):
        path = area / (key + ".pt")
        path.write_bytes((key + " checkpoint fixture").encode())
        result = dict(id=key, path=str(path), sha256=digest(path), bytes=path.stat().st_size,
                      stage="s1", kind=kind, metrics={"optimizer_updates": 1})
        if snapshot:
            preview = run_dir / "snapshots" / (key + ".ckpt")
            preview.parent.mkdir(parents=True, exist_ok=True)
            preview.write_bytes((key + " inference fixture").encode())
            result["snapshot"] = dict(path=str(preview), sha256=digest(preview), bytes=preview.stat().st_size)
        return result
    def index(entries, last=None):
        atomic_json(area / "index.json", dict(checkpoints=entries, last=last or entries[-1]["id"]))
    return jobs, run_id, run_dir, area, entry, index


def test_unindexed_half_written_and_orphan_files_are_never_selected(context):
    jobs, run_id, run_dir, area, entry, index = context
    first = entry("first")
    index([first])
    (area / "second.pt.abcd.part").write_bytes(b"half written")
    entry("orphan")  # Fully written, but index publication never happened.
    assert [item["id"] for item in checkpoints.list_checkpoints(jobs, run_id)] == ["first"]
    assert checkpoints.select_checkpoint(jobs, run_id)["id"] == "first"
    with pytest.raises(checkpoints.CheckpointInvalid, match="CHECKPOINT_NOT_INDEXED"):
        checkpoints.select_checkpoint(jobs, run_id, "orphan")


def test_latest_corrupt_reports_explicit_fallback_but_explicit_id_never_falls_back(context):
    jobs, run_id, run_dir, area, entry, index = context
    first, latest = entry("first"), entry("latest")
    index([first, latest])
    Path(latest["path"]).write_bytes(b"x" * latest["bytes"])
    rows = checkpoints.list_checkpoints(jobs, run_id)
    assert rows[0]["valid"] and not rows[1]["valid"]
    assert "HASH_MISMATCH" in rows[1]["reason"]
    selected = checkpoints.select_checkpoint(jobs, run_id)
    assert selected["id"] == "first" and selected["selection"]["fallback"]
    assert selected["selection"]["failed_entries"][0]["id"] == "latest"
    with pytest.raises(checkpoints.CheckpointInvalid) as error:
        checkpoints.select_checkpoint(jobs, run_id, "latest")
    assert error.value.available_fallback == "first"
    assert error.value.failed_entries[0]["id"] == "latest"


def test_missing_last_pointer_is_explained(context):
    jobs, run_id, run_dir, area, entry, index = context
    first = entry("first")
    index([first], last="uncommitted")
    selected = checkpoints.select_checkpoint(jobs, run_id)
    assert selected["id"] == "first"
    assert selected["selection"]["failed_entries"] == [{"id": "uncommitted", "reason": "INDEX_LAST_MISSING"}]


@pytest.mark.parametrize("damage", ["missing", "size", "hash", "temporary"])
def test_snapshot_is_part_of_complete_checkpoint_integrity(context, damage):
    jobs, run_id, run_dir, area, entry, index = context
    checkpoint = entry("point", kind="pause")
    target = Path(checkpoint["snapshot"]["path"])
    if damage == "missing":
        target.unlink()
    elif damage == "size":
        target.write_bytes(b"short")
    elif damage == "hash":
        target.write_bytes(b"x" * checkpoint["snapshot"]["bytes"])
    else:
        checkpoint["snapshot"]["path"] = str(target.with_suffix(".part"))
        target.rename(checkpoint["snapshot"]["path"])
    index([checkpoint])
    rows = checkpoints.list_checkpoints(jobs, run_id)
    assert not rows[0]["valid"] and "SNAPSHOT_" in rows[0]["reason"]
    with pytest.raises(checkpoints.CheckpointInvalid, match="不能回退官方底模"):
        checkpoints.select_checkpoint(jobs, run_id)


def test_absolute_and_relative_escape_are_rejected_before_reading(context):
    jobs, run_id, run_dir, area, entry, index = context
    first, second = entry("absolute"), entry("relative")
    first["path"] = str(run_dir.parent / "another-run" / "model.pt")
    second["path"] = "../run_meta.json"
    index([first, second])
    rows = checkpoints.list_checkpoints(jobs, run_id)
    assert all(not row["valid"] and "PATH_INVALID" in row["reason"] for row in rows)


def test_junction_or_symlink_escape_is_rejected(context):
    jobs, run_id, run_dir, area, entry, index = context
    checkpoint = entry("alias")
    outside = jobs.root / "outside"
    outside.mkdir()
    target = outside / "model.pt"
    target.write_bytes(Path(checkpoint["path"]).read_bytes())
    alias = area / "alias-directory"
    try:
        if os.name == "nt":
            script = jobs.root / "create-test-junction.ps1"
            script.write_text("param([string]$Target,[string]$Link)\n$ErrorActionPreference='Stop'\n"
                              "New-Item -ItemType Junction -Path $Link -Target $Target | Out-Null\n", encoding="utf-8")
            subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script),
                            "-Target", str(outside), "-Link", str(alias)], check=True, capture_output=True,
                           creationflags=subprocess.CREATE_NO_WINDOW, timeout=20)
        else:
            alias.symlink_to(outside, target_is_directory=True)
    except (OSError, AttributeError, subprocess.SubprocessError):
        pytest.skip("本环境不能创建重解析点；普通越界检查由独立测试覆盖")
    try:
        checkpoint["path"] = str(alias / "model.pt")
        index([checkpoint])
        assert not checkpoints.list_checkpoints(jobs, run_id)[0]["valid"]
        assert "重解析路径" in checkpoints.list_checkpoints(jobs, run_id)[0]["reason"]
        assert target.read_bytes().endswith(b"checkpoint fixture")
    finally:
        if os.name == "nt":
            alias.rmdir()
        else:
            alias.unlink()


def test_legacy_stage_final_warns_but_control_point_requires_snapshot(context):
    jobs, run_id, run_dir, area, entry, index = context
    old = entry("old", kind="stage_final", snapshot=False)
    pause = entry("pause", kind="pause", snapshot=False)
    index([old, pause])
    rows = checkpoints.list_checkpoints(jobs, run_id)
    assert rows[0]["valid"] and rows[0]["warning"]
    assert not rows[1]["valid"] and "SNAPSHOT_MISSING" in rows[1]["reason"]


def test_duplicate_ids_and_malformed_index_fail_closed(context):
    jobs, run_id, run_dir, area, entry, index = context
    first = entry("point")
    index([first, first])
    assert all(not row["valid"] and "DUPLICATE" in row["reason"] for row in checkpoints.list_checkpoints(jobs, run_id))
    (area / "index.json").write_bytes(b'{"checkpoints":')
    with pytest.raises(checkpoints.CheckpointInvalid, match="INDEX_INVALID"):
        checkpoints.select_checkpoint(jobs, run_id)


def test_disk_full_index_publication_preserves_last_confirmed_checkpoint(context, monkeypatch):
    jobs, run_id, run_dir, area, entry, index = context
    first = entry("first")
    index([first])
    before = (area / "index.json").read_bytes()
    pending = entry("new-valid-but-uncommitted")
    def full_disk(*args, **kwargs):
        raise OSError(errno.ENOSPC, "fixture: disk full during atomic index replace")
    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", full_disk)
        with pytest.raises(OSError, match="disk full"):
            index([first, pending])
    assert (area / "index.json").read_bytes() == before
    assert not list(area.glob("*.tmp"))
    assert checkpoints.select_checkpoint(jobs, run_id)["id"] == "first"


def test_retention_protects_recent_control_and_derived_reference_points(context):
    jobs, run_id, run_dir, area, entry, index = context
    points = [entry("p"+str(i)) for i in range(1, 7)]
    points.extend([entry("pause", kind="pause"), entry("final", kind="stage_final")])
    index(points)
    child = jobs.root / "runs" / "derived"
    child.mkdir()
    put(jobs, "run", "derived", dict(id="derived", run_dir=str(child), parent_run=run_id, parent_checkpoint="p1"))
    assert checkpoints.is_referenced(jobs, run_id, "p1")
    assert not checkpoints.is_referenced(jobs, run_id, "p2")
    plan = checkpoints.retention_plan(jobs, run_id)
    assert {p["id"] for p in plan["removable"]} == {"p2", "p3"}
    assert {p["id"] for p in plan["protected"]} == {"p1", "p4", "p5", "p6", "pause", "final"}
    assert plan["advisory_only"]
    assert all(Path(p["path"]).is_file() for p in points)


def test_corrupt_reference_registry_never_makes_deletion_safe(context):
    jobs, run_id, run_dir, area, entry, index = context
    index([entry("point")])
    with jobs.connect(write=True) as db:
        db.execute("INSERT INTO records VALUES('run','corrupt','not-json')")
    with pytest.raises(checkpoints.CheckpointInvalid, match="REFERENCE_INVALID"):
        checkpoints.retention_plan(jobs, run_id)


def test_retention_commits_index_then_deletes_only_unprotected_files(context):
    jobs, run_id, run_dir, area, entry, index = context
    points = [entry("p"+str(i)) for i in range(1, 7)]
    points.extend([entry("pause", kind="pause"), entry("final", kind="stage_final")])
    index(points)
    put(jobs, "run", "derived", dict(parent_run=run_id, parent_checkpoint="p1"))
    report = checkpoints.apply_retention(jobs, run_id)
    assert report["removed"] == ["p2", "p3"] and not report["orphan_files"]
    assert len(report["deleted_files"]) == 4
    assert {p["id"] for p in checkpoints.list_checkpoints(jobs, run_id)} == {"p1", "p4", "p5", "p6", "pause", "final"}
    assert checkpoints.select_checkpoint(jobs, run_id)["id"] == "final"
    for item in points:
        assert Path(item["path"]).exists() == (item["id"] not in {"p2", "p3"})
        assert Path(item["snapshot"]["path"]).exists() == (item["id"] not in {"p2", "p3"})


def test_retention_index_failure_preserves_every_file_and_last(context, monkeypatch):
    jobs, run_id, run_dir, area, entry, index = context
    points = [entry("p"+str(i)) for i in range(5)]
    index(points)
    before = (area / "index.json").read_bytes()
    def full_disk(*args, **kwargs):
        raise OSError(errno.ENOSPC, "fixture: disk full during retention index replace")
    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", full_disk)
        with pytest.raises(OSError, match="disk full"):
            checkpoints.apply_retention(jobs, run_id)
    assert (area / "index.json").read_bytes() == before
    assert all(Path(item["path"]).exists() and Path(item["snapshot"]["path"]).exists() for item in points)
    assert checkpoints.select_checkpoint(jobs, run_id)["id"] == "p4"


def test_retention_unlink_failure_leaves_unindexed_orphan(context, monkeypatch):
    jobs, run_id, run_dir, area, entry, index = context
    points = [entry("p"+str(i)) for i in range(4)]
    index(points)
    unlink = Path.unlink
    def denied(path, *args, **kwargs):
        if path == Path(points[0]["path"]):
            raise PermissionError("fixture: file is in use")
        return unlink(path, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", denied)
    report = checkpoints.apply_retention(jobs, run_id)
    assert report["removed"] == ["p0"] and len(report["orphan_files"]) == 1
    assert Path(points[0]["path"]).exists()
    assert [p["id"] for p in checkpoints.list_checkpoints(jobs, run_id)] == ["p1", "p2", "p3"]


def test_retention_holds_same_database_write_lock_as_derived_registration(context, monkeypatch):
    import sqlite3
    jobs, run_id, run_dir, area, entry, index = context
    points = [entry("p"+str(i)) for i in range(4)]
    index(points)
    atomic = checkpoints.atomic_json
    checked = []
    def publishing(path, value):
        with sqlite3.connect(jobs.db, timeout=0) as db:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                db.execute("BEGIN IMMEDIATE")
        checked.append(True)
        return atomic(path, value)
    monkeypatch.setattr(checkpoints, "atomic_json", publishing)
    checkpoints.apply_retention(jobs, run_id)
    assert checked == [True]


def test_retention_protects_same_run_pending_historical_resume(context):
    jobs, run_id, run_dir, area, entry, index = context
    points = [entry("p"+str(i)) for i in range(5)]
    index(points)
    put(jobs, "run", run_id, dict(id=run_id, run_dir=str(run_dir), resume_source_run=run_id,
                                  resume_checkpoint=points[0]))
    assert checkpoints.is_referenced(jobs, run_id, "p0")
    report = checkpoints.apply_retention(jobs, run_id)
    assert report["removed"] == ["p1"]
    assert Path(points[0]["path"]).exists()
