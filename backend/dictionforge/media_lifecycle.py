"""Durable media leases and explicitly retired TTS audio collection.

All admission and retirement decisions share Jobs.connect(write=True). A purge
intent is committed before any move, so restart can reconcile either location.
Only browser leases expire by time; job and HTTP leases follow owner identity.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat
import time
import uuid

import psutil

from .audio import digest
from .jobs import ACTIVE, TERMINAL, Conflict
from .records import get, put


class MediaGone(ValueError):
    pass


def _rows(db, kind):
    return [json.loads(row[0]) for row in db.execute('SELECT metadata FROM records WHERE kind=?', (kind,))]


def _safe(jobs, path):
    root, path = jobs.root.absolute(), Path(path).absolute()
    if not path.is_relative_to(root) or path == root or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('MEDIA_PATH_OUTSIDE_MANAGED_ROOT')
    current = path
    while True:
        if current.exists() or current.is_symlink():
            info = current.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                raise ValueError('MEDIA_REPARSE_POINT')
        if current == root:
            break
        current = current.parent
    return path


def _area(jobs, owner):
    if not isinstance(owner, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', owner):
        raise ValueError('MEDIA_OWNER_INVALID')
    return _safe(jobs, jobs.root.absolute()/'runtime'/'tts'/owner)


def _live(db, lease, now):
    if lease['kind'] == 'browser':
        return lease['expires_at'] > now
    if lease['kind'] == 'job':
        row = db.execute('SELECT state FROM jobs WHERE id=?', (lease['owner_job'],)).fetchone()
        # Missing owners are not silently considered expired. Reconciliation
        # must not release a job reference merely because of elapsed time.
        return row is None or row['state'] not in TERMINAL
    if lease['kind'] == 'http':
        try:
            process = psutil.Process(lease['pid'])
            return process.is_running() and abs(process.create_time()-lease['process_start']) < .001
        except psutil.NoSuchProcess:
            return False
        except psutil.AccessDenied:
            return True
    return True


def active_leases(jobs, *, media_id=None, owner=None, db=None):
    if db is None:
        with jobs.connect() as connection:
            return active_leases(jobs, media_id=media_id, owner=owner, db=connection)
    media_owner = get(jobs, 'media', media_id, db).get('owner') if media_id else None
    return [lease for lease in _rows(db, 'media_lease') if _live(db, lease, time.time()) and
            (owner is None or lease.get('owner') == owner) and
            (media_id is None or lease['media_id'] == media_id or
             (media_owner is not None and lease.get('scope') == 'owner' and lease.get('owner') == media_owner))]


def _available(jobs, media_id, db):
    item = get(jobs, 'media', media_id, db)
    if item.get('state') != 'AVAILABLE' or not Path(item['path']).is_file():
        raise MediaGone('AUDIO_EXPIRED：音频已失效或正在清理')
    return item


def _acquire(jobs, media_id, kind, *, ttl=90, scope='owner', owner_job=None, db=None):
    if db is None:
        with jobs.connect(write=True) as connection:
            return _acquire(jobs, media_id, kind, ttl=ttl, scope=scope, owner_job=owner_job, db=connection)
    if scope not in {'owner', 'audio'}:
        raise ValueError('MEDIA_LEASE_SCOPE_INVALID')
    if kind == 'browser' and (type(ttl) not in (int, float) or not 1 <= ttl <= 300):
        raise ValueError('MEDIA_LEASE_TTL_INVALID')
    item = _available(jobs, media_id, db)
    if kind == 'job':
        row = db.execute('SELECT state FROM jobs WHERE id=?', (owner_job,)).fetchone()
        if row is None or row['state'] not in ACTIVE:
            raise Conflict('MEDIA_JOB_LEASE_REQUIRES_ACTIVE_JOB')
    now, token = time.time(), uuid.uuid4().hex
    lease = dict(token=token, media_id=media_id, owner=item.get('owner'), kind=kind, scope=scope,
                 created=now, heartbeat=now, expires_at=now+ttl if kind == 'browser' else None)
    if kind == 'job':
        lease['owner_job'] = owner_job
    if kind == 'http':
        lease.update(pid=os.getpid(), process_start=psutil.Process(os.getpid()).create_time())
    put(jobs, 'media_lease', token, lease, db)
    return lease


def acquire_browser(jobs, audio_id, *, ttl=90, scope='owner', db=None):
    return _acquire(jobs, audio_id, 'browser', ttl=ttl, scope=scope, db=db)


def acquire_http(jobs, audio_id, db=None):
    return _acquire(jobs, audio_id, 'http', scope='audio', db=db)


def acquire_job(jobs, audio_ids, owner_job, db=None):
    if db is None:
        with jobs.connect(write=True) as connection:
            return acquire_job(jobs, audio_ids, owner_job, db=connection)
    return [_acquire(jobs, key, 'job', owner_job=owner_job, db=db) for key in dict.fromkeys(audio_ids)]


def heartbeat(jobs, token, *, ttl=90):
    if type(ttl) not in (int, float) or not 1 <= ttl <= 300:
        raise ValueError('MEDIA_LEASE_TTL_INVALID')
    with jobs.connect(write=True) as db:
        try:
            lease = get(jobs, 'media_lease', token, db)
        except KeyError as exc:
            raise MediaGone('MEDIA_LEASE_EXPIRED：播放租约已释放') from exc
        if lease['kind'] != 'browser':
            raise ValueError('ONLY_BROWSER_LEASE_HAS_HEARTBEAT')
        if not _live(db, lease, time.time()):
            raise MediaGone('MEDIA_LEASE_EXPIRED：请重新请求播放')
        _available(jobs, lease['media_id'], db)
        lease.update(heartbeat=time.time(), expires_at=time.time()+ttl)
        return put(jobs, 'media_lease', token, lease, db)


def release(jobs, token, db=None):
    if db is None:
        with jobs.connect(write=True) as connection:
            return release(jobs, token, connection)
    try:
        lease = get(jobs, 'media_lease', token, db)
    except KeyError:
        return {'released': True, 'token': token}
    if lease['kind'] == 'job' and _live(db, lease, time.time()):
        raise Conflict('MEDIA_JOB_LEASE_ACTIVE：任务结束后才能释放')
    db.execute("DELETE FROM records WHERE kind='media_lease' AND id=?", (token,))
    return {'released': True, 'token': token}


def retire_owner(jobs, owner, reason='superseded', db=None):
    if db is None:
        with jobs.connect(write=True) as connection:
            return retire_owner(jobs, owner, reason, connection)
    _area(jobs, owner)
    get(jobs, 'tts', owner, db)
    try:
        return get(jobs, 'media_retirement', owner, db)
    except KeyError:
        return put(jobs, 'media_retirement', owner, dict(owner=owner, reason=reason, retired=time.time(), state='RETIRED'), db)


def retire_previous(jobs, keep_owner, db=None):
    if db is None:
        with jobs.connect(write=True) as connection:
            return retire_previous(jobs, keep_owner, connection)
    return [retire_owner(jobs, row['job_id'], db=db) for row in _rows(db, 'tts') if row['job_id'] != keep_owner]


def state_for_owner(jobs, owner, db=None):
    if db is None:
        with jobs.connect() as connection:
            return state_for_owner(jobs, owner, connection)
    items = [row for row in _rows(db, 'media') if row.get('owner') == owner]
    if any(row.get('state') == 'PURGE_PENDING' for row in items):
        return 'PURGE_PENDING'
    if items and all(row.get('state') == 'DELETED' for row in items):
        return 'DELETED'
    try:
        if get(jobs, 'media_retirement', owner, db).get('state') == 'DELETED':
            return 'DELETED'
    except KeyError:
        pass
    return 'AVAILABLE'


def _result_state(jobs, owner, state, db):
    try:
        result = get(jobs, 'tts_result', owner, db)
    except KeyError:
        return
    result['media_state'] = state
    put(jobs, 'tts_result', owner, result, db)


def _eligible(jobs, owner, db):
    row = db.execute('SELECT state FROM jobs WHERE id=?', (owner,)).fetchone()
    if row is None or row['state'] not in TERMINAL:
        return False
    try:
        current = get(jobs, 'tts_current', 'current', db)['job_id']
    except KeyError:
        current = None
    if owner == current and row['state'] not in {'FAILED', 'CANCELLED', 'INTERRUPTED'}:
        return False
    return not active_leases(jobs, owner=owner, db=db)


def _plan(jobs, owner, db):
    area = _area(jobs, owner)
    request = get(jobs, 'tts', owner, db)
    if Path(request['output_dir']).absolute() != area:
        raise ValueError('MEDIA_TTS_DIRECTORY_MISMATCH')
    by_path = {}
    for item in _rows(db, 'media'):
        path = Path(item['path']).absolute()
        by_path.setdefault(path, []).append(item)
    paths = {path for path, items in by_path.items() if any(item.get('owner') == owner and
             not item.get('external') and item.get('state') != 'DELETED' for item in items)}
    if area.exists():
        # Interrupted workers can leave audio not yet registered. Only known
        # generated names are eligible; JSON, arbitrary files and directories stay.
        paths.update(path for path in area.iterdir() if re.fullmatch(
            r'(?:audio-[0-9a-f]{32}|sentence-[0-9a-f]{32}-[0-9]{4,})\.wav(?:\.part)?', path.name))
    identifier = uuid.uuid4().hex
    files = []
    for path in sorted(paths):
        _safe(jobs, path)
        if path.parent != area or path.suffix.lower() not in {'.wav', '.mp3', '.part'} or path.is_dir():
            raise ValueError('MEDIA_TTS_AUDIO_PATH_INVALID')
        aliases = by_path.get(path, [])
        if any(item.get('external') or item.get('owner') != owner for item in aliases):
            raise Conflict('MEDIA_SHARED_PATH_PROTECTED')
        destination = _safe(jobs, jobs.root.absolute()/'runtime'/'trash'/'tts'/owner/identifier/path.name)
        hashes = {item['sha256'] for item in aliases if item.get('sha256')}
        if len(hashes) > 1:
            raise ValueError('MEDIA_ALIAS_HASH_MISMATCH')
        checksum = next(iter(hashes), None)
        identity = None
        if path.is_file():
            info = path.stat()
            identity = dict(inode=info.st_ino, bytes=info.st_size, modified_ns=info.st_mtime_ns)
            try:
                actual = digest(path)
                if checksum and checksum != actual:
                    raise ValueError('MEDIA_PURGE_SOURCE_CHANGED')
                checksum = actual
            except OSError:
                pass  # Persist a retryable intent even when a Windows reader denies access.
        files.append(dict(source=str(path), trash=str(destination), media_ids=[i['id'] for i in aliases],
                          sha256=checksum, identity=identity, state='PENDING'))
    intent = dict(id=identifier, owner=owner, files=files, state='PURGE_PENDING', created=time.time())
    put(jobs, 'media_purge', identifier, intent, db)
    for item in files:
        for key in item['media_ids']:
            metadata = get(jobs, 'media', key, db)
            metadata.update(state='PURGE_PENDING', purge_id=identifier)
            put(jobs, 'media', key, metadata, db)
    retired = get(jobs, 'media_retirement', owner, db)
    retired.update(state='PURGE_PENDING', purge_id=identifier)
    put(jobs, 'media_retirement', owner, retired, db)
    _result_state(jobs, owner, 'PURGE_PENDING', db)
    return intent


def _move(source, destination):
    os.replace(source, destination)


def _finish(jobs, identifier):
    # The committed intent already prevents new admission. Keep the write lock
    # across moves and metadata changes to serialize all existing readers/savers.
    with jobs.connect(write=True) as db:
        intent = get(jobs, 'media_purge', identifier, db)
        owner, errors = intent['owner'], []
        if not _eligible(jobs, owner, db):
            return dict(id=identifier, state='PROTECTED')
        area = _area(jobs, owner)
        for item in intent['files']:
            source, trash = _safe(jobs, item['source']), _safe(jobs, item['trash'])
            expected_trash = jobs.root.absolute()/'runtime'/'trash'/'tts'/owner/identifier/source.name
            if source.parent != area or trash != expected_trash:
                raise ValueError('MEDIA_PURGE_INTENT_PATH_INVALID')
            try:
                if source.exists():
                    if trash.exists():
                        raise ValueError('MEDIA_PURGE_BOTH_LOCATIONS_EXIST')
                    info = source.stat()
                    identity = dict(inode=info.st_ino, bytes=info.st_size, modified_ns=info.st_mtime_ns)
                    if (source.is_dir() or (item.get('identity') and identity != item['identity']) or
                            (item['sha256'] and digest(source) != item['sha256'])):
                        raise ValueError('MEDIA_PURGE_SOURCE_CHANGED')
                    if not item['sha256']:
                        item['sha256'] = digest(source)
                    trash.parent.mkdir(parents=True, exist_ok=True)
                    _safe(jobs, trash)
                    _move(source, trash)
                elif trash.exists() and (trash.is_dir() or (item['sha256'] and digest(trash) != item['sha256'])):
                    raise ValueError('MEDIA_PURGE_TRASH_CHANGED')
                item['state'] = 'MOVED'
                for key in item['media_ids']:
                    metadata = get(jobs, 'media', key, db)
                    metadata.update(state='DELETED', deleted=time.time(), purge_id=identifier)
                    put(jobs, 'media', key, metadata, db)
            except OSError as exc:
                item.update(state='PURGE_PENDING', error=str(exc))
                errors.append(dict(path=str(source), error=str(exc)))
        intent.update(state='PURGE_PENDING' if errors else 'MOVED', errors=errors, updated=time.time())
        put(jobs, 'media_purge', identifier, intent, db)
        retired = get(jobs, 'media_retirement', owner, db)
        retired.update(state='PURGE_PENDING' if errors else 'DELETED', updated=time.time())
        put(jobs, 'media_retirement', owner, retired, db)
        _result_state(jobs, owner, retired['state'], db)
    # Unlink only after the durable DELETED metadata commit. A crash leaves a
    # known trash file, never an unrecorded removal of a live source.
    with jobs.connect(write=True) as db:
        intent = get(jobs, 'media_purge', identifier, db)
        for item in intent['files']:
            if item['state'] != 'MOVED':
                continue
            trash = _safe(jobs, item['trash'])
            try:
                trash.unlink(missing_ok=True)
                item['state'] = 'PURGED'
            except OSError as exc:
                item['unlink_error'] = str(exc)
        if all(item['state'] == 'PURGED' for item in intent['files']):
            intent['state'] = 'PURGED'
        put(jobs, 'media_purge', identifier, intent, db)
        return dict(id=identifier, state=intent['state'], errors=intent.get('errors', []))


def reconcile(jobs):
    report = dict(released_leases=[], purges=[], protected=[], errors=[])
    with jobs.connect(write=True) as db:
        for lease in _rows(db, 'media_lease'):
            if not _live(db, lease, time.time()):
                db.execute("DELETE FROM records WHERE kind='media_lease' AND id=?", (lease['token'],))
                report['released_leases'].append(lease['token'])
        for row in db.execute("SELECT id FROM jobs WHERE kind='tts' AND state IN ('FAILED','CANCELLED','INTERRUPTED')"):
            try:
                retire_owner(jobs, row['id'], reason='terminal_without_result', db=db)
            except KeyError:
                pass  # A job without a frozen TTS record owns no managed runtime directory.
        pending = [row['id'] for row in _rows(db, 'media_purge') if row['state'] != 'PURGED']
        for row in _rows(db, 'media_retirement'):
            if row['state'] != 'RETIRED':
                continue
            owner = row['owner']
            if not _eligible(jobs, owner, db):
                report['protected'].append(owner)
                continue
            try:
                pending.append(_plan(jobs, owner, db)['id'])
            except (ValueError, OSError) as exc:
                report['errors'].append(dict(owner=owner, error=str(exc)))
    for identifier in pending:
        try:
            report['purges'].append(_finish(jobs, identifier))
        except (ValueError, OSError) as exc:
            report['errors'].append(dict(id=identifier, error=str(exc)))
    return report
