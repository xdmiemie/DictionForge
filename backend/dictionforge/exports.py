"""Immutable export intent, CPU validation, atomic publication and idempotent retry."""
import copy
import json
import os
from pathlib import Path
import re
import shutil
import time
import uuid

from .audio import atomic_json, digest
from .engine import invoke
from .jobs import Conflict
from .models import artifact, dependencies, verify_artifact
from .records import get, put, media
from .settings import ROOT, Settings
from .training_config import config_fingerprint


def _ids(request_id):
    return uuid.uuid5(uuid.NAMESPACE_URL, 'dictionforge:snapshot:'+request_id).hex, uuid.uuid5(uuid.NAMESPACE_URL, 'dictionforge:model:'+request_id).hex


def _submit_snapshot(jobs, snapshot, request_id):
    payload = {name: snapshot[name] for name in ('snapshot_id', 'model_version_id', 'run_id', 'reason')}
    def accepted(db, job_id):
        try:
            current = get(jobs, 'export_snapshot', snapshot['snapshot_id'], db)
        except KeyError:
            current = None
        if current and current.get('retired'):
            raise Conflict('MODEL_DELETED: 导出快照已退役')
        area = jobs.root/'export_snapshots'/snapshot['snapshot_id']
        area.mkdir(parents=True, exist_ok=True)
        frozen = dict(snapshot, first_job_id=job_id)
        path = area/'manifest.json'
        if path.exists():
            previous = json.loads(path.read_text(encoding='utf-8'))
            if previous['fingerprint'] != snapshot['fingerprint']:
                raise Conflict('SNAPSHOT_CONFLICT')
            frozen = current or previous
        else:
            atomic_json(path, frozen)
        put(jobs, 'export_snapshot', snapshot['snapshot_id'], frozen, db)
    with jobs.connect() as db:
        old = db.execute('SELECT * FROM jobs WHERE request=?', (request_id,)).fetchone()
        if old:
            decoded = jobs.decode(old)
            if decoded['kind'] != 'model_export' or decoded['payload'] != payload:
                raise Conflict('导出请求 ID 已绑定其他快照')
            frozen = get(jobs, 'export_snapshot', snapshot['snapshot_id'], db)
            if frozen['fingerprint'] != snapshot['fingerprint']:
                raise Conflict('导出请求 ID 的冻结权重发生变化')
            return decoded
    return jobs.submit('model_export', payload, request_id, on_accept=accepted)


def enqueue_snapshot(jobs, request, stage_state, weights, reason, request_id, checkpoint_id=None):
    if reason not in {'export', 'stop_export', 'completed'} or set(weights) != {'gpt', 'sovits'}:
        raise ValueError('导出原因或权重对不完整')
    snapshot_id, model_version_id = _ids(request_id)
    with jobs.connect() as db:
        old = db.execute('SELECT * FROM jobs WHERE request=?', (request_id,)).fetchone()
        if old:
            job = jobs.decode(old)
            frozen = get(jobs, 'export_snapshot', snapshot_id, db)
            if job['kind'] != 'model_export' or frozen['run_id'] != request['run_id'] or frozen['reason'] != reason or frozen.get('checkpoint_id') != checkpoint_id:
                raise Conflict('导出请求 ID 已绑定其他快照')
            if any(Path(weights[name]).resolve() != Path(frozen['weights'][name]['path']).resolve() for name in weights):
                raise Conflict('导出请求 ID 的权重来源不同')
            return job
    frozen_weights = {name: artifact(path) for name, path in weights.items()}
    progress = copy.deepcopy(stage_state.get('stages', stage_state))
    for name, stage in [('gpt', 's1'), ('sovits', 's2')]:
        initial = request.get('initial_weights', {}).get(name)
        is_initial = bool(initial and Path(initial).resolve() == Path(weights[name]).resolve())
        if not initial:
            is_initial = progress.get(stage, {}).get('state') in {'PENDING', 'SKIPPED', 'OFFICIAL'}
        frozen_weights[name].update(stage=stage, source='INITIAL' if is_initial else 'TRAINED',
                                   initial_model=request['config'].get('initial_model', 'official') if is_initial else None)
        progress.setdefault(stage, {'state': 'PENDING'})['weight_source'] = frozen_weights[name]['source']
        progress[stage]['weight_sha256'] = frozen_weights[name]['sha256']
    reference = next((dict(clip) for clip in request.get('version', {}).get('clips', [])
                      if 3 <= clip.get('seconds', 0) <= 10 and clip.get('text', '').strip()), None)
    snapshot = dict(schema_version=1, snapshot_id=snapshot_id, model_version_id=model_version_id,
        run_id=request['run_id'], checkpoint_id=checkpoint_id, reason=reason, weights=frozen_weights,
        model_id=request['dataset_id'], role=request['role'], version_id=request.get('version', {}).get('id'),
        initial_model=request['config'].get('initial_model', 'official'), config=copy.deepcopy(request['config']),
        stage_progress=progress, default_reference=reference, upstream=request['upstream'], created=time.time(),
        source='training', source_locks={'upstream': digest(ROOT/'configs/upstream.lock.json'),
                                       'assets': digest(ROOT/'configs/assets.lock.json')})
    snapshot['fingerprint'] = config_fingerprint({key: value for key, value in snapshot.items() if key != 'created'})
    return _submit_snapshot(jobs, snapshot, request_id)


