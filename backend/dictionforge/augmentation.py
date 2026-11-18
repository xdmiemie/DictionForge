"""Sentence feedback with independent drafts and feature-before-version publication."""
import copy
import hashlib
import json
from pathlib import Path
import shutil
import time
import uuid
import numpy as np
import soundfile as sf
from . import datasets, features
from .audio import atomic_json, digest
from .errors import Cancelled
from .jobs import Conflict
from .media_lifecycle import acquire_job
from .models import verify_artifact
from .records import get, put, media
from .references import _safe
from .worker import check_cancel


def _rows(db, kind):
    return [json.loads(row[0]) for row in db.execute('SELECT metadata FROM records WHERE kind=?', (kind,))]


def _descendant(version, ancestor, rows):
    seen = set()
    while version and version['id'] not in seen:
        if version['id'] == ancestor:
            return True
        seen.add(version['id'])
        version = rows.get(version.get('parent_id'))
    return False


def latest(jobs, anchor, db=None):
    if db is None:
        with jobs.connect() as connection:
            return latest(jobs, anchor, connection)
    parent = get(jobs, 'version', anchor, db)
    rows = {row['id']: row for row in _rows(db, 'version') if row['dataset_id'] == parent['dataset_id']}
    return max((row for row in rows.values() if _descendant(row, anchor, rows)), key=lambda row: (row['created'], row['id']))


def target(jobs, tts_id, sentence_id=None):
    request = get(jobs, 'tts', tts_id)
    source = request['model'].get('provenance', {}).get('version_id')
    if not source:
        return dict(eligible=False, reason='此模型没有可追溯的角色训练数据集，请先使用本项目训练模型。')
    version = latest(jobs, source)
    return dict(eligible=True, source_model_id=request['model_id'], source_version_id=source,
                target_version_id=version['id'], dataset_id=version['dataset_id'], role=version['role'],
                config=version['config'])


def _replay(jobs, kind, payload, request_id):
    with jobs.connect() as db:
        old = db.execute('SELECT * FROM jobs WHERE request=?', (request_id,)).fetchone()
    if old:
        old = jobs.decode(old)
        if old['kind'] != kind or old['payload'] != payload:
            raise Conflict('REQUEST_ID_CONFLICT：请求已绑定其他追加')
        return old


def submit(jobs, tts_id, sentence_id, target_version_id, request_id, split_config=None):
    payload = dict(tts_id=tts_id, sentence_id=sentence_id, target_version_id=target_version_id,
                   split_config=split_config or {})
    old = _replay(jobs, 'augmentation_prepare', payload, request_id)
    if old:
        return old
    destination = target(jobs, tts_id)
    if not destination['eligible']:
        raise ValueError(destination['reason'])
    request, result = get(jobs, 'tts', tts_id), get(jobs, 'tts_result', tts_id)
    if jobs.get(tts_id)['state'] != 'COMPLETED' or result.get('outcome') != 'COMPLETED':
        raise ValueError('APPEND_TTS_NOT_COMPLETE')
    sentence = next((row for row in result['sentences'] if row['sentence_id'] == sentence_id), None)
    if not sentence:
        raise ValueError('APPEND_SENTENCE_UNKNOWN')
    with jobs.connect() as db:
        versions = {row['id']: row for row in _rows(db, 'version')}
    parent = versions.get(target_version_id)
    if not parent or not _descendant(parent, destination['source_version_id'], versions):
        raise ValueError('APPEND_TARGET_MISMATCH：目标版本不属于来源角色谱系')
    config = dict(parent['config'])
    if set(payload['split_config']) - set(config):
        raise ValueError('APPEND_SPLIT_CONFIG_UNKNOWN')
    config.update(payload['split_config'])
    # Validate the same rules as original recordings before enqueuing.
    datasets.bounds(np.zeros(48000, dtype=np.float32), **config)
    model = get(jobs, 'model', request['model_id'])
    for name, item in model['weights'].items():
        if item['sha256'] != request['weight_hashes'][name]:
            raise Conflict('APPEND_SOURCE_MODEL_CHANGED')
        verify_artifact(item)
    def accepted(db, job_id):
        get(jobs, 'version', target_version_id, db)
        if get(jobs, 'model', request['model_id'], db).get('status') != 'READY':
            raise Conflict('APPEND_SOURCE_MODEL_NOT_READY')
        acquire_job(jobs, [sentence['audio']['audio_id']], job_id, db=db)
        source = get(jobs, 'media', sentence['audio']['audio_id'], db)
        path = _safe(jobs.root/'runtime'/'tts'/tts_id, Path(source['path']))
        if source['sha256'] != sentence['audio']['sha256'] or digest(path) != source['sha256']:
            raise Conflict('APPEND_SOURCE_AUDIO_CHANGED')
        row = dict(id=job_id, state='QUEUED', created=time.time(), target_version_id=target_version_id,
            source_version_id=destination['source_version_id'], dataset_id=parent['dataset_id'], role=parent['role'],
            source_model_id=request['model_id'], source_weight_hashes=request['weight_hashes'],
            tts_id=tts_id, sentence=copy.deepcopy(sentence), config=config, asr_calls=0,
            source_key=datasets.fingerprint([tts_id, sentence_id, source['sha256']]),
            request_id=request_id, jobs=[job_id])
        put(jobs, 'augmentation', job_id, row, db)
    return jobs.submit('augmentation_prepare', payload, request_id, on_accept=accepted)


