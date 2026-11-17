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
from .training_config import config_fingerprint
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


def save_checkpoint(request, stage, model, optimizer, scheduler, scaler, effective, metrics, order, report):
    boundary = dict(metrics, grads_clear=True, accumulation_pending=0)
    payload = dict(schema_version=1, run_id=request['run_id'], stage=stage, config=request['config'],
        config_fingerprint=config_fingerprint(request['config']), version_fingerprint=request['version']['fingerprint'],
        features_manifest_sha256=request['preflight']['features_manifest_sha256'],
        upstream_lock=digest(ROOT/'configs/upstream.lock.json'), asset_lock=digest(ROOT/'configs/assets.lock.json'),
        initial_weights={key: {'path': value, 'sha256': digest(Path(value))} for key, value in request['initial_weights'].items()},
        model=to_cpu(model.state_dict()), optimizer=to_cpu(optimizer.state_dict()), scheduler=to_cpu(scheduler.state_dict()),
        scaler=to_cpu(scaler.state_dict()), rng=rng_state(), effective=effective, metrics=boundary,
        batch_order=order, dataset_names=report['dataset_names'], trainable_keys=[k for k,p in model.named_parameters() if p.requires_grad],
        dataloader={'num_workers': 0, 'mode': 'direct_frozen_batch_indices', 'generator_state': None})
    area = Path(request['run_dir']) / 'checkpoints'
    area.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(area).free < 2 * 1024**3:
        raise OSError('INSUFFICIENT_DISK: preserve 2 GiB before checkpoint')
    path = area / f"{stage}-u{metrics['optimizer_updates']}-{uuid.uuid4().hex[:8]}.pt"
    entry = save_torch(path, payload)
    del payload
    entry.update(stage=stage, metrics=boundary, kind='stage_final', id=path.stem)
    index_path = area / 'index.json'
    index = json.loads(index_path.read_text(encoding='utf-8')) if index_path.exists() else {'checkpoints': []}
    index['checkpoints'].append(entry)
    index['last'] = entry['id']
    atomic_json(index_path, index)
    return entry


def train_stage(request, stage, loader, report, emit):
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
    def post_step(_optimizer, _args, _kwargs):
        metrics['optimizer_updates'] += 1
    hook = optimizer.register_step_post_hook(post_step)
    torch.cuda.reset_peak_memory_stats()
    started = time.monotonic()
    consecutive_skips = 0
    order = []
    try:
        while True:
            order = epoch_order(loader, stage, metrics['epoch'])
            if not order:
                raise ValueError('NO_TRAIN_BATCH')
            metrics['batch_order_sha256'] = config_fingerprint(order)
            for start, end, group in cycles(order, config.get('accumulate', 1)):
                optimizer.zero_grad(set_to_none=True)
                losses, accuracies, batch_sizes = [], [], []
                # Dedicated generator means iterator creation never consumes model CPU RNG.
                batches = DataLoader(loader.dataset, batch_sampler=group, collate_fn=loader.collate_fn,
                    num_workers=0, generator=torch.Generator().manual_seed(seed + metrics['epoch']))
                for batch in batches:
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
                if not updated:
                    metrics['skipped_updates'] += 1
                    consecutive_skips += 1
                else:
                    consecutive_skips = 0
                    if stage == 's1':
                        scheduler.step()
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
                elapsed = time.monotonic()-started
                metrics.update(loss=sum(losses)/len(losses), learning_rate=update_lr,
                    next_learning_rate=optimizer.param_groups[0]['lr'],
                    seconds=elapsed, updates_per_second=metrics['optimizer_updates']/elapsed,
                    peak_allocated_bytes=torch.cuda.max_memory_allocated(), peak_reserved_bytes=torch.cuda.max_memory_reserved(),
                    batch_cycle_size=len(group), actual_batch_sizes=batch_sizes,
                    scaler_scale=scaler.get_scale(), update_applied=updated,
                    step=metrics['optimizer_updates'], peak_cuda_bytes=torch.cuda.max_memory_allocated())
                if accuracies:
                    metrics['accuracy'] = sum(accuracies)/len(accuracies)
                if not math.isfinite(metrics['loss']):
                    # JSON remains valid and the scaler skip remains visible.
                    metrics['loss'] = None
                completed = metrics['optimizer_updates'] if config['stop_mode'] == 'steps' else metrics['completed_epochs']
                emit(dict(metrics, stage=stage, completed=completed, total=config['target'],
                          unit_label='优化器更新' if config['stop_mode'] == 'steps' else '轮'))
                if completed >= config['target']:
                    metrics['state'] = 'COMPLETED'
                    metrics['final_parameters_sha256'] = parameter_hash(model)
                    if not metrics['optimizer_updates'] or metrics['final_parameters_sha256'] == metrics['initial_parameters_sha256']:
                        raise RuntimeError('TRAINING_NO_PARAMETER_CHANGE')
                    checkpoint = save_checkpoint(request, stage, model, optimizer, scheduler, scaler, effective, metrics, order, report)
                    package = implementation.inference_package(model, effective, metrics)
                    snapshot_path = Path(request['run_dir'])/'snapshots'/stage/('gpt.ckpt' if stage == 's1' else 'sovits.pth')
                    snapshot = save_torch(snapshot_path, package, v4=stage == 's2')
                    del package
                    return metrics, checkpoint, snapshot
            metrics['epoch'] += 1
            metrics['batch_cursor'] = 0
    finally:
        hook.remove()
        del model, optimizer, scheduler, scaler
        gc.collect()
        torch.cuda.empty_cache()


