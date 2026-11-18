from contextlib import contextmanager
import errno
import os
import subprocess
import sys
import json
import time
import traceback
from threading import Event, Lock, Thread
import psutil

class LeaseBusy(OSError):
    """Expected resource contention, distinct from launch and storage failures."""

@contextmanager
def exclusive(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as lock:
        lock.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                raise LeaseBusy(str(exc)) from exc
            raise
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
        self.last_media_reconcile = 0.
        self.state_lock = Lock()
        self.last_cycle = None
        self.errors = {}
        self.last_error = None
        self.logged_errors = {}

    def status(self):
        with self.state_lock:
            age = time.time() - self.last_cycle if self.last_cycle is not None else None
            alive = self.thread.is_alive()
            state = 'stopped' if not alive else 'stalled' if age is not None and age > 60 else 'retrying' if self.errors else 'ready'
            return dict(alive=alive, state=state, heartbeat_age_seconds=age,
                        current_errors=dict(self.errors), last_error=self.last_error)

    def record_error(self, lane, exc):
        now = time.time()
        error = dict(lane=lane, at=now, message=f'{type(exc).__name__}: {exc}')
        with self.state_lock:
            self.errors[lane] = error
            self.last_error = error
            previous = self.logged_errors.get(lane)
            write_log = previous is None or previous['message'] != error['message'] or now-previous['at'] >= 30
            if write_log:
                self.logged_errors[lane] = error
        if write_log:
            # Service stdout may be unavailable after Windows detachment. Keep
            # a direct workspace log, independent of SQLite and console handles.
            try:
                path = self.jobs.root/'logs'/'scheduler-errors.jsonl'
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open('a', encoding='utf-8', errors='backslashreplace') as stream:
                    stream.write(json.dumps(dict(error, traceback=traceback.format_exc()), ensure_ascii=False)+'\n')
            except OSError:
                pass  # A failed diagnostic write must not kill the scheduler.

    def recovered(self, lane):
        with self.state_lock:
            self.errors.pop(lane, None)

    def run(self):
        while not self.stop.is_set():
            with self.state_lock:
                self.last_cycle = time.time()
            for lane in ("gpu", "cpu"):
                try:
                    self.tick(lane)
                    self.recovered(lane)
                except Exception as exc:
                    self.record_error(lane, exc)
            if time.monotonic() - self.last_media_reconcile >= 2:
                self.last_media_reconcile = time.monotonic()
                try:
                    from .media_lifecycle import reconcile
                    from .saved_speech import reconcile_deletions
                    reconcile(self.jobs)
                    reconcile_deletions(self.jobs)
                    self.recovered('media')
                except Exception as exc:
                    self.record_error('media', exc)
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
        except LeaseBusy:
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
