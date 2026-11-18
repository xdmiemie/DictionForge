"""Frozen dependent-object deletion; sources, shared assets and saved works survive."""
import json
import os
from pathlib import Path
import re
import time
import uuid

from .audio import digest
from .jobs import ACTIVE, Conflict
from .records import get, put
from .references import _safe, _mentions, _purge
from .training_config import config_fingerprint

PRIMARY = {'model', 'version', 'draft'}
BUNDLES = {'run', 'training_result', 'preflight', 'model_probe', 'model_test', 'tts', 'tts_result', 'augmentation'}
LABELS = dict(model='模型版本', version='数据版本', draft='标注草稿', run='训练与恢复点', job='后台任务及日志',
              export_snapshot='导出快照', augmentation='回流记录', features='训练特征', preflight='预检记录',
              training_result='训练结果', tts='临时合成请求', tts_result='临时合成结果', tts_current='当前合成指向',
              model_probe='模型试听请求', model_test='模型试听结果', comparison='对比试听记录',
              model_selection='模型启用状态', model_defaults='默认参考设置')


def _bases(jobs, kind, key, value):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', key):
        raise ValueError('DELETE_MANAGED_IDENTITY_MISMATCH')
    if kind == 'model':
        if key == 'official':
            raise ValueError('共享官方底模不可删除')
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value['model_id']):
            raise ValueError('DELETE_MANAGED_IDENTITY_MISMATCH')
        expected = jobs.root/'models'/value['model_id']/key
    elif kind == 'version':
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value['dataset_id']):
            raise ValueError('DELETE_MANAGED_IDENTITY_MISMATCH')
        expected = jobs.root/'datasets'/value['dataset_id']/key
    else:
        expected = None
    if expected is not None and Path(value['path']).resolve() != expected.resolve():
        raise ValueError('DELETE_MANAGED_IDENTITY_MISMATCH')
    bases = [expected] if expected is not None else []
    if kind in {'version', 'features'}:
        bases.append(jobs.root/'features'/key)
    if kind == 'draft':
        bases.append(jobs.root/'drafts'/key)
    if kind in {'run', 'job', 'preflight', 'model_probe', 'model_test', 'augmentation'}:
        bases.append(jobs.root/'runs'/key)
    if kind in {'tts', 'tts_result'}:
        bases.append(jobs.root/'runtime'/'tts'/key)
    if kind == 'export_snapshot':
        bases.append(jobs.root/'export_snapshots'/key)
    # All paths come from known application layouts, never source/clip/weight paths.
    return [base for base in (_safe(jobs.root, path) for path in bases) if base.exists()]


def _inside(path, bases):
    return bool(path) and any(Path(path).absolute().is_relative_to(base) for base in bases)


