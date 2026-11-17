"""Registered immutable GPT/SoVITS v4 pairs and explicit shared dependencies."""
import json
from pathlib import Path
import time
import uuid

from .audio import digest
from .jobs import Conflict
from .records import get, list_records
from .settings import ROOT, Settings


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


def official(jobs, verify=True):
    required = dependencies(jobs, verify)
    def weight(suffix):
        return next(item for item in required if item['path'].replace('\\', '/').endswith(suffix))
    return dict(id='official', version_id='official', model_id='official', role='官方 GPT-SoVITS v4',
        status='READY', created=0, weights={'gpt': weight('/s1v3.ckpt'), 'sovits': weight('/s2Gv4.pth')},
        dependencies=required, stage_progress={'s1': {'state': 'OFFICIAL'}, 's2': {'state': 'OFFICIAL'}},
        provenance={'reason': 'official', 'initial_model': 'official'}, default_reference=None,
        validation={'structure': 'PASS', 'gpu': 'NOT_RUN', 'basis': 'pinned_official_assets'},
        inference_defaults={'seed': 42, 'sample_rate': 48000, 'super_sampling': False})


def list_models(jobs):
    rows = [item for item in list_records(jobs, 'model') if item.get('status') == 'READY']
    rows.sort(key=lambda item: (item.get('created', 0), item['id']), reverse=True)
    rows.append(official(jobs, verify=False))
    return rows


def resolve(jobs, model_version_id='official'):
    if model_version_id == 'official':
        return official(jobs)
    result = get(jobs, 'model', model_version_id)
    if result.get('status') != 'READY' or result.get('validation', {}).get('structure') != 'PASS':
        raise ValueError('MODEL_NOT_READY')
    for item in [*result['weights'].values(), *result.get('dependencies', [])]:
        verify_artifact(item)
    reference = result.get('default_reference')
    if reference and (not Path(reference['path']).is_file() or digest(Path(reference['path'])) != reference['sha256']):
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
