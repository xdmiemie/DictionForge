"""Publication/I/O contracts with byte fixtures; real Torch validation is separate."""
import copy
import json
from pathlib import Path
import uuid

import pytest

from dictionforge import exports, models, references
from dictionforge.audio import atomic_json, digest
from dictionforge.jobs import Jobs, Conflict
from dictionforge.records import get, put, list_records


@pytest.fixture
def context(tmp_path, monkeypatch):
    jobs = Jobs(tmp_path/'data')
    area = jobs.root/'runs'/'parent'
    area.mkdir(parents=True)
    paths = {}
    for name in ('gpt', 'sovits'):
        path = area/(name+'.weight')
        path.write_bytes((name+' fixture only').encode())
        paths[name] = str(path)
    version_path = jobs.root/'datasets'/'role'/'version'
    version_path.mkdir(parents=True)
    (version_path/'clip.wav').write_bytes(b'audio fixture only')
    version = dict(id='version', dataset_id='role', role='角色', path=str(version_path), clips=[], parent_id=None)
    put(jobs, 'version', 'version', version)
    request = dict(run_id='parent', dataset_id='role', role='角色', version=version,
                   config={'initial_model': 'official'}, upstream=str(tmp_path/'upstream'))
    state = {'stages': {'s2': {'state': 'RUNNING', 'optimizer_updates': 3}, 's1': {'state': 'PENDING'}}}
    monkeypatch.setattr(exports, 'dependencies', lambda _: [])
    def validate(_jobs, _job, _command, **kwargs):
        assert kwargs['env']['CUDA_VISIBLE_DEVICES'] == '-1'
        return dict(structure='PASS', gpu='NOT_RUN', test_fixture=True)
    monkeypatch.setattr(exports, 'invoke', validate)
    return jobs, request, state, paths


def publish(context, reason='export', request_id=None):
    jobs, request, state, paths = context
    job = exports.enqueue_snapshot(jobs, request, state, paths, reason, request_id or uuid.uuid4().hex, 'point')
    assert jobs.claim('cpu')['id'] == job['id']
    result = exports.process(jobs, job)
    jobs.status(job['id'], 'COMPLETED', result=result)
    return job, result


def test_export_publication_is_complete_copied_and_idempotent(context):
    jobs, request, state, paths = context
    job, result = publish(context, request_id='first')
    repeated = exports.enqueue_snapshot(jobs, request, state, paths, 'export', 'first', 'point')
    assert repeated['id'] == job['id']
    model = result['model']
    assert model['validation']['gpu'] == 'NOT_RUN' and model['status'] == 'READY'
    assert model['stage_progress']['s1']['state'] == 'PENDING'
    assert model['provenance']['checkpoint_id'] == 'point'
    for name, item in model['weights'].items():
        assert Path(item['path']).read_bytes() == Path(paths[name]).read_bytes()
        assert Path(item['path']).resolve() != Path(paths[name]).resolve()
    retried = exports.retry(jobs, job['id'], 'retry-first')
    again = exports.process(jobs, retried)
    assert again['model_version_id'] == result['model_version_id']
    assert len(list_records(jobs, 'model')) == 1


def test_failed_validation_never_registers_half_package_and_retry_uses_same_version(context, monkeypatch):
    jobs, request, state, paths = context
    job = exports.enqueue_snapshot(jobs, request, state, paths, 'stop_export', 'failure', 'point')
    with monkeypatch.context() as patch:
        patch.setattr(exports, 'invoke', lambda *a, **k: dict(structure='FAILED', error='fixture rejected'))
        failed = exports.process(jobs, job)
    assert failed['outcome'] == 'FAILED' and not list_records(jobs, 'model')
    retry = exports.retry(jobs, job['id'], 'retry-failure')
    assert retry['payload']['snapshot_id'] == job['payload']['snapshot_id']
    assert retry['payload']['model_version_id'] == job['payload']['model_version_id']
    result = exports.process(jobs, retry)
    assert result['model_version_id'] == failed['model_version_id']


def test_frozen_source_change_is_rejected_before_publication(context):
    jobs, request, state, paths = context
    job = exports.enqueue_snapshot(jobs, request, state, paths, 'completed', 'changed')
    Path(paths['gpt']).write_bytes(b'changed original')
    with pytest.raises(Conflict, match='MODEL_FILE_CHANGED'):
        exports.process(jobs, job)
    assert not list_records(jobs, 'model')


