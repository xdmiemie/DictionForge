"""Exercise the actual training loop against the pinned S1 sampler on CPU."""
import copy
import importlib.util
from pathlib import Path
import random

import pytest


def test_epoch_boundary_resume_keeps_real_s1_sampler_python_rng(tmp_path, monkeypatch):
    torch = pytest.importorskip('torch')
    from dictionforge import training_worker as worker

    sampler_path = Path(__file__).parents[1] / 'vendor/gpt-sovits/GPT_SoVITS/AR/data/bucket_sampler.py'
    if not sampler_path.is_file():
        pytest.skip('Pinned upstream checkout is required for the real sampler contract')
    spec = importlib.util.spec_from_file_location('dictionforge_test_pinned_s1_sampler', sampler_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    # No GPU allocation: model and real DataLoader stay on CPU. CUDA state APIs
    # are mocked only at the device boundary, not the training/resume loop.
    for name in ('manual_seed_all', 'set_rng_state_all', 'reset_peak_memory_stats', 'synchronize', 'empty_cache'):
        monkeypatch.setattr(torch.cuda, name, lambda *args, **kwargs: None)
    for name in ('device_count', 'max_memory_allocated', 'max_memory_reserved'):
        monkeypatch.setattr(torch.cuda, name, lambda *args, **kwargs: 0)
    monkeypatch.setattr(torch.cuda, 'get_rng_state_all', lambda: [])
    monkeypatch.setattr(torch.Tensor, 'cuda', lambda self, *args, **kwargs: self)
    observed = []

    class Dataset(torch.utils.data.Dataset):
        def __len__(self):
            return 7

        def get_sample_length(self, index):
            return 1.0 + index / 10

        def __getitem__(self, index):
            observed.append(random.random())
            return {'phoneme_ids': torch.tensor([float(index + 1)])}

    data = Dataset()
    sampler = module.DistributedBucketSampler(data, num_replicas=1, rank=0, batch_size=1, seed=42)
    loader = torch.utils.data.DataLoader(data, batch_size=1, sampler=sampler, num_workers=0)
    initial_python_state = random.getstate()

    def create(config, _initial, _run_dir):
        model = torch.nn.Linear(1, 1, bias=False)
        with torch.no_grad():
            model.weight.fill_(1.)
        optimizer = torch.optim.SGD(model.parameters(), lr=config['learning_rate'])
        scheduler = torch.optim.lr_scheduler.LambdaLR(optimizer, lambda _: 1.)
        return model, optimizer, scheduler, {'train': {}, 'data': {}}

    from dictionforge import s1_training
    monkeypatch.setattr(s1_training, 'create', create)
    monkeypatch.setattr(s1_training, 'loss', lambda model, batch, _: (model(batch['phoneme_ids']).square().mean(), None))
    monkeypatch.setattr(worker, 'save_checkpoint', lambda *args, **kwargs: {'id': 'test-final', 'snapshot': {'path': 'test-snapshot'}})
    stage_config = {'batch_size': 1, 'precision': 'fp32', 'stop_mode': 'epochs', 'target': 2,
                    'learning_rate': .001, 'accumulate': 1}
    run_dir = tmp_path / 'runs' / 'rng-boundary'
    run_dir.mkdir(parents=True)
    request = {'run_id': 'rng-boundary', 'data_root': str(tmp_path), 'run_dir': str(run_dir),
               'initial_weights': {'gpt': 'unused-by-cpu-fixture'}, 'version': {'id': 'fixture'},
               'config': {'seed': 42, 's1': stage_config}}
    model, optimizer, scheduler, _ = create(stage_config, '', run_dir)
    prior_order = worker.epoch_order(loader, 's1', 0)
    # Advance the Python RNG as if the previous epoch's samples were consumed.
    for _ in range(len(data)):
        random.random()
    boundary_rng = worker.rng_state()
    worker.epoch_order(loader, 's1', 1)
    expected = [random.random() for _ in range(len(data))]
    # The test must distinguish the old boundary RNG from the new sampler RNG.
    random.setstate(boundary_rng['python'])
    assert [random.random() for _ in range(len(data))] != expected
    metrics = {'state': 'PAUSED', 'epoch': 0, 'completed_epochs': 1, 'batch_cursor': len(prior_order),
               'processed_batches': len(prior_order), 'optimizer_updates': len(prior_order), 'skipped_updates': 0,
               'initial_parameters_sha256': worker.parameter_hash(model), 'final_parameters_sha256': worker.parameter_hash(model),
               'stage': 's1', 'target': 2, 'unit': 'epochs'}
    names = [str(i) for i in range(len(data))]
    resume = {'model': copy.deepcopy(model.state_dict()), 'optimizer': optimizer.state_dict(),
              'scheduler': scheduler.state_dict(), 'scaler': {}, 'metrics': metrics, 'batch_order': prior_order,
              'rng': boundary_rng, 'dataset_names': names, 'trainable_keys': ['weight'], '_entry': {'id': 'cpu-boundary'}}
    try:
        result, *_ = worker.train_stage(request, 's1', loader, {'dataset_names': names}, lambda _: None,
                                        {'stages': {'s1': {'state': 'PAUSED'}}}, resume)
        assert observed == expected
        assert result['completed_epochs'] == 2
        assert result['processed_batches'] == 2 * len(data)
        assert result['optimizer_updates'] == 2 * len(data)
    finally:
        random.setstate(initial_python_state)


def test_actual_loop_periodic_checkpoint_is_optimizer_update_100(tmp_path, monkeypatch):
    torch = pytest.importorskip('torch')
    from dictionforge import training_worker as worker, s1_training
    for name in ('manual_seed_all', 'set_rng_state_all', 'reset_peak_memory_stats', 'synchronize', 'empty_cache'):
        monkeypatch.setattr(torch.cuda, name, lambda *args, **kwargs: None)
    for name in ('device_count', 'max_memory_allocated', 'max_memory_reserved'):
        monkeypatch.setattr(torch.cuda, name, lambda *args, **kwargs: 0)
    monkeypatch.setattr(torch.cuda, 'get_rng_state_all', lambda: [])
    monkeypatch.setattr(torch.Tensor, 'cuda', lambda self, *args, **kwargs: self)

    class Dataset(torch.utils.data.Dataset):
        def __len__(self):
            return 3

        def __getitem__(self, index):
            return {'phoneme_ids': torch.tensor([float(index + 1)])}

    sampler = torch.utils.data.DistributedSampler(Dataset(), num_replicas=1, rank=0, seed=42)
    loader = torch.utils.data.DataLoader(sampler.dataset, batch_size=1, sampler=sampler, num_workers=0)
    live_models = []

    def create(config, _initial, _run_dir):
        model = torch.nn.Linear(1, 1, bias=False)
        with torch.no_grad():
            model.weight.fill_(1.)
        live_models.append(model)
        optimizer = torch.optim.SGD(model.parameters(), lr=config['learning_rate'])
        scheduler = torch.optim.lr_scheduler.LambdaLR(optimizer, lambda _: 1.)
        return model, optimizer, scheduler, {'train': {}, 'data': {}}

    payloads = []

    def capture_serialization(path, payload, **_kwargs):
        # Exercise the real save_checkpoint payload/index logic, replacing only
        # tensor serialization. All live gradients must already be cleared.
        assert all(parameter.grad is None for parameter in live_models[-1].parameters())
        if 'checkpoint_kind' in payload:
            payloads.append(copy.deepcopy(payload))
        return {'path': str(path), 'sha256': '0' * 64, 'bytes': 1}

    monkeypatch.setattr(s1_training, 'create', create)
    monkeypatch.setattr(s1_training, 'loss', lambda model, batch, _: (model(batch['phoneme_ids']).square().mean(), None))
    monkeypatch.setattr(worker, 'save_torch', capture_serialization)
    area = tmp_path / 'runs' / 'periodic-test'
    area.mkdir(parents=True)
    initial = tmp_path / 'initial.ckpt'
    initial.write_bytes(b'CPU test initialization descriptor')
    stage = {'batch_size': 1, 'precision': 'fp32', 'stop_mode': 'steps', 'target': 101,
             'learning_rate': .001, 'accumulate': 1}
    request = {'run_id': 'periodic-test', 'run_dir': str(area), 'data_root': str(tmp_path),
               'version': {'id': 'fixture', 'fingerprint': 'fixture-fingerprint'},
               'preflight': {'features_manifest_sha256': 'fixture-feature-hash'},
               'initial_weights': {'gpt': str(initial)}, 'config': {'seed': 42, 'stages': ['s1'], 's1': stage}}
    result, _, _, saved, _ = worker.train_stage(request, 's1', loader, {'dataset_names': ['a', 'b', 'c']},
        lambda _: None, {'stages': {'s2': {'state': 'SKIPPED'}, 's1': {'state': 'RUNNING'}}})
    assert [(p['checkpoint_kind'], p['metrics']['optimizer_updates']) for p in payloads] == [('periodic', 100), ('stage_final', 101)]
    assert [p['metrics']['processed_batches'] for p in payloads] == [100, 101]
    assert all(p['metrics']['grads_clear'] and p['metrics']['accumulation_pending'] == 0 for p in payloads)
    assert [point['kind'] for point in saved] == ['periodic', 'stage_final']
    assert result['optimizer_updates'] == 101
