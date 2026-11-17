"""Apply only hash-locked patches; refuse unrelated or unrecognized source edits."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def content_hash(path: Path):
    if not path.is_file():
        return None
    # Upstream contains CRLF files. Git may normalize checkout line endings.
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def safe_path(base: Path, relative: str):
    p = (base / relative).resolve()
    if not p.is_relative_to(base.resolve()):
        raise ValueError(f"PATCH_PATH_ESCAPE: {relative}")
    return p


def apply_locked(root: Path = ROOT):
    lock = json.loads((root / "configs/upstream.lock.json").read_text(encoding="utf-8-sig"))
    vendor = safe_path(root, lock["directory"])
    head = subprocess.check_output(["git", "-C", str(vendor), "rev-parse", "HEAD"], text=True).strip()
    if head != lock["commit"]:
        raise ValueError(f"UPSTREAM_REVISION_MISMATCH: {head}")
    patches = lock.get("patches", [])
    paths = {}
    for patch in patches:
        file = safe_path(root, patch["path"])
        if hashlib.sha256(file.read_bytes()).hexdigest() != patch["sha256"]:
            raise ValueError(f"PATCH_HASH_MISMATCH: {file}")
        for relative, hashes in patch["files"].items():
            safe_path(vendor, relative)
            paths.setdefault(relative, hashes["before"])
    modified = subprocess.check_output(["git", "-C", str(vendor), "diff", "HEAD", "--name-only"], text=True).splitlines()
    unknown = set(modified) - set(paths)
    if unknown:
        raise ValueError(f"UPSTREAM_MODIFIED: preserve unknown edits: {sorted(unknown)}")
    states = [paths.copy()]
    for patch in patches:
        next_state = states[-1].copy()
        for relative, hashes in patch["files"].items():
            if next_state[relative] != hashes["before"]:
                raise ValueError(f"PATCH_CHAIN_INVALID: {relative}")
            next_state[relative] = hashes["after"]
        states.append(next_state)
    current = {relative: content_hash(safe_path(vendor, relative)) for relative in paths}
    matches = [i for i, state in enumerate(states) if state == current]
    if not matches:
        raise ValueError("UPSTREAM_MODIFIED: source does not match a locked patch boundary; preserve and inspect")
    start = max(matches)
    for patch in patches[start:]:
        command = ["git", "-C", str(vendor), "apply", "--ignore-whitespace"]
        file = str(safe_path(root, patch["path"]))
        subprocess.run(command + ["--check", file], check=True)
        subprocess.run(command + [file], check=True)
    actual = {relative: content_hash(safe_path(vendor, relative)) for relative in paths}
    if actual != states[-1]:
        raise ValueError("PATCH_RESULT_MISMATCH: source preserved for inspection")
    return {"commit": head, "patches": len(patches), "applied": len(patches) - start}


if __name__ == "__main__":
    print(json.dumps(apply_locked(), ensure_ascii=False))
