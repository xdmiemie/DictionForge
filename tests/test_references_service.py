"""Real audio I/O and selection contracts. Synthetic tones are not voice/GPU acceptance."""
import copy
from pathlib import Path
import uuid

import numpy as np
import pytest
import soundfile as sf

from dictionforge import models, references, references_service as service
from dictionforge.audio import digest, ffmpeg
from dictionforge.jobs import Jobs, Conflict
from dictionforge.records import get, put, media, list_records


def waveform(path, seconds=4., frequency=440., silence=False):
    frames = np.arange(round(seconds*32000))/32000
    pcm = np.zeros_like(frames) if silence else .2*np.sin(2*np.pi*frequency*frames)
    sf.write(path, np.stack([pcm, pcm*.7], axis=1), 32000, subtype='PCM_24')
    return path


@pytest.fixture
def context(tmp_path):
    jobs = Jobs(tmp_path/'data')
    source = waveform(tmp_path/'原始 参考.wav')
    for key in ('model-one', 'model-two'):
        area = jobs.root/'models'/'role'/key
        area.mkdir(parents=True)
        weights = {}
        for name in ('gpt', 'sovits'):
            path = area/(name+'.fixture')
            path.write_bytes((key+name+' CPU I/O fixture only').encode())
            weights[name] = models.artifact(path)
        put(jobs, 'model', key, dict(id=key, version_id=key, model_id='role', path=str(area), status='READY',
            role='角色', weights=weights, dependencies=[], validation={'structure': 'PASS', 'gpu': 'NOT_RUN'}, default_reference=None))
    return jobs, source


def imported(context, request='ref', **kwargs):
    jobs, source = context
    return service.import_reference(jobs, source_path=source, text='你好，这是准确参考文本。', request_id=request, **kwargs)


def test_import_converts_to_new_immutable_mono_48k_pcm_and_preserves_original(context):
    jobs, source = context
    before = digest(source)
    reference = imported(context)
    info = sf.info(reference['path'])
    assert (info.samplerate, info.channels, info.subtype, info.frames) == (48000, 1, 'PCM_16', 192000)
    assert reference['source']['sha256'] == before and digest(source) == before
    assert Path(reference['path']) != source and reference['seconds'] == 4
    assert get(jobs, 'media', reference['audio_id'])['owner'] == reference['id']
    assert service.import_reference(jobs, source_path=source, text=reference['text'], request_id='ref') == reference
    second = imported(context, request='new-ref')
    assert second['id'] != reference['id'] and second['path'] != reference['path']
    assert second['fingerprint'] == reference['fingerprint']
    assert digest(Path(reference['path'])) == reference['sha256']


@pytest.mark.parametrize('seconds,silence', [(2.99, False), (10.01, False), (4., True)])
def test_duration_and_silence_are_rejected_without_registration(context, seconds, silence):
    jobs, source = context
    waveform(source, seconds=seconds, silence=silence)
    with pytest.raises(ValueError, match='REFERENCE_DURATION|REFERENCE_SILENT'):
        imported(context)
    assert not list_records(jobs, 'model_reference')


def test_nonempty_text_language_and_request_identity_are_checked(context):
    jobs, source = context
    with pytest.raises(ValueError, match='REFERENCE_TEXT_REQUIRED'):
        service.import_reference(jobs, source_path=source, text=' ', request_id='empty')
    with pytest.raises(ValueError, match='REFERENCE_TEXT_REQUIRED'):
        service.import_reference(jobs, source_path=source, text='test', language='en', request_id='language')
    imported(context)
    with pytest.raises(Conflict):
        service.import_reference(jobs, source_path=source, text='改了文字', request_id='ref')


def test_registered_media_import_verifies_hash_and_releases_lease(context):
    jobs, source = context
    audio_id = media(jobs, source, external=True, owner='external')
    reference = service.import_reference(jobs, audio_id=audio_id, text='参考录音', request_id='media')
    assert reference['source']['audio_id'] == audio_id
    assert not list_records(jobs, 'reference')
    waveform(source, frequency=880.)
    with pytest.raises(Conflict, match='REFERENCE_SOURCE_CHANGED'):
        service.import_reference(jobs, audio_id=audio_id, text='参考录音', request_id='changed-media')


