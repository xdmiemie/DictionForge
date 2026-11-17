"""Frozen-pair GPU load/synthesis acceptance, sharing the training GPU lane."""
from pathlib import Path
import time
from .audio import atomic_json, digest, probe
from .engine import invoke
from .jobs import Conflict
from .records import get, put, media
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
    def accepted(db,job_id):
        if model_id!='official' and get(jobs,'model',model_id,db).get('status')!='READY':
            raise Conflict('模型已不可用')
        area=jobs.root/'runs'/job_id
        area.mkdir(parents=True,exist_ok=True)
        request=dict(data_root=str(jobs.root),output_dir=str(area),upstream=str(Settings.from_environment().upstream),
            model_id=model_id,weights={key:item['path'] for key,item in model['weights'].items()},
            weight_hashes={key:item['sha256'] for key,item in model['weights'].items()},
            reference=frozen,text=FIXED_TEXT,language='zh',parameters=PARAMETERS,precision='fp16')
        request['conditions_fingerprint']=config_fingerprint({key:request[key] for key in ('reference','text','language','parameters','precision')})
        put(jobs,'model_probe',job_id,request,db)
        atomic_json(area/'inference-request.json',request)
    return jobs.submit('model_probe',payload,request_id,on_accept=accepted)

def process(jobs,job):
    request=get(jobs,'model_probe',job['id'])
    area=jobs.root/'runs'/job['id']
    output=area/'inference-result.json'
    settings=Settings.from_environment()
    result=invoke(jobs,job,[settings.worker_python,'-m','dictionforge.inference_worker',area/'inference-request.json',output],result=output)
    if result.get('outcome')=='FAILED': return result
    audio=result['audio']
    if audio['sample_rate']!=48000 or digest(Path(audio['path']))!=audio['sha256']:
        raise ValueError('INFERENCE_OUTPUT_INVALID')
    result.update(model_version_id=request['model_id'],conditions_fingerprint=request['conditions_fingerprint'],created=time.time())
    with jobs.connect(write=True) as db:
        audio['audio_id']=media(jobs,audio['path'],owner=job['id'],db=db)
        put(jobs,'model_test',job['id'],result,db)
        if request['model_id']!='official':
            model=get(jobs,'model',request['model_id'],db)
            model['validation'].update(gpu='PASS',gpu_test_id=job['id'])
            put(jobs,'model',model['id'],model,db)
    return result
