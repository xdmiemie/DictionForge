"""Freeze verified training inputs; launching never silently changes parameters."""
import json
from pathlib import Path
import time
from .audio import atomic_json, digest
from .engine import invoke
from .jobs import Conflict
from .records import get, put
from .settings import ROOT, Settings
from .training_config import config_fingerprint
from .source_lock import verify_source

def validate_preflight(jobs, preflight, *, upstream=None):
    if not preflight.get("ready"):
        raise ValueError("训练预检未通过")
    config = preflight["config"]
    if config_fingerprint(config) != preflight["config_fingerprint"]:
        raise Conflict("PREFLIGHT_STALE：配置指纹变化")
    version = get(jobs, "version", config["version_id"])
    if version["fingerprint"] != preflight["version_fingerprint"]:
        raise Conflict("PREFLIGHT_STALE：数据版本变化")
    area = jobs.root / "features" / version["id"]
    checks = {ROOT / "configs/upstream.lock.json": preflight.get("upstream_lock"),
              ROOT / "configs/assets.lock.json": preflight.get("asset_lock"),
              area / "manifest.json": preflight.get("features_manifest_sha256")}
    for path, expected in checks.items():
        if not expected or not path.is_file() or digest(path) != expected:
            raise Conflict(f"PREFLIGHT_STALE：{path.name} 已变化，请重新预检")
    try:
        verify_source(ROOT, upstream or Settings.from_environment().upstream)
    except Exception as exc:
        raise Conflict(f"PREFLIGHT_STALE：实际上游源码校验失败：{exc}") from exc
    manifest = json.loads((area / "manifest.json").read_text(encoding="utf-8"))
    expected_files = dict(manifest["indexes"])
    for entry in manifest["entries"].values():
        expected_files.update(entry["files"])
    for relative, expected in expected_files.items():
        path = (area / relative).resolve()
        if not path.is_relative_to(area.resolve()) or not path.is_file() or digest(path) != expected:
            raise Conflict(f"PREFLIGHT_STALE：特征缺失或损坏 {relative}")
    for clip in version["clips"]:
        if not Path(clip["path"]).is_file() or digest(Path(clip["path"])) != clip["sha256"]:
            raise Conflict(f"DATA_CHANGED：{clip['id']}")
    return version

def initial_weights(settings, config):
    if config["initial_model"] != "official":
        raise ValueError("来源模型初始化尚未注册")
    lock = json.loads((ROOT / "configs/assets.lock.json").read_text(encoding="utf-8"))
    relative = {"gpt": "GPT_SoVITS/pretrained_models/s1v3.ckpt",
                "sovits": "GPT_SoVITS/pretrained_models/gsv-v4-pretrained/s2Gv4.pth"}
    paths, hashes = {}, {}
    for name, path in relative.items():
        asset = next(a for a in lock["assets"] if a.get("upstream_path") == path)
        resolved = settings.upstream / path
        if not resolved.is_file() or digest(resolved) != asset["sha256"]:
            raise ValueError(f"MODEL_DEPENDENCY_INVALID：{resolved}")
        paths[name], hashes[name] = str(resolved), asset["sha256"]
    return paths, hashes

def submit(jobs, preflight_id, request_id):
    with jobs.connect() as db:
        previous = db.execute("SELECT * FROM jobs WHERE request=?", (request_id,)).fetchone()
        if previous:
            previous = jobs.decode(previous)
            if previous["kind"] != "train" or previous["payload"] != {"preflight_id": preflight_id}:
                raise Conflict("该请求已绑定其他训练")
            return previous
    preflight = get(jobs, "preflight", preflight_id)
    version = validate_preflight(jobs, preflight)
    settings = Settings.from_environment()
    weights, hashes = initial_weights(settings, preflight["config"])
    def accepted(db, job_id):
        run = {"id": job_id, "created": time.time(), "config": preflight["config"], "preflight_id": preflight_id,
               "preflight": preflight, "version": version, "role": version["role"], "dataset_id": version["dataset_id"],
               "initial_weights": weights, "initial_hashes": hashes, "upstream": str(settings.upstream),
               "run_dir": str(jobs.root / "runs" / job_id), "data_root": str(jobs.root), "run_id": job_id,
               "resume_checkpoint": None}
        area = Path(run["run_dir"])
        area.mkdir(parents=True, exist_ok=True)
        atomic_json(area / "run_meta.json", run)
        put(jobs, "run", job_id, run, db)
    return jobs.submit("train", {"preflight_id": preflight_id}, request_id, on_accept=accepted)

def process(jobs, job):
    run = get(jobs, "run", job["id"])
    validate_preflight(jobs, run["preflight"], upstream=Path(run["upstream"]))
    for name, path in run["initial_weights"].items():
        if digest(Path(path)) != run["initial_hashes"][name]:
            raise Conflict("初始化权重在排队期间变化")
    area = Path(run["run_dir"])
    request, result = area / "training-request.json", area / "training-result.json"
    atomic_json(request, run)
    settings = Settings.from_environment()
    output = invoke(jobs, job, [settings.worker_python, "-m", "dictionforge.training_worker", request, result], result=result)
    put(jobs, "training_result", job["id"], output)
    return output

def snapshot(jobs, run_id):
    job = jobs.get(run_id)
    run = get(jobs, "run", run_id)
    path = Path(run["run_dir"]) / "stage_state.json"
    return {**job, "run": run, "stage_state": json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None}
