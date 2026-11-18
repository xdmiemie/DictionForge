"""Resumable real HTTP/CUDA feedback acceptance; never fabricates feature/ASR outputs."""
import json
from pathlib import Path
import sys
import time
from validate_p10 import api, fresh, finished, call

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from dictionforge.audio import atomic_json,digest
from dictionforge.training_config import normalize_config
OUTPUT=ROOT/'data/p11-http.json'
report=json.loads(OUTPUT.read_text(encoding='utf-8')) if OUTPUT.exists() else {'started':time.time(),'stage':'P11'}
MODEL='a974c5da69695d0aba3477bff5c7b768'

def save():atomic_json(OUTPUT,report)
def once(name,path,payload):
    if name not in report:
        report[name]=api(path,payload);save()
    result=finished(report[name]);report[name]=result;save();return result

def whole():
    if 'source' not in report:
        report['source']=api('/tts/current')
        assert report['source']['id']=='1ba9920f732a4eef902f6917b6c6a7b9'
        report['parent']=next(v for v in api('/datasets') if v['id']=='76b5a08af4ca471f9c6fa9c903347e69')
        report['parent_manifest_sha']=digest(Path(report['parent']['path'])/'manifest.json');save()
    source=report['source'];sentence=source['result']['sentences'][0]
    job=once('whole','/tts/'+source['id']+'/append',fresh(sentence_id=sentence['sentence_id'],target_version_id=report['parent']['id']))
    result=api('/augmentations/'+job['id']);assert result['state']=='PUBLISHED' and result['asr_calls']==0,result
    report['whole_result']=result
    child=next(v for v in api('/datasets') if v['id']==result['version_id'])
    assert child['added_count']==1
    assert child['clips'][-1]['text']==sentence['text']
    assert [(c['id'],c['sha256'],c['split']) for c in child['clips'][:-1]]==[(c['id'],c['sha256'],c['split']) for c in report['parent']['clips']]
    assert digest(Path(report['parent']['path'])/'manifest.json')==report['parent_manifest_sha']
    report['whole_version']=child
    report['whole_features']=json.loads((ROOT/'data/features'/child['id']/'manifest.json').read_text(encoding='utf-8'));save()
    duplicate=once('duplicate','/tts/'+source['id']+'/append',fresh(sentence_id=sentence['sentence_id'],target_version_id=report['parent']['id']))
    result=api('/augmentations/'+duplicate['id']);assert result['state']=='DUPLICATE' and result['version_id']==child['id']
    report['duplicate_result']=result;save()
    print(json.dumps({'whole':job['id'],'version':child['id'],'asr_calls':0,'features':report['whole_features']['stages'],'duplicate':result['state']},ensure_ascii=False))

def split():
    # One long utterance, then let the real dataset rules slice it. No copying full text per fragment.
    tts=once('split_tts','/tts',fresh(model_version_id=MODEL,reference_id='563ea3259eed48938003d5bb0566d68e',
        text='今天我们一起去公园散步，湖边的风景很美，微风吹过树梢，明天还可以再来。',
        language='zh',split_rule='none',parameters={'seed':73,'sample_steps':16}))
    speech=api('/tts/'+tts['id']);sentence=speech['result']['sentences'][0]
    job=once('split','/tts/'+tts['id']+'/append',fresh(sentence_id=sentence['sentence_id'],target_version_id=report['whole_version']['id'],
        split_config={'min_seconds':1.0,'max_seconds':3.0}))
    result=api('/augmentations/'+job['id']);assert result['state']=='REVIEW_REQUIRED',result
    assert result['asr_calls']>1
    assert all(c['text']!=sentence['text'] for c in result['draft']['clips'])
    report['split_result']=result;report['split_speech']=speech;save()
    print(json.dumps({'augmentation':job['id'],'draft':result['draft'],'asr_calls':result['asr_calls']},ensure_ascii=False))

