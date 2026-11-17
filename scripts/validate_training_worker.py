"""Real worker acceptance in an isolated job database; no mock optimization."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'backend'))
from dictionforge.audio import atomic_json, digest
from dictionforge.jobs import Jobs
from dictionforge.training_config import normalize_config


def inspect_run(area):
    import torch
    from dictionforge.feature_worker import init_runtime
    init_runtime(ROOT/'vendor/gpt-sovits')
    from process_ckpt import load_sovits_new
    request = json.loads((area/'request.json').read_text(encoding='utf-8'))
    result = json.loads((area/'result.json').read_text(encoding='utf-8'))
    report = {'run_id': request['run_id'], 'outcome': result['outcome'], 'stages': {}}
    required = {'schema_version','run_id','stage','config','config_fingerprint','version_fingerprint',
        'features_manifest_sha256','upstream_lock','asset_lock','initial_weights','model','optimizer',
        'scheduler','scaler','rng','effective','metrics','batch_order','dataset_names','trainable_keys','dataloader'}
    for entry in result['checkpoints']:
        assert digest(Path(entry['path'])) == entry['sha256']
        checkpoint = torch.load(entry['path'], map_location='cpu', weights_only=False)
        assert required <= set(checkpoint)
        assert {'python','numpy','torch_cpu','torch_cuda'} == set(checkpoint['rng'])
        assert checkpoint['metrics']['grads_clear'] and checkpoint['metrics']['accumulation_pending'] == 0
        parameter_sha = hashlib.sha256()
        for name in checkpoint['trainable_keys']:
            value = checkpoint['model'][name]
            assert torch.isfinite(value).all().item()
            parameter_sha.update(name.encode())
            parameter_sha.update(value.float().contiguous().numpy().tobytes())
        assert parameter_sha.hexdigest() == checkpoint['metrics']['final_parameters_sha256']
        stage = entry['stage']
        snapshot_path = Path(result['snapshots']['gpt' if stage == 's1' else 'sovits'])
        package = torch.load(snapshot_path, map_location='cpu', weights_only=False) if stage == 's1' else load_sovits_new(str(snapshot_path))
        assert set(checkpoint['trainable_keys']) <= set(package['weight'])
        for name, value in package['weight'].items():
            assert torch.equal(value, checkpoint['model'][name].half())
        if stage == 's2':
            assert snapshot_path.read_bytes()[:2] == b'04'
            assert package['lora_rank'] == request['config']['s2']['rank']
        optimizer_steps = sorted({int(value['step']) for value in checkpoint['optimizer']['state'].values() if 'step' in value})
        report['stages'][stage] = {'checkpoint': entry, 'snapshot_sha256': digest(snapshot_path),
            'optimizer_state_entries': len(checkpoint['optimizer']['state']), 'optimizer_steps': optimizer_steps,
            'scheduler': checkpoint['scheduler'], 'scaler': checkpoint['scaler'],
            'verified_trainable_sha256': parameter_sha.hexdigest(), 'snapshot_matches_checkpoint_fp16': True,
            'rng_fields': sorted(checkpoint['rng']), 'batch_order': checkpoint['batch_order']}
        del checkpoint, package
    atomic_json(area/'checkpoint-inspection.json', report)
    print(json.dumps({'run_id': report['run_id'], 'inspection': 'PASSED'}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--version', default='76b5a08af4ca471f9c6fa9c903347e69')
    parser.add_argument('--stages', default='s2,s1')
    parser.add_argument('--mode', choices=['steps', 'epochs'], default='steps')
    parser.add_argument('--target', type=int, default=2)
    parser.add_argument('--precision', choices=['fp16', 'fp32'], default='fp16')
    parser.add_argument('--inspect-run', type=Path)
    args = parser.parse_args()
    if args.inspect_run:
        inspect_run(args.inspect_run.resolve())
        return
    version_path = next((ROOT/'data/datasets').glob(f'*/{args.version}/manifest.json'))
    version = json.loads(version_path.read_text(encoding='utf-8'))
    data_root = ROOT/'data/p05-validation'
    feature_target = data_root/'features'/args.version
    if not feature_target.exists():
        shutil.copytree(ROOT/'data/features'/args.version, feature_target)
    config = normalize_config(dict(version_id=args.version, stages=args.stages.split(','), seed=42,
        s1=dict(batch_size=1, precision=args.precision, stop_mode=args.mode, target=args.target,
                learning_rate=.002, accumulate=4),
        s2=dict(batch_size=1, precision=args.precision, stop_mode=args.mode, target=args.target,
                learning_rate=.0001, rank=32)))
    store = Jobs(data_root)
    job = store.submit('train', config, uuid.uuid4().hex)
    job = store.claim()
    area = data_root/'runs'/job['id']
    area.mkdir(parents=True, exist_ok=True)
    upstream = ROOT/'vendor/gpt-sovits'
    request = dict(run_id=job['id'], data_root=str(data_root), upstream=str(upstream), run_dir=str(area),
        config=config, version=version, resume_checkpoint=None,
        initial_weights=dict(gpt=str(upstream/'GPT_SoVITS/pretrained_models/s1v3.ckpt'),
                             sovits=str(upstream/'GPT_SoVITS/pretrained_models/gsv-v4-pretrained/s2Gv4.pth')))
    request['initial_hashes'] = {name: digest(Path(path)) for name,path in request['initial_weights'].items()}
    env = dict(os.environ, PYTHONPATH=str(ROOT/'backend'), PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1')
    python = ROOT/'.venv-worker/Scripts/python.exe'
    preflight_request, preflight_result = area/'preflight-request.json', area/'preflight-result.json'
    atomic_json(preflight_request, dict(request, action='preflight'))
    with (area/'preflight.log').open('wb') as log:
        subprocess.run([python, '-m', 'dictionforge.feature_worker', preflight_request, preflight_result],
                       cwd=ROOT, env=env, stdout=log, stderr=log, check=True)
    request['preflight'] = json.loads(preflight_result.read_text(encoding='utf-8'))
    if not request['preflight']['ready']:
        raise RuntimeError(request['preflight']['blockers'])
    source, output = area/'request.json', area/'result.json'
    atomic_json(source, request)
    print(json.dumps({'job_id': job['id'], 'run_dir': str(area)}, ensure_ascii=False), flush=True)
    started = time.monotonic()
    with (area/'training.log').open('wb') as log:
        process = subprocess.run([python, '-m', 'dictionforge.training_worker', source, output],
                                 cwd=ROOT, env=env, stdout=log, stderr=log)
    result = json.loads(output.read_text(encoding='utf-8'))
    result['validation_wall_seconds'] = time.monotonic()-started
    store.status(job['id'], result['outcome'], result=result, error=result.get('error'))
    atomic_json(area/'validation.json', result)
    print(json.dumps({'run_id': job['id'], 'outcome': result['outcome'], 'result': str(output)}, ensure_ascii=False))
    if process.returncode:
        print((area/'training.log').read_text(encoding='utf-8', errors='replace')[-8000:])
        raise SystemExit(process.returncode)


if __name__ == '__main__':
    main()
