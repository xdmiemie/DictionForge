"""Freeze verified training inputs; launching never silently changes parameters."""
import json
import copy
from pathlib import Path
import time
from .audio import atomic_json, digest
from .engine import invoke
from .jobs import Conflict
from .records import get, put
from .settings import ROOT, Settings
from .training_config import config_fingerprint, feature_fingerprint
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
              ROOT / "configs/assets.lock.json": preflight.get("asset_lock")}
    if not preflight.get("features_content_sha256"):
        checks[area / "manifest.json"] = preflight.get("features_manifest_sha256")
    for path, expected in checks.items():
        if not expected or not path.is_file() or digest(path) != expected:
            raise Conflict(f"PREFLIGHT_STALE：{path.name} 已变化，请重新预检")
    try:
        verify_source(ROOT, upstream or Settings.from_environment().upstream)
    except Exception as exc:
        raise Conflict(f"PREFLIGHT_STALE：实际上游源码校验失败：{exc}") from exc
    manifest = json.loads((area / "manifest.json").read_text(encoding="utf-8"))
    if preflight.get("features_content_sha256") and feature_fingerprint(manifest) != preflight["features_content_sha256"]:
        raise Conflict("PREFLIGHT_STALE：特征内容指纹变化")
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
    with jobs.connect() as db:
        pending = [dict(row) for row in db.execute("""SELECT c.* FROM controls c
            LEFT JOIN control_receipts r ON c.request=r.request
            WHERE c.job_id=? AND r.request IS NULL ORDER BY c.created""", (run_id,))]
    with jobs.connect() as db:
        export_jobs = [jobs.decode(row) for row in db.execute("SELECT * FROM jobs WHERE kind='model_export' ORDER BY created DESC")]
    export_jobs = [item for item in export_jobs if item["payload"].get("run_id") == run_id]
    intents = []
    for intent_path in (Path(run["run_dir"]) / "export_intents").glob("*.json"):
        if intent_path.is_symlink() or not intent_path.resolve().is_relative_to(Path(run["run_dir"]).resolve()):
            continue
        try:
            intents.append(dict(json.loads(intent_path.read_text(encoding="utf-8")), intent_id=intent_path.stem))
        except (OSError, ValueError):
            intents.append({"intent_id": intent_path.stem, "state": "FAILED", "error": "导出意图记录无法读取"})
    return {**job, "run": run, "pending_controls": pending, "exports": export_jobs,
            "export_intents": intents,
            "stage_state": json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None}


def checkpoints(jobs, run_id):
    from .checkpoints import list_checkpoints
    entries = list_checkpoints(jobs, run_id)
    inherited = None
    if not entries and get(jobs, "run", run_id).get("parent_run"):
        try:
            source, inherited = _resume_point(jobs, get(jobs, "run", run_id))
        except ValueError as exc:
            inherited = {"valid": False, "reason": str(exc)}
    last = next((e for e in reversed(entries) if e["valid"]), None)
    return {"checkpoints": entries, "last_valid": last or (inherited if inherited and inherited.get("valid") else None),
            "inherited_start_point": inherited, "invalid": [e for e in entries if not e["valid"]]}


def _resume_point(jobs, run):
    from .checkpoints import list_checkpoints, select_checkpoint
    if list_checkpoints(jobs, run["id"]):
        return run["id"], select_checkpoint(jobs, run["id"])
    parent = run.get("parent_run")
    frozen = run.get("resume_checkpoint") or {}
    if not parent or run.get("resume_source_run") != parent or frozen.get("id") != run.get("parent_checkpoint"):
        return run["id"], select_checkpoint(jobs, run["id"])
    point = select_checkpoint(jobs, parent, run["parent_checkpoint"])
    if any(point.get(key) != frozen.get(key) for key in ("sha256", "bytes")):
        raise Conflict("派生起点与冻结恢复点不一致")
    point["selection"].update(source_run=parent, parent_seed_reason="child_has_no_own_checkpoint")
    return parent, point


def _validate_run(jobs, run):
    validate_preflight(jobs, run["preflight"], upstream=Path(run["upstream"]))
    for name, path in run["initial_weights"].items():
        if not Path(path).is_file() or digest(Path(path)) != run["initial_hashes"][name]:
            raise Conflict("MODEL_DEPENDENCY_INVALID：冻结的初始化权重已变化")


