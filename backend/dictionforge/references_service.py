"""Immutable 48 kHz references and logical model selection, without GPU residency."""
import json
import os
from pathlib import Path
import re
import subprocess
import time
import uuid

import numpy as np
import soundfile as sf

from . import models
from .audio import atomic_json, digest, ffmpeg, probe
from .jobs import Conflict
from .records import get, put, list_records, media
from .training_config import config_fingerprint

RATE = 48000


def _local_path(value):
    value = str(value)
    if re.match(r'^[A-Za-z][A-Za-z0-9+.-]*:', value) and not re.match(r'^[A-Za-z]:[\\/]', value):
        raise ValueError('REFERENCE_LOCAL_FILE_REQUIRED：仅支持本地音频文件')
    path = Path(value).expanduser()
    if path.drive.startswith('\\\\'):
        raise ValueError('REFERENCE_LOCAL_FILE_REQUIRED：不接受网络共享路径')
    path = path.resolve()
    if path.drive.startswith('\\\\'):
        raise ValueError('REFERENCE_LOCAL_FILE_REQUIRED：不接受网络共享路径')
    return path


def _decode_error(exc):
    detail = getattr(exc, 'stderr', None) or str(exc)
    if isinstance(detail, bytes):
        detail = detail.decode('utf-8', errors='replace')
    return ValueError('REFERENCE_DECODE_FAILED：参考文件无法识别或解码；'+str(detail).strip()[:400])


def _probe_local(source):
    result = subprocess.run(['ffprobe', '-v', 'error', '-protocol_whitelist', 'file,pipe',
        '-select_streams', 'a:0', '-show_streams', '-show_format', '-of', 'json', str(source)],
        capture_output=True, check=True, timeout=30)
    data = json.loads(result.stdout)
    if data['format']['format_name'] not in {'wav', 'mp3', 'flac'}:
        raise ValueError('主参考支持 WAV、MP3 或 FLAC 本地音频')
    stream = data['streams'][0]
    return {'seconds': float(stream.get('duration') or data['format']['duration'])}


def _receipt(jobs, request_id, action, payload, db=None):
    if not request_id:
        raise ValueError('REQUEST_ID_REQUIRED')
    try:
        previous = get(jobs, 'reference_request', request_id, db)
    except KeyError:
        return None
    if previous['action'] != action or previous['payload'] != payload:
        raise Conflict('参考请求 ID 已绑定其他操作或输入')
    return previous['result']


def _remember(jobs, request_id, action, payload, result, db):
    put(jobs, 'reference_request', request_id, dict(action=action, payload=payload, result=result), db)
    return result


def validate_reference(reference):
    if not reference or reference.get('language') != 'zh' or not reference.get('text', '').strip():
        raise ValueError('REFERENCE_TEXT_REQUIRED：参考需要准确非空文本，语言须为 zh')
    if reference.get('status') not in (None, 'READY'):
        raise ValueError('REFERENCE_NOT_READY')
    path = Path(reference['path'])
    if not path.is_file() or digest(path) != reference['sha256']:
        raise Conflict('REFERENCE_CHANGED：参考文件缺失或哈希改变')
    try:
        pcm, rate = sf.read(path, dtype='float32', always_2d=True)
        info = sf.info(path)
    except (RuntimeError, OSError, ValueError) as exc:
        raise _decode_error(exc) from exc
    seconds = len(pcm)/rate
    if rate != RATE or pcm.shape[1] != 1 or not 3 <= seconds <= 10 or info.format != 'WAV' or info.subtype != 'PCM_16':
        raise ValueError('REFERENCE_FORMAT：主参考须为 3–10 秒、48 kHz 单声道 PCM')
    if not np.isfinite(pcm).all() or float(np.max(np.abs(pcm))) < 1e-4 or float(np.sqrt(np.mean(pcm.astype('float64')**2))) < 1e-5:
        raise ValueError('REFERENCE_SILENT：参考音频为空、静音或数值异常')
    if reference.get('fingerprint') and reference['fingerprint'] != models.reference_fingerprint(reference):
        raise Conflict('REFERENCE_FINGERPRINT_CHANGED')
    return dict(reference, seconds=seconds, sample_rate=rate, channels=1,
                fingerprint=models.reference_fingerprint(reference))


