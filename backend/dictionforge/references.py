"""Reference-aware deletion with exact preview, managed trash and crash recovery."""
import json
import os
from pathlib import Path
import re
import stat
import time
import uuid

from .audio import digest
from .jobs import Conflict
from .records import get, put, list_records
from .training_config import config_fingerprint


def _safe(root, path):
    root, path = Path(root).resolve(), Path(path).absolute()
    if not path.is_relative_to(root) or path == root or not path.resolve().is_relative_to(root):
        raise ValueError('DELETE_PATH_OUTSIDE_MANAGED_ROOT')
    current = path
    while current != root:
        if current.exists() or current.is_symlink():
            info = current.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                raise ValueError('DELETE_REPARSE_POINT')
        current = current.parent
    return path


def acquire(jobs, kind, key, owner):
    token = uuid.uuid4().hex
    with jobs.connect(write=True) as db:
        if kind in {'model', 'version', 'draft'} and key != 'official':
            get(jobs, kind, key, db)
        put(jobs, 'reference', token, dict(kind=kind, id=key, owner=owner, created=time.time()), db)
    return token


def release(jobs, token):
    with jobs.connect(write=True) as db:
        db.execute("DELETE FROM records WHERE kind='reference' AND id=?", (token,))


def _mentions(value, key):
    if isinstance(value, dict):
        return any(_mentions(item, key) for item in value.values())
    if isinstance(value, list):
        return any(_mentions(item, key) for item in value)
    return value == key


def references_for(jobs, kind, key):
    result = []
    def add(source_kind, source_id, reason):
        row = dict(kind=source_kind, id=source_id, reason=reason)
        if row not in result:
            result.append(row)
    from .media_lifecycle import active_leases
    for lease in active_leases(jobs, owner=key):
        add('media_lease', lease['token'], '该版本的音频正在播放、下载或被任务使用')
    with jobs.connect() as db:
        jobs_rows = [jobs.decode(row) for row in db.execute('SELECT * FROM jobs')]
        rows = [(row['kind'], row['id'], json.loads(row['metadata'])) for row in db.execute('SELECT * FROM records')]
    states = {job['id']: job['state'] for job in jobs_rows}
    media_ids = {row_id for category, row_id, row in rows if category == 'media' and row.get('owner') == key}
    for job in jobs_rows:
        if job['state'] in {'QUEUED', 'RUNNING'} and ((kind == 'draft' and job['id'] == key) or _mentions(job['payload'], key) or
                any(_mentions(job['payload'], media_id) for media_id in media_ids)):
            add('job', job['id'], '活跃任务冻结了该版本')
    for category, row_id, row in rows:
        if category == 'augmentation':
            if kind == 'draft' and (row_id == key or row.get('draft_id') == key):
                add('augmentation', row_id, '句子回流记录仍引用该审核草稿')
            if kind == 'model' and row.get('source_model_id') == key:
                add('augmentation', row_id, '句子回流记录与新微调默认来源依赖该模型')
            if kind == 'version' and key in {row.get('target_version_id'),row.get('version_id'),row.get('source_version_id')}:
                add('augmentation', row_id, '句子回流的父版本、来源谱系或发布结果')
        if category == 'reference' and row.get('kind') == kind and row.get('id') == key:
            add('reference', row_id, f"正在使用：{row.get('owner')}")
        elif category == 'reference' and row.get('kind') == 'media' and row.get('id') in media_ids:
            add('reference', row_id, '该版本的音频正在复制或处理中')
        elif category == 'model_selection' and kind == 'model' and row.get('model_id') == key:
            add('model_selection', row_id, '逻辑启用的模型，关闭或切换后可删除')
        elif category == 'tts' and states.get(row_id) in {'QUEUED', 'RUNNING'}:
            if kind == 'model' and row.get('model_id') == key:
                add('tts', row_id, '合成任务冻结了该模型版本')
            if kind == 'version':
                reference = row.get('reference', {})
                base = Path(get(jobs, 'version', key)['path']).resolve()
                if reference.get('audio_id') in media_ids or (reference.get('path') and Path(reference['path']).resolve().is_relative_to(base)):
                    add('tts', row_id, '合成任务冻结的参考仍使用该数据版本')
        elif category == 'tts_result' and kind == 'model' and row.get('outcome') == 'COMPLETED' and row.get('media_state') != 'DELETED':
            if row.get('provenance', {}).get('model_version_id') == key:
                add('tts_result', row_id, '保留的合成结果记录了该来源模型；结果回收前保留依赖')
        elif category == 'run':
            uses = row.get('config', {}).get('initial_model') == key if kind == 'model' else row.get('version', {}).get('id') == key
            if uses:
                index = jobs.root/'runs'/row_id/'checkpoints/index.json'
                has_points = index.is_file() and bool(json.loads(index.read_text(encoding='utf-8')).get('checkpoints'))
                if states.get(row_id) in {'QUEUED', 'RUNNING', 'PAUSED'} or has_points or row.get('resume_checkpoint'):
                    add('run', row_id, '活跃训练或完整恢复点依赖该版本')
        elif category == 'version' and kind == 'version' and row_id != key and row.get('parent_id') == key:
            add('version', row_id, '派生数据版本保留谱系依赖')
        elif category == 'draft' and kind == 'version':
            uses_parent = row.get('parent_version') == key and not row.get('published')
            base = Path(get(jobs, 'version', key)['path']).resolve()
            uses_files = any(clip.get('audio_id') in media_ids or
                             (clip.get('path') and Path(clip['path']).resolve().is_relative_to(base))
                             for clip in row.get('clips', []))
            if uses_parent or uses_files:
                add('draft', row_id, '校对草稿或只读草稿片段仍引用该数据版本')
        elif category == 'model' and row.get('status') == 'READY':
            if kind == 'draft' and (row.get('default_reference') or {}).get('path'):
                if Path(row['default_reference']['path']).resolve().is_relative_to((jobs.root/'drafts'/key).resolve()):
                    add('model', row_id, '默认参考仍使用该草稿音频')
            provenance = row.get('provenance', {})
            if kind == 'model' and row_id != key and provenance.get('initial_model') == key:
                add('model', row_id, '派生模型记录了该来源模型')
            if kind == 'version' and provenance.get('version_id') == key:
                add('model', row_id, '已发布模型使用该数据版本')
            if kind == 'version' and row.get('default_reference'):
                target = get(jobs, 'version', key)
                ref = Path(row['default_reference']['path']).resolve()
                if ref.is_relative_to(Path(target['path']).resolve()):
                    add('model', row_id, '默认参考仍使用该数据文件')
        elif category == 'export_snapshot' and not row.get('retired'):
            uses = row.get('initial_model') == key if kind == 'model' else row.get('version_id') == key
            if uses:
                add('export_snapshot', row_id, '不可变导出快照仍依赖该版本')
    return result


