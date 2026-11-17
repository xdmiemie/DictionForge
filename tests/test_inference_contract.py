import pytest
from dictionforge.audio import digest
from dictionforge.inference_worker import normalize_parameters, validate_request


def test_inference_parameters_are_explicit_and_validated():
    values = normalize_parameters({})
    assert values['seed'] == 42 and values['sample_steps'] == 32
    for supplied in ({'seed': -1}, {'sample_steps': 0}, {'speed_factor': float('nan')}, {'batch_size': 2}, {'fragment_interval': 0}):
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


def test_progress_reports_actual_phase_and_preserves_parameters_on_oom(tmp_path):
    from dictionforge.jobs import Jobs
    from dictionforge.inference_worker import InferenceProgress
    store = Jobs(tmp_path)
    job = store.submit('model_probe', {}, 'phase-contract')
    supplied = {'sample_steps': 32, 'speed_factor': 1.25}
    progress = InferenceProgress({'data_root': str(tmp_path), 'job_id': job['id'], 'model_id': 'fixture',
                                  'parameters': dict(supplied), 'precision': 'fp16'})
    progress.emit('model_loading')
    assert not store.events(job['id'], 0)[-1]['payload']['gpu_resident']
    # Contract fixture only: no real CUDA model is created by this test.
    progress.emit('model_ready', gpu_resident=True)
    class OutOfMemoryError(RuntimeError):
        pass
    class Frontend:
        def work(self):
            raise OutOfMemoryError('controlled CUDA out of memory')
    frontend = Frontend()
    progress.observe(frontend, 'work', 'text_frontend')
    with pytest.raises(OutOfMemoryError) as failure:
        frontend.work()
    report = progress.failure(failure.value)
    assert report['code'] == 'CUDA_OOM' and report['phase'] == 'text_frontend'
    assert report['requested_parameters'] == supplied
    assert report['parameters']['speed_factor'] == 1.25 and report['precision'] == 'fp16'
    assert report['parameters_changed'] is False and report['gpu_resident_at_failure'] is True
    assert store.events(job['id'], 0)[-1]['payload']['failed_phase'] == 'text_frontend'


def test_failure_placeholder_does_not_trigger_upstream_model_reload():
    from types import SimpleNamespace
    from dictionforge.inference_worker import collect_native_chunks
    error = RuntimeError('controlled CUDA out of memory')
    observed = []
    class Model:
        def run(self, _inputs):
            try:
                yield 16000, b'placeholder'
                observed.append('unwanted-reload')
            finally:
                observed.append('closed')
    with pytest.raises(RuntimeError, match='out of memory') as caught:
        collect_native_chunks(Model(), {}, SimpleNamespace(last_exception=error))
    assert caught.value is error
    assert observed == ['closed']


def test_owned_gpu_probe_plans_are_bounded_without_allocating():
    import importlib.util
    from pathlib import Path
    path = Path(__file__).parents[1]/'scripts/gpu_occupancy_probe.py'
    spec = importlib.util.spec_from_file_location('gpu_probe_contract', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    total = 16 * 1024**3
    assert module.allocation_plan('hold', total, 512) == 512 * 1024**2
    assert module.allocation_plan('oom', total) == 4 * total
    with pytest.raises(ValueError):
        module.allocation_plan('hold', total, 16384)
    with pytest.raises(ValueError):
        module.managed_path(Path(__file__).parents[1]/'docs/no-probe-write.json')
