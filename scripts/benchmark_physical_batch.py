"""Real GPU capacity probe with native model/loss/optimizer and full physical batches.

Runs disposable models in fresh worker processes. No trained model is published.
Repeats the longest real training items to stress padding even for a tiny dataset.
The allocator is bounded by currently free dedicated VRAM minus 256 MiB, so a
Windows shared-memory spill is not reported as usable GPU batch capacity.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from dictionforge.audio import atomic_json, digest


def gpu_snapshot():
    result = subprocess.run(['nvidia-smi', '--query-gpu=name,memory.total,memory.used,memory.free,utilization.gpu',
        '--format=csv,noheader,nounits'], capture_output=True, text=True, check=True, timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    name, total, used, free, utilization = [x.strip() for x in result.stdout.strip().splitlines()[0].split(',')]
    return dict(name=name, total_mib=int(total), used_mib=int(used), free_mib=int(free), utilization=int(utilization))


def child(request_path, output):
    request = json.loads(request_path.read_text(encoding='utf-8'))
    result = dict(stage=request['stage'], batch=request['batch'], updates=0, skipped=0, steps=[],
                  status='STARTING', profile='longest_real_items_repeated', requested_updates=request['updates'])
    atomic_json(output, result)
    try:
        from dictionforge.feature_worker import init_runtime, build_loaders
        from dictionforge.training_config import normalize_config
        init_runtime(ROOT / 'vendor/gpt-sovits')
        import random
        import numpy as np
        import torch
        from dictionforge import s1_training, s2_training
        from dictionforge.training_worker import parameter_hash
        stage, batch_size = request['stage'], request['batch']
        config = normalize_config(dict(version_id=request['version_id'], stages=[stage], seed=42,
            s1=dict(batch_size=min(batch_size,64), accumulate=1), s2=dict(batch_size=min(batch_size,64), rank=32)))
        # Capacity probes may exceed the UI's 64-item limit; this never changes
        # application validation or any persisted training configuration.
        config[stage]['batch_size'] = batch_size
        result['above_application_batch_limit'] = batch_size > 64
        random.seed(42); np.random.seed(42); torch.manual_seed(42); torch.cuda.manual_seed_all(42)
        torch.set_num_threads(2)
        initial_gpu = gpu_snapshot()
        total = torch.cuda.get_device_properties(0).total_memory
        budget = min(total, (initial_gpu['free_mib'] - 256) * 1024**2)
        if budget < 2 * 1024**3:
            raise RuntimeError('GPU_BUSY: less than 2 GiB free after desktop reserve')
        torch.cuda.set_per_process_memory_fraction(budget / total, 0)
        result.update(gpu_before=initial_gpu, allocator_budget_bytes=budget, reserve_mib=256,
                      torch=torch.__version__, precision='fp16', rank=32 if stage=='s2' else None,
                      accumulation=1, gradient_checkpointing=False)
        loaders, reports = build_loaders(Path(request['features']), config)
        if stage not in loaders:
            raise RuntimeError(str(reports))
        loader = loaders[stage]; dataset = loader.dataset
        eligible = [i for i, name in enumerate(reports[stage]['dataset_names']) if name in set(reports[stage]['names'])]
        if stage == 's1':
            semantic = lambda i: len(dataset.semantic_phoneme[i][0])
            phones = lambda i: len(dataset.semantic_phoneme[i][1])
            primary = max(eligible, key=lambda i: semantic(i) + phones(i))
            extremes = [primary, max(eligible,key=semantic), max(eligible,key=phones)]
        else:
            primary = max(eligible, key=lambda i: dataset.lengths[i])
            extremes = [primary, max(eligible, key=lambda i: len(dataset.audiopaths_sid_text[i][1]))]
        extremes = list(dict.fromkeys(extremes))
        items = {i: dataset[i] for i in extremes}
        indices = [extremes[i % len(extremes)] for i in range(batch_size)]
        names = reports[stage]['dataset_names']
        result.update(dataset_unique=reports[stage]['dataset_unique_samples'], eligible_unique=len(eligible),
                      batch_item_names=[names[i] for i in indices], distinct_batch_items=len(set(indices)))
        implementation = s1_training if stage=='s1' else s2_training
        model, optimizer, scheduler, effective = implementation.create(config[stage], request['initial'], output.parent)
        result['initial_parameters_sha256'] = parameter_hash(model)
        result['trainable_parameters'] = sum(p.numel() for p in model.parameters() if p.requires_grad)
        atomic_json(output.parent/'effective.json', effective)
        scaler = torch.amp.GradScaler('cuda', init_scale=128 if stage=='s1' else 65536, enabled=True)
        def updated(*_): result['updates'] += 1
        hook = optimizer.register_step_post_hook(updated)
        torch.cuda.reset_peak_memory_stats()
        started = time.perf_counter(); result['status']='RUNNING'; atomic_json(output,result)
        for attempt in range(request['updates'] + 32):
            optimizer.zero_grad(set_to_none=True)
            torch.cuda.synchronize(); tick=time.perf_counter()
            batch=loader.collate_fn([items[i] for i in indices])
            if isinstance(batch,dict):
                batch={k:v.cuda() if torch.is_tensor(v) else v for k,v in batch.items()}
                actual=int(batch['phoneme_ids'].shape[0])
                shapes={k:list(v.shape) for k,v in batch.items() if torch.is_tensor(v)}
            else:
                batch=[v.cuda() if torch.is_tensor(v) else v for v in batch]
                actual=int(batch[0].shape[0]);shapes=[list(v.shape) for v in batch if torch.is_tensor(v)]
            if actual != batch_size: raise RuntimeError('PHYSICAL_BATCH_SIZE_MISMATCH')
            with torch.autocast('cuda',dtype=torch.float16):
                loss,accuracy=implementation.loss(model,batch,effective)
                if not torch.isfinite(loss).all().item(): raise FloatingPointError('NONFINITE_LOSS')
            scaler.scale(loss).backward();scaler.unscale_(optimizer)
            before=result['updates'];scaler.step(optimizer);scaler.update();optimizer.zero_grad(set_to_none=True)
            if result['updates']==before:result['skipped']+=1
            elif stage=='s1':scheduler.step()
            torch.cuda.synchronize()
            result['steps'].append(dict(attempt=attempt+1,updated=result['updates']!=before,actual_batch=actual,
                loss=float(loss.detach().cpu()),seconds=time.perf_counter()-tick,scale=scaler.get_scale(),
                allocated_bytes=torch.cuda.memory_allocated(),reserved_bytes=torch.cuda.memory_reserved()))
            result.update(peak_allocated_bytes=torch.cuda.max_memory_allocated(),peak_reserved_bytes=torch.cuda.max_memory_reserved(),
                          tensor_shapes=shapes,seconds=time.perf_counter()-started)
            atomic_json(output,result)
            del batch,loss,accuracy
            if result['updates']>=request['updates']:break
        if result['updates']<request['updates']: raise FloatingPointError('TOO_MANY_SKIPPED_UPDATES')
        result['final_parameters_sha256']=parameter_hash(model)
        if result['final_parameters_sha256']==result['initial_parameters_sha256']:
            raise RuntimeError('NO_PARAMETER_CHANGE')
        warm=[row['seconds'] for row in result['steps'][1:] if row['updated']]
        result.update(status='PASS',median_step_seconds=statistics.median(warm) if warm else result['steps'][-1]['seconds'],
                      gpu_after=gpu_snapshot(),source_weights_sha256=digest(Path(request['initial'])))
        hook.remove()
    except Exception as exc:
        result.update(status='OOM' if 'out of memory' in str(exc).lower() or type(exc).__name__=='OutOfMemoryError' else 'FAILED',
                      error=f'{type(exc).__name__}: {exc}',traceback=traceback.format_exc())
        try:
            result.update(peak_allocated_bytes=torch.cuda.max_memory_allocated(),peak_reserved_bytes=torch.cuda.max_memory_reserved())
        except Exception: pass
    atomic_json(output,result)
    print(json.dumps({k:result.get(k) for k in ['stage','batch','status','updates','skipped','peak_allocated_bytes','peak_reserved_bytes','error']}),flush=True)


def run_trial(area, snapshot, stage, batch, updates, suffix='probe'):
    from dictionforge.jobs import Jobs
    from dictionforge.supervisor import exclusive
    jobs=Jobs(ROOT/'data');lock=None;last_notice=0
    while lock is None:
        guard=exclusive(jobs.state/'worker.lock')
        try:guard.__enter__()
        except OSError:guard=None
        if guard is not None:
            with jobs.connect() as db:
                active=db.execute("SELECT id FROM jobs WHERE state IN ('QUEUED','RUNNING') AND kind NOT IN ('model_export','speech_save') LIMIT 1").fetchone()
            if not active:lock=guard;break
            guard.__exit__(None,None,None)
        if time.monotonic()-last_notice>30:
            print(json.dumps(dict(status='WAIT_GPU',stage=stage,batch=batch)),flush=True);last_notice=time.monotonic()
        time.sleep(1)
    trial=area/f'{stage}-b{batch}-{suffix}';trial.mkdir(exist_ok=True)
    request=dict(stage=stage,batch=batch,updates=updates,features=snapshot['features'],version_id=snapshot['version']['id'],
                 initial=snapshot['initial_weights']['gpt' if stage=='s1' else 'sovits'])
    atomic_json(trial/'request.json',request)
    print(json.dumps(dict(status='START',stage=stage,batch=batch,updates=updates,trial=str(trial))),flush=True)
    started=time.monotonic();samples=[];proc=None
    try:
        env=dict(os.environ,PYTHONUTF8='1',PYTHONDONTWRITEBYTECODE='1',PYTHONPATH=str(ROOT/'backend'))
        with (trial/'worker.log').open('wb') as log:
            proc=subprocess.Popen([ROOT/'.venv-worker/Scripts/python.exe',Path(__file__).resolve(),'--child',trial/'request.json',trial/'result.json'],
                cwd=ROOT,env=env,stdout=log,stderr=log,stdin=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            while proc.poll() is None:
                try:samples.append(dict(elapsed=time.monotonic()-started,**gpu_snapshot()))
                except Exception:pass
                if time.monotonic()-started>max(240,updates*20):
                    proc.kill();proc.wait();break
                time.sleep(.5)
        output=trial/'result.json'
        result=json.loads(output.read_text(encoding='utf-8')) if output.exists() else dict(status='FAILED',error='No child report')
        if result['status'] in {'RUNNING','STARTING'}:result.update(status='TIMEOUT',error='Owned benchmark child exceeded time limit')
        result.update(stage=stage,batch=batch,suffix=suffix,wall_seconds=time.monotonic()-started,process_exit=proc.returncode,
                      sampled_gpu_peak_mib=max((s['used_mib'] for s in samples),default=None),path=str(output))
        atomic_json(trial/'gpu-samples.json',samples);atomic_json(output,result)
        print(json.dumps({k:result.get(k) for k in ['stage','batch','suffix','status','updates','skipped','peak_allocated_bytes','peak_reserved_bytes','sampled_gpu_peak_mib','median_step_seconds','error']},ensure_ascii=False),flush=True)
        return result
    finally:
        if proc is not None and proc.poll() is None:proc.kill();proc.wait()
        lock.__exit__(None,None,None)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--child',nargs=2,type=Path)
    parser.add_argument('--version',default='76b5a08af4ca471f9c6fa9c903347e69')
    parser.add_argument('--max-batch',type=int,default=64)
    parser.add_argument('--updates',type=int,default=3)
    parser.add_argument('--confirm-updates',type=int,default=12)
    args=parser.parse_args()
    if args.child:return child(*args.child)
    if not 1<=args.max_batch<=256:parser.error('--max-batch must be 1..256 (UI limit remains 64)')
    if not 1<=args.updates<=100 or not 1<=args.confirm_updates<=100:
        parser.error('update targets must be 1..100')
    from dictionforge.jobs import Jobs
    from dictionforge.records import get
    from dictionforge.references import acquire,release
    from dictionforge.feature_worker import checked_assets
    from dictionforge.source_lock import verify_source
    jobs=Jobs(ROOT/'data');stamp=datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    area=ROOT/'data/batch-benchmark'/stamp;area.mkdir(parents=True)
    token=acquire(jobs,'version',args.version,'batch-benchmark-'+stamp)
    try:
        version=get(jobs,'version',args.version)
        source=ROOT/'data/features'/args.version
        manifest=json.loads((source/'manifest.json').read_text(encoding='utf-8'))
        if not manifest['ready'] or manifest['version_fingerprint']!=version['fingerprint']:raise RuntimeError('Features not ready')
        destination=area/'features';shutil.copytree(source,destination)
        expected=dict(manifest['indexes'])
        for entry in manifest['entries'].values():expected.update(entry['files'])
        for name,sha in expected.items():
            if digest(destination/name)!=sha:raise RuntimeError('Feature hash mismatch: '+name)
        upstream=ROOT/'vendor/gpt-sovits';verify_source(ROOT,upstream);checked_assets(upstream)
        initial=dict(gpt=str(upstream/'GPT_SoVITS/pretrained_models/s1v3.ckpt'),sovits=str(upstream/'GPT_SoVITS/pretrained_models/gsv-v4-pretrained/s2Gv4.pth'))
        snapshot=dict(version=version,features=str(destination),manifest_sha256=digest(source/'manifest.json'),
                      initial_weights=initial,initial_hashes={k:digest(Path(p)) for k,p in initial.items()},
                      source_audio_hashes={c['path']:digest(Path(c['path'])) for c in version['clips']},
                      upstream_lock=digest(ROOT/'configs/upstream.lock.json'),asset_lock=digest(ROOT/'configs/assets.lock.json'))
        atomic_json(area/'source-snapshot.json',snapshot)
    finally:release(jobs,token)
    report=dict(started=datetime.now(timezone.utc).isoformat(),area=str(area),version=args.version,
                gpu=gpu_snapshot(),max_batch=args.max_batch,application_batch_limit=64,precision='fp16',s2_rank=32,accumulation=1,
                profile='longest_real_items_repeated',trials=[],stages={},status='RUNNING')
    latest=ROOT/'data/batch-benchmark/latest.json'
    def save():atomic_json(area/'report.json',report);atomic_json(latest,report)
    save()
    def trial(stage,batch,updates,suffix='probe'):
        value=run_trial(area,snapshot,stage,batch,updates,suffix);report['trials'].append(value);save();return value
    for stage in ('s2','s1'):
        low=0;high=None;batch=1
        while batch<=args.max_batch:
            value=trial(stage,batch,args.updates)
            if value['status']=='PASS':
                low=batch
                if batch==args.max_batch:break
                batch=min(args.max_batch,batch*2)
            elif value['status']=='OOM':high=batch;break
            else:break
        if high is not None:
            while high-low>1:
                middle=(high+low)//2;value=trial(stage,middle,args.updates)
                if value['status']=='PASS':low=middle
                elif value['status']=='OOM':high=middle
                else:break
        passed=[x for x in report['trials'] if x['stage']==stage and x['status']=='PASS']
        candidates=[x for x in passed if x['batch']<=64 and x['peak_reserved_bytes']<=x['allocator_budget_bytes']*.85]
        recommended=max(candidates,key=lambda x:x['batch'])['batch'] if candidates else (1 if low else None)
        confirmations=[]
        for count in dict.fromkeys([low,recommended]):
            if count:confirmations.append(trial(stage,count,args.confirm_updates,'confirm'))
        report['stages'][stage]=dict(max_passed=low,first_oom=high,recommended=recommended,confirmations=[dict(batch=x['batch'],status=x['status']) for x in confirmations],
                                    capped=low==args.max_batch)
        save()
    report['weights_unchanged']=all(digest(Path(p))==snapshot['initial_hashes'][k] for k,p in snapshot['initial_weights'].items())
    report['source_audio_unchanged']=all(Path(p).exists() and digest(Path(p))==sha for p,sha in snapshot['source_audio_hashes'].items())
    report.update(status='COMPLETE',finished=datetime.now(timezone.utc).isoformat());save()
    print(json.dumps(dict(status=report['status'],stages=report['stages'],report=str(area/'report.json')),ensure_ascii=False),flush=True)


if __name__=='__main__':main()
