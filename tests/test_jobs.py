import time
import pytest
import psutil

from fastapi.testclient import TestClient

from dictionforge.api import create_app
from dictionforge.settings import Settings
from dictionforge.jobs import Jobs, Conflict
from dictionforge.supervisor import live_identity


def test_idempotency_and_control_conflict(tmp_path):
    jobs = Jobs(tmp_path)
    a = jobs.submit("diagnostic", {}, "same")
    assert jobs.submit("diagnostic", {}, "same")["id"] == a["id"]
    with pytest.raises(Conflict):
        jobs.submit("diagnostic", {"changed": True}, "same")
    jobs.control(a["id"], "cancel", "c")
    assert jobs.get(a["id"])["state"] == "CANCELLED"
    assert jobs.claim() is None


def test_pid_reuse_is_not_live():
    current = psutil.Process()
    assert live_identity({"pid": current.pid, "process_start": current.create_time()})
    assert not live_identity({"pid": current.pid, "process_start": current.create_time()-1})


def test_lost_worker_reconciles(tmp_path):
    from dictionforge.supervisor import Supervisor
    jobs = Jobs(tmp_path)
    job = jobs.submit("diagnostic", {}, "lost")
    jobs.claim()
    supervisor = Supervisor(jobs)
    supervisor.thread.start()
    try:
        deadline = time.monotonic() + 5
        while jobs.get(job["id"])["state"] == "RUNNING" and time.monotonic() < deadline:
            time.sleep(.05)
        assert jobs.get(job["id"])["state"] == "INTERRUPTED"
    finally:
        supervisor.stop.set()
        supervisor.thread.join()


def test_worker_resumes_event_observation(tmp_path):
    import uuid

    app = create_app(Settings(tmp_path))
    with TestClient(app) as client:
        body = {"request_id": str(uuid.uuid4())}
        first = client.post("/api/jobs/diagnostic", json=body).json()
        second = client.post("/api/jobs/diagnostic", json=body).json()
        assert first["id"] == second["id"]
        path = "/api/jobs/" + first["id"]
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if client.get(path).json()["state"] == "COMPLETED":
                break
            time.sleep(0.1)
        assert client.get(path).json()["state"] == "COMPLETED"
        stream = client.get(path + "/events")
        ids = [int(line[4:]) for line in stream.text.splitlines() if line.startswith("id: ")]
        assert len(ids) == 7
        resumed = client.get(path + "/events", headers={"Last-Event-ID": str(ids[-2])})
        assert [line for line in resumed.text.splitlines() if line.startswith("id: ")] == [f"id: {ids[-1]}"]
