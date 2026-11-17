"""Training binding/queue tests use byte fixtures, never simulated model updates."""
import json
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

from dictionforge.audio import atomic_json, digest
from dictionforge.jobs import Jobs, Conflict
from dictionforge.records import get, put
from dictionforge.training_config import normalize_config, config_fingerprint


@pytest.fixture
def context(tmp_path, monkeypatch):
    from dictionforge import training
    workspace = tmp_path / "workspace"
    upstream = workspace / "vendor/gpt-sovits"
    data = workspace / "data"
    (workspace / "configs").mkdir(parents=True)
    jobs = Jobs(data)
    monkeypatch.setattr(training, "ROOT", workspace)
    monkeypatch.setattr(training, "verify_source", lambda root, upstream: {"fixture_only": True})
    settings = SimpleNamespace(upstream=upstream, worker_python=sys.executable)
    monkeypatch.setattr(training.Settings, "from_environment", classmethod(lambda cls: settings))
    # These bytes test hashing only; no claim that they are torch checkpoints.
    assets = []
    weights = {}
    for name, relative in {"gpt": "GPT_SoVITS/pretrained_models/s1v3.ckpt",
                           "sovits": "GPT_SoVITS/pretrained_models/gsv-v4-pretrained/s2Gv4.pth"}.items():
        path = upstream / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((name + " trusted byte fixture").encode())
        weights[name] = path
        assets.append({"upstream_path": relative, "sha256": digest(path)})
    atomic_json(workspace/"configs/assets.lock.json", {"assets": assets})
    atomic_json(workspace/"configs/upstream.lock.json", {"commit": "fixture-only"})
    source = data / "dataset.wav"
    source.write_bytes(b"immutable audio byte fixture")
    version = dict(id="dataset-version", role="fixture", dataset_id="dataset", fingerprint="version-fingerprint",
                   clips=[dict(id="clip", path=str(source), sha256=digest(source), split="train")])
    put(jobs, "version", version["id"], version)
    features = data / "features" / version["id"]
    features.mkdir(parents=True)
    index = features / "2-name2text.txt"
    index.write_bytes(b"feature index fixture")
    ssl = features / "4-cnhubert" / "clip.wav.pt"
    ssl.parent.mkdir()
    ssl.write_bytes(b"feature tensor byte fixture")
    atomic_json(features / "manifest.json", dict(indexes={index.name: digest(index)},
        entries={"clip.wav": {"files": {"4-cnhubert/clip.wav.pt": digest(ssl)}}}))
    config = normalize_config({"version_id": version["id"]})
    preflight = dict(id="preflight", ready=True, config=config, config_fingerprint=config_fingerprint(config),
        version_fingerprint=version["fingerprint"], upstream_lock=digest(workspace/"configs/upstream.lock.json"),
        asset_lock=digest(workspace/"configs/assets.lock.json"), features_manifest_sha256=digest(features/"manifest.json"))
    put(jobs, "preflight", "preflight", preflight)
    return SimpleNamespace(training=training, jobs=jobs, workspace=workspace, upstream=upstream,
        source=source, features=features, index=index, ssl=ssl, preflight=preflight, version=version, weights=weights)


def test_submit_freezes_verified_inputs_and_idempotent_receipt(context):
    c = context
    job = c.training.submit(c.jobs, "preflight", "request")
    run = get(c.jobs, "run", job["id"])
    assert run["config"] == c.preflight["config"]
    assert run["initial_hashes"] == {name: digest(path) for name, path in c.weights.items()}
    assert run["version"] == c.version
    assert json.loads((Path(run["run_dir"])/"run_meta.json").read_text())["initial_hashes"] == run["initial_hashes"]
    c.preflight["config"]["s1"]["target"] = 99
    put(c.jobs, "preflight", "preflight", c.preflight)
    assert c.training.submit(c.jobs, "preflight", "request")["id"] == job["id"]
    assert get(c.jobs, "run", job["id"])["config"]["s1"]["target"] == 1
    assert len(c.jobs.list()) == 1
    with pytest.raises(Conflict):
        c.training.submit(c.jobs, "different-preflight", "request")


