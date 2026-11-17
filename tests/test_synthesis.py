"""TTS orchestration and actual PCM I/O; synthetic samples are not GPU/voice acceptance."""
import copy
from pathlib import Path
import uuid

import numpy as np
import pytest
import soundfile as sf

from dictionforge import synthesis, models, references
from dictionforge.audio import digest
from dictionforge.errors import Cancelled
from dictionforge.jobs import Jobs, Conflict
from dictionforge.records import get, put, media, list_records


@pytest.fixture
def context(tmp_path):
    jobs = Jobs(tmp_path/'data')
    area = jobs.root/'models'/'role'/'voice'
    area.mkdir(parents=True)
    weights = {}
    for name in ('gpt', 'sovits'):
        path = area/(name+'.fixture')
        path.write_bytes((name+' fixture, not actual model').encode())
        weights[name] = models.artifact(path)
    reference_path = jobs.root/'references'/'fixture.wav'
    reference_path.parent.mkdir()
    sf.write(reference_path, .2*np.sin(2*np.pi*440*np.arange(4*48000)/48000), 48000, subtype='PCM_16')
    reference = dict(id='ref', path=str(reference_path), sha256=digest(reference_path), text='参考文本', language='zh',
                     seconds=4., sample_rate=48000, channels=1, status='READY')
    reference['fingerprint'] = models.reference_fingerprint(reference)
    reference['audio_id'] = media(jobs, reference_path, owner='ref')
    put(jobs, 'model_reference', 'ref', reference)
    model = dict(id='voice', model_id='role', version_id='voice', role='角色', path=str(area), weights=weights,
                 dependencies=[], status='READY', validation={'structure':'PASS','gpu':'NOT_RUN'},
                 default_reference=reference, provenance={'version_id': 'source-data'})
    put(jobs, 'model', 'voice', model)
    return jobs, model, reference


def submit(context, **kwargs):
    jobs, model, reference = context
    return synthesis.submit(jobs, request_id=kwargs.pop('request_id', uuid.uuid4().hex),
        model_version_id='voice', text=kwargs.pop('text', ' 你好🙂！\n你好🙂！ '), **kwargs)


def audio(path, pcm):
    sf.write(path, pcm, 48000, subtype='PCM_16')
    return dict(path=str(path), sha256=digest(path), bytes=path.stat().st_size, sample_rate=48000,
                channels=1, samples=len(pcm), seconds=len(pcm)/48000)


def result_fixture(request):
    area = Path(request['output_dir'])
    parts, rows, cursor = [], [], 0
    for unit in request['sentences']:
        pcm = np.zeros(4800+unit['index']*120, dtype='int16')
        pcm[10:-20] = np.arange(len(pcm)-30) % 800+200
        parts.append(pcm)
        item = audio(area/(unit['sentence_id']+'.wav'), pcm)
        rows.append(dict(unit, actual_seed=unit['seed'], audio=item, start_sample=cursor, end_sample=cursor+len(pcm),
                         leading_silence_samples=10, trailing_silence_samples=20,
                         silence_threshold={'amplitude':32,'unit':'pcm16'}))
        cursor += len(pcm)
    return dict(outcome='COMPLETED', model_id=request['model_id'], source_text=request['source_text'],
        text=request['source_text'], language=request['language'], parameters=request['parameters'],
        weight_hashes=request['weight_hashes'], reference=request['reference'], sentences=rows,
        audio=audio(area/'full.wav', np.concatenate(parts)), coordinate_system=dict(synthesis.COORDINATES))


def test_replay_does_not_restore_old_current_and_request_is_frozen(context):
    jobs, model, reference = context
    assert synthesis.current(jobs) is None
    first = submit(context, request_id='first')
    frozen = copy.deepcopy(get(jobs, 'tts', first['id']))
    second = submit(context, request_id='second', text='新句子。')
    assert submit(context, request_id='first')['id'] == first['id']
    assert synthesis.current(jobs)['id'] == second['id']
    model['default_reference']['text'] = '后来修改的参考'
    put(jobs, 'model', 'voice', model)
    assert get(jobs, 'tts', first['id']) == frozen
    assert frozen['source_text'] == ' 你好🙂！\n你好🙂！ '
    with pytest.raises(Conflict, match='REQUEST_ID_CONFLICT'):
        submit(context, request_id='first', text='另一个请求')


