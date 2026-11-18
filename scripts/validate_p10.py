"""Actual HTTP, FFmpeg, Windows handle and GPU lifecycle acceptance for P10."""
import ctypes
import json
from pathlib import Path
import sys
import time
import urllib.request
import urllib.error
import urllib.parse
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from dictionforge.audio import atomic_json,digest,probe
from dictionforge.supervisor import exclusive
from dictionforge.jobs import Jobs
URL='http://127.0.0.1:7867/api'
OUTPUT=ROOT/'data/p10-http.json'
report={}

def call(path,body=None,method=None,headers=None):
    request=urllib.request.Request(URL+path,data=None if body is None else json.dumps(body).encode(),method=method,
        headers={'Content-Type':'application/json',**(headers or {})})
    try:
        with urllib.request.urlopen(request,timeout=180) as response:
            raw=response.read()
            return response.status,dict(response.headers),json.loads(raw) if response.headers.get('Content-Type','').startswith('application/json') else raw
    except urllib.error.HTTPError as exc:
        raw=exc.read()
        return exc.code,dict(exc.headers),json.loads(raw) if exc.headers.get('Content-Type','').startswith('application/json') else raw

def api(path,body=None,method=None):
    code,_,result=call(path,body,method)
    if not 200<=code<300:raise RuntimeError(f'{path}: {code}: {result}')
    return result

def fresh(**values):return dict(values,request_id=str(uuid.uuid4()))
def save():atomic_json(OUTPUT,report)
def eventually(fn,timeout=180):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        result=fn()
        if result:return result
        time.sleep(.2)
    raise TimeoutError(str(fn))
def finished(job,expected='COMPLETED'):
    result=eventually(lambda:(j if (j:=api('/jobs/'+job['id']))['state'] not in ('QUEUED','RUNNING') else None),900)
    assert result['state']==expected,result
    return result
def new_tts(text):
    return api('/tts',fresh(model_version_id=report['model_id'],text=text,language='zh',split_rule='punctuation',parameters={'sample_steps':16,'seed':42}))
def saved(key,fmt,title):
    request=fresh(format=fmt,title=title)
    job=api('/tts/'+key+'/save',request)
    assert api('/tts/'+key+'/save',request)['id']==job['id']
    return finished(job)['result']['saved_speech']
def check_audio(record):
    audio=record['audio'];path=Path(audio['path']);info=probe(path)
    assert info['sample_rate']==48000 and info['channels']==1 and info['seconds']>0
    assert digest(path)==audio['sha256']
    response=call('/audio/'+audio['audio_id'],headers={'Range':'bytes=0-99'})
    assert response[0]==206 and response[2]==path.read_bytes()[:100]
    assert response[1].get('content-range',response[1].get('Content-Range'))==f'bytes 0-99/{path.stat().st_size}'
    assert call('/audio/'+audio['audio_id'],headers={'Range':'bytes=999999999-'})[0]==416
    download=call('/audio/'+audio['audio_id']+'?download=true',method='HEAD')
    assert download[0]==200 and 'attachment' in str(download[1]).lower()
    return dict(probe=info,range206=True,range416=True,download_head=True,sha256=audio['sha256'])

