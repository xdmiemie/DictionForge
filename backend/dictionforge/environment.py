import json
from pathlib import Path
import shutil
import subprocess
from .settings import ROOT

def status(settings, jobs):
    from .supervisor import live_identity
    try:
        gpu = subprocess.run(["nvidia-smi", "--query-gpu=name,driver_version,memory.total,memory.free",
                              "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=10)
        gpu = gpu.stdout.strip() if gpu.returncode == 0 else gpu.stderr.strip()
    except (OSError, subprocess.TimeoutExpired):
        gpu = "无法读取 NVIDIA 设备"
    assets = []
    lock = ROOT / "configs/assets.lock.json"
    if lock.is_file():
        document = json.loads(lock.read_text(encoding="utf-8"))
        entries = document.get("assets", document.get("files", []))
        for asset in entries:
            path = settings.root / "assets" / asset.get("path", asset.get("local_path", ""))
            assets.append({"id": asset.get("id", asset.get("path")), "present": path.is_file(),
                           "status": "待哈希验证" if path.is_file() else "缺失", "path": str(path)})
    disk = shutil.disk_usage(settings.root)
    resident=[]
    with jobs.connect() as db:
        for job in jobs.running('gpu'):
            process=db.execute("SELECT metadata FROM records WHERE kind='process' AND id=?",(job['id'],)).fetchone()
            if not process or not live_identity(json.loads(process[0])):continue
            rows=db.execute("SELECT payload FROM events WHERE job_id=? AND kind='progress' ORDER BY id DESC LIMIT 20",(job['id'],))
            latest=next((item for item in (json.loads(row[0]) for row in rows) if 'gpu_resident' in item),None)
            if latest and latest['gpu_resident']:
                resident.append(dict(job_id=job['id'],model_id=latest.get('model_id'),stage=latest.get('stage')))
    return {"gpu": gpu, "active_jobs": len(jobs.running()), "gpu_jobs": jobs.running("gpu"),
            "cpu_jobs": jobs.running("cpu"), "gpu_resident_models":resident, "assets": assets,
            "worker_python": settings.worker_python, "worker_present": Path(settings.worker_python).is_file(),
            "disk_free_bytes": disk.free}
