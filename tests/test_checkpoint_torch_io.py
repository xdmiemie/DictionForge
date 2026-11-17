"""Real Torch serialization on CPU with injected I/O faults, not GPU acceptance."""
import json
import os
from pathlib import Path
import subprocess

import pytest

from dictionforge.settings import ROOT, Settings


def test_torch_checkpoint_faults_preserve_committed_index(tmp_path):
    python = Path(Settings.from_environment().worker_python)
    if not python.is_file():
        pytest.skip("独立 Torch worker 尚未安装；该项不是模拟 GPU 训练")
    program = r'''
import errno
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch
import torch
from dictionforge import training_worker as worker
from dictionforge.audio import atomic_json

root = Path(sys.argv[1])
root.mkdir(parents=True, exist_ok=True)
payload = {'weight': torch.arange(16, dtype=torch.float32)}
required = worker.estimate_payload_bytes(payload)
assert required >= payload['weight'].numel() * 4 + 16 * 1024**2
with patch.object(worker.shutil, 'disk_usage', return_value=SimpleNamespace(free=0)):
    try:
        worker.save_torch(root/'disk-full.pt', payload)
        raise AssertionError('space estimate must reject')
    except OSError as error:
        assert 'INSUFFICIENT_DISK' in str(error)
assert not (root/'disk-full.pt').exists() and not list(root.glob('*.part'))

def partial_write(_payload, stream):
    stream.write(b'partial Torch serialization')
    raise OSError(errno.ENOSPC, 'fixture disk full during Torch write')
with patch.object(torch, 'save', partial_write):
    try:
        worker.save_torch(root/'partial.pt', payload)
        raise AssertionError('partial write must reject')
    except OSError as error:
        assert error.errno == errno.ENOSPC
assert not (root/'partial.pt').exists() and not list(root.glob('*.part'))

good = worker.save_torch(root/'good.pt', payload)
assert torch.equal(torch.load(good['path'], weights_only=False)['weight'], payload['weight'])
before = Path(good['path']).read_bytes()
try:
    worker.save_torch(Path(good['path']), {'weight': torch.zeros(16)})
    raise AssertionError('immutable target must reject overwrite')
except FileExistsError:
    pass
assert Path(good['path']).read_bytes() == before

(root/'configs').mkdir()
atomic_json(root/'configs/upstream.lock.json', {'fixture': 'source'})
atomic_json(root/'configs/assets.lock.json', {'fixture': 'assets'})
worker.ROOT = root
run = root/'runs/test'
model = torch.nn.Linear(2, 1)
optimizer = torch.optim.AdamW(model.parameters(), lr=.001)
model(torch.ones(1, 2)).sum().backward()
optimizer.step()
optimizer.zero_grad(set_to_none=True)
scheduler = torch.optim.lr_scheduler.StepLR(optimizer, step_size=1)
scaler = torch.amp.GradScaler('cpu')
request = dict(run_dir=str(run), run_id='test', config={'fixture': 'cpu I/O'},
    version={'fingerprint': 'version'}, preflight={'features_manifest_sha256': 'manifest'},
    initial_weights={'gpt': good['path']})
implementation = SimpleNamespace(inference_package=lambda model, effective, metrics:
    {'weight': model.state_dict(), 'config': effective})
args = (request, 's1', model, optimizer, scheduler, scaler, {}, {'optimizer_updates': 1},
        [[0]], {'dataset_names': ['fixture.wav']}, {'stages': {}}, implementation, 'pause')
first = worker.save_checkpoint(*args)
index_path = run/'checkpoints/index.json'
previous = index_path.read_bytes()
replace = os.replace
def fail_index(source, destination):
    if Path(destination) == index_path:
        raise OSError(errno.ENOSPC, 'fixture disk full during index replace')
    return replace(source, destination)
with patch.object(os, 'replace', fail_index):
    try:
        worker.save_checkpoint(*args)
        raise AssertionError('index publication must fail')
    except OSError as error:
        assert error.errno == errno.ENOSPC
assert index_path.read_bytes() == previous
index = json.loads(previous)
assert index['last'] == first['id'] and len(index['checkpoints']) == 1
assert len(list((run/'checkpoints').glob('*.pt'))) == 2  # unindexed complete orphan retained
assert not list(root.rglob('*.part')) and not list(root.rglob('*.tmp'))
print(json.dumps(dict(device='cpu', torch=torch.__version__, space_guard=True, partial_write=True,
    immutable=True, index_atomicity=True, indexed_checkpoints=len(index['checkpoints']), orphan_checkpoints=1)))
'''
    result = subprocess.run([str(python), "-c", program, str(tmp_path)], capture_output=True, text=True,
        env={**os.environ, "CUDA_VISIBLE_DEVICES": "-1", "PYTHONPATH": str(ROOT / "backend")},
        timeout=60, check=True)
    report = json.loads(result.stdout)
    (tmp_path / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    assert report["device"] == "cpu"
    assert all(report[key] for key in ("space_guard", "partial_write", "immutable", "index_atomicity"))
    assert report["indexed_checkpoints"] == report["orphan_checkpoints"] == 1
