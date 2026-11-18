"""Real single-GPU S2 -> S1 fine tuning with committed boundaries and snapshots."""
from __future__ import annotations

import copy
import gc
import hashlib
import json
import math
import os
from pathlib import Path
import random
import shutil
import sys
import time
import traceback
import uuid

from .audio import atomic_json, digest
from .feature_worker import init_runtime, build_loaders, checked_assets
from .jobs import Jobs
from .settings import ROOT
from .training_config import config_fingerprint, feature_fingerprint
from .source_lock import verify_source


def cycles(order, accumulation, cursor=0):
    if accumulation < 1 or not 0 <= cursor <= len(order):
        raise ValueError('invalid accumulation/cursor')
    while cursor < len(order):
        end = min(cursor + accumulation, len(order))
        yield cursor, end, order[cursor:end]
        cursor = end


def epoch_order(loader, stage, epoch):
    sampler = loader.sampler if stage == 's1' else loader.batch_sampler
    sampler.set_epoch(epoch)
    if stage == 's1':
        indices = list(sampler)
        return [indices[i:i + loader.batch_size] for i in range(0, len(indices), loader.batch_size)]
    return [list(batch) for batch in sampler]


def finalize_effective_config(effective, stage, config, seed):
    """Separate application loop settings from unused upstream script defaults."""
    ignored = effective.setdefault('ignored_template_fields', {})
    fields = {'train': ['log_interval', 'eval_interval', 'save_every_n_epoch', 'gradient_clip'],
              'data': ['max_eval_sample']}
    if stage == 's1':
        fields['optimizer'] = ['lr_init', 'lr_end', 'warmup_steps', 'decay_steps']
    else:
        fields['train'] += ['init_lr_ratio', 'warmup_epochs', 'c_mel', 'c_kl']
    for section, names in fields.items():
        for name in names:
            if name in effective.get(section, {}):
                ignored[f'{section}.{name}'] = effective[section].pop(name)
    effective['train'].update(seed=seed, stop_mode=config['stop_mode'], target=config['target'],
        epochs=config['target'] if config['stop_mode'] == 'epochs' else None,
        max_steps=config['target'] if config['stop_mode'] == 'steps' else None,
        learning_rate_policy='constant' if stage == 's1' else 'exponential_at_completed_epoch',
        gradient_clip_policy='ScaledAdam_internal_clipping_scale_2' if stage == 's1' else 'none')
    effective['data']['num_workers'] = 0
    effective['checkpoint_policy'] = {'interval_optimizer_updates': 100, 'periodic_keep': 3,
                                     'protected_kinds': ['pause', 'export', 'stage_final', 'stop_export'],
                                     'immutable_snapshot_per_checkpoint': True}
    effective['training_loop'] = 'DictionForge application loop; upstream model, loss, optimizer and dataset'
    return effective