def control(jobs, run_id, action, request_id):
    if action == "export" and jobs.get(run_id)["state"] in {"PAUSED", "COMPLETED", "EARLY_COMPLETED"}:
        from .exports import enqueue_snapshot, retry_intent
        view = snapshot(jobs, run_id)
        failed = next((item for item in view["export_intents"] if item["state"] in {"PENDING", "FAILED"}), None)
        if failed:
            return retry_intent(jobs, run_id, failed["intent_id"])
        weights = (view.get("result") or {}).get("snapshots")
        if not weights or set(weights) != {"gpt", "sovits"}:
            raise Conflict("没有已保存的完整权重对")
        return enqueue_snapshot(jobs, view["run"], view["stage_state"], weights, "export", request_id)
    if action in {"pause", "export", "stop_export"}:
        return jobs.control(run_id, action, request_id)
    if action != "resume":
        raise ValueError("未支持的训练控制")
    with jobs.connect() as db:
        previous = db.execute("SELECT * FROM controls WHERE request=?", (request_id,)).fetchone()
        if previous:
            if previous["job_id"] != run_id or previous["action"] != action:
                raise Conflict("该控制请求 ID 已使用")
            return jobs.get(run_id)
    run = get(jobs, "run", run_id)
    _validate_run(jobs, run)
    source, point = _resume_point(jobs, run)
    with jobs.connect(write=True) as db:
        previous = db.execute("SELECT * FROM controls WHERE request=?", (request_id,)).fetchone()
        if previous:
            if previous["job_id"] != run_id or previous["action"] != action:
                raise Conflict("该控制请求 ID 已使用")
            return jobs.decode(db.execute("SELECT * FROM jobs WHERE id=?", (run_id,)).fetchone())
        current = jobs.decode(db.execute("SELECT * FROM jobs WHERE id=?", (run_id,)).fetchone())
        if current["kind"] != "train" or current["state"] not in {"PAUSED", "INTERRUPTED", "FAILED", "EARLY_COMPLETED"}:
            raise Conflict("仅暂停、中断、失败或提前结束的训练可续训")
        if db.execute("SELECT 1 FROM records WHERE kind='service' AND id='draining'").fetchone():
            raise Conflict("服务正在安全停止")
        # Retention also holds this write lock; revalidate before binding the point.
        source, point = _resume_point(jobs, run)
        now = time.time()
        # A stale pause from an interrupted process must not immediately pause a resumed run.
        db.execute("INSERT OR IGNORE INTO control_receipts SELECT request FROM controls WHERE job_id=?", (run_id,))
        db.execute("INSERT INTO controls VALUES(?,?,?,?)", (request_id, run_id, action, now))
        db.execute("INSERT INTO control_receipts VALUES(?)", (request_id,))
        run["resume_checkpoint"] = point
        run["resume_source_run"] = source
        run["resumed_at"] = now
        put(jobs, "run", run_id, run, db)
        db.execute("UPDATE jobs SET state='QUEUED',result=NULL,error=NULL,pid=NULL,process_start=NULL,instance=NULL,updated=? WHERE id=?", (now, run_id))
        jobs.emit(db, run_id, "state", {"state": "QUEUED", "action": "resume", "checkpoint_id": point["id"], "selection": point["selection"]})
        result = jobs.decode(db.execute("SELECT * FROM jobs WHERE id=?", (run_id,)).fetchone())
    atomic_json(Path(run["run_dir"]) / "run_meta.json", run)
    return result


def derive(jobs, run_id, checkpoint_id, request_id):
    payload = {"parent_run": run_id, "parent_checkpoint": checkpoint_id}
    with jobs.connect() as db:
        previous = db.execute("SELECT * FROM jobs WHERE request=?", (request_id,)).fetchone()
        if previous:
            previous = jobs.decode(previous)
            if previous["kind"] != "train" or previous["payload"] != payload:
                raise Conflict("该请求已绑定其他训练")
            return previous
    parent = get(jobs, "run", run_id)
    _validate_run(jobs, parent)
    from .checkpoints import select_checkpoint
    point = select_checkpoint(jobs, run_id, checkpoint_id)
    def accepted(db, job_id):
        point = select_checkpoint(jobs, run_id, checkpoint_id)
        child = copy.deepcopy(parent)
        child.update(id=job_id, run_id=job_id, created=time.time(), run_dir=str(jobs.root / "runs" / job_id),
                     parent_run=run_id, parent_checkpoint=checkpoint_id, resume_checkpoint=point, resume_source_run=run_id)
        child.pop("resumed_at", None)
        Path(child["run_dir"]).mkdir(parents=True, exist_ok=True)
        atomic_json(Path(child["run_dir"]) / "run_meta.json", child)
        put(jobs, "run", job_id, child, db)
    return jobs.submit("train", payload, request_id, on_accept=accepted)
