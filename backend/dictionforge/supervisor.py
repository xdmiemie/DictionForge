from contextlib import contextmanager
import os
import subprocess
import sys
import json
from threading import Event, Thread
import psutil

@contextmanager
def exclusive(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as lock:
        lock.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield

def live_identity(job):
    if job["pid"] is None or job["process_start"] is None:
        return False
    try:
        p = psutil.Process(job["pid"])
        return p.create_time() == job["process_start"] and p.is_running()
    except psutil.NoSuchProcess:
        return False
    except psutil.AccessDenied:
        return True

def reap_owned_child(jobs, job_id):
    with jobs.connect() as db:
        row = db.execute("SELECT metadata FROM records WHERE kind='process' AND id=?", (job_id,)).fetchone()
    if not row:
        return
    child = json.loads(row["metadata"])
    if live_identity(child):
        try:
            parent = psutil.Process(child["pid"])
            processes = parent.children(recursive=True) + [parent]
            for p in processes:
                try: p.kill()
                except psutil.NoSuchProcess: pass
            _, alive = psutil.wait_procs(processes, timeout=10)
            if alive:
                raise OSError("工作进程树尚未退出，继续保护 GPU 租约")
        except psutil.NoSuchProcess:
            pass
    with jobs.connect(write=True) as db:
        db.execute("DELETE FROM records WHERE kind='process' AND id=?", (job_id,))

class Supervisor:
    def __init__(self, jobs):
        self.jobs = jobs
        self.stop = Event()
        self.thread = Thread(target=self.run, daemon=True)

    def run(self):
        while not self.stop.is_set():
            for lane in ("gpu", "cpu"):
                self.tick(lane)
            self.stop.wait(0.2)

    def tick(self, lane):
        try:
            with exclusive(self.jobs.state / ("cpu-worker.lock" if lane == "cpu" else "worker.lock")):
                running = self.jobs.running(lane)
                for job in running:
                    if not live_identity(job):
                        reap_owned_child(self.jobs, job["id"])
                        self.jobs.status(job["id"], "INTERRUPTED", error="工作进程已退出，检查最后有效产物")
                if not any(live_identity(j) for j in running):
                    job = self.jobs.claim(lane)
                    if job:
                        self.launch(job)
        except OSError:
            pass

    def launch(self, job):
        from .settings import ROOT
        env = dict(os.environ, PYTHONPATH=str(ROOT / "backend"))
        logdir = self.jobs.root / "runs" / job["id"]
        logdir.mkdir(parents=True, exist_ok=True)
        with (logdir / "worker.log").open("ab") as log:
            p = subprocess.Popen([sys.executable, "-B", "-m", "dictionforge.worker", str(self.jobs.root),
                                  job["id"], job["instance"]], env=env, stdin=subprocess.DEVNULL,
                                 stdout=log, stderr=log,
                                 creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        try:
            start = psutil.Process(p.pid).create_time()
            with self.jobs.connect(write=True) as db:
                db.execute("UPDATE jobs SET pid=?,process_start=? WHERE id=?", (p.pid, start, job["id"]))
        except psutil.NoSuchProcess:
            self.jobs.status(job["id"], "INTERRUPTED", error="工作进程启动后退出")
        Thread(target=p.wait, daemon=True).start()