def _inventory(jobs, kind, key):
    if kind not in {'model', 'version', 'draft'} or not re.fullmatch('[A-Za-z0-9_-]{1,128}', key) or key == 'official':
        raise ValueError('该对象不可删除')
    value = get(jobs, kind, key)
    if kind == 'model':
        expected = jobs.root/'models'/value['model_id']/key
        bases = [expected]
    elif kind == 'version':
        expected = jobs.root/'datasets'/value['dataset_id']/key
        bases = [expected]
        if (jobs.root/'features'/key).exists():
            bases.append(jobs.root/'features'/key)
    else:
        # Revised drafts can reference immutable version files and own no folder.
        # Never derive the deletion scope from a clip's source/audio path.
        expected = _safe(jobs.root, jobs.root/'drafts'/key)
        bases = [expected] if expected.exists() else []
    if kind != 'draft' and Path(value['path']).resolve() != expected.resolve():
        raise ValueError('DELETE_MANAGED_IDENTITY_MISMATCH')
    files = []
    for base in bases:
        _safe(jobs.root, base)
        if not base.is_dir():
            raise ValueError(f'DELETE_DIRECTORY_MISSING: {base}')
        for folder, dirs, names in os.walk(base, followlinks=False):
            for name in [*dirs, *names]:
                _safe(jobs.root, Path(folder)/name)
            for name in names:
                path = Path(folder)/name
                files.append(dict(path=str(path), bytes=path.stat().st_size, sha256=digest(path)))
    files.sort(key=lambda item: item['path'])
    return value, bases, files


def delete_preview(jobs, kind, key):
    value, bases, files = _inventory(jobs, kind, key)
    refs = references_for(jobs, kind, key)
    token = uuid.uuid4().hex
    preview = dict(kind=kind, id=key, token=token, preview_id=token, files=files, references=refs,
                   deletable=not refs, total_bytes=sum(item['bytes'] for item in files), expires=time.time()+600,
                   bases=[str(base) for base in bases], fingerprint=config_fingerprint(files), state='PREVIEW')
    if kind == 'draft':
        preview['record_fingerprint'] = config_fingerprint(value)
    put(jobs, 'delete_preview', token, preview)
    return preview