def plan(jobs, kind, key, db):
    if kind not in PRIMARY or key == 'official':
        raise ValueError('该对象不可删除')
    records = {(r['kind'], r['id']): json.loads(r['metadata']) for r in db.execute('SELECT * FROM records')}
    all_jobs = {r['id']: jobs.decode(r) for r in db.execute('SELECT * FROM jobs')}
    if (kind, key) not in records:
        raise KeyError(key)
    selected = {(kind, key)}
    reasons = {(kind, key): '本次选择的删除对象'}

    def add(category, identifier, reason):
        node = (category, identifier)
        if (category == 'job' and identifier in all_jobs or node in records) and node not in selected:
            selected.add(node); reasons[node] = reason

    while True:
        before = set(selected)
        ids = {k for c, k in selected if c in PRIMARY | BUNDLES | {'job', 'export_snapshot'} and k != 'official'}
        by_kind = {c: {k for cat, k in selected if cat == c} for c in LABELS}
        bases = [base for c, k in selected for base in _bases(jobs, c, k, all_jobs[k] if c == 'job' else records[c, k])]
        media_ids = {k for (c, k), value in records.items() if c == 'media' and
                     (_inside(value.get('path'), bases) or value.get('owner') in ids)}

        for (category, identifier), value in records.items():
            if (category, identifier) in selected:
                continue
            uses = False
            if category == 'model':
                provenance = value.get('provenance') or {}
                uses = (provenance.get('initial_model') in by_kind['model'] or provenance.get('version_id') in by_kind['version'] or
                        provenance.get('run_id') in by_kind['run'] or _inside((value.get('default_reference') or {}).get('path'), bases) or
                        (value.get('default_reference') or {}).get('audio_id') in media_ids)
            elif category == 'version':
                uses = value.get('parent_id') in by_kind['version']
            elif category == 'draft':
                uses = (value.get('parent_version') in by_kind['version'] or value.get('published') in by_kind['version'] or
                        any(_inside(c.get('path'), bases) or c.get('audio_id') in media_ids for c in value.get('clips', [])))
            elif category == 'run':
                config = value.get('config', {})
                uses = (config.get('initial_model') in by_kind['model'] or value.get('version', {}).get('id') in by_kind['version'] or
                        value.get('parent_run') in by_kind['run'] or value.get('resume_source_run') in by_kind['run'] or
                        any(_mentions(value.get('resume_checkpoint'), i) for i in by_kind['run']))
            elif category == 'export_snapshot':
                uses = (value.get('initial_model') in by_kind['model'] or value.get('model_version_id') in by_kind['model'] or
                        value.get('version_id') in by_kind['version'] or value.get('run_id') in by_kind['run'])
            elif category == 'augmentation':
                uses = (value.get('source_model_id') in by_kind['model'] or identifier in by_kind['draft'] or
                        value.get('draft_id') in by_kind['draft'] or value.get('tts_id') in by_kind['tts'] or
                        any(value.get(field) in by_kind['version'] for field in ('source_version_id','target_version_id','version_id','parent_version_id')))
            elif category in {'tts', 'tts_result', 'model_probe', 'model_test'}:
                uses = (value.get('model_id') in by_kind['model'] or value.get('model_version_id') in by_kind['model'] or
                        (value.get('provenance') or {}).get('model_version_id') in by_kind['model'] or
                        _inside((value.get('reference') or {}).get('path'), bases) or (value.get('reference') or {}).get('audio_id') in media_ids)
            elif category == 'preflight':
                config = value.get('config', {})
                uses = config.get('initial_model') in by_kind['model'] or config.get('version_id') in by_kind['version']
            elif category == 'features':
                uses = identifier in by_kind['version']
            elif category == 'model_selection':
                uses = value.get('model_id') in by_kind['model'] | by_kind['model_defaults']
            elif category == 'model_defaults':
                uses = _inside((value.get('default_reference') or {}).get('path'), bases)
            elif category == 'comparison':
                uses = value.get('model_id') in by_kind['model'] or any(i in by_kind['job'] for i in value.get('job_ids', []))
            elif category == 'tts_current':
                uses = value.get('job_id') in by_kind['tts']
            if uses:
                add(category, identifier, '依赖本次删除范围中的对象或文件')

        # Owned results travel with their task. Independent saved speech never does.
        for category, identifier in list(selected):
            if category in BUNDLES | {'job'}:
                add('job', identifier, '对应的后台任务与日志')
                for bundle in BUNDLES:
                    add(bundle, identifier, '对应任务的运行记录')
            if category == 'augmentation':
                row = records[category, identifier]
                add('draft', row.get('draft_id'), '回流审核草稿')
                add('version', row.get('version_id'), '该回流发布的数据版本')
                for job_id in row.get('jobs', []): add('job', job_id, '回流准备或发布任务')
            elif category == 'export_snapshot':
                add('model', records[category, identifier].get('model_version_id'), '此快照发布的模型版本')
            elif category == 'comparison':
                for job_id in records[category, identifier].get('job_ids', []): add('job', job_id, '对比试听任务')
        for job_id, job in all_jobs.items():
            if job['kind'] == 'speech_save' and job['state'] not in ACTIVE:
                continue
            if job_id in ids or any(_mentions(job['payload'], i) for i in ids | media_ids):
                add('job', job_id, '任务使用了本次删除的对象')
        if selected == before:
            break

    bases = sorted({base for c, k in selected for base in _bases(jobs, c, k, all_jobs[k] if c == 'job' else records[c, k])}, key=lambda p: (len(p.parts), str(p)))
    bases = [base for i, base in enumerate(bases) if not any(base.is_relative_to(parent) for parent in bases[:i])]
    selected_ids = {k for c, k in selected if c in PRIMARY | BUNDLES | {'job', 'export_snapshot'} and k != 'official'}
    affected_media = {k for (c, k), value in records.items() if c == 'media' and
                      (_inside(value.get('path'), bases) or value.get('owner') in selected_ids)}
    blockers = [dict(kind='job', id=k, reason='任务正在运行或排队，请结束或安全暂停后重新预览')
                for c, k in selected if c == 'job' and all_jobs[k]['state'] in ACTIVE]
    from .media_lifecycle import active_leases
    leases = active_leases(jobs, db=db)
    browser_leases = []
    for lease in leases:
        if lease.get('media_id') in affected_media or lease.get('owner') in selected_ids:
            if lease['kind'] == 'browser': browser_leases.append(lease['token'])
            else: blockers.append(dict(kind='media_lease', id=lease['token'], reason='音频正在下载或被任务读写，请完成后重试'))
    for (c, k), value in records.items():
        if c == 'reference' and (value.get('id') in selected_ids or value.get('id') in affected_media):
            blockers.append(dict(kind=c, id=k, reason='正在复制素材或建立任务引用，请稍后重试'))
    deleted_jobs = {k for c, k in selected if c == 'job'}
    release_leases = [k for (c,k), value in records.items() if c == 'media_lease' and
                      (value.get('media_id') in affected_media or value.get('owner') in selected_ids or value.get('owner_job') in deleted_jobs)]
    objects = [dict(kind=c, id=k, label=LABELS[c], title=(all_jobs[k] if c == 'job' else records[c,k]).get('role', ''), reason=reasons[c,k]) for c,k in sorted(selected)]
    frozen = [dict(kind=c,id=k,metadata=all_jobs[k] if c=='job' else records[c,k]) for c,k in sorted(selected)]
    files = []
    for base in bases:
        for folder, dirs, names in os.walk(base, followlinks=False):
            for name in [*dirs, *names]: _safe(jobs.root, Path(folder)/name)
            for name in names:
                path = Path(folder)/name
                files.append(dict(path=str(path), bytes=path.stat().st_size, sha256=digest(path)))
    files.sort(key=lambda item: item['path'])
    return dict(objects=objects, files=files, bases=[str(p) for p in bases], references=blockers,
                deletable=not blockers, total_bytes=sum(f['bytes'] for f in files),
                fingerprint=config_fingerprint(dict(records=frozen, files=files, bases=[str(p) for p in bases])),
                affected_media=sorted(affected_media), browser_leases=browser_leases, release_leases=release_leases)


