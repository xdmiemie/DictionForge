"""Frozen-pair GPU load/synthesis acceptance, sharing the training GPU lane."""
from pathlib import Path
import time
import uuid
from .audio import atomic_json, digest, probe
from .engine import invoke
from .jobs import Conflict
from .records import get, put, media, list_records
from .settings import Settings
from .training_config import config_fingerprint

FIXED_TEXT = "你好，欢迎来到声句工坊。"
PARAMETERS = dict(seed=42, sample_steps=32, top_k=5, top_p=1.0, temperature=1.0,
    speed_factor=1.0, repetition_penalty=1.35, fragment_interval=.3)

def submit(jobs, model_id, request_id, reference_audio_id=None, reference_text=None, reference_language='zh'):
    payload = dict(model_version_id=model_id, reference_audio_id=reference_audio_id,
                   reference_text=reference_text, reference_language=reference_language)
    with jobs.connect() as db:
        old=db.execute('SELECT * FROM jobs WHERE request=?',(request_id,)).fetchone()
        if old:
            old=jobs.decode(old)
            if old['kind']!='model_probe' or old['payload']!=payload:
                raise Conflict('请求 ID 已绑定其他试听条件')
            return old
    from .models import resolve
    model=resolve(jobs,model_id)
    if reference_audio_id:
        reference=get(jobs,'media',reference_audio_id)
        text=reference_text or ''
        language=reference_language
    else:
        reference=model.get('default_reference')
        if not reference: raise ValueError('REFERENCE_REQUIRED：请选择参考音频与准确文本')
        text=reference['text']
        language=reference['language']
    if language!='zh' or not text.strip():
        raise ValueError('参考语言须为 zh，参考文本不能为空')
    path=Path(reference['path'])
    if reference.get('state')=='DELETED' or not path.is_file() or digest(path)!=reference['sha256']:
        raise ValueError('REFERENCE_CHANGED：参考音频已失效或改变')
    info=probe(path)
    if not 3 <= info['seconds'] <= 10:
        raise ValueError('REFERENCE_DURATION：v4主参考需要3至10秒')
    frozen=dict(path=str(path),sha256=reference['sha256'],text=text.strip(),language=language,seconds=info['seconds'])
    frozen['fingerprint']=config_fingerprint({key:frozen[key] for key in ('sha256','text','language')})
    def accepted(db,job_id):
        if model_id!='official' and get(jobs,'model',model_id,db).get('status')!='READY':
            raise Conflict('模型已不可用')
        area=jobs.root/'runs'/job_id
        area.mkdir(parents=True,exist_ok=True)
        request=dict(data_root=str(jobs.root),output_dir=str(area),upstream=str(Settings.from_environment().upstream),
            model_id=model_id,weights={key:item['path'] for key,item in model['weights'].items()},
            weight_hashes={key:item['sha256'] for key,item in model['weights'].items()},
            reference=frozen,text=FIXED_TEXT,language='zh',parameters=PARAMETERS,precision='fp16',job_id=job_id)
        request['reference_fingerprint']=frozen['fingerprint']
        request['conditions_fingerprint']=config_fingerprint(dict(reference=frozen['fingerprint'],
            **{key:request[key] for key in ('text','language','parameters','precision')}))
        put(jobs,'model_probe',job_id,request,db)
        atomic_json(area/'inference-request.json',request)
    return jobs.submit('model_probe',payload,request_id,on_accept=accepted)