def concurrent():
    source=report['source']
    for index in (1,2):
        name='queued_append_'+str(index)
        if name not in report:
            report[name]=api('/tts/'+source['id']+'/append',fresh(sentence_id=source['result']['sentences'][index]['sentence_id'],target_version_id=report['parent']['id']))
            save()
    if 'split_tts' not in report:
        report['split_tts']=api('/tts',fresh(model_version_id=MODEL,reference_id='563ea3259eed48938003d5bb0566d68e',
            text='今天我们一起去公园散步，湖边的风景很美，微风吹过树梢，明天还可以再来。',
            language='zh',split_rule='none',parameters={'seed':73,'sample_steps':16}))
        save()
    # Acceptance of the new TTS retires the old current immediately; queued append leases retain its WAVs.
    code,_,_=call('/audio/'+source['result']['audio']['audio_id'],method='HEAD')
    report['queued_source_head']=code;assert code==200
    for index in (1,2):
        job=finished(report['queued_append_'+str(index)])
        report['queued_append_'+str(index)]=job
        row=api('/augmentations/'+job['id']);assert row['state']=='PUBLISHED'
        report['queued_result_'+str(index)]=row;save()
    versions={v['id']:v for v in api('/datasets')}
    newest=versions[report['queued_result_2']['version_id']]
    assert newest['parent_id']==report['queued_result_1']['version_id']
    assert len(newest['clips'])==len(report['parent']['clips'])+3
    assert [(c['id'],c['sha256']) for c in newest['clips'] if c['split']=='validation']==[(c['id'],c['sha256']) for c in report['parent']['clips'] if c['split']=='validation']
    report['concurrent_outcome']='PASS';save();print('queued append / new TTS / parent chain: PASS')

def train():
    # Uses the fully published whole-utterance version for a controlled source/base comparison.
    version=report['whole_version']['id']
    for label,model in [('source',MODEL),('official','official')]:
        config=normalize_config(dict(version_id=version,initial_model=model,seed=42,
            s2={'stop_mode':'steps','target':2},s1={'stop_mode':'steps','target':2,'accumulate':1}))
        pre=once(label+'_preflight','/training/preflight',fresh(**config));assert pre['result']['ready'],pre
        job=once(label+'_train','/training',fresh(preflight_id=pre['id']))
        view=api('/training/'+job['id']);report[label+'_view']=view
        for stage in ('s2','s1'):
            metrics=job['result']['stages'][stage]
            assert metrics['optimizer_updates']==2 and metrics['initial_parameters_sha256']!=metrics['final_parameters_sha256']
            initial=json.loads((ROOT/'data/runs'/job['id']/(stage+'.initialization.json')).read_text(encoding='utf-8'))
            assert initial['optimizer_updates']==0 and initial['optimizer_state_entries']==0
            if label=='source':
                assert initial['loaded']['all_tensors_equal']
                assert initial['loaded']['source_load_count']==1
            if stage=='s2':assert initial['loaded']['base_load_count']==initial['loaded']['lora_wrap_count']==1
            report[label+'_'+stage+'_initialization']=initial
        view['exports']=[finished(export) for export in view['exports']]
        save()
    for stage in ('s2','s1'):
        assert report['source_'+stage+'_initialization']['parameters_sha256']!=report['official_'+stage+'_initialization']['parameters_sha256']
    report['training_outcome']='PASS';save();print('source and official real optimizer updates: PASS')

def probes():
    source=next(m for m in api('/models') if m['id']==MODEL)
    reference=source['default_reference']
    for label in ('source','official'):
        view=api('/training/'+report[label+'_train']['id'])
        view['exports']=[finished(export) for export in view['exports']]
        report[label+'_view']=view;save()
        model=view['exports'][0]['result']['model_version_id']
        job=once(label+'_inference','/models/'+model+'/probe',fresh(reference_audio_id=reference['audio_id'],reference_text=reference['text']))
        assert job['result']['audio']['sample_rate']==48000
        assert Path(job['result']['audio']['path']).is_file()
    report['inference_outcome']='PASS';save();print('both exported initialization paths: native 48k inference PASS')

if __name__=='__main__':
    {'whole':whole,'split':split,'train':train,'concurrent':concurrent,'probes':probes}[sys.argv[1]]()
