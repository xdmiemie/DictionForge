"""Actual train_stage on CPU; CUDA transfers and heavyweight serialization isolated."""
from pathlib import Path
import pytest


@pytest.mark.parametrize('action,failures,expected_state,updates', [
    ('export', 0, 'COMPLETED', 3),
    ('export', 1, 'COMPLETED', 3),
    ('stop_export', 0, 'EARLY_COMPLETED', 1),
    ('stop_export', 1, 'PAUSED', 1),
])
def test_real_loop_control_boundary_and_frozen_retry(tmp_path, monkeypatch, action, failures, expected_state, updates):
    torch = pytest.importorskip('torch')
    from dictionforge import training_worker as worker, s1_training, exports
    from dictionforge.audio import digest
    from dictionforge.jobs import Jobs
    for name in ('manual_seed_all', 'set_rng_state_all', 'reset_peak_memory_stats', 'synchronize', 'empty_cache'):
        monkeypatch.setattr(torch.cuda, name, lambda *args, **kwargs: None)
    for name in ('device_count', 'max_memory_allocated', 'max_memory_reserved'):
        monkeypatch.setattr(torch.cuda, name, lambda *args, **kwargs: 0)
    monkeypatch.setattr(torch.cuda, 'get_rng_state_all', lambda: [])
    monkeypatch.setattr(torch.Tensor, 'cuda', lambda self, *args, **kwargs: self)
    class Dataset(torch.utils.data.Dataset):
        def __len__(self):
            return 5
        def __getitem__(self, index):
            return {'phoneme_ids': torch.tensor([float(index + 1)])}
    dataset = Dataset()
    sampler = torch.utils.data.DistributedSampler(dataset, num_replicas=1, rank=0, seed=42)
    loader = torch.utils.data.DataLoader(dataset, batch_size=1, sampler=sampler, num_workers=0)
    def create(config, _initial, _run_dir):
        model = torch.nn.Linear(1, 1, bias=False)
        optimizer = torch.optim.SGD(model.parameters(), lr=config['learning_rate'])
        scheduler = torch.optim.lr_scheduler.LambdaLR(optimizer, lambda _: 1.)
        return model, optimizer, scheduler, {'train': {}, 'data': {}}
    monkeypatch.setattr(s1_training, 'create', create)
    monkeypatch.setattr(s1_training, 'loss', lambda model, batch, _: (model(batch['phoneme_ids']).square().mean(), None))
    saved = []
    def serialize(path, payload, **_kwargs):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(path.name.encode())
        if 'checkpoint_kind' in payload:
            assert payload['metrics']['grads_clear'] and payload['metrics']['accumulation_pending'] == 0
            saved.append((payload['checkpoint_kind'], payload['metrics']['optimizer_updates']))
        return {'path': str(path), 'sha256': digest(path), 'bytes': path.stat().st_size}
    monkeypatch.setattr(worker, 'save_torch', serialize)
    store = Jobs(tmp_path)
    job = store.submit('train', {}, 'request')
    store.claim()
    store.control(job['id'], action, 'control')
    area = tmp_path/'runs'/job['id']
    area.mkdir(parents=True)
    initial = tmp_path/'initial'
    initial.write_bytes(b'CPU fixture initial')
    config = {'batch_size': 1, 'precision': 'fp32', 'stop_mode': 'steps', 'target': 3,
              'learning_rate': .001, 'accumulate': 2}
    request = {'run_id': job['id'], 'run_dir': str(area), 'data_root': str(tmp_path),
               'initial_weights': {'gpt': str(initial), 'sovits': str(initial)},
               'version': {'id': 'fixture', 'fingerprint': 'fixture'},
               'preflight': {'features_manifest_sha256': 'fixture'},
               'config': {'seed': 42, 'stages': ['s1'], 's1': config}}
    state = {'active_stage': 's1', 'stages': {'s2': {'state': 'SKIPPED'}, 's1': {'state': 'RUNNING'}}}
    attempts = []
    def enqueue(_jobs, _request, frozen_state, weights, _reason, _request_id, checkpoint_id=None):
        attempts.append((frozen_state['stages']['s1']['optimizer_updates'], dict(weights), checkpoint_id))
        if len(attempts) <= failures:
            raise OSError('controlled export registration failure')
        return {'id': 'export-job'}
    monkeypatch.setattr(exports, 'enqueue_snapshot', enqueue)
    result, point, _, _, control = worker.train_stage(request, 's1', loader,
        {'dataset_names': ['a', 'b', 'c', 'd', 'e']}, lambda _: None, state)
    assert result['state'] == expected_state and result['optimizer_updates'] == updates
    assert saved[0] == (action, 1)
    assert attempts[0][0] == 1
    assert attempts[0][1]['sovits'] == str(initial)
    if action == 'export':
        assert saved == [('export', 1), ('stage_final', 3)]
        assert control is None and store.pending_control(job['id']) is None
        if failures:
            assert attempts[0] == attempts[1]  # retry from u1 while live training advanced
    else:
        assert saved == [('stop_export', 1)]
        assert control['export_result']['ok'] is (failures == 0)
        # Outer orchestration acknowledges only after the durable stage_state.
        assert store.pending_control(job['id']) is not None
        if failures:
            assert worker.export_intent_results(request)[0]['state'] == 'FAILED'
