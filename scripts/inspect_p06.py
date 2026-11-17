"""CPU-only P06 checkpoint audit and observed parent/child numerical comparison."""
import gc
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'backend'))
from dictionforge.audio import atomic_json
from validate_training_worker import inspect_run


def compare_stage(parent, child, stage):
    import torch
    def load(report):
        point = report['stages'][stage]['checkpoint']
        payload = torch.load(point['path'], map_location='cpu', weights_only=False)
        return {name: payload['model'][name] for name in payload['trainable_keys']}
    first, second = load(parent), load(child)
    assert list(first) == list(second)
    count, changed, max_absolute, square_sum = 0, 0, 0., 0.
    for name, value in first.items():
        other = second[name]
        assert value.shape == other.shape
        difference = value.double() - other.double()
        count += value.numel()
        changed += int(torch.count_nonzero(difference))
        max_absolute = max(max_absolute, float(difference.abs().max()))
        square_sum += float(difference.square().sum())
    del first, second
    gc.collect()
    return {'parameters': count, 'different_values': changed, 'bitwise_equal': changed == 0,
            'max_absolute_difference': max_absolute, 'rms_difference': math.sqrt(square_sum/count),
            'final_rng_hashes_equal': parent['stages'][stage]['rng_hashes'] == child['stages'][stage]['rng_hashes'],
            'interpretation': 'Observed fixed-run comparison only; no general bitwise determinism guarantee.'}


def main():
    run_ids = ['be4045700e60491b95efb7101aaf411a', 'a1e76ef07ae34bedb9dbb45590216c2f']
    reports = []
    for run_id in run_ids:
        area = ROOT/'data/runs'/run_id
        inspect_run(area)
        reports.append(json.loads((area/'checkpoint-inspection.json').read_text(encoding='utf-8')))
    result = {'inspection_device': 'CPU; no CUDA model allocation', 'parent': reports[0], 'child': reports[1],
              'comparison': {stage: compare_stage(*reports, stage) for stage in ('s2', 's1')}}
    path = ROOT/'docs/evidence/P06_TRAINING_INSPECTION.json'
    atomic_json(path, result)
    print(json.dumps(result['comparison'], ensure_ascii=False))


if __name__ == '__main__':
    main()