def enqueue_import(jobs, gpt_path, sovits_path, role, request_id):
    snapshot_id, model_version_id = _ids(request_id)
    # Import source files are copied: later external edits cannot change the retry snapshot.
    with jobs.connect() as db:
        old = db.execute('SELECT * FROM jobs WHERE request=?', (request_id,)).fetchone()
        if old:
            job = jobs.decode(old)
            frozen = get(jobs, 'export_snapshot', snapshot_id, db)
            incoming = {'gpt': gpt_path, 'sovits': sovits_path}
            if job['kind'] != 'model_export' or frozen['role'] != role or any(
                    Path(incoming[name]).resolve() != Path(frozen['import_sources'][name]['path']).resolve() for name in incoming):
                raise Conflict('导入请求 ID 已绑定其他输入')
            return job
    original = {name: artifact(path) for name, path in {'gpt': gpt_path, 'sovits': sovits_path}.items()}
    area = jobs.root/'export_snapshots'/snapshot_id/'input'
    area.mkdir(parents=True, exist_ok=True)
    weights = {}
    if shutil.disk_usage(area).free < sum(item['bytes'] for item in original.values()) * 2 + 2 * 1024**3:
        raise OSError('INSUFFICIENT_DISK: 导入需快照、发布包及 2 GiB 余量')
    for name, item in original.items():
        target = area/('gpt.ckpt' if name == 'gpt' else 'sovits.pth')
        if target.exists():
            if digest(target) != item['sha256']:
                raise Conflict('IMPORT_SNAPSHOT_CHANGED')
        else:
            _copy_checked(item, target)
        weights[name] = dict(item, path=str(target.resolve()))
    snapshot = dict(schema_version=1, snapshot_id=snapshot_id, model_version_id=model_version_id, run_id=None,
        checkpoint_id=None, reason='import', weights=weights, model_id='external-'+snapshot_id[:16], role=role,
        version_id=None, initial_model=None, config={}, stage_progress={'s1': {'state': 'IMPORTED'}, 's2': {'state': 'IMPORTED'}},
        default_reference=None, upstream=str(Settings.from_environment().upstream), created=time.time(), source='import',
        import_sources=original, source_locks={'upstream': digest(ROOT/'configs/upstream.lock.json'),
                                             'assets': digest(ROOT/'configs/assets.lock.json')})
    snapshot['fingerprint'] = config_fingerprint({key: value for key, value in snapshot.items() if key != 'created'})
    return _submit_snapshot(jobs, snapshot, request_id)


