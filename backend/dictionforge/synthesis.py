"""Frozen TTS acceptance, exact audio validation and retained per-sentence results."""
import copy
import json
import math
from pathlib import Path
import time

import numpy as np
import soundfile as sf

from .audio import atomic_json, digest
from .engine import invoke
from .errors import Cancelled
from .inference_worker import normalize_parameters
from .jobs import Conflict
from . import models
from .records import get, put, media
from .references import _safe
from .references_service import validate_reference
from .settings import Settings
from .text_units import split_text
from .training_config import config_fingerprint

COORDINATES = dict(text='unicode_code_point_half_open', audio='sample_half_open', sample_rate=48000,
                   concatenation='exact_sequential_pcm_no_added_silence')


def submit(jobs, request_id, model_version_id, text, language='zh', split_rule='punctuation', parameters=None, reference_id=None):
    if language != 'zh':
        raise ValueError('UNSUPPORTED_LANGUAGE：当前合成支持普通话 zh')
    if not request_id or not model_version_id:
        raise ValueError('INVALID_REQUEST：缺少请求 ID 或模型版本')
    parameters = normalize_parameters({} if parameters is None else parameters)
    units = split_text(text, split_rule, parameters['seed'], request_id)
    payload = dict(model_version_id=model_version_id, text=text, language=language, split_rule=split_rule,
                   parameters=parameters, reference_id=reference_id)
    with jobs.connect() as db:
        old = db.execute('SELECT * FROM jobs WHERE request=?', (request_id,)).fetchone()
        if old:
            previous = jobs.decode(old)
            if previous['kind'] != 'tts' or previous['payload'] != payload:
                raise Conflict('REQUEST_ID_CONFLICT：请求 ID 已绑定其他合成输入')
            return previous  # A replay must never move current back to an old task.
    model = models.resolve(jobs, model_version_id, check_reference=reference_id is None)
    reference = get(jobs, 'model_reference', reference_id) if reference_id else model.get('default_reference')
    if not reference:
        raise ValueError('REFERENCE_REQUIRED：请先设置有效参考音频和准确文本')
    reference = validate_reference(reference)
    model = copy.deepcopy(model)
    reference = copy.deepcopy(reference)
    def accepted(db, job_id):
        if model_version_id != 'official' and get(jobs, 'model', model_version_id, db).get('status') != 'READY':
            raise Conflict('MODEL_NOT_READY：模型已被删除或不可用')
        if reference.get('audio_id'):
            source_media = get(jobs, 'media', reference['audio_id'], db)
            if source_media.get('state') != 'AVAILABLE' or source_media['sha256'] != reference['sha256']:
                raise Conflict('REFERENCE_CHANGED：参考已不可用')
        validate_reference(reference)
        area = jobs.root/'runtime'/'tts'/job_id
        area.mkdir(parents=True, exist_ok=True)
        request = dict(schema_version=1, job_id=job_id, request_id=request_id, data_root=str(jobs.root),
            output_dir=str(area), upstream=str(Settings.from_environment().upstream), model_id=model_version_id,
            model_version_id=model_version_id, model=model, reference=reference,
            reference_fingerprint=reference['fingerprint'], source_text=text, text=text, language=language,
            split_rule=split_rule, parameters=parameters, precision='fp16', sentences=units,
            silence_threshold={'amplitude': 32, 'unit': 'pcm16'},
            weights={key: item['path'] for key, item in model['weights'].items()},
            weight_hashes={key: item['sha256'] for key, item in model['weights'].items()}, created=time.time())
        request['conditions_fingerprint'] = config_fingerprint(dict(weight_hashes=request['weight_hashes'],
            reference=reference['fingerprint'], text=text, language=language, split_rule=split_rule, parameters=parameters))
        atomic_json(area/'inference-request.json', request)
        put(jobs, 'tts', job_id, request, db)
        put(jobs, 'tts_current', 'current', {'job_id': job_id, 'accepted': request['created']}, db)
        from .media_lifecycle import retire_previous
        retire_previous(jobs, job_id, db=db)
    return jobs.submit('tts', payload, request_id, on_accept=accepted)


def snapshot(jobs, key):
    job = jobs.get(key)
    if job['kind'] != 'tts':
        raise ValueError('UNKNOWN_TTS_TASK')
    request = get(jobs, 'tts', key)
    try:
        result = get(jobs, 'tts_result', key)
    except KeyError:
        result = None
    if result and result.get('audio'):
        from .media_lifecycle import state_for_owner
        result['media_state'] = state_for_owner(jobs, key)
        for audio in [result.get('audio'), *(row.get('audio') for row in result.get('sentences', []))]:
            if audio and audio.get('audio_id'):
                record = get(jobs, 'media', audio['audio_id'])
                audio['state'] = record.get('state', 'AVAILABLE')
                audio['available'] = audio['state'] == 'AVAILABLE'
    return dict(id=key, job=job, request=request, result=result)