def snapshot(jobs, key):
    row = get(jobs, 'augmentation', key)
    row['job'] = jobs.get(row['jobs'][-1])
    if row['state'] in {'QUEUED','PREPARING_AUDIO','LABELING','FEATURIZING'} and row['job']['state'] in {'CANCELLED','INTERRUPTED','FAILED'}:
        row.update(state=row['job']['state'], error=row.get('error') or row['job'].get('error'))
    if row.get('draft_id'):
        row['draft'] = get(jobs, 'draft', row['draft_id'])
    if row.get('version_id'):
        row['training_defaults'] = dict(version_id=row['version_id'], initial_model=row['source_model_id'])
    return row


def publish_submit(jobs, key, revision, request_id):
    payload = dict(augmentation_id=key, revision=revision)
    old = _replay(jobs, 'augmentation_publish', payload, request_id)
    if old:
        return old
    def accepted(db, job_id):
        row = get(jobs, 'augmentation', key, db)
        if row['state'] in {'PUBLISHED', 'DUPLICATE'}:
            raise Conflict('APPEND_ALREADY_PUBLISHED')
        if any(jobs.decode(item)['state'] in {'QUEUED', 'RUNNING'} for item in
               db.execute('SELECT * FROM jobs WHERE id IN ('+','.join('?' for _ in row['jobs'])+')', row['jobs'])):
            raise Conflict('APPEND_BUSY：追加仍在处理中')
        draft = get(jobs, 'draft', row['draft_id'], db)
        if draft['revision'] != revision:
            raise Conflict('VERSION_CONFLICT：请重新读取标注')
        row.update(state='QUEUED', error=None, jobs=[*row['jobs'], job_id])
        put(jobs, 'augmentation', key, row, db)
    return jobs.submit('augmentation_publish', payload, request_id, on_accept=accepted)


def _state(jobs, key, state, **values):
    with jobs.connect(write=True) as db:
        row = get(jobs, 'augmentation', key, db)
        row.update(state=state, **values)
        put(jobs, 'augmentation', key, row, db)
    return row


