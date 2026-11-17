"""Actual Torch parser failures on CPU; the valid full pair is accepted separately."""
import json
import os
from pathlib import Path
import subprocess

import pytest

from dictionforge.settings import ROOT, Settings


def test_cpu_torch_parser_header_nonfinite_rank_and_gpt_shape(tmp_path):
    settings = Settings.from_environment()
    if not Path(settings.worker_python).is_file():
        pytest.skip('独立 Torch worker 未安装')
    program = r'''
import json
from pathlib import Path
import sys
import torch
from dictionforge.feature_worker import init_runtime
from dictionforge.model_worker import load_package, shapes, validate_gpt, validate_sovits
root, upstream = Path(sys.argv[1]), Path(sys.argv[2])
init_runtime(upstream)
package = {'weight': {'tensor': torch.ones(2)}, 'config': {'model': {'vocab_size': 1025}}}
official = root/'official.ckpt'
torch.save(package, official)
candidate = root/'candidate.ckpt'
torch.save(dict(package, weight={'tensor': torch.ones(3)}), candidate)
try:
    validate_gpt(candidate, official)
    raise AssertionError('shape mismatch accepted')
except ValueError as error:
    assert 'GPT_ARCHITECTURE_MISMATCH' in str(error)
try:
    shapes({'bad': torch.tensor([float('nan')])})
    raise AssertionError('nonfinite accepted')
except ValueError as error:
    assert 'MODEL_NONFINITE' in str(error)
lora = root/'lora.pth'
torch.save(dict(package, lora_rank=3), lora)
with lora.open('r+b') as output:
    output.write(b'04')
assert torch.equal(load_package(lora)['weight']['tensor'], package['weight']['tensor'])
try:
    validate_sovits(lora, root/'unused-base.pth')
    raise AssertionError('invalid rank accepted')
except ValueError as error:
    assert 'LORA_RANK_INVALID' in str(error)
assert not torch.cuda.is_initialized()
print(json.dumps(dict(device='cpu', header04=True, gpt_shape_rejected=True,
                     nonfinite_rejected=True, invalid_rank_rejected=True)))
'''
    result = subprocess.run([settings.worker_python, '-c', program, str(tmp_path), str(settings.upstream)],
        capture_output=True, text=True, timeout=60, check=True,
        env={**os.environ, 'CUDA_VISIBLE_DEVICES': '-1', 'PYTHONPATH': str(ROOT/'backend')})
    report = json.loads(result.stdout)
    assert report['device'] == 'cpu' and all(report[key] for key in report if key != 'device')
    (tmp_path/'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
