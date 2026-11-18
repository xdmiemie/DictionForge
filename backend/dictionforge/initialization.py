"""Immutable initialization pairs for fresh training, separate from full resume."""
import copy
from pathlib import Path
from . import models
from .audio import digest
from .jobs import Conflict
from .settings import ROOT
from .training_config import config_fingerprint


def freeze(jobs, config):
    model = models.resolve(jobs, config['initial_model'], check_reference=False)
    dependencies = model['dependencies']
    base = next(item for item in dependencies if item['path'].replace('\\', '/').endswith('/s2Gv4.pth'))
    same_base = model['weights']['sovits']['sha256'] == base['sha256']
    spec = model.get('validation', {}).get('sovits', {})
    if not same_base and spec.get('format') != 'v4_lora':
        raise ValueError('MODEL_INITIALIZATION_INVALID：需要已验证的v4 LoRA或锁定底模')
    rank = None if same_base else spec['lora_rank']
    if rank and 's2' in config['stages'] and config['s2']['rank'] != rank:
        raise ValueError(f'MODEL_RANK_MISMATCH：来源模型需要rank={rank}')
    result = copy.deepcopy(dict(model_version_id=model['id'], weights=model['weights'], base=base,
        dependencies=dependencies, s2_format='v4_base' if same_base else 'v4_lora', lora_rank=rank,
        source_locks={'upstream': digest(ROOT/'configs/upstream.lock.json'),
                      'assets': digest(ROOT/'configs/assets.lock.json')}))
    result['fingerprint'] = config_fingerprint(result)
    return result


def verify(value, config=None):
    if config_fingerprint({k: v for k, v in value.items() if k != 'fingerprint'}) != value['fingerprint']:
        raise Conflict('INITIALIZATION_FINGERPRINT_CHANGED')
    if config and value['model_version_id'] != config['initial_model']:
        raise Conflict('INITIALIZATION_MODEL_CHANGED')
    if config and value.get('lora_rank') and 's2' in config['stages'] and config['s2']['rank'] != value['lora_rank']:
        raise Conflict('MODEL_RANK_MISMATCH')
    for item in [*value['weights'].values(), value['base'], *value['dependencies']]:
        models.verify_artifact(item)
    for name, filename in [('upstream', 'upstream.lock.json'), ('assets', 'assets.lock.json')]:
        if digest(ROOT/'configs'/filename) != value['source_locks'][name]:
            raise Conflict('INITIALIZATION_SOURCE_LOCK_CHANGED')
    return value


def loaded_weights_report(model, weights):
    """Compare the actual loaded tensors to the package, without GPU copies."""
    import hashlib
    import torch
    actual = model.state_dict()
    reference_hash, actual_hash = hashlib.sha256(), hashlib.sha256()
    for key in sorted(weights):
        reference = weights[key].detach().float().cpu().contiguous()
        loaded = actual[key].detach().float().cpu().contiguous()
        if reference.shape != loaded.shape or not torch.equal(reference, loaded):
            raise ValueError(f'INITIALIZATION_TENSOR_MISMATCH: {key}')
        reference_hash.update(key.encode()); reference_hash.update(reference.numpy().tobytes())
        actual_hash.update(key.encode()); actual_hash.update(loaded.numpy().tobytes())
    return dict(tensors=len(weights), source_tensor_sha256=reference_hash.hexdigest(),
                loaded_tensor_sha256=actual_hash.hexdigest(), all_tensors_equal=True)