def to_cpu(value):
    import torch
    if torch.is_tensor(value):
        return value.detach().cpu()
    if isinstance(value, dict):
        return {key: to_cpu(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(to_cpu(item) for item in value)
    return value


def rng_state():
    import numpy as np
    import torch
    return {'python': random.getstate(), 'numpy': np.random.get_state(), 'torch_cpu': torch.get_rng_state(),
            'torch_cuda': torch.cuda.get_rng_state_all()}


def restore_rng(value):
    import numpy as np
    import torch
    random.setstate(value['python'])
    np.random.set_state(value['numpy'])
    torch.set_rng_state(value['torch_cpu'])
    if len(value['torch_cuda']) != torch.cuda.device_count():
        raise ValueError('RESUME_DEVICE_MISMATCH: CUDA RNG device count differs')
    torch.cuda.set_rng_state_all(value['torch_cuda'])


def target_progress(metrics, config):
    return metrics['optimizer_updates'] if config['stop_mode'] == 'steps' else metrics['completed_epochs']


def estimate_payload_bytes(value):
    import torch
    def size(item):
        if torch.is_tensor(item):
            return item.numel() * item.element_size()
        if isinstance(item, dict):
            return sum(size(part) for part in item.values())
        if isinstance(item, (list, tuple)):
            return sum(size(part) for part in item)
        return 0
    return math.ceil(size(value) * 1.1) + 16 * 1024**2


def parameter_hash(model):
    import torch
    digest_value = hashlib.sha256()
    for name, parameter in model.named_parameters():
        if parameter.requires_grad:
            if not torch.isfinite(parameter).all().item():
                raise FloatingPointError(f'NONFINITE_PARAMETER: {name}')
            digest_value.update(name.encode())
            digest_value.update(parameter.detach().float().cpu().contiguous().numpy().tobytes())
    return digest_value.hexdigest()


def save_torch(path, payload, *, v4=False):
    import torch
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f'IMMUTABLE_ARTIFACT_EXISTS: {path}')
    required = estimate_payload_bytes(payload) + 2 * 1024**3
    if shutil.disk_usage(path.parent).free < required:
        raise OSError(f'INSUFFICIENT_DISK: need payload estimate plus 2 GiB reserve ({required} bytes)')
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.part')
    try:
        with temporary.open('w+b') as stream:
            torch.save(payload, stream)
            if v4:
                stream.seek(0)
                stream.write(b'04')
            stream.flush()
            os.fsync(stream.fileno())
        if v4:
            from process_ckpt import load_sovits_new
            check = load_sovits_new(str(temporary))
        else:
            check = torch.load(temporary, map_location='cpu', weights_only=False)
        if set(payload) != set(check):
            raise ValueError('CHECKPOINT_INVALID: verification keys differ')
        del check
        checksum = digest(temporary)
        os.replace(temporary, path)
        return {'path': str(path), 'sha256': checksum, 'bytes': path.stat().st_size}
    finally:
        temporary.unlink(missing_ok=True)


def save_checkpoint(request, stage, model, optimizer, scheduler, scaler, effective, metrics, order, report,
                    stage_state, implementation, kind):
    if any(parameter.grad is not None for parameter in model.parameters()):
        raise ValueError('CHECKPOINT_UNSAFE_BOUNDARY: gradients have not been cleared')
    boundary = dict(metrics, grads_clear=True, accumulation_pending=0)
    identifier = f"{stage}-u{metrics['optimizer_updates']}-{uuid.uuid4().hex[:12]}"
    package = implementation.inference_package(model, effective, metrics)
    snapshot_path = Path(request['run_dir'])/'snapshots'/identifier/('gpt.ckpt' if stage == 's1' else 'sovits.pth')
    snapshot = save_torch(snapshot_path, package, v4=stage == 's2')
    del package
    saved_stages = copy.deepcopy(stage_state)
    saved_stages['stages'][stage] = dict(boundary, snapshot=snapshot)
    payload = dict(schema_version=1, run_id=request['run_id'], stage=stage, config=request['config'],
        config_fingerprint=config_fingerprint(request['config']), version_fingerprint=request['version']['fingerprint'],
        features_manifest_sha256=request['preflight']['features_manifest_sha256'],
        features_content_sha256=request['preflight'].get('features_content_sha256'),
        upstream_lock=digest(ROOT/'configs/upstream.lock.json'), asset_lock=digest(ROOT/'configs/assets.lock.json'),
        initial_weights={key: {'path': value, 'sha256': digest(Path(value))} for key, value in request['initial_weights'].items()},
        model=to_cpu(model.state_dict()), optimizer=to_cpu(optimizer.state_dict()), scheduler=to_cpu(scheduler.state_dict()),
        scaler=to_cpu(scaler.state_dict()), rng=rng_state(), effective=effective, metrics=boundary,
        run_stage_state=saved_stages, snapshot=snapshot, checkpoint_kind=kind,
        batch_order=order, dataset_names=report['dataset_names'], trainable_keys=[k for k,p in model.named_parameters() if p.requires_grad],
        dataloader={'num_workers': 0, 'mode': 'direct_frozen_batch_indices', 'generator_state': None})
    area = Path(request['run_dir']) / 'checkpoints'
    area.mkdir(parents=True, exist_ok=True)
    path = area / (identifier + '.pt')
    entry = save_torch(path, payload)
    del payload
    entry.update(stage=stage, metrics=boundary, kind=kind, id=path.stem, snapshot=snapshot)
    index_path = area / 'index.json'
    index = json.loads(index_path.read_text(encoding='utf-8')) if index_path.exists() else {'checkpoints': []}
    index['checkpoints'].append(entry)
    index['last'] = entry['id']
    atomic_json(index_path, index)
    if sum(point.get('kind') == 'periodic' for point in index['checkpoints']) > 3:
        from .checkpoints import apply_retention
        try:
            retained = apply_retention(Jobs(Path(request['data_root'])), request['run_id'], keep_recent=3)
            entry['retention'] = {name: retained.get(name, []) for name in ('removed', 'orphan_files')}
        except Exception as exc:
            # A validated checkpoint remains usable if optional reclamation fails.
            # Retain files and report the failure; never roll back the durable last.
            entry['retention_warning'] = str(exc)
    return entry


def verify_artifact(entry, name):
    path = Path(entry['path'])
    if not path.is_file() or path.suffix == '.part' or digest(path) != entry['sha256']:
        raise ValueError(f'RESUME_ARTIFACT_CHANGED: {name}')
    if entry.get('bytes') is not None and path.stat().st_size != entry['bytes']:
        raise ValueError(f'RESUME_ARTIFACT_SIZE_CHANGED: {name}')


def resume_source_run(request):
    source = request.get('resume_source_run') or request.get('parent_run') or request['run_id']
    if source not in {request['run_id'], request.get('parent_run')}:
        raise ValueError('RESUME_SOURCE_RUN_MISMATCH')
    return source


def validate_resume_contract(request, entry, checkpoint):
    source_run = resume_source_run(request)
    if checkpoint['run_id'] != source_run or checkpoint['stage'] != entry['stage']:
        raise ValueError('RESUME_IDENTITY_MISMATCH')
    if source_run == request.get('parent_run') and request.get('parent_checkpoint') != entry['id']:
        raise ValueError('RESUME_PARENT_CHECKPOINT_MISMATCH')
    source_area = Path(request['data_root'])/'runs'/source_run/'checkpoints'
    if not Path(entry['path']).resolve().is_relative_to(source_area.resolve()):
        raise ValueError('RESUME_PATH_OUTSIDE_RUN')
    if checkpoint['stage'] not in request['config']['stages']:
        raise ValueError('RESUME_STAGE_NOT_ENABLED')
    expected = {'config_fingerprint': config_fingerprint(request['config']),
                'version_fingerprint': request['version']['fingerprint'],
                'upstream_lock': digest(ROOT/'configs/upstream.lock.json'),
                'asset_lock': digest(ROOT/'configs/assets.lock.json')}
    if checkpoint.get('features_content_sha256') and request['preflight'].get('features_content_sha256'):
        expected['features_content_sha256'] = request['preflight']['features_content_sha256']
    else:
        expected['features_manifest_sha256'] = request['preflight']['features_manifest_sha256']
    for name, value in expected.items():
        if checkpoint.get(name) != value:
            raise ValueError(f'RESUME_FINGERPRINT_MISMATCH: {name}')
    if config_fingerprint(checkpoint['config']) != checkpoint['config_fingerprint']:
        raise ValueError('RESUME_CONFIG_CORRUPT')
    if {name: item['sha256'] for name, item in checkpoint['initial_weights'].items()} != request['initial_hashes']:
        raise ValueError('RESUME_INITIAL_WEIGHTS_MISMATCH')
    required = {'model', 'optimizer', 'scheduler', 'scaler', 'rng', 'effective', 'metrics',
                'batch_order', 'dataset_names', 'trainable_keys', 'dataloader'}
    if required - set(checkpoint):
        raise ValueError('RESUME_INCOMPLETE_STATE')
    metrics = checkpoint['metrics']
    if not metrics.get('grads_clear') or metrics.get('accumulation_pending') != 0:
        raise ValueError('RESUME_UNSAFE_BOUNDARY')
    if checkpoint['dataloader']['num_workers'] != 0 or checkpoint['dataloader']['mode'] != 'direct_frozen_batch_indices':
        raise ValueError('RESUME_UNSUPPORTED_DATALOADER')
    order = checkpoint['batch_order']
    if not order or not 0 <= metrics['batch_cursor'] <= len(order):
        raise ValueError('RESUME_CURSOR_INVALID')
    if metrics.get('batch_order_sha256') != config_fingerprint(order):
        raise ValueError('RESUME_BATCH_ORDER_CHANGED')


def load_resume(request):
    import torch
    entry = request.get('resume_checkpoint')
    if not entry:
        return None
    source_run = resume_source_run(request)
    source_area = Path(request['data_root'])/'runs'/source_run/'checkpoints'
    if not Path(entry['path']).resolve().is_relative_to(source_area.resolve()):
        raise ValueError('RESUME_PATH_OUTSIDE_RUN')
    verify_artifact(entry, 'checkpoint')
    checkpoint = torch.load(entry['path'], map_location='cpu', weights_only=False)
    validate_resume_contract(request, entry, checkpoint)
    snapshot = checkpoint.get('snapshot') or entry.get('snapshot')
    if snapshot:
        verify_artifact(snapshot, 'snapshot')
        checkpoint['snapshot'] = snapshot
    checkpoint['_entry'] = entry
    return checkpoint


def resumed_stage_state(request, checkpoint, state):
    """Only inherit stages before the selected historical point; never future work."""
    saved = checkpoint.get('run_stage_state')
    if saved is None:
        # P05 stage-final files predate embedded handoff state; their source run
        # still provides the preceding completed stage. Future stages are ignored.
        source = resume_source_run(request)
        path = Path(request['data_root'])/'runs'/source/'stage_state.json'
        saved = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {'stages': {}}
    for stage in ('s2', 's1'):
        if stage == checkpoint['stage']:
            break
        if stage not in request['config']['stages']:
            continue
        prior = saved['stages'].get(stage, {})
        if prior.get('state') != 'COMPLETED' or not prior.get('checkpoint') or not prior.get('snapshot'):
            raise ValueError(f'RESUME_HANDOFF_INCOMPLETE: {stage}')
        verify_artifact(prior['checkpoint'], f'{stage} checkpoint')
        verify_artifact(prior['snapshot'], f'{stage} snapshot')
        state['stages'][stage] = copy.deepcopy(prior)
    current = checkpoint['stage']
    state['active_stage'] = current
    state['stages'][current] = dict(checkpoint['metrics'], checkpoint=checkpoint['_entry'],
                                    snapshot=checkpoint.get('snapshot'))
    return state


def available_weights(request, state):
    """Pair every current/preceding snapshot with the frozen unstarted side."""
    weights = dict(request['initial_weights'])
    for stage, key in (('s2', 'sovits'), ('s1', 'gpt')):
        snapshot = state['stages'][stage].get('snapshot')
        if snapshot:
            weights[key] = snapshot['path']
    return weights


def export_intent_path(request, request_id):
    identifier = hashlib.sha256(request_id.encode('utf-8')).hexdigest()
    return Path(request['run_dir'])/'export_intents'/(identifier + '.json')


def _enqueue_export(request, jobs, state, reason, request_id, checkpoint_id):
    """Persist an immutable intent before queue registration; retries never refreeze."""
    from .exports import enqueue_snapshot
    path = export_intent_path(request, request_id)
    if path.is_file():
        intent = json.loads(path.read_text(encoding='utf-8'))
        if intent['run_id'] != request['run_id'] or intent['request_id'] != request_id or intent['reason'] != reason:
            raise ValueError('EXPORT_INTENT_IDENTITY_MISMATCH')
    else:
        weights = available_weights(request, state)
        intent = {'schema_version': 1, 'run_id': request['run_id'], 'request_id': request_id,
                  'reason': reason, 'checkpoint_id': checkpoint_id, 'stage_state': copy.deepcopy(state),
                  'weights': weights, 'weight_hashes': {key: digest(Path(value)) for key, value in weights.items()},
                  'state': 'PENDING'}
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(path, intent)
    try:
        if any(digest(Path(value)) != intent['weight_hashes'][key] for key, value in intent['weights'].items()):
            raise ValueError('EXPORT_INTENT_WEIGHTS_CHANGED')
        job = enqueue_snapshot(jobs, request, intent['stage_state'], intent['weights'],
                               intent['reason'], intent['request_id'], checkpoint_id=intent['checkpoint_id'])
    except Exception as exc:
        intent.update(state='FAILED', error=str(exc))
        atomic_json(path, intent)
        return {'ok': False, 'intent_id': path.stem, 'intent_path': str(path), 'error': str(exc),
                'checkpoint_id': intent['checkpoint_id'], 'request_id': request_id}
    intent.update(state='QUEUED', job_id=job['id'])
    intent.pop('error', None)
    result = {'ok': True, 'intent_id': path.stem, 'intent_path': str(path), 'job_id': job['id'],
              'checkpoint_id': intent['checkpoint_id'], 'request_id': request_id}
    try:
        atomic_json(path, intent)
    except OSError as exc:
        # Queue registration already committed; an intent status update cannot
        # turn the registered export into a training failure or duplicate job.
        result['intent_status_warning'] = str(exc)
    return result


def enqueue_export(request, jobs, state, reason, request_id, checkpoint_id):
    try:
        return _enqueue_export(request, jobs, state, reason, request_id, checkpoint_id)
    except Exception as exc:
        path = export_intent_path(request, request_id)
        return {'ok': False, 'intent_id': path.stem, 'intent_path': str(path), 'request_id': request_id,
                'checkpoint_id': checkpoint_id, 'error': str(exc)}


def export_intent_results(request):
    fields = ('run_id', 'request_id', 'reason', 'checkpoint_id', 'state', 'error', 'job_id')
    result = []
    for path in sorted((Path(request['run_dir'])/'export_intents').glob('*.json')):
        value = json.loads(path.read_text(encoding='utf-8'))
        result.append(dict({key: value[key] for key in fields if key in value}, intent_id=path.stem, intent_path=str(path)))
    return result


def train_stage(request, stage, loader, report, emit, stage_state, resume=None):
    import numpy as np
    import torch
    from torch.utils.data import DataLoader
    from . import s1_training, s2_training

    implementation = s1_training if stage == 's1' else s2_training
    config = request['config'][stage]
    initial = request['initial_weights']['gpt' if stage == 's1' else 'sovits']
    seed = request['config']['seed']
    random.seed(seed); np.random.seed(seed); torch.manual_seed(seed); torch.cuda.manual_seed_all(seed)
    model, optimizer, scheduler, effective = implementation.create(config, initial, request['run_dir'])
    effective['data']['exp_dir'] = str(Path(request['data_root'])/'features'/request['version']['id'])
    finalize_effective_config(effective, stage, config, seed)
    effective['train_semantic_path'] = str(Path(effective['data']['exp_dir'])/'6-name2semantic.tsv')
    effective['train_phoneme_path'] = str(Path(effective['data']['exp_dir'])/'2-name2text.txt')
    initial_scale = 128.0 if stage == 's1' else 65536.0
    effective['train']['scaler_initial_scale'] = initial_scale
    atomic_json(Path(request['run_dir']) / f'{stage}.effective.json', effective)
    if stage == 's1':
        import yaml
        path = Path(request['run_dir']) / 's1.effective.yaml'
        temporary = path.with_suffix('.yaml.part')
        with temporary.open('w', encoding='utf-8') as stream:
            yaml.safe_dump(effective, stream, allow_unicode=True, sort_keys=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    scaler = torch.amp.GradScaler('cuda', init_scale=initial_scale, enabled=config['precision'] == 'fp16')
    metrics = dict(state='RUNNING', epoch=0, completed_epochs=0, batch_cursor=0, processed_batches=0,
        optimizer_updates=0, skipped_updates=0, initial_parameters_sha256=parameter_hash(model),
        trainable_parameters=sum(p.numel() for p in model.parameters() if p.requires_grad),
        physical_batch_size=config['batch_size'], precision=config['precision'], accumulation=config.get('accumulate', 1),
        stage=stage, target=config['target'], unit='optimizer_updates' if config['stop_mode'] == 'steps' else 'epochs')
    if resume is None:
        atomic_json(Path(request['run_dir']) / f'{stage}.initialization.json', dict(
            model_version_id=request['config'].get('initial_model', 'official'),
            frozen=request.get('initialization'), loaded=effective.get('initialization'),
            parameters_sha256=metrics['initial_parameters_sha256'], optimizer_updates=0,
            optimizer_state_entries=len(optimizer.state), dataset_id=request['version']['id']))
    order = []
    pending_rng = None
    if resume is not None:
        if report['dataset_names'] != resume['dataset_names']:
            raise ValueError('RESUME_DATASET_ORDER_CHANGED')
        if [key for key, parameter in model.named_parameters() if parameter.requires_grad] != resume['trainable_keys']:
            raise ValueError('RESUME_TRAINABLE_KEYS_CHANGED')
        order = resume['batch_order']
        if epoch_order(loader, stage, resume['metrics']['epoch']) != order:
            raise ValueError('RESUME_SAMPLER_ORDER_CHANGED')
        if any(type(index) is not int or not 0 <= index < len(loader.dataset) for batch in order for index in batch):
            raise ValueError('RESUME_BATCH_INDEX_INVALID')
        model.load_state_dict(resume.pop('model'), strict=True)
        optimizer.load_state_dict(resume.pop('optimizer'))
        scheduler.load_state_dict(resume.pop('scheduler'))
        scaler.load_state_dict(resume.pop('scaler'))
        optimizer.zero_grad(set_to_none=True)
        metrics = copy.deepcopy(resume['metrics'])
        restored_hash = parameter_hash(model)
        if metrics.get('final_parameters_sha256') and restored_hash != metrics['final_parameters_sha256']:
            raise ValueError('RESUME_PARAMETER_HASH_MISMATCH')
        metrics.update(state='RUNNING', resumed_from=resume['_entry']['id'],
                       restored_optimizer_updates=metrics['optimizer_updates'], restored_parameters_sha256=restored_hash)
        pending_rng = resume['rng']
        restore_rng(pending_rng)
    checkpoints = []
    jobs = Jobs(Path(request['data_root']))
    def save_boundary(kind, state_name):
        metrics.update(state=state_name, final_parameters_sha256=parameter_hash(model))
        if state_name == 'COMPLETED' and (not metrics['optimizer_updates'] or
                metrics['final_parameters_sha256'] == metrics['initial_parameters_sha256']):
            raise RuntimeError('TRAINING_NO_PARAMETER_CHANGE')
        emit(dict(metrics, state='SAVING', checkpoint_kind=kind))
        entry = save_checkpoint(request, stage, model, optimizer, scheduler, scaler, effective,
                                metrics, order, report, stage_state, implementation, kind)
        checkpoints.append(entry)
        stage_state['stages'][stage] = dict(metrics, checkpoint_id=entry['id'], checkpoint=entry, snapshot=entry['snapshot'])
        emit(stage_state['stages'][stage])
        return entry
    def post_step(_optimizer, _args, _kwargs):
        metrics['optimizer_updates'] += 1
    hook = optimizer.register_step_post_hook(post_step)
    torch.cuda.reset_peak_memory_stats()
    started = time.perf_counter()
    previous_seconds = metrics.get('seconds', 0.)
    previous_peak = metrics.get('peak_allocated_bytes', 0)
    previous_reserved = metrics.get('peak_reserved_bytes', 0)
    consecutive_skips = metrics.get('consecutive_skips', 0)
    try:
        if resume is not None and target_progress(metrics, config) >= config['target']:
            # A durable stage-final checkpoint may exist before the stage handoff record.
            # Reuse it without consuming even one sample or repeating an optimizer update.
            metrics['state'] = 'COMPLETED'
            entry = resume['_entry']
            if not entry.get('snapshot'):
                entry = dict(entry, snapshot=resume.get('snapshot'))
            if entry.get('snapshot') is None:
                entry = save_boundary('stage_final', 'COMPLETED')
            return metrics, entry, entry['snapshot'], checkpoints or [entry], None
        while True:
            if not order:
                order = epoch_order(loader, stage, metrics['epoch'])
                if pending_rng is not None:
                    # S1's upstream sampler seeds/shuffles Python's global RNG.
                    # Crossing a completed epoch must preserve this advancement,
                    # rather than restoring the preceding epoch after iteration.
                    pending_rng = rng_state()
            if not order:
                raise ValueError('NO_TRAIN_BATCH')
            metrics['batch_order_sha256'] = config_fingerprint(order)
            for start, end, group in cycles(order, config.get('accumulate', 1), metrics['batch_cursor']):
                optimizer.zero_grad(set_to_none=True)
                losses, accuracies, batch_sizes = [], [], []
                # Dedicated generator means iterator creation never consumes model CPU RNG.
                batches = DataLoader(loader.dataset, batch_sampler=group, collate_fn=loader.collate_fn,
                    num_workers=0, generator=torch.Generator().manual_seed(seed + metrics['epoch']))
                iterator = iter(batches)
                if pending_rng is not None:
                    # Restore after iterator creation, before the first remaining sample.
                    restore_rng(pending_rng)
                    pending_rng = None
                for batch in iterator:
                    if isinstance(batch, dict):
                        batch = {key: value.cuda() if torch.is_tensor(value) else value for key, value in batch.items()}
                    else:
                        batch = [value.cuda() if torch.is_tensor(value) else value for value in batch]
                    batch_sizes.append(int(batch['phoneme_ids'].shape[0] if isinstance(batch, dict) else batch[0].shape[0]))
                    with torch.autocast('cuda', dtype=torch.float16, enabled=config['precision'] == 'fp16'):
                        loss, accuracy = implementation.loss(model, batch, effective)
                        if not torch.isfinite(loss).all().item():
                            raise FloatingPointError('NONFINITE_LOSS: optimizer was not updated')
                        loss_for_backward = loss / len(group)
                    losses.append(float(loss.detach().cpu()))
                    if accuracy is not None:
                        accuracies.append(float(accuracy.detach().cpu()) if torch.is_tensor(accuracy) else float(accuracy))
                    scaler.scale(loss_for_backward).backward()
                    metrics['processed_batches'] += 1
                scaler.unscale_(optimizer)
                if not scaler.is_enabled() and any(p.grad is not None and not torch.isfinite(p.grad).all().item()
                                                   for p in model.parameters()):
                    raise FloatingPointError('NONFINITE_GRADIENT: fp32 optimizer was not updated')
                before = metrics['optimizer_updates']
                update_lr = optimizer.param_groups[0]['lr']
                scaler.step(optimizer)
                scaler.update()
                optimizer.zero_grad(set_to_none=True)
                updated = metrics['optimizer_updates'] != before
                if updated and metrics['optimizer_updates'] == 1 and resume is None:
                    metrics['first_update_parameters_sha256'] = parameter_hash(model)
                if not updated:
                    metrics['skipped_updates'] += 1
                    consecutive_skips += 1
                else:
                    consecutive_skips = 0
                    if stage == 's1':
                        scheduler.step()
                metrics['consecutive_skips'] = consecutive_skips
                if consecutive_skips >= 32:
                    raise FloatingPointError('32 consecutive gradient-scaler skipped updates')
                metrics['batch_cursor'] = end
                metrics['scheduler_applied_at_epoch_end'] = False
                if end == len(order):
                    metrics['completed_epochs'] = metrics['epoch'] + 1
                    if stage == 's2':
                        scheduler.step()
                        metrics['scheduler_applied_at_epoch_end'] = True
                torch.cuda.synchronize()
                elapsed = max(previous_seconds + time.perf_counter()-started, 1e-9)
                peak = max(previous_peak, torch.cuda.max_memory_allocated())
                reserved = max(previous_reserved, torch.cuda.max_memory_reserved())
                metrics.update(loss=sum(losses)/len(losses), learning_rate=update_lr,
                    next_learning_rate=optimizer.param_groups[0]['lr'],
                    seconds=elapsed, updates_per_second=metrics['optimizer_updates']/elapsed,
                    peak_allocated_bytes=peak, peak_reserved_bytes=reserved,
                    batch_cycle_size=len(group), actual_batch_sizes=batch_sizes,
                    scaler_scale=scaler.get_scale(), update_applied=updated,
                    step=metrics['optimizer_updates'], peak_cuda_bytes=peak)
                if accuracies:
                    metrics['accuracy'] = sum(accuracies)/len(accuracies)
                if not math.isfinite(metrics['loss']):
                    # JSON remains valid and the scaler skip remains visible.
                    metrics['loss'] = None
                completed = target_progress(metrics, config)
                emit(dict(metrics, stage=stage, completed=completed, total=config['target'],
                          unit_label='优化器更新' if config['stop_mode'] == 'steps' else '轮'))
                control = jobs.pending_control(request['run_id'])
                if control and control['action'] == 'pause':
                    checkpoint = save_boundary('pause', 'PAUSED')
                    return metrics, checkpoint, checkpoint['snapshot'], checkpoints, dict(control)
                if control and control['action'] in ('export', 'stop_export'):
                    action = control['action']
                    intent_path = export_intent_path(request, control['request'])
                    if intent_path.is_file():
                        intent = json.loads(intent_path.read_text(encoding='utf-8'))
                        frozen_stage = intent['stage_state']['active_stage']
                        checkpoint = intent['stage_state']['stages'][frozen_stage]['checkpoint']
                    else:
                        checkpoint = save_boundary(action, 'EARLY_COMPLETED' if action == 'stop_export' else 'RUNNING')
                    exported = enqueue_export(request, jobs, stage_state, action, control['request'], checkpoint['id'])
                    metrics['last_export_intent'] = exported
                    if exported['ok']:
                        metrics.pop('export_enqueue_error', None)
                        if action == 'export':
                            jobs.complete_control(request['run_id'], control['request'])
                    else:
                        metrics['export_enqueue_error'] = exported['error']
                    if action == 'stop_export':
                        metrics['state'] = 'EARLY_COMPLETED' if exported['ok'] else 'PAUSED'
                        return metrics, checkpoint, checkpoint['snapshot'], checkpoints, dict(control, export_result=exported)
                    emit(dict(metrics))
                if completed >= config['target']:
                    checkpoint = save_boundary('stage_final', 'COMPLETED')
                    return metrics, checkpoint, checkpoint['snapshot'], checkpoints, None
                if updated and metrics['optimizer_updates'] % effective['checkpoint_policy']['interval_optimizer_updates'] == 0:
                    save_boundary('periodic', 'RUNNING')
            metrics['epoch'] += 1
            metrics['batch_cursor'] = 0
            order = []
    finally:
        hook.remove()
        del model, optimizer, scheduler, scaler
        gc.collect()
        torch.cuda.empty_cache()


def run(request):
    import torch
    root, area = Path(request['data_root']), Path(request['run_dir'])
    if not area.resolve().is_relative_to((root/'runs').resolve()):
        raise ValueError('run_dir outside managed runs')
    jobs = Jobs(root)
    jobs.get(request['run_id'])  # Never silently discard real progress for an unknown task.
    config, preflight = request['config'], request['preflight']
    if request.get('initialization'):
        from .initialization import verify
        verify(request['initialization'], config)
    if config['stages'] != [stage for stage in ('s2', 's1') if stage in config['stages']]:
        raise ValueError('STAGE_ORDER_INVALID: expected S2 then S1')
    verify_source(ROOT, Path(request['upstream']))
    for name, path in request['initial_weights'].items():
        if request.get('initial_hashes', {}).get(name) != digest(Path(path)):
            raise ValueError(f'INITIAL_WEIGHTS_CHANGED: {name}')
    if not preflight.get('ready') or preflight['config_fingerprint'] != config_fingerprint(config):
        raise ValueError('PREFLIGHT_INVALID: config differs')
    features = root/'features'/request['version']['id']
    manifest = json.loads((features/'manifest.json').read_text(encoding='utf-8'))
    if preflight.get('features_content_sha256'):
        if feature_fingerprint(manifest) != preflight['features_content_sha256']:
            raise ValueError('PREFLIGHT_INVALID: feature content changed')
    elif digest(features/'manifest.json') != preflight['features_manifest_sha256']:
        raise ValueError('PREFLIGHT_INVALID: feature manifest changed')
    if manifest['version_fingerprint'] != request['version']['fingerprint']:
        raise ValueError('PREFLIGHT_INVALID: dataset differs')
    for lock in ('upstream', 'asset'):
        path = ROOT/'configs'/('upstream.lock.json' if lock == 'upstream' else 'assets.lock.json')
        if preflight[lock+'_lock'] != digest(path):
            raise ValueError('PREFLIGHT_INVALID: source/asset lock changed')
    expected = dict(manifest['indexes'])
    for entry in manifest['entries'].values():
        expected.update(entry['files'])
    for relative, checksum in expected.items():
        if digest(features/relative) != checksum:
            raise ValueError(f'FEATURE_CHANGED: {relative}')
    checked_assets(Path(request['upstream']))
    init_runtime(Path(request['upstream']))
    if not torch.cuda.is_available():
        raise RuntimeError('GPU_UNAVAILABLE')
    area.mkdir(parents=True, exist_ok=True)
    loaders, reports = build_loaders(features, config)
    if any(report.get('error') for report in reports.values()):
        raise ValueError(f'NO_TRAIN_BATCH: {reports}')
    state = {'schema_version': 1, 'run_id': request['run_id'], 'active_stage': None,
             'stages': {s: {'state': 'PENDING' if s in config['stages'] else 'SKIPPED'} for s in ('s2', 's1')}}
    resume = load_resume(request)
    if resume is not None:
        state = resumed_stage_state(request, resume, state)
    snapshots = dict(request['initial_weights'])
    checkpoints = []
    for prior in state['stages'].values():
        if prior.get('state') == 'COMPLETED' and prior.get('snapshot'):
            checkpoints.append(prior['checkpoint'])
            snapshots['gpt' if prior['checkpoint']['stage'] == 's1' else 'sovits'] = prior['snapshot']['path']
    atomic_json(area/'stage_state.json', state)
    outcome = 'COMPLETED'
    try:
        for stage in ('s2', 's1'):
            if stage not in config['stages']:
                continue
            if state['stages'][stage]['state'] == 'COMPLETED' and (resume is None or stage != resume['stage']):
                continue
            state['active_stage'] = stage
            state['stages'][stage] = {'state': 'RUNNING'}
            atomic_json(area/'stage_state.json', state)
            def emit(payload):
                state['stages'][stage] = payload
                atomic_json(area/'stage_state.json', state)
                jobs.progress(request['run_id'], payload)
            stage_resume = resume if resume is not None and resume['stage'] == stage else None
            metrics, checkpoint, snapshot, new_checkpoints, control = train_stage(
                request, stage, loaders[stage], reports[stage], emit, state, stage_resume)
            checkpoints.extend(item for item in new_checkpoints if item['id'] not in {c['id'] for c in checkpoints})
            snapshots['gpt' if stage == 's1' else 'sovits'] = snapshot['path']
            # Checkpoint and inference snapshot exist before stage completion can be observed.
            state['stages'][stage] = dict(metrics, checkpoint=checkpoint, snapshot=snapshot)
            atomic_json(area/'stage_state.json', state)
            if control is None:
                pending = jobs.pending_control(request['run_id'])
                if pending and pending['action'] == 'pause':
                    # A request arriving during final checkpoint I/O is satisfied
                    # by that durable boundary before constructing the next stage.
                    control = dict(pending)
                elif pending and pending['action'] in ('export', 'stop_export'):
                    exported = enqueue_export(request, jobs, state, pending['action'], pending['request'], checkpoint['id'])
                    metrics['last_export_intent'] = exported
                    if pending['action'] == 'stop_export':
                        control = dict(pending, export_result=exported)
                    elif exported['ok']:
                        jobs.complete_control(request['run_id'], pending['request'])
                    else:
                        metrics['export_enqueue_error'] = exported['error']
            jobs.progress(request['run_id'], dict(metrics, stage=stage, checkpoint_id=checkpoint['id']))
            if control is not None:
                exported = control.get('export_result')
                if exported is None or exported['ok']:
                    jobs.complete_control(request['run_id'], control['request'])
                outcome = 'EARLY_COMPLETED' if exported is not None and exported['ok'] else 'PAUSED'
                state['stages'][stage]['state'] = outcome
                if exported is not None and not exported['ok']:
                    state['stages'][stage]['export_enqueue_error'] = exported['error']
                if outcome == 'EARLY_COMPLETED':
                    for remaining in state['stages'].values():
                        if remaining['state'] == 'PENDING':
                            remaining.update(state='SKIPPED', reason='stopped_early')
                break
        if outcome == 'COMPLETED':
            state['active_stage'] = None
        atomic_json(area/'stage_state.json', state)
        if outcome == 'COMPLETED':
            exported = enqueue_export(request, jobs, state, 'completed', request['run_id'] + ':completed',
                                      checkpoints[-1]['id'] if checkpoints else None)
        result = dict(outcome=outcome, stages=state['stages'], snapshots=snapshots, checkpoints=checkpoints,
                      loader_reports=reports, config=config, config_fingerprint=config_fingerprint(config),
                      export_intents=export_intent_results(request))
        failed_exports = [intent for intent in result['export_intents'] if intent['state'] == 'FAILED']
        if failed_exports:
            result['export_enqueue_errors'] = failed_exports
            if outcome == 'PAUSED':
                result['error'] = '导出登记失败；完整恢复点已保存，可重试相同快照：' + failed_exports[-1].get('error', '')
        atomic_json(area/'training_summary.json', result)
        return result
    except Exception as exc:
        if state['active_stage']:
            state['stages'][state['active_stage']].update(state='FAILED', error=str(exc))
        atomic_json(area/'stage_state.json', state)
        raise


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        raise SystemExit('usage: python -m dictionforge.training_worker request.json result.json')
    request_path, output = [Path(value).resolve() for value in args]
    try:
        request = json.loads(request_path.read_text(encoding='utf-8-sig'))
        result = run(request)
    except Exception as exc:
        traceback.print_exc()
        atomic_json(output, {'outcome': 'FAILED', 'error': str(exc), 'code': type(exc).__name__})
        return 1
    atomic_json(output, result)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
