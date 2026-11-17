from dictionforge.training_worker import cycles, epoch_order, finalize_effective_config


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
