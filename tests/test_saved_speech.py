"""Permanent copies and deletion contracts using generated PCM, never GPU claims."""
import json
from pathlib import Path
import uuid

import numpy as np
import pytest
import soundfile as sf

from dictionforge import saved_speech as saved, media_lifecycle as lifecycle
from dictionforge.audio import digest
from dictionforge.errors import Cancelled
from dictionforge.jobs import Jobs, Conflict
from dictionforge.records import get, put, media, list_records
from dictionforge.supervisor import exclusive


@pytest.fixture
def source(tmp_path):
    jobs = Jobs(tmp_path/'data')
    tts = jobs.submit('tts', {}, uuid.uuid4().hex)
    jobs.claim()
    area = jobs.root/'runtime/tts'/tts['id']
    area.mkdir(parents=True)
    path = area/'full.wav'
    sf.write(path, .2*np.sin(np.arange(36000)*2*np.pi*330/48000), 48000, subtype='PCM_16')
    audio = dict(path=str(path), sha256=digest(path), bytes=path.stat().st_size, sample_rate=48000,
                 channels=1, samples=36000, seconds=.75, audio_id=media(jobs,path,owner=tts['id']))
    request = dict(job_id=tts['id'], output_dir=str(area), model_id='model-a', model={'role':'测试角色'})
    result = dict(outcome='COMPLETED', audio=audio, text='原文测试。', parameters={'seed':42},
                  reference={'sha256':'reference historical hash'}, provenance={'model_version_id':'model-a'})
    put(jobs,'tts',tts['id'],request)
    put(jobs,'tts_result',tts['id'],result)
    put(jobs,'tts_current','current',{'job_id':tts['id']})
    jobs.status(tts['id'],'COMPLETED',result=result)
    return jobs, tts['id'], audio


def save(source, title='测试作品', format='wav'):
    jobs, tts_id, audio = source
    job = saved.submit(jobs,tts_id,format,title,uuid.uuid4().hex)
    assert jobs.claim('cpu')['id'] == job['id']
    result = saved.process(jobs,job)
    jobs.status(job['id'],result['outcome'],result=result)
    assert result['outcome']=='COMPLETED', result
    return job, result['saved_speech']


def test_wav_independent_restart_idempotency_and_source_lease(source):
    jobs, tts_id, audio = source
    job = saved.submit(jobs,tts_id,'wav','独立作品','request-save')
    frozen = get(jobs,'speech_save',job['id'])
    assert lifecycle.active_leases(jobs,media_id=audio['audio_id'])[0]['owner_job']==job['id']
    assert saved.submit(jobs,tts_id,'wav','独立作品','request-save')['id']==job['id']
    with pytest.raises(Conflict):
        saved.submit(jobs,tts_id,'mp3','独立作品','request-save')
    jobs.claim('cpu')
    result = saved.process(jobs,job)
    jobs.status(job['id'],'COMPLETED',result=result)
    row = result['saved_speech']
    assert row['id']==frozen['id'] and row['wav']['sha256']==audio['sha256']
    assert row['wav']['path']!=audio['path'] and row['audio']['audio_id']==row['wav']['audio_id']
    assert not lifecycle.active_leases(jobs,media_id=audio['audio_id'])
    Path(audio['path']).unlink()
    restarted = Jobs(jobs.root)
    assert saved.list_saved(restarted)['items']==[row]
    assert saved.process(restarted,job)==result
    assert sf.read(row['audio']['path'])[1]==48000
    assert get(jobs,'tts_current','current')['job_id']==tts_id


def test_real_mp3_320k_decodes_to_source_length_and_keeps_wav(source):
    jobs, tts_id, audio = source
    job,row = save(source,format='mp3')
    assert row['audio']['bitrate']==320000 and row['audio']['sample_rate']==48000
    assert row['audio']['samples']==audio['samples'] and row['audio']['codec']=='mp3'
    assert row['wav']['sha256']==audio['sha256'] and row['wav']['audio_id']!=row['audio']['audio_id']
    assert saved._mp3_info(Path(row['audio']['path']),audio['samples'])['decode_verified']