def test_replacing_default_preserves_old_frozen_probe_and_changes_fingerprint(context):
    from dictionforge import model_probe
    jobs, source = context
    first = imported(context)
    result = service.set_default(jobs, 'model-one', first['id'], 'default-one')
    assert result['model']['default_reference']['id'] == first['id']
    enabled = service.enable(jobs, 'model-one', 'enable')
    assert enabled['valid'] and enabled['active_model'] == 'model-one' and not enabled['gpu_resident']
    job = model_probe.submit(jobs, 'model-one', 'frozen-probe')
    frozen = copy.deepcopy(get(jobs, 'model_probe', job['id']))
    waveform(source, frequency=660.)
    second = imported(context, request='second-ref')
    changed = service.set_default(jobs, 'model-one', second['id'], 'default-two')
    assert changed['selection']['reference_fingerprint'] != enabled['reference_fingerprint']
    assert changed['model']['validation']['gpu'] == 'NOT_RUN'
    assert get(jobs, 'model_probe', job['id']) == frozen
    assert frozen['reference']['path'] == first['path'] and digest(Path(first['path'])) == first['sha256']


def test_enable_requires_valid_reference_and_keeps_previous_selection_on_error(context):
    jobs, source = context
    first = imported(context)
    service.set_default(jobs, 'model-one', first['id'], 'default')
    service.enable(jobs, 'model-one', 'enable-one')
    with pytest.raises(ValueError, match='REFERENCE_TEXT_REQUIRED'):
        service.enable(jobs, 'model-two', 'bad-enable')
    assert service.selection(jobs)['active_model'] == 'model-one'
    Path(first['path']).write_bytes(b'changed reference')
    status = service.selection(jobs)
    assert not status['valid'] and 'REFERENCE_CHANGED' in status['errors'][0]
    with pytest.raises(Conflict):
        service.enable(jobs, 'model-one', 'bad-hash')


def test_active_model_delete_protection_changes_with_selection(context):
    jobs, source = context
    reference = imported(context)
    for key in ('model-one', 'model-two'):
        service.set_default(jobs, key, reference['id'], 'default-'+key)
    service.enable(jobs, 'model-one', 'enable-one')
    assert any(item['kind'] == 'model_selection' for item in references.delete_preview(jobs, 'model', 'model-one')['references'])
    service.enable(jobs, 'model-two', 'enable-two')
    assert references.delete_preview(jobs, 'model', 'model-one')['deletable']
    assert not references.delete_preview(jobs, 'model', 'model-two')['deletable']
    assert service.disable(jobs)['active_model'] is None
    assert references.delete_preview(jobs, 'model', 'model-two')['deletable']


def test_selection_and_catalog_do_not_rehash_model_weights(context, monkeypatch):
    jobs, source = context
    reference = imported(context)
    service.set_default(jobs, 'model-one', reference['id'], 'default')
    service.enable(jobs, 'model-one', 'enable')
    monkeypatch.setattr(models, 'verify_artifact', lambda _: (_ for _ in ()).throw(AssertionError('large weight hashing on poll')))
    assert service.selection(jobs)['valid']
    assert models.describe(jobs, 'model-one')['id'] == 'model-one'


def test_official_reference_is_persisted_without_prefill_and_gpu_status_is_condition_specific(context, monkeypatch):
    jobs, source = context
    area = jobs.root/'assets'
    area.mkdir()
    dependencies = []
    for name in ('s1v3.ckpt', 's2Gv4.pth'):
        path = area/name
        path.write_bytes(b'official fixture only')
        dependencies.append(dict(models.artifact(path), id=name))
    monkeypatch.setattr(models, 'dependencies', lambda _jobs, verify=True: dependencies)
    assert models.official(jobs)['default_reference'] is None
    reference = imported(context)
    service.set_default(jobs, 'official', reference['id'], 'official-default')
    put(jobs, 'model_test', 'old-pass', dict(model_version_id='official', reference_fingerprint=reference['fingerprint'], created=1))
    assert models.official(jobs)['validation']['gpu'] == 'PASS'
    assert models.official(jobs)['validation']['gpu_test_id'] == 'old-pass'
    waveform(source, frequency=880.)
    second = imported(context, request='official-new')
    service.set_default(jobs, 'official', second['id'], 'official-replace')
    assert models.official(jobs)['validation']['gpu'] == 'NOT_RUN'
    put(jobs, 'model_test', 'new-fail', dict(model_version_id='official', reference_fingerprint=second['fingerprint'], created=2, outcome='FAILED'))
    assert models.official(jobs)['validation']['gpu'] == 'FAILED'
    assert service.enable(jobs, 'official', 'official-enable')['valid']