def process(jobs,job):
    request=get(jobs,'model_probe',job['id'])
    area=jobs.root/'runs'/job['id']
    output=area/'inference-result.json'
    settings=Settings.from_environment()
    from .errors import Cancelled
    try:
        result=invoke(jobs,job,[settings.worker_python,'-m','dictionforge.inference_worker',area/'inference-request.json',output],result=output)
    except Cancelled:
        raise
    except Exception as exc:
        result={'outcome':'FAILED','error':str(exc),'code':type(exc).__name__}
    finally:
        jobs.progress(job['id'],{'stage':'gpu_released','model_id':request['model_id'],'gpu_resident':False})
    result.update(model_version_id=request['model_id'],conditions_fingerprint=request['conditions_fingerprint'],
                  reference_fingerprint=request['reference_fingerprint'],created=time.time())
    if result.get('outcome')!='FAILED':
        try:
            audio=result['audio']
            if audio['sample_rate']!=48000 or digest(Path(audio['path']))!=audio['sha256']:
                raise ValueError('INFERENCE_OUTPUT_INVALID')
        except (KeyError,ValueError,OSError) as exc:
            result.update(outcome='FAILED',error=str(exc),code=type(exc).__name__,phase='output_validation')
    if result.get('outcome')=='FAILED':
        put(jobs,'model_test',job['id'],result)
        return result
    with jobs.connect(write=True) as db:
        audio['audio_id']=media(jobs,audio['path'],owner=job['id'],db=db)
        put(jobs,'model_test',job['id'],result,db)
        if request['model_id']!='official':
            model=get(jobs,'model',request['model_id'],db)
            current_ref=model.get('default_reference')
            current_fp=config_fingerprint({key:current_ref[key] for key in ('sha256','text','language')}) if current_ref else None
            if current_fp==request['reference_fingerprint']:
                model['validation'].update(gpu='PASS',gpu_test_id=job['id'],reference_fingerprint=current_fp)
            put(jobs,'model',model['id'],model,db)
    return result


def compare(jobs,model_id,request_id):
    if model_id=='official':
        raise ValueError('请选择自训或导入模型，与官方底模对比')
    from .models import resolve
    from .references import acquire,release
    try:
        comparison=get(jobs,'comparison',request_id)
        if comparison['model_id']!=model_id:raise Conflict('比较请求已绑定其他模型')
    except KeyError:
        model=resolve(jobs,model_id)
        ref=model.get('default_reference')
        if not ref or not ref.get('audio_id'):raise ValueError('请先设置有效默认参考')
        ref=dict(ref)
        ref['fingerprint']=config_fingerprint({key:ref[key] for key in ('sha256','text','language')})
        comparison=dict(id=request_id,comparison_id=request_id,model_id=model_id,reference=ref,
            parameters=PARAMETERS,text=FIXED_TEXT,created=time.time(),job_ids=[],state='PREPARING')
        with jobs.connect(write=True) as db:
            try:
                previous=get(jobs,'comparison',request_id,db)
                if previous['model_id']!=model_id:raise Conflict('比较请求已绑定其他模型')
                comparison=previous
            except KeyError:put(jobs,'comparison',request_id,comparison,db)
    token=None
    try:
        token=acquire(jobs,'model',model_id,'comparison:'+request_id)
        ref=comparison['reference']
        ids=[]
        for member in (model_id,'official'):
            child_id=str(uuid.uuid5(uuid.NAMESPACE_URL,'dictionforge:compare:'+request_id+':'+member))
            job=submit(jobs,member,child_id,ref['audio_id'],ref['text'],ref['language'])
            ids.append(job['id'])
            comparison['job_ids']=list(ids)
            put(jobs,'comparison',request_id,comparison)
        comparison['state']='QUEUED'
        comparison.pop('error',None)
        put(jobs,'comparison',request_id,comparison)
    except Exception as exc:
        comparison.update(state='FAILED',error=str(exc))
        put(jobs,'comparison',request_id,comparison)
        raise
    finally:
        if token:release(jobs,token)
    return comparison_view(jobs,request_id)


def comparison_view(jobs,key):
    result=get(jobs,'comparison',key)
    members=[jobs.get(job_id) for job_id in result.get('job_ids',[])]
    if len(members)==2:
        states={job['state'] for job in members}
        if states=={'COMPLETED'}:
            fingerprints={job['result']['conditions_fingerprint'] for job in members}
            result['state']='COMPLETED' if len(fingerprints)==1 else 'FAILED'
            if len(fingerprints)!=1:result['error']='对比条件指纹不一致'
        elif states & {'FAILED','CANCELLED','INTERRUPTED'}:result['state']='FAILED'
        else:result['state']='RUNNING' if 'RUNNING' in states else 'QUEUED'
    return dict(result,jobs=members)


def comparisons(jobs):
    return [comparison_view(jobs,item['id']) for item in list_records(jobs,'comparison')]
