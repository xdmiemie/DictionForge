"""One frozen model/reference/text request, native v4 48 kHz output, then exit."""
from __future__ import annotations

from contextlib import contextmanager
import functools
import gc
import json
import math
import os
from pathlib import Path
import socket
import sys
import time
import traceback
import uuid

from .audio import atomic_json, digest
from .feature_worker import checked_assets, init_runtime
from .settings import ROOT
from .source_lock import verify_source
from .jobs import Jobs


LANGUAGES = {'zh': 'all_zh', 'en': 'en', 'ja': 'all_ja', 'ko': 'all_ko', 'yue': 'all_yue'}
DEFAULTS = {'seed': 42, 'sample_steps': 32, 'top_k': 5, 'top_p': 1., 'temperature': 1.,
            'speed_factor': 1., 'repetition_penalty': 1.35, 'fragment_interval': .3}


class InferenceFailure(RuntimeError):
    def __init__(self, report):
        super().__init__(report['error'])
        self.report = report


def is_cuda_oom(error):
    return error.__class__.__name__ == 'OutOfMemoryError' or 'cuda out of memory' in str(error).lower()


class InferenceProgress:
    def __init__(self, request):
        self.request = request
        supplied = request.get('parameters', {})
        self.parameters = dict(DEFAULTS, **supplied) if isinstance(supplied, dict) else dict(DEFAULTS)
        self.phase = 'request_validation'
        self.gpu_resident = False
        self.events = []
        self.started = time.perf_counter()
        self.last_exception = None
        self.failure_phase = None
        self.jobs = Jobs(Path(request['data_root'])) if request.get('job_id') else None
        import psutil
        self.worker_process_start = psutil.Process(os.getpid()).create_time()

    def emit(self, stage, *, gpu_resident=None, **extra):
        if gpu_resident is not None:
            self.gpu_resident = gpu_resident
        self.phase = stage
        payload = {'stage': stage, 'model_id': self.request.get('model_id'),
                   'gpu_resident': self.gpu_resident, 'worker_pid': os.getpid(),
                   'worker_process_start': self.worker_process_start,
                   'elapsed_seconds': time.perf_counter()-self.started, **extra}
        self.events.append(payload)
        print(json.dumps({'inference_phase': payload}, ensure_ascii=False), flush=True)
        if self.jobs is not None:
            self.jobs.progress(self.request['job_id'], payload)

    def observe(self, owner, name, phase):
        original = getattr(owner, name)
        @functools.wraps(original)
        def wrapped(*args, **kwargs):
            if self.phase != phase:
                self.emit(phase)
            try:
                return original(*args, **kwargs)
            except Exception as exc:
                self.last_exception, self.failure_phase = exc, phase
                raise
        setattr(owner, name, wrapped)

    def failure(self, error):
        phase = self.failure_phase if error is self.last_exception else self.phase
        report = {'outcome': 'FAILED', 'error': str(error), 'code': 'CUDA_OOM' if is_cuda_oom(error) else type(error).__name__,
                  'phase': phase, 'model_id': self.request.get('model_id'),
                  'parameters': self.parameters, 'requested_parameters': self.request.get('parameters', {}),
                  'precision': self.request.get('precision', 'fp16'), 'parameters_changed': False,
                  'gpu_resident_at_failure': self.gpu_resident, 'worker_pid': os.getpid()}
        torch = sys.modules.get('torch')
        if torch is not None and torch.cuda.is_initialized():
            try:
                report['cuda'] = {'device': torch.cuda.get_device_name(0), 'allocated_bytes': torch.cuda.memory_allocated(),
                                  'reserved_bytes': torch.cuda.memory_reserved(), 'peak_bytes': torch.cuda.max_memory_allocated()}
            except Exception as diagnostic_error:
                report['cuda_diagnostic_error'] = str(diagnostic_error)
        try:
            self.emit('failed', failed_phase=phase, error=report['error'], code=report['code'],
                      parameters=self.parameters, precision=report['precision'], parameters_changed=False)
        except Exception as progress_error:
            report['progress_error'] = str(progress_error)
        report['phase_events'] = self.events
        return report


