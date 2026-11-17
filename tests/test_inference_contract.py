import pytest
from dictionforge.audio import digest
from dictionforge.inference_worker import normalize_parameters, validate_request


def test_inference_parameters_are_explicit_and_validated():
    values = normalize_parameters({})
    assert values['seed'] == 42 and values['sample_steps'] == 32
    for supplied in ({'seed': -1}, {'sample_steps': 0}, {'speed_factor': float('nan')}, {'batch_size': 2}):
        with pytest.raises(ValueError, match='INFERENCE_PARAMETERS_INVALID'):
            normalize_parameters(supplied)


def test_inference_requires_unchanged_pair_and_reference(tmp_path):
    weights = {}
    for key in ('gpt', 'sovits'):
        path = tmp_path/key
        path.write_bytes(key.encode())
        weights[key] = str(path)
    reference = tmp_path/'reference.wav'
    reference.write_bytes(b'reference fixture; validation checks identity before decoding')
    request = {'data_root': str(tmp_path), 'output_dir': str(tmp_path/'runs/probe'),
               'weights': weights, 'weight_hashes': {key: digest(tmp_path/key) for key in weights},
               'reference': {'path': str(reference), 'sha256': digest(reference), 'text': '你好。', 'language': 'zh'},
               'text': '欢迎来到声句工坊。'}
    assert validate_request(request)['seed'] == 42
    with pytest.raises(ValueError, match='OUTPUT_OUTSIDE_DATA'):
        validate_request(dict(request, output_dir=str(tmp_path.parent)))
    reference.write_bytes(b'changed')
    with pytest.raises(ValueError, match='REFERENCE_CHANGED'):
        validate_request(request)
