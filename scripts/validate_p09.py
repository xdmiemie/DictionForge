"""Real HTTP/GPU P09 acceptance. Creates TTS results; does not save library works."""
import array
import hashlib
import json
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request
import uuid
import wave

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from dictionforge.audio import atomic_json,digest

URL='http://127.0.0.1:7867/api'
OUTPUT=ROOT/'data/p09-http.json'
MODEL='a974c5da69695d0aba3477bff5c7b768'
report={'cases':{},'observations':[],'started':time.time()}

def call(path,data=None):
    request=urllib.request.Request(URL+path,data=None if data is None else json.dumps(data).encode(),
        headers={'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(request,timeout=180) as response:return response.status,json.load(response)
    except urllib.error.HTTPError as exc:return exc.code,json.loads(exc.read())

def api(path,data=None):
    code,result=call(path,data)
    if not 200<=code<300:raise RuntimeError(f'{path}: {code}: {result}')
    return result

def save():atomic_json(OUTPUT,report)

def pcm(path):
    with wave.open(str(path),'rb') as stream:
        assert (stream.getframerate(),stream.getnchannels(),stream.getsampwidth())==(48000,1,2)
        count=stream.getnframes()
        raw=stream.readframes(count+1)
        assert len(raw)==count*2 and count>0
    values=array.array('h',raw)
    assert any(values)
    return raw,count

def wait(key):
    deadline=time.monotonic()+900
    while time.monotonic()<deadline:
        view=api('/tts/'+key)
        status=api('/system/status')
        report['observations'].append({'at':time.time(),'id':key,'state':view['job']['state'],
            'gpu_jobs':[j['id'] for j in status['gpu_jobs']],'resident':status['gpu_resident_models']})
        assert len(status['gpu_jobs'])<=1
        if view['job']['state']=='COMPLETED':return view
        if view['job']['state'] in {'FAILED','INTERRUPTED','CANCELLED'}:raise RuntimeError(json.dumps(view,ensure_ascii=False))
        time.sleep(.5)
    raise TimeoutError(key)

def verify(view,expected):
    request,result=view['request'],view['result']
    assert request['text']==expected['text'] and result['text']==expected['text']
    assert result['model_id']==MODEL
    assert len(result['sentences'])==expected['count']
    full,count=pcm(result['audio']['path'])
    assert digest(Path(result['audio']['path']))==result['audio']['sha256']
    assert count==result['audio']['samples']
    ids=set(); cursor=0; clips=[]
    for index,unit in enumerate(result['sentences']):
        assert unit['index']==index and unit['sentence_id'] not in ids
        ids.add(unit['sentence_id'])
        assert request['text'][unit['source_start']:unit['source_end']]==unit['text']
        assert unit['actual_seed']==(request['parameters']['seed']+index)%2**32
        assert unit['start_sample']==cursor
        raw,samples=pcm(unit['audio']['path'])
        assert digest(Path(unit['audio']['path']))==unit['audio']['sha256']
        assert unit['end_sample']-unit['start_sample']==samples
        assert full[unit['start_sample']*2:unit['end_sample']*2]==raw
        assert unit['audio']['audio_id'] and unit['audio']['sample_rate']==48000
        cursor=unit['end_sample']
        clips.append(dict(id=unit['sentence_id'],path=unit['audio']['path'],language='zh',expected_text=unit['text']))
    assert cursor==count
    events=result['phase_events']
    assert sum(e['stage']=='model_ready' for e in events)==1
    return dict(full_pcm_sha256=hashlib.sha256(full).hexdigest(),samples=count,
        sentence_count=len(ids),exact_pcm_concatenation=True,one_model_load=True,asr_clips=clips)

def main():
    cases={
        'single':dict(text='今天天气很好，我们一起去公园散步。',split_rule='punctuation',count=1),
        'repeated':dict(text=' 你好，今天我们一起去公园散步。\n我们先去湖边看风景。\n你好，今天我们一起去公园散步。 ',split_rule='punctuation',count=3),
        'lines':dict(text='第一行保持原样，先看蓝天。再看白云。\n第二行开始，慢慢读完这段话。',split_rule='lines',count=2),
        'none':dict(text='这是没有分句的测试。两句话使用同一个单元。',split_rule='none',count=1),
    }
    report['selection_before']=api('/synthesis/selection')
    clips=[]
    for name,case in cases.items():
        payload=dict(request_id=str(uuid.uuid4()),model_version_id=MODEL,text=case['text'],language='zh',
            split_rule=case['split_rule'],parameters={'seed':42,'speed_factor':1.0,'sample_steps':32})
        submitted=api('/tts',payload)
        report['cases'][name]={'submitted':submitted,'payload':payload};save()
        assert api('/tts',payload)['id']==submitted['id']
        assert api('/tts/current')['id']==submitted['id']
        frozen=api('/tts/'+submitted['id'])['request']
        assert frozen['text']==case['text']
        done=wait(submitted['id'])
        checked=verify(done,case)
        clips.extend(checked['asr_clips'])
        report['cases'][name].update(snapshot=done,validation=checked);save()
        print(name,submitted['id'],'PASS',flush=True)
    current=api('/tts/current')['id']
    old=report['cases']['single']
    assert api('/tts',old['payload'])['id']==old['submitted']['id']
    assert api('/tts/current')['id']==current
    report['old_idempotent_retry_preserved_current']=True
    refusals=[]
    for change in ({'text':''},{'text':' \n\t '},{'text':'？！……'},{'language':'fr'},
                   {'split_rule':'unknown'},{'parameters':{'speed_factor':0}}, {'text':'测试\u0000文本'}):
        payload=dict(old['payload'],request_id=str(uuid.uuid4()),**change)
        response=call('/tts',payload)
        assert response[0]==422,response
        assert api('/tts/current')['id']==current
        refusals.append({'change':change,'status':response[0],'response':response[1]})
    report['invalid_requests_preserved_current']=refusals
    repeated=report['cases']['repeated']['snapshot']['result']['sentences']
    assert repeated[0]['text']==repeated[2]['text'] and repeated[0]['sentence_id']!=repeated[2]['sentence_id']
    assert repeated[0]['source_start']!=repeated[2]['source_start']
    report['repeated_sentence_ids_and_offsets_distinct']=True
    request={'upstream':str(ROOT/'vendor/gpt-sovits'),'clips':clips}
    atomic_json(ROOT/'data/p09-asr-request.json',request)
    report.update(outcome='PASS',finished=time.time(),current_id=current);save()
    print('P09 HTTP acceptance PASS',flush=True)

if __name__=='__main__':main()