def preview(jobs, kind, key):
    # WAL snapshot: hashing large model/checkpoint files must not hold the writer lock.
    with jobs.connect() as db:
        db.execute('BEGIN')
        result=plan(jobs,kind,key,db)
    token=uuid.uuid4().hex
    result.update(mode='cascade',kind=kind,id=key,preview_id=token,token=token,expires=time.time()+600,state='PREVIEW')
    return put(jobs,'delete_preview',token,result)


def delete(jobs, preview):
    token=preview['token']; trash_root=_safe(jobs.root,jobs.root/'trash'/token)
    trash_root.mkdir(parents=True,exist_ok=True)
    operation=dict(kind=preview['kind'],id=preview['id'],token=token,mode='cascade',state='PREPARED',created=time.time(),
                   moves=[dict(source=base,trash=str(trash_root/str(i))) for i,base in enumerate(preview['bases'])])
    # A second request may have read the preview before the first committed.
    # Claim the durable intent atomically instead of overwriting its receipt.
    with jobs.connect(write=True) as db:
        try:
            previous = get(jobs, 'deletion', token, db)
        except KeyError:
            previous = None
        if previous and previous['state'] in {'DELETED', 'PURGE_PENDING'}:
            return previous
        if previous and previous['state'] == 'PREPARED':
            raise Conflict('DELETE_IN_PROGRESS：此删除请求正在处理，请稍后查看结果')
        put(jobs, 'deletion', token, operation, db)
    moved=[]
    try:
        with jobs.connect(write=True) as db:
            current=plan(jobs,preview['kind'],preview['id'],db)
            if current['references']:
                raise Conflict('DELETE_BUSY: '+json.dumps(current['references'],ensure_ascii=False))
            if current['fingerprint']!=preview['fingerprint']:
                raise Conflict('DELETE_PREVIEW_CHANGED：关联对象或文件已变化，请重新查看删除范围')
            for move in operation['moves']:
                source=_safe(jobs.root,Path(move['source'])); target=_safe(jobs.root/'trash',Path(move['trash']))
                source.replace(target);moved.append((source,target))
            for obj in current['objects']:
                kind,key=obj['kind'],obj['id']
                if kind=='job':
                    db.execute('DELETE FROM control_receipts WHERE request IN (SELECT request FROM controls WHERE job_id=?)',(key,))
                    db.execute('DELETE FROM controls WHERE job_id=?',(key,));db.execute('DELETE FROM events WHERE job_id=?',(key,))
                    db.execute('DELETE FROM jobs WHERE id=?',(key,))
                else: db.execute('DELETE FROM records WHERE kind=? AND id=?',(kind,key))
            for key in current['affected_media']:
                item=get(jobs,'media',key,db);item['state']='DELETED';put(jobs,'media',key,item,db)
            for key in current['release_leases']:db.execute("DELETE FROM records WHERE kind='media_lease' AND id=?",(key,))
            for obj in current['objects']:
                if obj['kind']=='tts':
                    try:
                        retirement=get(jobs,'media_retirement',obj['id'],db)
                    except KeyError:continue
                    retirement.update(state='DELETED',updated=time.time())
                    put(jobs,'media_retirement',obj['id'],retirement,db)
            operation.update(state='PURGE_PENDING',deleted_objects=current['objects'],deleted_media_ids=current['affected_media'],
                             deleted_jobs=[x['id'] for x in current['objects'] if x['kind']=='job'])
            put(jobs,'deletion',token,operation,db)
    except BaseException:
        for source,target in reversed(moved):
            if target.exists() and not source.exists():target.replace(source)
        operation['state']='ROLLED_BACK';put(jobs,'deletion',token,operation);raise
    return _purge(jobs,operation)
