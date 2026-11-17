"""API-worker feature and preflight orchestration. GPU work stays isolated."""
from pathlib import Path
from .audio import atomic_json
from .engine import invoke
from .records import get, put
from .settings import Settings
from .training_config import normalize_config, config_fingerprint


def _run(jobs, job, action, version, config=None):
    settings = Settings.from_environment()
    area = jobs.root / "runs" / job["id"]
    area.mkdir(parents=True, exist_ok=True)
    request, result = area / "feature-request.json", area / "feature-result.json"
    atomic_json(request, dict(action=action, version=version, config=config,
                data_root=str(jobs.root), upstream=str(settings.upstream), job_id=job["id"]))
    return invoke(jobs, job, [settings.worker_python, "-m", "dictionforge.feature_worker", request, result], result=result)


def process(jobs, job):
    version = get(jobs, "version", job["payload"]["version_id"])
    result = _run(jobs, job, "features", version)
    if not result["ready"]:
        messages = [str(item.get("message", item)) for item in result.get("errors", [])]
        result.update(outcome="FAILED", error="；".join(messages[:5]) or "特征准备未通过逐项验证，请查看阶段日志")
    put(jobs, "features", version["id"], result)
    # Readiness is mutable metadata; the frozen published manifest stays unchanged.
    with jobs.connect(write=True) as db:
        current = get(jobs, "version", version["id"], db)
        current["feature_status"] = "READY" if result["ready"] else "FAILED"
        current["feature_path"] = result["path"]
        put(jobs, "version", version["id"], current, db)
    return result


def preflight(jobs, job):
    config = normalize_config(job["payload"])
    version = get(jobs, "version", config["version_id"])
    result = _run(jobs, job, "preflight", version, config)
    result.update(config=config, config_fingerprint=config_fingerprint(config),
                  version_fingerprint=version["fingerprint"], id=job["id"])
    put(jobs, "preflight", job["id"], result)
    return result
