"""Subprocess boundary: explicit arguments, logs, cancellation and process-tree cleanup."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import psutil
from .errors import Cancelled, JobError
from .settings import ROOT

def invoke(jobs, job, command, *, cwd=None, env=None, result=None):
    area = jobs.root / "runs" / job["id"]
    area.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ, PYTHONPATH=str(ROOT / "backend"), PYTHONDONTWRITEBYTECODE="1",
                       HF_HOME=str(jobs.root / "assets/hf-cache"), MODELSCOPE_CACHE=str(jobs.root / "assets/modelscope"))
    environment.update(env or {})
    with (area / "process.log").open("ab") as log:
        launch = list(map(str, command))
        if os.name == "nt":
            launch = [sys.executable, "-m", "dictionforge.process_runner", *launch]
        process = subprocess.Popen(launch, cwd=cwd or ROOT, env=environment,
                                   stdout=log, stderr=log, stdin=subprocess.PIPE if os.name == "nt" else subprocess.DEVNULL,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        tree = None
        try:
            if os.name == "nt":
                from .process_tree import WindowsProcessTree
                tree = WindowsProcessTree(process)
            identity = {"pid": process.pid, "process_start": psutil.Process(process.pid).create_time(), "job_id": job["id"]}
            with jobs.connect(write=True) as db:
                db.execute("INSERT OR REPLACE INTO records VALUES('process',?,?)", (job["id"], json.dumps(identity)))
            if os.name == "nt":
                process.stdin.write(b"go\n")
                process.stdin.close()
            while process.poll() is None:
                if jobs.cancelled(job["id"]):
                    raise Cancelled()
                time.sleep(.2)
            if process.returncode:
                if result and Path(result).is_file():
                    failure = json.loads(Path(result).read_text(encoding="utf-8"))
                    if failure.get("ok") is False:
                        failure.update(outcome="FAILED", error="；".join(e.get("message", str(e)) for e in failure.get("errors", [])))
                    if failure.get("outcome") == "FAILED":
                        failure["child_exit_code"] = process.returncode
                        return failure
                raise JobError(f"子进程失败（退出码 {process.returncode}），详见 {area / 'process.log'}")
        finally:
            if tree:
                tree.close()
            if process.poll() is None:
                try:
                    parent = psutil.Process(process.pid)
                    children = parent.children(recursive=True)
                    for child in reversed(children):
                        try: child.kill()
                        except psutil.NoSuchProcess: pass
                    parent.kill()
                    psutil.wait_procs(children + [parent], timeout=10)
                except psutil.NoSuchProcess:
                    pass
                process.wait(timeout=15)
            with jobs.connect(write=True) as db:
                db.execute("DELETE FROM records WHERE kind='process' AND id=?", (job["id"],))
    if result:
        path = Path(result)
        if not path.is_file():
            raise JobError("工作进程未提交结果清单")
        return json.loads(path.read_text(encoding="utf-8"))
    return {"exit_code": 0, "log": str(area / "process.log")}
