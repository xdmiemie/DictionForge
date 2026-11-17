import copy
from pathlib import Path
import pytest
from dictionforge.training_config import config_fingerprint
from dictionforge.training_worker import (cycles, epoch_order, finalize_effective_config,
                                         validate_resume_contract, target_progress, resumed_stage_state)


def test_tail_cycle_does_not_repeat_or_drop_consumption():
    order = [[2], [4], [1], [0], [3]]
    groups = list(cycles(order, 4))
    assert groups == [(0, 4, [[2], [4], [1], [0]]), (4, 5, [[3]])]
    assert list(cycles(order, 4, cursor=4)) == [(4, 5, [[3]])]
    assert list(cycles(order, 4, cursor=5)) == []


def test_materialized_sampler_order_uses_real_indices_and_partial_batch():
    class Sampler:
        def set_epoch(self, value):
            self.epoch = value
        def __iter__(self):
            return iter([4, 0, 3, 1, 2])
    class Loader:
        sampler = Sampler()
        batch_size = 2
    loader = Loader()
    assert epoch_order(loader, 's1', 3) == [[4, 0], [3, 1], [2]]
    assert loader.sampler.epoch == 3


def test_effective_config_marks_actual_stop_and_unused_scheduler_fields():
    effective = {'train': {'epochs': 20, 'gradient_clip': 1},
                 'optimizer': {'lr': .002, 'warmup_steps': 2000, 'lr_end': .0001},
                 'data': {'num_workers': 4}}
    result = finalize_effective_config(effective, 's1', {'stop_mode': 'steps', 'target': 2}, 42)
    assert result['train']['epochs'] is None
    assert result['train']['max_steps'] == 2
    assert result['train']['learning_rate_policy'] == 'constant'
    assert result['data']['num_workers'] == 0
    assert result['optimizer'] == {'lr': .002}
    assert result['ignored_template_fields']['optimizer.warmup_steps'] == 2000
    assert 'gradient_clip' not in result['train']
    result = finalize_effective_config({'train': {'epochs': 15, 'warmup_epochs': 0}, 'data': {}},
                                      's2', {'stop_mode': 'epochs', 'target': 1}, 42)
    assert result['train']['epochs'] == 1
    assert result['train']['max_steps'] is None
    assert result['train']['learning_rate_policy'] == 'exponential_at_completed_epoch'


def resume_fixture(tmp_path, monkeypatch):
    from dictionforge import training_worker as worker
    monkeypatch.setattr(worker, 'digest', lambda _: 'lock')
    config = {'stages': ['s2', 's1'], 'seed': 42, 's1': {'target': 2}, 's2': {'target': 2}}
    request = {'run_id': 'child', 'parent_run': 'parent', 'parent_checkpoint': 's2-u1-test',
               'data_root': str(tmp_path), 'config': config, 'version': {'fingerprint': 'version'},
               'preflight': {'features_content_sha256': 'features', 'features_manifest_sha256': 'raw-new'},
               'initial_hashes': {'gpt': 'gpt', 'sovits': 'sovits'}}
    entry = {'id': 's2-u1-test', 'stage': 's2', 'path': str(tmp_path/'runs/parent/checkpoints/s2-u1-test.pt')}
    checkpoint = {'run_id': 'parent', 'stage': 's2', 'config': config, 'config_fingerprint': config_fingerprint(config),
                  'version_fingerprint': 'version', 'features_content_sha256': 'features',
                  'features_manifest_sha256': 'raw-old', 'upstream_lock': 'lock', 'asset_lock': 'lock',
                  'initial_weights': {'gpt': {'sha256': 'gpt'}, 'sovits': {'sha256': 'sovits'}},
                  'model': {}, 'optimizer': {}, 'scheduler': {}, 'scaler': {}, 'rng': {}, 'effective': {},
                  'dataset_names': ['a', 'b'], 'trainable_keys': [],
                  'metrics': {'grads_clear': True, 'accumulation_pending': 0, 'batch_cursor': 1,
                              'batch_order_sha256': config_fingerprint([[1], [0]])},
                  'batch_order': [[1], [0]],
                  'dataloader': {'num_workers': 0, 'mode': 'direct_frozen_batch_indices'}}
    return request, entry, checkpoint


def test_resume_contract_allows_only_identical_content_and_target(tmp_path, monkeypatch):
    request, entry, checkpoint = resume_fixture(tmp_path, monkeypatch)
    validate_resume_contract(request, entry, checkpoint)
    changed = copy.deepcopy(request)
    changed['config']['s2']['target'] = 3
    with pytest.raises(ValueError, match='config_fingerprint'):
        validate_resume_contract(changed, entry, checkpoint)
    changed = copy.deepcopy(checkpoint)
    changed['features_content_sha256'] = 'changed-tensors'
    with pytest.raises(ValueError, match='features_content_sha256'):
        validate_resume_contract(request, entry, changed)
    changed = copy.deepcopy(checkpoint)
    changed['metrics']['accumulation_pending'] = 1
    with pytest.raises(ValueError, match='UNSAFE_BOUNDARY'):
        validate_resume_contract(request, entry, changed)
    changed = copy.deepcopy(checkpoint)
    changed['batch_order'] = [[0], [1]]
    with pytest.raises(ValueError, match='BATCH_ORDER_CHANGED'):
        validate_resume_contract(request, entry, changed)


def test_resume_contract_rejects_wrong_parent_and_path(tmp_path, monkeypatch):
    request, entry, checkpoint = resume_fixture(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match='PARENT_CHECKPOINT'):
        validate_resume_contract(dict(request, parent_checkpoint='other'), entry, checkpoint)
    with pytest.raises(ValueError, match='PATH_OUTSIDE_RUN'):
        validate_resume_contract(request, dict(entry, path=str(tmp_path/'elsewhere.pt')), checkpoint)
    assert target_progress({'optimizer_updates': 2, 'completed_epochs': 0}, {'stop_mode': 'steps'}) == 2
    assert target_progress({'optimizer_updates': 2, 'completed_epochs': 0}, {'stop_mode': 'epochs'}) == 0


def test_derived_run_can_resume_its_own_later_checkpoint(tmp_path, monkeypatch):
    request, entry, checkpoint = resume_fixture(tmp_path, monkeypatch)
    request['resume_source_run'] = 'child'
    checkpoint['run_id'] = 'child'
    entry.update(id='s2-u2-child', path=str(tmp_path/'runs/child/checkpoints/s2-u2-child.pt'))
    validate_resume_contract(request, entry, checkpoint)
    with pytest.raises(ValueError, match='SOURCE_RUN_MISMATCH'):
        validate_resume_contract(dict(request, resume_source_run='unrelated'), entry, checkpoint)


def test_historical_resume_does_not_inherit_later_completed_stage(tmp_path, monkeypatch):
    request, entry, checkpoint = resume_fixture(tmp_path, monkeypatch)
    checkpoint['_entry'] = entry
    checkpoint['run_stage_state'] = {'stages': {'s1': {'state': 'COMPLETED', 'checkpoint': {'id': 'future'}}}}
    state = {'stages': {'s2': {'state': 'PENDING'}, 's1': {'state': 'PENDING'}}}
    result = resumed_stage_state(request, checkpoint, state)
    assert result['stages']['s1'] == {'state': 'PENDING'}
    assert result['stages']['s2']['checkpoint']['id'] == entry['id']
