"""Publication and failure contracts. Feature/ASR fixtures are explicitly CPU fakes."""
import copy
from pathlib import Path
import uuid
import numpy as np
import pytest
import soundfile as sf
from dictionforge import augmentation as app, datasets
from dictionforge.audio import digest, atomic_json
from dictionforge.jobs import Jobs, Conflict
from dictionforge.records import get, put, media, list_records
from dictionforge.media_lifecycle import active_leases
from dictionforge.errors import Cancelled


@pytest.fixture
def context(tmp_path,monkeypatch):
    jobs=Jobs(tmp_path/'data')
    config=dict(min_seconds=1.0,max_seconds=12.0,silence_db=-40.0,silence_seconds=.4)
    area=jobs.root/'datasets'/'role'/'parent';area.mkdir(parents=True)
    clips=[]
    for i in range(2):
        path=area/f'parent{i}.wav';sf.write(path,.2*np.sin(np.arange(96000)*2*np.pi*(110+i*30)/48000),48000,subtype='PCM_16')
        clips.append(dict(id=f'parent{i}',path=str(path),sha256=digest(path),audio_id=media(jobs,path,owner='parent'),
            text='父版本标注',split='train' if i==0 else 'validation',seconds=2,group=str(i),source='recording',
            reviewed=True,excluded=False,language='zh',sample_rate=48000))
    parent=dict(id='parent',dataset_id='role',role='角色',parent_id=None,created=0,clips=clips,config=config,
        path=str(area),list_path=str(area/'train.list'),seconds=4,fingerprint=datasets.fingerprint(clips),warnings=[])
    put(jobs,'version','parent',parent);atomic_json(area/'manifest.json',parent)
    weight=jobs.root/'model.pth';weight.write_bytes(b'byte fixture, not model weights')
    weights={name:dict(path=str(weight),bytes=weight.stat().st_size,sha256=digest(weight)) for name in ['gpt','sovits']}
    put(jobs,'model','model',dict(id='model',status='READY',weights=weights))
    job=jobs.submit('tts',{},uuid.uuid4().hex);jobs.claim()
    output=jobs.root/'runtime'/'tts'/job['id'];output.mkdir(parents=True)
    sentences=[]
    for i in range(2):
        path=output/f'sentence-{uuid.uuid4().hex}-{i}.wav'
        t=np.arange(192000)/48000
        sf.write(path,.2*np.sin(2*np.pi*((330+i*90)*t+15*t*t)),48000,subtype='PCM_16')
        audio=dict(path=str(path),sha256=digest(path),audio_id=media(jobs,path,owner=job['id']))
        sentences.append(dict(sentence_id=f's{i}',text=f'只追加第{i}句。',index=i,audio=audio))
    put(jobs,'tts',job['id'],dict(model_id='model',weight_hashes={k:v['sha256'] for k,v in weights.items()},
        model={'provenance':{'version_id':'parent'}}))
    put(jobs,'tts_result',job['id'],dict(outcome='COMPLETED',sentences=sentences))
    jobs.status(job['id'],'COMPLETED')
    monkeypatch.setattr(app.features,'_run',fake_features)
    return jobs,job['id'],parent,sentences


def fake_features(jobs,job,action,version):
    area=jobs.root/'features'/version['id'];area.mkdir(parents=True)
    index=area/'index';index.write_text('CPU feature fixture')
    result=dict(ready=True,version_fingerprint=version['fingerprint'],indexes={'index':digest(index)},
        entries={c['id']:{'files':{}} for c in version['clips'] if c['split']=='train'})
    atomic_json(area/'manifest.json',result)
    return result


def submit(context,sentence='s0',config=None,request=None):
    jobs,tts,*_=context
    return app.submit(jobs,tts,sentence,'parent',request or uuid.uuid4().hex,config)


def run(jobs,job):
    assert jobs.claim()['id']==job['id']
    try:
        result=app.process(jobs,job)
    except Exception as exc:
        jobs.status(job['id'],'CANCELLED' if isinstance(exc,Cancelled) else 'FAILED',error=str(exc));raise
    jobs.status(job['id'],'COMPLETED',result=result)
    return app.snapshot(jobs,job['payload'].get('augmentation_id',job['id']))


def test_whole_no_asr_independent_source_holdout_and_replay(context,monkeypatch):
    jobs,tts,parent,sentences=context
    monkeypatch.setattr(datasets,'transcribe',lambda *_:pytest.fail('whole must not invoke ASR'))
    before=copy.deepcopy(parent);job=submit(context,request='same')
    assert active_leases(jobs,media_id=sentences[0]['audio']['audio_id'])
    assert submit(context,request='same')['id']==job['id']
    with pytest.raises(Conflict):submit(context,'s1',request='same')
    row=run(jobs,job);assert row['state']=='PUBLISHED' and row['asr_calls']==0 and row['added_count']==1
    version=get(jobs,'version',row['version_id']);assert len(version['clips'])==3
    assert version['clips'][-1]['text']=='只追加第0句。' and version['clips'][-1]['split']=='train'
    assert [(c['id'],c['sha256'],c['split']) for c in version['clips'][:2]]==[(c['id'],c['sha256'],c['split']) for c in before['clips']]
    assert get(jobs,'version','parent')==before
    assert not active_leases(jobs,media_id=sentences[0]['audio']['audio_id'])
    Path(sentences[0]['audio']['path']).unlink()
    assert Path(version['clips'][-1]['path']).is_file()
    assert submit(context,request='same')['id']==job['id']


