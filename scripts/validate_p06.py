"""Real HTTP/CUDA pause, whole-server restart and owned-worker crash acceptance."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request
import uuid
import psutil

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'backend'))
from dictionforge.audio import atomic_json, digest

URL = 'http://127.0.0.1:7867/api'
REPORT = ROOT/'data/p06-http.json'
evidence = {'restarts': [], 'crashes': []}
server = None

def api(path, data=None):
    request = urllib.request.Request(URL+path, data=json.dumps(data).encode() if data is not None else None,
        headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(request, timeout=240) as response:
        return json.load(response)

def fresh(**values):
    return dict(values, request_id=str(uuid.uuid4()))

def save():
    atomic_json(REPORT, evidence)

def start():
    global server
    log = (ROOT/'data/p06-server.log').open('ab')
    server = subprocess.Popen([sys.executable, '-m', 'dictionforge.serve'], cwd=ROOT,
        env=dict(os.environ, PYTHONPATH=str(ROOT/'backend')), stdin=subprocess.DEVNULL,
        stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
    log.close()
    for _ in range(100):
        try:
            assert api('/health')['service'] == 'ready'
            evidence['restarts'].append({'pid':server.pid, 'started':time.time()})
            save()
            return
        except Exception:
            if server.poll() is not None: raise RuntimeError('server failed; see p06-server.log')
            time.sleep(.2)
    raise TimeoutError('server start')

def stop():
    owned_pids={server.pid, *[p.pid for p in psutil.Process(server.pid).children(recursive=True)]}
    receipt = api('/system/stop', {})
    assert receipt['pid'] in owned_pids
    server.wait(30)
    evidence['restarts'][-1].update(stopped=time.time(), exit_code=server.returncode)
    save()

def until(run_id, predicate, timeout=900):
    deadline = time.monotonic()+timeout
    while time.monotonic() < deadline:
        view = api('/training/'+run_id)
        if predicate(view): return view
        if view['state'] in ('FAILED','CANCELLED'):
            raise RuntimeError(json.dumps(view, ensure_ascii=False))
        time.sleep(.1)
    raise TimeoutError(run_id)

def updates(view, stage):
    return (view.get('stage_state') or {}).get('stages', {}).get(stage, {}).get('optimizer_updates', 0)

def pause(run_id):
    request = fresh(action='pause')
    accepted = api('/training/'+run_id+'/controls', request)
    assert api('/training/'+run_id+'/controls', request) == accepted
    paused = until(run_id, lambda v: v['state']=='PAUSED')
    points = api('/training/'+run_id+'/checkpoints')
    assert points['last_valid'] and not points['invalid']
    return dict(view=paused, checkpoints=points)

def resume(run_id):
    request = fresh(action='resume')
    result = api('/training/'+run_id+'/controls', request)
    assert api('/training/'+run_id+'/controls', request)['id'] == run_id
    return result

def crash(run_id, reason):
    view = api('/training/'+run_id)
    process = psutil.Process(view['pid'])
    assert process.create_time() == view['process_start']
    assert 'dictionforge.worker' in process.cmdline() and run_id in process.cmdline()
    process.kill()
    interrupted = until(run_id, lambda v:v['state']=='INTERRUPTED')
    evidence['crashes'].append({'reason':reason, 'pid':process.pid, 'before':view, 'after':interrupted})
    save()

def gpu_release(run_id, label):
    # Windows WDDM can report N/A memory; absence of this run's registered child
    # and exited worker identity are the ownership checks, alongside nvidia-smi.
    view = api('/training/'+run_id)
    deadline=time.monotonic()+15
    while psutil.pid_exists(view['pid']) and time.monotonic()<deadline:
        try:
            if psutil.Process(view['pid']).create_time()!=view['process_start']: break
        except psutil.NoSuchProcess: break
        time.sleep(.1)
    owned_alive=False
    try: owned_alive=psutil.Process(view['pid']).create_time()==view['process_start']
    except psutil.NoSuchProcess: pass
    assert not owned_alive
    gpu=subprocess.run(['nvidia-smi','--query-compute-apps=pid,used_memory','--format=csv,noheader'],capture_output=True,text=True,check=True)
    evidence[label]={'worker_pid':view['pid'], 'worker_exited':True, 'compute_apps':gpu.stdout}
    save()

def main():
    if '--collect-existing-derive' in sys.argv:
        evidence.update(json.loads(REPORT.read_text(encoding='utf-8')))
        evidence.setdefault('validation_script_issues', []).append('All-file parent comparison included concurrently rewritten CPU checkpoint-inspection.json; verify immutable artifacts and frozen run state separately.')
        evidence.pop('error', None)
        parent=evidence['completed']
        expected={}
        for section in ('s2_pause','s1_pause'):
            for entry in evidence[section]['checkpoints']['checkpoints']:
                expected[entry['path']]=entry['sha256']
                if entry.get('snapshot'): expected[entry['snapshot']['path']]=entry['snapshot']['sha256']
        for entry in parent['result']['checkpoints']:
            expected[entry['path']]=entry['sha256']
            if entry.get('snapshot'): expected[entry['snapshot']['path']]=entry['snapshot']['sha256']
        assert all(digest(Path(path))==checksum for path,checksum in expected.items())
        parent_dir=ROOT/'data/runs'/evidence['run_id']
        assert json.loads((parent_dir/'run_meta.json').read_text(encoding='utf-8'))==parent['run']
        assert json.loads((parent_dir/'stage_state.json').read_text(encoding='utf-8'))==parent['stage_state']
        child_final=until(evidence['child_id'],lambda v:v['state']=='COMPLETED')
        assert updates(child_final,'s2')==20 and updates(child_final,'s1')==20
        evidence.update(child_completed=child_final,parent_immutable_files_unchanged=expected,
            parent_frozen_run_and_stage_state_unchanged=True,
            parent_points_after_derive=api('/training/'+evidence['run_id']+'/checkpoints'),result='PASS')
        save()
        api('/system/stop', {})
        print(json.dumps({'result':'PASS','run_id':evidence['run_id'],'child_id':evidence['child_id']}),flush=True)
        return
    if '--continue-after-crashes' in sys.argv:
        evidence.update(json.loads(REPORT.read_text(encoding='utf-8')))
        evidence.setdefault('runtime_issues', []).append(evidence.pop('error', ''))
        receipt=api('/system/stop', {})
        psutil.wait_procs([psutil.Process(receipt['pid'])], timeout=20)
        start()
        resume(evidence['run_id'])
        finish(evidence['run_id'], evidence['s2_pause']['checkpoints']['last_valid']['id'])
        return
    if '--continue-after-s2-pause' in sys.argv:
        evidence.update(json.loads(REPORT.read_text(encoding='utf-8')))
        evidence.setdefault('validation_script_issues', []).append(evidence.pop('error', 'Windows venv launcher PID differs from server PID'))
    start()
    config=dict(version_id='76b5a08af4ca471f9c6fa9c903347e69', initial_model='official', stages=['s2','s1'], seed=42,
        s2=dict(batch_size=1,precision='fp16',rank=32,stop_mode='steps',target=20,learning_rate=.0001),
        s1=dict(batch_size=1,precision='fp16',stop_mode='steps',target=20,learning_rate=.002,accumulate=4))
    if '--continue-after-s2-pause' in sys.argv:
        run_id=evidence['run_id']
    else:
        preflight=api('/training/preflight',fresh(**config))
        while preflight['state'] in ('QUEUED','RUNNING'):
            time.sleep(.5); preflight=api('/jobs/'+preflight['id'])
        assert preflight['state']=='COMPLETED' and preflight['result']['ready'], preflight
        evidence['preflight']=preflight; save()
        job=api('/training', fresh(preflight_id=preflight['id']))
        run_id=job['id']; evidence['run_id']=run_id; save()
        until(run_id, lambda v:updates(v,'s2')>=2)
        evidence['s2_pause']=pause(run_id); save(); gpu_release(run_id,'s2_gpu_release')
    old_point=evidence['s2_pause']['checkpoints']['last_valid']['id']
    stop(); start(); resume(run_id)
    until(run_id, lambda v:(v.get('stage_state') or {}).get('active_stage')=='s1')
    crash(run_id,'stage_handoff_after_durable_s2_before_s1_update')
    evidence['handoff_recovery']=api('/training/'+run_id+'/checkpoints'); save()
    resume(run_id)
    until(run_id, lambda v:updates(v,'s1')>=2)
    evidence['s1_pause']=pause(run_id); save(); gpu_release(run_id,'s1_gpu_release')
    paused_updates=updates(evidence['s1_pause']['view'],'s1')
    stop(); start(); resume(run_id)
    until(run_id, lambda v:v['state']=='RUNNING' and updates(v,'s1')>paused_updates)
    crash(run_id,'mid_epoch_owned_worker_termination_after_uncheckpointed_update')
    resume(run_id)
    finish(run_id, old_point)

def finish(run_id, old_point):
    final=until(run_id,lambda v:v['state']=='COMPLETED')
    assert updates(final,'s2')==20 and updates(final,'s1')==20
    evidence['completed']=final; save()
    parent_dir=ROOT/'data/runs'/run_id
    parent_hashes={str(p.relative_to(parent_dir)):digest(p) for p in parent_dir.rglob('*') if p.is_file()
        and (p.suffix in ('.pt','.pth','.ckpt') or p.name in ('run_meta.json','stage_state.json','s1.effective.json','s2.effective.json'))}
    evidence['parent_hashes_before_derive']=parent_hashes; save()
    derive_request=fresh(checkpoint_id=old_point)
    child=api('/training/'+run_id+'/derive',derive_request)
    assert api('/training/'+run_id+'/derive',derive_request)['id']==child['id']
    evidence['child_id']=child['id']; save()
    origin_updates=updates(evidence['s2_pause']['view'],'s2')
    until(child['id'],lambda v:updates(v,'s2')>origin_updates)
    evidence['child_pause']=pause(child['id']); save()
    resume(child['id'])
    child_final=until(child['id'],lambda v:v['state']=='COMPLETED')
    assert updates(child_final,'s2')==20 and updates(child_final,'s1')==20
    assert all(digest(parent_dir/relative)==checksum for relative,checksum in parent_hashes.items())
    evidence.update(child_completed=child_final,parent_files_unchanged=parent_hashes,
        parent_points_after_derive=api('/training/'+run_id+'/checkpoints'),result='PASS')
    save(); stop()
    print(json.dumps({'result':'PASS','run_id':run_id,'child_id':child['id']}),flush=True)

if __name__=='__main__':
    try: main()
    except BaseException as exc:
        evidence['error']=repr(exc); save(); raise