def assert_cuda_models(model):
    summary = {}
    for name in ('t2s_model', 'vits_model', 'bert_model', 'cnhuhbert_model', 'vocoder'):
        module = getattr(model, name, None)
        parameters = list(module.parameters()) if module is not None else []
        if not parameters or any(parameter.device.type != 'cuda' for parameter in parameters):
            raise RuntimeError(f'MODEL_DEVICE_MISMATCH: {name} has not fully loaded on CUDA')
        summary[name] = sum(parameter.numel()*parameter.element_size() for parameter in parameters)
    return summary


def observe_model(model, progress):
    progress.observe(model, 'set_ref_audio', 'reference_processing')
    progress.observe(model.text_preprocessor, 'segment_and_extract_feature_for_text', 'text_frontend')
    progress.observe(model.text_preprocessor, 'preprocess', 'text_frontend')
    progress.observe(model, 'to_batch', 'text_frontend')
    progress.observe(model.t2s_model.model, 'infer_panel_naive_batched', 'synthesizing')
    progress.observe(model, 'using_vocoder_synthesis', 'synthesizing')
    progress.observe(model, 'audio_postprocess', 'synthesizing')


def collect_native_chunks(model, inputs, progress):
    chunks = []
    generator = model.run(inputs)
    try:
        for rate, pcm in generator:
            if rate != 48000:
                # Stop on upstream's failure placeholder before its attempted
                # model reinitialization. Preserve the observed original error.
                if progress.last_exception is not None:
                    raise progress.last_exception
                raise RuntimeError('INFERENCE_NATIVE_RATE_INVALID: upstream returned a failure placeholder')
            chunks.append((rate, pcm))
    finally:
        generator.close()
    return chunks


def normalize_parameters(supplied):
    if not isinstance(supplied, dict) or set(supplied) - set(DEFAULTS):
        raise ValueError('INFERENCE_PARAMETERS_INVALID: unsupported parameter')
    result = dict(DEFAULTS, **supplied)
    for key, low, high in (('seed', 0, 2**32-1), ('top_k', 1, 100)):
        if type(result[key]) is not int or not low <= result[key] <= high:
            raise ValueError(f'INFERENCE_PARAMETERS_INVALID: {key}')
    if type(result['sample_steps']) is not int or result['sample_steps'] not in (8, 16, 32):
        raise ValueError('INFERENCE_PARAMETERS_INVALID: sample_steps must be 8, 16 or 32')
    for key, low, high in (('top_p', .01, 1.), ('temperature', .1, 2.), ('speed_factor', .5, 2.),
                           ('repetition_penalty', 1., 2.), ('fragment_interval', .01, 1.)):
        value = result[key]
        if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f'INFERENCE_PARAMETERS_INVALID: {key}')
    return result


def validate_request(request):
    data_root, output = Path(request['data_root']).resolve(), Path(request['output_dir']).resolve()
    if not output.is_relative_to(data_root) or output == data_root:
        raise ValueError('INFERENCE_OUTPUT_OUTSIDE_DATA')
    if request.get('precision', 'fp16') not in ('fp16', 'fp32'):
        raise ValueError('INFERENCE_PRECISION_INVALID')
    if request.get('language', 'zh') not in LANGUAGES:
        raise ValueError('INFERENCE_LANGUAGE_INVALID')
    text = request.get('text', '').strip()
    if not text or len(text) > 5000:
        raise ValueError('INFERENCE_TEXT_INVALID: require 1–5000 characters')
    reference = request['reference']
    if not reference.get('text', '').strip() or reference.get('language', 'zh') not in LANGUAGES:
        raise ValueError('REFERENCE_TEXT_LANGUAGE_INVALID')
    if set(request['weights']) != {'gpt', 'sovits'} or set(request['weight_hashes']) != {'gpt', 'sovits'}:
        raise ValueError('MODEL_PAIR_REQUIRED')
    for key, value in request['weights'].items():
        path = Path(value)
        if not path.is_absolute() or not path.is_file() or digest(path) != request['weight_hashes'][key]:
            raise ValueError(f'MODEL_WEIGHT_CHANGED: {key}')
    path = Path(reference['path'])
    if not path.is_absolute() or not path.is_file() or digest(path) != reference.get('sha256'):
        raise ValueError('REFERENCE_CHANGED')
    return normalize_parameters(request.get('parameters', {}))