def _prepare(jobs, job, row):
    source = row['sentence']['audio']
    path = _safe(jobs.root/'runtime'/'tts'/row['tts_id'], Path(source['path']))
    if digest(path) != source['sha256']:
        raise Conflict('APPEND_SOURCE_AUDIO_CHANGED')
    pcm, rate = sf.read(path, dtype='int16')
    if rate != 48000 or pcm.ndim != 1 or not len(pcm) or np.max(np.abs(pcm.astype(np.int32))) < 32:
        raise ValueError('APPEND_AUDIO_INVALID')
    floats = pcm.astype(np.float32)/32768
    intervals = datasets.bounds(floats, **row['config'])
    if not intervals:
        raise ValueError('APPEND_NO_AUDIO：当前切分规则没有保留有效语音')
    whole = len(intervals) == 1 and row['config']['min_seconds'] <= len(pcm)/48000 <= row['config']['max_seconds']
    if whole:
        a,b = intervals[0]
        if np.any(np.abs(pcm[:a].astype(np.int32)) > 32) or np.any(np.abs(pcm[b:].astype(np.int32)) > 32):
            intervals = [(0,len(pcm))]  # Keep the complete utterance when trimming could remove quiet speech.
    area = _safe(jobs.root, jobs.root/'drafts'/row['id'])
    area.mkdir(parents=True, exist_ok=True)
    clips = []
    for index, (start, stop) in enumerate(intervals):
        check_cancel(jobs, job['id'])
        key = uuid.uuid4().hex
        output = area/(key+'.wav')
        sf.write(output, pcm[start:stop], 48000, subtype='PCM_16')
        provenance = dict(augmentation_id=row['id'], source_key=row['source_key'], tts_id=row['tts_id'],
            sentence_id=row['sentence']['sentence_id'], source_model_id=row['source_model_id'],
            source_weight_hashes=row['source_weight_hashes'], source_audio_sha256=source['sha256'],
            text_sha256=hashlib.sha256(row['sentence']['text'].encode()).hexdigest(), segment_index=index,
            start_sample=start, end_sample=stop, trim_applied=whole and (start,stop)!=(0,len(pcm)))
        clips.append(dict(id=key, path=str(output), audio_id=media(jobs, output, owner=row['id']),
            sha256=digest(output), pcm_sha256=hashlib.sha256(pcm[start:stop].tobytes()).hexdigest(),
            seconds=(stop-start)/48000, sample_rate=48000, start_sample=start, end_sample=stop,
            source='synthesized', source_id=row['tts_id'], source_sha256=source['sha256'],
            group='tts:'+row['source_key'], language='zh', text=row['sentence']['text'] if whole else '',
            label_source='tts_original' if whole else 'pending', reviewed=whole, excluded=False,
            asr_error=None, split='train', provenance=provenance,
            clipping_ratio=float(np.mean(np.abs(floats[start:stop]) >= .999))))
    omitted=[]
    cursor=0
    for start,stop in [*intervals,(len(pcm),len(pcm))]:
        if start>cursor:
            omitted.append(dict(start_sample=cursor,end_sample=start,seconds=(start-cursor)/48000,
                                reason='静音或短于最小时长的未保留区间'))
        cursor=stop
    draft = dict(id=row['id'], augmentation_id=row['id'], dataset_id=row['dataset_id'], role=row['role'],
        parent_version=row['target_version_id'], revision=0, clips=clips, config=row['config'],
        created=time.time(), rejected=omitted)
    put(jobs, 'draft', draft['id'], draft)
    _state(jobs,row['id'],'LABELING',draft_id=draft['id'],whole=whole,asr_calls=0 if whole else len(clips))
    if not whole:
        try:
            datasets.transcribe(jobs, job, draft)
        except Cancelled:
            raise
        except Exception as exc:
            _state(jobs,row['id'],'REVIEW_REQUIRED',error='ASR失败，可重识别或手工校对：'+str(exc))
        return _state(jobs,row['id'],'REVIEW_REQUIRED')
    return _publish(jobs, job, get(jobs,'augmentation',row['id']), 0)


def _pcm_hash(clip):
    if clip.get('pcm_sha256'):
        return clip['pcm_sha256']
    pcm, rate = sf.read(clip['path'], dtype='int16')
    if rate != 48000 or pcm.ndim != 1:
        raise ValueError('APPEND_PARENT_AUDIO_FORMAT')
    return hashlib.sha256(pcm.tobytes()).hexdigest()


