"""Fault-injected scheduler contracts; no fake model training or publication."""
import json
import sqlite3
import time
from threading import Event

import pytest
from fastapi.testclient import TestClient

from dictionforge.api import create_app
from dictionforge.jobs import Jobs
from dictionforge.settings import Settings
from dictionforge.supervisor import Supervisor, exclusive


def until(predicate, timeout=5):
    deadline = time.monotonic()+timeout
    while not predicate():
        assert time.monotonic()<deadline, 'scheduler did not recover'
        time.sleep(.02)


@pytest.mark.parametrize('failed_lane', ['gpu','cpu'])
def test_transient_database_failure_preserves_queue_and_other_lane(tmp_path, monkeypatch, failed_lane):
    jobs=Jobs(tmp_path); supervisor=Supervisor(jobs)
    gpu=jobs.submit('diagnostic',{},'gpu')['id']; cpu=jobs.submit('model_export',{},'cpu')['id']
    real_claim=jobs.claim; failures=[]; launched=[]
    def claim(lane='gpu'):
        if lane==failed_lane and not failures:
            failures.append(lane);raise sqlite3.OperationalError('injected database is locked')
        return real_claim(lane)
    def launch(job):
        launched.append(job['id']);jobs.status(job['id'],'COMPLETED',result={'test_fixture':True})
    monkeypatch.setattr(jobs,'claim',claim);monkeypatch.setattr(supervisor,'launch',launch)
    supervisor.thread.start()
    try:
        until(lambda: all(jobs.get(key)['state']=='COMPLETED' for key in (gpu,cpu)))
        assert launched[0]==(cpu if failed_lane=='gpu' else gpu)
        assert supervisor.thread.is_alive()
        until(lambda: supervisor.status()['state']=='ready')
        assert supervisor.status()['last_error']['lane']==failed_lane
        logs=[json.loads(line) for line in (tmp_path/'logs/scheduler-errors.jsonl').read_text().splitlines()]
        assert len(logs)==1 and 'OperationalError' in logs[0]['traceback']
    finally:
        supervisor.stop.set();supervisor.thread.join(5)


def test_busy_lease_preserves_job_and_dispatches_once_after_release(tmp_path, monkeypatch):
    jobs=Jobs(tmp_path);supervisor=Supervisor(jobs);launched=[]
    job=jobs.submit('model_export',{},'export')
    monkeypatch.setattr(supervisor,'launch',lambda item:launched.append(item['id']))
    with exclusive(jobs.state/'cpu-worker.lock'):
        supervisor.tick('cpu')
        assert jobs.get(job['id'])['state']=='QUEUED' and not launched
    supervisor.tick('cpu')
    assert launched==[job['id']]


def test_spawn_oserror_is_not_silently_treated_as_busy(tmp_path, monkeypatch):
    jobs=Jobs(tmp_path);supervisor=Supervisor(jobs)
    first=jobs.submit('model_export',{},'first')
    def fail(_):raise OSError('injected process launch failure')
    monkeypatch.setattr(supervisor,'launch',fail)
    with pytest.raises(OSError,match='launch failure'):supervisor.tick('cpu')
    second=jobs.submit('model_export',{},'second');launched=[]
    monkeypatch.setattr(supervisor,'launch',lambda item:launched.append(item['id']))
    supervisor.tick('cpu')
    assert jobs.get(first['id'])['state']=='INTERRUPTED'
    assert launched==[second['id']]


def test_failed_diagnostic_log_does_not_stop_dispatch(tmp_path, monkeypatch):
    jobs=Jobs(tmp_path);(tmp_path/'logs').write_text('blocks log directory')
    supervisor=Supervisor(jobs);calls=[]
    def tick(lane):
        calls.append(lane)
        if len(calls)==1:raise RuntimeError('injected failure with unwritable log')
    monkeypatch.setattr(supervisor,'tick',tick)
    supervisor.thread.start()
    try:
        until(lambda:len(calls)>=4)
        assert supervisor.thread.is_alive() and supervisor.status()['last_error']
    finally:supervisor.stop.set();supervisor.thread.join(5)


def test_health_reports_retry_recovery_and_stale_heartbeat(tmp_path, monkeypatch):
    allow=Event();real_tick=Supervisor.tick
    def tick(self,lane):
        if lane=='cpu' and not allow.is_set():raise sqlite3.OperationalError('injected transient fault')
        return real_tick(self,lane)
    monkeypatch.setattr(Supervisor,'tick',tick)
    app=create_app(Settings(tmp_path))
    with TestClient(app) as client:
        until(lambda:client.get('/api/health').json()['scheduler']['state']=='retrying')
        assert client.get('/api/health').json()['scheduler']['alive']
        allow.set()
        until(lambda:client.get('/api/health').json()['scheduler']['state']=='ready')
        assert client.get('/api/health').json()['scheduler']['last_error']['lane']=='cpu'
        app.state.supervisor.stop.set();app.state.supervisor.thread.join(5)
        assert client.get('/api/health').json()['scheduler']['state']=='stopped'
    supervisor=Supervisor(Jobs(tmp_path/'stale'))
    monkeypatch.setattr(supervisor.thread,'is_alive',lambda:True)
    supervisor.last_cycle=time.time()-61
    assert supervisor.status()['state']=='stalled'
