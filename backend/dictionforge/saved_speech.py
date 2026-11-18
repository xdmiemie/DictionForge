"""Independent saved audio, frozen selections, and recoverable managed deletion."""
import copy
import json
import os
from pathlib import Path
import re
import subprocess
import time
import uuid

import soundfile as sf

from .audio import atomic_json, digest
from .engine import invoke
from .errors import Cancelled
from .jobs import Conflict
from .records import get, put, list_records, media
from .references import _safe
from .supervisor import exclusive
from .training_config import config_fingerprint


def _id(value):
    if not isinstance(value, str) or not re.fullmatch('[a-f0-9]{32}', value):
        raise ValueError('SAVED_ID_INVALID')
    return value


def _cancel(jobs, key, db=None):
    cancelled = (db.execute("SELECT 1 FROM controls WHERE job_id=? AND action='cancel'", (key,)).fetchone()
                 if db else jobs.cancelled(key))
    if cancelled:
        raise Cancelled()


def submit(jobs, tts_id, format='wav', title='', request_id=None):
    if format not in {'wav', 'mp3'} or not isinstance(title, str) or len(title) > 200 or not request_id:
        raise ValueError('SAVE_INPUT_INVALID：格式为 wav/mp3，标题最多 200 字符，必须有请求 ID')
    payload = dict(tts_id=tts_id, format=format, title=title.strip())
    def accepted(db, job_id):
        from .media_lifecycle import acquire_job
        result = get(jobs, 'tts_result', tts_id, db)
        if result.get('outcome') != 'COMPLETED' or not result.get('audio', {}).get('audio_id'):
            raise Conflict('SAVE_SOURCE_NOT_COMPLETED')
        request = get(jobs, 'tts', tts_id, db)
        audio = copy.deepcopy(result['audio'])
        acquire_job(jobs, [audio['audio_id']], owner_job=job_id, db=db)
        from .synthesis import _audio
        _audio(audio, Path(request['output_dir']))
        saved_id = uuid.uuid4().hex
        frozen = dict(schema_version=1, id=saved_id, job_id=job_id, request_id=request_id,
            tts_id=tts_id, format=format, title=title.strip() or result['text'].strip()[:60],
            text=result['text'], model_id=request['model_id'], model_name=request['model'].get('role', request['model_id']),
            source_audio=audio, provenance=copy.deepcopy(result.get('provenance', {})),
            parameters=copy.deepcopy(result['parameters']), reference=copy.deepcopy(result['reference']),
            sentences=copy.deepcopy(result.get('sentences', [])), created=time.time())
        put(jobs, 'speech_save', job_id, frozen, db)
    return jobs.submit('speech_save', payload, request_id, on_accept=accepted)


def _wav_info(path, expected_hash=None):
    if expected_hash and digest(path) != expected_hash:
        raise ValueError('SAVE_SOURCE_HASH_CHANGED')
    with sf.SoundFile(path) as stream:
        if stream.format != 'WAV' or stream.subtype != 'PCM_16' or stream.samplerate != 48000 or stream.channels != 1 or not len(stream):
            raise ValueError('SAVE_WAV_FORMAT_INVALID')
        count = 0
        for block in stream.blocks(blocksize=65536, dtype='int16'):
            count += len(block)
        if count != len(stream):
            raise ValueError('SAVE_WAV_DECODE_INCOMPLETE')
    return dict(path=str(path), sha256=digest(path), bytes=path.stat().st_size,
                sample_rate=48000, channels=1, samples=count, seconds=count/48000,
                codec='pcm_s16le', decode_verified=True)


def _mp3_info(path, source_samples):
    raw = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'a:0', '-show_streams', '-of', 'json', str(path)],
                         check=True, capture_output=True, timeout=30)
    stream = json.loads(raw.stdout)['streams'][0]
    if (stream['codec_name'] != 'mp3' or int(stream['sample_rate']) != 48000 or
            stream['channels'] != 1 or int(stream['bit_rate']) != 320000):
        raise ValueError('SAVE_MP3_FORMAT_INVALID：需要 320 kbps / 48 kHz / 单声道')
    decoded = subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-protocol_whitelist', 'file,pipe',
        '-i', str(path), '-map', '0:a:0', '-f', 's16le', '-acodec', 'pcm_s16le', '-'],
        check=True, capture_output=True, timeout=900).stdout
    count = len(decoded)//2
    if len(decoded) % 2 or count != source_samples:
        raise ValueError('SAVE_MP3_DECODE_LENGTH_CHANGED')
    return dict(path=str(path), sha256=digest(path), bytes=path.stat().st_size, sample_rate=48000,
                channels=1, samples=count, seconds=count/48000, codec='mp3', bitrate=320000, decode_verified=True)


