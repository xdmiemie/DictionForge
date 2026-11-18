"""Disposable managed files only; cascade tests never touch application data."""
import json
from pathlib import Path
import uuid

import pytest
from fastapi.testclient import TestClient

from dictionforge.api import create_app
from dictionforge.jobs import Jobs, Conflict
from dictionforge.records import put, get, media
from dictionforge.settings import Settings
from dictionforge import references
from dictionforge.media_lifecycle import acquire_browser, acquire_http, release


def file(path, text='fixture'):
    path.parent.mkdir(parents=True, exist_ok=True);path.write_text(text, encoding='utf-8');return path


def model(jobs, key, **provenance):
    area=jobs.root/'models'/'role'/key;file(area/'weights.bin',key)
    return put(jobs,'model',key,dict(id=key,model_id='role',path=str(area),role=key,status='READY',provenance=provenance))


def version(jobs, key, parent=None):
    area=jobs.root/'datasets'/'role'/key;audio=file(area/'clip.wav',key)
    return put(jobs,'version',key,dict(id=key,dataset_id='role',path=str(area),role=key,parent_id=parent,
               clips=[dict(path=str(audio),audio_id=media(jobs,audio,owner=key))]))


def task(jobs, kind, payload, state='COMPLETED'):
    job=jobs.submit(kind,payload,str(uuid.uuid4()))
    with jobs.connect(write=True) as db:db.execute('UPDATE jobs SET state=? WHERE id=?',(state,job['id']))
    file(jobs.root/'runs'/job['id']/'worker.log')
    return job['id']


def preview(jobs, kind, key):return references.delete_preview(jobs,kind,key,cascade=True)
def remove(jobs, kind, key, view=None):return references.delete(jobs,kind,key,(view or preview(jobs,kind,key))['token'])


@pytest.fixture
def env(tmp_path):
    jobs=Jobs(tmp_path/'data');source=file(tmp_path/'original.wav','keep source')
    asset=file(jobs.root/'assets'/'base.bin','keep shared base')
    saved=file(jobs.root/'saved_speech'/'saved'/'source.wav','keep permanent work')
    saved_media=media(jobs,saved,owner='saved')
    put(jobs,'saved_speech','saved',dict(id='saved',model_id='parent',audio=dict(path=str(saved),audio_id=saved_media)))
    model(jobs,'parent');version(jobs,'upstream')
    return jobs,source,asset,saved,saved_media


def test_model_cascade_removes_dependents_and_keeps_upstream_sources_and_saved_audio(env):
    jobs,source,asset,saved,saved_media=env
    run=task(jobs,'train',dict(initial_model='parent',version_id='upstream'),'PAUSED')
    put(jobs,'run',run,dict(id=run,config=dict(initial_model='parent'),version=dict(id='upstream')))
    file(jobs.root/'runs'/run/'checkpoints'/'full.ckpt')
    model(jobs,'child',initial_model='parent',run_id=run,version_id='upstream')
    put(jobs,'export_snapshot','snapshot',dict(initial_model='parent',model_version_id='child',run_id=run))
    file(jobs.root/'export_snapshots'/'snapshot'/'manifest.json')
    version(jobs,'feedback','upstream')
    put(jobs,'augmentation','append',dict(source_model_id='parent',target_version_id='upstream',version_id='feedback',draft_id='append',jobs=[]))
    put(jobs,'draft','append',dict(id='append',clips=[]));file(jobs.root/'drafts'/'append'/'clip.wav')
    put(jobs,'model_selection','active',dict(model_id='parent'))
    audio=file(jobs.root/'runtime'/'tts'/'tts-one'/'source.wav');audio_id=media(jobs,audio,owner='tts-one')
    put(jobs,'tts','tts-one',dict(model_id='parent'));put(jobs,'tts_result','tts-one',dict(model_id='parent'))
    put(jobs,'tts_current','current',dict(job_id='tts-one'))
    lease=acquire_browser(jobs,audio_id)
    view=preview(jobs,'model','parent');nodes={(x['kind'],x['id']) for x in view['objects']}
    assert view['deletable'] and view['mode']=='cascade'
    assert {('model','parent'),('model','child'),('run',run),('job',run),('augmentation','append'),('draft','append'),('version','feedback'),('tts_current','current')} <= nodes
    assert ('version','upstream') not in nodes and all(x['kind']!='saved_speech' for x in view['objects'])
    result=remove(jobs,'model','parent',view);assert result['state']=='DELETED'
    assert run in result['deleted_jobs'] and audio_id in result['deleted_media_ids']
    assert not audio.exists() and not (jobs.root/'runs'/run).exists()
    assert get(jobs,'version','upstream') and source.read_text()=='keep source'
    assert asset.read_text()=='keep shared base' and saved.read_text()=='keep permanent work'
    assert get(jobs,'media',saved_media)['state']=='AVAILABLE'
    assert get(jobs,'media',audio_id)['state']=='DELETED'
    with pytest.raises(KeyError):get(jobs,'media_lease',lease['token'])
    assert remove(jobs,'model','parent',view)['state']=='DELETED'


def test_dataset_cascade_includes_models_children_features_and_borrowed_draft(env):
    jobs,source,_,saved,_=env
    model(jobs,'trained',version_id='upstream');version(jobs,'derived','upstream')
    original=get(jobs,'version','upstream')
    put(jobs,'draft','revision',dict(id='revision',parent_version='upstream',clips=original['clips']))
    put(jobs,'features','upstream',dict(version_id='upstream'));file(jobs.root/'features'/'upstream'/'bert.pt')
    view=preview(jobs,'version','upstream')
    assert {('model','trained'),('version','derived'),('draft','revision'),('features','upstream')} <= {(x['kind'],x['id']) for x in view['objects']}
    assert remove(jobs,'version','upstream',view)['state']=='DELETED'
    assert not Path(original['path']).exists() and source.exists() and saved.exists()
    assert get(jobs,'model','parent')