def test_media_lease_protects_owning_version_during_reference_copy(context):
    jobs, source = context
    area = jobs.root/'datasets'/'role'/'source-version'
    area.mkdir(parents=True)
    target = area/'clip.wav'
    target.write_bytes(source.read_bytes())
    put(jobs, 'version', 'source-version', dict(id='source-version', dataset_id='role', path=str(area)))
    audio_id = media(jobs, target, owner='source-version')
    lease = references.acquire(jobs, 'media', audio_id, 'active-reference-copy')
    assert not references.delete_preview(jobs, 'version', 'source-version')['deletable']
    references.release(jobs, lease)
    assert references.delete_preview(jobs, 'version', 'source-version')['deletable']


def test_corrupt_audio_reports_decode_error_without_changing_selection(context):
    jobs, source = context
    reference = imported(context)
    service.set_default(jobs, 'model-one', reference['id'], 'default')
    service.enable(jobs, 'model-one', 'enable')
    source.write_bytes(b'not audio')
    with pytest.raises(ValueError, match='REFERENCE_DECODE_FAILED'):
        imported(context, request='bad-source')
    assert source.read_bytes() == b'not audio'
    assert service.selection(jobs)['valid']
    assert service.selection(jobs)['default_reference']['id'] == reference['id']


def test_bad_upload_returns_422_and_cleans_only_incoming_file(context):
    from fastapi.testclient import TestClient
    from dictionforge.api import create_app
    from dictionforge.settings import Settings
    jobs, source = context
    original = digest(source)
    app = create_app(Settings(jobs.root))
    app.state.jobs = jobs  # No lifespan/supervisor: this test exercises CPU HTTP only.
    client = TestClient(app)
    response = client.post('/api/references/upload', params={'request_id': str(uuid.uuid4()), 'text': '参考文字'},
                           content=b'not an audio file', headers={'content-type': 'audio/wav'})
    assert response.status_code == 422
    assert 'REFERENCE_DECODE_FAILED' in response.json()['detail']
    assert len(response.json()['detail']) < 500
    assert not list((jobs.root/'uploads').glob('*.upload'))
    assert digest(source) == original and not list_records(jobs, 'model_reference')


@pytest.mark.parametrize('path', ['https://example.invalid/audio.wav', r'\\server\share\audio.wav', 'file:///C:/audio.wav'])
def test_only_local_reference_paths_are_accepted(context, path):
    jobs, source = context
    with pytest.raises(ValueError, match='REFERENCE_LOCAL_FILE_REQUIRED'):
        service.import_reference(jobs, source_path=path, text='参考', request_id='local-only')


@pytest.mark.parametrize('extension,codec', [('mp3', 'libmp3lame'), ('flac', 'flac')])
def test_local_mp3_and_flac_decode_under_protocol_whitelist(context, extension, codec):
    jobs, source = context
    target = source.with_suffix('.'+extension)
    ffmpeg('-i', source, '-c:a', codec, target)
    reference = service.import_reference(jobs, source_path=target, text='参考', request_id=extension)
    assert reference['sample_rate'] == 48000 and reference['channels'] == 1
    assert 3 <= reference['seconds'] <= 10


def test_soundfile_decode_errors_use_same_reference_error(context):
    jobs, source = context
    source.write_bytes(b'broken wav')
    reference = dict(path=str(source), sha256=digest(source), text='参考', language='zh')
    with pytest.raises(ValueError, match='REFERENCE_DECODE_FAILED'):
        service.validate_reference(reference)
    assert source.read_bytes() == b'broken wav'