def test_import_owns_a_copy_and_rejects_untrusted_or_single_weight(context):
    jobs, request, state, paths = context
    with pytest.raises(ValueError, match='可信'):
        models.import_submit(jobs, paths['gpt'], paths['sovits'], '外部', trusted=False, request_id='untrusted')
    with pytest.raises(ValueError, match='MODEL_FILE_MISSING'):
        models.import_submit(jobs, paths['gpt'], paths['sovits']+'.missing', '外部', request_id='missing')
    job = models.import_submit(jobs, paths['gpt'], paths['sovits'], '外部', request_id='import')
    snapshot = get(jobs, 'export_snapshot', job['payload']['snapshot_id'])
    frozen = Path(snapshot['weights']['gpt']['path']).read_bytes()
    Path(paths['gpt']).write_bytes(b'externally edited')
    assert Path(snapshot['weights']['gpt']['path']).read_bytes() == frozen
    result = exports.process(jobs, job)
    assert Path(result['model']['weights']['gpt']['path']).read_bytes() == frozen


def test_delete_rejects_active_job_and_late_reference(context):
    jobs, request, state, paths = context
    _, result = publish(context)
    key = result['model_version_id']
    preview = models.delete_preview(jobs, key)
    assert preview['deletable'] and len(preview['files']) == 4
    probe = jobs.submit('model_probe', {'model_version_id': key}, 'probe')
    assert jobs.claim('gpu')['id'] == probe['id']
    assert not models.delete_preview(jobs, key)['deletable']
    with pytest.raises(Conflict, match='DELETE_REFERENCED'):
        models.delete(jobs, key, preview['token'])
    assert get(jobs, 'model', key)['status'] == 'READY'
    jobs.status(probe['id'], 'COMPLETED')
    preview = models.delete_preview(jobs, key)
    result = models.delete(jobs, key, preview['token'])
    assert result['state'] == 'DELETED'
    assert models.delete(jobs, key, preview['token'])['state'] == 'DELETED'
    assert not list_records(jobs, 'model')
    assert all(Path(path).is_file() for path in paths.values())


def test_deleted_model_cannot_be_resurrected_by_export_retry(context):
    jobs, request, state, paths = context
    job, result = publish(context)
    preview = models.delete_preview(jobs, result['model_version_id'])
    models.delete(jobs, result['model_version_id'], preview['token'])
    with pytest.raises(Conflict, match='MODEL_DELETED'):
        exports.retry(jobs, job['id'], 'resurrect')
    with pytest.raises(Conflict, match='MODEL_DELETED'):
        exports.process(jobs, job)


def test_dataset_is_protected_by_child_model_checkpoint_and_reference_media(context):
    jobs, request, state, paths = context
    put(jobs, 'version', 'child', dict(id='child', parent_id='version'))
    assert any(row['kind'] == 'version' for row in references.delete_preview(jobs, 'version', 'version')['references'])
    with jobs.connect(write=True) as db:
        db.execute("DELETE FROM records WHERE kind='version' AND id='child'")
    point = jobs.root/'runs'/'recoverable'/'checkpoints'
    point.mkdir(parents=True)
    atomic_json(point/'index.json', {'checkpoints': [{'id': 'point'}]})
    put(jobs, 'run', 'recoverable', dict(version={'id': 'version'}))
    assert any(row['kind'] == 'run' for row in references.delete_preview(jobs, 'version', 'version')['references'])
    with jobs.connect(write=True) as db:
        db.execute("DELETE FROM records WHERE kind='run' AND id='recoverable'")
    put(jobs, 'media', 'audio', dict(owner='version'))
    jobs.submit('model_probe', {'model_version_id': 'official', 'reference_audio_id': 'audio'}, 'ref-probe')
    assert any(row['kind'] == 'job' for row in references.delete_preview(jobs, 'version', 'version')['references'])