def _verify_record(jobs, row):
    base = _safe(jobs.root/'saved_speech', jobs.root/'saved_speech'/_id(row['id']))
    for key in ('wav', 'audio'):
        item = row[key]
        path = _safe(base, Path(item['path']))
        if not path.is_file() or path.stat().st_size != item['bytes'] or digest(path) != item['sha256']:
            raise ValueError('SAVED_FILE_CHANGED')
    return row


def _register(jobs, job, row):
    _verify_record(jobs, row)
    with jobs.connect(write=True) as db:
        _cancel(jobs, job['id'], db)
        try:
            existing = get(jobs, 'saved_speech', row['id'], db)
        except KeyError:
            existing = None
        if existing:
            if existing.get('status') != 'READY':
                raise Conflict('SAVED_ALREADY_DELETED')
            return existing
        by_path = {}
        for key in ('wav', 'audio'):
            item = row[key]
            audio_id = by_path.get(item['path'])
            if audio_id is None:
                audio_id = media(jobs, item['path'], owner=row['id'], db=db)
                by_path[item['path']] = audio_id
                if get(jobs, 'media', audio_id, db)['sha256'] != item['sha256']:
                    raise ValueError('SAVE_AUDIO_CHANGED_DURING_REGISTRATION')
            item['audio_id'] = audio_id
        put(jobs, 'saved_speech', row['id'], row, db)
        jobs.emit(db, job['id'], 'speech_saved', {'id': row['id'], 'audio_id': row['audio']['audio_id']})
    return row


def process(jobs, job):
    frozen = get(jobs, 'speech_save', job['id'])
    key = _id(frozen['id'])
    base = _safe(jobs.root/'saved_speech', jobs.root/'saved_speech'/key)
    stage = _safe(jobs.root/'saved_speech', jobs.root/'saved_speech'/('.'+key+'.staging'))
    phase = 'save_validation'
    try:
        try:
            existing = get(jobs, 'saved_speech', key)
        except KeyError:
            existing = None
        if existing:
            if existing.get('status') != 'READY':
                raise Conflict('SAVED_ALREADY_DELETED')
            return dict(outcome='COMPLETED', saved_speech=_verify_record(jobs, existing))
        _cancel(jobs, job['id'])
        if base.exists():
            row = json.loads((base/'manifest.json').read_text(encoding='utf-8'))
            if (row.get('id') != key or row.get('save_job_id') != job['id'] or
                    row.get('source_audio_sha256') != frozen['source_audio']['sha256'] or
                    row.get('wav', {}).get('sha256') != frozen['source_audio']['sha256'] or
                    row.get('wav', {}).get('path') != str(base/'source.wav') or
                    row.get('audio', {}).get('path') != str(base/('audio.mp3' if frozen['format']=='mp3' else 'source.wav'))):
                raise ValueError('SAVE_PUBLISHED_IDENTITY_MISMATCH')
            return dict(outcome='COMPLETED', saved_speech=_register(jobs, job, row))
        stage.mkdir(parents=True, exist_ok=True)
        source = _safe(jobs.root, Path(frozen['source_audio']['path']))
        _wav_info(source, frozen['source_audio']['sha256'])
        phase = 'wav_copy'
        jobs.progress(job['id'], {'stage': phase, 'message': '保存独立原生 WAV 副本'})
        wav = stage/'source.wav'
        temporary = stage/'source.wav.part'
        _safe(stage, temporary)
        with source.open('rb') as incoming, temporary.open('wb') as outgoing:
            while chunk := incoming.read(1024*1024):
                _cancel(jobs, job['id'])
                outgoing.write(chunk)
            outgoing.flush()
            os.fsync(outgoing.fileno())
        wav_info = _wav_info(temporary, frozen['source_audio']['sha256'])
        if digest(source) != frozen['source_audio']['sha256']:
            raise ValueError('SAVE_SOURCE_CHANGED_DURING_COPY')
        temporary.replace(wav)
        wav_info['path'] = str(base/'source.wav')
        output_info = dict(wav_info)
        if frozen['format'] == 'mp3':
            phase = 'mp3_encoding'
            jobs.progress(job['id'], {'stage': phase, 'message': '编码 320 kbps MP3 并完整解码校验'})
            mp3 = stage/'audio.mp3'
            part = stage/'audio.mp3.part'
            _safe(stage, part)
            invoke(jobs, job, ['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
                '-protocol_whitelist', 'file,pipe', '-i', wav, '-map', '0:a:0', '-vn', '-map_metadata', '-1',
                '-ac', '1', '-ar', '48000', '-codec:a', 'libmp3lame', '-b:a', '320k', '-f', 'mp3', part])
            _cancel(jobs, job['id'])
            output_info = _mp3_info(part, wav_info['samples'])
            part.replace(mp3)
            output_info['path'] = str(base/'audio.mp3')
        phase = 'save_publication'
        row = {name: copy.deepcopy(frozen[name]) for name in ('id','tts_id','title','text','format','model_id',
            'model_name','created','provenance','parameters','reference','sentences')}
        # Sentence metadata is provenance only: saved playback is the independent full WAV/MP3.
        row.update(schema_version=1, status='READY', path=str(base), save_job_id=job['id'],
                   source_audio_sha256=frozen['source_audio']['sha256'], wav=wav_info, audio=output_info)
        atomic_json(stage/'manifest.json', row)
        _cancel(jobs, job['id'])
        stage.replace(base)
        return dict(outcome='COMPLETED', saved_speech=_register(jobs, job, row))
    except Cancelled:
        raise
    except Exception as exc:
        return dict(outcome='FAILED', phase=phase, error=str(exc), code=type(exc).__name__, saved_id=key)


