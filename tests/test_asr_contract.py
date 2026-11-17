import json
from pathlib import Path
import socket
from unittest.mock import patch

import pytest

from dictionforge import asr_worker as worker


def request(tmp_path, languages=('zh', 'zh', 'zh')):
    clips = []
    for index, language in enumerate(languages):
        path = tmp_path / f'{index}.wav'
        path.write_bytes(b'fixture-for-contract-only')
        clips.append({'id': str(index), 'path': str(path), 'language': language})
    return {'upstream': str(tmp_path), 'clips': clips}


def test_partial_errors_do_not_drop_successful_clips(tmp_path):
    payload = request(tmp_path)
    loads = []

    class Model:
        def __init__(self, **kwargs):
            loads.append(kwargs)

        def generate(self, input):
            if Path(input).stem == '1':
                raise RuntimeError('decoder failed')
            return [{'text': '真实调用边界夹具', 'score': .99}]

    with patch.object(worker, 'assets', return_value=({'model': 'local'}, {'revision': 'fixed'})):
        result = worker.run(payload, model_factory=Model)
    assert len(loads) == 1
    assert [row['error'] is None for row in result['clips']] == [True, False, True]
    assert result['clips'][1]['error']['code'] == 'ASR_FAILED'
    assert all(row['confidence'] is None for row in result['clips'])
    assert result['asset_revision'] == {'revision': 'fixed'}


def test_unsupported_and_missing_file_do_not_load_model(tmp_path):
    payload = request(tmp_path, ('en', 'zh'))
    Path(payload['clips'][1]['path']).unlink()
    with patch.object(worker, 'assets') as prepare:
        result = worker.run(payload)
    prepare.assert_not_called()
    assert [row['error']['code'] for row in result['clips']] == ['UNSUPPORTED_LANGUAGE', 'INVALID_AUDIO_PATH']


def test_missing_assets_report_each_clip_and_network_is_restored(tmp_path):
    payload = request(tmp_path)
    original = socket.getaddrinfo
    with patch.object(worker, 'assets', side_effect=worker.ASRError('MODEL_DEPENDENCY_MISSING', 'missing')) as prepare:
        result = worker.run(payload)
    assert prepare.call_count == 1
    assert all(row['error']['code'] == 'MODEL_DEPENDENCY_MISSING' for row in result['clips'])
    assert socket.getaddrinfo is original


def test_network_fallback_is_explicitly_blocked(tmp_path):
    with worker.offline(), pytest.raises(worker.ASRError, match='本地资产'):
        socket.getaddrinfo('example.com', 443)


def test_cli_publishes_result_and_rejects_duplicate_ids(tmp_path):
    payload = request(tmp_path)
    payload['clips'][1]['id'] = payload['clips'][0]['id']
    source, output = tmp_path / 'request.json', tmp_path / 'result.json'
    source.write_text(json.dumps(payload), encoding='utf-8')
    assert worker.main([str(source), str(output)]) == 1
    assert json.loads(output.read_text(encoding='utf-8'))['error']['code'] == 'INVALID_REQUEST'
    assert not list(tmp_path.glob('*.tmp'))