def test_delete_preview_changed_bytes_rejected_and_source_remains(context):
    jobs, request, state, paths = context
    preview = references.delete_preview(jobs, 'version', 'version')
    path = Path(request['version']['path'])/'clip.wav'
    path.write_bytes(b'new revision')
    with pytest.raises(Conflict, match='DELETE_PREVIEW_CHANGED'):
        references.delete(jobs, 'version', 'version', preview['token'])
    assert path.read_bytes() == b'new revision'


def test_delete_crash_before_registry_commit_restores_staged_directory(context):
    jobs, request, state, paths = context
    preview = references.delete_preview(jobs, 'version', 'version')
    source = Path(request['version']['path'])
    trash = jobs.root/'trash'/preview['token']/'0'
    trash.parent.mkdir(parents=True)
    operation = dict(kind='version', id='version', token=preview['token'], state='PREPARED',
                     moves=[dict(source=str(source), trash=str(trash))])
    put(jobs, 'deletion', preview['token'], operation)
    source.replace(trash)  # Simulated process loss before the registry transaction committed.
    references.reconcile_deletions(jobs)
    assert source.is_dir() and not trash.exists()
    assert get(jobs, 'version', 'version')['id'] == 'version'


def test_export_reference_protects_periodic_checkpoint(context):
    from dictionforge.checkpoints import is_referenced
    jobs, request, state, paths = context
    exports.enqueue_snapshot(jobs, request, state, paths, 'export', 'reference', 'periodic-point')
    assert is_referenced(jobs, 'parent', 'periodic-point')


def test_retry_rechecks_retired_snapshot_inside_submit_transaction(context, monkeypatch):
    jobs, request, state, paths = context
    job, result = publish(context)
    original_submit = jobs.submit
    def concurrent_retire(*args, **kwargs):
        snapshot = get(jobs, 'export_snapshot', job['payload']['snapshot_id'])
        snapshot['retired'] = True
        put(jobs, 'export_snapshot', snapshot['snapshot_id'], snapshot)
        return original_submit(*args, **kwargs)
    monkeypatch.setattr(jobs, 'submit', concurrent_retire)
    with pytest.raises(Conflict, match='MODEL_DELETED'):
        exports.retry(jobs, job['id'], 'race-retry')
    assert get(jobs, 'export_snapshot', job['payload']['snapshot_id'])['retired']
    assert all(item['request'] != 'race-retry' for item in jobs.list())


@pytest.mark.parametrize('damage', [False, True])
def test_publication_crash_recovers_reference_registration_or_rejects_missing_reference(context, monkeypatch, damage):
    jobs, request, state, paths = context
    path = Path(request['version']['path'])/'clip.wav'
    request['version']['clips'] = [dict(path=str(path), seconds=4., text='测试参考', language='zh', sha256=digest(path))]
    job = exports.enqueue_snapshot(jobs, request, state, paths, 'completed', 'crash-ref')
    with monkeypatch.context() as patch:
        patch.setattr(exports, '_register', lambda *a: (_ for _ in ()).throw(RuntimeError('fixture crash before registry')))
        with pytest.raises(RuntimeError, match='crash'):
            exports.process(jobs, job)
    assert not list_records(jobs, 'model')
    final = jobs.root/'models'/'role'/job['payload']['model_version_id']
    assert (final/'reference.wav').is_file()
    if damage:
        (final/'reference.wav').unlink()
        with pytest.raises(Conflict, match='MODEL_REFERENCE_CHANGED'):
            exports.process(jobs, job)
        assert not list_records(jobs, 'model')
    else:
        result = exports.process(jobs, job)
        reference = result['model']['default_reference']
        assert get(jobs, 'media', reference['audio_id'])['owner'] == result['model_version_id']
        assert get(jobs, 'model', result['model_version_id'])['default_reference']['audio_id'] == reference['audio_id']


@pytest.mark.parametrize('published', [None, 'previous-child'])
def test_edit_draft_keeps_parent_audio_referenced(context, published):
    jobs, request, state, paths = context
    put(jobs, 'draft', 'correction', dict(parent_version='version', published=published,
        clips=[dict(path=str(Path(request['version']['path'])/'clip.wav'))]))
    preview = references.delete_preview(jobs, 'version', 'version')
    assert not preview['deletable']
    assert any(item['kind'] == 'draft' and item['id'] == 'correction' for item in preview['references'])
