import json
from pathlib import Path

import pytest

from dictionforge.feature_worker import component_keys, restore, cache
from dictionforge.training_config import normalize_config, config_fingerprint


def test_config_is_explicit_and_fingerprint_changes():
    base = normalize_config({"version_id": "version"})
    assert base["s1"]["batch_size"] == base["s2"]["batch_size"] == 1
    assert base["s1"]["stop_mode"] == base["s2"]["stop_mode"] == "epochs"
    assert base["stages"] == ["s2", "s1"]
    changed = normalize_config({"version_id": "version", "s1": {"batch_size": 2}})
    assert changed["s1"]["batch_size"] == 2
    assert config_fingerprint(base) != config_fingerprint(changed)


@pytest.mark.parametrize("payload", [
    {"s1": {"batch_size": 0}}, {"s2": {"rank": 3}}, {"s1": {"target": True}},
    {"s2": {"precision": "auto"}}, {"s1": {"learning_rate": float("nan")}},
    {"stages": ["s1", "s1"]}, {"stages": []}, {"s1": {"unconnected": 1}},
])
def test_invalid_training_parameters_fail_without_silent_adjustment(payload):
    with pytest.raises(ValueError):
        normalize_config({"version_id": "version", **payload})


def test_component_invalidation_dependencies():
    clip = {"text": "测试文本", "language": "zh", "sha256": "audio-a"}
    original = component_keys(clip, "source", "assets")
    text_edit = component_keys({**clip, "text": "修改文本"}, "source", "assets")
    assert text_edit["text"] != original["text"]
    assert text_edit["acoustic"] == original["acoustic"]
    assert text_edit["semantic"] == original["semantic"]
    audio_edit = component_keys({**clip, "sha256": "audio-b"}, "source", "assets")
    assert audio_edit["acoustic"] != original["acoustic"]
    assert audio_edit["semantic"] != original["semantic"]
    assert component_keys(clip, "source-v2", "assets") != original


def test_cache_repairs_missing_files_and_detects_corruption(tmp_path):
    pool, first, second = tmp_path/"cache", tmp_path/"first", tmp_path/"second"
    for relative, value in [("4-cnhubert/item.wav.pt", b"ssl-bytes"), ("5-wav32k/item.wav", b"wav-bytes")]:
        target = first / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(value)
    cache(pool, first, "acoustic", "fingerprint", "item.wav", "zh", "32k")
    assert restore(pool, second, "acoustic", "fingerprint", "item.wav", "zh")
    (second/"5-wav32k/item.wav").unlink()
    assert restore(pool, second, "acoustic", "fingerprint", "item.wav", "zh")
    assert (second/"5-wav32k/item.wav").read_bytes() == b"wav-bytes"
    (pool/"acoustic/fingerprint/ssl").write_bytes(b"corrupt")
    assert restore(pool, second, "acoustic", "fingerprint", "item.wav", "zh") is None
    assert (second/"4-cnhubert/item.wav.pt").read_bytes() == b"ssl-bytes"


def test_feature_readiness_updates_registry_without_rewriting_dataset(tmp_path, monkeypatch):
    from dictionforge import features
    from dictionforge.jobs import Jobs
    from dictionforge.records import get, put
    jobs = Jobs(tmp_path)
    manifest = tmp_path / "dataset-manifest.json"
    manifest.write_text('{"frozen":true}', encoding="utf-8")
    frozen = manifest.read_bytes()
    version = {"id": "version", "fingerprint": "frozen", "feature_status": "MISSING"}
    put(jobs, "version", "version", version)
    monkeypatch.setattr(features, "_run", lambda *args: {"path": str(tmp_path/"features/version"), "ready": True})
    features.process(jobs, {"payload": {"version_id": "version"}})
    assert get(jobs, "version", "version")["feature_status"] == "READY"
    assert get(jobs, "version", "version")["fingerprint"] == "frozen"
    assert manifest.read_bytes() == frozen
