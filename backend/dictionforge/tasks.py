def execute(jobs, job):
    from .settings import Settings, ROOT
    from .engine import invoke
    settings = Settings.from_environment()
    if job["kind"] == "system_probe":
        result = jobs.root / "runs" / job["id"] / "probe.json"
        return invoke(jobs, job, [settings.worker_python, ROOT / "scripts/probe.py", "--output", result], result=result)
    if job["kind"] == "asset_prepare":
        import sys
        result = jobs.root / "runs" / job["id"] / "assets.json"
        return invoke(jobs, job, [sys.executable, ROOT / "scripts/assets.py", "prepare", "--report", result],
                      env={"DICTIONFORGE_DATA": str(jobs.root)}, result=result)
    raise ValueError("未知任务类型：" + job["kind"])