def test_save_failure_and_cancellation_keep_current_and_no_saved_record(source,monkeypatch):
    jobs,tts_id,audio=source
    job=saved.submit(jobs,tts_id,'mp3','失败','fail-save')
    jobs.claim('cpu')
    def fail(*args,**kwargs):
        raise OSError('encoder unavailable')
    monkeypatch.setattr(saved,'invoke',fail)
    failed=saved.process(jobs,job)
    assert failed['outcome']=='FAILED' and failed['phase']=='mp3_encoding'
    assert not saved.list_saved(jobs)['items'] and digest(Path(audio['path']))==audio['sha256']
    assert get(jobs,'tts_current','current')['job_id']==tts_id
    jobs.control(job['id'],'cancel','cancel-save')
    with pytest.raises(Cancelled):
        saved.process(jobs,job)
    assert not saved.list_saved(jobs)['items']


def test_interrupted_publication_recovers_same_id_from_manifest(source,monkeypatch):
    jobs,tts_id,audio=source
    job=saved.submit(jobs,tts_id,'wav','恢复','recover-save')
    jobs.claim('cpu')
    original=saved._register
    def fail(*args):
        raise OSError('simulated registry failure after directory publication')
    monkeypatch.setattr(saved,'_register',fail)
    assert saved.process(jobs,job)['outcome']=='FAILED'
    key=get(jobs,'speech_save',job['id'])['id']
    assert (jobs.root/'saved_speech'/key/'manifest.json').is_file()
    monkeypatch.setattr(saved,'_register',original)
    restarted=Jobs(jobs.root)
    result=saved.process(restarted,job)
    assert result['outcome']=='COMPLETED' and result['saved_speech']['id']==key
    assert len(saved.list_saved(jobs)['items'])==1


def test_filter_pagination_frozen_all_scope_and_lease_protection(source):
    jobs,tts_id,audio=source
    rows=[save(source,title='批量作品 '+str(i))[1] for i in range(12)]
    first=saved.list_saved(jobs,query='批量',page=1,page_size=10)
    second=saved.list_saved(jobs,model_id='model-a',query='批量',page=2)
    assert first['total']==12 and len(first['items'])==10 and len(second['items'])==2
    assert not saved.list_saved(jobs,model_id='other')['items']
    assert saved.list_saved(jobs,query='原文')['total']==12
    with pytest.raises(ValueError):
        saved.list_saved(jobs,page_size=11)
    preview=saved.delete_preview(jobs,all_filtered=True,query='批量')
    later=save(source,title='批量作品 later')[1]
    held=rows[0]
    lease=lifecycle.acquire_browser(jobs,held['audio']['audio_id'])
    result=saved.delete(jobs,preview['token'])
    assert result['counts']==dict(deleted=11,already_deleted=0,protected=1)
    assert {row['id'] for row in saved.list_saved(jobs)['items']}=={held['id'],later['id']}
    lifecycle.release(jobs,lease['token'])
    result=saved.delete(jobs,preview['token'])
    assert result['counts']==dict(deleted=1,already_deleted=11,protected=0)
    assert saved.list_saved(jobs)['items']==[later]


def test_delete_changed_files_expiry_and_path_escape_are_rejected(source):
    jobs,tts_id,audio=source
    _,row=save(source)
    preview=saved.delete_preview(jobs,ids=[row['id']])
    Path(row['audio']['path']).write_bytes(b'changed')
    result=saved.delete(jobs,preview['token'])
    assert result['results'][0]['status']=='protected'
    assert 'PREVIEW_CHANGED' in result['results'][0]['reason']
    preview['expires']=0
    put(jobs,'saved_delete_preview',preview['token'],preview)
    with pytest.raises(Conflict):
        saved.delete(jobs,preview['token'])
    row['path']=str(jobs.root.parent)
    put(jobs,'saved_speech',row['id'],row)
    with pytest.raises(ValueError,match='DIRECTORY'):
        saved.delete_preview(jobs,ids=[row['id']])
    assert digest(Path(audio['path']))==audio['sha256']


