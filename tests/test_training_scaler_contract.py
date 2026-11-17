"""Real Torch CPU scaler contract; does not substitute for GPU training acceptance."""
import json
import os
from pathlib import Path
import subprocess

import pytest

from dictionforge.settings import Settings


def test_real_cpu_grad_scaler_skips_optimizer_hook_on_nonfinite_gradient():
    python = Path(Settings.from_environment().worker_python)
    if not python.is_file():
        pytest.skip("独立 Torch worker 尚未安装；该项不是模拟 GPU 训练")
    program = r'''
import json
import torch
p = torch.nn.Parameter(torch.tensor([1.0]))
optimizer = torch.optim.SGD([p], lr=.1)
scaler = torch.amp.GradScaler('cpu', init_scale=128.)
actual = [0]
def stepped(_optimizer, _args, _kwargs):
    actual[0] += 1
hook = optimizer.register_step_post_hook(stepped)
def update(multiplier):
    optimizer.zero_grad(set_to_none=True)
    loss = (p * multiplier).sum()
    scaler.scale(loss).backward()
    scaler.unscale_(optimizer)
    scaler.step(optimizer)
    scaler.update()
    return p.detach().clone()
finite = update(torch.tensor(2.))
assert actual[0] == 1
scale_before = scaler.get_scale()
skipped = update(torch.tensor(float('inf')))
assert actual[0] == 1 and torch.equal(finite, skipped)
assert scaler.get_scale() < scale_before
restored_scaler = torch.amp.GradScaler('cpu')
restored_scaler.load_state_dict(scaler.state_dict())
assert restored_scaler.get_scale() == scaler.get_scale()
final = update(torch.tensor(3.))
assert actual[0] == 2 and not torch.equal(final, skipped)
hook.remove()
print(json.dumps(dict(device='cpu', torch=torch.__version__, actual_optimizer_updates=actual[0],
    attempted_updates=3, skipped_updates=1, scale_after_skip=restored_scaler.get_scale(), parameter=float(final))))
'''
    result = subprocess.run([str(python), "-c", program], capture_output=True, text=True,
        env={**os.environ, "CUDA_VISIBLE_DEVICES": "-1"}, timeout=60, check=True)
    report = json.loads(result.stdout)
    assert report["device"] == "cpu"
    assert report["actual_optimizer_updates"] == 2
    assert report["attempted_updates"] == 3
    assert report["skipped_updates"] == 1