def _copy_checked(item, target):
    source = verify_artifact(item)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name('.'+target.name+'.'+uuid.uuid4().hex+'.part')
    try:
        with source.open('rb') as inp, temporary.open('xb') as out:
            shutil.copyfileobj(inp, out, 1024*1024)
            out.flush()
            os.fsync(out.fileno())
        if temporary.stat().st_size != item['bytes'] or digest(temporary) != item['sha256']:
            raise Conflict('SNAPSHOT_CHANGED_DURING_COPY')
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def retry(jobs, key, request_id):
    try:
        snapshot = get(jobs, 'export_snapshot', key)
    except KeyError:
        job = jobs.get(key)
        if job['kind'] != 'model_export':
            raise ValueError('不是导出任务')
        snapshot = get(jobs, 'export_snapshot', job['payload']['snapshot_id'])
    if snapshot.get('retired'):
        raise Conflict('MODEL_DELETED: 该模型版本已删除，不能通过旧导出重新发布')
    return _submit_snapshot(jobs, snapshot, request_id)


def retry_intent(jobs, run_id, intent_id):
    if not re.fullmatch('[a-f0-9]{64}', intent_id):
        raise ValueError('EXPORT_INTENT_ID_INVALID')
    run = get(jobs, 'run', run_id)
    path = jobs.root/'runs'/run_id/'export_intents'/(intent_id+'.json')
    intent = json.loads(path.read_text(encoding='utf-8'))
    if intent['run_id'] != run_id:
        raise Conflict('EXPORT_INTENT_RUN_MISMATCH')
    for name, value in intent['weights'].items():
        if digest(Path(value)) != intent['weight_hashes'][name]:
            raise Conflict('EXPORT_INTENT_WEIGHT_CHANGED')
    job = enqueue_snapshot(jobs, run, intent['stage_state'], intent['weights'], intent['reason'],
                           intent['request_id'], intent.get('checkpoint_id'))
    intent.update(state='QUEUED', status='QUEUED', job_id=job['id'], error=None)
    atomic_json(path, intent)
    return job


def _register(jobs, job, model, snapshot):
    reference = model.get('default_reference')
    if reference:
        path = Path(reference['path'])
        expected = snapshot.get('default_reference')
        if (path.resolve() != (Path(model['path'])/'reference.wav').resolve() or not expected or
                any(reference.get(key) != expected.get(key) for key in ('sha256', 'text', 'language')) or
                not path.is_file() or digest(path) != reference['sha256']):
            raise Conflict('MODEL_REFERENCE_CHANGED')
    with jobs.connect(write=True) as db:
        current = get(jobs, 'export_snapshot', snapshot['snapshot_id'], db)
        if current.get('retired'):
            raise Conflict('MODEL_DELETED: 导出快照已退役')
        if reference:
            registered = None
            if reference.get('audio_id'):
                try:
                    registered = get(jobs, 'media', reference['audio_id'], db)
                except KeyError:
                    pass
            if not registered or registered.get('state') != 'AVAILABLE':
                reference['audio_id'] = media(jobs, reference['path'], owner=model['id'], db=db)
            atomic_json(Path(model['path'])/'manifest.json', model)
        put(jobs, 'model', model['id'], model, db)
        jobs.emit(db, job['id'], 'model_published', {'model_version_id': model['id'], 'snapshot_id': snapshot['snapshot_id']})