@pytest.mark.parametrize('state',['QUEUED','RUNNING'])
def test_active_job_blocks_cascade_without_moving_files(env,state):
    jobs,*_=env
    run=task(jobs,'train',dict(initial_model='parent'),state)
    view=preview(jobs,'model','parent');assert not view['deletable']
    assert any(x['id']==run for x in view['references'])
    with pytest.raises(Conflict,match='DELETE_BUSY'):remove(jobs,'model','parent',view)
    assert Path(get(jobs,'model','parent')['path']).is_dir() and jobs.get(run)['state']==state


@pytest.mark.parametrize('change',['new_child','changed_file','changed_metadata'])
def test_confirmation_rejects_scope_changes(env,change):
    jobs,*_=env;view=preview(jobs,'model','parent')
    if change=='new_child':model(jobs,'new-child',initial_model='parent')
    elif change=='changed_file':file(Path(get(jobs,'model','parent')['path'])/'weights.bin','new bytes')
    else:
        value=get(jobs,'model','parent');value['role']='edited';put(jobs,'model','parent',value)
    with pytest.raises(Conflict,match='DELETE_PREVIEW_CHANGED'):remove(jobs,'model','parent',view)
    assert get(jobs,'model','parent')


def test_new_live_reference_after_preview_blocks_confirmation(env):
    jobs,*_=env;value=get(jobs,'model','parent')
    audio=file(Path(value['path'])/'reference.wav');key=media(jobs,audio,owner='parent')
    view=preview(jobs,'model','parent');lease=acquire_http(jobs,key)
    with pytest.raises(Conflict,match='DELETE_BUSY'):remove(jobs,'model','parent',view)
    release(jobs,lease['token']);assert remove(jobs,'model','parent',view)['state']=='DELETED'


def test_terminal_job_lease_on_retained_source_is_removed_with_owner(env):
    jobs,*_=env
    run=task(jobs,'train',dict(initial_model='parent'))
    source=get(jobs,'version','upstream')['clips'][0]['audio_id']
    put(jobs,'media_lease','old-lease',dict(token='old-lease',kind='job',owner_job=run,owner='upstream',media_id=source))
    assert remove(jobs,'model','parent')['state']=='DELETED'
    assert get(jobs,'media',source)['state']=='AVAILABLE'
    with pytest.raises(KeyError):get(jobs,'media_lease','old-lease')


def test_failure_during_moves_rolls_back_all_files_and_registry(env,monkeypatch):
    jobs,*_=env;model(jobs,'child',initial_model='parent');view=preview(jobs,'model','parent')
    original=Path.replace;calls=[]
    def fail_second(source,target):
        if 'trash' in Path(target).parts:
            calls.append(source)
            if len(calls)==2:raise OSError('injected move failure')
        return original(source,target)
    monkeypatch.setattr(Path,'replace',fail_second)
    with pytest.raises(OSError,match='injected'):remove(jobs,'model','parent',view)
    for key in ('parent','child'):assert Path(get(jobs,'model',key)['path']).is_dir()
    assert get(jobs,'deletion',view['token'])['state']=='ROLLED_BACK'


def test_restart_reconciles_uncommitted_cascade_moves(env):
    jobs,*_=env;view=preview(jobs,'model','parent');source=Path(view['bases'][0]);trash=jobs.root/'trash'/view['token']/'0'
    trash.parent.mkdir(parents=True);source.replace(trash)
    put(jobs,'deletion',view['token'],dict(kind='model',id='parent',token=view['token'],mode='cascade',state='PREPARED',moves=[dict(source=str(source),trash=str(trash))]))
    references.reconcile_deletions(jobs)
    assert source.is_dir() and get(jobs,'model','parent')


def test_duplicate_request_cannot_overwrite_in_progress_intent(env):
    jobs,*_=env;view=preview(jobs,'model','parent')
    intent=dict(kind='model',id='parent',token=view['token'],mode='cascade',state='PREPARED',moves=[])
    put(jobs,'deletion',view['token'],intent)
    with pytest.raises(Conflict,match='DELETE_IN_PROGRESS'):remove(jobs,'model','parent',view)
    assert get(jobs,'deletion',view['token'])==intent
    assert Path(get(jobs,'model','parent')['path']).is_dir()


def test_stale_inflight_request_preserves_completed_receipt(env):
    from dictionforge.cascade_deletion import delete
    jobs,*_=env;view=preview(jobs,'model','parent');result=remove(jobs,'model','parent',view)
    assert delete(jobs,view)==result
    assert get(jobs,'deletion',view['token'])['state']=='DELETED'


def test_routes_freeze_cascade_and_require_exact_root(env):
    jobs,*_=env;model(jobs,'child',initial_model='parent')
    app=create_app(Settings(jobs.root));app.state.jobs=jobs;client=TestClient(app)
    ordinary=client.get('/api/models/parent/delete-preview').json();assert not ordinary['deletable']
    view=client.get('/api/models/parent/delete-preview?cascade=true').json();assert view['deletable']
    assert client.request('DELETE','/api/models/child',json={'preview_id':view['token']}).status_code==409
    response=client.request('DELETE','/api/models/parent',json={'preview_id':view['token']})
    assert response.status_code==200 and response.json()['state']=='DELETED'


def test_managed_identity_cannot_escape_to_original(env):
    jobs,source,*_=env;value=get(jobs,'model','parent');value['path']=str(source.parent);put(jobs,'model','parent',value)
    with pytest.raises(ValueError,match='IDENTITY_MISMATCH'):preview(jobs,'model','parent')
    assert source.exists()
