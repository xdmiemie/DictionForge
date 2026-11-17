import hashlib
import importlib.util
from pathlib import Path
import zipfile

import pytest

spec = importlib.util.spec_from_file_location("asset_tool", Path(__file__).parents[1] / "scripts/assets.py")
assets = importlib.util.module_from_spec(spec)
spec.loader.exec_module(assets)


@pytest.fixture
def asset_root(tmp_path, monkeypatch):
    monkeypatch.setattr(assets, "ROOT", tmp_path)
    monkeypatch.setattr(assets, "DATA", tmp_path / "data")
    return tmp_path


def item(content=b"trusted"):
    return dict(id="test", path="fixture/model.bin", size=len(content),
                sha256=hashlib.sha256(content).hexdigest(), upstream_path="weights/model.bin")


def test_import_hash_and_original_protected(asset_root):
    source = asset_root / "source.bin"
    source.write_bytes(b"trusted")
    target = assets.install(item(), source)
    assert target.read_bytes() == source.read_bytes()
    assert source.read_bytes() == b"trusted"
    assert assets.map_asset(item(), target) in {"hardlink", "copy"}
    assert (asset_root / "vendor/gpt-sovits/weights/model.bin").read_bytes() == b"trusted"


def test_invalid_import_keeps_previous_file(asset_root):
    source = asset_root / "source.bin"
    source.write_bytes(b"garbage")
    target = asset_root / "data/assets/fixture/model.bin"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"prior")
    with pytest.raises(ValueError, match="HASH_MISMATCH"):
        assets.install(item(), source)
    assert target.read_bytes() == b"prior"
    assert not list(target.parent.glob("*.part"))


def test_map_conflicting_file_is_not_overwritten(asset_root):
    source = asset_root / "source.bin"
    source.write_bytes(b"trusted")
    target = asset_root / "vendor/gpt-sovits/weights/model.bin"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"changed")
    with pytest.raises(ValueError, match="HASH_MISMATCH"):
        assets.map_asset(item(), source)
    assert target.read_bytes() == b"changed"


def test_escape_paths_rejected(asset_root):
    with pytest.raises(ValueError, match="escapes"):
        assets.contained(asset_root, "../outside")


def test_archive_uses_only_locked_members(asset_root):
    archive = asset_root / "model.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("safe/model.bin", b"trusted")
        z.writestr("../../escape.txt", b"evil")
    member = dict(item(), archive_name="safe/model.bin")
    assets.map_asset(dict(members=[member]), archive)
    assert (asset_root / "data/assets/fixture/model.bin").read_bytes() == b"trusted"
    assert not (asset_root.parent / "escape.txt").exists()


def test_missing_asset_is_explicit(asset_root):
    with pytest.raises(FileNotFoundError):
        assets.validate(asset_root / "missing.bin", item())