def run(request):
    import torch
    if request.get('resume_checkpoint'):
        raise ValueError('RESUME_NOT_AVAILABLE: P06 will enable verified full restore')
    root, area = Path(request['data_root']), Path(request['run_dir'])
    if not area.resolve().is_relative_to((root/'runs').resolve()):
        raise ValueError('run_dir outside managed runs')
    jobs = Jobs(root)
    jobs.get(request['run_id'])  # Never silently discard real progress for an unknown task.
    config, preflight = request['config'], request['preflight']
    if config['stages'] != [stage for stage in ('s2', 's1') if stage in config['stages']]:
        raise ValueError('STAGE_ORDER_INVALID: expected S2 then S1')
    verify_source(ROOT, Path(request['upstream']))
    for name, path in request['initial_weights'].items():
        if request.get('initial_hashes', {}).get(name) != digest(Path(path)):
            raise ValueError(f'INITIAL_WEIGHTS_CHANGED: {name}')
    if not preflight.get('ready') or preflight['config_fingerprint'] != config_fingerprint(config):
        raise ValueError('PREFLIGHT_INVALID: config differs')
    features = root/'features'/request['version']['id']
    if digest(features/'manifest.json') != preflight['features_manifest_sha256']:
        raise ValueError('PREFLIGHT_INVALID: feature manifest changed')
    manifest = json.loads((features/'manifest.json').read_text(encoding='utf-8'))
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
    snapshots = dict(request['initial_weights'])
    checkpoints = []
    atomic_json(area/'stage_state.json', state)
    try:
        for stage in ('s2', 's1'):
            if stage not in config['stages']:
                continue
            state['active_stage'] = stage
            state['stages'][stage] = {'state': 'RUNNING'}
            atomic_json(area/'stage_state.json', state)
            def emit(payload):
                state['stages'][stage] = payload
                atomic_json(area/'stage_state.json', state)
                jobs.progress(request['run_id'], payload)
            metrics, checkpoint, snapshot = train_stage(request, stage, loaders[stage], reports[stage], emit)
            checkpoints.append(checkpoint)
            snapshots['gpt' if stage == 's1' else 'sovits'] = snapshot['path']
            # Checkpoint and inference snapshot exist before stage completion can be observed.
            state['stages'][stage] = dict(metrics, checkpoint=checkpoint, snapshot=snapshot)
            atomic_json(area/'stage_state.json', state)
            jobs.progress(request['run_id'], dict(metrics, stage=stage, state='COMPLETED', checkpoint_id=checkpoint['id']))
        state['active_stage'] = None
        atomic_json(area/'stage_state.json', state)
        result = dict(outcome='COMPLETED', stages=state['stages'], snapshots=snapshots, checkpoints=checkpoints,
                      loader_reports=reports, config=config, config_fingerprint=config_fingerprint(config))
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