def current(jobs):
    try:
        key = get(jobs, 'tts_current', 'current')['job_id']
    except KeyError:
        return None
    return snapshot(jobs, key)


def _audio(item, area):
    path = _safe(area, Path(item['path']))
    if path.name.startswith('.') or path.suffix.lower() != '.wav' or not path.is_file():
        raise ValueError('TTS_AUDIO_PATH_INVALID')
    if type(item.get('bytes')) is not int or path.stat().st_size != item['bytes'] or digest(path) != item.get('sha256'):
        raise ValueError('TTS_AUDIO_HASH_OR_SIZE_CHANGED')
    info = sf.info(path)
    if info.format != 'WAV' or info.subtype != 'PCM_16' or info.samplerate != 48000 or info.channels != 1 or info.frames <= 0:
        raise ValueError('TTS_AUDIO_FORMAT_INVALID：需要原生 48 kHz 单声道 PCM16')
    if any(type(item.get(key)) is not int or item[key] != value for key, value in [('sample_rate', 48000), ('channels', 1), ('samples', info.frames)]):
        raise ValueError('TTS_AUDIO_METADATA_MISMATCH')
    seconds = item.get('seconds')
    if type(seconds) not in (int, float) or not math.isfinite(seconds) or abs(seconds-info.frames/48000) > 1e-7:
        raise ValueError('TTS_AUDIO_DURATION_MISMATCH')
    return path, info.frames


def validate_result(request, result):
    if result.get('outcome') != 'COMPLETED':
        raise ValueError('TTS_INCOMPLETE_RESULT')
    if result.get('coordinate_system') != COORDINATES:
        raise ValueError('TTS_COORDINATE_SYSTEM_INVALID')
    if result.get('model_id') != request['model_id'] or result.get('text') != request['source_text'] or result.get('language') != request['language']:
        raise ValueError('TTS_RESULT_IDENTITY_CHANGED')
    if result.get('source_text', request['source_text']) != request['source_text']:
        raise ValueError('TTS_RESULT_SOURCE_TEXT_CHANGED')
    if result.get('parameters') != request['parameters'] or result.get('weight_hashes') != request['weight_hashes']:
        raise ValueError('TTS_EFFECTIVE_INPUTS_CHANGED')
    received_reference = result.get('reference', {})
    if any(received_reference.get(key) != request['reference'][key] for key in ('sha256', 'text', 'language')):
        raise ValueError('TTS_REFERENCE_CHANGED')
    rows = result.get('sentences')
    if not isinstance(rows, list) or len(rows) != len(request['sentences']):
        raise ValueError('TTS_SENTENCE_COUNT_MISMATCH')
    area = Path(request['output_dir'])
    full, samples = _audio(result['audio'], area)
    seen = {full.resolve()}
    cursor = 0
    with sf.SoundFile(full) as stream:
        for expected, row in zip(request['sentences'], rows):
            label = f"sentence index={expected['index']} id={expected['sentence_id']}"
            integers = ('index', 'source_start', 'source_end', 'seed', 'actual_seed', 'start_sample', 'end_sample',
                        'leading_silence_samples', 'trailing_silence_samples')
            if not isinstance(row, dict) or any(type(row.get(key)) is not int for key in integers):
                raise ValueError(f'TTS_SENTENCE_INTEGER_INVALID: {label}')
            if any(row.get(key) != value for key, value in expected.items()) or row.get('actual_seed') != expected['seed']:
                raise ValueError(f'TTS_SENTENCE_IDENTITY_CHANGED: {label}')
            try:
                path, count = _audio(row['audio'], area)
            except (KeyError, ValueError, OSError, RuntimeError) as exc:
                raise ValueError(f'TTS_SENTENCE_AUDIO_INVALID: {label}: {exc}') from exc
            if path.resolve() in seen:
                raise ValueError(f'TTS_DUPLICATE_AUDIO_PATH: {label}')
            seen.add(path.resolve())
            if row.get('start_sample') != cursor or row.get('end_sample') != cursor+count:
                raise ValueError(f'TTS_SAMPLE_RANGE_INVALID: {label}')
            pcm, rate = sf.read(path, dtype='int16', always_2d=False)
            if len(pcm) != count or rate != 48000 or not np.array_equal(stream.read(count, dtype='int16'), pcm):
                raise ValueError(f'TTS_CONCATENATION_MISMATCH: {label}')
            if digest(path) != row['audio']['sha256']:
                raise ValueError(f'TTS_AUDIO_CHANGED_DURING_DECODE: {label}')
            threshold = row.get('silence_threshold', {})
            amplitude = threshold.get('amplitude')
            if threshold != request['silence_threshold'] or type(amplitude) is not int:
                raise ValueError(f'TTS_SILENCE_THRESHOLD_INVALID: {label}')
            sounding = np.flatnonzero(np.abs(pcm.astype('int32')) > amplitude)
            if not len(sounding):
                raise ValueError(f'TTS_SENTENCE_SILENT: {label}')
            leading = int(sounding[0]) if len(sounding) else count
            trailing = count-1-int(sounding[-1]) if len(sounding) else count
            if row.get('leading_silence_samples') != leading or row.get('trailing_silence_samples') != trailing:
                raise ValueError(f'TTS_SILENCE_RANGE_MISMATCH: {label}')
            cursor += count
        if cursor != samples or len(stream.read(1, dtype='int16')):
            raise ValueError('TTS_TOTAL_SAMPLES_MISMATCH')
    if digest(full) != result['audio']['sha256']:
        raise ValueError('TTS_FULL_AUDIO_CHANGED_DURING_DECODE')
    return result