def process(jobs, job):
    snapshot = get(jobs, 'export_snapshot', job['payload']['snapshot_id'])
    if snapshot.get('retired'):
        raise Conflict('MODEL_DELETED: 导出快照已退役')
    model_id = snapshot['model_version_id']
    try:
        existing = get(jobs, 'model', model_id)
    except KeyError:
        existing = None
    if existing is not None:
        from .models import resolve
        return dict(status='PUBLISHED', snapshot_id=snapshot['snapshot_id'], model_version_id=model_id, model=resolve(jobs, model_id))
    final = jobs.root/'models'/snapshot['model_id']/model_id
    staging = final.with_name('.'+model_id+'.part')
    staging.mkdir(parents=True, exist_ok=True)
    jobs.progress(job['id'], {'stage': 'WRITING', 'snapshot_id': snapshot['snapshot_id']})
    if final.exists():
        # A previous process may have committed files just before registry insertion.
        staged = json.loads((final/'manifest.json').read_text(encoding='utf-8'))
        if staged['provenance']['snapshot_id'] != snapshot['snapshot_id'] or staged.get('validation', {}).get('structure') != 'PASS':
            raise Conflict('MODEL_PUBLICATION_CONFLICT')
        for name, item in staged['weights'].items():
            if item['sha256'] != snapshot['weights'][name]['sha256'] or item['bytes'] != snapshot['weights'][name]['bytes']:
                raise Conflict('MODEL_PUBLICATION_CHANGED')
            verify_artifact(item)
        for item in staged.get('dependencies', []):
            verify_artifact(item)
        _register(jobs, job, staged, snapshot)
        return dict(status='PUBLISHED', snapshot_id=snapshot['snapshot_id'], model_version_id=model_id, model=staged)
    deps = dependencies(jobs)
    if shutil.disk_usage(staging).free < sum(item['bytes'] for item in snapshot['weights'].values()) + 2*1024**3:
        raise OSError('INSUFFICIENT_DISK: 导出包需完整副本及 2 GiB 余量')
    copied = {}
    for name, item in snapshot['weights'].items():
        target = staging/('gpt.ckpt' if name == 'gpt' else 'sovits.pth')
        if not target.exists() or digest(target) != item['sha256']:
            _copy_checked(item, target)
        copied[name] = dict(item, path=str(target.resolve()))
    request = dict(weights=copied, dependencies=deps, upstream=snapshot['upstream'], source_locks=snapshot['source_locks'])
    area = jobs.root/'runs'/job['id']
    area.mkdir(parents=True, exist_ok=True)
    request_path, result_path = area/'model-request.json', area/'model-validation.json'
    atomic_json(request_path, request)
    jobs.progress(job['id'], {'stage': 'VALIDATING', 'snapshot_id': snapshot['snapshot_id']})
    validation = invoke(jobs, job, [Settings.from_environment().worker_python, '-m', 'dictionforge.model_worker', request_path, result_path],
                        env={'CUDA_VISIBLE_DEVICES': '-1'}, result=result_path)
    if validation.get('structure') != 'PASS':
        return dict(outcome='FAILED', error=validation.get('error', 'MODEL_STRUCTURE_INVALID'), snapshot_id=snapshot['snapshot_id'], model_version_id=model_id)
    reference = snapshot.get('default_reference')
    if reference:
        ref_item = artifact(reference['path'])
        if ref_item['sha256'] != reference['sha256']:
            raise Conflict('REFERENCE_CHANGED')
        _copy_checked(ref_item, staging/'reference.wav')
        reference = {key: reference[key] for key in ('text', 'language', 'seconds', 'sha256')}
        reference['path'] = str(final/'reference.wav')
    weights = {name: dict(item, path=str(final/Path(item['path']).name)) for name, item in copied.items()}
    model = dict(schema_version=1, id=model_id, version_id=model_id, model_id=snapshot['model_id'], role=snapshot['role'],
        status='READY', created=snapshot['created'], path=str(final), weights=weights, dependencies=deps,
        stage_progress=snapshot['stage_progress'], default_reference=reference,
        provenance={key: snapshot[key] for key in ('run_id', 'version_id', 'initial_model', 'reason', 'snapshot_id', 'checkpoint_id')},
        validation=validation, inference_defaults={'seed': 42, 'sample_rate': 48000, 'super_sampling': False})
    atomic_json(staging/'training_summary.json', {key: snapshot[key] for key in ('run_id', 'version_id', 'config', 'stage_progress', 'reason', 'initial_model')})
    atomic_json(staging/'manifest.json', model)
    staging.replace(final)
    _register(jobs, job, model, snapshot)
    return dict(status='PUBLISHED', snapshot_id=snapshot['snapshot_id'], model_version_id=model_id, model=model)