@pytest.mark.parametrize("change", ["config", "version", "upstream_lock", "asset_lock", "feature_manifest", "index", "ssl", "audio"])
def test_stale_preflight_refused_before_queue(context, change):
    c = context
    if change == "config":
        c.preflight["config"]["s2"]["batch_size"] += 1
        put(c.jobs, "preflight", "preflight", c.preflight)
    elif change == "version":
        c.version["fingerprint"] = "changed-version"
        put(c.jobs, "version", c.version["id"], c.version)
    else:
        paths = {"upstream_lock": c.workspace/"configs/upstream.lock.json",
                 "asset_lock": c.workspace/"configs/assets.lock.json",
                 "feature_manifest": c.features/"manifest.json", "index": c.index, "ssl": c.ssl, "audio": c.source}
        paths[change].write_bytes(paths[change].read_bytes()+b"changed")
    with pytest.raises(Conflict):
        c.training.submit(c.jobs, "preflight", "request")
    assert not c.jobs.list()


def test_failed_preflight_and_unknown_initialization_refused(context):
    c = context
    c.preflight["ready"] = False
    put(c.jobs, "preflight", "preflight", c.preflight)
    with pytest.raises(ValueError, match="未通过"):
        c.training.submit(c.jobs, "preflight", "request")
    c.preflight["ready"] = True
    c.preflight["config"]["initial_model"] = "not-registered"
    c.preflight["config_fingerprint"] = config_fingerprint(c.preflight["config"])
    put(c.jobs, "preflight", "preflight", c.preflight)
    with pytest.raises(ValueError, match="来源模型"):
        c.training.submit(c.jobs, "preflight", "request")


def test_initial_weight_hash_checked_before_submission_and_after_queue(context, monkeypatch):
    c = context
    original = c.weights["gpt"].read_bytes()
    c.weights["gpt"].write_bytes(b"damaged weight")
    with pytest.raises(ValueError, match="MODEL_DEPENDENCY_INVALID"):
        c.training.submit(c.jobs, "preflight", "request")
    c.weights["gpt"].write_bytes(original)
    job = c.training.submit(c.jobs, "preflight", "request")
    c.weights["sovits"].write_bytes(b"changed while queued")
    monkeypatch.setattr(c.training, "invoke", lambda *a, **k: pytest.fail("stale weights must not launch"))
    with pytest.raises(Conflict, match="排队期间"):
        c.training.process(c.jobs, job)


def test_queued_feature_change_does_not_launch_worker(context, monkeypatch):
    c = context
    job = c.training.submit(c.jobs, "preflight", "request")
    c.ssl.unlink()
    monkeypatch.setattr(c.training, "invoke", lambda *a, **k: pytest.fail("missing feature must not launch"))
    with pytest.raises(Conflict, match="特征"):
        c.training.process(c.jobs, job)


def test_worker_receives_frozen_initialization_and_snapshot(context, monkeypatch):
    c = context
    job = c.training.submit(c.jobs, "preflight", "request")
    observed = {}
    def boundary(jobs, accepted, command, *, result):
        observed.update(json.loads(Path(command[-2]).read_text(encoding="utf-8")))
        assert command[1:3] == ["-m", "dictionforge.training_worker"]
        return {"outcome": "COMPLETED", "unit_test_boundary": True}
    monkeypatch.setattr(c.training, "invoke", boundary)
    output = c.training.process(c.jobs, job)
    assert observed["config"] == c.preflight["config"]
    assert observed["initial_weights"] == {name: str(path) for name, path in c.weights.items()}
    assert observed["resume_checkpoint"] is None
    assert get(c.jobs, "training_result", job["id"]) == output
    snapshot = c.training.snapshot(c.jobs, job["id"])
    assert snapshot["run"]["initial_hashes"] == observed["initial_hashes"]
    assert snapshot["stage_state"] is None


def test_actual_source_tamper_is_rejected_even_when_lock_file_unchanged(context, monkeypatch):
    c = context
    job = c.training.submit(c.jobs, "preflight", "request")
    def source_changed(*args):
        raise ValueError("UPSTREAM_MODIFIED: training source changed")
    monkeypatch.setattr(c.training, "verify_source", source_changed)
    with pytest.raises(Conflict, match="实际上游源码"):
        c.training.process(c.jobs, job)
    with pytest.raises(Conflict, match="实际上游源码"):
        c.training.submit(c.jobs, "preflight", "new-request")
