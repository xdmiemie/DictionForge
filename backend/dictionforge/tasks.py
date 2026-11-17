def execute(jobs, job):
    from .settings import Settings, ROOT
    from .engine import invoke
    settings = Settings.from_environment()
    if job["kind"] == "model_probe":
        from .model_probe import process
        return process(jobs, job)
    if job["kind"] == "model_export":
        from .exports import process
        return process(jobs, job)
    if job["kind"] == "train":
        from .training import process
        return process(jobs, job)
    if job["kind"] in {"features", "preflight"}:
        from . import features
        return features.process(jobs, job) if job["kind"] == "features" else features.preflight(jobs, job)
    if job["kind"] in {"dataset_scan", "dataset_prepare", "dataset_asr"}:
        from . import datasets
        if job["kind"] == "dataset_scan":
            return datasets.scan(jobs, job)
        if job["kind"] == "dataset_prepare":
            return datasets.prepare(jobs, job)
        draft = datasets.get_draft(jobs, job["payload"]["draft_id"])
        updated = datasets.transcribe(jobs, job, draft, job["payload"]["clip_ids"])
        return {"draft_id": updated["id"], "revision": updated["revision"]}
    if job["kind"] == "system_probe":
        result = jobs.root / "runs" / job["id"] / "probe.json"
        return invoke(jobs, job, [settings.worker_python, ROOT / "scripts/probe.py", "--output", result], result=result)
    if job["kind"] == "asset_prepare":
        import sys
        result = jobs.root / "runs" / job["id"] / "assets.json"
        return invoke(jobs, job, [sys.executable, ROOT / "scripts/assets.py", "prepare", "--report", result],
                      env={"DICTIONFORGE_DATA": str(jobs.root)}, result=result)
    raise ValueError("未知任务类型：" + job["kind"])