def import_reference(jobs, source_path=None, audio_id=None, text='', language='zh', request_id=None):
    if bool(source_path) == bool(audio_id):
        raise ValueError('请提供本地路径或已登记音频 ID，二者选择一项')
    text = text.strip()
    if language != 'zh' or not text:
        raise ValueError('REFERENCE_TEXT_REQUIRED：参考文本不能为空，语言须为 zh')
    payload = dict(source_path=str(_local_path(source_path)) if source_path else None,
                   audio_id=audio_id, text=text, language=language)
    previous = _receipt(jobs, request_id, 'import', payload)
    if previous:
        return previous
    source_media = get(jobs, 'media', audio_id) if audio_id else None
    source = _local_path(source_media['path'] if source_media else payload['source_path'])
    if not source.is_file() or source_media and source_media.get('state') != 'AVAILABLE':
        raise ValueError('REFERENCE_SOURCE_MISSING')
    try:
        info = _probe_local(source)
    except (subprocess.SubprocessError, KeyError, IndexError, ValueError) as exc:
        raise _decode_error(exc) from exc
    if not 2.95 <= info['seconds'] <= 10.1:
        raise ValueError('REFERENCE_DURATION：请选择 3–10 秒的参考录音')
    checksum = digest(source)
    if source_media and source_media['sha256'] != checksum:
        raise Conflict('REFERENCE_SOURCE_CHANGED')
    lease = None
    if audio_id:
        from .references import acquire
        lease = acquire(jobs, 'media', audio_id, 'reference-import:'+request_id)
    try:
        try:
            pcm = np.frombuffer(ffmpeg('-protocol_whitelist', 'file,pipe', '-i', source, '-vn', '-t', '11', '-ac', '1', '-ar', RATE, '-f', 'f32le', 'pipe:1'), dtype='<f4')
        except (subprocess.SubprocessError, ValueError) as exc:
            raise _decode_error(exc) from exc
        if not 3*RATE <= len(pcm) <= 10*RATE:
            raise ValueError('REFERENCE_DURATION：解码后的参考录音须为 3–10 秒')
        if not np.isfinite(pcm).all() or float(np.max(np.abs(pcm))) < 1e-4 or float(np.sqrt(np.mean(pcm.astype('float64')**2))) < 1e-5:
            raise ValueError('REFERENCE_SILENT：参考录音静音或音量过低')
        if digest(source) != checksum:
            raise Conflict('REFERENCE_SOURCE_CHANGED_DURING_IMPORT')
        key = uuid.uuid4().hex
        parent = jobs.root/'references'
        parent.mkdir(parents=True, exist_ok=True)
        staging, final = parent/('.'+key+'.part'), parent/key
        staging.mkdir()
        target = staging/'reference.wav'
        sf.write(target, pcm, RATE, subtype='PCM_16')
        with target.open('r+b') as stream:
            stream.flush()
            os.fsync(stream.fileno())
        reference = dict(id=key, path=str(final/'reference.wav'), sha256=digest(target), text=text, language=language,
            seconds=len(pcm)/RATE, samples=len(pcm), sample_rate=RATE, channels=1, subtype='PCM_16',
            source={'path': str(source), 'sha256': checksum, 'audio_id': audio_id}, status='READY', created=time.time())
        reference['fingerprint'] = models.reference_fingerprint(reference)
        atomic_json(staging/'manifest.json', reference)
        staging.replace(final)
        with jobs.connect(write=True) as db:
            previous = _receipt(jobs, request_id, 'import', payload, db)
            if previous:
                return previous
            reference['audio_id'] = media(jobs, reference['path'], owner=key, db=db)
            atomic_json(final/'manifest.json', reference)
            put(jobs, 'model_reference', key, reference, db)
            return _remember(jobs, request_id, 'import', payload, reference, db)
    finally:
        if lease:
            from .references import release
            release(jobs, lease)


