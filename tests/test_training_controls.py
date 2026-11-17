"""State, lineage and immutable-content checks; byte fixtures are not real training."""
import json
from pathlib import Path
import pytest
from test_training_orchestration import context
from dictionforge.audio import atomic_json, digest
from dictionforge.jobs import Conflict
from dictionforge.records import get, put
from dictionforge.training_config import feature_fingerprint


def paused(c):
    job = c.training.submit(c.jobs, "preflight", "train")
    c.jobs.claim()
    c.jobs.control(job["id"], "pause", "old-pause")
    c.jobs.status(job["id"], "PAUSED")
    area = c.jobs.root / "runs" / job["id"]
    point = area / "checkpoints/p1.pt"
    point.parent.mkdir()
    point.write_bytes(b"checkpoint integrity fixture only")
    snap = area / "snapshots/p1.pth"
    snap.parent.mkdir()
    snap.write_bytes(b"snapshot integrity fixture only")
    entry = dict(id="p1", stage="s2", kind="pause", path=str(point), sha256=digest(point), bytes=point.stat().st_size,
        snapshot=dict(path=str(snap), sha256=digest(snap), bytes=snap.stat().st_size))
    atomic_json(point.parent/"index.json", dict(last="p1", checkpoints=[entry]))
    return job, entry


def test_resume_receipt_and_stale_controls_are_atomic(context):
    c = context
    job, entry = paused(c)
    assert c.training.snapshot(c.jobs, job["id"])["pending_controls"] == []
    # Simulate an old persisted receipt gap from an earlier application version.
    with c.jobs.connect(write=True) as db:
        db.execute("DELETE FROM control_receipts WHERE request='old-pause'")
    result = c.training.control(c.jobs, job["id"], "resume", "resume")
    assert result["state"] == "QUEUED"
    assert get(c.jobs, "run", job["id"])["resume_checkpoint"]["id"] == entry["id"]
    assert c.training.snapshot(c.jobs, job["id"])["pending_controls"] == []
    c.jobs.claim()
    assert c.training.control(c.jobs, job["id"], "resume", "resume")["state"] == "RUNNING"
    with pytest.raises(Conflict):
        c.training.control(c.jobs, job["id"], "resume", "another-resume")
    assert len(c.jobs.list()) == 1


def test_derive_preserves_parent_and_receipt(context):
    c = context
    job, entry = paused(c)
    parent = get(c.jobs, "run", job["id"])
    before = Path(parent["run_dir"])/"run_meta.json"
    checksum = digest(before)
    child = c.training.derive(c.jobs, job["id"], entry["id"], "derive")
    run = get(c.jobs, "run", child["id"])
    assert run["parent_run"] == run["resume_source_run"] == job["id"]
    assert run["parent_checkpoint"] == entry["id"]
    assert run["config"] == parent["config"] and run["run_dir"] != parent["run_dir"]
    assert digest(before) == checksum and get(c.jobs, "run", job["id"]) == parent
    assert c.training.derive(c.jobs, job["id"], entry["id"], "derive")["id"] == child["id"]
    assert c.training.checkpoints(c.jobs, job["id"])["checkpoints"][0]["protected"]


def test_corrupt_checkpoint_never_falls_back_to_base(context):
    c = context
    job, entry = paused(c)
    Path(entry["path"]).write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="没有已登记"):
        c.training.control(c.jobs, job["id"], "resume", "resume")
    assert c.jobs.get(job["id"])["state"] == "PAUSED"


def test_derived_run_interrupted_before_first_point_reuses_frozen_parent(context):
    c = context
    job, entry = paused(c)
    child = c.training.derive(c.jobs, job["id"], entry["id"], "derive")
    assert c.jobs.claim()["id"] == child["id"]
    c.jobs.status(child["id"], "INTERRUPTED")
    points = c.training.checkpoints(c.jobs, child["id"])
    assert points["checkpoints"] == []
    assert points["inherited_start_point"]["selection"]["source_run"] == job["id"]
    c.training.control(c.jobs, child["id"], "resume", "child-resume")
    assert get(c.jobs, "run", child["id"])["resume_source_run"] == job["id"]


def test_pause_arriving_after_final_boundary_has_terminal_receipt(context):
    c = context
    job = c.training.submit(c.jobs, "preflight", "train")
    c.jobs.claim()
    c.jobs.control(job["id"], "pause", "late-pause")
    c.jobs.status(job["id"], "COMPLETED", result={"fixture": "final boundary already saved"})
    assert c.training.snapshot(c.jobs, job["id"])["pending_controls"] == []
    receipts = [e for e in c.jobs.events(job["id"], 0) if e["kind"] == "control_finished"]
    assert receipts[-1]["payload"]["terminal_state"] == "COMPLETED"


def test_feature_rebuild_statistics_do_not_invalidate_content(context):
    c = context
    path = c.features / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest.update(version_fingerprint=c.version["fingerprint"], upstream_lock=c.preflight["upstream_lock"],
        asset_lock=c.preflight["asset_lock"], ready=True, seconds=10, reuse={"text": 0})
    c.preflight["features_content_sha256"] = feature_fingerprint(manifest)
    atomic_json(path, manifest)
    put(c.jobs, "preflight", "preflight", c.preflight)
    manifest.update(seconds=1, reuse={"text": 1})
    atomic_json(path, manifest)
    c.training.submit(c.jobs, "preflight", "pass")
    manifest["entries"]["clip.wav"]["files"]["4-cnhubert/clip.wav.pt"] = "changed"
    atomic_json(path, manifest)
    with pytest.raises(Conflict, match="特征内容"):
        c.training.submit(c.jobs, "preflight", "fail")