def process(jobs, job):
    request = get(jobs, 'tts', job['id'])
    try:
        previous = get(jobs, 'tts_result', job['id'])
    except KeyError:
        previous = None
    area = Path(request['output_dir'])
    output = area/'inference-result.json'
    settings = Settings.from_environment()
    phase = 'inference'
    try:
        if previous and previous.get('outcome') == 'COMPLETED':
            phase = 'result_revalidation'
            return validate_result(request, previous)
        if jobs.cancelled(job['id']):
            raise Cancelled()
        result = invoke(jobs, job, [settings.worker_python, '-m', 'dictionforge.inference_worker',
            area/'inference-request.json', output], result=output)
        if jobs.cancelled(job['id']):
            raise Cancelled()
        if result.get('outcome') != 'FAILED':
            phase = 'output_validation'
            validate_result(request, result)
        if result.get('outcome') == 'FAILED':
            result.update(job_id=job['id'], model_version_id=request['model_id'], created=time.time())
            put(jobs, 'tts_result', job['id'], result)
            return result
        result.update(job_id=job['id'], model_version_id=request['model_id'], source_text=request['source_text'],
            text=request['source_text'], language=request['language'], split_rule=request['split_rule'], created=time.time(),
            conditions_fingerprint=request['conditions_fingerprint'], reference_fingerprint=request['reference_fingerprint'],
            provenance=dict(model_version_id=request['model_id'], model_id=request['model']['model_id'],
                role=request['model']['role'], source_data_version_id=request['model'].get('provenance', {}).get('version_id'),
                reference_id=request['reference'].get('id'), reference_sha256=request['reference']['sha256'], request_id=request['request_id']))
        with jobs.connect(write=True) as db:
            # Serialize the last cancel check with publication. A cancel accepted
            # before this transaction must not publish partially processed media.
            if db.execute("SELECT 1 FROM controls WHERE job_id=? AND action='cancel'", (job['id'],)).fetchone():
                raise Cancelled()
            for audio in [result['audio'], *(row['audio'] for row in result['sentences'])]:
                audio['audio_id'] = media(jobs, audio['path'], owner=job['id'], db=db)
                if get(jobs, 'media', audio['audio_id'], db)['sha256'] != audio['sha256']:
                    raise ValueError('TTS_AUDIO_CHANGED_DURING_REGISTRATION')
            put(jobs, 'tts_result', job['id'], result, db)
            jobs.emit(db, job['id'], 'tts_ready', {'audio_id': result['audio']['audio_id'], 'sentences': len(result['sentences'])})
        return result
    except Cancelled:
        put(jobs, 'tts_result', job['id'], dict(outcome='CANCELLED', error='合成已取消', phase=phase, code='CANCELLED'))
        raise
    except Exception as exc:
        result = dict(outcome='FAILED', error=str(exc), code=type(exc).__name__, phase=phase,
                      job_id=job['id'], model_version_id=request['model_id'], created=time.time())
        put(jobs, 'tts_result', job['id'], result)
        return result
    finally:
        jobs.progress(job['id'], {'stage': 'gpu_released', 'model_id': request['model_id'], 'gpu_resident': False})
