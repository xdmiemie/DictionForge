import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest


def module(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parents[1] / f"scripts/{name}.py")
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def test_check_detects_missing_and_corrupt_mapping(tmp_path, monkeypatch):
    tool = module("assets")
    monkeypatch.setattr(tool, "ROOT", tmp_path)
    record = dict(upstream_path="model.bin", size=4, sha256=hashlib.sha256(b"good").hexdigest())
    with pytest.raises(FileNotFoundError):
        tool.validate_mapping(record)
    target = tmp_path / "vendor/gpt-sovits/model.bin"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"evil")
    with pytest.raises(ValueError, match="HASH_MISMATCH"):
        tool.validate_mapping(record)
    target.write_bytes(b"good")
    tool.validate_mapping(record)


def test_patch_replay_idempotent_and_preserves_unknown_edits(tmp_path):
    tool = module("apply_patches")
    vendor = tmp_path / "vendor/upstream"
    vendor.mkdir(parents=True)
    def git(*args):
        return subprocess.check_output(["git", "-C", str(vendor), *args], stderr=subprocess.STDOUT).decode().strip()
    git("init", "-q")
    source = vendor / "module.py"
    source.write_bytes(b"a = 1\nb = 2\nc = 3\n")
    before = tool.content_hash(source)
    git("add", "module.py")
    git("-c", "user.name=Test", "-c", "user.email=test@localhost", "commit", "-qm", "fixture")
    commit = git("rev-parse", "HEAD")
    patch = tmp_path / "fix.patch"
    patch.write_bytes(b"--- a/module.py\n+++ b/module.py\n@@ -1,3 +1,3 @@\n a = 1\n-b = 2\n+b = 4\n c = 3\n")
    after = hashlib.sha256(b"a = 1\nb = 4\nc = 3\n").hexdigest()
    lock = dict(directory="vendor/upstream", commit=commit, patches=[dict(
        path="fix.patch", sha256=hashlib.sha256(patch.read_bytes()).hexdigest(),
        files={"module.py": {"before": before, "after": after}})])
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs/upstream.lock.json").write_text(json.dumps(lock))
    with pytest.raises(ValueError, match="UPSTREAM_PATCHES_MISSING"):
        tool.verify_locked(tmp_path)
    assert tool.content_hash(source) == before
    assert tool.apply_locked(tmp_path)["applied"] == 1
    assert tool.apply_locked(tmp_path)["applied"] == 0
    assert tool.verify_locked(tmp_path)["applied"] == 0
    source.write_bytes(b"user modification\n")
    with pytest.raises(ValueError, match="UPSTREAM_MODIFIED"):
        tool.apply_locked(tmp_path)
    assert source.read_bytes() == b"user modification\n"