def test_windows_move_lock_keeps_record_and_retry_succeeds(source,monkeypatch):
    jobs,tts_id,audio=source
    _,row=save(source)
    preview=saved.delete_preview(jobs,ids=[row['id']])
    original=Path.replace
    def locked(path,target):
        if path==Path(row['path']):
            raise PermissionError('controlled Windows directory lock')
        return original(path,target)
    monkeypatch.setattr(Path,'replace',locked)
    assert saved.delete(jobs,preview['token'])['counts']['protected']==1
    assert get(jobs,'saved_speech',row['id'])['status']=='READY'
    assert Path(row['audio']['path']).is_file()
    monkeypatch.setattr(Path,'replace',original)
    assert saved.delete(jobs,preview['token'])['counts']['deleted']==1


def test_crash_restore_and_purge_pending_recovery(source,monkeypatch):
    jobs,tts_id,audio=source
    _,row=save(source)
    preview=saved.delete_preview(jobs,ids=[row['id']])
    original=saved.put
    class SimulatedCrash(BaseException):
        pass
    def crash(j,k,key,value,db=None):
        if k=='saved_speech' and value.get('status')=='DELETED':
            raise SimulatedCrash()
        return original(j,k,key,value,db)
    monkeypatch.setattr(saved,'put',crash)
    with pytest.raises(SimulatedCrash):
        saved.delete(jobs,preview['token'])
    assert not Path(row['path']).exists() and get(jobs,'saved_speech',row['id'])['status']=='READY'
    monkeypatch.setattr(saved,'put',original)
    assert saved.reconcile_deletions(Jobs(jobs.root))[0]['state']=='ROLLED_BACK'
    assert Path(row['audio']['path']).is_file()
    unlink=Path.unlink
    def locked(path,*args,**kwargs):
        if 'trash' in path.parts:
            raise PermissionError('controlled trash handle')
        return unlink(path,*args,**kwargs)
    monkeypatch.setattr(Path,'unlink',locked)
    assert saved.delete(jobs,preview['token'])['counts']['deleted']==1
    assert list_records(jobs,'saved_deletion')[0]['state']=='PURGE_PENDING'
    assert saved.reconcile_deletions(jobs)[0]['state']=='PURGE_PENDING'
    monkeypatch.setattr(Path,'unlink',unlink)
    assert saved.reconcile_deletions(jobs)[0]['state']=='DELETED'


def test_reconciler_does_not_touch_live_confirmation(source):
    jobs,_,_=source
    with exclusive(jobs.state/'saved-deletion.lock'):
        assert saved.reconcile_deletions(jobs)==[]


def test_equal_creation_times_use_descending_id_and_page_scope(source):
    jobs,_,_=source
    rows=[save(source,title='同一时刻')[1] for _ in range(3)]
    for row in rows:
        row['created']=100
        put(jobs,'saved_speech',row['id'],row)
    listed=saved.list_saved(jobs,page_size=2)
    assert [row['id'] for row in listed['items']]==sorted((row['id'] for row in rows),reverse=True)[:2]
    preview=saved.delete_preview(jobs,ids=[row['id'] for row in listed['items']])
    assert saved.delete(jobs,preview['token'])['counts']['deleted']==2
    assert saved.list_saved(jobs)['total']==1


def test_copy_failure_preserves_source_and_does_not_publish(source,monkeypatch):
    jobs,tts_id,audio=source
    job=saved.submit(jobs,tts_id,'wav','磁盘写入失败','disk-failure')
    jobs.claim('cpu')
    original=Path.open
    def fail(path,*args,**kwargs):
        if path.name=='source.wav.part':
            raise OSError(28,'controlled disk full')
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'open',fail)
    result=saved.process(jobs,job)
    assert result['outcome']=='FAILED' and result['phase']=='wav_copy'
    assert not saved.list_saved(jobs)['items']
    assert digest(Path(audio['path']))==audio['sha256']
    assert get(jobs,'tts_current','current')['job_id']==tts_id


def test_save_replay_after_deletion_never_resurrects_copy(source):
    jobs,_,_=source
    job,row=save(source)
    preview=saved.delete_preview(jobs,ids=[row['id']])
    saved.delete(jobs,preview['token'])
    replay=saved.process(jobs,job)
    assert replay['outcome']=='FAILED' and 'ALREADY_DELETED' in replay['error']
    assert not Path(row['path']).exists()