def main():
    if '--verify-restart' in sys.argv:
        report.update(json.loads(OUTPUT.read_text(encoding='utf-8')))
        report['restart_validation']=[check_audio(item) for item in report['permanent']]
        assert api('/tts/current')['id']==report['final_current']['id']
        report['outcome']='PASS';save();return
    if OUTPUT.exists():raise RuntimeError('Existing P10 evidence; do not accidentally repeat mutating acceptance')
    current=api('/tts/current');report.update(started=time.time(),original_current=current['id'],model_id=current['request']['model_id'])
    report['permanent']=[saved(current['id'],'wav','P10 保留 WAV'),saved(current['id'],'mp3','P10 保留 MP3')]
    report['permanent_audio_checks']=[check_audio(item) for item in report['permanent']];save()
    audio=current['result']['audio'];browser=api('/media-leases',{'audio_id':audio['audio_id']})
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateFileW.argtypes=[ctypes.c_wchar_p,ctypes.c_uint32,ctypes.c_uint32,ctypes.c_void_p,ctypes.c_uint32,ctypes.c_uint32,ctypes.c_void_p]
    kernel.CreateFileW.restype=ctypes.c_void_p;kernel.CloseHandle.argtypes=[ctypes.c_void_p]
    handle=kernel.CreateFileW(audio['path'],0x80000000,1,None,3,0,None)
    assert handle not in (None,ctypes.c_void_p(-1).value)
    try:
        with exclusive(ROOT/'data/state/cpu-worker.lock'):
            saving=api('/tts/'+current['id']+'/save',fresh(format='wav',title='P10 并发保存验收'))
            new=finished(new_tts('新的合成结果已经准备好了。'))
            assert Path(audio['path']).is_file() and api('/jobs/'+saving['id'])['state']=='QUEUED'
            api('/media-leases/'+browser['token']+'/release',{})
            time.sleep(2.5)
            assert Path(audio['path']).is_file(), 'queued save lease must protect old PCM'
        race=finished(saving)['result']['saved_speech']
        pending=eventually(lambda:(view if (view:=api('/tts/'+current['id'])).get('result',{}).get('media_state')=='PURGE_PENDING' else None))
        report['save_cleanup_race']={'saved':race,'old_media_state':pending['result']['media_state'],'raw_windows_handle_preserved':Path(audio['path']).is_file()};save()
    finally:kernel.CloseHandle(handle)
    gone=eventually(lambda:(view if (view:=api('/tts/'+current['id'])).get('result',{}).get('media_state')=='DELETED' else None))
    assert gone['request']['text']==current['request']['text'] and not Path(audio['path']).exists()
    assert call('/audio/'+audio['audio_id'])[0]==410
    assert call('/tts/'+current['id']+'/save',fresh(format='wav',title='expired'))[0]==410
    report['old_result_expired']=True
    before=api('/tts/current')['id']
    assert call('/tts',fresh(model_version_id=report['model_id'],text='？！'))[0]==422
    assert api('/tts/current')['id']==before
    cancelling=new_tts('现在验证合成中取消，已经生成的临时文件会被正确处理。'*20)
    ready=eventually(lambda:(j if (j:=api('/jobs/'+cancelling['id'])).get('state')=='RUNNING' and any(e.get('stage') in ('model_ready','text_frontend','synthesizing') for e in Jobs(ROOT/'data').events(cancelling['id'],0) for e in [e.get('payload',{})]) else None),180)
    api('/jobs/'+cancelling['id']+'/control',fresh(action='cancel'))
    report['cancelled_job']=finished(cancelling,'CANCELLED');save()
    final=finished(new_tts('今天我们一起去公园散步。湖边的风景很美。明天还可以再来。'))
    report['final_current']=api('/tts/'+final['id']);save()
    prefix='P10 删除范围 '+uuid.uuid4().hex[:8]
    items=[saved(final['id'],'wav',prefix+' '+str(i)) for i in range(12)]
    query=urllib.parse.quote(prefix)
    page1=api('/saved-speech?query='+query+'&page=1');page2=api('/saved-speech?query='+query+'&page=2')
    assert len(page1['items'])==10 and len(page2['items'])==2 and page1['total']==12
    preview=api('/saved-speech/delete-preview',{'all_filtered':True,'query':prefix})
    assert set(preview['ids'])=={item['id'] for item in items}
    extra=saved(final['id'],'wav',prefix+' preview之后新增')
    protected=api('/media-leases',{'audio_id':items[0]['audio']['audio_id']})
    deleted=api('/saved-speech/delete',{'preview_id':preview['preview_id']})
    assert deleted['counts']['deleted']==11 and deleted['counts']['protected']==1,deleted
    assert api('/saved-speech?query='+query)['total']==2
    api('/media-leases/'+protected['token']+'/release',{})
    remaining=api('/saved-speech/delete-preview',{'ids':[items[0]['id'],extra['id']]})
    result=api('/saved-speech/delete',{'preview_id':remaining['preview_id']})
    assert result['counts']['deleted']==2
    report['pagination_and_delete']={'prefix':prefix,'page1':page1,'page2':page2,'frozen_preview':preview,'new_after_preview':extra['id'],'first_delete':deleted,'final_delete':result};save()
    report.update(outcome='RESTART_CHECK_PENDING',finished=time.time());save()
    print('P10 HTTP lifecycle tests passed; restart verification pending',flush=True)

if __name__=='__main__':main()
