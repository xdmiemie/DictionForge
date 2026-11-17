"""Pinned asset downloader/importer; no model runtime dependency required."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("DICTIONFORGE_DATA", str(ROOT / "data"))).resolve()
LOCK = ROOT / "configs/assets.lock.json"


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def contained(base: Path, relative: str) -> Path:
    target = (base / relative).resolve()
    if not target.is_relative_to(base.resolve()):
        raise ValueError(f"Asset path escapes managed directory: {relative}")
    return target


def validate(path: Path, item: dict) -> None:
    if not path.is_file():
        raise FileNotFoundError(str(path))
    if path.stat().st_size != item["size"]:
        raise ValueError(f"SIZE_MISMATCH: {path}")
    if digest(path) != item["sha256"]:
        raise ValueError(f"HASH_MISMATCH: {path}")


def validate_mapping(item: dict) -> None:
    if item.get("upstream_path"):
        validate(contained(ROOT / "vendor/gpt-sovits", item["upstream_path"]), item)
    for member in item.get("members", []):
        validate(contained(DATA / "assets", member["path"]), member)
        validate_mapping(member)


def install(item: dict, source: Path | None = None) -> Path:
    target = contained(DATA / "assets", item["path"])
    if target.exists():
        try:
            validate(target, item)
            return target
        except ValueError:
            pass  # Keep existing file until a complete, valid replacement exists.
    target.parent.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(target.parent).free < item["size"] + 2 * 1024**3:
        raise OSError(f"INSUFFICIENT_DISK: need asset size plus 2 GiB reserve: {target}")
    pending = target.with_name(target.name + "." + uuid.uuid4().hex + ".part")
    try:
        if source is not None:
            validate(source, item)
            with source.open("rb") as inp, pending.open("xb") as out:
                shutil.copyfileobj(inp, out, 4 * 1024 * 1024)
                out.flush()
                os.fsync(out.fileno())
        else:
            req = urllib.request.Request(item["url"], headers={"User-Agent": "DictionForge/0.1"})
            with urllib.request.urlopen(req, timeout=120) as inp, pending.open("xb") as out:
                shutil.copyfileobj(inp, out, 4 * 1024 * 1024)
                out.flush()
                os.fsync(out.fileno())
        validate(pending, item)
        os.replace(pending, target)
    finally:
        pending.unlink(missing_ok=True)
    return target


def map_asset(item: dict, source: Path) -> str:
    if item.get("members"):
        with zipfile.ZipFile(source) as archive:
            for member in item["members"]:
                target = contained(DATA / "assets", member["path"])
                valid = False
                if target.exists():
                    try:
                        validate(target, member)
                        valid = True
                    except ValueError:
                        pass
                if not valid:
                    info = archive.getinfo(member["archive_name"])
                    if info.file_size != member["size"]:
                        raise ValueError(f"ARCHIVE_SIZE_MISMATCH: {info.filename}")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    pending = target.with_name(target.name + "." + uuid.uuid4().hex + ".part")
                    try:
                        with archive.open(info) as inp, pending.open("xb") as out:
                            shutil.copyfileobj(inp, out, 4 * 1024 * 1024)
                            out.flush()
                            os.fsync(out.fileno())
                        validate(pending, member)
                        os.replace(pending, target)
                    finally:
                        pending.unlink(missing_ok=True)
                map_asset(member, target)
        return "verified_archive_members"
    relative = item.get("upstream_path")
    if not relative:
        return "not_required"
    target = contained(ROOT / "vendor/gpt-sovits", relative)
    if target.exists():
        validate(target, item)
        return "already_valid"
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, target)
        return "hardlink"
    except OSError:
        pending = target.with_name(target.name + "." + uuid.uuid4().hex + ".part")
        try:
            shutil.copyfile(source, pending)
            validate(pending, item)
            os.replace(pending, target)
        finally:
            pending.unlink(missing_ok=True)
        return "copy"


def ensure_vendor() -> None:
    lock = json.loads((ROOT / "configs/upstream.lock.json").read_text(encoding="utf-8-sig"))
    target = contained(ROOT, lock["directory"])
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "--depth", "1", "--branch", lock["tag"],
                        lock["repository"], str(target)], check=True)
    head = subprocess.check_output(["git", "-C", str(target), "rev-parse", "HEAD"], text=True).strip()
    if head != lock["commit"]:
        raise ValueError(f"UPSTREAM_REVISION_MISMATCH: expected {lock['commit']}; found {head}")
    # Only known patch-boundary content is accepted; unrelated edits are preserved.
    subprocess.run([sys.executable, str(ROOT / "scripts/apply_patches.py")], check=True)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="校验、下载或导入已锁定 GPT-SoVITS v4 资产")
    p.add_argument("action", choices=["check", "download", "prepare", "import", "map"])
    p.add_argument("--id", help="单个资产 ID；默认全部")
    p.add_argument("--source", type=Path, help="import 的本地可信文件")
    p.add_argument("--group", help="按 v4 / asr / text 组过滤")
    p.add_argument("--report", type=Path)
    args = p.parse_args(argv)
    if args.action == "prepare":
        ensure_vendor()
        args.action = "download"
    lock = json.loads(LOCK.read_text(encoding="utf-8-sig"))
    items = [a for a in lock["assets"] if (not args.id or a["id"] == args.id)
             and (not args.group or a["group"] == args.group)]
    if not items:
        p.error("没有匹配资产")
    if args.action == "import" and (len(items) != 1 or not args.source):
        p.error("import 需要 --id 和 --source")
    results = []
    for item in items:
        result = {"id": item["id"], "path": item["path"], "sha256": item["sha256"]}
        try:
            target = contained(DATA / "assets", item["path"])
            if args.action in {"download", "import"}:
                print(f"Preparing {item['id']} ({item['size']} bytes)", flush=True)
                target = install(item, args.source if args.action == "import" else None)
            validate(target, item)
            if args.action == "check":
                validate_mapping(item)
            if args.action in {"download", "import", "map"}:
                result["mapping"] = map_asset(item, target)
            result["status"] = "PASS"
        except Exception as exc:
            result.update(status="FAIL", error=f"{type(exc).__name__}: {exc}")
        results.append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps({"action": args.action, "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    return int(any(r["status"] != "PASS" for r in results))


if __name__ == "__main__":
    sys.exit(main())


