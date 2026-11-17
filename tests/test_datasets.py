"""P03 I/O fixtures only: these tones do not stand in for speech/ASR acceptance."""
import hashlib
from pathlib import Path
import shutil
import subprocess
import uuid

import numpy as np
import pytest
import soundfile as sf

from dictionforge.jobs import Jobs, Conflict


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def waveform(path, frequency=220, *, silent=False):
    rate = 48000
    time = np.arange(rate * 4, dtype=np.float64) / rate
    values = np.zeros(len(time)) if silent else 0.2 * np.sin(2 * np.pi * frequency * time)
    values[int(rate * 1.6):int(rate * 2.4)] = 0
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(path, values, rate, subtype="PCM_16")
    return path


def encode(source, destination):
    subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                    "-i", str(source), str(destination)], check=True, capture_output=True)


@pytest.fixture
def recordings(tmp_path):
    folder = tmp_path / "原始 角色 录音"
    first = waveform(folder / "甲" / "重名.wav")
    second = waveform(folder / "乙" / "重名.wav", frequency=330)
    waveform(folder / "静音.wav", silent=True)
    shutil.copyfile(first, folder / "重复.wav")
    (folder / "损坏.wav").write_bytes(b"not a wav file")
    encode(second, folder / "编码.flac")
    encode(second, folder / "编码.mp3")
    (folder / "忽略.txt").write_text("This is not audio", encoding="utf-8")
    return folder


@pytest.fixture
def jobs(tmp_path):
    return Jobs(tmp_path / "managed")


def execute(jobs, kind, payload):
    from dictionforge import datasets
    job = jobs.submit(kind, payload, str(uuid.uuid4()))
    claimed = jobs.claim()
    assert claimed["id"] == job["id"]
    result = getattr(datasets, kind)(jobs, claimed)
    jobs.status(job["id"], "COMPLETED", result=result, instance=claimed["instance"])
    return result


def scan(jobs, folder, recursive=True):
    return execute(jobs, "scan", dict(source=str(folder), role="测试角色", language="zh", recursive=recursive))


def prepare(jobs, scan_id, excluded=None):
    return execute(jobs, "prepare", dict(scan_id=scan_id, excluded=excluded or [],
        min_seconds=0.5, max_seconds=2.0, silence_db=-40, silence_seconds=0.4, asr=False))


def reviewed_version(jobs, folder):
    from dictionforge import datasets
    result = prepare(jobs, scan(jobs, folder)["scan_id"])
    draft = datasets.get_draft(jobs, result["draft_id"])
    edited = datasets.update_draft(jobs, draft["id"], draft["revision"], [
        dict(id=clip["id"], text=f"片段 {i} | 原文。\n下一行。", language="zh", reviewed=True)
        for i, clip in enumerate(draft["clips"])
    ])
    return draft, edited, datasets.publish(jobs, edited["id"], edited["revision"])


def test_scan_mixed_formats_duplicates_and_invalid_input(jobs, recordings):
    original = {str(p): sha(p) for p in recordings.rglob("*") if p.is_file()}
    result = scan(jobs, recordings)
    assert len(result["files"]) == 7
    assert len({row["id"] for row in result["files"]}) == 7
    assert {Path(row["name"]).suffix for row in result["files"]} == {".wav", ".flac", ".mp3"}
    assert sum(Path(row["name"]).name == "重名.wav" for row in result["files"]) == 2
    assert {row["status"] for row in result["files"]} == {"ready", "duplicate", "silent", "damaged"}
    assert all(row["reason"] for row in result["files"] if row["status"] != "ready")
    assert all(row.get("audio_id") for row in result["files"] if row["status"] != "damaged")
    assert result["seconds"] == pytest.approx(sum(row["seconds"] for row in result["files"] if row["status"] == "ready"))
    assert original == {str(p): sha(p) for p in recordings.rglob("*") if p.is_file()}
    shallow = scan(jobs, recordings, recursive=False)
    assert all(len(Path(row["name"]).parts) == 1 for row in shallow["files"])


def test_prepare_exclusion_slicing_and_original_protection(jobs, recordings):
    from dictionforge import datasets
    original = {str(p): sha(p) for p in recordings.rglob("*") if p.is_file()}
    scanned = scan(jobs, recordings)
    excluded = next(row["id"] for row in scanned["files"] if row["status"] == "ready")
    result = prepare(jobs, scanned["scan_id"], [excluded])
    draft = datasets.get_draft(jobs, result["draft_id"])
    assert draft["clips"]
    assert excluded not in {clip["source_id"] for clip in draft["clips"]}
    assert any(r["id"] == excluded and "手动" in r["reason"] for r in draft["rejected"])
    scanned_by_id = {row["id"]: row for row in scanned["files"]}
    for clip in draft["clips"]:
        info = sf.info(clip["path"])
        assert 0 <= clip["start_sample"] < clip["end_sample"] <= scanned_by_id[clip["source_id"]]["samples"]
        assert info.frames == clip["end_sample"] - clip["start_sample"]
        assert info.samplerate == 48000
        assert 0.5 <= clip["seconds"] <= 2.0
        assert clip["seconds"] == pytest.approx(info.frames / info.samplerate)
        assert sha(clip["path"]) == clip["sha256"]
        assert not clip["reviewed"] and clip["label_source"] == "pending"
    assert result["seconds"] == pytest.approx(sum(clip["seconds"] for clip in draft["clips"]))
    assert original == {str(p): sha(p) for p in recordings.rglob("*") if p.is_file()}
    with pytest.raises(ValueError, match="标注"):
        datasets.publish(jobs, draft["id"], draft["revision"])


