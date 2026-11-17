import sys
import time
import traceback
from pathlib import Path
from .jobs import Jobs, lock_for
from .supervisor import exclusive
from .errors import Cancelled, JobError

def check_cancel(jobs, job_id):
    if jobs.cancelled(job_id):
        raise Cancelled()

def run_task(jobs, job):
    if job["kind"] == "diagnostic":
        for i in range(4):
            check_cancel(jobs, job["id"])
            time.sleep(.25)
            jobs.progress(job["id"], {"stage": "diagnostic", "completed": i+1, "total": 4, "unit": "check", "message": "任务系统诊断"})
        return {"diagnostic": True}
    from .tasks import execute
    return execute(jobs, job)

def main():
    jobs = Jobs(Path(sys.argv[1]))
    job_id, instance = sys.argv[2:4]
    lock_name = lock_for(jobs.get(job_id)["kind"])
    for _ in range(200):
        try:
            with exclusive(jobs.state / lock_name):
                job = jobs.get(job_id)
                if job["state"] != "RUNNING" or job["instance"] != instance:
                    return
                try:
                    result = run_task(jobs, job)
                    jobs.status(job_id, result.get("outcome", "COMPLETED"), result=result,
                                error=result.get("error"), instance=instance)
                except Cancelled:
                    jobs.status(job_id, "CANCELLED", instance=instance)
                except Exception as exc:
                    traceback.print_exc()
                    jobs.status(job_id, "FAILED", error=str(exc), instance=instance)
                return
        except OSError:
            time.sleep(.05)
    jobs.status(job_id, "INTERRUPTED", error="未取得 GPU/worker 租约", instance=instance)

if __name__ == "__main__":
    main()