def _publish(jobs, job, row, revision):
    for attempt in range(3):
        check_cancel(jobs, job['id'])
        draft = get(jobs, 'draft', row['draft_id'])
        if draft['revision'] != revision:
            raise Conflict('VERSION_CONFLICT：发布期间标注已变化')
        selected = [copy.deepcopy(c) for c in draft['clips'] if not c['excluded']]
        if not selected or any(not c['reviewed'] or not c['text'].strip() for c in selected):
            raise ValueError('APPEND_REVIEW_REQUIRED：请确认每个保留片段的标注')
        parent = latest(jobs, row['target_version_id'])
        existing = {_pcm_hash(c) for c in parent['clips']}
        prior_source = any(c.get('provenance', {}).get('source_key') == row['source_key'] for c in parent['clips'])
        added = []
        if not prior_source:
            for clip in selected:
                pcm_hash = _pcm_hash(clip)
                if pcm_hash not in existing:
                    added.append(clip); existing.add(pcm_hash)
        if not added:
            with jobs.connect(write=True) as db:
                if db.execute("SELECT 1 FROM controls WHERE job_id=? AND action='cancel'",(job['id'],)).fetchone():
                    raise Cancelled()
                if get(jobs,'draft',draft['id'],db)['revision'] != revision:
                    raise Conflict('VERSION_CONFLICT：去重前标注已变化')
                if latest(jobs,row['target_version_id'],db)['id'] != parent['id']:
                    continue
                fresh = get(jobs,'augmentation',row['id'],db)
                fresh.update(state='DUPLICATE', version_id=parent['id'], added_count=0, added_seconds=0)
                put(jobs,'augmentation',row['id'],fresh,db)
                draft['published'] = parent['id']; put(jobs,'draft',draft['id'],draft,db)
            return fresh
        version_id = uuid.uuid4().hex
        area = _safe(jobs.root, jobs.root/'datasets'/row['dataset_id']/version_id)
        area.mkdir(parents=True)
        clips = copy.deepcopy(parent['clips']) + added
        lines = []
        for clip in clips:
            check_cancel(jobs, job['id'])
            if digest(Path(clip['path'])) != clip['sha256']:
                raise Conflict('APPEND_CLIP_CHANGED: '+clip['id'])
            output = area/(clip['id']+'.wav')
            shutil.copyfile(clip['path'], output)
            clip.update(path=str(output), audio_id=uuid.uuid4().hex)
            if clip in added:
                clip['split']='train'
                clip['raw_text']=clip['text']
                clip['text']=' '.join(clip['text'].replace('|','，').split())
            if clip['split']=='train':
                lines.append(f"{output}|{row['dataset_id']}|ZH|{clip['text']}")
        version = dict(id=version_id,dataset_id=row['dataset_id'],role=row['role'],parent_id=parent['id'],
            created=time.time(),clips=clips,seconds=sum(c['seconds'] for c in clips),config=parent['config'],
            path=str(area),list_path=str(area/'train.list'),fingerprint=datasets.fingerprint(clips),
            feature_status='READY',feature_path=str(jobs.root/'features'/version_id),
            warnings=parent.get('warnings',[]),augmentation_id=row['id'],source_model_id=row['source_model_id'],
            added_count=len(added),added_seconds=sum(c['seconds'] for c in added))
        (area/'train.list').write_text('\n'.join(lines)+'\n',encoding='utf-8')
        atomic_json(area/'manifest.json',version)
        _state(jobs,row['id'],'FEATURIZING',candidate_version_id=version_id,rebase_attempt=attempt)
        result = features._run(jobs,job,'features',version)
        if not result.get('ready'):
            raise ValueError('APPEND_FEATURES_FAILED: '+json.dumps(result.get('errors',[]),ensure_ascii=False))
        if result.get('version_fingerprint') != version['fingerprint']:
            raise Conflict('APPEND_FEATURE_FINGERPRINT_CHANGED')
        files = dict(result['indexes'])
        for entry in result['entries'].values(): files.update(entry['files'])
        for relative, expected in files.items():
            path = _safe(jobs.root/'features'/version_id, jobs.root/'features'/version_id/relative)
            if digest(path) != expected: raise Conflict('APPEND_FEATURE_CHANGED: '+relative)
        for clip in clips:
            if digest(Path(clip['path'])) != clip['sha256']:
                raise Conflict('APPEND_CLIP_CHANGED: '+clip['id'])
        check_cancel(jobs,job['id'])
        with jobs.connect(write=True) as db:
            current_draft = get(jobs,'draft',draft['id'],db)
            if current_draft['revision'] != revision:
                raise Conflict('VERSION_CONFLICT：特征准备期间标注已变化')
            if latest(jobs,row['target_version_id'],db)['id'] != parent['id']:
                continue  # Candidate remains unregistered; shared valid feature cache is reusable.
            if db.execute("SELECT 1 FROM controls WHERE job_id=? AND action='cancel'",(job['id'],)).fetchone():
                raise Cancelled('追加已取消')
            for clip in clips:
                put(jobs,'media',clip['audio_id'],dict(id=clip['audio_id'],path=clip['path'],owner=version_id,
                    external=False,sha256=clip['sha256'],state='AVAILABLE'),db)
            put(jobs,'version',version_id,version,db)
            put(jobs,'features',version_id,result,db)
            current_draft['published']=version_id
            put(jobs,'draft',draft['id'],current_draft,db)
            fresh=get(jobs,'augmentation',row['id'],db)
            fresh.update(state='PUBLISHED',version_id=version_id,parent_version_id=parent['id'],
                         added_count=version['added_count'],added_seconds=version['added_seconds'],error=None)
            put(jobs,'augmentation',row['id'],fresh,db)
            return fresh
    raise Conflict('APPEND_PARENT_CHANGED：数据版本连续变化，草稿已保留，请重试发布')


def process(jobs, job):
    key = job['id'] if job['kind']=='augmentation_prepare' else job['payload']['augmentation_id']
    row=get(jobs,'augmentation',key)
    try:
        result=_prepare(jobs,job,row) if job['kind']=='augmentation_prepare' else _publish(jobs,job,row,job['payload']['revision'])
        return dict(outcome='COMPLETED',augmentation_id=key,**{k:result[k] for k in ('state','version_id','added_count','added_seconds','asr_calls') if k in result})
    except Cancelled:
        _state(jobs,key,'CANCELLED'); raise
    except Exception as exc:
        _state(jobs,key,'FAILED',error=str(exc))
        raise
