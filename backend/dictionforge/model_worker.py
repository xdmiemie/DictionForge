"""CPU-only trusted local weight structure validation; never claims GPU inference."""
import gc
import io
import json
import os
from pathlib import Path
import sys
import traceback

from .audio import atomic_json, digest
from .feature_worker import init_runtime
from .models import verify_artifact
from .settings import ROOT
from .source_lock import verify_source


class HeaderReader(io.BufferedReader):
    """Read the upstream 04 header as PK without mutating or duplicating the file."""
    def read(self, size=-1):
        start = self.tell()
        data = super().read(size)
        if start < 2:
            prefix = b'PK'[start:min(2, start+len(data))]
            data = prefix + data[len(prefix):]
        return data

    def readinto(self, buffer):
        value = self.read(len(buffer))
        buffer[:len(value)] = value
        return len(value)


def load_package(path):
    import torch
    with Path(path).open('rb') as stream:
        header = stream.read(2)
    if header == b'04':
        with HeaderReader(io.FileIO(path, 'r')) as stream:
            return torch.load(stream, map_location='cpu', weights_only=False)
    if header != b'PK':
        raise ValueError('MODEL_FORMAT_INVALID: expected GPT zip or v4 04 header')
    return torch.load(path, map_location='cpu', weights_only=False)


def shapes(weights):
    import torch
    if not isinstance(weights, dict) or not weights:
        raise ValueError('MODEL_WEIGHT_EMPTY')
    result = {}
    for key, value in weights.items():
        if not isinstance(key, str) or not torch.is_tensor(value):
            raise ValueError('MODEL_WEIGHT_INVALID')
        if value.is_floating_point() and not torch.isfinite(value).all().item():
            raise ValueError(f'MODEL_NONFINITE: {key}')
        result[key] = tuple(value.shape)
    return result


def validate_gpt(path, official_path):
    base = load_package(official_path)
    expected, model_config = shapes(base['weight']), base['config']['model']
    del base
    gc.collect()
    package = load_package(path)
    actual = shapes(package['weight'])
    if actual != expected or package.get('config', {}).get('model') != model_config:
        raise ValueError('GPT_ARCHITECTURE_MISMATCH: tensor keys/shapes or model config differ from v4 GPT')
    result = {'tensors': len(actual), 'architecture': model_config, 'precision': sorted({str(v.dtype) for v in package['weight'].values()})}
    del package
    gc.collect()
    return result


def validate_sovits(path, base_path):
    import torch
    from module.models import SynthesizerTrnV3
    from peft import LoraConfig, get_peft_model
    with Path(path).open('rb') as stream:
        is_lora = stream.read(2) == b'04'
    package = load_package(path)
    actual = shapes(package['weight'])
    rank = package.get('lora_rank')
    if is_lora and (type(rank) is not int or rank not in {8, 16, 32, 64, 128}):
        raise ValueError('LORA_RANK_INVALID')
    base = load_package(base_path)
    base_config = base['config']
    if not is_lora:
        # Upstream v4 base recognition is by the pinned official weight bytes.
        if digest(Path(path)) != digest(Path(base_path)) or actual != shapes(base['weight']):
            raise ValueError('SOVITS_V4_REQUIRED: full weights must be the pinned official v4 base')
        result = {'format': 'v4_base', 'lora_rank': None, 'tensors': len(actual)}
    else:
        config = package.get('config', {})
        if config.get('model', {}).get('version') != 'v4' or config.get('model', {}).get('semantic_frame_rate') != '25hz':
            raise ValueError('SOVITS_V4_CONFIG_INVALID')
        # Construct the pinned architecture on meta tensors to validate every
        # trainable branch and buffer without allocating a second real model.
        model_options = dict(base_config['model'], version='v4', semantic_frame_rate='25hz')
        if any(config['model'].get(key) != value for key, value in model_options.items()):
            raise ValueError('SOVITS_ARCHITECTURE_CONFIG_MISMATCH')
        for key in ('filter_length', 'hop_length', 'win_length', 'sampling_rate', 'n_speakers'):
            if config.get('data', {}).get(key) != base_config['data'].get(key):
                raise ValueError(f'SOVITS_AUDIO_CONFIG_MISMATCH: {key}')
        if config.get('train', {}).get('segment_size') != base_config['train'].get('segment_size'):
            raise ValueError('SOVITS_SEGMENT_CONFIG_MISMATCH')
        with torch.device('meta'):
            model = SynthesizerTrnV3(base_config['data']['filter_length']//2+1,
                base_config['train']['segment_size']//base_config['data']['hop_length'],
                n_speakers=base_config['data']['n_speakers'], **model_options)
            model.cfm = get_peft_model(model.cfm, LoraConfig(target_modules=['to_k', 'to_q', 'to_v', 'to_out.0'],
                r=rank, lora_alpha=rank, init_lora_weights=False))
        frozen = {key for key, parameter in model.named_parameters() if not parameter.requires_grad}
        expected = {key: tuple(value.shape) for key, value in model.state_dict().items() if key not in frozen and 'enc_q' not in key}
        if actual != expected:
            missing = sorted(set(expected)-set(actual))[:5]
            extra = sorted(set(actual)-set(expected))[:5]
            wrong = [key for key in expected.keys() & actual.keys() if expected[key] != actual[key]][:5]
            raise ValueError(f'LORA_STRUCTURE_INVALID: missing={missing}, extra={extra}, shape={wrong}')
        result = {'format': 'v4_lora', 'lora_rank': rank, 'tensors': len(actual), 'base_required': True}
        del model
    del base, package
    gc.collect()
    return result


def validate(request):
    os.environ['CUDA_VISIBLE_DEVICES'] = '-1'
    import torch
    torch.set_num_threads(2)
    upstream = Path(request['upstream'])
    verify_source(ROOT, upstream)
    for name, filename in [('upstream', 'upstream.lock.json'), ('assets', 'assets.lock.json')]:
        if request['source_locks'][name] != digest(ROOT/'configs'/filename):
            raise ValueError('MODEL_SOURCE_LOCK_CHANGED')
    for item in [*request['weights'].values(), *request['dependencies']]:
        verify_artifact(item)
    def dependency(suffix):
        return next(item['path'] for item in request['dependencies'] if item['path'].replace('\\', '/').endswith(suffix))
    init_runtime(upstream)
    gpt = validate_gpt(request['weights']['gpt']['path'], dependency('/s1v3.ckpt'))
    sovits = validate_sovits(request['weights']['sovits']['path'], dependency('/s2Gv4.pth'))
    if torch.cuda.is_initialized():
        raise RuntimeError('CPU_VALIDATOR_INITIALIZED_CUDA')
    return dict(structure='PASS', gpu='NOT_RUN', device='cpu', torch=torch.__version__, gpt=gpt, sovits=sovits,
                sample_rate=48000, super_sampling=False)


def main():
    request_path, result_path = [Path(value).resolve() for value in sys.argv[1:3]]
    try:
        result = validate(json.loads(request_path.read_text(encoding='utf-8')))
    except Exception as exc:
        traceback.print_exc()
        atomic_json(result_path, {'outcome': 'FAILED', 'structure': 'FAILED', 'gpu': 'NOT_RUN', 'error': str(exc)})
        raise SystemExit(1)
    atomic_json(result_path, result)


if __name__ == '__main__':
    main()