@contextmanager
def offline():
    previous = socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo
    def deny(*_args, **_kwargs):
        raise RuntimeError('NETWORK_DISABLED: inference requires prepared, locked local assets')
    socket.socket.connect = socket.socket.connect_ex = socket.getaddrinfo = deny
    try:
        yield
    finally:
        socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo = previous


def _run(request, progress):
    started = time.perf_counter()
    progress.emit('request_validation')
    parameters = validate_request(request)
    progress.parameters = parameters
    upstream, output_dir = Path(request['upstream']).resolve(), Path(request['output_dir']).resolve()
    progress.emit('dependency_validation')
    verify_source(ROOT, upstream)
    checked_assets(upstream)
    import numpy as np
    import soundfile as sf
    import torch
    reference = dict(request['reference'])
    progress.emit('reference_validation')
    reference_info = sf.info(reference['path'])
    if reference_info.channels != 1 or not 3 <= reference_info.duration <= 10:
        raise ValueError('REFERENCE_DURATION_INVALID: require mono reference of 3–10 seconds')
    if not torch.cuda.is_available():
        raise RuntimeError('GPU_UNAVAILABLE')
    output_dir.mkdir(parents=True, exist_ok=True)
    init_runtime(upstream)
    os.environ['MODELSCOPE_OFFLINE'] = '1'
    identifier = uuid.uuid4().hex
    assets = upstream/'GPT_SoVITS/pretrained_models'
    config = {'version': 'v4', 'device': 'cuda', 'is_half': request.get('precision', 'fp16') == 'fp16',
              't2s_weights_path': request['weights']['gpt'], 'vits_weights_path': request['weights']['sovits'],
              'cnhuhbert_base_path': str(assets/'chinese-hubert-base'),
              'bert_base_path': str(assets/'chinese-roberta-wwm-ext-large')}
    inputs = dict(parameters, text=request['text'].strip(), text_lang=LANGUAGES[request.get('language', 'zh')],
                  ref_audio_path=reference['path'], prompt_text=reference['text'].strip(),
                  prompt_lang=LANGUAGES[reference.get('language', 'zh')], text_split_method='cut0',
                  batch_size=1, parallel_infer=False, split_bucket=False, return_fragment=False)
    effective = {'config': config, 'inputs': inputs, 'upstream_lock': digest(ROOT/'configs/upstream.lock.json'),
                 'asset_lock': digest(ROOT/'configs/assets.lock.json'), 'weight_hashes': request['weight_hashes'],
                 'reference_sha256': reference['sha256'], 'native_sample_rate_required': 48000}
    atomic_json(output_dir/f'inference-{identifier}.effective.json', effective)
    torch.cuda.reset_peak_memory_stats()
    progress.emit('model_loading', gpu_resident=False, parameters=parameters,
                  precision=request.get('precision', 'fp16'))
    with offline():
        from TTS_infer_pack.TTS import TTS, TTS_Config
        from process_ckpt import get_sovits_version_from_path_fast
        _, version, _ = get_sovits_version_from_path_fast(request['weights']['sovits'])
        if version != 'v4':
            raise ValueError('MODEL_VERSION_INVALID: native 48 kHz requires the v4 route')
        tts_config = TTS_Config({'version': 'v4', 'custom': config})
        tts_config.configs_path = str(output_dir/f'inference-{identifier}.effective.yaml')
        model = TTS(tts_config)
        resident_modules = assert_cuda_models(model)
        torch.cuda.synchronize()
        progress.emit('model_ready', gpu_resident=True, model_parameter_bytes=resident_modules,
                      allocated_bytes=torch.cuda.memory_allocated(), reserved_bytes=torch.cuda.memory_reserved())
        observe_model(model, progress)
        load_seconds = time.perf_counter() - started
        before = time.perf_counter()
        chunks = collect_native_chunks(model, inputs, progress)
        torch.cuda.synchronize()
        generation_seconds = time.perf_counter() - before
    if not chunks or any(rate != 48000 for rate, _ in chunks):
        raise RuntimeError('INFERENCE_NATIVE_RATE_INVALID: upstream must return 48000 Hz')
    pcm = np.concatenate([pcm for _, pcm in chunks])
    if pcm.ndim != 1 or pcm.dtype != np.int16 or not pcm.size or not np.any(pcm):
        raise RuntimeError('INFERENCE_AUDIO_INVALID: require nonempty mono native int16 PCM')
    if digest(Path(reference['path'])) != reference['sha256']:
        raise ValueError('REFERENCE_CHANGED_DURING_INFERENCE')
    progress.emit('audio_writing')
    output = output_dir/f'audio-{identifier}.wav'
    temporary = output.with_suffix('.wav.part')
    try:
        sf.write(temporary, pcm, 48000, format='WAV', subtype='PCM_16')
        with temporary.open('r+b') as stream:
            stream.flush()
            os.fsync(stream.fileno())
        info = sf.info(temporary)
        if info.samplerate != 48000 or info.channels != 1 or info.frames != len(pcm):
            raise RuntimeError('INFERENCE_FILE_INVALID')
        checksum = digest(temporary)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    audio = {'path': str(output), 'sha256': checksum, 'bytes': output.stat().st_size, 'sample_rate': 48000,
             'channels': 1, 'samples': len(pcm), 'seconds': len(pcm)/48000,
             'peak': float(np.abs(pcm.astype(np.float64)/32768).max()),
             'rms': float(np.sqrt(np.mean((pcm.astype(np.float64)/32768)**2)))}
    progress.emit('audio_written', audio_sha256=checksum, sample_rate=48000, samples=len(pcm))
    result = {'outcome': 'COMPLETED', 'model_id': request.get('model_id'), 'audio': audio,
              'weights': request['weights'], 'weight_hashes': request['weight_hashes'], 'reference': reference,
              'text': request['text'].strip(), 'language': request.get('language', 'zh'),
              'parameters': parameters, 'effective': effective, 'phase_events': progress.events,
              'metrics': {'generation_seconds': generation_seconds, 'load_seconds': load_seconds,
                          'total_seconds': time.perf_counter()-started,
                          'peak_cuda_bytes': torch.cuda.max_memory_allocated(),
                          'peak_reserved_bytes': torch.cuda.max_memory_reserved(), 'device': torch.cuda.get_device_name(0)}}
    del model
    gc.collect()
    torch.cuda.empty_cache()
    return result


def run(request):
    progress = InferenceProgress(request)
    try:
        return _run(request, progress)
    except Exception as exc:
        raise InferenceFailure(progress.failure(exc)) from exc


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        raise SystemExit('usage: python -m dictionforge.inference_worker request.json result.json')
    source, output = [Path(value).resolve() for value in args]
    try:
        result = run(json.loads(source.read_text(encoding='utf-8-sig')))
    except Exception as exc:
        traceback.print_exc()
        failure = exc.report if isinstance(exc, InferenceFailure) else {
            'outcome': 'FAILED', 'error': str(exc), 'code': type(exc).__name__, 'phase': 'request_loading'}
        atomic_json(output, failure)
        return 1
    atomic_json(output, result)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