def list_references(jobs):
    return sorted(list_records(jobs, 'model_reference'), key=lambda item: item.get('created', 0), reverse=True)


def selection(jobs, db=None):
    try:
        stored = get(jobs, 'model_selection', 'active', db)
    except KeyError:
        stored = {}
    model_id = stored.get('model_id')
    result = dict(active_model=model_id, model=None, default_reference=None, reference_fingerprint=None,
                  enabled=bool(model_id), valid=False, errors=[], gpu_resident=False)
    if not model_id:
        return result
    try:
        model = models.describe(jobs, model_id, db)
        result.update(model=model, default_reference=model.get('default_reference'),
                      reference_fingerprint=model.get('reference_fingerprint'))
        if model.get('status') != 'READY':
            raise ValueError('MODEL_NOT_READY')
        for item in [*model['weights'].values(), *model.get('dependencies', [])]:
            path = Path(item['path'])
            if not path.is_file() or path.stat().st_size != item['bytes']:
                raise ValueError('MODEL_FILE_MISSING_OR_SIZE_CHANGED')
        reference = validate_reference(model.get('default_reference'))
        result.update(model=model, default_reference=reference, reference_fingerprint=reference['fingerprint'], valid=True)
    except (KeyError, ValueError, OSError) as exc:
        result['errors'].append(str(exc))
    return result


def set_default(jobs, model_id, reference_id, request_id):
    payload = dict(model_id=model_id, reference_id=reference_id)
    previous = _receipt(jobs, request_id, 'set_default', payload)
    if previous:
        return previous
    reference = validate_reference(get(jobs, 'model_reference', reference_id))
    with jobs.connect(write=True) as db:
        previous = _receipt(jobs, request_id, 'set_default', payload, db)
        if previous:
            return previous
        reference = validate_reference(get(jobs, 'model_reference', reference_id, db))
        if model_id == 'official':
            put(jobs, 'model_defaults', 'official', dict(default_reference=reference, reference_fingerprint=reference['fingerprint'], updated=time.time()), db)
        else:
            model = get(jobs, 'model', model_id, db)
            if model.get('status') != 'READY':
                raise ValueError('MODEL_NOT_READY')
            model.update(default_reference=reference, reference_fingerprint=reference['fingerprint'], reference_updated=time.time())
            model['validation'] = dict(model['validation'], gpu='NOT_RUN', reference_fingerprint=reference['fingerprint'])
            model['validation'].pop('gpu_test_id', None)
            put(jobs, 'model', model_id, model, db)
        result = dict(model=models.describe(jobs, model_id, db), reference=reference, selection=selection(jobs, db))
        return _remember(jobs, request_id, 'set_default', payload, result, db)


def enable(jobs, model_id, request_id):
    payload = dict(model_id=model_id)
    previous = _receipt(jobs, request_id, 'enable', payload)
    if previous:
        return previous
    model = models.resolve(jobs, model_id)
    reference = validate_reference(model.get('default_reference'))
    with jobs.connect(write=True) as db:
        previous = _receipt(jobs, request_id, 'enable', payload, db)
        if previous:
            return previous
        if model_id != 'official' and get(jobs, 'model', model_id, db).get('status') != 'READY':
            raise Conflict('MODEL_NOT_READY')
        reference = validate_reference(models.describe(jobs, model_id, db).get('default_reference'))
        put(jobs, 'model_selection', 'active', dict(model_id=model_id, enabled=time.time(),
            reference_fingerprint=reference['fingerprint']), db)
        result = selection(jobs, db)
        return _remember(jobs, request_id, 'enable', payload, result, db)


def disable(jobs):
    with jobs.connect(write=True) as db:
        db.execute("DELETE FROM records WHERE kind='model_selection' AND id='active'")
    return selection(jobs)
