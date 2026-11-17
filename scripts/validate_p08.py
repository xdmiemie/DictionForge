"""Real frozen-reference comparison, training exclusion and external GPU pressure."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request
import urllib.error
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from dictionforge.audio import atomic_json,digest
from dictionforge.jobs import Jobs
URL='http://127.0.0.1:7867/api'
OUTPUT=ROOT/'data/p08-http.json'
report={'observations':[]}
def fresh(**values):return dict(values,request_id=str(uuid.uuid4()))
def call(path,data=None):
    req=urllib.request.Request(URL+path,data=json.dumps(data).encode() if data is not None else None,headers={'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(req,timeout=240) as response:return response.status,json.load(response)
    except urllib.error.HTTPError as exc:return exc.code,json.loads(exc.read())
def api(path,data=None):
    code,result=call(path,data)
    if not 200<=code<300:raise RuntimeError(f'{path}: {code}: {result}')
    return result
def save():atomic_json(OUTPUT,report)
def compare_wait(key):
    deadline=time.monotonic()+1200
    while time.monotonic()<deadline:
        view=api('/comparisons/'+key)
        status=api('/system/status')
        report['observations'].append(dict(time=time.time(),comparison=key,state=view['state'],
            gpu_jobs=[dict(id=j['id'],kind=j['kind'],state=j['state']) for j in status['gpu_jobs']],
            resident=status['gpu_resident_models'],gpu=status['gpu']))
        assert len(status['gpu_jobs'])<=1
        if view['state']=='COMPLETED':return view
        if view['state']=='FAILED':raise RuntimeError(json.dumps(view,ensure_ascii=False))
        time.sleep(.5)
    raise TimeoutError(key)
def main():
    model_id='a974c5da69695d0aba3477bff5c7b768'
    version=next(v for v in api('/datasets') if v['id']=='76b5a08af4ca471f9c6fa9c903347e69')
    clips=[c for c in version['clips'] if 3<=c['seconds']<=10]
    assert len(clips)>=2
    refs=[]
    for clip in clips[:2]:
        req=fresh(audio_id=clip['audio_id'],text=clip['text'],language='zh')
        ref=api('/references/import',req)
        assert api('/references/import',req)['id']==ref['id']
        refs.append(ref)
    a,b=refs
    report.update(model_id=model_id,reference_a=a,reference_b=b);save()
    refused=call('/references/import',fresh(audio_id=clips[0]['audio_id'],text='',language='zh'))
    assert refused[0]==422
    report['empty_reference_text_refused']=refused
    api('/models/'+model_id+'/reference',fresh(reference_id=a['id']))
    enabled=api('/models/'+model_id+'/enable',fresh())
    assert enabled['enabled'] and enabled['valid'] and enabled['active_model']==model_id
    assert not enabled['gpu_resident']
    report['logical_enabled']=enabled;save()
    train_id='cca9eda7641b4436b238c936a9b23ffd'
    resumed=api('/training/'+train_id+'/controls',fresh(action='resume'))
    report['resumed_train']=resumed
    req=fresh()
    comp_a=api('/models/'+model_id+'/compare',req)
    assert api('/models/'+model_id+'/compare',req)['id']==comp_a['id']
    report['comparison_a_submitted']=comp_a
    changed=api('/models/'+model_id+'/reference',fresh(reference_id=b['id']))
    assert changed['reference']['fingerprint']==b['fingerprint'] and a['fingerprint']!=b['fingerprint']
    report['default_changed_while_jobs_queued']=changed;save()
    done_a=compare_wait(comp_a['id'])
    assert {j['result']['reference_fingerprint'] for j in done_a['jobs']}=={a['fingerprint']}
    assert len({j['result']['conditions_fingerprint'] for j in done_a['jobs']})==1
    shown=next(m for m in api('/models') if m['id']==model_id)
    assert shown['validation']['gpu']=='NOT_RUN',shown['validation']
    report.update(comparison_a=done_a,new_reference_still_not_run=shown['validation']);save()
    store=Jobs(ROOT/'data')
    train=store.get(train_id)
    assert train['state']=='COMPLETED'
    train_end=next(e['created'] for e in store.events(train_id,0) if e['kind']=='state' and e['payload']['state']=='COMPLETED')
    starts=[]
    for child in done_a['jobs']:
        start=next(e['created'] for e in store.events(child['id'],0) if e['kind']=='state' and e['payload']['state']=='RUNNING')
        assert start>=train_end
        starts.append(start)
    report['gpu_exclusion']={'train_finished':train_end,'comparison_started':starts}
    diagnostic=ROOT/'data/diagnostics'/('p08-'+uuid.uuid4().hex)
    diagnostic.mkdir(parents=True)
    hold_path,release=diagnostic/'hold.json',diagnostic/'release'
    command=[str(ROOT/'.venv-worker/Scripts/python.exe'),str(ROOT/'scripts/gpu_occupancy_probe.py'),
        '--mode','hold','--mib','512','--output',str(hold_path),'--release-file',str(release),'--timeout-seconds','600']
    with (diagnostic/'hold.log').open('wb') as log:
        holder=subprocess.Popen(command,stdout=log,stderr=log,stdin=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        deadline=time.monotonic()+120
        while time.monotonic()<deadline:
            if hold_path.exists():
                held=json.loads(hold_path.read_text(encoding='utf-8'))
                if held.get('state')=='HOLDING':break
            if holder.poll() is not None:raise RuntimeError('occupancy probe exited; see '+str(diagnostic))
            time.sleep(.1)
        else:raise TimeoutError('GPU occupancy')
        report['external_gpu_hold']=held;save()
        comp_b=api('/models/'+model_id+'/compare',fresh())
        done_b=compare_wait(comp_b['id'])
        assert {j['result']['reference_fingerprint'] for j in done_b['jobs']}=={b['fingerprint']}
        assert done_b['jobs'][0]['result']['conditions_fingerprint']!=done_a['jobs'][0]['result']['conditions_fingerprint']
        report['comparison_b']=done_b;save()
    finally:
        release.write_text('release',encoding='utf-8')
        holder.wait(timeout=30)
        report['external_gpu_exit']=json.loads(hold_path.read_text(encoding='utf-8'));save()
    oom_path=diagnostic/'oom.json'
    with (diagnostic/'oom.log').open('wb') as log:
        oom=subprocess.run([str(ROOT/'.venv-worker/Scripts/python.exe'),str(ROOT/'scripts/gpu_occupancy_probe.py'),
            '--mode','oom','--output',str(oom_path)],stdout=log,stderr=log,timeout=120,creationflags=subprocess.CREATE_NO_WINDOW)
    report['oom']=json.loads(oom_path.read_text(encoding='utf-8'))
    report['oom_exit_code']=oom.returncode
    assert any(o['resident'] for o in report['observations'])
    assert not api('/system/status')['gpu_resident_models']
    assert digest(Path(a['path']))==a['sha256'] and digest(Path(b['path']))==b['sha256']
    report.update(final_selection=api('/synthesis/selection'),result='PASS');save()
    print(json.dumps({'result':'PASS','comparison_a':done_a['id'],'comparison_b':done_b['id']}),flush=True)
if __name__=='__main__':
    try:main()
    except BaseException as exc:report['error']=repr(exc);save();raise
