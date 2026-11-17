import json
import sys
import time
import psutil
from dictionforge.jobs import Jobs
from dictionforge.engine import invoke
from dictionforge.supervisor import reap_owned_child

def test_subprocess_result_and_identity_cleanup(tmp_path):
    jobs = Jobs(tmp_path)
    job = jobs.submit("probe", {}, "probe")
    result = tmp_path / "result.json"
    answer = invoke(jobs, job, [sys.executable, "-c", "import json,sys;json.dump({'real':True},open(sys.argv[1],'w'))", result], result=result)
    assert answer == {"real": True}
    with jobs.connect() as db:
        assert db.execute("SELECT count(*) FROM records WHERE kind='process'").fetchone()[0] == 0

def test_orphan_child_reaped_by_exact_identity(tmp_path):
    import subprocess
    jobs = Jobs(tmp_path)
    child = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(60)"])
    try:
        identity = {"pid": child.pid, "process_start": psutil.Process(child.pid).create_time()}
        with jobs.connect(write=True) as db:
            db.execute("INSERT INTO records VALUES('process','job',?)", (json.dumps(identity),))
        reap_owned_child(jobs, "job")
        child.wait(timeout=5)
        assert child.returncode is not None
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()

def test_launcher_exit_does_not_leave_grandchild(tmp_path):
    import os
    import pytest
    if os.name != "nt":
        pytest.skip("Windows kernel job guarantee")
    jobs = Jobs(tmp_path)
    job = jobs.submit("probe", {}, "tree")
    pid_file = tmp_path / "descendant.json"
    script = "import subprocess,sys,psutil,json; p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']); json.dump({'pid':p.pid,'process_start':psutil.Process(p.pid).create_time()},open(sys.argv[1],'w'))"
    invoke(jobs, job, [sys.executable, "-c", script, pid_file])
    from dictionforge.supervisor import live_identity
    assert not live_identity(json.loads(pid_file.read_text()))
