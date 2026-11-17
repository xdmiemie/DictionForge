"""Live CPU/GPU overlap and three exported-pair synthesis checks over HTTP."""
import json
from pathlib import Path
import time
import urllib.error
import urllib.request
import uuid
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from dictionforge.audio import atomic_json

URL='http://127.0.0.1:7867/api'
OUTPUT=ROOT/'data/p07-http.json'
report={'polls':[]}

def api(path,data=None,method=None):
    req=urllib.request.Request(URL+path,data=json.dumps(data).encode() if data is not None else None,
        method=method,headers={'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(req,timeout=240) as response: return json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f'{path}: {exc.code}: {exc.read().decode()}') from exc

def fresh(**values): return dict(values,request_id=str(uuid.uuid4()))
def save(): atomic_json(OUTPUT,report)

def wait_job(job,states=('COMPLETED',),timeout=1200):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        job=api('/jobs/'+job['id'])
        if job['state'] in states: return job
        if job['state'] not in ('QUEUED','RUNNING'): raise RuntimeError(json.dumps(job,ensure_ascii=False))
        time.sleep(.3)
    raise TimeoutError(job['id'])

def start_training(target):
    pre=wait_job(api('/training/preflight',fresh(version_id='76b5a08af4ca471f9c6fa9c903347e69',
        s2=dict(stop_mode='steps',target=target),s1=dict(stop_mode='steps',target=4))))
    assert pre['result']['ready'],pre
    return api('/training',fresh(preflight_id=pre['id']))

def stage(view,name): return (view.get('stage_state') or {}).get('stages',{}).get(name,{})

def wait_update(job,minimum=2):
    deadline=time.monotonic()+900
    while time.monotonic()<deadline:
        view=api('/training/'+job['id'])
        if view['state']=='FAILED': raise RuntimeError(view['error'])
        if stage(view,'s2').get('optimizer_updates',0)>=minimum:return view
        time.sleep(.1)
    raise TimeoutError(job['id'])

def main():
    job=start_training(40)
    report['continue_run_id']=job['id']; save()
    before=wait_update(job)
    command=fresh(action='export')
    api('/training/'+job['id']+'/controls',command)
    exported=None
    deadline=time.monotonic()+1200
    while time.monotonic()<deadline:
        view=api('/training/'+job['id'])
        exports=view['exports']
        update=stage(view,'s2').get('optimizer_updates',0)
        report['polls'].append(dict(time=time.time(),train_state=view['state'],s2_updates=update,
            s1_updates=stage(view,'s1').get('optimizer_updates',0),exports=[dict(id=e['id'],state=e['state'],reason=e['payload']['reason']) for e in exports]))
        exported=next((e for e in exports if e['payload']['reason']=='export'),None)
        if view['state']=='FAILED': raise RuntimeError(view['error'])
        if view['state']=='COMPLETED': break
        time.sleep(.25)
    else: raise TimeoutError('continue train')
    assert exported
    report['continue_completed']=view
    report['mid_export']=wait_job(exported)
    auto=next(e for e in view['exports'] if e['payload']['reason']=='completed')
    report['automatic_export']=wait_job(auto)
    report['cpu_gpu_overlap']=any(p['train_state']=='RUNNING' and p['s2_updates']>stage(before,'s2')['optimizer_updates']
        and any(e['state']=='RUNNING' for e in p['exports']) for p in report['polls'])
    assert report['cpu_gpu_overlap']
    assert stage(view,'s2')['optimizer_updates']==40 and stage(view,'s1')['optimizer_updates']==4
    save()
    early=start_training(40)
    report['early_run_id']=early['id'];save()
    wait_update(early)
    api('/training/'+early['id']+'/controls',fresh(action='stop_export'))
    report['early_completed']=wait_job(early,('EARLY_COMPLETED',))
    early_view=api('/training/'+early['id'])
    early_export=next(e for e in early_view['exports'] if e['payload']['reason']=='stop_export')
    report['early_export']=wait_job(early_export)
    assert stage(early_view,'s2')['optimizer_updates']<40
    assert stage(early_view,'s1').get('optimizer_updates',0)==0
    report['early_snapshot']=early_view; save()
    # Every publication path loads the real pair and synthesizes native 48 kHz.
    report['gpu_probes']={}
    for name in ('mid_export','automatic_export','early_export'):
        model_id=report[name]['result']['model_version_id']
        probe=wait_job(api('/models/'+model_id+'/probe',fresh()))
        assert probe['result']['audio']['sample_rate']==48000
        assert probe['result']['audio']['samples']>0
        report['gpu_probes'][name]=probe;save()
    model_id=report['automatic_export']['result']['model_version_id']
    report['models']=api('/models')
    assert next(m for m in report['models'] if m['id']==model_id)['validation']['gpu']=='PASS'
    report['result']='PASS';save()
    print(json.dumps({'result':'PASS','run_id':job['id'],'early_run_id':early['id'],
        'models':[report[name]['result']['model_version_id'] for name in ('mid_export','automatic_export','early_export')]}),flush=True)

if __name__=='__main__':
    try:main()
    except BaseException as exc: report['error']=repr(exc);save();raise
