"""Export CPU work must continue while the training worker owns the GPU lane."""
from dictionforge.jobs import Jobs, lane_for, lock_for
from dictionforge.supervisor import Supervisor, exclusive

def test_cpu_exports_do_not_consume_gpu_queue(tmp_path):
    jobs=Jobs(tmp_path)
    export=jobs.submit('model_export',{},'export')
    train=jobs.submit('train',{},'train')
    assert jobs.claim()['id']==train['id']
    assert jobs.claim('cpu')['id']==export['id']
    assert [j['id'] for j in jobs.running('gpu')]==[train['id']]
    assert [j['id'] for j in jobs.running('cpu')]==[export['id']]
    assert lock_for('train')!=lock_for('model_export')

def test_supervisor_launches_cpu_while_gpu_lock_held(tmp_path, monkeypatch):
    jobs=Jobs(tmp_path)
    cpu=jobs.submit('model_export',{},'cpu')
    gpu=jobs.submit('diagnostic',{},'gpu')
    supervisor=Supervisor(jobs)
    launched=[]
    monkeypatch.setattr(supervisor,'launch',lambda job:launched.append(job))
    with exclusive(jobs.state/'worker.lock'):
        supervisor.tick('gpu')
        supervisor.tick('cpu')
    assert [j['id'] for j in launched]==[cpu['id']]
    assert jobs.get(gpu['id'])['state']=='QUEUED'