def test_revision_manual_review_manifest_and_group_split(jobs, recordings):
    from dictionforge import datasets
    draft, edited, version = reviewed_version(jobs, recordings)
    with pytest.raises(Conflict):
        datasets.update_draft(jobs, draft["id"], draft["revision"], [])
    assert version["fingerprint"]
    assert datasets.versions(jobs)[0]["id"] == version["id"]
    groups = {}
    names = set()
    for clip in version["clips"]:
        groups.setdefault(clip["group"], set()).add(clip["split"])
        assert Path(clip["path"]).name not in names
        names.add(Path(clip["path"]).name)
        assert sha(clip["path"]) == clip["sha256"]
        assert "|" not in clip["text"] and "\n" not in clip["text"]
        assert "|" in clip["raw_text"] and "\n" in clip["raw_text"]
    assert all(len(splits) == 1 for splits in groups.values())
    assert {clip["split"] for clip in version["clips"]} == {"train", "validation"}
    lines = Path(version["list_path"]).read_text(encoding="utf-8").splitlines()
    assert len(lines) == sum(clip["split"] == "train" for clip in version["clips"])
    for line in lines:
        fields = line.split("|")
        assert len(fields) == 4 and fields[2] == "ZH" and fields[3].strip()
        assert Path(fields[0]).is_file()
    manifest = Path(version["path"]) / "manifest.json"
    frozen = manifest.read_bytes()
    with pytest.raises(Conflict):
        datasets.update_draft(jobs, edited["id"], edited["revision"], [dict(id=edited["clips"][0]["id"], text="改动")])
    assert manifest.read_bytes() == frozen
    from dictionforge.records import get
    assert all(Path(get(jobs, "media", c["audio_id"])["path"]).parent == Path(version["path"]) for c in version["clips"])
    child_draft = datasets.edit_version(jobs, version["id"], "new-labels")
    child_draft = datasets.update_draft(jobs, child_draft["id"], 0, [{"id": child_draft["clips"][0]["id"], "text": "新标注。"}])
    child = datasets.publish(jobs, child_draft["id"], child_draft["revision"])
    assert child["parent_id"] == version["id"]
    assert manifest.read_bytes() == frozen
    assert {c["id"]: c["split"] for c in child["clips"]} == {c["id"]: c["split"] for c in version["clips"]}


def test_source_drift_is_rejected_before_publication(jobs, recordings):
    from dictionforge import datasets
    scanned = scan(jobs, recordings)
    target = Path(next(row["path"] for row in scanned["files"] if row["status"] == "ready"))
    target.write_bytes(target.read_bytes() + b"changed source fixture")
    changed = sha(target)
    with pytest.raises(Conflict, match="源录音"):
        prepare(jobs, scanned["scan_id"])
    assert sha(target) == changed
    assert datasets.versions(jobs) == []


def test_single_source_group_warns_and_corrupt_clip_cannot_publish(jobs, tmp_path):
    from dictionforge import datasets
    folder = tmp_path / "单来源"
    waveform(folder / "一句.wav")
    result = prepare(jobs, scan(jobs, folder)["scan_id"])
    draft = datasets.get_draft(jobs, result["draft_id"])
    draft = datasets.update_draft(jobs, draft["id"], draft["revision"], [
        dict(id=c["id"], text="已校对的夹具文本", reviewed=True) for c in draft["clips"]])
    path = Path(draft["clips"][0]["path"])
    original = path.read_bytes()
    path.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="哈希"):
        datasets.publish(jobs, draft["id"], draft["revision"])
    assert datasets.versions(jobs) == []
    path.write_bytes(original)
    version = datasets.publish(jobs, draft["id"], draft["revision"])
    assert version["warnings"]
    assert all(c["split"] == "train" for c in version["clips"])


def test_asr_retry_retains_manual_review(jobs, tmp_path, monkeypatch):
    from dictionforge import datasets, engine
    folder = tmp_path / "识别失败夹具"
    waveform(folder / "sample.wav")
    result = prepare(jobs, scan(jobs, folder)["scan_id"])
    draft = datasets.get_draft(jobs, result["draft_id"])
    assert len(draft["clips"]) >= 2
    reviewed_id = draft["clips"][0]["id"]
    draft = datasets.update_draft(jobs, draft["id"], draft["revision"], [
        dict(id=reviewed_id, text="保留人工校对内容", reviewed=True)])
    job = jobs.submit("asr_retry", {}, str(uuid.uuid4()))
    def recognition_failure(*args, **kwargs):
        # Deliberately mocked failure, not a speech-recognition quality assertion.
        return {"clips": [dict(id=c["id"], text="", error="fixture ASR unavailable") for c in draft["clips"]]}
    monkeypatch.setattr(engine, "invoke", recognition_failure)
    retried = datasets.transcribe(jobs, job, draft)
    manual = next(c for c in retried["clips"] if c["id"] == reviewed_id)
    assert manual["text"] == "保留人工校对内容" and manual["reviewed"]
    assert all(c["asr_error"] == "fixture ASR unavailable" for c in retried["clips"] if c["id"] != reviewed_id)