@pytest.mark.parametrize('change', [{'text':''}, {'text':'\n！？'}, {'text':'字\0'}, {'language':'en'},
    {'split_rule':'words'}, {'parameters':{'seed':True}}, {'parameters':{'sample_steps':7}},
    {'parameters':{'temperature':float('nan')}}, {'parameters':{'unknown':1}}])
def test_invalid_request_never_changes_current(context, change):
    jobs, model, reference = context
    first = submit(context)
    with pytest.raises(ValueError):
        submit(context, **change)
    assert synthesis.current(jobs)['id'] == first['id']
    assert len(jobs.list()) == 1


def test_explicit_reference_can_override_broken_default(context):
    jobs, model, reference = context
    model['default_reference'] = dict(reference, path=str(jobs.root/'missing.wav'))
    put(jobs, 'model', 'voice', model)
    job = submit(context, reference_id='ref')
    assert get(jobs, 'tts', job['id'])['reference']['path'] == reference['path']


def test_weight_hash_failure_and_delete_race_never_change_current(context, monkeypatch):
    jobs, model, reference = context
    first = submit(context)
    path = Path(model['weights']['gpt']['path'])
    saved = path.read_bytes()
    path.write_bytes(b'changed')
    with pytest.raises(Conflict):
        submit(context)
    path.write_bytes(saved)
    original = jobs.submit
    def deleted(*args, **kwargs):
        with jobs.connect(write=True) as db:
            db.execute("DELETE FROM records WHERE kind='model' AND id='voice'")
        return original(*args, **kwargs)
    monkeypatch.setattr(jobs, 'submit', deleted)
    with pytest.raises(KeyError):
        submit(context)
    assert synthesis.current(jobs)['id'] == first['id'] and len(jobs.list()) == 1


def test_actual_decode_ranges_and_media_registration_are_complete_and_idempotent(context, monkeypatch):
    jobs, model, reference = context
    job = submit(context, parameters={'seed': 2**32-1})
    request = get(jobs, 'tts', job['id'])
    supplied = result_fixture(request)
    calls = []
    def infer(*args, **kwargs):
        calls.append(True)
        return copy.deepcopy(supplied)
    monkeypatch.setattr(synthesis, 'invoke', infer)
    result = synthesis.process(jobs, job)
    assert result['outcome'] == 'COMPLETED' and len(result['sentences']) == 2
    assert [row['actual_seed'] for row in result['sentences']] == [2**32-1, 0]
    assert result['audio']['samples'] == sum(row['audio']['samples'] for row in result['sentences'])
    assert result['provenance']['model_version_id'] == 'voice'
    assert all(get(jobs, 'media', item['audio_id'])['owner'] == job['id'] for item in [result['audio'], *(r['audio'] for r in result['sentences'])])
    assert synthesis.process(jobs, job) == result and len(calls) == 1
    assert len([row for row in list_records(jobs, 'media') if row['owner'] == job['id']]) == 3
    assert any(row['kind'] == 'tts_result' for row in references.references_for(jobs, 'model', 'voice'))


