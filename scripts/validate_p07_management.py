"""Real weight import, Windows publication failure/retry, and protected deletion."""
import ctypes
import json
from pathlib import Path
import sys
import time
import urllib.request
import urllib.error
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from dictionforge.audio import atomic_json,digest
URL='http://127.0.0.1:7867/api'
OUTPUT=ROOT/'data/p07-management.json'
report={}

def call(path,data=None,method=None):
    req=urllib.request.Request(URL+path,data=json.dumps(data).encode() if data is not None else None,
        method=method,headers={'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(req,timeout=240) as response:return response.status,json.load(response)
    except urllib.error.HTTPError as exc:return exc.code,json.loads(exc.read())
def api(path,data=None,method=None):
    code,value=call(path,data,method)
    if not 200<=code<300:raise RuntimeError(f'{path}: {code}: {value}')
    return value
def fresh(**values):return dict(values,request_id=str(uuid.uuid4()))
def save():atomic_json(OUTPUT,report)
def wait(job):
    deadline=time.monotonic()+900
    while time.monotonic()<deadline:
        job=api('/jobs/'+job['id'])
        if job['state'] not in ('QUEUED','RUNNING'):return job
        time.sleep(.2)
    raise TimeoutError(job['id'])

def main():
    prior=json.loads((ROOT/'data/p07-http.json').read_text(encoding='utf-8'))
    original=prior['automatic_export']['result']['model']
    source_hashes={item['path']:item['sha256'] for item in original['weights'].values()}
    denied=call('/models/import',fresh(gpt_path=original['weights']['gpt']['path'],
        sovits_path=original['weights']['sovits']['path'],role='未确认来源',trusted=False))
    assert denied[0]==422
    report['untrusted_refused']=denied;save()
    imported=api('/models/import',fresh(gpt_path=original['weights']['gpt']['path'],
        sovits_path=original['weights']['sovits']['path'],role='P07 导入与重试验收副本',trusted=True))
    report['import_job']=imported;save()
    snapshot_id=imported['payload']['snapshot_id']
    model_id=imported['payload']['model_version_id']
    staging=ROOT/'data/models'/('external-'+snapshot_id[:16])/('.'+model_id+'.part')
    deadline=time.monotonic()+60
    while not staging.is_dir() and time.monotonic()<deadline:time.sleep(.01)
    assert staging.is_dir()
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateFileW.argtypes=[ctypes.c_wchar_p,ctypes.c_uint32,ctypes.c_uint32,ctypes.c_void_p,ctypes.c_uint32,ctypes.c_uint32,ctypes.c_void_p]
    kernel.CreateFileW.restype=ctypes.c_void_p
    kernel.CloseHandle.argtypes=[ctypes.c_void_p]
    handle=kernel.CreateFileW(str(staging),0x80000000,3,None,3,0x02000000,None)
    assert handle!=ctypes.c_void_p(-1).value,ctypes.get_last_error()
    frozen=ROOT/'data/export_snapshots'/snapshot_id/'manifest.json'
    frozen_hash=digest(frozen)
    try:failed=wait(imported)
    finally:kernel.CloseHandle(handle)
    report['locked_directory_failure']=failed;save()
    assert failed['state']=='FAILED' and ('WinError' in failed['error'] or 'Permission' in failed['error']),failed
    assert not any(item['id']==model_id for item in api('/models'))
    retry_request=fresh()
    retry=api('/model-exports/'+imported['id']+'/retry',retry_request)
    assert api('/model-exports/'+imported['id']+'/retry',retry_request)['id']==retry['id']
    published=wait(retry)
    assert published['state']=='COMPLETED',published
    assert published['result']['model_version_id']==model_id
    assert digest(frozen)==frozen_hash
    assert len([item for item in api('/models') if item['id']==model_id])==1
    report['retry']=published;report['snapshot_unchanged_sha256']=frozen_hash;save()
    # Registered model copies are independent of trusted source files.
    preview=api('/models/'+model_id+'/delete-preview')
    assert preview['deletable'] and preview['files'] and not preview['references']
    deleted=api('/models/'+model_id,{'preview_id':preview['preview_id']},'DELETE')
    assert deleted['state']=='DELETED'
    assert all(not Path(item['path']).exists() for item in preview['files'])
    assert all(digest(Path(path))==checksum for path,checksum in source_hashes.items())
    retired=call('/model-exports/'+imported['id']+'/retry',fresh())
    assert retired[0] in (409,422)
    report.update(model_preview=preview,model_deleted=deleted,retired_retry_refused=retired,source_weights_unchanged=source_hashes)
    version='76b5a08af4ca471f9c6fa9c903347e69'
    protected=api('/dataset-versions/'+version+'/delete-preview')
    assert not protected['deletable'] and protected['references']
    denied=call('/dataset-versions/'+version,{'preview_id':protected['preview_id']},'DELETE')
    assert denied[0]==409
    report.update(protected_dataset=protected,protected_delete_refused=denied);save()
    draft=api('/dataset-versions/'+version+'/draft',fresh())
    new=api('/dataset-drafts/'+draft['id']+'/publish',{'revision':draft['revision']})
    report['clone_version']=new;save()
    preview=api('/dataset-versions/'+new['id']+'/delete-preview')
    assert preview['deletable'],preview
    deleted=api('/dataset-versions/'+new['id'],{'preview_id':preview['preview_id']},'DELETE')
    assert deleted['state']=='DELETED'
    assert all(not Path(item['path']).exists() for item in preview['files'])
    assert any(item['id']==version for item in api('/datasets'))
    report.update(dataset_preview=preview,dataset_deleted=deleted,result='PASS');save()
    print(json.dumps({'result':'PASS','import_version':model_id,'deleted_clone':new['id']}),flush=True)

if __name__=='__main__':
    try:main()
    except BaseException as exc:report['error']=repr(exc);save();raise
