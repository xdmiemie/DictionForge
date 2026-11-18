import uuid
from pathlib import Path

import pytest

from dictionforge import datasets, references
from dictionforge.jobs import Jobs, Conflict
from dictionforge.records import put, get, media


def fixture(tmp_path, *, owned=True):
    jobs = Jobs(tmp_path/'managed')
    key = uuid.uuid4().hex
    source = tmp_path/'original.wav'
    source.write_bytes(b'original must remain')
    area = jobs.root/('drafts' if owned else 'datasets')/(key if owned else 'parent-version')
    area.mkdir(parents=True)
    audio = area/'clip.wav'
    audio.write_bytes(b'prepared test fixture')
    clip = dict(id='clip', path=str(audio), audio_id=media(jobs,audio,owner=key if owned else 'parent'),
                text='识别文字', label_source='asr', language='zh', reviewed=False, excluded=False)
    draft = dict(id=key, revision=0, role='测试角色', clips=[clip])
    put(jobs,'draft',key,draft)
    return jobs, draft, audio, source


@pytest.mark.parametrize('owned',[True,False])
def test_draft_deletion_never_removes_original_or_shared_version(tmp_path,owned):
    jobs,draft,audio,source=fixture(tmp_path,owned=owned)
    preview=references.delete_preview(jobs,'draft',draft['id'])
    assert preview['deletable'] and len(preview['files']) == int(owned)
    result=references.delete(jobs,'draft',draft['id'],preview['token'])
    assert result['state']=='DELETED'
    assert source.read_bytes()==b'original must remain'
    assert audio.exists() is not owned
    with pytest.raises(KeyError):get(jobs,'draft',draft['id'])
    assert references.delete(jobs,'draft',draft['id'],preview['token'])['state']=='DELETED'


def test_changed_review_invalidates_metadata_only_deletion_preview(tmp_path):
    jobs,draft,audio,_=fixture(tmp_path,owned=False)
    preview=references.delete_preview(jobs,'draft',draft['id'])
    datasets.update_draft(jobs,draft['id'],0,[dict(id='clip',reviewed=True)])
    with pytest.raises(Conflict,match='DELETE_PREVIEW_CHANGED'):
        references.delete(jobs,'draft',draft['id'],preview['token'])
    assert audio.is_file() and get(jobs,'draft',draft['id'])['revision']==1


@pytest.mark.parametrize('protection',['job','preparing','augmentation','reference'])
def test_in_use_draft_cannot_be_deleted(tmp_path,protection):
    jobs,draft,audio,_=fixture(tmp_path)
    key=draft['id']
    if protection=='job':
        jobs.submit('asr',{'draft_id':key},str(uuid.uuid4()))
    elif protection=='preparing':
        job=jobs.submit('dataset_prepare',{},str(uuid.uuid4()))
        key=job['id']
        put(jobs,'draft',key,{**draft,'id':key})
    elif protection=='augmentation':
        put(jobs,'augmentation','append',dict(draft_id=key))
    else:
        references.acquire(jobs,'draft',key,'test-reader')
    preview=references.delete_preview(jobs,'draft',key)
    assert not preview['deletable'] and preview['references']
    with pytest.raises(Conflict,match='DELETE_REFERENCED'):
        references.delete(jobs,'draft',key,preview['token'])
    assert audio.is_file()


def test_default_approval_keeps_asr_provenance_but_correction_is_manual(tmp_path):
    jobs,draft,_,_=fixture(tmp_path)
    value=datasets.update_draft(jobs,draft['id'],0,[dict(id='clip',text='识别文字',reviewed=True)])
    assert value['clips'][0]['label_source']=='asr'
    value=datasets.update_draft(jobs,draft['id'],1,[dict(id='clip',text='修正后的文字',reviewed=True)])
    assert value['clips'][0]['label_source']=='manual'