@pytest.mark.parametrize('damage', ['model', 'text', 'language', 'order', 'seed', 'boolean', 'range', 'hash', 'concat', 'silence', 'near_silence'])
def test_mismatched_output_is_not_registered_and_reports_phase(context, monkeypatch, damage):
    jobs, model, reference = context
    job = submit(context)
    request = get(jobs, 'tts', job['id'])
    result = result_fixture(request)
    row = result['sentences'][0]
    if damage in {'model','text','language'}:
        result[{'model':'model_id','text':'text','language':'language'}[damage]] = 'other'
    elif damage == 'order':
        result['sentences'].reverse()
    elif damage == 'seed':
        row['actual_seed'] += 1
    elif damage == 'boolean':
        row['start_sample'] = False
    elif damage == 'range':
        row['end_sample'] += 1
    elif damage == 'hash':
        row['audio']['sha256'] = '0'*64
    elif damage == 'concat':
        full, _ = sf.read(result['audio']['path'], dtype='int16')
        full[100] += 1
        result['audio'] = audio(Path(result['audio']['path']), full)
    else:
        count = row['audio']['samples']
        quiet = np.full(count, 32 if damage == 'near_silence' else 0, dtype='int16')
        row['audio'] = audio(Path(row['audio']['path']), quiet)
        row.update(leading_silence_samples=count, trailing_silence_samples=count)
        second, _ = sf.read(result['sentences'][1]['audio']['path'], dtype='int16')
        result['audio'] = audio(Path(result['audio']['path']), np.concatenate([quiet,second]))
    monkeypatch.setattr(synthesis, 'invoke', lambda *a, **k: result)
    failed = synthesis.process(jobs, job)
    assert failed['outcome'] == 'FAILED' and failed['phase'] == 'output_validation'
    if damage not in {'model','text','language'}:
        assert 'index=' in failed['error']
    assert not [item for item in list_records(jobs, 'media') if item.get('owner') == job['id']]
    assert synthesis.current(jobs)['result']['outcome'] == 'FAILED'


def test_active_tts_holds_original_reference_dataset_after_default_replacement(context):
    jobs, model, reference = context
    area = jobs.root/'datasets'/'role'/'reference-version'
    area.mkdir(parents=True)
    path = area/'reference.wav'
    path.write_bytes(Path(reference['path']).read_bytes())
    put(jobs, 'version', 'reference-version', dict(id='reference-version', dataset_id='role', path=str(area)))
    cloned = dict(reference, path=str(path), audio_id=media(jobs,path,owner='reference-version'))
    model['default_reference'] = cloned
    put(jobs,'model','voice',model)
    job = submit(context)
    model['default_reference'] = reference
    put(jobs,'model','voice',model)
    assert any(item['kind']=='tts' for item in references.delete_preview(jobs,'version','reference-version')['references'])


@pytest.mark.parametrize('boundary', ['before_inference', 'child_exit', 'after_decode'])
def test_cancel_before_publication_never_registers_audio(context, monkeypatch, boundary):
    jobs, model, reference = context
    job = submit(context)
    assert jobs.claim()['id'] == job['id']
    result = result_fixture(get(jobs, 'tts', job['id']))
    calls = []
    def cancel():
        jobs.control(job['id'], 'cancel', 'cancel-'+boundary)
    def infer(*args, **kwargs):
        calls.append(True)
        if boundary == 'child_exit':
            cancel()
        return result
    monkeypatch.setattr(synthesis, 'invoke', infer)
    if boundary == 'before_inference':
        cancel()
    if boundary == 'after_decode':
        original = synthesis.validate_result
        def validate(*args):
            value = original(*args)
            cancel()
            return value
        monkeypatch.setattr(synthesis, 'validate_result', validate)
    with pytest.raises(Cancelled):
        synthesis.process(jobs, job)
    assert len(calls) == (0 if boundary == 'before_inference' else 1)
    assert not [row for row in list_records(jobs, 'media') if row['owner'] == job['id']]
    assert synthesis.current(jobs)['id'] == job['id']
    assert synthesis.current(jobs)['result']['outcome'] == 'CANCELLED'


def test_completed_result_revalidation_detects_later_corruption(context, monkeypatch):
    jobs, model, reference = context
    job = submit(context)
    supplied = result_fixture(get(jobs, 'tts', job['id']))
    calls = []
    def infer(*args, **kwargs):
        calls.append(True)
        return supplied
    monkeypatch.setattr(synthesis, 'invoke', infer)
    completed = synthesis.process(jobs, job)
    assert completed['outcome'] == 'COMPLETED'
    Path(completed['audio']['path']).write_bytes(b'externally corrupted audio')
    failed = synthesis.process(jobs, job)
    assert failed['outcome'] == 'FAILED' and failed['phase'] == 'result_revalidation'
    assert synthesis.current(jobs)['result'] == failed
    assert len(calls) == 1