def _matching(jobs, model_id=None, query=''):
    if not isinstance(query, str) or len(query) > 500:
        raise ValueError('SAVED_FILTER_INVALID')
    text = query.strip().casefold()
    rows = [row for row in list_records(jobs, 'saved_speech') if row.get('status') == 'READY' and
            (not model_id or row['model_id'] == model_id) and
            (not text or text in row['title'].casefold() or text in row['text'].casefold())]
    return sorted(rows, key=lambda row: (row['created'], row['id']), reverse=True)


def list_saved(jobs, model_id=None, query='', page=1, page_size=10):
    if type(page) is not int or page < 1 or type(page_size) is not int or not 1 <= page_size <= 10:
        raise ValueError('SAVED_PAGE_INVALID：每页 1–10 条')
    rows = _matching(jobs, model_id, query)
    return dict(items=rows[(page-1)*page_size:page*page_size], total=len(rows), page=page, page_size=page_size,
                pages=(len(rows)+page_size-1)//page_size, filter=dict(model_id=model_id, query=query))


def _inventory(jobs, row):
    base = _safe(jobs.root/'saved_speech', jobs.root/'saved_speech'/_id(row['id']))
    if Path(row['path']).resolve() != base.resolve() or not base.is_dir():
        raise ValueError('SAVED_DIRECTORY_MISSING_OR_CHANGED')
    files = []
    for folder, dirs, names in os.walk(base, followlinks=False):
        for name in [*dirs, *names]:
            _safe(base, Path(folder)/name)
        for name in names:
            path = Path(folder)/name
            files.append(dict(path=str(path), sha256=digest(path), bytes=path.stat().st_size))
    return sorted(files, key=lambda item: item['path'])


def delete_preview(jobs, ids=None, model_id=None, query='', all_filtered=False):
    if type(all_filtered) is not bool or (all_filtered and ids is not None) or (not all_filtered and not ids):
        raise ValueError('SAVED_DELETE_SCOPE_INVALID')
    if all_filtered:
        selected = [row['id'] for row in _matching(jobs, model_id, query)]
    else:
        if not isinstance(ids, list) or len(ids) > 10000:
            raise ValueError('SAVED_DELETE_IDS_INVALID')
        selected = list(dict.fromkeys(_id(key) for key in ids))
    items = []
    for key in selected:
        row = get(jobs, 'saved_speech', key)
        files = _inventory(jobs, row) if row.get('status') == 'READY' else []
        items.append(dict(id=key, title=row['title'], files=files, fingerprint=config_fingerprint(files)))
    token = uuid.uuid4().hex
    result = dict(token=token, preview_id=token, ids=selected, count=len(selected), items=items,
        filter=dict(model_id=model_id, query=query) if all_filtered else None, expires=time.time()+600)
    put(jobs, 'saved_delete_preview', token, result)
    return result


def _purge(jobs, operation):
    base = _safe(jobs.root/'trash/saved_speech', Path(operation['trash']))
    try:
        if base.exists():
            for folder, dirs, names in os.walk(base, topdown=False, followlinks=False):
                for name in names:
                    _safe(base, Path(folder)/name).unlink()
                for name in dirs:
                    _safe(base, Path(folder)/name).rmdir()
            base.rmdir()
        operation.update(state='DELETED', error=None)
    except OSError as exc:
        operation.update(state='PURGE_PENDING', error=str(exc))
    put(jobs, 'saved_deletion', operation['id'], operation)
    return operation


def _restore(jobs, operation):
    source = _safe(jobs.root/'saved_speech', Path(operation['source']))
    trash = _safe(jobs.root/'trash/saved_speech', Path(operation['trash']))
    try:
        if trash.exists():
            if source.exists():
                raise OSError('SAVE_DELETE_RESTORE_COLLISION')
            trash.replace(source)
        if not source.is_dir():
            raise OSError('SAVED_DELETE_RESTORE_SOURCE_MISSING')
        operation.update(state='ROLLED_BACK', error=None)
    except OSError as exc:
        operation.update(state='RESTORE_PENDING', error=str(exc))
    put(jobs, 'saved_deletion', operation['id'], operation)
    return operation


def _delete_one(jobs, token, selected):
    from .media_lifecycle import active_leases
    key = _id(selected['id'])
    operation_id = token+'-'+key
    try:
        old = get(jobs, 'saved_deletion', operation_id)
    except KeyError:
        old = None
    if old and old['state'] in {'PREPARED','RESTORE_PENDING'}:
        old = _restore(jobs, old)
        if old['state'] == 'RESTORE_PENDING':
            return dict(id=key, status='protected', reason=old['error'])
    if old and old['state'] in {'PURGE_PENDING', 'DELETED'}:
        if old['state'] == 'PURGE_PENDING':
            _purge(jobs, old)
        return dict(id=key, status='already_deleted')
    source = _safe(jobs.root/'saved_speech', jobs.root/'saved_speech'/key)
    trash = _safe(jobs.root/'trash/saved_speech', jobs.root/'trash/saved_speech'/operation_id)
    trash.parent.mkdir(parents=True, exist_ok=True)
    operation = dict(id=operation_id, saved_id=key, token=token, state='PREPARED', source=str(source), trash=str(trash))
    put(jobs, 'saved_deletion', operation_id, operation)
    try:
        with jobs.connect(write=True) as db:
            row = get(jobs, 'saved_speech', key, db)
            if row.get('status') == 'DELETED':
                operation['state'] = 'DELETED'
                put(jobs, 'saved_deletion', operation_id, operation, db)
                return dict(id=key, status='already_deleted')
            leases = active_leases(jobs, owner=key, db=db)
            for item in (row['audio'], row['wav']):
                leases += active_leases(jobs, media_id=item.get('audio_id'), db=db)
            if leases:
                raise Conflict('SAVED_REFERENCED：作品正在播放、下载或处理中')
            if config_fingerprint(_inventory(jobs, row)) != selected['fingerprint']:
                raise Conflict('SAVED_DELETE_PREVIEW_CHANGED')
            source.replace(trash)
            row.update(status='DELETED', deleted=time.time())
            put(jobs, 'saved_speech', key, row, db)
            for item in (row['audio'], row['wav']):
                stored = get(jobs, 'media', item['audio_id'], db)
                stored['state'] = 'DELETED'
                put(jobs, 'media', stored['id'], stored, db)
            operation['state'] = 'PURGE_PENDING'
            put(jobs, 'saved_deletion', operation_id, operation, db)
    except Exception as exc:
        _restore(jobs, operation)
        return dict(id=key, status='protected', reason=str(exc))
    _purge(jobs, operation)
    return dict(id=key, status='deleted')


def delete(jobs, token):
    _id(token)
    preview = get(jobs, 'saved_delete_preview', token)
    if preview['expires'] < time.time():
        raise Conflict('SAVED_DELETE_CONFIRMATION_EXPIRED')
    try:
        with exclusive(jobs.state/'saved-deletion.lock'):
            results = [_delete_one(jobs, token, item) for item in preview['items']]
    except OSError as exc:
        results = [dict(id=item['id'], status='protected', reason='SAVED_DELETE_BUSY: '+str(exc))
                   for item in preview['items']]
    return dict(token=token, results=results, counts={status: sum(row['status'] == status for row in results)
                for status in ('deleted', 'already_deleted', 'protected')})


def reconcile_deletions(jobs):
    results = []
    try:
        with exclusive(jobs.state/'saved-deletion.lock'):
            for operation in list_records(jobs, 'saved_deletion'):
                if operation['state'] in {'PREPARED', 'RESTORE_PENDING'}:
                    results.append(_restore(jobs, operation))
                elif operation['state'] == 'PURGE_PENDING':
                    results.append(_purge(jobs, operation))
    except OSError:
        pass  # Active confirmation owns the durable intents; retry on next tick.
    return results