def test_repeat_source_different_request_is_content_duplicate(context):
    jobs,*_=context
    first=run(jobs,submit(context));second=run(jobs,submit(context))
    assert second['state']=='DUPLICATE' and second['version_id']==first['version_id']
    assert len(list_records(jobs,'version'))==2


def test_split_asr_review_and_only_special_feature_publish(context,monkeypatch):
    jobs,*_=context;calls=[]
    def asr(jobs,job,draft):
        calls.extend(c['id'] for c in draft['clips'])
        for i,c in enumerate(draft['clips']):c.update(text=f'逐段识别{i}',label_source='asr')
        put(jobs,'draft',draft['id'],draft)
    monkeypatch.setattr(datasets,'transcribe',asr)
    row=run(jobs,submit(context,config={'max_seconds':2,'min_seconds':1}))
    assert row['state']=='REVIEW_REQUIRED' and len(calls)>1
    assert len(list_records(jobs,'version'))==1
    with pytest.raises(Conflict,match='APPEND_PUBLISH_REQUIRED'):datasets.publish(jobs,row['id'],0)
    draft=datasets.update_draft(jobs,row['id'],0,[dict(id=c['id'],reviewed=True) for c in row['draft']['clips']])
    job=app.publish_submit(jobs,row['id'],draft['revision'],'publish')
    published=run(jobs,job)
    assert published['state']=='PUBLISHED' and published['added_count']==len(calls)
    version=get(jobs,'version',published['version_id'])
    assert all(c['text'].startswith('逐段识别') for c in version['clips'][2:])


def test_feature_failure_preserves_parent_and_retry_uses_independent_draft(context,monkeypatch):
    jobs,tts,parent,sentences=context
    monkeypatch.setattr(app.features,'_run',lambda *_:dict(ready=False,errors=['controlled failure']))
    job=submit(context)
    with pytest.raises(ValueError,match='APPEND_FEATURES_FAILED'):run(jobs,job)
    row=app.snapshot(jobs,job['id']);assert row['state']=='FAILED'
    assert list_records(jobs,'version')==[parent]
    Path(sentences[0]['audio']['path']).unlink()
    monkeypatch.setattr(app.features,'_run',fake_features)
    retry=app.publish_submit(jobs,row['id'],row['draft']['revision'],'retry')
    assert run(jobs,retry)['state']=='PUBLISHED'


def test_concurrent_publication_rebases_without_overwriting(context,monkeypatch):
    jobs,*_=context;first=submit(context,'s0');second=submit(context,'s1');entered=False
    def concurrent(jobs,job,action,version):
        nonlocal entered
        if job['id']==first['id'] and not entered:
            entered=True;run(jobs,second)
        return fake_features(jobs,job,action,version)
    monkeypatch.setattr(app.features,'_run',concurrent)
    row=run(jobs,first)
    assert row['rebase_attempt']==1
    version=get(jobs,'version',row['version_id'])
    assert {c['text'] for c in version['clips'][2:]}=={'只追加第0句。','只追加第1句。'}
    assert len(list_records(jobs,'version'))==3  # stale candidate never registered


def test_changed_draft_during_features_never_publishes(context,monkeypatch):
    jobs,*_=context
    def changed(jobs,job,action,version):
        draft=get(jobs,'draft',job['id']);datasets.update_draft(jobs,draft['id'],draft['revision'],[{'id':draft['clips'][0]['id'],'text':'changed'}])
        return fake_features(jobs,job,action,version)
    monkeypatch.setattr(app.features,'_run',changed)
    with pytest.raises(Conflict,match='VERSION_CONFLICT'):run(jobs,submit(context))
    assert len(list_records(jobs,'version'))==1


def test_asr_failure_leaves_editable_independent_draft(context,monkeypatch):
    jobs,*_=context
    def fail(*_):raise RuntimeError('controlled ASR outage')
    monkeypatch.setattr(datasets,'transcribe',fail)
    row=run(jobs,submit(context,config={'max_seconds':2}))
    assert row['state']=='REVIEW_REQUIRED' and 'ASR' in row['error'] and row['draft']['clips']
    assert len(list_records(jobs,'version'))==1


def test_cancel_at_publication_boundary_keeps_parent(context,monkeypatch):
    jobs,*_=context
    def cancel(jobs,job,action,version):
        jobs.control(job['id'],'cancel','cancel-request')
        return fake_features(jobs,job,action,version)
    monkeypatch.setattr(app.features,'_run',cancel)
    with pytest.raises(Cancelled):run(jobs,submit(context))
    assert len(list_records(jobs,'version'))==1


def test_changed_candidate_audio_after_features_never_publishes(context,monkeypatch):
    jobs,*_=context
    def changed(jobs,job,action,version):
        result=fake_features(jobs,job,action,version)
        Path(version['clips'][-1]['path']).write_bytes(b'changed after feature extraction')
        return result
    monkeypatch.setattr(app.features,'_run',changed)
    with pytest.raises(Conflict,match='APPEND_CLIP_CHANGED'):run(jobs,submit(context))
    assert len(list_records(jobs,'version'))==1
