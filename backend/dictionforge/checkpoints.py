"""CPU-only checkpoint index integrity, explicit fallback, and retention protection.

This module does not unpickle weights. The model worker additionally validates full
training state/configuration before restoring anything.
"""
from __future__ import annotations
from collections import Counter
import json
from pathlib import Path
import re
import stat

from .audio import atomic_json, digest
from .records import get


class CheckpointInvalid(ValueError):
    def __init__(self, message, *, failed_entries=None, available_fallback=None):
        super().__init__(message)
        self.failed_entries = failed_entries or []
        self.available_fallback = available_fallback


def _no_reparse(path, base):
    """Reject symlink/junction aliases below an explicitly managed directory."""
    current = path
    while current != base:
        if current.exists() or current.is_symlink():
            info = current.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise CheckpointInvalid(f"CHECKPOINT_PATH_INVALID：重解析路径 {current}")
        if current.parent == current:
            raise CheckpointInvalid("CHECKPOINT_PATH_INVALID：目录越界")
        current = current.parent


def _area(jobs, run_id):
    if not isinstance(run_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", run_id):
        raise CheckpointInvalid("CHECKPOINT_PATH_INVALID：运行 ID 无效")
    run = get(jobs, "run", run_id)
    root = jobs.root.resolve()
    expected = root / "runs" / run_id
    actual = Path(run["run_dir"])
    if not actual.is_absolute() or actual.resolve() != expected.resolve():
        raise CheckpointInvalid("CHECKPOINT_PATH_INVALID：恢复目录不属于该运行")
    _no_reparse(expected, root)
    area = expected / "checkpoints"
    _no_reparse(area, root)
    return expected, area


def _index(jobs, run_id):
    run_dir, area = _area(jobs, run_id)
    path = area / "index.json"
    _no_reparse(path, run_dir)
    if not path.exists():
        return run_dir, area, {"checkpoints": [], "last": None}
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(result, dict) or not isinstance(result.get("checkpoints"), list):
            raise ValueError("checkpoints must be an array")
        if any(not isinstance(entry, dict) for entry in result["checkpoints"]):
            raise ValueError("checkpoint entry must be an object")
        return run_dir, area, result
    except (OSError, ValueError) as exc:
        raise CheckpointInvalid(f"CHECKPOINT_INDEX_INVALID：{exc}") from exc


def _file(entry, *, base, relative_base, label):
    value = entry.get("path")
    if not isinstance(value, str) or not value:
        raise CheckpointInvalid(f"{label}_PATH_INVALID：路径为空")
    candidate = Path(value)
    if not candidate.is_absolute():
        if ".." in candidate.parts:
            raise CheckpointInvalid(f"{label}_PATH_INVALID：相对路径越界")
        candidate = relative_base / candidate
    if not candidate.absolute().is_relative_to(base.absolute()):
        raise CheckpointInvalid(f"{label}_PATH_INVALID：路径不属于本运行")
    _no_reparse(candidate.absolute(), base.absolute())
    resolved = candidate.resolve()
    if not resolved.is_relative_to(base.resolve()) or resolved == base.resolve():
        raise CheckpointInvalid(f"{label}_PATH_INVALID：解析路径越界")
    if candidate.name.startswith(".") or candidate.suffix.lower() in {".part", ".tmp"}:
        raise CheckpointInvalid(f"{label}_INCOMPLETE：不能选择临时文件")
    if not candidate.is_file():
        raise CheckpointInvalid(f"{label}_MISSING：{candidate.name}")
    expected_size = entry.get("bytes")
    if type(expected_size) is not int or expected_size <= 0 or candidate.stat().st_size != expected_size:
        raise CheckpointInvalid(f"{label}_SIZE_MISMATCH：{candidate.name}")
    checksum = entry.get("sha256")
    if not isinstance(checksum, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", checksum):
        raise CheckpointInvalid(f"{label}_HASH_INVALID：SHA256 缺失或格式错误")
    if digest(candidate) != checksum.lower():
        raise CheckpointInvalid(f"{label}_HASH_MISMATCH：{candidate.name}")
    return resolved


def _references(jobs):
    result = set()
    with jobs.connect() as db:
        rows = db.execute("SELECT id,metadata FROM records WHERE kind='run'").fetchall()
    for row in rows:
        try:
            run = json.loads(row["metadata"])
            if run.get("parent_run") and run.get("parent_checkpoint"):
                result.add((run["parent_run"], run["parent_checkpoint"]))
            if run.get("resume_checkpoint"):
                checkpoint = run["resume_checkpoint"]
                source = run.get("resume_source_run") or run.get("parent_run") or row["id"]
                if not isinstance(checkpoint, dict) or not isinstance(checkpoint.get("id"), str) or not isinstance(source, str):
                    raise ValueError("resume reference malformed")
                result.add((source, checkpoint["id"]))
        except (ValueError, TypeError, AttributeError):
            # A corrupt reference registry must never make deletion appear safe.
            raise CheckpointInvalid(f"CHECKPOINT_REFERENCE_INVALID：运行 {row['id']} 的引用记录损坏") from None
    return result


def is_referenced(jobs, run_id, checkpoint_id):
    return (run_id, checkpoint_id) in _references(jobs)


def _inspect(jobs, run_id):
    run_dir, area, index = _index(jobs, run_id)
    references = _references(jobs)
    ids = Counter(str(entry.get("id")) for entry in index["checkpoints"])
    entries = []
    for ordinal, raw in enumerate(index["checkpoints"]):
        entry = dict(raw, valid=False, reason=None, validation="file_integrity_only", ordinal=ordinal)
        protected = []
        try:
            key = entry.get("id")
            if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", key):
                raise CheckpointInvalid("CHECKPOINT_ID_INVALID")
            if ids[key] != 1:
                raise CheckpointInvalid("CHECKPOINT_ID_DUPLICATE")
            # Checkpoint paths are absolute or relative to checkpoints/. Snapshot
            # paths are absolute or relative to the run directory.
            entry["path"] = str(_file(entry, base=area, relative_base=area, label="CHECKPOINT"))
            if entry.get("snapshot") is not None:
                if not isinstance(entry["snapshot"], dict):
                    raise CheckpointInvalid("SNAPSHOT_INVALID：快照记录格式错误")
                _file(entry["snapshot"], base=run_dir, relative_base=run_dir, label="SNAPSHOT")
            elif entry.get("kind") in {"periodic", "pause", "stop_export"}:
                raise CheckpointInvalid("SNAPSHOT_MISSING：完整控制恢复点缺少配套快照")
            else:
                entry["warning"] = "旧版恢复点无快照索引，恢复时由 worker 检验兼容路径"
            entry["valid"] = True
        except (OSError, ValueError) as exc:
            entry["reason"] = str(exc)
            protected.append("损坏或不完整点保留诊断")
        if raw.get("kind") != "periodic":
            protected.append("暂停、阶段交接、最终或非周期恢复点")
        if raw.get("id") == index.get("last"):
            protected.append("last 指向的恢复点")
        if isinstance(raw.get("id"), str) and (run_id, raw["id"]) in references:
            protected.append("派生或待恢复运行引用")
        entry.update(protected=bool(protected), protected_reasons=protected)
        entries.append(entry)
    return index, entries


def list_checkpoints(jobs, run_id):
    """Return committed index entries only; unindexed .part/orphan files are ignored."""
    return _inspect(jobs, run_id)[1]


def select_checkpoint(jobs, run_id, checkpoint_id=None):
    index, entries = _inspect(jobs, run_id)
    recent_valid = next((entry for entry in reversed(entries) if entry["valid"]), None)
    if checkpoint_id is not None:
        chosen = next((entry for entry in entries if entry.get("id") == checkpoint_id), None)
        if chosen is None or not chosen["valid"]:
            failed = [{"id": checkpoint_id, "reason": chosen["reason"] if chosen else "CHECKPOINT_NOT_INDEXED"}]
            fallback = recent_valid["id"] if recent_valid else None
            raise CheckpointInvalid(f"CHECKPOINT_INVALID：指定恢复点 {checkpoint_id} 不可用；最近有效点 {fallback or '无'}；{failed[0]['reason']}",
                                    failed_entries=failed, available_fallback=fallback)
        return dict(chosen, selection={"requested_id": checkpoint_id, "index_last": index.get("last"), "fallback": False, "failed_entries": []})
    preferred = next((entry for entry in entries if entry.get("id") == index.get("last")), None)
    chosen = preferred if preferred and preferred["valid"] else recent_valid
    failed = [{"id": entry.get("id"), "reason": entry["reason"]} for entry in entries if not entry["valid"]]
    if index.get("last") and preferred is None:
        failed.append({"id": index["last"], "reason": "INDEX_LAST_MISSING"})
    if chosen is None:
        raise CheckpointInvalid("CHECKPOINT_INVALID：没有已登记且校验通过的恢复点；不能回退官方底模", failed_entries=failed)
    return dict(chosen, selection={"requested_id": None, "index_last": index.get("last"),
                "fallback": chosen["id"] != index.get("last"), "failed_entries": failed})


def retention_plan(jobs, run_id, keep_recent=3):
    """Advisory only: deletion must recheck references atomically before moving files."""
    if type(keep_recent) is not int or keep_recent < 1:
        raise ValueError("至少保留一个周期恢复点")
    index, entries = _inspect(jobs, run_id)
    recent = {entry["id"] for entry in [e for e in entries if e.get("kind") == "periodic" and isinstance(e.get("id"), str)][-keep_recent:]}
    last_valid = next((entry["id"] for entry in reversed(entries) if entry["valid"]), None)
    protected, removable = [], []
    for entry in entries:
        reasons = list(entry["protected_reasons"])
        if isinstance(entry.get("id"), str) and entry["id"] in recent:
            reasons.append(f"最近 {keep_recent} 个周期点")
        if entry.get("id") == last_valid:
            reasons.append("最近有效恢复点")
        if reasons:
            protected.append(dict(entry, protected=True, protected_reasons=reasons))
        else:
            removable.append(entry)
    return {"run_id": run_id, "keep_recent": keep_recent, "protected": protected, "removable": removable, "advisory_only": True}


def apply_retention(jobs, run_id, keep_recent=3):
    """Publish retention under the same write lock used to register derived runs.

    A derived run must reselect its checkpoint inside Jobs.submit/on_accept. After
    index publication, deletion failure leaves an unindexed orphan, never a broken
    indexed checkpoint. No recursive deletion is used.
    """
    with jobs.connect(write=True):
        run_dir, area, before = _index(jobs, run_id)
        plan = retention_plan(jobs, run_id, keep_recent)
        selected = {entry["id"] for entry in plan["removable"]}
        result = {"run_id": run_id, "keep_recent": keep_recent, "removed": [], "deleted_files": [],
                  "orphan_files": [], "protected": [entry["id"] for entry in plan["protected"]]}
        if not selected:
            return result
        kept = [entry for entry in before["checkpoints"]
                if not isinstance(entry.get("id"), str) or entry["id"] not in selected]
        # Prevent even an unusual shared artifact from being deleted while a kept
        # entry still needs it. The normal writer uses immutable unique names.
        kept_paths = set()
        for entry in kept:
            for item, base in ((entry, area), (entry.get("snapshot"), run_dir)):
                if isinstance(item, dict) and isinstance(item.get("path"), str):
                    path = Path(item["path"])
                    kept_paths.add((path if path.is_absolute() else base / path).resolve())
        candidates = []
        for entry in plan["removable"]:
            for item, base, relative_base, label in ((entry, area, area, "CHECKPOINT"),
                    (entry.get("snapshot"), run_dir / "snapshots", run_dir, "SNAPSHOT")):
                if item is None:
                    continue
                path = _file(item, base=base, relative_base=relative_base, label=label)
                if path not in kept_paths:
                    candidates.append(path)
        # Atomic index failure propagates before any file is removed.
        atomic_json(area / "index.json", dict(before, checkpoints=kept))
        result["removed"] = [entry["id"] for entry in plan["removable"]]
        for path in dict.fromkeys(candidates):
            try:
                # Recheck aliases immediately before a single managed unlink.
                _no_reparse(path, run_dir)
                if not path.resolve().is_relative_to(run_dir.resolve()):
                    raise CheckpointInvalid("CHECKPOINT_PATH_INVALID：清理路径越界")
                path.unlink()
                result["deleted_files"].append(str(path))
            except (OSError, ValueError) as exc:
                result["orphan_files"].append({"path": str(path), "reason": str(exc)})
        return result