def _purge(jobs, operation):
    errors = []
    for move in operation['moves']:
        trash = _safe(jobs.root/'trash', Path(move['trash']))
        if not trash.exists():
            continue
        for folder, dirs, names in os.walk(trash, topdown=False, followlinks=False):
            for name in names:
                path = _safe(jobs.root/'trash', Path(folder)/name)
                try:
                    path.unlink()
                except OSError as exc:
                    errors.append(dict(path=str(path), error=str(exc)))
            for name in dirs:
                path = _safe(jobs.root/'trash', Path(folder)/name)
                try:
                    path.rmdir()
                except OSError as exc:
                    errors.append(dict(path=str(path), error=str(exc)))
        try:
            trash.rmdir()
        except OSError as exc:
            errors.append(dict(path=str(trash), error=str(exc)))
    operation.update(state='PURGE_PENDING' if errors else 'DELETED', errors=errors)
    put(jobs, 'deletion', operation['token'], operation)
    return operation


def delete(jobs, kind, key, token):
    if not re.fullmatch('[a-f0-9]{32}', token):
        raise ValueError('DELETE_CONFIRMATION_INVALID')
    try:
        old = get(jobs, 'deletion', token)
    except KeyError:
        old = None
    if old and old['kind'] == kind and old['id'] == key and old['state'] in {'DELETED', 'PURGE_PENDING'}:
        return old if old['state'] == 'DELETED' else _purge(jobs, old)
    preview = get(jobs, 'delete_preview', token)
    if preview['kind'] != kind or preview['id'] != key or preview['expires'] < time.time():
        raise Conflict('DELETE_CONFIRMATION_EXPIRED_OR_MISMATCH')
    trash_root = jobs.root/'trash'/token
    trash_root.mkdir(parents=True, exist_ok=True)
    operation = dict(kind=kind, id=key, token=token, state='PREPARED', created=time.time(),
                     moves=[dict(source=base, trash=str(trash_root/str(i))) for i, base in enumerate(preview['bases'])])
    # Durable intent lets startup restore moves if the registry transaction never committed.
    put(jobs, 'deletion', token, operation)
    moved = []
    try:
        with jobs.connect(write=True) as db:
            value, bases, files = _inventory(jobs, kind, key)
            if config_fingerprint(files) != preview['fingerprint']:
                raise Conflict('DELETE_PREVIEW_CHANGED')
            if kind == 'draft' and config_fingerprint(value) != preview.get('record_fingerprint'):
                raise Conflict('DELETE_PREVIEW_CHANGED')
            refs = references_for(jobs, kind, key)
            if refs:
                raise Conflict('DELETE_REFERENCED: '+json.dumps(refs, ensure_ascii=False))
            for move in operation['moves']:
                source = _safe(jobs.root, Path(move['source']))
                target = _safe(jobs.root/'trash', Path(move['trash']))
                source.replace(target)
                moved.append((source, target))
            db.execute('DELETE FROM records WHERE kind=? AND id=?', (kind, key))
            for row in db.execute("SELECT id,metadata FROM records WHERE kind='media'").fetchall():
                item = json.loads(row['metadata'])
                if item.get('owner') == key:
                    item['state'] = 'DELETED'
                    put(jobs, 'media', row['id'], item, db)
            if kind == 'model':
                for row in db.execute("SELECT id,metadata FROM records WHERE kind='export_snapshot'").fetchall():
                    item = json.loads(row['metadata'])
                    if item['model_version_id'] == key:
                        item['retired'] = True
                        put(jobs, 'export_snapshot', row['id'], item, db)
            operation.update(state='PURGE_PENDING', metadata=value)
            put(jobs, 'deletion', token, operation, db)
    except BaseException:
        for source, target in reversed(moved):
            if target.exists() and not source.exists():
                target.replace(source)
        operation['state'] = 'ROLLED_BACK'
        put(jobs, 'deletion', token, operation)
        raise
    return _purge(jobs, operation)


def reconcile_deletions(jobs):
    results = []
    for operation in list_records(jobs, 'deletion'):
        if operation['state'] == 'PREPARED':
            for move in reversed(operation['moves']):
                source = _safe(jobs.root, Path(move['source']))
                target = _safe(jobs.root/'trash', Path(move['trash']))
                if target.exists() and not source.exists():
                    target.replace(source)
            operation['state'] = 'ROLLED_BACK'
            put(jobs, 'deletion', operation['token'], operation)
            results.append(operation)
        elif operation['state'] == 'PURGE_PENDING':
            results.append(_purge(jobs, operation))
    return results
