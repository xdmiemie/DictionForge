"""Registered immutable GPT/SoVITS v4 pairs and explicit shared dependencies."""
import json
from pathlib import Path
import time
import uuid

from .audio import digest
from .jobs import Conflict
from .records import get, list_records
from .settings import ROOT, Settings
from .training_config import config_fingerprint


def reference_fingerprint(reference):
    if not reference:
        return None
    return config_fingerprint({key: reference[key] for key in ('sha256', 'text', 'language')})


def _presentation(jobs, model, db=None):
    """Small metadata reads only; full asset hashing belongs to resolve/enable."""
    if db is None:
        with jobs.connect() as connection:
            return _presentation(jobs, model, connection)
    model = dict(model, validation=dict(model.get('validation', {})))
    if model['id'] == 'official':
        try:
            defaults = get(jobs, 'model_defaults', 'official', db)
            model['default_reference'] = defaults.get('default_reference')
        except KeyError:
            pass
    fingerprint = reference_fingerprint(model.get('default_reference'))
    model['reference_fingerprint'] = fingerprint
    history = [dict(json.loads(row['metadata']), id=row['id'])
               for row in db.execute("SELECT id,metadata FROM records WHERE kind='model_test'")]
    history = sorted((item for item in history if item.get('model_version_id') == model['id']),
                     key=lambda item: item.get('created', 0), reverse=True)
    if history:
        model['latest_gpu_test_id'] = history[0].get('job_id') or history[0].get('id')
    matching = []
    for test in history:
        test_fingerprint = test.get('reference_fingerprint')
        if not test_fingerprint and test.get('request', {}).get('reference'):
            test_fingerprint = reference_fingerprint(test['request']['reference'])
        if not test_fingerprint:
            try:
                test_fingerprint = reference_fingerprint(get(jobs, 'model_probe', test['id'], db)['reference'])
            except KeyError:
                pass
        test['reference_fingerprint'] = test_fingerprint
        if fingerprint is None or test_fingerprint == fingerprint:
            matching.append(test)
    if fingerprint is not None:
        model['validation'].update(gpu='NOT_RUN', reference_fingerprint=fingerprint)
        model['validation'].pop('gpu_test_id', None)
    if matching:
        test = matching[0]
        model['validation'].update(gpu='FAILED' if test.get('outcome') == 'FAILED' else 'PASS',
                                   reference_fingerprint=test.get('reference_fingerprint'))
        if test.get('job_id') or test.get('id'):
            model['validation']['gpu_test_id'] = test.get('job_id') or test.get('id')
    return model


def artifact(path):
    path = Path(path).resolve()
    if not path.is_file() or path.suffix.lower() in {'.part', '.tmp'}:
        raise ValueError(f'MODEL_FILE_MISSING: {path}')
    return dict(path=str(path), sha256=digest(path), bytes=path.stat().st_size)


def verify_artifact(item):
    path = Path(item['path'])
    if not path.is_file() or path.stat().st_size != item['bytes'] or digest(path) != item['sha256']:
        raise Conflict(f'MODEL_FILE_CHANGED: {path}')
    return path


def dependencies(jobs, verify=True):
    lock = json.loads((ROOT/'configs/assets.lock.json').read_text(encoding='utf-8'))
    result = []
    selected = []
    for asset in lock['assets']:
        if asset.get('group') == 'v4':
            selected.append(asset)
        elif asset.get('group') == 'text':
            selected.extend(dict(member, id=member['path'], revision=asset['revision']) for member in asset.get('members', []))
    for asset in selected:
        path = (jobs.root/'assets'/asset['path']).resolve()
        item = dict(id=asset['id'], path=str(path), sha256=asset['sha256'], bytes=asset['size'],
                    upstream_path=asset.get('upstream_path'), revision=asset.get('revision'))
        if verify:
            verify_artifact(item)
        result.append(item)
    return result


def official(jobs, verify=True, db=None):
    required = dependencies(jobs, verify)
    def weight(suffix):
        return next(item for item in required if item['path'].replace('\\', '/').endswith(suffix))
    return _presentation(jobs, dict(id='official', version_id='official', model_id='official', role='官方 GPT-SoVITS v4',
        status='READY', created=0, weights={'gpt': weight('/s1v3.ckpt'), 'sovits': weight('/s2Gv4.pth')},
        dependencies=required, stage_progress={'s1': {'state': 'OFFICIAL'}, 's2': {'state': 'OFFICIAL'}},
        provenance={'reason': 'official', 'initial_model': 'official'}, default_reference=None,
        validation={'structure': 'PASS', 'gpu': 'NOT_RUN', 'basis': 'pinned_official_assets'},
        inference_defaults={'seed': 42, 'sample_rate': 48000, 'super_sampling': False}), db)


def list_models(jobs):
    rows = [_presentation(jobs, item) for item in list_records(jobs, 'model') if item.get('status') == 'READY']
    rows.sort(key=lambda item: (item.get('created', 0), item['id']), reverse=True)
    rows.append(official(jobs, verify=False))
    return rows


def describe(jobs, model_version_id, db=None):
    return official(jobs, verify=False, db=db) if model_version_id == 'official' else _presentation(jobs, get(jobs, 'model', model_version_id, db), db)


def resolve(jobs, model_version_id='official', *, check_reference=True):
    if model_version_id == 'official':
        return official(jobs)
    result = describe(jobs, model_version_id)
    if result.get('status') != 'READY' or result.get('validation', {}).get('structure') != 'PASS':
        raise ValueError('MODEL_NOT_READY')
    for item in [*result['weights'].values(), *result.get('dependencies', [])]:
        verify_artifact(item)
    reference = result.get('default_reference')
    if check_reference and reference and (not Path(reference['path']).is_file() or digest(Path(reference['path'])) != reference['sha256']):
        raise Conflict('MODEL_REFERENCE_CHANGED')
    return result


def import_submit(jobs, gpt_path, sovits_path, role, trusted=True, request_id=None):
    if not trusted:
        raise ValueError('仅可导入用户确认来源可信的本地权重')
    if not request_id or not role.strip():
        raise ValueError('导入需要角色和 request_id')
    from .exports import enqueue_import
    return enqueue_import(jobs, gpt_path, sovits_path, role.strip(), request_id)


def delete_preview(jobs, model_version_id):
    from .references import delete_preview as preview
    return preview(jobs, 'model', model_version_id)


def delete(jobs, model_version_id, token):
    from .references import delete as remove
    return remove(jobs, 'model', model_version_id, token)
